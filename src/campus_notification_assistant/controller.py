"""Qt 适配层：QML 只绑定状态并调用应用服务操作。"""

from collections.abc import Callable
from typing import Any, cast

from PySide6.QtCore import Property, QObject, QRunnable, QThreadPool, Signal, Slot

from .errors import ServiceError
from .models import CitedText, Deadline, Material, MaterialRelation, Todo
from .service import NotificationService


class WorkerSignals(QObject):
    finished = Signal(int, str)


class Worker(QRunnable):
    def __init__(self, operation: Callable[[], object], request_id: int) -> None:
        super().__init__()
        self.operation = operation
        self.request_id = request_id
        self.signals = WorkerSignals()

    def run(self) -> None:
        error = ""
        try:
            self.operation()
        except ServiceError as exc:
            error = str(exc)
        except Exception:  # noqa: BLE001 - Qt worker must not leak exceptions or secrets
            # Never expose exception/HTTP bodies, which may contain credentials or notice data.
            error = "操作失败，输入与草稿已保留；请检查配置后重试。"
        self.signals.finished.emit(self.request_id, error)


class Controller(QObject):
    changed = Signal()
    evidenceSelected = Signal(int, int)

    def __init__(self, service: NotificationService) -> None:
        super().__init__()
        self.service = service
        self.pool = QThreadPool(self)
        self._busy = False
        self._message = ""
        self._selected: Todo | None = None
        self._todos: list[Todo] = []
        self._duplicates: list[Todo] = []
        self._duplicate_source: str | None = None
        self._configured = False
        self._worker: Worker | None = None
        self._operation = ""
        self._request_id = 0
        try:
            self._configured = service.key_configured()
            self._todos = service.list_todos()
        except ServiceError as exc:
            self._message = str(exc)

    @Property(bool, notify=changed)
    def busy(self) -> bool:
        return self._busy

    @Property(str, notify=changed)
    def message(self) -> str:
        return self._message

    @Property(bool, notify=changed)
    def keyConfigured(self) -> bool:
        return self._configured

    @Property(str, constant=True)
    def dataPath(self) -> str:
        return str(self.service.database.resolve())

    @Property(str, notify=changed)
    def sourceText(self) -> str:
        return self._selected.source if self._selected else self.service.source

    @Property(bool, notify=changed)
    def unsaved(self) -> bool:
        return self.service.has_unsaved_changes

    @Property(bool, notify=changed)
    def readOnly(self) -> bool:
        return (
            self._selected is not None
            or self.service.draft is not None
            or (self._busy and self._operation == "save")
        )

    @Property(bool, notify=changed)
    def canSave(self) -> bool:
        return (
            not self._busy
            and self._selected is None
            and self.service.draft is not None
            and self.service.has_unsaved_changes
            and self.service.draft.item.evidence_ready
        )

    @Property("QVariantMap", notify=changed)  # type: ignore[arg-type]
    def resultData(self) -> dict[str, Any]:
        result = self._selected or self.service.draft
        if result is None:
            return {}

        def display(field: CitedText | Deadline) -> dict[str, Any]:
            data = field.model_dump(mode="json")
            statuses = ["用户补充内容"] if field.origin == "user" else []
            if field.review == "invalid":
                statuses.append("依据待核对")
            if isinstance(field, Material) and not field.requirement.strip():
                statuses.append("材料说明未记录，请对照原文核对")
            if isinstance(field, Material) and field.relation_label:
                statuses.append(field.relation_label)
            if isinstance(field, Deadline):
                data["value"] = field.date.isoformat() if field.date else field.raw
                if field.status == "missing":
                    data["value"] = "原文未提及"
                elif field.status == "uncertain":
                    statuses.append("待确认时间")
            data["label"] = " · ".join(statuses)
            return data

        return {
            "action": display(result.item.action),
            "deadline": display(result.item.deadline),
            "materials": [display(m) for m in result.item.materials],
        }

    @Property("QVariantList", notify=changed)  # type: ignore[arg-type]
    def todos(self) -> list[dict[str, str]]:
        return [
            {
                "id": todo.id,
                "title": todo.item.action.value,
                "date": todo.item.deadline.date.isoformat()
                if todo.item.deadline.date
                else (
                    "待确认时间：" + todo.item.deadline.raw
                    if todo.item.deadline.status == "uncertain"
                    else "原文未提及"
                ),
            }
            for todo in self._todos
        ]

    @Property(str, notify=changed)
    def resultHeading(self) -> str:
        if self._selected or (self.service.draft and not self.service.has_unsaved_changes):
            return "已保存的待办"
        return "提取结果 · 待核对"

    def _field(self, name: str) -> CitedText | Deadline | None:
        result = self._selected or self.service.draft
        if result is None:
            return None
        if name == "action":
            return result.item.action
        if name == "deadline":
            return result.item.deadline
        if name.startswith("materials."):
            try:
                index = int(name.split(".")[1])
                return result.item.materials[index] if index >= 0 else None
            except (ValueError, IndexError):
                return None
        return None

    @Slot(str)
    def locateEvidence(self, name: str) -> None:
        field = self._field(name)
        if field is None or field.origin == "user" or field.review != "matched":
            self._message = "该字段暂无可定位的有效原文依据。"
            self.changed.emit()
            return
        result = self._selected or self.service.draft
        if result is None:
            return
        source = result.source
        start, end = field.evidence_start, field.evidence_end
        if start is None or end is None:
            if not field.evidence or source.count(field.evidence) != 1:
                return
            start = source.index(field.evidence)
            end = start + len(field.evidence)
        self.evidenceSelected.emit(
            len(source[:start].encode("utf-16-le")) // 2, len(source[:end].encode("utf-16-le")) // 2
        )

    @Slot(str, str, bool, bool, int, int)
    @Slot(str, str, bool, bool, int, int, str)
    @Slot(str, str, bool, bool, int, int, str, str)
    def correctField(
        self,
        name: str,
        value: str,
        user: bool,
        remove: bool,
        start: int,
        end: int,
        requirement: str | None = None,
        relation: str = "",
    ) -> None:
        if self._busy or self._selected or self.service.draft is None:
            return
        source = self.service.source

        def python_index(position: int) -> int:
            return len(
                source.encode("utf-16-le")[: position * 2].decode("utf-16-le", errors="ignore")
            )

        try:
            self.service.correct_field(
                self.service.draft.id,
                name,
                value,
                user_supplied=user,
                remove=remove,
                evidence_start=python_index(start) if start >= 0 else None,
                evidence_end=python_index(end) if end >= 0 else None,
                requirement=requirement,
                relation=cast(MaterialRelation, relation) if relation else None,
            )
            self._message = "修正已更新，请继续核对。"
        except ServiceError as exc:
            self._message = str(exc)
        self.changed.emit()

    @Property(bool, notify=changed)
    def duplicatePending(self) -> bool:
        return self._duplicate_source is not None

    @Slot(str, int, int)
    def selectEvidence(self, name: str, start: int, end: int) -> None:
        if self._busy or self._selected or self.service.draft is None:
            return
        encoded = self.service.source.encode("utf-16-le")
        try:
            if not 0 <= start < end <= len(encoded) // 2:
                raise ServiceError("请选择通知原文中的依据。")
            self.service.select_evidence(
                self.service.draft.id,
                name,
                len(encoded[: start * 2].decode("utf-16-le", errors="ignore")),
                len(encoded[: end * 2].decode("utf-16-le", errors="ignore")),
            )
            self._message = "依据已更新。"
        except ServiceError as exc:
            self._message = str(exc)
        self.changed.emit()

    @Property("QVariantList", notify=changed)  # type: ignore[arg-type]
    def duplicateTodos(self) -> list[dict[str, str]]:
        return [{"id": todo.id, "title": todo.item.action.value} for todo in self._duplicates]

    def _check_duplicates(self) -> bool:
        self._duplicates = self.service.find_duplicates()
        self._duplicate_source = self.service.source if self._duplicates else None
        return bool(self._duplicates)

    @Slot()
    def cancelDuplicate(self) -> None:
        self._duplicate_source = None
        self.changed.emit()

    @Slot()
    def reextractDuplicate(self) -> None:
        if self._busy or self._selected is not None:
            return
        if self._duplicate_source != self.service.source:
            self.cancelDuplicate()
            return
        self._duplicate_source = None
        self._start(self.service.prepare_extraction(), "extract")

    @Slot(str)
    def openDuplicate(self, todo_id: str) -> None:
        if self._busy or not any(todo.id == todo_id for todo in self._duplicates):
            return
        try:
            todo = self.service.get_todo(todo_id)
            if todo.source != self.service.source:
                return
            self.service.discard(confirmed=True)
            self._selected = todo
            self._duplicate_source = None
            self._message = "待办及原文已从本地数据库读取。"
        except ServiceError as exc:
            self._message = str(exc)
        self.changed.emit()

    @Slot(str)
    def replaceSource(self, text: str) -> None:
        if self._busy or self._selected:
            return
        self.service.set_source(text, discard=True)
        self._check_duplicates()
        self._message = "原文已修改，旧提取结果已失效，请重新提取。"
        self.changed.emit()

    @Slot(str)
    def updateSource(self, text: str) -> None:
        if self._selected is not None or (self._busy and self._operation == "save"):
            return
        try:
            changed = text != self.service.source
            self.service.set_source(text)
            if changed:
                self._request_id += 1
                self._busy = False
                self._message = ""
            self._check_duplicates()
        except ServiceError as exc:
            self._message = str(exc)
        self.changed.emit()

    @Slot(str)
    def configureKey(self, secret: str) -> None:
        if self._busy:
            return
        try:
            self.service.configure_key(secret)
            self._configured = True
            self._message = "API Key 已安全保存到 Windows 凭据存储。"
        except ServiceError as exc:
            self._message = str(exc)
        self.changed.emit()

    def _start(self, operation: Callable[[], object], name: str) -> None:
        if self._busy:
            return
        self._busy = True
        self._operation = name
        self._message = "正在提取，请稍候…" if name == "extract" else "正在保存…"
        self._request_id += 1
        self._worker = Worker(operation, self._request_id)
        self._worker.signals.finished.connect(self._finished)
        self.changed.emit()
        self.pool.start(self._worker)

    @Slot()
    def extract(self) -> None:
        if self._selected is None and not self._busy:
            try:
                if self._check_duplicates():
                    self.changed.emit()
                    return
            except ServiceError as exc:
                self._message = str(exc)
                self.changed.emit()
                return
            self._start(self.service.prepare_extraction(), "extract")

    @Slot()
    def confirm(self) -> None:
        if self.canSave and self.service.draft is not None:
            draft_id = self.service.draft.id
            self._start(lambda: self.service.confirm(draft_id), "save")

    @Slot(int, str)
    def _finished(self, request_id: int, error: str) -> None:
        if request_id != self._request_id:
            return
        self._busy = False
        extract_message = (
            "请逐项核对右侧信息与原文依据，确认无误后保存。"
            if self.service.draft is not None
            else "未发现可办理事项，未生成待办；原文已保留。"
        )
        self._message = error or (
            extract_message if self._operation == "extract" else "已保存，可从待办清单打开。"
        )
        try:
            self._todos = self.service.list_todos()
        except ServiceError as exc:
            self._message = str(exc)
        self.changed.emit()

    @Slot()
    def clearDraft(self) -> None:
        if self._busy and self._operation == "save":
            return
        self._request_id += 1
        self._busy = False
        self.service.discard(confirmed=True)
        self._duplicate_source = None
        self._duplicates = []
        self._selected = None
        self._message = ""
        self.changed.emit()

    @Slot(str)
    def openTodo(self, todo_id: str) -> None:
        if self._busy:
            return
        try:
            self.service.discard()
            self._selected = self.service.get_todo(todo_id)
            self._message = "待办及原文已从本地数据库读取。"
        except ServiceError as exc:
            self._message = str(exc)
        self.changed.emit()

    @Slot()
    def notifyBusyClose(self) -> None:
        self._message = "操作进行中，请等待完成后再退出。"
        self.changed.emit()

    def wait_for_workers(self) -> None:
        self.pool.waitForDone()
