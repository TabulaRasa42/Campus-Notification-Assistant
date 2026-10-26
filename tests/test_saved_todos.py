import httpx
from test_service import SOURCE, ai_response

from campus_notification_assistant.service import NotificationService


def saved_pair(tmp_path):
    service = NotificationService(
        tmp_path / "saved.sqlite3", transport=httpx.MockTransport(ai_response)
    )
    service.set_source(SOURCE)
    first = service.confirm(service.extract(api_key="offline").id)
    second = service.confirm(service.extract(api_key="offline").id)
    service.discard(confirmed=True)
    return service, first, second


def test_explicit_edit_save_keeps_source_other_todo_and_restarts(tmp_path):
    service, first, second = saved_pair(tmp_path)
    service.begin_todo_edit(first.id)
    service.correct_todo_field(first.id, "action", "我的报名", user_supplied=True)
    assert service.get_todo(first.id) == first
    assert service.draft is None
    edited = service.save_todo_edit(first.id)
    restored = NotificationService(service.database)
    assert restored.get_todo(first.id) == edited
    assert edited.id == first.id
    assert edited.source == SOURCE
    assert edited.item.action.value == "我的报名"
    assert edited.item.action.origin == "user"
    assert edited.item.action.evidence == ""
    assert edited.item.materials == first.item.materials
    assert restored.get_todo(second.id) == second


def test_complete_restore_and_delete_are_persistent_and_target_one_id(tmp_path):
    service, first, second = saved_pair(tmp_path)
    assert not first.completed
    assert service.set_completed(first.id, True).completed
    restarted = NotificationService(service.database)
    assert restarted.get_todo(first.id).completed
    assert not restarted.set_completed(first.id, False).completed
    restarted.delete_todo(first.id)
    assert NotificationService(service.database).list_todos() == [second]


def test_failed_save_keeps_edit_for_retry_and_never_inserts_deleted_id(tmp_path):
    import sqlite3

    import pytest

    from campus_notification_assistant.errors import ServiceError

    service, first, _second = saved_pair(tmp_path)
    service.begin_todo_edit(first.id)
    service.correct_todo_field(first.id, "action", "保留修改", user_supplied=True)
    with sqlite3.connect(service.database) as locked:
        locked.execute("BEGIN IMMEDIATE")
        with pytest.raises(ServiceError, match="保存失败"):
            service.save_todo_edit(first.id)
        assert service.todo_edit.item.action.value == "保留修改"
        assert service.get_todo(first.id) == first
    assert service.save_todo_edit(first.id).item.action.value == "保留修改"
    service.begin_todo_edit(first.id)
    service.correct_todo_field(first.id, "action", "不能复活", user_supplied=True)
    NotificationService(service.database).delete_todo(first.id)
    with pytest.raises(ServiceError, match="不存在"):
        service.save_todo_edit(first.id)
    assert service.todo_edit.item.action.value == "不能复活"
    assert all(todo.id != first.id for todo in service.list_todos())


def test_legacy_read_and_unrelated_edits_leave_materials_unchanged(tmp_path):
    import json
    import sqlite3

    service, first, _second = saved_pair(tmp_path)
    legacy = first.model_dump(mode="json")
    legacy.pop("completed")
    legacy["item"]["materials"][0] = {"value": "报名表", "evidence": "报名表"}
    serialized = json.dumps(legacy, ensure_ascii=False)
    with sqlite3.connect(service.database) as connection:
        connection.execute("UPDATE todos SET data=? WHERE id=?", (serialized, first.id))
    original = service.get_todo(first.id)
    assert not original.completed
    assert original.item.materials[0].requirement == ""
    assert original.item.materials[0].relation is None
    assert NotificationService(service.database).get_todo(first.id) == original
    service.begin_todo_edit(first.id)
    service.correct_todo_field(first.id, "action", "用户事项", user_supplied=True)
    assert service.save_todo_edit(first.id).item.materials == original.item.materials


def test_blank_action_rejected_but_missing_date_materials_can_save(tmp_path):
    import pytest

    from campus_notification_assistant.errors import ServiceError

    service, first, _second = saved_pair(tmp_path)
    service.begin_todo_edit(first.id)
    with pytest.raises(ServiceError, match="不能为空"):
        service.correct_todo_field(first.id, "action", "  ", user_supplied=True)
    service.correct_todo_field(first.id, "deadline", "", remove=True)
    service.correct_todo_field(first.id, "materials.1", "", remove=True)
    service.correct_todo_field(first.id, "materials.0", "", remove=True)
    saved = service.save_todo_edit(first.id)
    assert saved.item.deadline.status == "missing"
    assert saved.item.materials == ()


def test_cancel_and_switch_protect_unsaved_edits_and_original_source(tmp_path):
    import pytest

    from campus_notification_assistant.errors import ServiceError

    service, first, second = saved_pair(tmp_path)
    service.begin_todo_edit(first.id)
    service.correct_todo_field(first.id, "action", "保留修改", user_supplied=True)
    with pytest.raises(ServiceError, match="尚未保存"):
        service.begin_todo_edit(second.id)
    assert service.todo_edit.id == first.id
    service.cancel_todo_edit(first.id)
    assert service.todo_edit is None
    assert service.get_todo(first.id) == first
    assert service.begin_todo_edit(second.id) == second


def test_saved_material_addition_and_selection_keep_independent_review_state(tmp_path):
    import pytest

    from campus_notification_assistant.errors import ServiceError

    service, first, _second = saved_pair(tmp_path)
    service.begin_todo_edit(first.id)
    service.correct_todo_field(first.id, "deadline", "下周提交")
    service.correct_todo_field(
        first.id, "materials.2", "报名表", requirement="困难生网上提交报名表", relation="any"
    )
    assert service.todo_edit.item.materials[2].review == "invalid"
    with pytest.raises(ServiceError, match="无效原文依据"):
        service.save_todo_edit(first.id)
    service.correct_todo_field(first.id, "action", "我的报名", user_supplied=True)
    assert service.todo_edit.item.deadline.raw == "下周提交"
    assert service.todo_edit.item.deadline.review == "invalid"
    start = SOURCE.index("提交报名表")
    service.select_todo_evidence(first.id, "materials.2", start, len(SOURCE) - 1)
    material = service.todo_edit.item.materials[2]
    assert material.value == "报名表"
    assert material.requirement == "困难生网上提交报名表"
    assert material.relation == "any"
    assert service.todo_edit.item.deadline.review == "invalid"
    service.correct_todo_field(first.id, "deadline", "下周提交", user_supplied=True)
    saved = service.save_todo_edit(first.id)
    assert saved.item.deadline.status == "uncertain"
    assert saved.item.deadline.origin == "user"
    assert saved.item.materials[2] == material
    assert NotificationService(service.database).get_todo(first.id) == saved


def test_failed_complete_and_delete_do_not_fake_success(tmp_path):
    import sqlite3

    import pytest

    from campus_notification_assistant.errors import ServiceError

    service, first, second = saved_pair(tmp_path)
    with sqlite3.connect(service.database) as locked:
        locked.execute("BEGIN IMMEDIATE")
        with pytest.raises(ServiceError, match="保存失败"):
            service.set_completed(first.id, True)
        with pytest.raises(ServiceError, match="删除失败"):
            service.delete_todo(first.id)
        assert service.get_todo(first.id) == first
        assert service.get_todo(second.id) == second
    assert service.set_completed(first.id, True).completed
    service.delete_todo(first.id)
    assert NotificationService(service.database).list_todos() == [second]
