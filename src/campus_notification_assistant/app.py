"""桌面应用入口。"""

import sys
from pathlib import Path

from PySide6.QtCore import QStandardPaths, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtWidgets import QApplication, QMessageBox

from .controller import Controller
from .errors import ServiceError
from .service import NotificationService


def create_engine(service: NotificationService) -> tuple[QQmlApplicationEngine, Controller]:
    controller = Controller(service)
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("backend", controller)
    engine.load(QUrl.fromLocalFile(str(Path(__file__).parent / "qml" / "Main.qml")))
    if not engine.rootObjects():
        raise ServiceError("QML 界面加载失败，请检查安装是否完整。")
    return engine, controller


def main() -> int:
    app = QApplication(sys.argv)
    QGuiApplication.setOrganizationName("CampusNotificationAssistant")
    QGuiApplication.setApplicationName("CampusNotificationAssistant")
    try:
        data_dir = Path(
            QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)
        )
        service = NotificationService(data_dir / "todos.sqlite3")
        engine, controller = create_engine(service)
    except ServiceError as exc:
        QMessageBox.critical(None, "校园通知助手无法启动", str(exc))
        return 1
    app.aboutToQuit.connect(controller.wait_for_workers)
    # Keep both objects alive for the Qt event loop.
    assert engine.rootObjects()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
