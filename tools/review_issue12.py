"""Inspect captured Issue #12 results offline in the real QML application.

Always uses recorded HTTP, an in-memory credential backend and temporary SQLite.
No production API calls or user database access. Closing the process removes its data.
--verify-correction checks the documented manual correction through public operations;
it does not change the original response or the semantic score.
"""

import argparse
import json
import tempfile
from pathlib import Path

import httpx
from keyring.backend import KeyringBackend
from keyring.errors import PasswordDeleteError

from campus_notification_assistant.service import NotificationService


class MemoryKeyring(KeyringBackend):
    priority = 1

    def __init__(self):
        self.values = {("issue12-offline-review", "api-key"): "offline-playback"}

    def get_password(self, service, username):
        return self.values.get((service, username))

    def set_password(self, service, username, password):
        self.values[service, username] = password

    def delete_password(self, service, username):
        if self.values.pop((service, username), None) is None:
            raise PasswordDeleteError("No review credential")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--case", choices=("M01", "M02", "M03", "M04", "M05", "M06", "M11", "M12"), required=True
    )
    parser.add_argument("--trial", type=int, choices=(1, 2, 3), default=1)
    parser.add_argument("--verify-correction", action="store_true")
    parser.add_argument(
        "--screenshot", type=Path, help="Capture rendered UI and exit; no interaction"
    )
    args = parser.parse_args()
    folder = Path(__file__).resolve().parents[1] / "docs" / "evaluation"
    raw = json.loads((folder / "issue-12-candidate-v3-raw.json").read_text(encoding="utf-8"))
    run = next(r for r in raw["runs"] if (r["case"], r["trial"]) == (args.case, args.trial))
    response = {"choices": run["attempts"][-1]["choices"]}
    with tempfile.TemporaryDirectory(prefix="campus-issue12-offline-review-") as temporary:
        service = NotificationService(
            Path(temporary) / "review.sqlite3",
            credential_namespace="issue12-offline-review",
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=response)),
        )
        service.credentials.backend = MemoryKeyring()
        source = run["draft"]["source"]
        service.set_source(source)
        draft = service.extract(api_key="offline-playback")
        if draft.item.model_dump(mode="json") != run["draft"]["item"]:
            print("使用当前版本重放：引用校验或上下文可能更新，历史记录和评分不变。")
        if args.verify_correction:
            corrected_fields = []
            if (args.case, args.trial) == ("M01", 1):
                quote = "请大家积极报名！"
                start = source.index(quote)
                service.correct_field(
                    draft.id,
                    "action",
                    draft.item.action.value,
                    evidence_start=start,
                    evidence_end=start + len(quote),
                )
                corrected_fields.append("action.evidence")
            if (args.case, args.trial) == ("M02", 3):
                start = source.index("请所有报名者")
                for index, material in enumerate(draft.item.materials):
                    service.correct_field(
                        draft.id,
                        f"materials.{index}",
                        material.value,
                        evidence_start=start,
                        evidence_end=len(source),
                    )
                    corrected_fields.append(f"materials.{index}.evidence")
            assert service.list_todos() == []
            saved = service.confirm(draft.id)
            restored = NotificationService(service.database).get_todo(saved.id)
            assert restored.item == service.draft.item and restored.source == source
            print(
                json.dumps(
                    {
                        "case": args.case,
                        "trial": args.trial,
                        "new_api_calls": 0,
                        "corrected_fields": corrected_fields,
                        "save_restart_verified": True,
                        "original_semantic_grade_unchanged": True,
                    },
                    ensure_ascii=False,
                )
            )
            return

        from PySide6.QtCore import QTimer
        from PySide6.QtQuick import QQuickItem, QQuickWindow
        from PySide6.QtWidgets import QApplication

        from campus_notification_assistant.app import create_engine

        app = QApplication([])
        engine, controller = create_engine(service)
        root = engine.rootObjects()[0]
        assert isinstance(root, QQuickWindow)
        root.setTitle(f"校园通知助手 · 离线验收 {args.case}/{args.trial} · 临时数据")
        app.aboutToQuit.connect(controller.wait_for_workers)
        if args.screenshot:

            def capture():
                try:
                    if args.case == "M12":
                        scroll = root.findChild(QQuickItem, "resultsScrollView")
                        content = scroll.property("contentItem")
                        content.setProperty(
                            "contentY", max(0, content.property("contentHeight") - content.height())
                        )
                        app.processEvents()
                    assert root.grabWindow().save(str(args.screenshot))
                finally:
                    root.setProperty("allowClose", True)
                    root.close()
                    app.quit()

            QTimer.singleShot(400, capture)
        app.exec()


if __name__ == "__main__":
    main()
