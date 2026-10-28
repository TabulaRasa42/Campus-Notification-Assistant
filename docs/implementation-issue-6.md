# Issue #6：已保存待办维护

范围：[编辑、完成、恢复和删除已保存待办](https://github.com/TabulaRasa42/Campus-Notification-Assistant/issues/6)。共同基线 `1fff213c2d204a3c98c6cc354e427c06be28d446`；分支 `codex/issue-6-saved-todos`。

共同字段规则来自主代理提交 `5d4a52aa525a0602fda97bc027909101907aa826`，在本分支 cherry-pick 为 `b96c754`。保留 `Todo.deadline_scope` 的兼容默认值并新增 `completed=False`，旧 JSON 读取不迁移、不批量重写材料。

## 结果与边界

- 待办详情继续只有事项、截止时间、材料三部分。完整通知原文只读；每份材料保留名称、完整说明、关系及各自依据，依据窗口可查看或重新选择引用。
- `todo_edit: Todo | None` 是独立内存快照。修正字段、补充/移除材料、选择依据都不即时写库，也不借用提取 `draft`。明确保存使用原 ID 的 `UPDATE`；失败保留快照并允许重试，数据库中已删除的 ID 不会重新插入。
- `begin_todo_edit`、`correct_todo_field`、`select_todo_evidence`、`save_todo_edit`、`cancel_todo_edit`、`set_completed`、`delete_todo` 是公开操作；`has_todo_edits` 暴露是否有未保存修改。
- 完成、恢复、删除针对单个主键持久化。编辑取消恢复已保存内容；切换待办、新建、关闭及取消有未保存修改时要求确认。保存失败不会展示成功。
- 未解决的时间或引用不被其他字段修改清除。时间和材料可缺，标题不可空。用户补充清除原文引用并明确标记；无效引用阻止保存。
- 未调用真实 API、未访问真实待办或凭据，未实现 #8 提醒联动、#9、#10，未推送/创建 PR/合并/关闭 Issue。

## 失败定位与红绿证据

测试边界已由用户确认：公开应用服务、真实临时 SQLite、QML；仅替换外部 HTTP 与凭据。

| 层 | 最小复现与错误事实 | 修复判据及结果 |
| --- | --- | --- |
| 测试环境 | 首轮 pytest 指定 `.scratch/pytest-issue6` 时父目录不存在，`FileNotFoundError` | 创建本树 `.scratch` 后再运行；这是环境失败，不算功能红灯 |
| 编辑/保存 | `test_explicit_edit_save_keeps_source_other_todo_and_restarts` 调用 `begin_todo_edit`，`AttributeError`，1 failed | 引入独立快照和显式更新；同测试 1 passed，重启回查 ID、原文和其他待办不变 |
| 完成/删除 | `test_complete_restore_and_delete_are_persistent_and_target_one_id` 调用 `set_completed`，`AttributeError`，1 failed/1 passed | 完成、恢复及删除持久化；2 passed |
| QML | `test_qml_saved_todo_edit_requires_explicit_save` 找不到 `editTodoButton` | 增加待办维护控件并复用字段/依据窗口；完整点击编辑、保存、完成、恢复及删除通过 |
| 旧材料 | `test_legacy_read_and_unrelated_edits_leave_materials_unchanged`：编辑事项后旧材料被填入 offsets，与原值不等，1 failed/15 passed | 共享 helper 只替换目标字段；同步共同提交后相同回归通过，不静默重解释旧材料 |
| QML 取消 | 最小尺寸补材料后，测试直接调用不存在的 `QObject.discard()` 失败 | 改用可见 `discardTodoEditButton` 实际点击；确认取消后快照清空，已保存待办仍在 |
| QML 测试同步 | 全套运行时打开待办后立即按旧布局坐标点击“编辑”，偶发未进入编辑，1 failed/145 passed | 在可见状态及布局更新后再点击，保留真实鼠标事件，不绕过界面 |
| 离线渲染 | offscreen 默认未发现字体，截图中文全为方框 | QA 显式加载已安装 `msyh.ttc`/`msyhbd.ttc` 后重新截图查看；QML fixture 可选加载 `msyh.ttc`，生产样式不改 |

公开服务回归另覆盖真实 `BEGIN IMMEDIATE` 写锁：保存、完成和删除报明确失败，内容与数据库保持原状；解除写锁后重试成功。保存缺失 ID 保留快照且不 INSERT。独立截止时间编辑、材料新增及重选引用、用户补充、空标题、缺失材料/日期、取消与切换均有断言。

## 验收映射

| 操作 | 预期 | 工程侧反馈 |
| --- | --- | --- |
| 从清单打开待办，查看各字段依据 | 显示事项、日期/时间状态、材料说明/关系及自己的引用；左侧保留完整原文 | `test_qml_background_extract_review_save_and_open` 及服务重启回查通过 |
| 点击“编辑待办”，修正事项/时间/材料，补充材料或改选依据 | 修改暂存，用户补充清楚标记；不改原文、其他待办及未处理状态 | `test_explicit_edit_save_keeps_source_other_todo_and_restarts`、`test_saved_material_addition_and_selection_keep_independent_review_state` 通过 |
| 点击“保存修改”，故意让 SQLite 被写锁占用，再释放锁重试 | 失败保留修改并明确错误；重试成功后重启读到相同内容 | `test_failed_save_keeps_edit_for_retry_and_never_inserts_deleted_id` 与 QML `test_qml_saved_edit_failure_retains_changes_and_retry` 通过 |
| 输入空标题；移除日期、移除材料 | 空标题拒绝，缺日期/材料允许保存 | `test_blank_action_rejected_but_missing_date_materials_can_save` 通过 |
| 取消编辑、新建、切换或关闭 | 有未保存修改先确认；返回可继续编辑，丢弃恢复原待办 | 服务切换/取消、QML新建确认及最小窗口取消测试通过；关闭沿用同一 requestAction 分支 |
| 标记完成、恢复未完成、确认删除并重启 | 目标状态正确，其他 ID 不变，删除不复活 | `test_complete_restore_and_delete_are_persistent_and_target_one_id` 与 QML完整流程通过 |
| 读取旧 JSON 后修正其他字段 | 默认未完成；旧材料说明/关系/引用状态不被擅自补写 | `test_legacy_read_and_unrelated_edits_leave_materials_unchanged` 通过 |

## 验证记录

- 使用本树临时数据库；`PYTEST_ADDOPTS=--basetemp=.scratch/pytest-issue6`，`QT_QPA_PLATFORM=offscreen`；测试自动隔离凭据并禁止真实 HTTP。
- 公开服务新增 8 项；QML 共 12 项（含原有回归）。
- `uv run python tools/check.py`：Ruff 通过，mypy 10 个源文件通过，pytest **146 passed**（38.48 秒）。
- 视觉检查：本树 `.scratch/issue6-detail-960.png`、`.scratch/issue6-detail-1180.png`、`.scratch/issue6-edit-960.png`、`.scratch/issue6-edit-1180.png`。使用模拟凭据/离线 HTTP 与独立 `.scratch/preview-issue6.sqlite3`，加载 Windows 现有中文字体；在 960×700 与 1180×820 实际查看，按钮位于窗口内，材料区可滚动，全文清楚可读。截图和预览数据库不提交。
- 以上是离线应用行为与 offscreen 渲染验证，不是模型质量评测，也不代替用户桌面验收；未消耗真实 API 授权。

子代理提交时的共享日期限制已由主代理在集成提交 `027b44a` 修复：手动补充完整日期按日期精度保存，保留原话与用户来源；缺年份、相对表述和非法日期仍待确认。共享公开服务测试先2红后绿，并通过独立修正／时间及24轮历史录制回归共60项。
