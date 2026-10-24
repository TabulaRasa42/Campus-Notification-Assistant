"""Issue #12 behavior at the agreed public service and QML boundaries."""

from test_evidence_time import service_for

from campus_notification_assistant.service import NotificationService


def test_material_process_and_evidence_survive_confirmation_and_restart(tmp_path):
    source = (
        "【设备借用申请】请于2026年11月19日前申请借用设备。"
        "请提前下载并打印一份设备借用申请表，申请时自行携带，现场签署后提交。"
    )
    requirement = "提前下载打印一份设备借用申请表，申请时自行携带，现场签署后提交"
    evidence = "请提前下载并打印一份设备借用申请表，申请时自行携带，现场签署后提交。"
    service = service_for(
        tmp_path,
        source,
        {
            "action": {"value": "设备借用申请", "evidence": "申请借用设备"},
            "deadline": {"date": "2026-11-19", "evidence": "2026年11月19日前"},
            "materials": [
                {"value": "设备借用申请表", "requirement": requirement, "evidence": evidence}
            ],
        },
    )
    draft = service.extract(api_key="test")
    assert draft.item.materials[0].requirement == requirement
    assert draft.item.materials[0].review == "matched"
    assert NotificationService(service.database).list_todos() == []
    todo = service.confirm(draft.id)
    restored = NotificationService(service.database).get_todo(todo.id)
    assert restored.item.materials[0].requirement == requirement
    assert (
        restored.source[
            restored.item.materials[0].evidence_start : restored.item.materials[0].evidence_end
        ]
        == evidence
    )


def test_user_can_correct_material_requirement_and_cite_complete_condition(tmp_path):
    source = "请报名。首次报名者提交学生证复印件，往年已报名者免交。"
    service = service_for(
        tmp_path,
        source,
        {
            "action": {"value": "报名", "evidence": "请报名"},
            "materials": [
                {
                    "value": "学生证复印件",
                    "requirement": "所有人提交学生证复印件",
                    "evidence": "学生证复印件",
                }
            ],
        },
    )
    draft = service.extract(api_key="test")
    corrected = service.correct_field(
        draft.id,
        "materials.0",
        "学生证复印件",
        requirement="仅首次报名者提交学生证复印件，往年已报名者免交",
        evidence_start=4,
        evidence_end=len(source),
    )
    material = corrected.item.materials[0]
    assert material.requirement == "仅首次报名者提交学生证复印件，往年已报名者免交"
    assert material.evidence == "首次报名者提交学生证复印件，往年已报名者免交。"
    assert material.origin == "extracted"
    todo = service.confirm(draft.id)
    assert NotificationService(service.database).get_todo(todo.id).item.materials[0] == material


def test_qml_material_requirement_is_visible_editable_and_reloaded(qapp, qtbot, tmp_path):
    from test_qml import click, control

    from campus_notification_assistant.app import create_engine

    source = "😀申请门禁。申请时须提交申请表，并携带学生证或校园卡任一原件供核验。"
    evidence = "申请时须提交申请表，并携带学生证或校园卡任一原件供核验。"
    service = service_for(
        tmp_path,
        source,
        {
            "action": {"value": "申请门禁", "evidence": "申请门禁"},
            "materials": [
                {
                    "value": "学生证或校园卡任一原件",
                    "requirement": "申请时携带学生证或校园卡任一原件供核验",
                    "evidence": evidence,
                }
            ],
        },
    )
    service.extract(api_key="test")
    engine, controller = create_engine(service)
    root = engine.rootObjects()[0]
    try:
        assert control(root, "materialRequirement_0").property("text") == (
            "申请时携带学生证或校园卡任一原件供核验"
        )
        click(root, "materialValue_0")
        qtbot.waitUntil(lambda: control(root, "sourceInput").property("selectedText") == evidence)
        results = control(root, "resultsScrollView").property("contentItem")
        results.setProperty(
            "contentY", max(0, results.property("contentHeight") - results.height())
        )
        qapp.processEvents()
        click(root, "correction_materials.0")
        qtbot.waitUntil(lambda: control(root, "correctionDialog").property("opened"))
        assert control(root, "correctionRequirement").property("text") == (
            "申请时携带学生证或校园卡任一原件供核验"
        )
        control(root, "correctionRequirement").setProperty(
            "text", "申请时携带两证中的任一原件，供核验"
        )
        control(root, "correctionDialog").accept()
        qtbot.waitUntil(
            lambda: (
                control(root, "materialRequirement_0").property("text")
                == ("申请时携带两证中的任一原件，供核验")
            )
        )
        assert controller.canSave
        click(root, "confirmButton")
        qtbot.waitUntil(lambda: len(service.list_todos()) == 1)
        qtbot.waitUntil(lambda: not controller.busy)
        saved_id = service.list_todos()[0].id
    finally:
        controller.wait_for_workers()
        root.setProperty("allowClose", True)
        root.close()
        engine.deleteLater()
        qapp.processEvents()
    restarted = NotificationService(service.database)
    engine2, controller2 = create_engine(restarted)
    root2 = engine2.rootObjects()[0]
    try:
        controller2.openTodo(saved_id)
        qtbot.waitUntil(lambda: bool(control(root2, "materialRequirement_0").property("visible")))
        assert control(root2, "materialRequirement_0").property("text") == (
            "申请时携带两证中的任一原件，供核验"
        )
        click(root2, "materialValue_0")
        qtbot.waitUntil(lambda: control(root2, "sourceInput").property("selectedText") == evidence)
        assert not control(root2, "confirmButton").property("enabled")
    finally:
        root2.setProperty("allowClose", True)
        root2.close()
        engine2.deleteLater()
        qapp.processEvents()


def test_legacy_material_is_not_reinterpreted_as_unconditional(qapp, tmp_path):
    import json
    import sqlite3

    from campus_notification_assistant.app import create_engine

    service = NotificationService(tmp_path / "legacy.sqlite3")
    # Seed a historical row, before requirement existed. Read only through public operations.
    legacy = {
        "id": "historical",
        "source": "请报名。仅首次报名者提交学生证复印件。",
        "item": {
            "action": {"value": "报名", "evidence": "请报名"},
            "materials": [{"value": "学生证复印件", "evidence": "学生证复印件"}],
        },
    }
    with sqlite3.connect(service.database) as db:
        db.execute("INSERT INTO todos VALUES (?, ?)", ("historical", json.dumps(legacy)))
    before = service.get_todo("historical")
    assert before.item.materials[0].requirement == ""
    engine, controller = create_engine(service)
    root = engine.rootObjects()[0]
    try:
        controller.openTodo("historical")
        assert "材料说明未记录" in controller.resultData["materials"][0]["label"]
        assert controller.resultData["materials"][0]["value"] == "学生证复印件"
        assert not controller.canSave
        assert NotificationService(service.database).get_todo("historical") == before
    finally:
        root.setProperty("allowClose", True)
        root.close()
        engine.deleteLater()
        qapp.processEvents()
