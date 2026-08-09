# QMT 复权因子可恢复回填

## 目标与边界

本链路通过现有出站 QMT Agent 调用只读
`xtdata.get_divid_factors`，把沪深股票公司行为因子分批上传并持久化到
PostgreSQL `divid_factors`。campaign 始终额外包含研究基准
`000300.SH`；指数零因子行是合法结果，但仍需完成请求来证明窗口已检查。

- Worker 不导入 `xtquant`，QMT SDK 仍只存在于 `apps/qmt-agent`。
- Agent 必须同时声明 `market-data`、`divid-factors` 和 `data-only`。
- 请求只使用 `market_data_request`，不创建账户、委托或交易命令。
- 因子是公司行为发生日的稀疏数据；某只股票返回零行是合法结果，不表示
  下载失败。
- `--code-limit` 只限制股票数，不会移除 `000300.SH` 基准。
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
5. 状态账本记录源记录数、实际有事件的股票数、日期范围、分片数、源摘要和
   持久化摘要。

旧表没有 `(stock_code, ex_date)` 唯一约束，因此不能安全依赖
`ON CONFLICT`。精确窗口事务替换同时兼容既有数据库并保证重跑幂等。失败重试
会增加请求 attempt，已完成请求则从分片和数据库重新验收，不重复盲写。

## 运行

先确认日线历史回填已经退出，再部署代码并重启 full profile，使 QMT Agent
加载 `divid_factors` operation。不要在日线回填运行期间重启 Agent。

先用独立状态文件验证一个小批次：

```powershell
uv run --package quantx-worker python -u `
  apps/worker/scripts/backfill_divid_factors.py `
  --start-date 20200313 `
  --end-date 20260730 `
  --batch-size 5 `
  --code-limit 5 `
  --max-jobs 1 `
  --state-file .runtime/research-backfill/divid-factor-smoke.json
```

全沪深股票回填：

```powershell
uv run --package quantx-worker python -u `
  apps/worker/scripts/backfill_divid_factors.py `
  --start-date 20200313 `
  --end-date 20260730 `
  --batch-size 200 `
  --poll-seconds 3 `
  --state-file `
    .runtime/research-backfill/full-a-share-divid-factors-20200313-20260730.json
```

同一命令重跑会读取状态账本并从未完成批次继续。状态文件的
`summary.source_records` 与 `summary.persisted_records` 必须相等，且所有作业
必须为 `completed`。注意记录数远小于股票交易日数是正常现象。
包含基准的新 campaign 使用 state schema v2；旧 schema v1 状态不含基准，
不能继续作为正式研究覆盖证明，应使用新的 state 文件重新发起。

研究正式 gate 不读取本地 state 文件，而是查询 PostgreSQL 中持久化的
`market_data_request`：只接受 `status=COMPLETED`、
`source=qmt-get-divid-factors-v1`、分片数完整的请求，并按每个目标代码合并
请求日期区间。`COMPLETED` 只会在上传校验、因子校验和精确窗口事务替换成功
后写入，因此该数据库证据也能证明“请求过但确实零事件”的代码。state 文件
仍是 campaign 续跑和逐批 source/persisted 摘要验收的运维账本。

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
