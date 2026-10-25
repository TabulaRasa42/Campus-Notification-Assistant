import json

import httpx
import pytest

from campus_notification_assistant.service import NotificationService, ServiceError


def service_for(tmp_path, source, item):
    payload = {"items": [] if item is None else [item]}
    response = httpx.Response(
        200,
        json={"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(payload)}}]},
    )
    service = NotificationService(
        tmp_path / "review.sqlite3", transport=httpx.MockTransport(lambda r: response)
    )
    service.set_source(source)
    return service


def test_invalid_citation_stays_reviewable_until_explicit_user_correction(tmp_path):
    service = service_for(
        tmp_path,
        "请提交报名表。",
        {"action": {"value": "报名", "evidence": "不存在"}, "deadline": None, "materials": []},
    )
    draft = service.extract(api_key="test")
    assert draft.item.action.review == "invalid"
    with pytest.raises(ServiceError, match="依据"):
        service.confirm(draft.id)
    service.correct_field(draft.id, "action", "报名", user_supplied=True)
    todo = service.confirm(draft.id)
    assert todo.item.action.origin == "user"
    assert NotificationService(service.database).get_todo(todo.id).item.deadline.status == "missing"


@pytest.mark.parametrize(
    "source,deadline,expected",
    [
        ("请于11月15日前报名", {"date": "2026-11-15", "evidence": "11月15日前"}, "uncertain"),
        ("请于本周五前报名", {"date": "2026-10-23", "evidence": "本周五前"}, "uncertain"),
        (
            "请于2026年11月15日前报名，另称2026年11月16日截止",
            {"date": "2026-11-15", "evidence": "2026年11月15日前报名，另称2026年11月16日截止"},
            "uncertain",
        ),
        (
            "活动于2026年11月15日开始，请报名",
            {"date": "2026-11-15", "evidence": "活动于2026年11月15日开始"},
            "uncertain",
        ),
        (
            "活动于2026年11月15日开始，请提交报名表。",
            {"date": "2026-11-15", "evidence": "2026年11月15日开始"},
            "uncertain",
        ),
        (
            "2026年11月15日前往礼堂参加活动，请报名。",
            {"date": "2026-11-15", "evidence": "2026年11月15日"},
            "uncertain",
        ),
        (
            "请于本周五报名。参考往届通知：2026年11月15日截止。",
            {"date": "2026-11-15", "evidence": "本周五"},
            "uncertain",
        ),
        (
            "请于11月15日前报名。参考往届通知：2026年11月15日截止。",
            {"date": "2026-11-15", "evidence": "11月15日前"},
            "uncertain",
        ),
        (
            "请于2026年11月15日前报名",
            {"date": "2026-11-15", "evidence": "2026年11月15日前"},
            "certain",
        ),
    ],
)
def test_deadline_is_conservative_and_survives_restart(tmp_path, source, deadline, expected):
    service = service_for(
        tmp_path,
        source,
        {"action": {"value": "报名", "evidence": "报名"}, "deadline": deadline, "materials": []},
    )
    draft = service.extract(api_key="test")
    assert draft.item.deadline.status == expected
    if expected == "uncertain":
        assert draft.item.deadline.date is None
        assert draft.item.deadline.raw == deadline["evidence"]
    else:
        assert draft.item.deadline.date.isoformat() == "2026-11-15"
        assert draft.item.deadline.precision == "date"
    todo = service.confirm(draft.id)
    assert (
        NotificationService(service.database).get_todo(todo.id).item.deadline == todo.item.deadline
    )


def test_repeated_chinese_newline_citation_requires_exact_span_and_can_be_corrected(tmp_path):
    source = "😀报名表\n请提交报名表"
    service = service_for(
        tmp_path,
        source,
        {
            "action": {"value": "报名", "evidence": "请提交报名表"},
            "deadline": None,
            "materials": [{"value": "报名表", "evidence": "报名表"}],
        },
    )
    draft = service.extract(api_key="test")
    assert draft.item.materials[0].review == "invalid"
    with pytest.raises(ServiceError):
        service.confirm(draft.id)
    corrected = service.correct_field(
        draft.id, "materials.0", "报名表", evidence_start=8, evidence_end=11
    )
    assert corrected.item.materials[0].review == "matched"
    assert corrected.item.materials[0].evidence_start == 8
    service.confirm(draft.id)


def test_empty_result_is_not_a_blank_todo(tmp_path):
    service = service_for(tmp_path, "放假快乐", None)
    assert service.extract(api_key="test") is None
    assert service.draft is None
    assert service.list_todos() == []


def test_invalid_material_can_be_removed_without_inventing_no_materials(tmp_path):
    service = service_for(
        tmp_path,
        "请报名",
        {
            "action": {"value": "报名", "evidence": "报名"},
            "deadline": None,
            "materials": [{"value": "身份证", "evidence": "请报名"}],
        },
    )
    draft = service.extract(api_key="test")
    assert draft.item.materials[0].review == "invalid"
    service.correct_field(draft.id, "materials.0", "", remove=True)
    assert service.confirm(draft.id).item.materials == ()


def test_legacy_rows_keep_complete_deadline(tmp_path):
    import sqlite3

    service = NotificationService(tmp_path / "legacy.sqlite3")
    data = {
        "id": "legacy",
        "source": "请于2026年11月15日前报名",
        "item": {
            "action": {"value": "报名", "evidence": "报名"},
            "deadline": {"date": "2026-11-15", "evidence": "2026年11月15日前"},
            "materials": [],
        },
    }
    with sqlite3.connect(service.database) as db:
        db.execute("INSERT INTO todos VALUES (?, ?)", ("legacy", json.dumps(data)))
    assert service.get_todo("legacy").item.deadline.date.isoformat() == "2026-11-15"


def test_ai_cannot_claim_user_supplied_origin_to_bypass_review(tmp_path):
    service = service_for(
        tmp_path,
        "请报名",
        {
            "action": {
                "value": "报名",
                "evidence": "不存在",
                "origin": "user",
                "review": "matched",
            },
            "deadline": None,
            "materials": [],
        },
    )
    draft = service.extract(api_key="test")
    assert draft.item.action.origin == "extracted"
    assert draft.item.action.review == "invalid"
    with pytest.raises(ServiceError):
        service.confirm(draft.id)


def test_qml_field_click_selects_utf16_evidence_and_correction_updates_review(
    qapp, qtbot, tmp_path
):
    from test_qml import click, control

    from campus_notification_assistant.app import create_engine

    source = "😀通知\n请提交报名表"
    service = service_for(
        tmp_path,
        source,
        {
            "action": {"value": "报名", "evidence": "请提交报名表"},
            "deadline": None,
            "materials": [],
        },
    )
    service.extract(api_key="test")
    engine, controller = create_engine(service)
    root = engine.rootObjects()[0]
    try:
        click(root, "actionValue")
        qtbot.waitUntil(
            lambda: control(root, "sourceInput").property("selectedText") == "请提交报名表"
        )
        assert control(root, "sourceInput").property("selectionStart") == 5
        assert control(root, "sourceInput").property("selectionEnd") == 11
        controller.correctField("action", "用户说明", True, False, 0, 0)
        assert controller.resultData["action"]["origin"] == "user"
        assert controller.canSave
        assert "用户补充内容" in controller.resultData["action"]["label"]
    finally:
        controller.wait_for_workers()
        root.setProperty("allowClose", True)
        root.close()
        engine.deleteLater()
        qapp.processEvents()


def test_conflicting_deadline_elsewhere_in_source_is_not_hidden_by_short_quote(tmp_path):
    source = "请于2026年11月15日前报名。另写2026年11月16日截止。"
    service = service_for(
        tmp_path,
        source,
        {
            "action": {"value": "报名", "evidence": "报名"},
            "deadline": {"date": "2026-11-15", "evidence": "2026年11月15日前"},
            "materials": [],
        },
    )
    assert service.extract(api_key="test").item.deadline.status == "uncertain"


def test_qml_invalid_evidence_correction_dialog_unblocks_save(qapp, qtbot, tmp_path):
    from test_qml import click, control

    from campus_notification_assistant.app import create_engine

    service = service_for(
        tmp_path,
        "请报名",
        {"action": {"value": "报名", "evidence": "不存在"}, "deadline": None, "materials": []},
    )
    service.extract(api_key="test")
    engine, _controller = create_engine(service)
    root = engine.rootObjects()[0]
    try:
        assert not control(root, "confirmButton").property("enabled")
        click(root, "actionCorrection")
        qtbot.waitUntil(lambda: control(root, "correctionDialog").property("opened"))
        control(root, "correctionValue").setProperty("text", "用户提供事项")
        control(root, "supplement").setProperty("checked", True)
        control(root, "correctionDialog").accept()
        qtbot.waitUntil(lambda: bool(control(root, "confirmButton").property("enabled")))
        assert service.draft.item.action.origin == "user"
    finally:
        root.setProperty("allowClose", True)
        root.close()
        engine.deleteLater()
        qapp.processEvents()


@pytest.mark.parametrize("span", [(0, 2), (4, 7)])
def test_supplied_ai_span_must_match_quoted_text(tmp_path, span):
    service = service_for(
        tmp_path,
        "通知：请报名",
        {
            "action": {
                "value": "报名",
                "evidence": "报名",
                "evidence_start": span[0],
                "evidence_end": span[1],
            },
            "deadline": None,
            "materials": [],
        },
    )
    draft = service.extract(api_key="test")
    assert draft.item.action.review == "invalid"
    with pytest.raises(ServiceError):
        service.confirm(draft.id)


def test_saved_legacy_todo_can_locate_unique_evidence(qapp, qtbot, tmp_path):
    import sqlite3

    from test_qml import click, control

    from campus_notification_assistant.app import create_engine

    source = "😀通知\n请报名"
    service = NotificationService(tmp_path / "legacy-ui.sqlite3")
    data = {
        "id": "legacy",
        "source": source,
        "item": {
            "action": {"value": "报名", "evidence": "报名"},
            "deadline": {"date": "2026-11-15", "evidence": "2026年11月15日前"},
            "materials": [],
        },
    }
    with sqlite3.connect(service.database) as db:
        db.execute("INSERT INTO todos VALUES (?, ?)", ("legacy", json.dumps(data)))
    engine, controller = create_engine(service)
    root = engine.rootObjects()[0]
    try:
        controller.openTodo("legacy")
        qtbot.waitUntil(lambda: control(root, "actionValue").property("width") > 0)
        qapp.processEvents()
        click(root, "actionValue")
        qtbot.waitUntil(lambda: control(root, "sourceInput").property("selectedText") == "报名")
        assert control(root, "sourceInput").property("selectionStart") == 6
        assert service.get_todo("legacy").item.action.evidence_start is None
    finally:
        root.setProperty("allowClose", True)
        root.close()
        engine.deleteLater()
        qapp.processEvents()
