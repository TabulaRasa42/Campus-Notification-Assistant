"""应用服务：内存草稿与确认后的 SQLite 待办。"""

import re
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from threading import RLock
from uuid import uuid4

import httpx
from pydantic import ValidationError

from .credentials import DEFAULT_NAMESPACE, Credentials
from .deepseek import DeepSeek
from .errors import ServiceError, StaleRequestError
from .models import Draft, Item, Todo


def validate_evidence(item: Item, source: str) -> None:
    citations = [
        item.action.evidence,
        item.deadline.evidence,
        *(material.evidence for material in item.materials),
    ]
    if any(evidence not in source for evidence in citations):
        raise ServiceError("原文依据无法匹配，请保留原文并重新提取。")
    if any(material.value not in material.evidence for material in item.materials):
        raise ServiceError("材料与原文依据不符，请重新提取。")
    dates = re.findall(r"(\d{4})[年/\-](\d{1,2})[月/\-](\d{1,2})(?:日)?", item.deadline.evidence)
    try:
        matches = {date(int(y), int(m), int(d)) for y, m, d in dates}
    except ValueError:
        raise ServiceError("原文日期无效，请核对通知。") from None
    if item.deadline.date not in matches:
        raise ServiceError("截止日期与原文依据不符；本版本要求明确的完整年月日。")


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

    def extract(self, *, api_key: str | None = None) -> Draft:
        return self.prepare_extraction(api_key=api_key)()

    def prepare_extraction(self, *, api_key: str | None = None) -> Callable[[], Draft]:
        """Capture the click-time request before it is queued on a worker."""
        with self._state_lock:
            self._generation += 1
            generation = self._generation
            source = self.source
            self.draft = None
        return lambda: self._extract_snapshot(source, generation, api_key)

    def _extract_snapshot(self, source: str, generation: int, api_key: str | None) -> Draft:
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
        if len(result.items) != 1:
            raise ServiceError("本版本需要一条事项、明确的完整截止日期和材料，请检查通知后重试。")
        item = result.items[0]
        if not item.materials:
            raise ServiceError("未提取到明确材料；本版本需要材料齐全的单事项通知。")
        validate_evidence(item, source)
        with self._state_lock:
            if generation != self._generation:
                raise StaleRequestError()
            self.draft = Draft(id=str(uuid4()), source=source, item=item)
            return self.draft

    def confirm(self, draft_id: str) -> Todo:
        with self._state_lock:
            if self.draft is None or self.draft.id != draft_id:
                raise ServiceError("请先提取并核对通知。")
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
