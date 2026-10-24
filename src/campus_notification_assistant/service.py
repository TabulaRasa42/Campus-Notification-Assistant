"""应用服务：内存草稿与确认后的 SQLite 待办。"""

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

from .credentials import DEFAULT_NAMESPACE, Credentials
from .deepseek import DeepSeek
from .errors import ServiceError, StaleRequestError
from .models import CitedText, Deadline, Draft, Item, Material, Todo

Citation = TypeVar("Citation", CitedText, Deadline, Material)
FULL_DATE = re.compile(r"(\d{4})[年/\-](\d{1,2})[月/\-](\d{1,2})(?:日)?")


def validate_evidence(item: Item) -> None:
    if not item.evidence_ready:
        raise ServiceError("仍有无效原文依据，请选取真实依据、标为用户补充内容或移除字段。")


def normalize_item(item: Item, source: str) -> Item:
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

    action = citation(item.action)
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
    else:
        deadline = citation(deadline)
        raw = deadline.raw if deadline.origin == "user" else deadline.evidence or deadline.raw
        # Only an explicit full date associated with a deadline may be normalized.
        candidates: set[date] = set()
        for match in FULL_DATE.finditer(source):
            following = re.split(r"[。；;，,\n]", source[match.end() :], maxsplit=1)[0][:12]
            preceding = re.split(r"[。；;，,\n]", source[: match.start()])[-1][-12:]
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
        self._confirmed_id: str | None = None
        try:
            self.database.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as connection:
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS todos (id TEXT PRIMARY KEY, data TEXT NOT NULL)"
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
            self.source = source

    def discard(self, *, confirmed: bool = False) -> None:
        with self._state_lock:
            if self.has_unsaved_changes and not confirmed:
                raise ServiceError("草稿尚未保存，请先确认丢弃。")
            self._generation += 1
            self.source = ""
            self.draft = None

    def extract(self, *, api_key: str | None = None) -> Draft | None:
        return self.prepare_extraction(api_key=api_key)()

    def prepare_extraction(self, *, api_key: str | None = None) -> Callable[[], Draft | None]:
        """Capture the click-time request before it is queued on a worker."""
        with self._state_lock:
            self._generation += 1
            generation = self._generation
            source = self.source
            self.draft = None
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
                return None
        if len(result.items) != 1:
            raise ServiceError("本次仅支持一条办理事项，请分条提取。")
        received = result.items[0]
        received = received.model_copy(
            update={
                "action": received.action.model_copy(update={"origin": "extracted"}),
                "deadline": received.deadline.model_copy(update={"origin": "extracted"}),
                "materials": tuple(
                    m.model_copy(update={"origin": "extracted"}) for m in received.materials
                ),
            }
        )
        item = normalize_item(received, source)
        with self._state_lock:
            if generation != self._generation:
                raise StaleRequestError()
            self.draft = Draft(id=str(uuid4()), source=source, item=item)
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
    ) -> Draft:
        with self._state_lock:
            if self.draft is None or self.draft.id != draft_id or self._confirmed_id == draft_id:
                raise ServiceError("请先提取未保存的草稿。")
            item = self.draft.item
            old: CitedText | Deadline
            data = item.model_dump()
            if field == "action":
                if remove or not value.strip():
                    raise ServiceError("事项内容不能为空。")
                old = item.action
            elif field == "deadline":
                old = item.deadline
            elif field.startswith("materials."):
                try:
                    index = int(field.split(".")[1])
                    if index < 0:
                        raise IndexError
                    old = item.materials[index]
                except (ValueError, IndexError):
                    raise ServiceError("材料不存在。") from None
            else:
                raise ServiceError("字段不存在。")
            if remove:
                replacement = Deadline(
                    status="missing", review="missing", precision="unknown"
                ).model_dump()
            else:
                if not value.strip():
                    raise ServiceError("内容不能为空。")
                evidence = ""
                if not user_supplied:
                    if (
                        evidence_start is None
                        or evidence_end is None
                        or not 0 <= evidence_start < evidence_end <= len(self.source)
                    ):
                        raise ServiceError("请选取真实原文依据或明确标为用户补充内容。")
                    evidence = self.source[evidence_start:evidence_end]
                replacement = old.model_dump()
                replacement.update(
                    origin="user" if user_supplied else "extracted",
                    evidence=evidence,
                    evidence_start=evidence_start,
                    evidence_end=evidence_end,
                )
                if field == "deadline":
                    replacement.update(
                        raw=value, date=None, status="uncertain", precision="unknown"
                    )
                else:
                    replacement["value"] = value
                    if isinstance(old, Material):
                        replacement["requirement"] = (
                            requirement.strip()
                            if requirement is not None
                            else old.requirement
                            if value.strip() == old.value
                            else ""
                        )
            if field.startswith("materials."):
                entries = list(data["materials"])
                if remove:
                    entries.pop(index)
                else:
                    entries[index] = replacement
                data["materials"] = entries
            else:
                data[field] = replacement
            corrected = normalize_item(Item.model_validate(data), self.source)
            self.draft = self.draft.model_copy(update={"item": corrected})
            return self.draft

    def confirm(self, draft_id: str) -> Todo:
        with self._state_lock:
            if self.draft is None or self.draft.id != draft_id:
                raise ServiceError("请先提取并核对通知。")
            validate_evidence(self.draft.item)
            todo = Todo.model_validate(self.draft.model_dump())
            try:
                with self._connect() as connection:
                    connection.execute(
                        "INSERT OR IGNORE INTO todos VALUES (?, ?)",
                        (todo.id, todo.model_dump_json()),
                    )
            except sqlite3.Error:
                raise ServiceError(
                    "保存失败，草稿已保留；请检查数据库占用或可用空间后重试。"
                ) from None
            self._confirmed_id = todo.id
            return todo

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
