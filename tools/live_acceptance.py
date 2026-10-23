"""使用安全配置的真实凭据运行一次无个人信息的真实 API 验收。

不读取 .env 文件；不接受命令行密钥；不打印密钥或原始异常。
"""

import os
from pathlib import Path
from tempfile import TemporaryDirectory

from campus_notification_assistant.credentials import Credentials
from campus_notification_assistant.errors import ServiceError
from campus_notification_assistant.service import NotificationService

SAMPLE = "实践项目报名通知\n请于2026年11月15日前完成实践项目报名，提交报名表和学生证明。"


def main() -> int:
    try:
        secret = os.environ.get("DEEPSEEK_API_KEY") or Credentials().read()
        if not secret:
            print("未验收：没有安全配置的 DeepSeek 密钥。请先通过应用配置 API Key。")
            return 2
        with TemporaryDirectory(prefix="campus-live-acceptance-") as directory:
            path = Path(directory) / "todos.sqlite3"
            service = NotificationService(path)
            service.set_source(SAMPLE)
            draft = service.extract(api_key=secret)
            assert service.list_todos() == []
            todo = service.confirm(draft.id)
            restored = NotificationService(path).get_todo(todo.id)
            assert restored == todo
            assert restored.source == SAMPLE
            assert restored.item.deadline.date.isoformat() == "2026-11-15"
            assert {m.value for m in restored.item.materials} == {"报名表", "学生证明"}
            print("真实 DeepSeek 调用、确认保存和重启回查通过。请人工核对以下合成样例结果：")
            print(restored.item.model_dump_json(indent=2))
            return 0
    except ServiceError as exc:
        print("验收未通过：" + str(exc))
        return 1
    except Exception:  # noqa: BLE001 - never print raw exceptions containing request data
        print("验收未通过：返回结果与合成样例不符或本机环境异常。未输出原始异常。")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
