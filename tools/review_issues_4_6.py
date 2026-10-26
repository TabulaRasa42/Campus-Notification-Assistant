"""Open the real QML app with synthetic HTTP and a persistent isolated demo database.

This is application acceptance, not a model quality evaluation. No real credentials,
network or normal application database are used. Run the same session again to inspect
persisted edits and completion state; choose another session for a fresh start.
"""

import argparse
import json
import re
from pathlib import Path

import httpx
from keyring.backend import KeyringBackend

from campus_notification_assistant.service import NotificationService


class DemoKeyring(KeyringBackend):
    priority = 1

    def get_password(self, service, username):
        return "synthetic-offline-demo"

    def set_password(self, service, username, password):
        pass

    def delete_password(self, service, username):
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", default="acceptance", help="Reuse to verify restart persistence")
    parser.add_argument("--screenshot", type=Path, help="Render the initial window and exit")
    parser.add_argument("--minimum", action="store_true", help="Use minimum supported 960x700 size")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,48}", args.session):
        parser.error("session 仅支持1至48位英文字母、数字、下划线或短横线")
    root_path = Path(__file__).resolve().parents[1]
    sample = json.loads(
        (root_path / "docs/evaluation/issue-4-offline-example.json").read_text(encoding="utf-8")
    )
    database = root_path / ".scratch" / "acceptance-issues-4-6" / args.session / "todos.sqlite3"
    service = NotificationService(
        database,
        credential_namespace="issue46-synthetic-offline",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={"choices": [{"finish_reason": "stop", "message": {
                    "content": json.dumps(sample["response"], ensure_ascii=False)
                }}]},
            )
        ),
    )
    service.credentials.backend = DemoKeyring()
    if not service.list_todos():
        service.set_source(sample["source"])
        service.extract(api_key="synthetic-offline-demo")

    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QFontDatabase
    from PySide6.QtWidgets import QApplication

    from campus_notification_assistant.app import create_engine

    app = QApplication([])
    # The isolated Qt runtime may not discover Windows fonts automatically.
    font = Path("C:/Windows/Fonts/msyh.ttc")
    if font.exists():
        QFontDatabase.addApplicationFont(str(font))
    engine, controller = create_engine(service)
    window = engine.rootObjects()[0]
    window.setTitle("校园通知助手 · #4/#6 合成离线验收 · 独立演示数据")
    if args.minimum:
        window.resize(960, 700)
    app.aboutToQuit.connect(controller.wait_for_workers)
    print("合成固定响应；新增模型调用0；演示数据保留于：" + str(database), flush=True)
    if args.screenshot:
        def capture():
            args.screenshot.parent.mkdir(parents=True, exist_ok=True)
            if not window.grabWindow().save(str(args.screenshot)):
                app.exit(1)
                return
            window.setProperty("allowClose", True)
            window.close()
            app.quit()

        QTimer.singleShot(1000, capture)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
