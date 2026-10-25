"""Current-item material attribution through the public application service."""

import json
from pathlib import Path

import httpx
import pytest
from test_evidence_time import service_for

from campus_notification_assistant.service import NotificationService, ServiceError


def test_other_phase_material_from_real_response_is_not_saved_as_registration_material(tmp_path):
    capture = json.loads(
        (Path(__file__).parents[1] / "docs/evaluation/issue-12-precheck-v4-raw.json").read_text(
            encoding="utf-8"
        )
    )
    run = next(run for run in capture["runs"] if run["case"] == "M01")
    response = {"choices": run["attempts"][0]["choices"]}
    service = NotificationService(
        tmp_path / "attribution.sqlite3",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=response)),
    )
    service.set_source(run["draft"]["source"])
    draft = service.extract(api_key="offline-test")
    assert draft.item.materials == ()
    assert draft.item.deadline.status == "uncertain"
    assert "10月18日晚24:00" in draft.item.deadline.raw
    saved = service.confirm(draft.id)
    restored = NotificationService(service.database).get_todo(saved.id)
    assert restored.item.materials == ()
    assert restored.source == run["draft"]["source"]


@pytest.mark.parametrize(
    "action,stage,name",
    [("报名", "报到时", "通行证"), ("申请", "领取时", "申请表")],
)
def test_explicit_other_stage_exclusion_does_not_depend_on_document_name(
    tmp_path, action, stage, name
):
    source = f"请{action}。{stage}请携带{name}原件。"
    service = service_for(
        tmp_path,
        source,
        {
            "action": {"value": action, "evidence": f"请{action}。"},
            "deadline": None,
            "materials": [{"value": name, "evidence": source}],
        },
    )
    draft = service.extract(api_key="offline-test")
    assert draft.item.materials == ()
    todo = service.confirm(draft.id)
    assert NotificationService(service.database).get_todo(todo.id).item.materials == ()


@pytest.mark.parametrize(
    "context",
    [
        "报名时请携带学生证/校园卡原件供核验。",
        "参加比赛的学生报名时请携带学生证/校园卡原件供核验。",
        "首次报名者提交学生证/校园卡复印件，其他人免交。",
        "请自行准备学生证/校园卡。",
        "报名时和报到时均须携带学生证/校园卡。",
        "请提交学生证/校园卡复印件，领取时用于登记。",
    ],
)
def test_current_or_unclear_stage_keeps_material_metadata(tmp_path, context):
    source = "请报名。" + context
    service = service_for(
        tmp_path,
        source,
        {
            "action": {"value": "报名", "evidence": "请报名。"},
            "materials": [
                {
                    "value": "学生证/校园卡",
                    "evidence": context,
                    "requirement": context,
                    "relation": "uncertain",
                }
            ],
        },
    )
    draft = service.extract(api_key="offline-test")
    assert len(draft.item.materials) == 1
    material = draft.item.materials[0]
    assert material.requirement == context
    assert material.relation == "uncertain"
    assert source[material.evidence_start : material.evidence_end] == context
    todo = service.confirm(draft.id)
    assert NotificationService(service.database).get_todo(todo.id).item.materials == (material,)


def test_invalid_other_stage_citation_still_blocks_confirmation(tmp_path):
    source = "请报名。参加比赛时携带通行证。"
    service = service_for(
        tmp_path,
        source,
        {
            "action": {"value": "报名", "evidence": "请报名。"},
            "materials": [{"value": "通行证", "evidence": "报到时携带通行证。"}],
        },
    )
    draft = service.extract(api_key="offline-test")
    assert draft.item.materials[0].review == "invalid"
    with pytest.raises(ServiceError, match="依据"):
        service.confirm(draft.id)
    assert NotificationService(service.database).list_todos() == []


def test_other_stage_quote_does_not_hide_a_material_also_required_for_registration(tmp_path):
    source = "请报名。报名时自行携带学生证。参加比赛请带学生证。"
    service = service_for(
        tmp_path,
        source,
        {
            "action": {"value": "报名", "evidence": "请报名。"},
            "materials": [{"value": "学生证", "evidence": "参加比赛请带学生证。"}],
        },
    )
    draft = service.extract(api_key="offline-test")
    assert len(draft.item.materials) == 1


def test_action_label_alone_is_not_enough_to_remove_material(tmp_path):
    source = "比赛通知。参加比赛请带学生证。"
    service = service_for(
        tmp_path,
        source,
        {
            "action": {"value": "报名", "evidence": "比赛通知。"},
            "materials": [{"value": "学生证", "evidence": "参加比赛请带学生证。"}],
        },
    )
    assert len(service.extract(api_key="offline-test").item.materials) == 1


def test_explicit_user_correction_is_not_filtered_again_on_save_or_restart(tmp_path):
    source = "请报名。参加比赛请带学生证。"
    service = service_for(
        tmp_path,
        source,
        {
            "action": {"value": "报名", "evidence": "请报名。"},
            "materials": [{"value": "待修正", "evidence": "不存在"}],
        },
    )
    draft = service.extract(api_key="offline-test")
    service.correct_field(
        draft.id,
        "materials.0",
        "学生证",
        requirement="用户核对后保留",
        evidence_start=4,
        evidence_end=len(source),
    )
    todo = service.confirm(draft.id)
    restarted = NotificationService(service.database)
    assert restarted.get_todo(todo.id).item.materials[0].requirement == "用户核对后保留"
    assert restarted.list_todos()[0].item.materials[0].value == "学生证"


def test_historical_material_is_not_silently_removed(tmp_path):
    import sqlite3

    service = NotificationService(tmp_path / "legacy.sqlite3")
    # Historical fixture setup only; all assertions use the public read operations.
    legacy = {
        "id": "historical",
        "source": "请报名。参加比赛请带学生证。",
        "item": {
            "action": {"value": "报名", "evidence": "请报名。"},
            "materials": [{"value": "学生证", "evidence": "参加比赛请带学生证。"}],
        },
    }
    with sqlite3.connect(service.database) as db:
        db.execute("INSERT INTO todos VALUES (?, ?)", ("historical", json.dumps(legacy)))
    restarted = NotificationService(service.database)
    assert restarted.get_todo("historical").item.materials[0].value == "学生证"
    assert restarted.list_todos()[0].item.materials[0].requirement == ""


def test_qml_filtered_draft_can_be_reviewed_saved_and_reopened(qapp, qtbot, tmp_path):
    from test_qml import click, control

    from campus_notification_assistant.app import create_engine

    source = "请报名。参加比赛请带通行证。"
    service = service_for(
        tmp_path,
        source,
        {
            "action": {"value": "报名", "evidence": "请报名。"},
            "materials": [{"value": "通行证", "evidence": "参加比赛请带通行证。"}],
        },
    )
    service.extract(api_key="offline-test")
    engine, controller = create_engine(service)
    root = engine.rootObjects()[0]
    try:
        assert controller.resultData["materials"] == []
        assert control(root, "materialsMissing").property("visible")
        assert control(root, "materialsMissing").property("text") == "原文未提及"
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
        assert controller2.resultData["materials"] == []
        assert control(root2, "materialsMissing").property("visible")
        assert control(root2, "sourceInput").property("text") == source
        assert not controller2.canSave
    finally:
        root2.setProperty("allowClose", True)
        root2.close()
        engine2.deleteLater()
        qapp.processEvents()
