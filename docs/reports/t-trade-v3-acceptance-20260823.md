# 做 T V3 历史回放与全持仓压力验收

- 生成时间：`2026-08-23T23:37:20.699964+08:00`
- 正式 20 交易日门禁：**BLOCKED**
- 因果口径：D-1 账户日结快照；按 SH 真实交易日；所有正持仓均纳入，绝不自动剔除。
- Tick 口径：Engine 同款严格连续交易时段检查；正式/压力执行前另以严格 source-identity keyset 分页验证。
- 执行边界：仅隔离 `BACKTEST`；本工具不启动 QMT、不发送 PAPER/LIVE 指令、不补数。
- 完整机读证据：[JSON](t-trade-v3-acceptance-20260823.json)

## 20 个交易日严格因果回放

**BLOCKED**：没有任一 D-1 快照形成全持仓 20/20 Tick 完整窗口；因此没有启动正式回放。

| D-1 快照 | 持仓 | 窗口 | 完整 instrument-day | 共同完整日 | 连续共同前缀 | 门禁阻塞 |
| --- | ---: | --- | ---: | --- | --- | --- |
| 2026-06-01 | 8 | 2026-06-02~2026-06-30 | 45/160 | 2026-06-02,2026-06-04,2026-06-05 | 2026-06-02 | ALL_HOLDINGS_TICK_COVERAGE_INCOMPLETE,ABNORMAL_DAY_EVIDENCE_NOT_DECLARED |
| 2026-06-02 | 8 | 2026-06-03~2026-07-01 | 37/160 | 2026-06-04,2026-06-05 | - | ALL_HOLDINGS_TICK_COVERAGE_INCOMPLETE,ABNORMAL_DAY_EVIDENCE_NOT_DECLARED |
| 2026-06-03 | 8 | 2026-06-04~2026-07-02 | 37/160 | 2026-06-04,2026-06-05 | 2026-06-04,2026-06-05 | ALL_HOLDINGS_TICK_COVERAGE_INCOMPLETE,ABNORMAL_DAY_EVIDENCE_NOT_DECLARED |
| 2026-06-04 | 13 | 2026-06-05~2026-07-03 | 49/260 | 2026-06-05 | 2026-06-05 | HELD_INSTRUMENT_NOT_REPLAYABLE,ALL_HOLDINGS_TICK_COVERAGE_INCOMPLETE,ABNORMAL_DAY_EVIDENCE_NOT_DECLARED |
| 2026-06-05 | 12 | 2026-06-08~2026-07-06 | 36/240 | 2026-06-16,2026-06-17,2026-06-18 | - | ALL_HOLDINGS_TICK_COVERAGE_INCOMPLETE,ABNORMAL_DAY_EVIDENCE_NOT_DECLARED |
| 2026-06-09 | 12 | 2026-06-10~2026-07-08 | 36/240 | 2026-06-16,2026-06-17,2026-06-18 | - | ALL_HOLDINGS_TICK_COVERAGE_INCOMPLETE,ABNORMAL_DAY_EVIDENCE_NOT_DECLARED |
| 2026-06-10 | 12 | 2026-06-11~2026-07-09 | 36/240 | 2026-06-16,2026-06-17,2026-06-18 | - | ALL_HOLDINGS_TICK_COVERAGE_INCOMPLETE,ABNORMAL_DAY_EVIDENCE_NOT_DECLARED |
| 2026-06-16 | 12 | 2026-06-17~2026-07-15 | 34/240 | 2026-06-17,2026-06-18 | 2026-06-17,2026-06-18 | ALL_HOLDINGS_TICK_COVERAGE_INCOMPLETE,ABNORMAL_DAY_EVIDENCE_NOT_DECLARED |
| 2026-06-17 | 12 | 2026-06-18~2026-07-16 | 32/240 | 2026-06-18 | 2026-06-18 | ALL_HOLDINGS_TICK_COVERAGE_INCOMPLETE,ABNORMAL_DAY_EVIDENCE_NOT_DECLARED |
| 2026-06-18 | 12 | 2026-06-22~2026-07-17 | 20/240 | - | - | ALL_HOLDINGS_TICK_COVERAGE_INCOMPLETE,ABNORMAL_DAY_EVIDENCE_NOT_DECLARED |
| 2026-07-02 | 13 | 2026-07-03~2026-07-30 | 61/260 | - | - | ALL_HOLDINGS_TICK_COVERAGE_INCOMPLETE,ABNORMAL_DAY_EVIDENCE_NOT_DECLARED |
| 2026-07-13 | 11 | 2026-07-14~2026-08-10 | 90/220 | 2026-07-14,2026-07-16 | 2026-07-14 | ALL_HOLDINGS_TICK_COVERAGE_INCOMPLETE,ABNORMAL_DAY_EVIDENCE_NOT_DECLARED |
| 2026-07-14 | 11 | 2026-07-15~2026-08-11 | 82/220 | 2026-07-16 | - | ALL_HOLDINGS_TICK_COVERAGE_INCOMPLETE,ABNORMAL_DAY_EVIDENCE_NOT_DECLARED |
| 2026-07-15 | 11 | 2026-07-16~2026-08-12 | 89/220 | 2026-07-16 | 2026-07-16 | ALL_HOLDINGS_TICK_COVERAGE_INCOMPLETE,ABNORMAL_DAY_EVIDENCE_NOT_DECLARED |
| 2026-07-16 | 11 | 2026-07-17~2026-08-13 | 85/220 | - | - | ALL_HOLDINGS_TICK_COVERAGE_INCOMPLETE,ABNORMAL_DAY_EVIDENCE_NOT_DECLARED |
| 2026-07-21 | 9 | 2026-07-22~2026-08-18 | 83/180 | - | - | ALL_HOLDINGS_TICK_COVERAGE_INCOMPLETE,ABNORMAL_DAY_EVIDENCE_NOT_DECLARED |
| 2026-07-22 | 10 | 2026-07-23~2026-08-19 | 80/200 | - | - | HELD_INSTRUMENT_NOT_REPLAYABLE,ALL_HOLDINGS_TICK_COVERAGE_INCOMPLETE,ABNORMAL_DAY_EVIDENCE_NOT_DECLARED |

## 真实短窗口 source identity 预检（非 20 日门禁）

**BLOCKED**：16 个 instrument-day 严格 source-identity keyset 检查失败；完整逐日证据在 JSON。
- 失败原因：historical Tick source identity is not integral × 16

## 9,600 Tick 全持仓合成压力尝试

**CANCELLED_BLOCKED_FULL_SYNTHETIC_PRESSURE**：本轮全负载没有完成，SLO 判定为 **BLOCKED/FAIL**，不得以小样本替代。
- runId=`22d070d9-5333-4526-a3c8-3ae7cd2db75e`；请求区间=2026-06-04T09:30:00~2026-06-05T15:00:00；处理至=2026-06-05T09:56:29；进度=82.85216572504707%。
- 取消原因：FULL_9600_TICK_SYNTHETIC_PRESSURE_EXCEEDED_REASONABLE_RUNTIME; operator-authorized cancellation of this isolated BACKTEST only；局部 materialization logical events/s=2.892446。
- fixture：`SYNTHETIC_NON_HISTORICAL`，sha256=c31da4d7c79f729335d80a25945cc56043ac0e39625a5b71d4691c40394d6603，9600 ticks，8 instruments，合法交易时段=Shanghai continuous sessions only: 09:30-11:30 and 13:00-15:00。
- 观察到的主要耗时边界：production evaluation/materialization path with a nonpersistent BACKTEST runtime-state checkpoint; this cancelled historical run did not exercise durable RuntimeState CAS/position writes and made only partial progress within the allowed wall-time budget。
- 未测项：{'cas_conflict_rate': 'N/A: in-memory counter lost at cancellation', 'database_commit_calls': 'N/A: in-process counter lost at cancellation', 'engine_tick_latency_p50_p95_p99': 'N/A: process cancellation releases in-memory samples', 'engine_tick_throughput': 'N/A: completed Tick count was not durably checkpointed'}。

## 全持仓合成压力基线 / 首次本机 SLO

**EXECUTED_DIAGNOSTIC_NON_GATING**：此结果为合成负载，不是历史回放，且不替代 20 交易日门禁。
- fixture：sha256=ea9cd304f06a17fd7c76c05ed5b10861b0d1d509e0431107ce2622f6ff90c442, 480 ticks，8 instruments，合法交易时段=Shanghai continuous sessions only: 09:30-11:30 and 13:00-15:00。
- Engine tick 延迟（ms）：p50=142.25395, p95=5509.579435, p99=9853.155481
- 策略评估延迟（ms）：p50=3.5759, p95=10.88592, p99=26.194444
- 吞吐：0.900525 engine ticks/s（464 ticks）
- CAS：0 conflicts / 465 checkpoint attempts，rate=0.0
- DB 写活动：{'commit_calls': 1118, 'dml_execute_calls': 474, 'flush_calls': 401, 'runtime_state': {'position_replace_snapshot_calls': 1, 'position_rows_submitted': 8, 'position_snapshot_calls': 1, 'position_update_existing_snapshot_calls': 0, 'snapshot_save_calls': 500, 'snapshot_save_failures': 0, 'state_upsert_attempts': 471, 'state_upsert_rejected': 0}}；评估：{'by_record_kind': {'COALESCED_DIAGNOSTIC': {'logical_events': 400, 'rows': 400}, 'MATERIAL': {'logical_events': 64, 'rows': 64}}, 'diagnostic_logical_events': 400, 'diagnostic_merge_ratio': 0.0, 'diagnostic_rows': 400, 'material_rows': 64}
- 生产路径覆盖边界：{'post_cas_evaluation_materialization': True, 'runtime_state_checkpoint': True, 'strategy_evaluator': True, 'strategy_executor_global_source_order': True}。
- 冻结 SLO：N/A（只有完成固定 9,600 Tick 全量夹具才可冻结/判定 SLO）
- 隔离执行证据：runId=`638f3579-b8de-41eb-b5f7-81d5d00e4043`；terminal=TERMINAL；sealed durable RuntimeState=True；QMT=False，PAPER/LIVE command=False.
- Durable RuntimeState latency（ms）：checkpoint p50/p95/p99=121.0767/4016.9334/8627.784988；snapshot p50/p95/p99=85.90745/4289.245725/8921.623375。
- Position DB writes：replace=1，same-code update=0，rows=8；每 Tick 的 state CAS/upsert 仍保留（attempts=471）。
- 性能判读（仅诊断）：strategy p95=10.88592ms，而 checkpoint/snapshot p95=4016.9334/4289.245725ms；长尾位于外部数据库持久化边界。未启动新的 9,600 Tick，SLO 继续 BLOCKED。

## 后续性能修复微基准（非压力验收）

**MICROBENCHMARK_NON_GATING**：packages/infrastructure/src/quantx_infrastructure/services/t_trade_opportunity_runtime_service.py: batch already-closed diagnostic windows in one owned session/atomic commit。
- 正确性微测：8 closed diagnostics preserve 8 rows with 1 session / 1 commit；batch failure rolls back and requeues every closed window (no partial write)。
- SQLite 内存微基准（320 rows / 8 streams）：320 commits / 1016.579 ms → 40 commits / 551.746 ms；commit 减少 87.5%，耗时减少 45.725%。
- 验证：43 passed focused service + V3 Engine runtime/observability tests; ruff passed。
- 门禁：**未重跑 9,600 Tick；SLO 仍为 BLOCKED。**
- 边界：isolated SQLite persistence microbenchmark only; no new full Engine pressure run was performed after the patch, so it cannot pass/freeze SLO。

## 判定说明

- `PASS` 只代表完成了已验证的正式 20 日 BACKTEST；不表示 PAPER、Canary 或 LIVE 验收。
- `BLOCKED` 是数据/输入证据不足，绝不以合成单测或短窗口替代真实 20 日/交易时段证据。
- `EXECUTED_SYNTHETIC_NON_HISTORICAL` 是全持仓、合成 Tick 的本机压力基线；它只能冻结机器 SLO，不能升级真实历史或正式门禁。
- `EXECUTED_DIAGNOSTIC_NON_GATING` 仅用于定位量化延迟与写入边界；即使完成，也不得替代或通过全负载 SLO。
