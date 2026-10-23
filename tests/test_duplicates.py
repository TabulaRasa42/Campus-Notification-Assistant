import httpx
from test_service import SOURCE, ai_response

from campus_notification_assistant.service import NotificationService


def test_exact_duplicates_include_every_saved_todo_and_survive_restart(tmp_path):
    path = tmp_path / "duplicates.sqlite3"
    service = NotificationService(path, transport=httpx.MockTransport(ai_response))
    service.set_source(SOURCE)
    first = service.confirm(service.extract(api_key="test-key").id)
    second = service.confirm(service.extract(api_key="test-key").id)
    restarted = NotificationService(path)
    restarted.set_source(SOURCE)
    assert {todo.id for todo in restarted.find_duplicates()} == {first.id, second.id}
    assert restarted.find_duplicates(SOURCE + "\n") == []
    assert restarted.find_duplicates(SOURCE.replace("\n", " ")) == []
    assert restarted.get_todo(first.id) == first
    assert restarted.get_todo(second.id) == second
