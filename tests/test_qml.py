import threading
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from PySide6.QtCore import QObject, Qt, QTimer
from PySide6.QtGui import QFontDatabase
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest
from test_service import SOURCE, ai_response

from campus_notification_assistant.app import create_engine
from campus_notification_assistant.service import NotificationService


@pytest.fixture
def ui(qapp, tmp_path, request):
    # The offscreen Windows plugin may not discover installed CJK fonts itself.
    font_path = Path("C:/Windows/Fonts/msyh.ttc")
    if font_path.is_file():
        QFontDatabase.addApplicationFont(str(font_path))
    request_config = request
    namespace = "campus-notification-assistant.qt-test." + str(uuid4())
    release = threading.Event()
    started = threading.Event()

    def delayed(request):
        started.set()
        assert release.wait(10)
        if getattr(request_config, "param", "success") == "failure":
            return httpx.Response(401)
        if getattr(request_config, "param", "success") == "empty":
            return httpx.Response(
                200,
                json={
                    "choices": [{"finish_reason": "stop", "message": {"content": '{"items":[]}'}}]
                },
            )
        return ai_response(request)

    service = NotificationService(
        tmp_path / "todos.sqlite3",
        credential_namespace=namespace,
        transport=httpx.MockTransport(delayed),
    )
    service.configure_key("isolated-ui-test-credential")
    engine, controller = create_engine(service)
    root = engine.rootObjects()[0]
    try:
        yield root, service, started, release
    finally:
        release.set()
        controller.wait_for_workers()
        service.clear_key()
        root.setProperty("allowClose", True)
        root.close()
        engine.deleteLater()
        qapp.processEvents()


def find_control(root, name):
    found = root.findChild(QObject, name)
    pending = [root.contentItem()]
    while found is None and pending:
        item = pending.pop()
        if item.objectName() == name:
            return item
        pending.extend(item.childItems())
    return found


def control(root, name):
    found = find_control(root, name)
    assert found is not None, name
    return found


def click(root, name):
    item = control(root, name)
    assert isinstance(item, QQuickItem)
    point = item.mapToScene(item.boundingRect().center()).toPoint()
    QTest.mouseClick(root, Qt.MouseButton.LeftButton, pos=point)


def test_qml_background_extract_review_save_and_open(ui, qtbot):
    root, service, started, release = ui
    control(root, "sourceInput").setProperty("text", SOURCE)
    click(root, "extractButton")
    qtbot.waitUntil(started.is_set)
    assert control(root, "loadingIndicator").property("running")
    assert not control(root, "confirmButton").property("enabled")
    ticks = []
    QTimer.singleShot(0, lambda: ticks.append(True))
    qtbot.waitUntil(lambda: bool(ticks))
    release.set()
    qtbot.waitUntil(lambda: control(root, "confirmButton").property("enabled"))
    assert control(root, "sourceInput").property("text") == SOURCE
    assert "完成实践项目报名" in control(root, "actionValue").property("text")
    assert "2026-11-15" in control(root, "deadlineValue").property("text")
    assert service.list_todos() == []
    click(root, "confirmButton")
    qtbot.waitUntil(lambda: len(service.list_todos()) == 1)
    qtbot.waitUntil(lambda: not control(root, "loadingIndicator").property("running"))
    assert not control(root, "confirmButton").property("enabled")
    click(root, "newButton")
    qtbot.waitUntil(lambda: control(root, "sourceInput").property("text") == "")
    qtbot.waitUntil(lambda: find_control(root, "todoButton") is not None)
    click(root, "todoButton")
    assert control(root, "sourceInput").property("text") == SOURCE
    click(root, "chooseEvidence_deadline")
    qtbot.waitUntil(lambda: control(root, "evidenceDialog").property("opened"))
    qtbot.waitUntil(
        lambda: "2026年11月15日前" in control(root, "evidenceInput").property("selectedText")
    )
    assert not control(root, "evidenceApply").property("visible")
    control(root, "evidenceDialog").reject()


def test_qml_prompts_before_discarding_unsaved_input(ui, qtbot):
    root, service, _started, _release = ui
    control(root, "sourceInput").setProperty("text", SOURCE)
    click(root, "newButton")
    qtbot.waitUntil(lambda: control(root, "discardDialog").property("opened"))
    assert service.source == SOURCE
    control(root, "discardDialog").reject()
    assert control(root, "sourceInput").property("text") == SOURCE


@pytest.mark.parametrize("ui", ["failure"], indirect=True)
def test_qml_failure_retains_input_and_allows_retry(ui, qtbot):
    root, service, started, release = ui
    control(root, "sourceInput").setProperty("text", SOURCE)
    click(root, "extractButton")
    qtbot.waitUntil(started.is_set)
    release.set()
    qtbot.waitUntil(lambda: not control(root, "loadingIndicator").property("running"))
    assert control(root, "sourceInput").property("text") == SOURCE
    assert "密钥无效" in control(root, "statusMessage").property("text")
    assert control(root, "extractButton").property("enabled")
    assert not control(root, "confirmButton").property("enabled")
    assert service.list_todos() == []


def test_qml_key_configuration_does_not_leave_secret_in_field(ui, qtbot):
    root, service, _started, _release = ui
    click(root, "keyButton")
    qtbot.waitUntil(lambda: control(root, "keyDialog").property("opened"))
    secret_input = control(root, "secretInput")
    secret_input.setProperty("text", "replacement-ui-test-key")
    control(root, "keyDialog").accept()
    assert secret_input.property("text") == ""
    assert service.key_configured()
    assert "已安全保存" in control(root, "statusMessage").property("text")


def test_qml_error_configuration_and_successful_recovery(qapp, qtbot, tmp_path):
    attempts = []

    def response(request):
        attempts.append(request.headers["Authorization"])
        if request.headers["Authorization"] == "Bearer wrong-key":
            return httpx.Response(401)
        return ai_response(request)

    service = NotificationService(
        tmp_path / "recover.sqlite3", transport=httpx.MockTransport(response)
    )
    service.configure_key("wrong-key")
    engine, controller = create_engine(service)
    root = engine.rootObjects()[0]
    try:
        control(root, "sourceInput").setProperty("text", SOURCE)
        click(root, "extractButton")
        qtbot.waitUntil(lambda: "密钥无效" in controller.message)
        assert not controller.busy
        assert not controller.canSave
        assert service.source == SOURCE
        click(root, "keyButton")
        qtbot.waitUntil(lambda: control(root, "keyDialog").property("opened"))
        control(root, "secretInput").setProperty("text", "correct-key")
        control(root, "keyDialog").accept()
        click(root, "extractButton")
        qtbot.waitUntil(lambda: controller.canSave)
        assert len(attempts) == 2
        assert service.list_todos() == []
    finally:
        controller.wait_for_workers()
        root.setProperty("allowClose", True)
        root.close()
        engine.deleteLater()
        qapp.processEvents()


def test_qml_editing_source_ignores_old_async_completion(ui, qtbot):
    root, service, started, release = ui
    control(root, "sourceInput").setProperty("text", SOURCE)
    click(root, "extractButton")
    qtbot.waitUntil(started.is_set)
    control(root, "sourceInput").setProperty("text", "新的通知")
    qtbot.waitUntil(lambda: service.source == "新的通知")
    release.set()
    qtbot.waitUntil(lambda: not control(root, "loadingIndicator").property("running"))
    # Process the queued worker-finished signal too, not only the immediate input update.
    qtbot.wait(100)
    assert service.draft is None
    assert control(root, "sourceInput").property("text") == "新的通知"
    assert not control(root, "confirmButton").property("enabled")
    assert control(root, "statusMessage").property("text") == ""


def test_qml_duplicate_cancel_and_view_do_not_send_ai(ui, qtbot):
    root, service, started, release = ui
    release.set()
    service.set_source(SOURCE)
    first = service.confirm(service.extract(api_key="test-key").id)
    second = service.confirm(service.extract(api_key="test-key").id)
    service.discard(confirmed=True)
    started.clear()
    control(root, "sourceInput").setProperty("text", SOURCE)
    qtbot.waitUntil(lambda: control(root, "duplicateDialog").property("opened"))
    click(root, "duplicateCancelButton")
    assert not started.is_set()
    assert service.source == SOURCE
    assert {t.id for t in service.list_todos()} == {first.id, second.id}
    click(root, "extractButton")
    qtbot.waitUntil(lambda: control(root, "duplicateDialog").property("opened"))
    click(root, "duplicateViewButton")
    qtbot.waitUntil(lambda: control(root, "duplicateListDialog").property("opened"))
    assert control(root, "duplicateListDialog").property("matchCount") == 2
    click(root, "duplicateTodoButton")
    qtbot.waitUntil(
        lambda: control(root, "actionValue").property("text") == first.item.action.value
    )
    assert not started.is_set()
    assert control(root, "sourceInput").property("text") == SOURCE
    assert not control(root, "confirmButton").property("enabled")


def test_qml_duplicate_reextract_requires_confirmation_and_preserves_original(ui, qtbot):
    root, service, started, release = ui
    release.set()
    service.set_source(SOURCE)
    original = service.confirm(service.extract(api_key="test-key").id)
    service.discard(confirmed=True)
    started.clear()
    control(root, "sourceInput").setProperty("text", SOURCE)
    qtbot.waitUntil(lambda: control(root, "duplicateDialog").property("opened"))
    assert not started.is_set()
    click(root, "duplicateContinueButton")
    qtbot.waitUntil(lambda: control(root, "confirmButton").property("enabled"))
    assert started.is_set()
    assert service.list_todos() == [original]
    click(root, "confirmButton")
    qtbot.waitUntil(lambda: len(service.list_todos()) == 2)
    qtbot.waitUntil(lambda: not control(root, "loadingIndicator").property("running"))
    assert service.get_todo(original.id) == original
    assert len({todo.id for todo in service.find_duplicates(SOURCE)}) == 2


@pytest.mark.parametrize("ui", ["empty"], indirect=True)
def test_qml_empty_extraction_has_explicit_message_and_cannot_save(ui, qtbot):
    root, service, started, release = ui
    control(root, "sourceInput").setProperty("text", SOURCE)
    click(root, "extractButton")
    qtbot.waitUntil(started.is_set)
    release.set()
    qtbot.waitUntil(lambda: not control(root, "loadingIndicator").property("running"))
    assert "未发现可办理事项" in control(root, "statusMessage").property("text")
    assert not control(root, "confirmButton").property("enabled")
    assert control(root, "sourceInput").property("text") == SOURCE
    assert service.list_todos() == []


def test_qml_saved_todo_edit_requires_explicit_save(ui, qtbot):
    root, service, _started, release = ui
    release.set()
    control(root, "sourceInput").setProperty("text", SOURCE)
    click(root, "extractButton")
    qtbot.waitUntil(lambda: control(root, "confirmButton").property("enabled"))
    click(root, "confirmButton")
    qtbot.waitUntil(lambda: not control(root, "loadingIndicator").property("running"))
    todo = service.list_todos()[0]
    click(root, "newButton")
    qtbot.waitUntil(lambda: control(root, "sourceInput").property("text") == "")
    click(root, "todoButton")
    qtbot.waitUntil(lambda: control(root, "editTodoButton").property("visible"))
    qtbot.wait(100)
    click(root, "editTodoButton")
    qtbot.waitUntil(lambda: control(root, "actionCorrection").property("visible"))
    qtbot.wait(100)
    click(root, "actionCorrection")
    qtbot.waitUntil(lambda: control(root, "correctionDialog").property("opened"))
    control(root, "correctionValue").setProperty("text", "我的报名")
    control(root, "supplement").setProperty("checked", True)
    control(root, "correctionDialog").accept()
    assert service.get_todo(todo.id).item.action.value == todo.item.action.value
    click(root, "newButton")
    qtbot.waitUntil(lambda: control(root, "discardDialog").property("opened"))
    control(root, "discardDialog").reject()
    click(root, "saveTodoButton")
    qtbot.waitUntil(lambda: service.get_todo(todo.id).item.action.value == "我的报名")
    qtbot.waitUntil(lambda: not control(root, "loadingIndicator").property("running"))
    assert service.get_todo(todo.id).source == SOURCE
    click(root, "completeTodoButton")
    assert service.get_todo(todo.id).completed
    click(root, "completeTodoButton")
    assert not service.get_todo(todo.id).completed
    click(root, "deleteTodoButton")
    qtbot.waitUntil(lambda: control(root, "deleteTodoDialog").property("opened"))
    control(root, "deleteTodoDialog").accept()
    assert service.list_todos() == []


def test_qml_saved_edit_failure_retains_changes_and_retry(ui, qtbot):
    import sqlite3

    root, service, _started, release = ui
    release.set()
    control(root, "sourceInput").setProperty("text", SOURCE)
    click(root, "extractButton")
    qtbot.waitUntil(lambda: control(root, "confirmButton").property("enabled"))
    click(root, "confirmButton")
    qtbot.waitUntil(lambda: not control(root, "loadingIndicator").property("running"))
    todo = service.list_todos()[0]
    click(root, "newButton")
    qtbot.waitUntil(lambda: control(root, "sourceInput").property("text") == "")
    click(root, "todoButton")
    qtbot.waitUntil(lambda: control(root, "editTodoButton").property("visible"))
    qtbot.wait(100)
    click(root, "editTodoButton")
    qtbot.wait(100)
    click(root, "actionCorrection")
    qtbot.waitUntil(lambda: control(root, "correctionDialog").property("opened"))
    control(root, "correctionValue").setProperty("text", "失败后保留")
    control(root, "supplement").setProperty("checked", True)
    control(root, "correctionDialog").accept()
    with sqlite3.connect(service.database) as locked:
        locked.execute("BEGIN IMMEDIATE")
        click(root, "saveTodoButton")
        qtbot.waitUntil(lambda: "保存失败" in control(root, "statusMessage").property("text"))
        assert control(root, "actionValue").property("text") == "失败后保留"
        assert control(root, "saveTodoButton").property("enabled")
        assert service.get_todo(todo.id) == todo
    click(root, "saveTodoButton")
    qtbot.waitUntil(lambda: service.get_todo(todo.id).item.action.value == "失败后保留")
    qtbot.waitUntil(lambda: not control(root, "loadingIndicator").property("running"))
    assert "已保存" in control(root, "statusMessage").property("text")


def test_qml_saved_material_and_cancel_edit_at_minimum_size(ui, qtbot):
    root, service, _started, release = ui
    release.set()
    root.setWidth(960)
    root.setHeight(700)
    control(root, "sourceInput").setProperty("text", SOURCE)
    click(root, "extractButton")
    qtbot.waitUntil(lambda: control(root, "confirmButton").property("enabled"))
    click(root, "confirmButton")
    qtbot.waitUntil(lambda: not control(root, "loadingIndicator").property("running"))
    todo = service.list_todos()[0]
    click(root, "newButton")
    qtbot.waitUntil(lambda: control(root, "sourceInput").property("text") == "")
    click(root, "todoButton")
    qtbot.waitUntil(lambda: control(root, "editTodoButton").property("visible"))
    qtbot.wait(100)
    click(root, "editTodoButton")
    qtbot.wait(100)
    scroll = control(root, "resultsScrollView").property("contentItem")
    scroll.setProperty("contentY", control(root, "addTodoMaterialButton").y())
    qtbot.wait(100)
    click(root, "addTodoMaterialButton")
    qtbot.waitUntil(lambda: control(root, "correctionDialog").property("opened"))
    control(root, "correctionValue").setProperty("text", "个人补充材料")
    control(root, "correctionRequirement").setProperty("text", "自愿补充电子版")
    control(root, "supplement").setProperty("checked", True)
    control(root, "correctionDialog").accept()
    assert service.todo_edit.item.materials[-1].value == "个人补充材料"
    assert service.todo_edit.item.materials[-1].origin == "user"
    assert service.get_todo(todo.id) == todo
    click(root, "cancelTodoEditButton")
    qtbot.waitUntil(lambda: control(root, "cancelTodoEditDialog").property("opened"))
    click(root, "discardTodoEditButton")
    assert service.todo_edit is None
    assert service.get_todo(todo.id) == todo
