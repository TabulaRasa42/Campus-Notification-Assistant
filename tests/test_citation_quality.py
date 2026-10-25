"""Evidence content at the public service boundary, using recorded or HTTP-boundary data."""

import json
from pathlib import Path

import httpx
import pytest
from test_evidence_time import service_for

from campus_notification_assistant.service import NotificationService, ServiceError


def recorded_service(tmp_path, case, trial):
    capture = json.loads(
        (Path(__file__).parents[1] / "docs/evaluation/issue-12-candidate-v3-raw.json").read_text(
            encoding="utf-8"
        )
    )
    run = next(r for r in capture["runs"] if (r["case"], r["trial"]) == (case, trial))
    service = NotificationService(
        tmp_path / "recorded.sqlite3",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"choices": run["attempts"][-1]["choices"]})
        ),
    )
    service.set_source(run["draft"]["source"])
    return service


def test_recorded_background_quote_cannot_confirm_a_registration_action(tmp_path):
    service = recorded_service(tmp_path, "M01", 1)
    draft = service.extract(api_key="offline-test")
    assert draft.item.action.review == "invalid"
    with pytest.raises(ServiceError):
        service.confirm(draft.id)
    assert NotificationService(service.database).list_todos() == []


def test_recorded_material_clause_keeps_its_source_sentence_subject(tmp_path):
    service = recorded_service(tmp_path, "M02", 3)
    draft = service.extract(api_key="offline-test")
    for material in draft.item.materials:
        assert "请所有报名者" in material.evidence
        assert draft.source[material.evidence_start : material.evidence_end] == material.evidence
        assert "所有报名者报名时上传" in material.requirement
    assert draft.item.deadline.date.isoformat() == "2026-11-15"
    saved = service.confirm(draft.id)
    assert NotificationService(service.database).get_todo(saved.id).item == draft.item


def test_supported_paraphrase_need_not_repeat_the_action_name(tmp_path):
    source = "【讲座报名】有意参加者联系办公室。"
    service = service_for(
        tmp_path,
        source,
        {
            "action": {"value": "讲座报名", "evidence": "有意参加者联系办公室。"},
        },
    )
    draft = service.extract(api_key="offline-test")
    assert draft.item.action.review == "matched"
    saved = service.confirm(draft.id)
    assert NotificationService(service.database).get_todo(saved.id).item.action.value == "讲座报名"


def test_sentence_completion_does_not_append_the_next_sentence_after_spaces(tmp_path):
    source = "全体学生报名，报名时上传报名表。  其他事项另行通知。"
    service = service_for(
        tmp_path,
        source,
        {
            "action": {"value": "报名", "evidence": "全体学生报名"},
            "materials": [
                {
                    "value": "报名表",
                    "evidence": "报名时上传报名表。  ",
                    "requirement": "全体学生报名时上传报名表",
                }
            ],
        },
    )
    material = service.extract(api_key="offline-test").item.materials[0]
    assert "全体学生" in material.evidence
    assert "其他事项" not in material.evidence
    assert source[material.evidence_start : material.evidence_end] == material.evidence


def test_other_edit_does_not_clear_bad_action_but_selecting_real_request_recovers(tmp_path):
    service = recorded_service(tmp_path, "M01", 1)
    draft = service.extract(api_key="offline-test")
    edited = service.correct_field(draft.id, "deadline", draft.item.deadline.raw)
    assert edited.item.action.review == "invalid"
    quote = "请大家积极报名！"
    start = draft.source.index(quote)
    corrected = service.select_evidence(draft.id, "action", start, start + len(quote))
    assert corrected.item.action.review == "matched"
    assert corrected.item.materials == ()
    assert corrected.item.deadline.date is None
    saved = service.confirm(draft.id)
    assert NotificationService(service.database).get_todo(saved.id).item == corrected.item


def test_invalid_material_span_is_not_repaired_by_sentence_completion(tmp_path):
    source = "所有申请者申请时提交申请表。"
    service = service_for(
        tmp_path,
        source,
        {
            "action": {"value": "申请", "evidence": "所有申请者申请时提交申请表。"},
            "materials": [
                {"value": "申请表", "evidence": "申请表", "evidence_start": 0, "evidence_end": 3}
            ],
        },
    )
    draft = service.extract(api_key="offline-test")
    assert draft.item.materials[0].review == "invalid"
    assert draft.item.materials[0].evidence == "申请表"
    with pytest.raises(ServiceError):
        service.confirm(draft.id)


def test_sentence_completion_does_not_rewrite_explicit_user_selection(tmp_path):
    source = "😀所有申请者申请时提交申请表。"
    service = service_for(
        tmp_path,
        source,
        {
            "action": {"value": "申请", "evidence": "所有申请者申请时提交申请表。"},
            "materials": [
                {
                    "value": "申请表",
                    "evidence": "申请表",
                    "requirement": "所有申请者申请时提交申请表",
                    "relation": "single",
                }
            ],
        },
    )
    draft = service.extract(api_key="offline-test")
    assert "所有申请者" in draft.item.materials[0].evidence
    start = source.index("申请表")
    selected = service.select_evidence(draft.id, "materials.0", start, start + 3)
    assert selected.item.materials[0].evidence == "申请表"
    assert selected.item.materials[0].relation == "single"


def test_historical_background_quote_remains_unchanged_on_read(tmp_path):
    import sqlite3

    service = NotificationService(tmp_path / "historical.sqlite3")
    data = {
        "id": "historical",
        "source": "比赛通知。请报名。",
        "item": {
            "action": {"value": "报名", "evidence": "比赛通知。"},
        },
    }
    with sqlite3.connect(service.database) as db:
        db.execute("INSERT INTO todos VALUES (?, ?)", (data["id"], json.dumps(data)))
    restored = NotificationService(service.database).get_todo(data["id"])
    assert restored.item.action.evidence == "比赛通知。"
    assert restored.item.action.review == "matched"
