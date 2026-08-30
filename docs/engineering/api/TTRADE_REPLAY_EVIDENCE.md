# 做 T 回放：信号与决策审计

## 页面与真源

回放工作区导航为：总览、信号、决策审计、仓位与批次、运行动态、参数、账户。

| 页面 | 回答的问题 | 权威数据 |
| --- | --- | --- |
| 信号 | 何时出现机会、候选如何变化、为何被抑制 | 已持久化的机会评估事件与对应快照 |
| 决策审计 | 策略作了什么决定，意图随后如何定量、风控和执行 | DecisionTrace、TradeIntent、执行摘要 |
| 运行动态 | 本次回放发生了哪些运行、上下文和执行事件 | 真实评估、回放阶段、报告、批次和模拟执行事实 |

信号不要求存在交易意图。信号列表展示时间、标的、事件、路径/阶段、机会分/阈值、
候选状态、关联意图；方向和数量在决策审计中展示。无意图的审计仍展示评估标的和
原因，意图列明确为“无交易意图”，执行列为“不适用”。`strategy_output` 是内部标签，
不能作为业务原因。

策略接口仍然只有 `StrategyBase.step(StrategyInput)`，输出 `TradeIntent[]` 和算法
状态补丁。这里的“信号”只是只读页面概念，不恢复旧领域 Signal 主路径。

## 信号分类

信号页默认只包含 `record_kind=MATERIAL` 的下列事件：

- `FSM_TRANSITION`
- `CANDIDATE_LATCHED`
- `CANDIDATE_AWAITING_APPROVAL`
- `CANDIDATE_SUPPRESSED`
- `CANDIDATE_REARMING`
- `CANDIDATE_CLEARED`
- `CANDIDATE_STATE_CHANGED`
- `INTENT_LINKED`

策略配置、标的画像和行情连续性变化属于 `CONTEXT`，在运行动态默认可见。
已有的 `COALESCED_DIAGNOSTIC` 记录属于 `DIAGNOSTIC`，仅由运行动态的诊断开关
显式查询。本次重构只归档已经持久化的事实，不新增普通 Tick、诊断观测或无变化
状态的持久化路径。

## 精确版本契约

两个查询都要求同时提供 `runId` 和 `backtestId`：

```graphql
tTradeReplaySignalEvaluations(
  runId: String!
  backtestId: String!
  filters: TTradeReplaySignalFilterInput
  first: Int! = 50
  after: String
): TTradeReplaySignalPage!

tTradeReplayDecisionAudit(
  runId: String!
  backtestId: String!
  filters: TTradeReplayAuditFilterInput
  first: Int! = 50
  after: String
): TTradeReplayAuditPage!
```

Resolver 校验运行模式、做 T 回放标记、回测归属及冻结账户与运行账户一致性，再执行
账户授权。操作权限为 `strategy:read`。查询不发命令、不确认候选、不下单。

| 字段 | 含义 |
| --- | --- |
| `evidence.runId/backtestId/backtestVersion` | 本次读取的精确身份，客户端必须与当前选择一致 |
| `evidence.availability` | `AVAILABLE` 或 `UNAVAILABLE`，不可用不等于零事件 |
| `evidence.source` | 运行中为 `RUN_PROJECTION`，终态为 `VERSION_ARCHIVE` |
| `evidence.sealed` | 新版已完成归档通过密封与指纹校验；旧版审计为 false |
| `evidence.contentFingerprint` | 已读取产物的指纹，也参与游标身份绑定 |
| `summary` | 当前筛选的全部匹配记录统计，不是当前分页条数 |
| `pageInfo` | 与版本、来源、指纹和筛选绑定的游标 |

信号筛选支持标的、事件类型、路径、候选状态、候选 ID、事件键、搜索，以及显式的
上下文/诊断开关。审计筛选支持标的、有无意图、执行状态、事件键和搜索。

`first` 只允许 1–100。排序为事件时间与稳定行 ID 倒序；文件扫描只保留 `first+1`
条分页候选，同时计算准确摘要。信号行使用原始 `eventKey` 去重；归档审计行使用
精确回测版本内的文件记录序号作为不透明 ID。`traceId` 是一组策略/定量/风控记录
的关联标识，不是唯一行 ID，不能据此合并记录。

运行中的查询只允许读取当前最新回测的 run-scoped 投影，读取前后都检查版本是否
变化。终态查询只读选中版本的文件，绝不回退到最新版本、实盘投影或其它回放。
切换 run/backtest/filter 后前端重新开始分页，并拒绝旧请求的迟到结果及错误。
投影转为归档后，旧游标必须刷新，不能跨来源合并。

## 双向追溯与数量口径

信号到审计的关联只使用 `output_summary.evaluation_references[].evaluation_event_key`。
审计到信号使用同一精确事件键。候选链路是同一回测版本内按 `candidateId` 筛选的
真实事件序列，不调用实盘候选查询，不按时间/股票猜测关联。

没有直接关联审计时，页面明确说明未携带关联，不表示信号不存在，也不补造意图。

`TradeIntentView` 的目标量使用显式字段：

- `targetVolume`：股数目标。
- `targetAmount`：金额目标。
- `targetPositionPct`：资产比例的小数值，页面按百分数展示。

这些是策略目标，不是合法委托数量。审计详情分别展示定量结果、风控、委托标识、
执行状态和模拟成交。缺失价格不展示为零；未记录成交结果不宣称无成交。
风险阻断统计只计显式风险决策事实，不把候选抑制、零数量定量拒绝或普通撤单当作
风控拒绝。

## 归档与完成顺序

新回测 manifest 使用 schema v4。机会评估产物为版本目录内的
`opportunity_evaluations.jsonl`；metadata 包含账户、运行、回测、版本、记录数及
SHA-256。`decision_events.jsonl` 与 `execution_summary.jsonl` 也记录文件指纹。

1. 完成终态状态检查点，确认机会评估材料已经落库。
2. Repository 以 `(evaluated_at, id)`、每页 500 条导出真实评估。
3. 写临时 JSONL，flush/fsync 后原子替换目标产物。
4. 写其它结果产物与指纹，最后原子发布 `sealed=true` 的 manifest。
5. 成功后才推进回测和运行完成状态；归档失败不得报告完成。

已密封版本禁止再次 flush 或覆盖信号归档。重新回测前必须先校验上一已完成版本的
归档，才允许清空 run-scoped 评估投影。路径只允许版本目录内的产物文件名，不接受
目录逃逸或跨版本替换。

旧 schema v3 不含真实信号归档：信号页返回 `SIGNAL_ARCHIVE_NOT_RECORDED`，提示
重新回放生成新版证据；不重建旧信号、不回填、不双写。原有决策/执行文件仍可只读
查看，标识为“历史归档”，不冒充新版密封证据。

## 不可用与错误

| `reasonCode` | 用户语义 |
| --- | --- |
| `SIGNAL_ARCHIVE_NOT_RECORDED` | 此版本没有信号归档，需重新回放 |
| `AUDIT_ARCHIVE_NOT_RECORDED` | 此版本未保存可读取的审计产物 |
| `ARCHIVE_UNAVAILABLE` | 产物缺失或不可读取 |
| `ARCHIVE_NOT_SEALED` | 归档未完成，不能作为完成版证据 |
| `ARCHIVE_IDENTITY_MISMATCH` | 账户/运行/回测/版本身份不一致 |
| `ARCHIVE_INTEGRITY_FAILED` | 指纹、记录数或文件格式校验失败 |

以上返回显式不可用页面；鉴权、非法版本和游标错误按 GraphQL 错误处理，前端提供
重新读取入口。加载、真实空结果、筛选无匹配、不可用、请求失败分别展示。

## 验证与运行边界

单元测试覆盖归档为空/有内容、流式写入中断、密封禁止覆盖、损坏、账户和版本校验、
200 条以上及同时间同 trace 分页、无意图信号、旧审计读取、跨页追溯和旧响应隔离。
实时信号页仍复用原有审批与信任门禁，回放页面不提供实盘操作。

GraphQL 改动必须通过 Caddy `/graphql` 生成客户端，再运行根工作区的
`npm run check`、`npm run lint`、`npm run test:run`、`npm run build`，并运行
`npm run docs:contracts` 更新公开契约。浏览器验收使用批准的 V01/V02，覆盖
1440px 和 1024px 桌面、展开/折叠、键盘 focus/Escape、横向滚动与错误状态。

API 需要重新加载契约时，先取得重启许可，再通过统一 `ops/quantx.ps1` 入口整体
重启 Dev，保持 `full/live`。不能为 codegen 单独起 API、绕开 Caddy、静默切换
data-only，或在测试过程中触发真实交易。

### V01 / V02 验证记录（2026-08-31）

- 已按许可重启 full/live，Caddy codegen、check、lint、707 项前端测试、Web/Docs
  构建及原有包体预算通过；后端针对性测试 171 项、公开契约测试 9 项通过。
- 浏览器检查原反馈的 af16d791 / V18：旧信号归档明确不可用，审计显示 6014 条，
  首屏分页 50 条；标的、无意图说明、输入和状态变化均可读。1440px 展开态已检查。
- 1024px、剩余真实键盘操作和跨页点击因浏览器控制连接持续超时未完成；相关组件
  状态与交叉追溯已有单元测试，但不替代这些浏览器检查。
- 本次没有启动真实交易或重跑历史回测。QMT 交易账户仍未就绪，不宣称实盘验收通过。
