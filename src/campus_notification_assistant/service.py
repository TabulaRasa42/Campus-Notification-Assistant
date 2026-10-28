"""应用服务：内存草稿与确认后的 SQLite 待办。"""

import json
import re
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from threading import RLock
from typing import TypeVar
from uuid import uuid4

import httpx
from pydantic import ValidationError

from .citation_quality import complete_material_citations, reject_background_action_quote
from .credentials import DEFAULT_NAMESPACE, Credentials
from .deepseek import DeepSeek
from .errors import ServiceError, StaleRequestError
from .material_scope import exclude_other_stage_materials, retain_explicit_shared_subject
from .models import (
    CitedText,
    Deadline,
    Draft,
    DraftBatch,
    DraftEntry,
    Item,
    Material,
    MaterialRelation,
    Todo,
)

Citation = TypeVar("Citation", CitedText, Deadline, Material)
FULL_DATE = re.compile(r"(\d{4})[年/\-](\d{1,2})[月/\-](\d{1,2})(?:日)?")


def validate_evidence(item: Item) -> None:
    if not item.evidence_ready:
        raise ServiceError("仍有无效原文依据，请选取真实依据、标为用户补充内容或移除字段。")


def normalize_item(item: Item, source: str, *, deadline_scope: str | None = None) -> Item:
    def citation(field: Citation) -> Citation:
        if field.origin == "user":
            return field.model_copy(
                update={
                    "review": "matched",
                    "evidence": "",
                    "evidence_start": None,
                    "evidence_end": None,
                }
            )
        text = field.evidence
        start, end = field.evidence_start, field.evidence_end
        exact = (
            start is not None
            and end is not None
            and 0 <= start < end <= len(source)
            and source[start:end] == text
        )
        if start is None and end is None and text and source.count(text) == 1:
            start = source.index(text)
            end = start + len(text)
            exact = True
        return field.model_copy(
            update={
                "review": "matched" if exact else "invalid",
                "evidence_start": start if exact else None,
                "evidence_end": end if exact else None,
            }
        )

    action = reject_background_action_quote(citation(item.action))
    materials = tuple(
        citation(m).model_copy(update={"review": "invalid"})
        if m.origin != "user" and m.value not in m.evidence
        else citation(m)
        for m in item.materials
    )
    deadline = item.deadline
    if not deadline.evidence and deadline.date is None and not deadline.raw:
        deadline = deadline.model_copy(
            update={"status": "missing", "review": "missing", "precision": "unknown"}
        )
    elif deadline.origin == "user":
        raw = deadline.raw or (deadline.date.isoformat() if deadline.date else "")
        explicit = FULL_DATE.fullmatch(raw.strip().removesuffix("前"))
        personal_date = None
        if explicit:
            try:
                personal_date = date(*(int(part) for part in explicit.groups()))
            except ValueError:
                pass
        deadline = citation(deadline).model_copy(
            update={
                "raw": raw,
                "date": personal_date,
                "status": "certain" if personal_date else "uncertain",
                "precision": "date" if personal_date else "unknown",
            }
        )
    else:
        deadline = citation(deadline)
        raw = deadline.raw if deadline.origin == "user" else deadline.evidence or deadline.raw
        # Only an explicit full date associated with a deadline may be normalized.
        candidates: set[date] = set()
        context = (
            deadline_scope
            if deadline_scope and deadline_scope in source and deadline.evidence in deadline_scope
            else source
        )
        for match in FULL_DATE.finditer(context):
            following = re.split(r"[。；;，,\n]", context[match.end() :], maxsplit=1)[0][:12]
            preceding = re.split(r"[。；;，,\n]", context[: match.start()])[-1][-12:]
            # Submission alone is not a deadline; do not cross a clause boundary
            # from an activity start into a later request to submit something.
            if re.match(
                r"\s*(?:(?:\d{1,2}[:：]\d{2}|\d{1,2}[时点](?:\d{1,2}分)?)(?:整)?\s*)?"
                r"(?:之前|以前|前(?!往)|截止|为止)",
                following,
            ) or re.search(
                r"(?:截止(?:日期|时间)?|截至|最迟)(?:应|须|必须|在|于|至|为|是|到|：|:|\s)*$",
                preceding,
            ):
                try:
                    candidates.add(date(*(int(part) for part in match.groups())))
                except ValueError:
                    pass
        quoted_dates = set()
        for match in FULL_DATE.finditer(deadline.evidence):
            try:
                quoted_dates.add(date(*(int(part) for part in match.groups())))
            except ValueError:
                pass
        relative = re.search(
            r"[本上下]周|[本上下]月|今年|明年|今天|明天|后天|昨日|今日|明日", deadline.evidence
        )
        certain = (
            deadline.review == "matched"
            and deadline.date is not None
            and deadline.date in quoted_dates
            and candidates == {deadline.date}
            and not relative
        )
        if deadline.status == "uncertain" or not certain:
            deadline = deadline.model_copy(
                update={"date": None, "raw": raw, "status": "uncertain", "precision": "unknown"}
            )
        else:
            deadline = deadline.model_copy(
                update={"raw": raw, "status": "certain", "precision": "date"}
            )
    return Item(action=action, deadline=deadline, materials=materials)


def _keep_deadline_text(original: Deadline, reviewed: Deadline) -> Deadline:
    """Changing a citation must not replace the displayed deadline correction."""
    if not original.raw:
        return reviewed
    raw = original.raw.strip()
    full = FULL_DATE.fullmatch(raw.removesuffix("前"))
    quoted = {
        tuple(int(part) for part in match.groups())
        for match in FULL_DATE.finditer(reviewed.evidence)
    }
    supported = raw in reviewed.evidence or (
        full is not None and quoted == {tuple(int(part) for part in full.groups())}
    )
    return reviewed.model_copy(update={
        "raw": original.raw,
        "review": reviewed.review if supported else "invalid",
    })


def correct_item_field(
    item: Item,
    source: str,
    field: str,
    value: str,
    *,
    evidence_start: int | None = None,
    evidence_end: int | None = None,
    user_supplied: bool = False,
    remove: bool = False,
    requirement: str | None = None,
    relation: MaterialRelation | None = None,
    deadline_scope: str | None = None,
) -> Item:
    """Apply one explicit correction using the same rules for drafts and saved edits."""
    old: CitedText | Deadline
    data = item.model_dump()
    deadline_needs_review = False
    if field == "action":
        if remove or not value.strip():
            raise ServiceError("事项内容不能为空。")
        old = item.action
    elif field == "deadline":
        old = item.deadline
    elif field.startswith("materials."):
        try:
            index = int(field.split(".")[1])
            if index < 0 or index > len(item.materials):
                raise IndexError
            if index == len(item.materials) and not remove:
                if not value.strip():
                    raise ServiceError("内容不能为空。")
                old = Material(value=value, review="invalid")
            else:
                old = item.materials[index]
        except (ValueError, IndexError):
            raise ServiceError("材料不存在。") from None
    else:
        raise ServiceError("字段不存在。")
    if remove:
        replacement = Deadline(status="missing", review="missing", precision="unknown").model_dump()
    else:
        if not value.strip():
            raise ServiceError("内容不能为空。")
        replacement = old.model_dump()
        if user_supplied:
            replacement.update(origin="user", evidence="", evidence_start=None, evidence_end=None)
        elif evidence_start is not None or evidence_end is not None:
            if (
                evidence_start is None
                or evidence_end is None
                or not 0 <= evidence_start < evidence_end <= len(source)
            ):
                raise ServiceError("请选取真实原文依据或明确标为用户补充内容。")
            replacement.update(
                origin="extracted",
                evidence=source[evidence_start:evidence_end],
                evidence_start=evidence_start,
                evidence_end=evidence_end,
            )
        elif old.origin == "user":
            # Unchecking user content does not invent a source citation.
            replacement.update(origin="extracted")
        if field == "deadline":
            assert isinstance(old, Deadline)
            unchanged = value.strip() == old.raw or (
                old.date is not None and value.strip() == old.date.isoformat()
            )
            if not unchanged:
                replacement.update(raw=value, date=None, status="uncertain", precision="unknown")
            deadline_needs_review = (
                not user_supplied
                and old.origin != "user"
                and evidence_start is None
                and evidence_end is None
                and (not unchanged or old.review == "invalid")
            )
        else:
            replacement["value"] = value
            if isinstance(old, Material):
                if relation is not None and relation not in {
                    "single",
                    "all",
                    "any",
                    "uncertain",
                }:
                    raise ServiceError("材料关系无效，请选择真实关系或待核对。")
                replacement["requirement"] = (
                    requirement.strip()
                    if requirement is not None
                    else old.requirement
                    if value.strip() == old.value
                    else ""
                )
                if relation is not None:
                    replacement["relation"] = relation
                elif value.strip() != old.value:
                    replacement["relation"] = None
    if field.startswith("materials."):
        entries = list(data["materials"])
        if remove:
            entries.pop(index)
        else:
            if index == len(entries):
                entries.append(replacement)
            else:
                entries[index] = replacement
        data["materials"] = entries
    else:
        data[field] = replacement
    normalized = normalize_item(Item.model_validate(data), source, deadline_scope=deadline_scope)
    if field == "deadline" and not remove and not user_supplied:
        normalized = normalized.model_copy(update={
            "deadline": _keep_deadline_text(Deadline.model_validate(replacement), normalized.deadline)
        })
    target = "materials" if field.startswith("materials.") else field
    corrected = item.model_copy(update={target: getattr(normalized, target)})
    if target == "materials":
        # Existing siblings retain their content, citations and unresolved review state.
        entries = list(item.materials)
        if remove:
            entries.pop(index)
        elif index == len(entries):
            entries.append(normalized.materials[index])
        else:
            entries[index] = normalized.materials[index]
        corrected = item.model_copy(update={"materials": tuple(entries)})
    if deadline_needs_review:
        # Retain an independent edit without letting the previous citation
        # silently overwrite it or validate a different deadline.
        corrected = corrected.model_copy(
            update={
                "deadline": corrected.deadline.model_copy(
                    update={"raw": value.strip(), "review": "invalid"}
                )
            }
        )
    return corrected


def select_item_evidence(
    item: Item, source: str, field: str, start: int, end: int, *, deadline_scope: str | None = None
) -> Item:
    """Select evidence without replacing the content or unrelated review state."""
    if not 0 <= start < end <= len(source):
        raise ServiceError("请选择通知原文中的依据。")
    data = item.model_dump()
    if field in {"action", "deadline"}:
        selected = data[field]
    elif field.startswith("materials."):
        try:
            index = int(field.split(".")[1])
            if index < 0:
                raise IndexError
            entries = list(data["materials"])
            selected = entries[index]
            data["materials"] = entries
        except (ValueError, IndexError):
            raise ServiceError("材料不存在。") from None
    else:
        raise ServiceError("字段不存在。")
    selected.update(
        origin="extracted",
        evidence=source[start:end],
        evidence_start=start,
        evidence_end=end,
    )
    normalized = normalize_item(Item.model_validate(data), source, deadline_scope=deadline_scope)
    if field == "deadline":
        normalized = normalized.model_copy(update={
            "deadline": _keep_deadline_text(item.deadline, normalized.deadline)
        })
    if field.startswith("materials."):
        entries = list(item.materials)
        entries[index] = normalized.materials[index]
        corrected = item.model_copy(update={"materials": tuple(entries)})
    else:
        corrected = item.model_copy(update={field: getattr(normalized, field)})
    return corrected


class NotificationService:
    def __init__(
        self,
        database: Path,
        *,
        transport: httpx.BaseTransport | None = None,
        credential_namespace: str = DEFAULT_NAMESPACE,
    ) -> None:
        self.database = database
        self.ai = DeepSeek(transport)
        self.credentials = Credentials(credential_namespace)
        self._state_lock = RLock()
        self._generation = 0
        self.source = ""
        self.draft: Draft | None = None
        self.todo_edit: Todo | None = None
        self._todo_original: Todo | None = None
        self.batch: DraftBatch | None = None
        self._confirmed_id: str | None = None
        try:
            self.database.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as connection:
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS todos (id TEXT PRIMARY KEY, data TEXT NOT NULL)"
                )
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS confirmed_batches "
                    "(id TEXT PRIMARY KEY, data TEXT NOT NULL)"
                )
        except (sqlite3.Error, OSError):
            raise ServiceError("无法打开本地数据库，请检查数据目录权限与可用空间。") from None

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database, timeout=0.5)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @property
    def has_unsaved_changes(self) -> bool:
        return bool(self.source.strip()) and (
            self.draft is None or self.draft.id != self._confirmed_id
        )

    def key_configured(self) -> bool:
        return bool(self.credentials.read())

    def configure_key(self, secret: str) -> None:
        self.credentials.save(secret)

    def clear_key(self) -> None:
        self.credentials.clear()

    def set_source(self, source: str, *, discard: bool = False) -> None:
        with self._state_lock:
            if source != self.source:
                self._generation += 1
                if self.draft is not None and self.has_unsaved_changes and not discard:
                    raise ServiceError("修改原文会丢弃当前提取结果，请先确认丢弃。")
                self.draft = None
                self.batch = None
            self.source = source

    def discard(self, *, confirmed: bool = False) -> None:
        with self._state_lock:
            if self.has_unsaved_changes and not confirmed:
                raise ServiceError("草稿尚未保存，请先确认丢弃。")
            self._generation += 1
            self.source = ""
            self.draft = None
            self.batch = None

    def extract(self, *, api_key: str | None = None) -> Draft | None:
        return self.prepare_extraction(api_key=api_key)()

    def prepare_extraction(self, *, api_key: str | None = None) -> Callable[[], Draft | None]:
        """Capture the click-time request before it is queued on a worker."""
        with self._state_lock:
            self._generation += 1
            generation = self._generation
            source = self.source
            self.draft = None
            self.batch = None
        return lambda: self._extract_snapshot(source, generation, api_key)

    def _extract_snapshot(self, source: str, generation: int, api_key: str | None) -> Draft | None:
        with self._state_lock:
            if generation != self._generation:
                raise StaleRequestError()
        if not source.strip():
            raise ServiceError("请先粘贴通知原文。")
        if len(source) > 50000:
            raise ServiceError("通知超过 50000 字符，请缩小为单条通知；未截断或发送。")
        secret = api_key if api_key is not None else self.credentials.read()
        if not secret:
            raise ServiceError("请先配置个人 DeepSeek API Key。")
        try:
            result = self.ai.extract(source, secret)
        except (ValidationError, ValueError, KeyError, IndexError, TypeError):
            raise ServiceError(
                "AI 返回 JSON 或结构不合法，请主动重试；输入已保留。", code="invalid_response"
            ) from None
        with self._state_lock:
            if generation != self._generation:
                raise StaleRequestError()
            if not result.items:
                self.draft = None
                self.batch = DraftBatch(id=str(uuid4()), source=source, entries=(), current_id=None)
                return None
        batch_id = str(uuid4())
        scopes: dict[int, str] = {}
        scope_ranges: dict[int, tuple[int, int]] = {}
        if len(result.items) > 1:
            starts: list[tuple[int, int]] = []
            for index, received in enumerate(result.items):
                quote = received.action.evidence
                left, right = received.action.evidence_start, received.action.evidence_end
                if left is None and right is None and quote and source.count(quote) == 1:
                    left, right = source.index(quote), source.index(quote) + len(quote)
                if (
                    left is None
                    or right is None
                    or not 0 <= left < right <= len(source)
                    or source[left:right] != quote
                ):
                    break
                boundaries = list(re.finditer(r"[。！？!?；;\n，,]", source[:left]))
                starts.append((boundaries[-1].end() if boundaries else 0, index))
            if len(starts) == len(result.items) and len({s for s, _ in starts}) == len(starts):
                starts.sort()
                for position, (left, index) in enumerate(starts):
                    right = starts[position + 1][0] if position + 1 < len(starts) else len(source)
                    left = 0 if position == 0 else left
                    scopes[index] = source[left:right]
                    scope_ranges[index] = (left, right)
            # Positional grouping is safe only when no full date remains unassigned.
            # A later reference or a quote with different dates may belong to an earlier
            # action; literal coverage alone cannot establish ownership.
            deadline_spans = []
            for index, received in enumerate(result.items):
                quote = received.deadline.evidence
                left, right = received.deadline.evidence_start, received.deadline.evidence_end
                if left is None and right is None and quote and source.count(quote) == 1:
                    left, right = source.index(quote), source.index(quote) + len(quote)
                bounds = scope_ranges.get(index)
                if (
                    bounds is not None and left is not None and right is not None
                    and bounds[0] <= left < right <= bounds[1]
                    and source[left:right] == quote
                ):
                    deadline_spans.append((left, right))
            mixed_date_quote = any(
                len({
                    tuple(int(part) for part in match.groups())
                    for match in FULL_DATE.finditer(received.deadline.evidence)
                }) > 1
                for received in result.items
            )
            if mixed_date_quote or any(
                sum(left <= match.start() and match.end() <= right for left, right in deadline_spans)
                != 1
                for match in FULL_DATE.finditer(source)
            ):
                scopes.clear()
        entries = []
        for index, received in enumerate(result.items):
            received = received.model_copy(
                update={
                    "action": received.action.model_copy(update={"origin": "extracted"}),
                    "deadline": received.deadline.model_copy(update={"origin": "extracted"}),
                    "materials": tuple(
                        m.model_copy(update={"origin": "extracted"}) for m in received.materials
                    ),
                }
            )
            item = exclude_other_stage_materials(
                normalize_item(received, source, deadline_scope=scopes.get(index)), source
            )
            item = complete_material_citations(item, source)
            item = retain_explicit_shared_subject(item)
            entries.append(
                DraftEntry(
                    id=batch_id if index == 0 else str(uuid4()),
                    item=item,
                    deadline_scope=scopes.get(index),
                )
            )
        with self._state_lock:
            if generation != self._generation:
                raise StaleRequestError()
            self.batch = DraftBatch(
                id=batch_id, source=source, entries=tuple(entries), current_id=entries[0].id
            )
            self.draft = Draft(id=batch_id, source=source, item=entries[0].item)
            return self.draft

    def correct_field(
        self,
        draft_id: str,
        field: str,
        value: str,
        *,
        evidence_start: int | None = None,
        evidence_end: int | None = None,
        user_supplied: bool = False,
        remove: bool = False,
        requirement: str | None = None,
        relation: MaterialRelation | None = None,
    ) -> Draft:
        with self._state_lock:
            if self.draft is None or self.draft.id != draft_id or self._confirmed_id == draft_id:
                raise ServiceError("请先提取未保存的草稿。")
            item = self.draft.item
            if field != "action" and not item.action.value.strip():
                raise ServiceError("请先填写事项内容。")
            corrected = correct_item_field(
                item,
                self.source,
                field,
                value,
                evidence_start=evidence_start,
                evidence_end=evidence_end,
                user_supplied=user_supplied,
                remove=remove,
                requirement=requirement,
                relation=relation,
                deadline_scope=self._current_scope(),
            )
            self.draft = self.draft.model_copy(update={"item": corrected})
            self._replace_current(corrected)
            return self.draft

    def select_evidence(self, draft_id: str, field: str, start: int, end: int) -> Draft:
        """Replace only a citation; field content, conditions and relation are retained."""
        with self._state_lock:
            if self.draft is None or self.draft.id != draft_id or self._confirmed_id == draft_id:
                raise ServiceError("请先提取未保存的草稿。")
            if not self.draft.item.action.value.strip():
                raise ServiceError("请先填写事项内容。")
            corrected = select_item_evidence(
                self.draft.item,
                self.source,
                field,
                start,
                end,
                deadline_scope=self._current_scope(),
            )
            self.draft = self.draft.model_copy(update={"item": corrected})
            self._replace_current(corrected)
            return self.draft

    def _require_batch(self, batch_id: str, *, editable: bool = True) -> DraftBatch:
        if self.batch is None or self.batch.id != batch_id:
            raise ServiceError("请先提取并核对通知。")
        if editable and self._confirmed_id == batch_id:
            raise ServiceError("本批次已保存，请新建通知后继续。")
        return self.batch

    def _current_scope(self) -> str | None:
        return (
            next(
                (
                    entry.deadline_scope
                    for entry in self.batch.entries
                    if entry.id == self.batch.current_id
                ),
                None,
            )
            if self.batch
            else None
        )

    def _sync_draft(self) -> None:
        batch = self.batch
        current = (
            next((entry for entry in batch.entries if entry.id == batch.current_id), None)
            if batch
            else None
        )
        self.draft = (
            Draft(id=batch.id, source=batch.source, item=current.item)
            if batch and current
            else None
        )

    def _replace_current(self, item: Item) -> None:
        assert self.batch is not None
        self.batch = self.batch.model_copy(
            update={
                "entries": tuple(
                    entry.model_copy(update={"item": item})
                    if entry.id == self.batch.current_id
                    else entry
                    for entry in self.batch.entries
                )
            }
        )
        self._sync_draft()

    def focus_item(self, batch_id: str, item_id: str) -> None:
        with self._state_lock:
            batch = self._require_batch(batch_id, editable=False)
            if not any(entry.id == item_id for entry in batch.entries):
                raise ServiceError("事项不存在。")
            self.batch = batch.model_copy(update={"current_id": item_id})
            self._sync_draft()

    def set_item_selected(self, batch_id: str, item_id: str, selected: bool) -> None:
        with self._state_lock:
            batch = self._require_batch(batch_id)
            if not any(entry.id == item_id for entry in batch.entries):
                raise ServiceError("事项不存在。")
            self.batch = batch.model_copy(
                update={
                    "entries": tuple(
                        entry.model_copy(update={"selected": selected})
                        if entry.id == item_id
                        else entry
                        for entry in batch.entries
                    )
                }
            )

    def add_item(self, batch_id: str, title: str = "") -> DraftEntry:
        with self._state_lock:
            batch = self._require_batch(batch_id)
            # A blank editor row is allowed in memory; confirmation validates the title.
            action = (
                CitedText(value=title, origin="user")
                if title.strip()
                else CitedText.model_construct(value="", origin="user")
            )
            entry = DraftEntry(id=str(uuid4()), item=Item(action=action))
            self.batch = batch.model_copy(
                update={"entries": (*batch.entries, entry), "current_id": entry.id}
            )
            self._sync_draft()
            return entry

    def remove_item(self, batch_id: str, item_id: str) -> None:
        with self._state_lock:
            batch = self._require_batch(batch_id)
            remaining = tuple(entry for entry in batch.entries if entry.id != item_id)
            if len(remaining) == len(batch.entries):
                raise ServiceError("事项不存在。")
            current = (
                batch.current_id
                if batch.current_id != item_id
                else remaining[0].id
                if remaining
                else None
            )
            self.batch = batch.model_copy(update={"entries": remaining, "current_id": current})
            self._sync_draft()

    def add_material(
        self,
        batch_id: str,
        value: str,
        *,
        user_supplied: bool = False,
        evidence_start: int | None = None,
        evidence_end: int | None = None,
        requirement: str = "",
        relation: MaterialRelation | None = None,
    ) -> Draft:
        with self._state_lock:
            self._require_batch(batch_id)
            if self.draft is None:
                raise ServiceError("请先选择一条事项。")
            if not value.strip():
                raise ServiceError("材料内容不能为空。")
            item = self.draft.item
            if not item.action.value.strip():
                raise ServiceError("请先填写事项内容。")
            corrected = correct_item_field(
                item,
                self.source,
                f"materials.{len(item.materials)}",
                value,
                user_supplied=user_supplied,
                evidence_start=evidence_start,
                evidence_end=evidence_end,
                requirement=requirement,
                relation=relation,
                deadline_scope=self._current_scope(),
            )
            self._replace_current(corrected)
            assert self.draft is not None
            return self.draft

    def confirm(self, draft_id: str) -> Todo:
        if self.batch is not None and self.batch.id == draft_id and len(self.batch.entries) != 1:
            raise ServiceError("请逐项选择并确认保存本批次事项。")
        saved = self.confirm_selected(draft_id)
        if len(saved) != 1:
            raise ServiceError("该批次包含多个事项，请使用批量确认。")
        return saved[0]

    def confirm_selected(self, batch_id: str) -> list[Todo]:
        with self._state_lock:
            try:
                with self._connect() as connection:
                    receipt = connection.execute(
                        "SELECT data FROM confirmed_batches WHERE id = ?", (batch_id,)
                    ).fetchone()
                    if receipt is not None:
                        return [Todo.model_validate(value) for value in json.loads(receipt[0])]
                    if self.batch is None or self.batch.id != batch_id:
                        raise ServiceError("请先提取并核对通知。")
                    selected = [entry for entry in self.batch.entries if entry.selected]
                    if not selected:
                        raise ServiceError("请至少选择一条事项保存。")
                    for entry in selected:
                        if not entry.item.action.value.strip():
                            raise ServiceError("事项内容不能为空。")
                        validate_evidence(entry.item)
                    todos = [
                        Todo(
                            id=entry.id,
                            source=self.batch.source,
                            item=entry.item,
                            deadline_scope=entry.deadline_scope,
                        )
                        for entry in selected
                    ]
                    for todo in todos:
                        connection.execute(
                            "INSERT INTO todos VALUES (?, ?)", (todo.id, todo.model_dump_json())
                        )
                    connection.execute(
                        "INSERT INTO confirmed_batches VALUES (?, ?)",
                        (batch_id, json.dumps([todo.model_dump(mode="json") for todo in todos])),
                    )
            except sqlite3.Error:
                raise ServiceError(
                    "保存失败，草稿已保留；请检查数据库占用或可用空间后重试。"
                ) from None
            self._confirmed_id = batch_id
            return todos

    def list_todos(self) -> list[Todo]:
        try:
            with self._connect() as connection:
                rows = connection.execute("SELECT data FROM todos ORDER BY rowid DESC").fetchall()
            return [Todo.model_validate_json(row[0]) for row in rows]
        except (sqlite3.Error, ValidationError):
            raise ServiceError("无法读取待办，请检查数据库；数据未被覆盖。") from None

    def find_duplicates(self, source: str | None = None) -> list[Todo]:
        """Return every saved Todo for the exact, unmodified notification text."""
        text = self.source if source is None else source
        return [todo for todo in self.list_todos() if todo.source == text]

    def get_todo(self, todo_id: str) -> Todo:
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT data FROM todos WHERE id = ?", (todo_id,)
                ).fetchone()
            if row is None:
                raise ServiceError("待办不存在。")
            return Todo.model_validate_json(row[0])
        except (sqlite3.Error, ValidationError):
            raise ServiceError("无法读取待办，请检查数据库；数据未被覆盖。") from None

    @property
    def has_todo_edits(self) -> bool:
        return self.todo_edit is not None and self.todo_edit != self._todo_original

    def begin_todo_edit(self, todo_id: str) -> Todo:
        with self._state_lock:
            if self.todo_edit is not None:
                if self.todo_edit.id == todo_id:
                    return self.todo_edit
                if self.has_todo_edits:
                    raise ServiceError("待办编辑尚未保存，请先保存或确认丢弃。")
            todo = self.get_todo(todo_id)
            self.todo_edit = todo
            self._todo_original = todo
            return todo

    def correct_todo_field(
        self,
        todo_id: str,
        field: str,
        value: str,
        *,
        evidence_start: int | None = None,
        evidence_end: int | None = None,
        user_supplied: bool = False,
        remove: bool = False,
        requirement: str | None = None,
        relation: MaterialRelation | None = None,
    ) -> Todo:
        with self._state_lock:
            edit = self.todo_edit
            if edit is None or edit.id != todo_id:
                raise ServiceError("请先打开待办编辑。")
            corrected = correct_item_field(
                edit.item,
                edit.source,
                field,
                value,
                evidence_start=evidence_start,
                evidence_end=evidence_end,
                user_supplied=user_supplied,
                remove=remove,
                requirement=requirement,
                relation=relation,
                deadline_scope=edit.deadline_scope,
            )
            self.todo_edit = edit.model_copy(update={"item": corrected})
            return self.todo_edit

    def select_todo_evidence(self, todo_id: str, field: str, start: int, end: int) -> Todo:
        with self._state_lock:
            edit = self.todo_edit
            if edit is None or edit.id != todo_id:
                raise ServiceError("请先打开待办编辑。")
            corrected = select_item_evidence(
                edit.item, edit.source, field, start, end, deadline_scope=edit.deadline_scope
            )
            self.todo_edit = edit.model_copy(update={"item": corrected})
            return self.todo_edit

    def _update_todo(self, todo: Todo) -> Todo:
        try:
            with self._connect() as connection:
                updated = connection.execute(
                    "UPDATE todos SET data = ? WHERE id = ?", (todo.model_dump_json(), todo.id)
                )
                if updated.rowcount != 1:
                    raise ServiceError("待办不存在，未保存任何更改。")
        except sqlite3.Error:
            raise ServiceError(
                "保存失败，待办编辑已保留；请检查数据库占用或可用空间后重试。"
            ) from None
        return todo

    def save_todo_edit(self, todo_id: str) -> Todo:
        with self._state_lock:
            edit = self.todo_edit
            if edit is None or edit.id != todo_id:
                raise ServiceError("请先打开待办编辑。")
            validate_evidence(edit.item)
            if not edit.item.action.value.strip():
                raise ServiceError("事项内容不能为空。")
            saved = self._update_todo(edit)
            self.todo_edit = None
            self._todo_original = None
            return saved

    def cancel_todo_edit(self, todo_id: str) -> None:
        with self._state_lock:
            if self.todo_edit is not None and self.todo_edit.id == todo_id:
                self.todo_edit = None
                self._todo_original = None

    def set_completed(self, todo_id: str, completed: bool) -> Todo:
        with self._state_lock:
            todo = self.get_todo(todo_id)
            saved = self._update_todo(todo.model_copy(update={"completed": completed}))
            if self.todo_edit is not None and self.todo_edit.id == todo_id:
                self.todo_edit = self.todo_edit.model_copy(update={"completed": completed})
                self._todo_original = todo.model_copy(update={"completed": completed})
            return saved

    def delete_todo(self, todo_id: str) -> None:
        with self._state_lock:
            try:
                with self._connect() as connection:
                    deleted = connection.execute("DELETE FROM todos WHERE id = ?", (todo_id,))
                    if deleted.rowcount != 1:
                        raise ServiceError("待办不存在。")
            except sqlite3.Error:
                raise ServiceError("删除失败，待办与编辑已保留；请检查数据库占用后重试。") from None
            self.cancel_todo_edit(todo_id)
