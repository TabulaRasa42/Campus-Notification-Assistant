# #4 / #6 共同接口与集成边界

基线：`fe8b90094eb299030ce0fa3c3e61c94c5c9f8290`；2026-10-26 已 fetch 核实远端 main。

## 身份与状态

- #4 的提取批次和事项均为内存草稿；批次 ID 用于一次确认的幂等，事项 ID 稳定，不用列表位置作为保存身份。
  各事项可选择；保存前所有选中事项一起校验并在同一个 SQLite 事务写入。成功后本批次冻结，重复提交返回原保存结果。
- #6 的 Todo ID 是数据库主键，单条待办编辑快照独立于通知提取草稿。
  修改暂存在内存，明确保存才更新此 ID；失败保留快照，其他待办及通知全文不变。
- 待办完成状态使用 `completed: bool = False`，旧 JSON 缺字段按未完成读取，不批量改写旧记录。
  完成／恢复更新同一 JSON；删除只删除目标主键。提醒联动留给 #8，本批不实现提醒。

## 字段规则

- 共同使用 `service.correct_item_field(item, source, field, value, ...) -> Item` 和
  `service.select_item_evidence(item, source, field, start, end) -> Item`。
  字段路径继续是 `action`、`deadline`、`materials.N`；Python 字符位置由控制器转换 QML UTF-16 位置。
- 内容修正和选依据分开；只处理明确指定字段。沿用 #12 的独立时间修正与待核对保留规则。
  选依据不得回退已修正的材料名称、说明或关系；其他字段操作不得消除未解决引用／时间状态。
- 用户补充内容清除原文引用并明确标记。未选依据的修改不得伪造支持；存在无效依据的选中事项不可保存。
- 保留事项名、截止日期、所需材料三部分；材料继续用 Material 的名称、完整说明、关系和引用。
  不重新解释历史材料，不新增注意事项。新增材料操作也须经过上述来源规则。

## 责任与兼容

- A：#4 草稿集合、当前事项导航／选择、增删修正、批量确认、事务与幂等，保留单事项公开调用兼容。
  多事项确认提供 `confirm_selected(batch_id) -> list[Todo]`；`confirm` 保留单事项调用行为。
- B：#6 `begin_todo_edit(id)`、`correct_todo_field(...)`、`select_todo_evidence(...)`、
  `save_todo_edit(id)`、`cancel_todo_edit(id)`、`set_completed(id, bool)`、`delete_todo(id)`。
  编辑快照可用独立服务属性 `todo_edit`；不要借用 `service.draft`。
- 双方可在各自隔离工作树修改 service/controller/Main.qml；整合冲突由主代理负责。
  公共 helper 与本文的修改先协调；不让实现子代理编辑主集成工作树。
- 共同测试边界已获用户确认：公开应用服务、真实临时 SQLite、QML 交互；仅替换 HTTP、凭据等外部边界。
  每轮先记录行为失败再最小实现，保留红／绿证据；不写私有实现断言。
- 新真实 API 授权余额为 0。两个实现代理均不得真实调用 API、合并 main、关闭 Issue、实现 #8/#9/#10。

## 界面约束

沿用 QML Fusion 灰度、8/16/24 间距与 12/14/18/24 字号；通知和当前事项为主，状态和依据为次。
新增草稿导航和待办操作使用文字状态；同一阶段只保留一个保存主操作。最小 960×700 和常规窗口均实际渲染检查。
