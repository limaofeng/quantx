# Canonical Tick Archive（V3 正式因果回放的离线输入）

`canonical_tick_archive` 是一套独立、不可变且外部可溯源的 Tick 对象库。它只服务于显式选择 token 的做 T V3 正式 `BACKTEST` 验收；默认回测、PAPER、LIVE、QMT Agent 和 Influx 路径完全不变。

它不修复或猜测历史缺口：缺少原始 `source_time_ms` / `tick_ordinal`、缺少 provider 原始文件，或无法验证来源 hash 时，导入和正式验收都会失败。

## 完整外部来源契约

每一次发布必须提供一个 UTF-8 JSON source manifest，以及与之逐项对应的原始 UTF-8 NDJSON 输入。manifest 不是可选元数据：它必须是 `VERIFIED`，并且所有 `instrument-day` 的原始输入 SHA-256 会在任何 archive object 发布前实际校验。

```json
{
  "schema_version": 1,
  "source": {
    "provider_id": "provider-id",
    "source_kind": "EXTERNAL_RAW_TICK_NDJSON",
    "acquired_at": "2026-08-21T16:00:00+08:00",
    "as_of": "2026-08-21T15:05:00+08:00",
    "verification_status": "VERIFIED",
    "identity_provenance": {
      "source_time_ms": "provider event epoch milliseconds",
      "tick_ordinal": "provider stable same-millisecond ordinal"
    }
  },
  "formal_scope": {
    "snapshot_date": "2026-07-24",
    "instrument_codes": ["000001.SZ", "600000.SH"],
    "trading_dates": ["... exactly 20 SH trading dates ..."],
    "scope_fingerprint": "sha256"
  },
  "instrument_days": [
    {
      "instrument_code": "000001.SZ",
      "trade_date": "2026-07-27",
      "raw_input_sha256": "sha256 of exact raw NDJSON file",
      "identity_provenance": {
        "source_time_ms": "provider event epoch milliseconds",
        "tick_ordinal": "provider stable same-millisecond ordinal"
      }
    }
  ]
}
```

`formal_scope` 必须恰好声明 D-1 snapshot、20 个递增日期，以及所有声明持仓与 20 日的完整笛卡尔积。archive 格式本身强制完整 20 日范围；正式 acceptance 还会以运行时 SH 日历、实际 D-1 持仓 snapshot 和 token scope 三重精确比对，因此任意 20 个自然日或子集都不能冒充正式范围。

每一行 Tick NDJSON 必须恰好含有以下字段，不能补默认值或携带未知字段：

```text
stock_code, period, time, last_price, open, high, low, last_close, amount,
volume, pvolume, tickvol, stock_status, open_int, last_settlement_price,
settlement_price, transaction_num, price_tick, up_stop_price, down_stop_price,
ask_price, bid_price, ask_vol, bid_vol, source_time_ms, tick_ordinal,
continuity_generation, market_stream_id, market_stream_sequence,
market_stream_reset
```

`period` 固定为 `tick`；`time` 必须含时区，并严格等于 `(source_time_ms, tick_ordinal)` 的 storage timestamp；`market_stream_id` 不得为空。每个 Tick 的上海交易日必须等于对应 manifest pair。

## 导入与不可变发布

正式账户范围优先通过已登记 QMT Agent 准备。该命令只请求 XTData 历史 Tick，
不会访问交易 Broker；它把每个标的拆成最多 7 个日历日的请求，对完整范围采集两
遍并逐日比对记录数、内容 hash 与 source identity，全部一致且归档质量门通过后才
输出 token：

```powershell
python -m quantx_engine.t_trade_v3_acceptance `
  --account-id <account-id> `
  --trading-days 20 `
  --prepare-canonical-tick-archive `
  --snapshot-date <D-1-snapshot-date> `
  --canonical-tick-archive-root D:\quantx-canonical-ticks
```

若 Agent 不可用、设备串行队列被既有 ingestion 失败阻塞、任一 instrument-day
缺失、两次采集不一致或 Tick 会话质量不合格，准备立即 fail-closed，不发布 token。
Worker 恢复上传时也按请求声明的 destination 路由，canonical 输入不会写入普通
Influx 路径。

已经持有独立、可验证外部来源文件时，也可以使用底层显式发布入口：

```powershell
python -m quantx_infrastructure.services.canonical_tick_archive publish `
  --archive-root D:\quantx-canonical-ticks `
  --source-manifest D:\verified-source\source-manifest.json `
  --record '000001.SZ@2026-07-27=D:\verified-source\000001.SZ-2026-07-27.ndjson' `
  --record '600000.SH@2026-07-27=D:\verified-source\600000.SH-2026-07-27.ndjson'
```

发布按固定大小 chunk 外排；归并后逐行写 staged object，并增量计算记录数和 SHA-256，不会把完整数据集累计进内存。object 以内容 hash 命名，staged file 通过原子 hard-link 发布；已存在同 hash object 是幂等成功，不同内容冲突失败。所有 object、manifest 和 cutover 都验证完成后才会发布 `canonical-tick-v1-<manifest-sha256>` token。

reader 只提供有界 `iter_tick_pages`（或严格 limit 的小范围 helper）。它会验证 manifest、object hash、每行字段与 source identity。其公共窗口需要时区感知的 start/end；跨周末或节假日时只遍历 scope 内的 SH trading dates，不会要求虚构的日历日文件。窗口越过正式 scope 则失败。

Engine 的 legacy offset 接口由 archive adapter 的严格 query-key 顺序游标桥接：只接受 `offset=0, N, 2N...` 的连续调用，非预期 offset、并发/中途 reset 均失败。这样真实 executor 多页读取是线性流式的，不会在每一页从对象开头重复跳过记录。

## 正式 V3 验收显式选择

仅在离线、正式 20 日验收时同时传 root 和 token：

```powershell
python -m quantx_engine.t_trade_v3_acceptance `
  --account-id <account-id> `
  --trading-days 20 `
  --execute `
  --canonical-tick-archive-root D:\quantx-canonical-ticks `
  --canonical-tick-cutover-token canonical-tick-v1-<manifest-sha256>
```

该模式会：

- 打开并验证 token、manifest、source provenance 与 object 内容；
- 要求 token scope 与 D-1 snapshot、全持仓、20 个已完成 SH trading dates 精确一致；
- 对窗口内全部真实行情统一执行因果性、连续性和 source identity 门禁；
- 用 archive 的流式 Tick completeness / source-identity 审计；
- 在 task-local isolated adapter lease 中启动真实 `StrategyExecutor` BACKTEST；
- 禁止 `HistoricalMarketDataService`、Influx、QMT 补数及任何 fallback/dual-read。

若 token 缺失、篡改、scope 不完整、日期不是已完成交易日，或 archive adapter 查询不满足严格分页契约，启动失败而不会退回旧数据源。适配器仅由该 run 持有，并在启动失败、自然结束、异常、取消和显式 stop 时幂等释放；它不会改变普通 Influx backtest 的引用计数。

## 已完成交易日规则

正式 D-1 因果窗口从 snapshot 后向前取第一个 20 个 **已完成** SH trading dates，并且永远不包含当前日期；历史 snapshot 已经完整落在当前日期之前时，日期不会被重新改写。若不足 20 日，正式门禁保持 blocked。

独立的最近 N 日诊断可用：

```powershell
python -m quantx_engine.t_trade_v3_acceptance `
  --recent-completed-trading-days 5 `
  --report docs/reports/t-trade-v3-recent-completed-diagnostic.md
```

它调用同一有界 SH 日历 resolver，从当前日期之前向后取最近 N 个完整交易日（例如 2026-08-24 取 2026-08-17 至 2026-08-21），并显式标记 `NON_GATING_NON_CAUSAL`。它不启动回放，不能写入 formal/PAPER/LIVE 证据，也不能与 archive formal execution 混用。

即使未传 `--report`，最近 N 日诊断也会默认写入 `docs/reports/t-trade-v3-recent-completed-diagnostic.md`，绝不会覆盖正式验收报告 `docs/reports/t-trade-v3-acceptance.md`。
