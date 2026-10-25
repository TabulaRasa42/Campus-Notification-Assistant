---
status: accepted
---

# 使用 Python 与 Qt Quick 构建 Windows 桌面应用

校园通知助手第一版面向自己和少量同学，用户选择以 Python 开发和维护业务，并接受使用 QML 编写界面以获得更自由的动态交互。因此采用 Python + PySide6 + Qt Quick / QML；曾考虑主要用 Python 编写界面的 Qt Widgets，但最终选择将界面交互交给 QML。Python 负责提取、校验、持久化和提醒等业务，通过 Qt 的模型、属性、信号和槽与界面连接。

配套默认方案为 SQLite（通过 Python sqlite3）保存通知、待办、原文依据及提醒状态，HTTPX 请求 DeepSeek，Pydantic 校验返回结构，并另行匹配原文依据；API Key 通过 keyring 保存到 Windows 凭据存储。使用 uv 管理环境和依赖，pytest 与 pytest-qt 验证业务和 Qt 集成；QML 交互另需覆盖实际界面验收。具体依赖版本在实现准备时验证兼容性并锁定。

用户选择 Windows 安装向导作为首版交付形式。配套采用 PyInstaller 目录构建，再由 Inno Setup 制作安装程序，提供安装、卸载和开始菜单入口，默认按当前用户安装。应用数据与安装目录分开存放，更新应用时保留数据；打包验证必须覆盖 QML 资源、Qt 插件和未安装 Python 的 Windows 环境。

提醒依赖应用运行：关闭窗口后驻留托盘，明确退出后停止提醒，启动或恢复时检查持久化记录并集中补提示。系统设置可能抑制桌面通知，因此应用内保留提醒记录，不能将调用系统通知等同于用户已经看到提醒。

参考：[Qt 界面技术选择](https://doc.qt.io/qt-6/topics-ui.html)、[Python 与 QML](https://doc.qt.io/qtforpython-6/tutorials/qmlapp/qmlapplication.html)、[PyInstaller](https://pyinstaller.org/en/stable/operating-mode.html)、[Inno Setup](https://jrsoftware.org/isinfo.php)、[当前用户安装](https://jrsoftware.org/ishelp/topic_setup_privilegesrequired.htm)。
