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
