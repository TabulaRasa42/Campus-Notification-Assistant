"""Edit content and select its evidence independently through public operations."""

import pytest
from test_evidence_time import service_for

from campus_notification_assistant.service import NotificationService, ServiceError


def test_edit_without_selecting_evidence_preserves_existing_citation(tmp_path):
    source = "请报名参加校运会。"
    service = service_for(
        tmp_path,
        source,
        {
            "action": {"value": "报名", "evidence": source},
            "materials": [],
        },
    )
    draft = service.extract(api_key="offline-test")
    corrected = service.correct_field(draft.id, "action", "校运会报名")
    assert corrected.item.action.value == "校运会报名"
    assert corrected.item.action.evidence == source
    assert corrected.item.action.review == "matched"
    saved = service.confirm(draft.id)
    assert (
        NotificationService(service.database).get_todo(saved.id).item.action
        == corrected.item.action
    )


def test_deadline_edit_is_kept_without_inheriting_an_unrelated_date(tmp_path):
    source = "请于2028年4月11日前办理阅览证。"
    service = service_for(
        tmp_path,
        source,
        {
            "action": {"value": "办理阅览证", "evidence": source},
            "deadline": {"date": "2028-04-11", "evidence": "2028年4月11日前"},
        },
    )
    draft = service.extract(api_key="offline-test")
    corrected = service.correct_field(draft.id, "deadline", "4月12日前")
    assert corrected.item.deadline.raw == "4月12日前"
    assert corrected.item.deadline.date is None
    assert corrected.item.deadline.review == "invalid"
    with pytest.raises(ServiceError):
        service.confirm(draft.id)


def test_other_field_operations_keep_an_unresolved_deadline_edit(tmp_path):
    source = "请于2028年4月11日前办理阅览证。办理时携带学生证。"
    service = service_for(
        tmp_path,
        source,
        {
            "action": {"value": "办理阅览证", "evidence": source},
            "deadline": {"date": "2028-04-11", "evidence": "2028年4月11日前"},
            "materials": [{"value": "学生证", "evidence": "办理时携带学生证。"}],
        },
    )
    draft = service.extract(api_key="offline-test")
    edited = service.correct_field(draft.id, "deadline", "4月12日前").item.deadline
    updated = service.correct_field(draft.id, "action", "办理临时阅览证")
    assert updated.item.deadline == edited
    quote = "办理时携带学生证。"
    start = source.index(quote)
    selected = service.select_evidence(draft.id, "materials.0", start, start + len(quote))
    assert selected.item.deadline == edited
    with pytest.raises(ServiceError):
        service.confirm(draft.id)
    assert NotificationService(service.database).list_todos() == []
    service.correct_field(draft.id, "deadline", "4月12日前", user_supplied=True)
    saved = service.confirm(draft.id)
    restored = NotificationService(service.database).get_todo(saved.id)
    assert restored.item.deadline.raw == "4月12日前"
    assert restored.item.deadline.origin == "user"


def test_resubmitting_an_unresolved_deadline_edit_keeps_review_required(tmp_path):
    source = "请于2028年4月11日前办理阅览证。"
    service = service_for(
        tmp_path,
        source,
        {
            "action": {"value": "办理阅览证", "evidence": source},
            "deadline": {"date": "2028-04-11", "evidence": "2028年4月11日前"},
        },
    )
    draft = service.extract(api_key="offline-test")
    edited = service.correct_field(draft.id, "deadline", "4月12日前").item.deadline
    repeated = service.correct_field(draft.id, "deadline", "4月12日前")
    assert repeated.item.deadline == edited
    with pytest.raises(ServiceError):
        service.confirm(draft.id)
    quote = "2028年4月11日前"
    start = source.index(quote)
    selected = service.select_evidence(draft.id, "deadline", start, start + len(quote))
    assert selected.item.deadline.review == "matched"
    assert selected.item.deadline.raw == quote
    assert selected.item.deadline.date is None


def test_qml_edit_does_not_require_a_selection_in_the_notice(qapp, qtbot, tmp_path):
    from test_qml import click, control

    from campus_notification_assistant.app import create_engine

    source = "请报名参加校运会。"
    service = service_for(
        tmp_path,
        source,
        {
            "action": {"value": "报名", "evidence": source},
        },
    )
    service.extract(api_key="offline-test")
    engine, controller = create_engine(service)
    root = engine.rootObjects()[0]
    try:
        control(root, "sourceInput").deselect()
        click(root, "actionCorrection")
        qtbot.waitUntil(lambda: control(root, "correctionDialog").property("opened"))
        control(root, "correctionValue").setProperty("text", "校运会报名")
        control(root, "correctionDialog").accept()
        qapp.processEvents()
        assert service.draft.item.action.value == "校运会报名"
        assert service.draft.item.action.evidence == source
        assert controller.canSave
    finally:
        root.setProperty("allowClose", True)
        root.close()
        engine.deleteLater()
        qapp.processEvents()


def test_select_evidence_alone_keeps_date_and_material_content_after_restart(tmp_path):
    source = "😀请于2028年4月11日前办理阅览证。办理时携带学生证原件核验。"
    service = service_for(
        tmp_path,
        source,
        {
            "action": {"value": "办理阅览证", "evidence": "办理阅览证"},
            "deadline": {"date": "2028-04-11", "evidence": "2028年4月11日前"},
            "materials": [
                {
                    "value": "学生证",
                    "requirement": "办理时携带学生证原件核验",
                    "relation": "single",
                    "evidence": "学生证",
                }
            ],
        },
    )
    draft = service.extract(api_key="offline-test")
    evidence = "办理时携带学生证原件核验。"
    updated = service.select_evidence(draft.id, "materials.0", source.index(evidence), len(source))
    assert updated.item.materials[0].requirement == "办理时携带学生证原件核验"
    assert updated.item.materials[0].evidence == evidence
    updated = service.select_evidence(draft.id, "deadline", 1, source.index("。") + 1)
    assert updated.item.deadline.date.isoformat() == "2028-04-11"
    assert updated.item.deadline.status == "certain"
    saved = service.confirm(draft.id)
    assert NotificationService(service.database).get_todo(saved.id).item == updated.item


def test_qml_evidence_window_selects_repeated_quote_without_editing_field(qapp, qtbot, tmp_path):
    from test_qml import click, control

    from campus_notification_assistant.app import create_engine

    source = "😀请报名。请报名。"
    service = service_for(
        tmp_path,
        source,
        {
            "action": {"value": "报名", "evidence": "不存在"},
        },
    )
    service.extract(api_key="offline-test")
    engine, controller = create_engine(service)
    root = engine.rootObjects()[0]
    try:
        click(root, "chooseEvidence_action")
        qtbot.waitUntil(lambda: control(root, "evidenceDialog").property("opened"))
        selector = control(root, "evidenceInput")
        assert selector.property("text") == source
        assert selector.property("readOnly")
        assert not control(root, "evidenceApply").property("enabled")
        selector.select(6, 10)
        assert selector.property("selectedText") == "请报名。"
        click(root, "evidenceApply")
        qtbot.waitUntil(lambda: not control(root, "evidenceDialog").property("opened"))
        assert service.draft.item.action.value == "报名"
        assert service.draft.item.action.evidence_start == 5
        assert service.draft.item.action.evidence_end == 9
        assert controller.canSave
        saved = service.confirm(service.draft.id)
        assert (
            NotificationService(service.database).get_todo(saved.id).item.action.evidence_start == 5
        )
    finally:
        root.setProperty("allowClose", True)
        root.close()
        engine.deleteLater()
        qapp.processEvents()


def test_qml_results_hide_redundant_provenance_and_deadline_explanation(qapp, tmp_path):
    from campus_notification_assistant.app import create_engine

    source = "请于2028年4月11日前办理阅览证。"
    service = service_for(
        tmp_path,
        source,
        {
            "action": {"value": "办理阅览证", "evidence": source},
            "deadline": {"date": "2028-04-11", "evidence": "2028年4月11日前"},
        },
    )
    service.extract(api_key="offline-test")
    engine, _controller = create_engine(service)
    root = engine.rootObjects()[0]
    try:
        pending = [root.contentItem()]
        texts = []
        while pending:
            item = pending.pop()
            if item.isVisible() and item.property("text") is not None:
                texts.append(item.property("text"))
            pending.extend(item.childItems())
        assert "截止时间" in texts
        assert not any("原文提取" in text or "原文依据：" in text for text in texts)
        assert not any("不推算，无默认时刻" in text for text in texts)
    finally:
        root.setProperty("allowClose", True)
        root.close()
        engine.deleteLater()
        qapp.processEvents()


def test_material_edit_can_precede_selection_and_keeps_edited_metadata(tmp_path):
    source = "申请时提交申请表。另行提交成绩单。"
    service = service_for(
        tmp_path,
        source,
        {
            "action": {"value": "申请", "evidence": source},
            "materials": [{"value": "申请表", "evidence": "申请表"}],
        },
    )
    draft = service.extract(api_key="offline-test")
    corrected = service.correct_field(
        draft.id,
        "materials.0",
        "成绩单",
        requirement="申请时提交成绩单",
        relation="single",
    )
    assert corrected.item.materials[0].value == "成绩单"
    assert corrected.item.materials[0].review == "invalid"
    with pytest.raises(ServiceError):
        service.confirm(draft.id)
    corrected = service.select_evidence(draft.id, "materials.0", 0, len(source))
    assert corrected.item.materials[0].requirement == "申请时提交成绩单"
    assert corrected.item.materials[0].relation == "single"
    saved = service.confirm(draft.id)
    assert NotificationService(service.database).get_todo(saved.id).item == corrected.item


def test_edit_with_missing_evidence_requires_citation_or_explicit_user_content_to_save(tmp_path):
    service = service_for(
        tmp_path,
        "请报名。",
        {
            "action": {"value": "报名", "evidence": "不存在"},
        },
    )
    draft = service.extract(api_key="offline-test")
    corrected = service.correct_field(draft.id, "action", "校运会报名")
    assert corrected.item.action.value == "校运会报名"
    assert corrected.item.action.origin == "extracted"
    with pytest.raises(ServiceError):
        service.confirm(draft.id)
    service.correct_field(draft.id, "action", "校运会报名", user_supplied=True)
    saved = service.confirm(draft.id)
    assert NotificationService(service.database).get_todo(saved.id).item.action.origin == "user"


def test_unmarking_user_content_requires_a_real_citation_without_losing_the_edit(tmp_path):
    service = service_for(
        tmp_path,
        "请报名。",
        {
            "action": {"value": "报名", "evidence": "请报名。"},
        },
    )
    draft = service.extract(api_key="offline-test")
    service.correct_field(draft.id, "action", "社团报名", user_supplied=True)
    corrected = service.correct_field(draft.id, "action", "社团报名")
    assert corrected.item.action.value == "社团报名"
    assert corrected.item.action.origin == "extracted"
    assert corrected.item.action.review == "invalid"
    with pytest.raises(ServiceError):
        service.confirm(draft.id)


def test_qml_cancelling_evidence_selection_keeps_original_citation(qapp, qtbot, tmp_path):
    from test_qml import click, control

    from campus_notification_assistant.app import create_engine

    source = "😀请报名。另行说明。"
    service = service_for(
        tmp_path,
        source,
        {
            "action": {"value": "报名", "evidence": "请报名。"},
        },
    )
    draft = service.extract(api_key="offline-test")
    original = draft.item
    engine, _controller = create_engine(service)
    root = engine.rootObjects()[0]
    try:
        click(root, "chooseEvidence_action")
        qtbot.waitUntil(lambda: control(root, "evidenceDialog").property("opened"))
        selector = control(root, "evidenceInput")
        qtbot.waitUntil(lambda: selector.property("selectedText") == "请报名。")
        assert selector.property("selectionStart") == 2
        selector.select(6, 11)
        control(root, "evidenceDialog").reject()
        assert service.draft.item == original
    finally:
        root.setProperty("allowClose", True)
        root.close()
        engine.deleteLater()
        qapp.processEvents()
