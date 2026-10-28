"""Keep every accepted single-item recorded draft unchanged; no new model calls."""

import json
from pathlib import Path

import httpx
import pytest

from campus_notification_assistant.service import NotificationService

CAPTURE = Path(__file__).resolve().parents[1] / "docs/evaluation/issue-12-candidate-v4-raw.json"
RUNS = json.loads(CAPTURE.read_text(encoding="utf-8"))["runs"]


@pytest.mark.parametrize("run", RUNS, ids=lambda r: f"{r['case']}-{r['trial']}")
def test_accepted_single_item_recording_remains_unchanged_after_save_and_restart(tmp_path, run):
    service = NotificationService(
        tmp_path / "recording.sqlite3",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"choices": run["attempts"][-1]["choices"]})
        ),
    )
    service.set_source(run["draft"]["source"])
    draft = service.extract(api_key="offline-recorded-boundary")
    assert draft.item.model_dump(mode="json") == run["draft"]["item"]
    todo = service.confirm(draft.id)
    assert service.confirm(draft.id).id == todo.id
    restarted = NotificationService(service.database)
    assert len(restarted.list_todos()) == 1
    assert restarted.get_todo(todo.id).item == draft.item
    assert restarted.get_todo(todo.id).source == run["draft"]["source"]
