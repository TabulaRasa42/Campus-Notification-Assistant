"""Cross-issue acceptance through the public application service."""

import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from campus_notification_assistant.errors import ServiceError
from campus_notification_assistant.service import NotificationService

SOURCE = "请于2026年11月15日前完成报名，提交报名表。"
ITEM = {
    "action": {"value": "完成报名", "evidence": "完成报名"},
    "deadline": {"date": "2026-11-15", "evidence": "2026年11月15日前"},
    "materials": [{"value": "报名表", "evidence": "报名表"}],
}


def response(item=ITEM):
    return httpx.Response(
        200,
        json={
            "choices": [
                {"finish_reason": "stop", "message": {"content": json.dumps({"items": [item]})}}
            ]
        },
    )


def test_legacy_duplicate_retry_review_and_atomic_confirmation(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    # A literal Issue #2 row: setup fixture, all observations use public operations.
    legacy = {"id": "legacy-id", "source": SOURCE, "item": ITEM}
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE todos (id TEXT PRIMARY KEY, data TEXT NOT NULL)")
        connection.execute("INSERT INTO todos VALUES (?, ?)", ("legacy-id", json.dumps(legacy)))
    attempts = []

    def transient_then_invalid_citation(request):
        attempts.append(request)
        if len(attempts) == 1:
            return httpx.Response(503)
        return response({**ITEM, "materials": [{"value": "报名表", "evidence": "不存在的引用"}]})

    service = NotificationService(
        path, transport=httpx.MockTransport(transient_then_invalid_citation)
    )
    service.set_source(SOURCE)
    original = service.get_todo("legacy-id")
    assert [todo.id for todo in service.find_duplicates()] == ["legacy-id"]
    assert service.find_duplicates(SOURCE + "\n") == []
    assert attempts == []
    # Explicit re-extraction retries the transient error, but invalid citation is reviewable.
    draft = service.extract(api_key="synthetic-test-key")
    assert len(attempts) == 2
    with pytest.raises(ServiceError):
        service.confirm(draft.id)
    service.correct_field(draft.id, "materials.0", "报名表", user_supplied=True)
    with sqlite3.connect(path) as blocker:
        blocker.execute("BEGIN EXCLUSIVE")
        with pytest.raises(ServiceError, match="保存失败"):
            service.confirm(draft.id)
    assert service.draft is not None
    assert service.get_todo("legacy-id") == original
    with ThreadPoolExecutor(max_workers=2) as pool:
        saved = list(pool.map(lambda _: service.confirm(draft.id), range(2)))
    assert saved[0].id == saved[1].id
    assert saved[0].id != original.id
    restarted = NotificationService(path)
    assert len(restarted.find_duplicates(SOURCE)) == 2
    assert restarted.get_todo("legacy-id") == original
    assert restarted.get_todo(saved[0].id).item.materials[0].origin == "user"
    assert len(attempts) == 2


@pytest.mark.parametrize("empty_old_result", [False, True])
def test_older_same_source_request_cannot_replace_new_confirmed_draft(tmp_path, empty_old_result):
    first_started = threading.Event()
    release_first = threading.Event()
    attempts = []

    def ordered(request):
        attempts.append(request)
        if len(attempts) == 1:
            first_started.set()
            assert release_first.wait(10)
            if empty_old_result:
                return httpx.Response(
                    200,
                    json={
                        "choices": [
                            {"finish_reason": "stop", "message": {"content": '{"items":[]}'}}
                        ]
                    },
                )
            return response({**ITEM, "action": {"value": "旧结果", "evidence": "完成报名"}})
        return response()

    service = NotificationService(
        tmp_path / "todos.sqlite3", transport=httpx.MockTransport(ordered)
    )
    service.set_source(SOURCE)
    with ThreadPoolExecutor(max_workers=1) as pool:
        older = pool.submit(service.extract, api_key="synthetic-test-key")
        try:
            assert first_started.wait(5)
            current = service.extract(api_key="synthetic-test-key")
            saved = service.confirm(current.id)
        finally:
            release_first.set()
        try:
            older.result(timeout=5)
        except ServiceError:
            pass  # A stale request may explicitly report cancellation to its caller.
    assert service.draft.id == current.id
    assert service.get_todo(saved.id).item.action.value == "完成报名"
    assert len(service.find_duplicates(SOURCE)) == 1
