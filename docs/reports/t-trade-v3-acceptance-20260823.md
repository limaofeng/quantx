# 做 T V3 历史回放与全持仓压力验收

- 生成时间：`2026-08-24T02:54:10.397643+08:00`
- 正式 20 交易日门禁：**BLOCKED**
- 因果口径：D-1 账户日结快照；按 SH 真实交易日；所有正持仓均纳入，绝不自动剔除。
- Tick 口径：Engine 同款严格连续交易时段检查；正式/压力执行前另以严格 source-identity keyset 分页验证。
- 执行边界：仅隔离 `BACKTEST`；本工具不启动 QMT、不发送 PAPER/LIVE 指令、不补数。
- 完整机读证据：[JSON](t-trade-v3-acceptance-20260823.json)

## 20 个交易日严格因果回放

**BLOCKED**：没有任一 D-1 快照形成全持仓 20/20 Tick 完整窗口；因此没有启动正式回放。

| D-1 快照 | 持仓 | 窗口 | 完整 instrument-day | 共同完整日 | 连续共同前缀 | 门禁阻塞 |
| --- | ---: | --- | ---: | --- | --- | --- |
| 2026-06-01 | 8 | 2026-06-02~2026-06-30 | 46/160 | 2026-06-02,2026-06-04,2026-06-05 | 2026-06-02 | ALL_HOLDINGS_TICK_COVERAGE_INCOMPLETE,ABNORMAL_DAY_EVIDENCE_NOT_DECLARED |
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

**PASS**：0 个 instrument-day 严格 source-identity keyset 检查失败；完整逐日证据在 JSON。
- 失败原因：-

## 本轮数据、恢复与上线门禁补充

- 历史 Tick 传输：received/saved/verified=212291/212291/212291；状态=COMPLETED。
- 严格覆盖：46/160 instrument-day；source identity 严格 keyset 失败=0。该覆盖不足以启动正式 20 日回放。
- 正式因果回放：0/20；stage=SHADOW；未以合成压力替代历史回放。
- restore-verify：PASSED；备份 schema 20260822_0028_qmt_agent_handover 在隔离 scratch DB 前向升级到 20260823_0031_watchlist_groups 并通过；production database restored=False。
- 上线门禁：PAPER BLOCKED（连续交易日 0/5，完成候选生命周期 0/20）；CANARY=NOT_EXECUTED；LIVE=NOT_EXECUTED；operator_review=False。

## 9,600 Tick 全持仓合成压力尝试

**EXECUTED_SYNTHETIC_NON_HISTORICAL**：当前生产 Engine 路径已完成固定全持仓全量合成负载；该结果只冻结本机合成 SLO，绝不替代 20 日历史回放。
- runId=`e3231ca1-43a3-43cb-bbce-7fc7bab775a5`；请求区间=2026-06-04T09:30:00~2026-06-05T15:00:00；处理至=2026-06-05T15:00:00；进度=100.0%；wall=4702.04363s。
- 请求/有效处理：9600 / 9472 engine ticks；采样 engine tick=9472，checkpoint=9473。
- fixture：`SYNTHETIC_NON_HISTORICAL`，sha256=c31da4d7c79f729335d80a25945cc56043ac0e39625a5b71d4691c40394d6603，9600 ticks，8 instruments，合法交易时段=Shanghai continuous sessions only: 09:30-11:30 and 13:00-15:00。
- deadline：21600.0s；timed_out=False；隔离 BACKTEST cancellation=None
- Tick 口径核对：请求=9600，实际评估=9472，策略过滤=128；16 个 instrument-day 每个过滤 8 条（13:00 <= local_time < 14:57，14:57:10..14:59:59），不是丢失或未处理 Tick。
- 历史取消尝试保留在 JSON 的 `full_pressure_attempt`，不作为当前 SLO 结果。

## 全持仓合成压力基线 / 首次本机 SLO

**EXECUTED_SYNTHETIC_NON_HISTORICAL**：此结果为合成负载，不是历史回放，且不替代 20 交易日门禁。
- fixture：sha256=c31da4d7c79f729335d80a25945cc56043ac0e39625a5b71d4691c40394d6603, 9600 ticks，8 instruments，合法交易时段=Shanghai continuous sessions only: 09:30-11:30 and 13:00-15:00。
- Engine tick 延迟（ms）：p50=155.8777, p95=3143.521485, p99=6706.478538
- 策略评估延迟（ms）：p50=4.8887, p95=6.99358, p99=10.471892
- 吞吐：2.014443 engine ticks/s（9472 ticks）
- CAS：0 conflicts / 9473 checkpoint attempts，rate=0.0
- DB 写活动：{'call_sites': {'commit': {'backtest_repository.py:create_backtest': 1, 'backtest_repository.py:update_backtest_start': 1, 'backtest_repository.py:update_backtest_status': 1, 'runtime_state_manager.py:save_snapshot': 9503, 'strategy_decision_trace_repository.py:create_trace': 9473, 'strategy_performance_sample_repository.py:bulk_create': 10, 'strategy_performance_sample_repository.py:delete_by_backtest': 1, 'strategy_run_repository.py:create_strategy_run': 1, 'strategy_run_repository.py:update_run': 3, 't_trade_opportunity_intelligence_repository.py:_append': 1072, 't_trade_opportunity_runtime_service.py:_persist_pending_diagnostics': 1050, 't_trade_replay_projection_service.py:update': 1186}, 'dml_execute': {'strategy_performance_sample_repository.py:delete_by_backtest': 1, 'strategy_run_state_repository.py:replace_positions_snapshot': 1, 'strategy_run_state_repository.py:upsert_state': 9504}, 'flush': {'strategy_run_state_repository.py:upsert_state': 1, 't_trade_opportunity_intelligence_repository.py:_append': 8400}}, 'commit_calls': 22302, 'dml_execute_calls': 9506, 'flush_calls': 8401, 'latency': {'commit': {'dropped_samples': 0, 'max': 21832.5147, 'p50': 11.70915, 'p95': 290.99876, 'p99': 1507.059212, 'quantiles_exact': True, 'sample_count': 22302, 'unit': 'milliseconds'}, 'dml_execute': {'dropped_samples': 0, 'max': 8180.792, 'p50': 46.78555, 'p95': 2568.266025, 'p99': 5410.327295, 'quantiles_exact': True, 'sample_count': 9506, 'unit': 'milliseconds'}, 'flush': {'dropped_samples': 0, 'max': 828.7357, 'p50': 1.1274, 'p95': 157.0191, 'p99': 172.4986, 'quantiles_exact': True, 'sample_count': 8401, 'unit': 'milliseconds'}}, 'runtime_state': {'latency': {'position_replace_snapshot': {'dropped_samples': 0, 'max': 671.2657, 'p50': 671.2657, 'p95': 671.2657, 'p99': 671.2657, 'quantiles_exact': True, 'sample_count': 1, 'unit': 'milliseconds'}, 'position_update_existing_snapshot': {'dropped_samples': 0, 'max': None, 'p50': None, 'p95': None, 'p99': None, 'quantiles_exact': True, 'sample_count': 0, 'unit': 'milliseconds'}, 'state_upsert': {'dropped_samples': 0, 'max': 8181.1541, 'p50': 47.1906, 'p95': 2569.25333, 'p99': 5411.144282, 'quantiles_exact': True, 'sample_count': 9503, 'unit': 'milliseconds'}}, 'position_replace_snapshot_calls': 1, 'position_rows_submitted': 8, 'position_snapshot_calls': 1, 'position_update_existing_snapshot_calls': 0, 'snapshot_save_calls': 9852, 'snapshot_save_failures': 0, 'state_upsert_attempts': 9503, 'state_upsert_rejected': 0}}；评估：{'by_record_kind': {'COALESCED_DIAGNOSTIC': {'logical_events': 8400, 'rows': 8400}, 'MATERIAL': {'logical_events': 1072, 'rows': 1072}}, 'diagnostic_logical_events': 8400, 'diagnostic_merge_ratio': 0.0, 'diagnostic_rows': 8400, 'material_rows': 1072}
- 生产路径覆盖边界：{'post_cas_evaluation_materialization': True, 'runtime_state_checkpoint': True, 'strategy_evaluator': True, 'strategy_executor_global_source_order': True}。
- 冻结 SLO：{'limits': {'cas_conflict_rate_max': 0.001, 'database_commit_calls_max': 44604, 'engine_tick_p50_ms_max': 233.81655, 'engine_tick_p95_ms_max': 4715.282227, 'engine_tick_p99_ms_max': 10059.717807, 'engine_ticks_per_second_min': 1.611554}, 'not_a_formal_replay_gate': True, 'policy': 'first local synthetic baseline; latency upper bounds = observed × 1.5; throughput floor = observed × 0.8; values require re-baselining on hardware, runtime, or workload change', 'status': 'FROZEN_FIRST_LOCAL_SYNTHETIC_BASELINE'}
- 隔离执行证据：runId=`e3231ca1-43a3-43cb-bbce-7fc7bab775a5`；terminal=TERMINAL；sealed durable RuntimeState=True；QMT=False，PAPER/LIVE command=False.
- Durable RuntimeState latency（ms）：checkpoint p50/p95/p99=134.3787/2728.24794/5765.6164；snapshot p50/p95/p99=92.75935/2969.290545/5733.998341。
- Position DB writes：replace=1，same-code update=0，rows=8；每 Tick 的 state CAS/upsert 仍保留（attempts=9503）。
- 性能判读（仅诊断）：strategy p95=6.99358ms，而 checkpoint/snapshot p95=2728.24794/2969.290545ms；长尾位于外部数据库持久化边界。固定 9,600 Tick 已完成；本机合成 SLO 仅按本机和本工作负载冻结，不改变正式 20 日历史门禁。

## 后续性能修复微基准（非压力验收）

**MICROBENCHMARK_NON_GATING**：packages/infrastructure/src/quantx_infrastructure/services/t_trade_opportunity_runtime_service.py: batch already-closed diagnostic windows in one owned session/atomic commit。
- 正确性微测：8 closed diagnostics preserve 8 rows with 1 session / 1 commit；batch failure rolls back and requeues every closed window (no partial write)。
- SQLite 内存微基准（320 rows / 8 streams）：320 commits / 1016.579 ms → 40 commits / 551.746 ms；commit 减少 87.5%，耗时减少 45.725%。
- 验证：43 passed focused service + V3 Engine runtime/observability tests; ruff passed。
- 门禁：**已使用性能补丁后的完整 9,600 Tick 运行；本机合成 SLO 为 FROZEN_FIRST_LOCAL_SYNTHETIC_BASELINE。**
- 边界：completed current-production-path 9,600-Tick synthetic baseline; it freezes only this machine's local synthetic SLO and does not replace the formal 20-day causal-replay gate。

## 判定说明

- `PASS` 只代表完成了已验证的正式 20 日 BACKTEST；不表示 PAPER、Canary 或 LIVE 验收。
- `BLOCKED` 是数据/输入证据不足，绝不以合成单测或短窗口替代真实 20 日/交易时段证据。
- `EXECUTED_SYNTHETIC_NON_HISTORICAL` 是全持仓、合成 Tick 的本机压力基线；它只能冻结机器 SLO，不能升级真实历史或正式门禁。
- `EXECUTED_DIAGNOSTIC_NON_GATING` 仅用于定位量化延迟与写入边界；即使完成，也不得替代或通过全负载 SLO。
