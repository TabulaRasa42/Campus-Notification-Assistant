import json
import sqlite3

import httpx
import pytest

from campus_notification_assistant.service import NotificationService, ServiceError

SOURCE = (
    "请于2027年4月9日前申请补助。转专业学生申请时提交成绩单，其他学生免交。"
    "请于2027年4月12日前办理住宿。办理住宿时须携带学生证或身份证任一。"
)
CONTENT = {
    "items": [
        {
            "action": {"value": "申请补助", "evidence": "申请补助"},
            "deadline": {"date": "2027-04-09", "evidence": "2027年4月9日前"},
            "materials": [
                {
                    "value": "成绩单",
                    "requirement": "转专业学生申请时提交成绩单，其他学生免交",
                    "relation": "single",
                    "evidence": "转专业学生申请时提交成绩单，其他学生免交。",
                }
            ],
        },
        {
            "action": {"value": "办理住宿", "evidence": "请于2027年4月12日前办理住宿。"},
            "deadline": {"date": "2027-04-12", "evidence": "2027年4月12日前"},
            "materials": [
                {
                    "value": "学生证或身份证",
                    "requirement": "办理住宿时须携带学生证或身份证任一",
                    "relation": "any",
                    "evidence": "办理住宿时须携带学生证或身份证任一。",
                }
            ],
        },
    ]
}


def service_for(tmp_path, content=None):
    response = httpx.Response(
        200,
        json={
            "choices": [
                {"finish_reason": "stop", "message": {"content": json.dumps(content or CONTENT)}}
            ]
        },
    )
    service = NotificationService(
        tmp_path / "multi.sqlite3", transport=httpx.MockTransport(lambda r: response)
    )
    service.set_source(SOURCE)
    return service


def test_multi_actions_keep_own_materials_and_save_as_one_batch(tmp_path):
    service = service_for(tmp_path)
    service.extract(api_key="offline")
    batch = service.batch
    assert len(batch.entries) == 2
    assert [entry.item.action.value for entry in batch.entries] == ["申请补助", "办理住宿"]
    assert [[m.value for m in entry.item.materials] for entry in batch.entries] == [
        ["成绩单"],
        ["学生证或身份证"],
    ]
    saved = service.confirm_selected(batch.id)
    assert len(saved) == 2
    restarted = NotificationService(service.database)
    assert {todo.id for todo in restarted.list_todos()} == {entry.id for entry in batch.entries}
    assert all(restarted.get_todo(todo.id).source == SOURCE for todo in saved)
    assert restarted.get_todo(saved[1].id).item.materials[0].relation == "any"


def test_add_remove_correct_and_select_only_valid_items(tmp_path):
    service = service_for(tmp_path)
    service.extract(api_key="offline")
    batch = service.batch
    first, second = batch.entries
    service.focus_item(batch.id, second.id)
    service.correct_field(batch.id, "action", "宿舍申请", user_supplied=True)
    service.add_material(
        batch.id,
        "照片",
        user_supplied=True,
        requirement="本人补充：办理住宿携带一张照片",
        relation="single",
    )
    service.correct_field(batch.id, "materials.0", "", remove=True)
    third = service.add_item(batch.id)
    assert third.id not in {first.id, second.id}
    with pytest.raises(ServiceError, match="不能为空"):
        service.confirm_selected(batch.id)
    service.set_item_selected(batch.id, third.id, False)
    service.remove_item(batch.id, first.id)
    saved = service.confirm_selected(batch.id)
    assert len(saved) == 1 and saved[0].id == second.id
    assert saved[0].item.action.value == "宿舍申请"
    assert saved[0].item.action.origin == "user"
    assert [m.value for m in saved[0].item.materials] == ["照片"]
    assert "本人补充" in saved[0].item.materials[0].requirement
    assert third.id not in {todo.id for todo in service.list_todos()}


def test_write_failure_rolls_back_whole_batch_and_retry_keeps_ids(tmp_path):
    service = service_for(tmp_path)
    service.extract(api_key="offline")
    batch = service.batch
    second = batch.entries[1]
    with sqlite3.connect(service.database) as connection:
        connection.execute(
            "CREATE TRIGGER fail_second BEFORE INSERT ON todos WHEN NEW.id = '"
            + second.id
            + "' BEGIN SELECT RAISE(ABORT, 'test write failure'); END"
        )
    with pytest.raises(ServiceError, match="保存失败"):
        service.confirm_selected(batch.id)
    assert NotificationService(service.database).list_todos() == []
    assert service.batch == batch and service.has_unsaved_changes
    service.focus_item(batch.id, second.id)
    service.correct_field(batch.id, "action", "住宿办理", user_supplied=True)
    with sqlite3.connect(service.database) as connection:
        connection.execute("DROP TRIGGER fail_second")
    saved = service.confirm_selected(batch.id)
    assert [todo.id for todo in saved] == [entry.id for entry in batch.entries]
    assert saved[1].item.action.value == "住宿办理"
    assert not service.has_unsaved_changes


def test_batch_receipt_survives_deletion_restart_and_repeated_confirm(tmp_path):
    service = service_for(tmp_path)
    service.extract(api_key="offline")
    batch = service.batch
    original = service.confirm_selected(batch.id)
    with sqlite3.connect(service.database) as connection:
        connection.execute("DELETE FROM todos WHERE id = ?", (original[0].id,))
        edited = original[1].model_copy(
            update={
                "item": original[1].item.model_copy(
                    update={
                        "action": original[1].item.action.model_copy(update={"value": "后续已修改"})
                    }
                )
            }
        )
        connection.execute(
            "UPDATE todos SET data = ? WHERE id = ?", (edited.model_dump_json(), edited.id)
        )
    restarted = NotificationService(service.database)
    assert restarted.confirm_selected(batch.id) == original
    assert restarted.list_todos() == [edited]
    with pytest.raises(ServiceError, match="已保存"):
        service.add_item(batch.id, "不能新增")


def test_unselected_invalid_evidence_does_not_block_valid_selected_item(tmp_path):
    content = json.loads(json.dumps(CONTENT))
    content["items"][0]["action"]["evidence"] = "不存在"
    service = service_for(tmp_path, content)
    service.extract(api_key="offline")
    batch = service.batch
    with pytest.raises(ServiceError, match="依据"):
        service.confirm_selected(batch.id)
    service.set_item_selected(batch.id, batch.entries[0].id, False)
    saved = service.confirm_selected(batch.id)
    assert [todo.id for todo in saved] == [batch.entries[1].id]


def test_distinct_deadlines_stay_associated_through_corrections_and_restart(tmp_path):
    service = service_for(tmp_path)
    service.extract(api_key="offline")
    batch = service.batch
    assert [entry.item.deadline.date.isoformat() for entry in batch.entries] == [
        "2027-04-09",
        "2027-04-12",
    ]
    service.focus_item(batch.id, batch.entries[1].id)
    service.correct_field(batch.id, "action", "住宿办理", user_supplied=True)
    quote = "2027年4月12日前"
    start = SOURCE.index(quote)
    service.select_evidence(batch.id, "deadline", start, start + len(quote))
    saved = service.confirm_selected(batch.id)
    assert [todo.item.deadline.date.isoformat() for todo in saved] == ["2027-04-09", "2027-04-12"]
    restarted = NotificationService(service.database)
    assert all(restarted.get_todo(todo.id).deadline_scope for todo in saved)


def test_empty_extraction_can_be_repaired_by_adding_an_omitted_action(tmp_path):
    service = service_for(tmp_path, {"items": []})
    assert service.extract(api_key="offline") is None
    assert service.draft is None
    batch = service.batch
    entry = service.add_item(batch.id, "用户补充事项")
    assert service.confirm_selected(batch.id)[0].id == entry.id


@pytest.mark.parametrize(
    "middle,first_status", [("，", "certain"), ("。另写2027年4月10日截止。", "uncertain")]
)
def test_item_date_scope_handles_distinct_clauses_but_keeps_same_item_conflict(
    tmp_path, middle, first_status
):
    source = "请于2027年4月9日前申请补助" + middle + "请于2027年4月12日前办理住宿。"
    content = {
        "items": [
            {
                "action": {"value": "申请补助", "evidence": "申请补助"},
                "deadline": {"date": "2027-04-09", "evidence": "2027年4月9日前"},
            },
            {
                "action": {"value": "办理住宿", "evidence": "办理住宿"},
                "deadline": {"date": "2027-04-12", "evidence": "2027年4月12日前"},
            },
        ]
    }
    if first_status == "uncertain":
        # The first item explicitly cites both conflicting dates; the second is independent.
        content["items"][0]["deadline"]["evidence"] = "2027年4月9日前申请补助" + middle
    service = service_for(tmp_path, content)
    service.set_source(source)
    service.extract(api_key="offline")
    assert service.batch.entries[0].item.deadline.status == first_status
    assert service.batch.entries[1].item.deadline.date.isoformat() == "2027-04-12"
    service.confirm_selected(service.batch.id)


def test_no_selection_and_empty_batch_cannot_create_todos(tmp_path):
    service = service_for(tmp_path)
    service.extract(api_key="offline")
    batch = service.batch
    for entry in batch.entries:
        service.set_item_selected(batch.id, entry.id, False)
    with pytest.raises(ServiceError, match="至少选择"):
        service.confirm_selected(batch.id)
    for entry in batch.entries:
        service.remove_item(batch.id, entry.id)
    assert service.draft is None
    with pytest.raises(ServiceError, match="至少选择"):
        service.confirm_selected(batch.id)
    assert service.list_todos() == []


def test_blank_new_item_can_be_filled_without_leaking_validation_errors(tmp_path):
    service = service_for(tmp_path)
    service.extract(api_key="offline")
    batch = service.batch
    service.add_item(batch.id)
    with pytest.raises(ServiceError, match="事项内容"):
        service.correct_field(batch.id, "deadline", "4月12日", user_supplied=True)
    with pytest.raises(ServiceError, match="事项内容"):
        service.add_material(batch.id, "照片", user_supplied=True)
    service.correct_field(batch.id, "action", "办理住宿", user_supplied=True)
    assert service.confirm_selected(batch.id)[-1].item.action.value == "办理住宿"


@pytest.mark.parametrize("size", [(960, 700), (1180, 820)])
def test_qml_multi_item_review_add_remove_material_and_save(qapp, qtbot, tmp_path, size):
    from pathlib import Path

    from PySide6.QtGui import QFontDatabase
    from test_qml import click, control

    from campus_notification_assistant.app import create_engine

    service = service_for(tmp_path)
    service.extract(api_key="offline")
    for font in ("msyh.ttc", "msyhbd.ttc"):
        path = Path("C:/Windows/Fonts") / font
        if path.exists():
            QFontDatabase.addApplicationFont(str(path))
    engine, controller = create_engine(service)
    root = engine.rootObjects()[0]
    root.setWidth(size[0])
    root.setHeight(size[1])
    try:
        qtbot.waitUntil(lambda: control(root, "actionValue").property("width") > 0)
        assert control(root, "draftPosition").property("text") == "1 / 2"
        click(root, "nextDraft")
        qtbot.waitUntil(lambda: control(root, "actionValue").property("text") == "办理住宿")
        click(root, "draftSelected")
        assert controller.selectedCount == 1
        click(root, "addDraft")
        assert control(root, "draftPosition").property("text") == "3 / 3"
        assert not controller.canSave
        click(root, "actionCorrection")
        qtbot.waitUntil(lambda: control(root, "correctionDialog").property("opened"))
        control(root, "correctionValue").setProperty("text", "补交说明")
        control(root, "supplement").setProperty("checked", True)
        control(root, "correctionDialog").accept()
        click(root, "removeDraft")
        assert len(service.batch.entries) == 2
        results = control(root, "resultsScrollView").property("contentItem")
        results.setProperty(
            "contentY", max(0, results.property("contentHeight") - results.height())
        )
        qapp.processEvents()
        click(root, "addMaterial")
        qtbot.waitUntil(lambda: control(root, "correctionDialog").property("opened"))
        control(root, "correctionValue").setProperty("text", "照片")
        control(root, "correctionRequirement").setProperty("text", "用户补充：携带照片")
        control(root, "supplement").setProperty("checked", True)
        control(root, "correctionDialog").accept()
        assert service.draft.item.materials[-1].value == "照片"
        assert controller.canSave
        results.setProperty("contentY", 0)
        qapp.processEvents()
        root.grabWindow().save(f".scratch/issue4-{size[0]}.png")
        click(root, "confirmButton")
        qtbot.waitUntil(lambda: len(service.list_todos()) == 1)
        qtbot.waitUntil(lambda: not controller.busy)
        saved = service.list_todos()[0]
        assert saved.item.action.value == "申请补助"
        assert saved.item.materials[-1].value == "照片"
    finally:
        controller.wait_for_workers()
        root.setProperty("allowClose", True)
        root.close()
        engine.deleteLater()
        qapp.processEvents()


def test_qml_added_action_and_material_require_explicit_evidence(qapp, qtbot, tmp_path):
    from test_qml import click, control

    from campus_notification_assistant.app import create_engine

    content = {"items": [CONTENT["items"][0]]}
    service = service_for(tmp_path, content)
    service.extract(api_key="offline")
    engine, controller = create_engine(service)
    root = engine.rootObjects()[0]

    def reveal(name):
        item = control(root, name)
        viewport = control(root, "resultsScrollView").property("contentItem")

        def visible_after_scroll():
            center = item.mapToScene(item.boundingRect().center()).y()
            top = viewport.mapToScene(viewport.boundingRect().topLeft()).y()
            if top + 8 < center < top + viewport.height() - 8:
                return True
            viewport.setProperty(
                "contentY",
                max(0, viewport.property("contentY") + center - top - viewport.height() / 2),
            )
            return False

        qtbot.waitUntil(visible_after_scroll)

    def apply_evidence(field, quote):
        reveal("chooseEvidence_" + field)
        click(root, "chooseEvidence_" + field)
        qtbot.waitUntil(lambda: control(root, "evidenceDialog").property("opened"))
        start = SOURCE.index(quote)
        control(root, "evidenceInput").select(start, start + len(quote))
        click(root, "evidenceApply")
        qtbot.waitUntil(lambda: not control(root, "evidenceDialog").property("opened"))

    try:
        qtbot.waitUntil(lambda: control(root, "actionValue").property("width") > 0)
        click(root, "addDraft")
        click(root, "actionCorrection")
        qtbot.waitUntil(lambda: control(root, "correctionDialog").property("opened"))
        control(root, "correctionValue").setProperty("text", "办理住宿")
        control(root, "supplement").setProperty("checked", False)
        control(root, "correctionDialog").accept()
        assert not controller.canSave
        apply_evidence("action", "请于2027年4月12日前办理住宿。")
        assert controller.canSave
        results = control(root, "resultsScrollView").property("contentItem")
        results.setProperty(
            "contentY", max(0, results.property("contentHeight") - results.height())
        )
        qapp.processEvents()
        click(root, "addMaterial")
        qtbot.waitUntil(lambda: control(root, "correctionDialog").property("opened"))
        control(root, "correctionValue").setProperty("text", "学生证或身份证")
        control(root, "correctionRequirement").setProperty("text", "办理住宿时携带两证任一")
        control(root, "correctionRelation").setProperty("currentIndex", 2)
        control(root, "correctionDialog").accept()
        assert not controller.canSave
        qapp.processEvents()
        results.setProperty(
            "contentY", max(0, results.property("contentHeight") - results.height())
        )
        qapp.processEvents()
        apply_evidence("materials.0", "办理住宿时须携带学生证或身份证任一。")
        assert service.draft.item.materials[0].relation == "any"
        assert service.draft.item.materials[0].requirement == "办理住宿时携带两证任一"
        click(root, "correction_materials.0")
        qtbot.waitUntil(lambda: control(root, "correctionDialog").property("opened"))
        click(root, "removeField")
        assert service.draft.item.materials == ()
        click(root, "confirmButton")
        qtbot.waitUntil(lambda: len(service.list_todos()) == 2)
        qtbot.waitUntil(lambda: not controller.busy)
        assert {todo.item.action.value for todo in service.list_todos()} == {"申请补助", "办理住宿"}
    finally:
        controller.wait_for_workers()
        root.setProperty("allowClose", True)
        root.close()
        engine.deleteLater()
        qapp.processEvents()


def test_unassigned_later_deadline_conflict_falls_back_to_full_notice(tmp_path):
    source = (
        "请于2027年4月9日前申请补助。请于2027年4月12日前办理住宿。"
        "另注明补助申请截止为2027年4月10日。"
    )
    content = json.loads(json.dumps(CONTENT))
    for item in content["items"]:
        item["materials"] = []
    service = service_for(tmp_path, content)
    service.set_source(source)
    service.extract(api_key="offline")
    assert all(entry.item.deadline.status == "uncertain" for entry in service.batch.entries)
    assert all(entry.deadline_scope is None for entry in service.batch.entries)
    saved = service.confirm_selected(service.batch.id)
    assert all(todo.item.deadline.date is None for todo in saved)
    assert all(NotificationService(service.database).get_todo(todo.id).item.deadline.date is None
               for todo in saved)


def test_qml_unsaved_unselected_item_is_never_labelled_as_saved(qapp, qtbot, tmp_path):
    from test_qml import control

    from campus_notification_assistant.app import create_engine

    service = service_for(tmp_path)
    service.extract(api_key="offline")
    engine, controller = create_engine(service)
    root = engine.rootObjects()[0]
    try:
        controller.focusDraft(1)
        controller.selectDraft(False)
        controller.confirm()
        qtbot.waitUntil(lambda: not controller.busy)
        assert len(service.list_todos()) == 1
        assert "已保存" in controller.resultHeading
        assert control(root, "actionValue").property("text") == "申请补助"
        controller.focusDraft(1)
        assert "未保存" in controller.resultHeading
        assert "已保存" not in controller.resultHeading
        assert control(root, "actionValue").property("text") == "办理住宿"
    finally:
        controller.wait_for_workers()
        root.setProperty("allowClose", True)
        root.close()
        engine.deleteLater()
        qapp.processEvents()
