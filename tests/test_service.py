import json
import sqlite3
from uuid import uuid4

import httpx
import pytest

from campus_notification_assistant.service import NotificationService, ServiceError

SOURCE = "实践项目报名通知\n请于2026年11月15日前完成实践项目报名，提交报名表和学生证明。"
RESULT = {
    "items": [
        {
            "action": {"value": "完成实践项目报名", "evidence": "完成实践项目报名"},
            "deadline": {"date": "2026-11-15", "evidence": "2026年11月15日前"},
            "materials": [
                {"value": "报名表", "evidence": "报名表"},
                {"value": "学生证明", "evidence": "学生证明"},
            ],
        }
    ]
}


def ai_response(request):
    return httpx.Response(
        200,
        json={"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(RESULT)}}]},
    )


def test_confirmed_todo_survives_restart_with_source_and_evidence(tmp_path):
    path = tmp_path / "todos.sqlite3"
    service = NotificationService(path, transport=httpx.MockTransport(ai_response))
    service.set_source(SOURCE)
    draft = service.extract(api_key="test-key")
    assert service.list_todos() == []
    assert NotificationService(path).list_todos() == []
    todo = service.confirm(draft.id)
    assert service.confirm(draft.id).id == todo.id
    restarted = NotificationService(path)
    restored = restarted.get_todo(todo.id)
    assert restored.source == SOURCE
    assert restored.item.action.value == "完成实践项目报名"
    assert restored.item.deadline.date.isoformat() == "2026-11-15"
    assert [m.value for m in restored.item.materials] == ["报名表", "学生证明"]
    assert restored.item.action.evidence == "完成实践项目报名"
    assert (
        restored.item.materials[1].evidence
        == "请于2026年11月15日前完成实践项目报名，提交报名表和学生证明。"
    )
    assert len(restarted.list_todos()) == 1


@pytest.mark.parametrize(
    "items",
    [
        [{**RESULT["items"][0], "action": {"value": "", "evidence": "报名"}}],
    ],
)
def test_invalid_extraction_cannot_be_confirmed(tmp_path, items):
    def invalid(request):
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"finish_reason": "stop", "message": {"content": json.dumps({"items": items})}}
                ]
            },
        )

    service = NotificationService(
        tmp_path / "todos.sqlite3", transport=httpx.MockTransport(invalid)
    )
    service.set_source(SOURCE)
    with pytest.raises(ServiceError):
        service.extract(api_key="test-key")
    assert service.source == SOURCE
    assert service.draft is None
    assert service.list_todos() == []


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(401),
        httpx.Response(402),
        httpx.Response(429),
        httpx.Response(200, json={"choices": []}),
        httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "length", "message": {"content": json.dumps(RESULT)}}]
            },
        ),
        httpx.Response(
            200, json={"choices": [{"finish_reason": "stop", "message": {"content": "bad-json"}}]}
        ),
    ],
)
def test_failed_ai_preserves_input_without_todo(tmp_path, response):
    service = NotificationService(
        tmp_path / "todos.sqlite3", transport=httpx.MockTransport(lambda r: response)
    )
    service.set_source(SOURCE)
    with pytest.raises(ServiceError):
        service.extract(api_key="secret-never-show")
    assert service.source == SOURCE
    assert service.draft is None
    assert service.list_todos() == []


def test_transient_failure_retries_once_and_sends_only_current_notice(tmp_path):
    attempts = []

    def transient(request):
        payload = json.loads(request.content)
        assert str(request.url) == "https://api.deepseek.com/chat/completions"
        assert payload["model"] == "deepseek-flash"
        assert payload["thinking"] == {"type": "disabled"}
        assert payload["stream"] is False
        assert payload["response_format"] == {"type": "json_object"}
        assert payload["messages"][1] == {"role": "user", "content": SOURCE}
        attempts.append(request)
        if len(attempts) == 1:
            return httpx.Response(503)
        return ai_response(request)

    service = NotificationService(
        tmp_path / "todos.sqlite3", transport=httpx.MockTransport(transient)
    )
    service.set_source(SOURCE)
    assert service.extract(api_key="test-key").item.action.value == "完成实践项目报名"
    assert len(attempts) == 2


def test_key_is_available_after_restart_without_returning_secret(tmp_path):
    namespace = "campus-notification-assistant.test." + str(uuid4())
    path = tmp_path / "todos.sqlite3"
    service = NotificationService(path, credential_namespace=namespace)
    try:
        assert not service.key_configured()
        service.configure_key("isolated-test-credential")
        restarted = NotificationService(
            path, credential_namespace=namespace, transport=httpx.MockTransport(ai_response)
        )
        assert restarted.key_configured()
        restarted.set_source(SOURCE)
        assert restarted.extract().item.action.value == "完成实践项目报名"
    finally:
        service.clear_key()
    assert not service.key_configured()
    assert b"isolated-test-credential" not in path.read_bytes()


def test_save_failure_preserves_draft_and_allows_retry(tmp_path):
    path = tmp_path / "todos.sqlite3"
    service = NotificationService(path, transport=httpx.MockTransport(ai_response))
    service.set_source(SOURCE)
    draft = service.extract(api_key="test-key")
    with sqlite3.connect(path) as blocker:
        blocker.execute("BEGIN EXCLUSIVE")
        with pytest.raises(ServiceError, match="保存失败"):
            service.confirm(draft.id)
        assert service.draft == draft
    assert service.list_todos() == []
    service.confirm(draft.id)
    assert len(NotificationService(path).list_todos()) == 1


def test_draft_discard_requires_confirmation_and_source_change_invalidates_result(tmp_path):
    service = NotificationService(
        tmp_path / "todos.sqlite3", transport=httpx.MockTransport(ai_response)
    )
    service.set_source(SOURCE)
    draft = service.extract(api_key="test-key")
    assert service.has_unsaved_changes
    with pytest.raises(ServiceError):
        service.discard()
    assert service.draft == draft
    with pytest.raises(ServiceError):
        service.set_source("新的通知")
    service.set_source("新的通知", discard=True)
    assert service.draft is None
    with pytest.raises(ServiceError):
        service.confirm(draft.id)
    service.discard(confirmed=True)
    assert service.source == ""
    assert not service.has_unsaved_changes
    assert service.list_todos() == []


@pytest.mark.parametrize(
    "content",
    [
        "",
        "{}",
        "null",
        json.dumps(
            {
                "items": [
                    {
                        **RESULT["items"][0],
                        "deadline": {
                            "date": "2026-11-15T00:00:00Z",
                            "evidence": "2026年11月15日前",
                        },
                    }
                ]
            }
        ),
    ],
)
def test_incomplete_or_non_date_only_result_is_rejected(tmp_path, content):
    response = httpx.Response(
        200, json={"choices": [{"finish_reason": "stop", "message": {"content": content}}]}
    )
    service = NotificationService(
        tmp_path / "todos.sqlite3", transport=httpx.MockTransport(lambda r: response)
    )
    service.set_source(SOURCE)
    with pytest.raises(ServiceError):
        service.extract(api_key="test-key")
    assert service.source == SOURCE
    assert service.list_todos() == []


def test_no_key_gives_actionable_error_without_sending_notice(tmp_path):
    namespace = "campus-notification-assistant.no-key-test." + str(uuid4())

    def must_not_send(request):
        pytest.fail("No key: notice must not be sent")

    service = NotificationService(
        tmp_path / "todos.sqlite3",
        credential_namespace=namespace,
        transport=httpx.MockTransport(must_not_send),
    )
    service.set_source(SOURCE)
    with pytest.raises(ServiceError, match="配置"):
        service.extract()
    assert service.source == SOURCE


def test_deadline_evidence_can_include_activity_date_in_same_sentence(tmp_path):
    source = "活动于2026年12月1日举行，请于2026年11月15日前完成报名并提交报名表。"
    content = {
        "items": [
            {
                "action": {"value": "完成报名", "evidence": "完成报名"},
                "deadline": {"date": "2026-11-15", "evidence": source},
                "materials": [{"value": "报名表", "evidence": "报名表"}],
            }
        ]
    }
    response = httpx.Response(
        200,
        json={"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(content)}}]},
    )
    service = NotificationService(
        tmp_path / "todos.sqlite3", transport=httpx.MockTransport(lambda r: response)
    )
    service.set_source(source)
    draft = service.extract(api_key="test-key")
    todo = service.confirm(draft.id)
    assert todo.item.deadline.date.isoformat() == "2026-11-15"
    assert todo.item.deadline.evidence == source
