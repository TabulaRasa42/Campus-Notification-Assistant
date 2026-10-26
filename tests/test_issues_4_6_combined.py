"""The complete #4 to #6 workflow at the agreed public service seam."""

import json
from pathlib import Path

import httpx
import pytest

from campus_notification_assistant.errors import ServiceError
from campus_notification_assistant.service import NotificationService

SAMPLE = json.loads(
    (Path(__file__).resolve().parents[1] / "docs/evaluation/issue-4-offline-example.json").read_text(
        encoding="utf-8"
    )
)


def test_multi_item_correct_select_save_edit_complete_restore_delete_and_restart(tmp_path):
    service = NotificationService(
        tmp_path / "combined.sqlite3",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={"choices": [{"finish_reason": "stop", "message": {
                    "content": json.dumps(SAMPLE["response"], ensure_ascii=False)
                }}]},
            )
        ),
    )
    service.set_source(SAMPLE["source"])
    service.extract(api_key="offline-combined-fixture")
    batch = service.batch
    first, second, excluded = batch.entries
    assert first.item.deadline.date.isoformat() == "2029-05-10"
    assert second.item.deadline.date.isoformat() == "2029-05-12"
    assert excluded.item.deadline.status == "uncertain"

    service.focus_item(batch.id, first.id)
    edited = service.correct_field(batch.id, "action", "志愿者服务报名")
    materials = edited.item.materials
    quote = "请于2029年5月10日前完成志愿者报名。"
    start = SAMPLE["source"].index(quote)
    service.select_evidence(batch.id, "action", start, start + len(quote))
    assert service.draft.item.action.value == "志愿者服务报名"
    assert service.draft.item.materials == materials

    service.focus_item(batch.id, excluded.id)
    invalid = service.correct_field(batch.id, "deadline", "5月19日前")
    assert invalid.item.deadline.review == "invalid"
    service.set_item_selected(batch.id, excluded.id, False)
    manual = service.add_item(batch.id, "补交报名说明")
    service.focus_item(batch.id, manual.id)
    service.correct_field(batch.id, "action", "补交报名说明", user_supplied=True)
    service.add_material(batch.id, "说明表", user_supplied=True, requirement="自行补交说明表")
    saved = service.confirm_selected(batch.id)
    assert len(saved) == 3
    by_name = {todo.item.action.value: todo for todo in saved}
    first_todo = by_name["志愿者服务报名"]
    second_todo = by_name["申请借用投影仪"]
    manual_todo = by_name["补交报名说明"]
    assert all(todo.source == SAMPLE["source"] for todo in saved)
    assert second_todo.item.materials[0].requirement == (
        "首次借用者申请时提交盖章申请表，其他借用者免交申请表"
    )

    service.begin_todo_edit(first_todo.id)
    service.correct_todo_field(first_todo.id, "deadline", "5月20日前")
    service.correct_todo_field(first_todo.id, "action", "已核对志愿者报名", user_supplied=True)
    with pytest.raises(ServiceError, match="依据"):
        service.save_todo_edit(first_todo.id)
    assert service.todo_edit.item.deadline.review == "invalid"
    service.correct_todo_field(first_todo.id, "deadline", "5月20日前", user_supplied=True)
    revised_first = service.save_todo_edit(first_todo.id)
    assert revised_first.item.materials == first_todo.item.materials

    service.begin_todo_edit(second_todo.id)
    service.correct_todo_field(second_todo.id, "action", "借用投影仪申请已核对", user_supplied=True)
    revised_second = service.save_todo_edit(second_todo.id)
    assert revised_second.item.deadline == second_todo.item.deadline
    assert revised_second.item.materials == second_todo.item.materials
    assert service.get_todo(first_todo.id) == revised_first

    service.set_completed(first_todo.id, True)
    restarted = NotificationService(service.database)
    assert restarted.get_todo(first_todo.id).completed
    restarted.set_completed(first_todo.id, False)
    restarted.set_completed(second_todo.id, True)
    restarted.delete_todo(manual_todo.id)

    # A repeated batch receipt cannot overwrite B's edits or resurrect a deletion.
    service.confirm_selected(batch.id)
    after = NotificationService(service.database)
    assert len(after.list_todos()) == 2
    assert not after.get_todo(first_todo.id).completed
    assert after.get_todo(first_todo.id).item == revised_first.item
    assert after.get_todo(second_todo.id).completed
    assert after.get_todo(second_todo.id).item == revised_second.item
    assert all(todo.source == SAMPLE["source"] for todo in after.list_todos())
    with pytest.raises(ServiceError, match="不存在"):
        after.get_todo(manual_todo.id)
