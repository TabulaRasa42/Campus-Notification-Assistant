import threading
from uuid import uuid4

import httpx
import pytest
from PySide6.QtCore import QObject, Qt, QTimer
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest
from test_service import SOURCE, ai_response

from campus_notification_assistant.app import create_engine
from campus_notification_assistant.service import NotificationService


@pytest.fixture
def ui(qapp, tmp_path, request):
    request_config = request
    namespace = "campus-notification-assistant.qt-test." + str(uuid4())
    release = threading.Event()
    started = threading.Event()

    def delayed(request):
        started.set()
        assert release.wait(10)
        if getattr(request_config, "param", "success") == "failure":
            return httpx.Response(401)
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
    assert "2026年11月15日前" in control(root, "deadlineEvidence").property("text")


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
