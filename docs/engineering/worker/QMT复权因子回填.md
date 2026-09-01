# QMT 复权因子可恢复回填

## 目标与边界

本链路通过现有出站 QMT Agent 调用只读
`xtdata.get_divid_factors`，把沪深股票和 ETF 的公司行为因子分批上传并持久化到
PostgreSQL `divid_factors`。股票、ETF 是唯一正式默认 universe，campaign 始终
额外包含研究基准
`000300.SH`；指数零因子行是合法结果，但仍需完成请求来证明窗口已检查。

- Worker 不导入 `xtquant`，QMT SDK 仍只存在于 `apps/qmt-agent`。
- Agent 必须同时声明 `market-data`、`divid-factors`，并处于 `live` 或
  `data-only` 模式。
- 请求只使用 `market_data_request`，不创建账户、委托或交易命令。
- 因子是公司行为发生日的稀疏数据；某只股票或 ETF 返回零行是合法结果，不表示
  下载失败。
- `--code-limit` 仅用于 smoke，分别限制股票数和 ETF 数，不会移除
  `000300.SH` 基准。正式 campaign 不得传该参数。
- 复权因子回填和日线回填共用一个 PostgreSQL advisory lock，禁止两者并发
  占用串行 XTData 请求通道。

QMT 实测返回 DataFrame 的索引是 `YYYYMMDD` 除权日，字段是：

```text
time interest stockBonus stockGift allotNum allotPrice gugai dr
```

## 写入与恢复语义

一次作业只替换精确的 `stock_codes × ex_date window`：

1. 校验上传分片顺序、SHA-256、记录数和 JSON 类型。
2. 校验代码属于请求范围，`ex_date` 位于请求窗口，`time` 与除权日一致，
   所有数值有限且 `dr > 0`。
3. 在同一事务中审计原窗口、删除原窗口、插入 QMT 权威结果。
4. 回读并逐行核对代码、时间、字段值和 PostgreSQL 定点精度；不一致则
   rollback，删除不会单独提交。
5. 在把请求标记为 `COMPLETED` 前，强制核对范围、原有/删除记录数、
   源/写入记录数和规范化整行 SHA-256；任一项不一致都拒绝完成请求。
6. 临时上传文件在成功入库后按设计清理。campaign 的后验收只读取持久化的
   request、分片元数据和 replacement audit，再从 PostgreSQL 独立回读同一
   精确窗口并重算整行摘要。

旧表没有 `(stock_code, ex_date)` 唯一约束，因此不能安全依赖
`ON CONFLICT`。仓储中的单条保存、旧批量追加、按代码删除和精确窗口替换会
获取同一个 PostgreSQL 事务级 advisory lock；这既阻止旧写入口与权威替换
交错，也阻止两个重叠空窗口同时通过各自的事务内校验。新 QMT 入库只使用
精确窗口替换，旧 `bulk_save` 仅为尚未删除的历史服务入口保留。精确窗口事务
替换同时兼容既有数据库并保证重跑幂等。快照 loader 与 campaign 后验收在读取
request evidence 前获取同一 advisory lock 的事务级 shared 模式，直到当前因子
行的逐代码摘要校验完成才释放，避免 replacement 插入两次读取之间。只有
QMT 请求或传输失败才增加 attempt。请求完成后先把 `request_id` 持久化为
`verifying`；后验收或状态写盘失败时只重验同一完成请求，不重复下载。
运行期异常会先用同一幂等键恢复，每三次重新选择一次 Agent，连续九次仍不能
收敛才停止为 `failed`；历史事件有固定上限。完成请求连续两次无法通过持久化
验收时也会停止为 `failed`。此时显式 `--retry-failed` 才会放弃旧证明并创建
新的 attempt。campaign 写为 `completed` 前还会重新验收全部已完成批次，防止
长跑期间的数据库漂移被旧 state 摘要掩盖。campaign 的会话级 advisory lock
会同时记录 PostgreSQL backend PID；每次写 state 前，都用持锁连接查询
`pg_locks` 核对该 PID 仍持有目标锁。连接透明重连或锁丢失后进程立即失败，
不会以旧所有者身份继续改写账本。成功账本在顶层记录 `completed_at`。

## 运行

先确认日线历史回填已经退出，再部署代码并重启 full profile，使 QMT Agent
加载 `divid_factors` operation。不要在日线回填运行期间重启 Agent。
`daily-market-data-sync` 的下载请求只包含 `bars`，不会隐式补齐
`divid_factors`；必须先完成本 campaign，再执行
`compute_daily_signals=true` 的日级因子快照。

日级因子快照默认覆盖全部沪深股票和 ETF。持久化完成请求必须为每个目标代码
连续覆盖“最早快照日减 540 个日历日”到“最晚快照日”的闭区间。例如重算
`20260803..20260831` 的保留窗口，最小复权证明范围是
`20250209..20260831`。下面的全历史命令从更早的 `20200313` 开始，因此也满足
该 540 日 lookback。

先用独立状态文件验证一个小批次：

```powershell
uv run --package quantx-worker python -u `
  apps/worker/scripts/backfill_divid_factors.py `
  --start-date 20200313 `
  --end-date 20260831 `
  --batch-size 5 `
  --code-limit 5 `
  --max-jobs 1 `
  --state-file .runtime/research-backfill/divid-factor-smoke.json
```

全沪深股票、ETF 和研究基准回填：

```powershell
uv run --package quantx-worker python -u `
  apps/worker/scripts/backfill_divid_factors.py `
  --start-date 20200313 `
  --end-date 20260831 `
  --batch-size 200 `
  --poll-seconds 3 `
  --state-file `
    .runtime/research-backfill/full-stock-etf-divid-factors-audit-v2-20200313-20260831.json
```

未传 `--state-file` 时也会使用上述
`full-stock-etf-divid-factors-audit-v2-<start>-<end>.json`
命名。正式运行不得沿用旧的 `full-a-share-*` 账本。

同一命令重跑会读取状态账本并从未完成批次继续。状态文件的
`summary.source_records` 与 `summary.persisted_records` 必须相等，且所有作业
必须为 `completed`；每个完成作业的 `audit.code_audits` 会保留逐代码记录数和
源/当前数据库摘要。注意记录数远小于股票交易日数是正常现象。
股票、ETF 和基准的正式 campaign 使用 state schema v4，并记录 replacement
audit schema v2、universe 版本、两类标的数量、各自代码摘要和总代码摘要。
旧 state schema 和 replacement audit schema v1 不能续跑或作为全市场日级
快照的覆盖证明，必须使用新的 state 文件重新发起。

研究正式 gate 不读取本地 state 文件，而是查询 PostgreSQL 中持久化的
`market_data_request`。只有同时满足以下条件的请求才能进入覆盖区间合并：

- `status=COMPLETED`、`source=qmt-get-divid-factors-v1` 且分片数完整；
- `ingestion_result.replacement_audit.audit_schema_version=2`；
- 代码范围、日期范围、删除/插入/回读行数和代码摘要自洽；
- 源数据与落库的聚合摘要一致，并且 `code_audits` 精确包含请求中的每个代码
  （包括零事件代码），逐代码记录数与规范化整行摘要均一致；
- 消费时按代码重新回读当前 `divid_factors` 精确范围，逐代码行数与摘要仍与
  audit 一致。某个代码后续被重写只会使该代码的旧证明失效，不会连带废除同批
  其他未变化代码的证明。

因此旧版只有 `COMPLETED` 和分片计数的请求不能证明研究或快照就绪；已完成后
又发生内容漂移的请求也会立即失去证明资格。精确审计的零行结果仍能证明
“请求过且窗口内确实没有公司行为”。state 文件仍是 campaign 续跑和逐批
source/persisted 摘要验收的运维账本。

逐代码摘要允许同一日期窗口内只重刷部分代码而不连带废除其他代码的旧证明。
它不把同一代码的任意重叠日期窗口切成可拼接摘要：若用较小子窗口改写某代码，
该代码原全窗口摘要会失效，而子窗口证明不能代表其余日期。正式回填因此必须
持续使用一致的全历史窗口；需要支持同代码分段修正时，应先引入固定日期分片且
强制写入窗口对齐，不能把任意重叠窗口当作可安全合并的证明。

任何复权因子写入都会在同一个数据库事务内撤销现有日级因子快照的发布资格：
`daily-v1` 快照行不再携带当前计算版本，对应的 `success` 或
`scoped_success` 运行记录转为 `invalidated`。这样，因子锁释放后的历史修订不会
让 API 继续把旧因子计算结果认证为可用；必须重新计算并完整发布快照后才能恢复
选股结果。

日级快照不再使用固定 TTL 的 Redis 互斥。每个目标交易日使用独立 PostgreSQL
会话级 advisory lock，批次前后和终态写入前后都核对 backend PID 与
`pg_locks` 所有权；透明重连或锁丢失后立即停止写入。一次 Flow 只读取一次完整
标的及上市/退市元数据，在内存中冻结各日 active universe，并要求
`saved + skipped + failed = total_codes`，否则不能写出全市场 `success`。
快照计算 Flow 不执行跨日期的全局保留期清理，避免不同目标日期并发计算时互相
删除结果；保留期清理由独立维护任务在统一的全局清理栅栏下执行。

## `dr` 方向实测与复权公式

对 `600519.SH` 的 2020-06-24 现金分红日进行了只读核对：

| 项目 | 数值 |
| --- | ---: |
| 2020-06-23 原始收盘 | 1474.50 |
| 2020-06-24 QMT `preClose` | 1457.48 |
| QMT `interest` | 17.025 |
| QMT `dr` | 1.011677 |
| `1474.50 / 1457.48` | 1.01167769 |

因此 QMT 的事件因子方向是：

```text
dr = 除权前原始收盘 / 除权参考价
```

要构造以最新价格为基准的前复权序列，应把事件日前历史价格除以该事件的
`dr`，事件日及以后保持不变。以最早价格为基准的后复权序列，应保持事件日前
价格不变，把事件日及以后乘以 `dr`。

QuantX 基础设施与研究侧统一使用以下公式，其中 `cum_past` 包含当前 bar
当日已经生效的事件，`total` 只包含研究 `as_of` 日及之前的事件：

```text
front_adjust_factor = cum_past / total
back_adjust_factor  = cum_past
```

前复权在历史日期上会使用该日期之后、但 `as_of` 之前发生的公司行为，是
“以研究截止日为基准的事后重述”。这适合在固定研究截止日生成连续历史价格，
但不能把同一份全样本前复权序列直接作为历史逐日决策输入。禁止未来泄漏的
事件研究和回测应使用 `point_in_time`（等价于后复权）：每根 bar 只累计当日
及之前已生效的 `dr`，并显式忽略 `as_of` 之后因子。

数值回归测试同时覆盖前复权、后复权和“未来事件不改变截止日前结果”。

## 可辩护的 `pre_close` 回退

QMT 日线的 `preClose` 在除权日已经是公司行为调整后的参考价。上例中
2020-06-24 的原始收盘为 1460.01：

```text
1460.01 / 1457.48 - 1 = 0.1736%
```

它不会把现金分红造成的机械价格缺口误认为负收益。因此在完整因子尚未回填或
累计公式尚未修正时，可按每只股票构造不使用未来信息的公司行为中性指数：

```text
index_t = index_(t-1) * close_t / pre_close_t
```

要求 `close`、`pre_close` 均为有限正数，并对停牌、缺失和重复交易日保守
降级。若研究还需要同日 OHLC，可令
`scale_t = index_t / close_t`，再将原始 OHLC 同乘 `scale_t`。

当前日线仓储已保存 `pre_close`，但研究 canonical columns 尚未保留该字段；
采用此回退前需要在研究数据适配层显式接入并记录质量覆盖率。
