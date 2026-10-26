# Issue #4 多事项草稿实现记录

工作树：`E:/Program/Codex/.codex/worktrees/issue-4-multi-drafts/Campus-Notification-Assistant`。
分支：`codex/issue-4-multi-drafts`。起点：`1fff213c2d204a3c98c6cc354e427c06be28d446`。
公共修正 helper 精确同步主代理提交 `5d4a52aa525a0602fda97bc027909101907aa826`。
本记录覆盖离线应用行为和 QML；未调用真实模型，新增真实 API 授权和消耗均为 0。

## 公开接口

- `service.batch` 为 `DraftBatch(id, source, entries, current_id)`；`entries` 是不可变的
  `DraftEntry(id, item, selected=True, deadline_scope=None)` 元组。事项 ID 在导航、修正、增删和重试间稳定。
- `service.draft` 保持 `Draft(id=batch.id, source, item=当前事项)`，原有单事项读取、
  `correct_field`、`select_evidence` 和 `confirm(draft.id)` 调用兼容。
  多事项用 `confirm_selected(batch.id) -> list[Todo]`，不通过单事项 confirm 隐式保存其他事项。
- `focus_item(batch_id, item_id)`、`set_item_selected(batch_id, item_id, selected)`、
  `add_item(batch_id, title="") -> DraftEntry`、`remove_item(batch_id, item_id)` 操作内存批次。
- `add_material(batch_id, value, *, user_supplied=False, evidence_start=None,
  evidence_end=None, requirement="", relation=None) -> Draft` 操作当前事项，复用公共字段修正规则。
- 空提取结果保留空批次，`draft` 仍是 None；可手工补回漏项。空标题仅允许作为内存编辑行，
  不能确认保存；填写标题后才可修改日期、材料和依据。未选择的空标题或无效依据不会拦住其他有效选中项。

确认在同一真实 SQLite 事务内写入全部选中待办与 `confirmed_batches` 回执。
任一写入失败则整体回滚，内存草稿可修改和重试。成功后整批冻结；再次确认（包括重启后）
读取首次确认的回执并返回首次结果，不重新写入待办。因此后续修改或删除的待办不会被覆盖或复活。
回执是首次确认快照，重复确认返回值不表示后续待办的最新编辑状态。

## 失败记录与红绿证据

测试边界已由用户确认：公开应用服务、真实临时 SQLite、实际 QML 交互；仅替换 HTTP 和凭据边界。

| 层 | 最小复现与失败事实（Red） | 修复判据及 Green |
| --- | --- | --- |
| 应用处理 | 两个独立动作返回时，`test_multi_actions_keep_own_materials_and_save_as_one_batch` 抛出“仅支持一条办理事项” | 建立两个稳定 ID 事项，材料留在各自事项，批量保存并重启回查；首片 1 passed |
| 修正 | `test_add_remove_correct_and_select_only_valid_items` 缺少 `focus_item`，AttributeError | 导航到第二项、改标题、补充材料、移除原材料、增加空项并取消选择、删除第一项；仅保存第二项 |
| 应用时间处理 | `test_distinct_deadlines_stay_associated_through_corrections_and_restart` 中两个日期均被降为 None | 多事项从明确 action 原文位置计算各自连续上下文，传入公共 helper 的 `deadline_scope`；改标题、重选日期、保存及重启后保留 4 月 9 日和 4 月 12 日；相关 36 passed |
| 展示 | 两个尺寸的 `test_qml_multi_item_review_add_remove_material_and_save` 均找不到 `draftPosition` | QML 新增上一项/下一项、保存此项、新增/移除事项、新增材料；960×700 和 1180×820 的真实鼠标交互通过 |
| 修正 | 零结果后 `test_empty_extraction_can_be_repaired_by_adding_an_omitted_action` 无 batch，无法补漏 | 空批次不生成待办，但支持手工增加事项后确认；1 passed |
| 修正 | 空白新事项先改日期，`test_blank_new_item_can_be_filled_without_leaking_validation_errors` 泄漏 Pydantic ValidationError | 应用服务明确提示先填写事项内容，原草稿保持，填标题后保存；1 passed |

另以 SQLite `BEFORE INSERT` 触发器令第二项写入失败，通过公开 `list_todos` 验证重启后无部分结果；
移除故障、修改第二项后重试，保存 ID 与失败前一致。未替换或模拟数据库内部实现。
回执测试通过真实 SQLite 设置已保存条目的后续修改/删除状态，再通过重启服务公开确认和读取验证不覆盖、不复活。
此测试树尚未包含 #6 API；主集成树另需用 #6 公开编辑/删除操作验证同一组合流程。

QML 完整路径覆盖人工新增事项、填写内容、标为用户补充或选原文依据、增加材料、选择完整材料依据、
保留修改后的说明与任选关系、移除材料、选择保存及确认。新增非用户材料在未选依据时阻止保存。
字体使用测试进程加载的系统微软雅黑；已查看 `.scratch/issue4-960.png` 和 `.scratch/issue4-1180.png`。
截图为本地检查产物；最小窗口通过滚动访问材料，主保存操作始终可见。

## 验收映射

| 操作 | 预期 | 离线反馈 |
| --- | --- | --- |
| 提取多个独立动作，每项有不同日期和材料 | 各事项独立，多份材料或选择组不拆成事项，条件/阶段/方式保留在材料说明及引用中 | 服务测试通过；沿用 #12 的过滤与完整引用回归 |
| 新增/移除事项、改字段、新增/移除材料、选择依据或用户补充 | 仅修改当前事项，原修正和其他待核对状态保持 | 服务与 QML 测试通过，公共 helper 保留无关字段 |
| 选中空标题或无效依据；再取消选择 | 选中无效项阻止整批保存，取消后有效项可保存，未选项不入库 | 服务与 QML 测试通过 |
| 取消所有选择或移除全部事项后确认 | 不创建待办，可再新增事项 | 服务测试通过 |
| 第二项写入失败后修改并重试 | 无部分待办，原 ID 和草稿保持，重试整体成功 | 真实 SQLite 触发器验证通过 |
| 重复确认、后续修改/删除并重启再确认 | 不重复创建，不覆盖后续修改，不重新插回已删除项 | 持久回执验证通过；组合 #6 API 验证由主代理完成 |
| 不同动作分别有日期；同一事项重复给出冲突日期 | 不因其他事项日期而丢失正确日期，同事项冲突仍待确认 | 不同句/相邻分句、scope 内冲突及原单项跨句冲突回归通过 |
| 重复通知、过期请求、缺失/歧义、引用待核对、用户明确修正材料 | 原行为保持；修正后不自动重滤，未解决状态不被无关操作清除 | 原有全量离线回归覆盖 |

## 检查与限制

统一入口：在上述完整工作树中设置 `PYTEST_ADDOPTS=--basetemp=.scratch/pytest-issue4`，
运行 `uv run python tools/check.py`。最终结果：Ruff 通过、mypy 通过（10 个源文件），
**147 passed in 34.85s**；`git diff --check` 通过。

原有测试中“多个事项属于非法提取”的参数被新需求取代，已删除该参数；空标题和其他非法结构测试保留。
未修改生产提示词、未真实调用模型；这些结果不证明模型提取质量，也不替代用户最终可用性验收。
事项位置无法唯一对应、同段动作无法可靠区分时不缩窄时间上下文，保守保留待确认时间，允许用户修正后保存。
用户新增事项没有自动推断的日期或材料，须明确补充或选择原文依据。
未实现提醒、打包或自动更新；未合并 main、推送、创建 PR 或关闭 Issue。
