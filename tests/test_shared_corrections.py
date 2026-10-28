"""Shared correction behavior through the application's public draft operations."""

import pytest
from test_evidence_time import service_for

from campus_notification_assistant.service import NotificationService, ServiceError


def test_missing_material_can_be_added_reviewed_and_retained_across_other_edits(tmp_path):
    source = "请报名。首次报名者提交学生证或校园卡原件，往年报名者免交。"
    service = service_for(tmp_path, source, {"action": {"value": "报名", "evidence": "请报名。"}})
    draft = service.extract(api_key="offline")
    added = service.correct_field(
        draft.id,
        "materials.0",
        "学生证或校园卡",
        requirement="首次报名者提交学生证或校园卡原件，往年报名者免交",
        relation="any",
    )
    assert added.item.materials[0].review == "invalid"
    with pytest.raises(ServiceError, match="依据"):
        service.confirm(draft.id)
    quote = "首次报名者提交学生证或校园卡原件，往年报名者免交。"
    start = source.index(quote)
    reviewed = service.select_evidence(draft.id, "materials.0", start, start + len(quote))
    service.correct_field(draft.id, "action", "社团报名")
    saved = service.confirm(draft.id)
    restored = NotificationService(service.database).get_todo(saved.id)
    assert restored.item.materials == reviewed.item.materials
    assert restored.item.materials[0].relation == "any"
    assert restored.source == source


@pytest.mark.parametrize("value", ["2029-06-01", "2029年6月1日前"])
def test_explicit_personal_deadline_remains_a_date_after_other_edits_and_restart(tmp_path, value):
    service = service_for(tmp_path, "请报名。", {"action": {"value": "报名", "evidence": "请报名。"}})
    draft = service.extract(api_key="offline")
    corrected = service.correct_field(draft.id, "deadline", value, user_supplied=True)
    assert corrected.item.deadline.date.isoformat() == "2029-06-01"
    assert corrected.item.deadline.status == "certain"
    service.correct_field(draft.id, "action", "社团报名")
    saved = service.confirm(draft.id)
    restored = NotificationService(service.database).get_todo(saved.id)
    assert restored.item.deadline == corrected.item.deadline
    assert restored.item.deadline.origin == "user"
    assert restored.item.deadline.evidence == ""


def test_selecting_unrelated_deadline_evidence_cannot_erase_a_correction_in_either_editor(tmp_path):
    source = "请于2027年4月9日前报名。另注明2027年4月15日前截止。"
    service = service_for(tmp_path, source, {
        "action": {"value": "报名", "evidence": "报名"},
        "deadline": {"date": "2027-04-09", "evidence": "2027年4月9日前"},
    })
    draft = service.extract(api_key="offline")
    quote = "2027年4月9日前"
    start = source.index(quote)
    service.correct_field(draft.id, "deadline", "4月15日前")
    selected = service.select_evidence(draft.id, "deadline", start, start + len(quote))
    assert selected.item.deadline.raw == "4月15日前"
    assert selected.item.deadline.review == "invalid"
    with pytest.raises(ServiceError, match="依据"):
        service.confirm(draft.id)
    support = "2027年4月15日前"
    support_start = source.index(support)
    selected = service.select_evidence(draft.id, "deadline", support_start, support_start + len(support))
    assert selected.item.deadline.raw == "4月15日前"
    assert selected.item.deadline.review == "matched"
    assert selected.item.deadline.status == "uncertain"
    todo = service.confirm(draft.id)
    service.begin_todo_edit(todo.id)
    service.correct_todo_field(todo.id, "deadline", "4月16日前")
    selected = service.select_todo_evidence(todo.id, "deadline", start, start + len(quote))
    assert selected.item.deadline.raw == "4月16日前"
    assert selected.item.deadline.review == "invalid"
    with pytest.raises(ServiceError, match="依据"):
        service.save_todo_edit(todo.id)
