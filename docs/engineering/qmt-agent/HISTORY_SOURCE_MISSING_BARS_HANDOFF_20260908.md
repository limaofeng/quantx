# QMT 历史分钟线偶发缺数：待解决问题交接

状态：**未解决，留待新任务继续定位**。记录日期：2026-09-08。

## 问题与影响

生产回读并发优化验收中，同一组 300 标的、2026-09-08 的 `1m` 数据，既有基准每个标的
241 条、合计 72,300 条；一次请求中 `300319.SZ` 被源端上报为空，接收、保存、回读均为
72,059 条，少 241 条。定向重下载及后续完整复测恢复，但这不代表根因已修复。

诊断结果的 `status=success` 只说明本次已上传数据完成入库和回读；此次
`summaries_match_reference=false`，**不属于合格的整批完整性验收**。源端少上传的数据无法
由“回读已上传键”这一检查发现。应区分合法无行情与异常空结果，不能简单把所有空标的判错，
也不能把再次下载成功当作永久修复。

## 已核实证据

所有请求均通过受管理的 Agent 行情链路执行，没有真实交易测试。

| 请求 | request_id | 接收/保存/回读 | 结果 |
| --- | --- | ---: | --- |
| 异常完整批次 batch2 | `9c257d92-e295-4f6e-ae11-06394a60bfd4` | 72,059 | 300 组摘要，其中 `300319.SZ` 空，基准不匹配 |
| 单标的定向重下载 | `a6032b2f-fd37-43e6-8d6e-8318ddd012a9` | 241 | 基准匹配 |
| 300 标的完整复测 | `9226c226-6e28-4e0b-b183-7d098da722cd` | 72,300 | 全部摘要匹配 |

既有基准请求为 `6c9283a8-b8c4-4665-ba83-c63b9e406b46`；其 `request_payload` 可用于恢复
完整标的列表，其 `ingestion_result` 可用于恢复逐标的摘要。以上 ID 可用于只读查询 PostgreSQL
`market_data_request`，避免依赖会轮转的日志。

异常请求的 Agent 日志中未发现 `cache_visibility_retry`。现有重试仅对缺失或长度为零的
DataFrame 生效，在显式下载后按 0.1、0.3、0.6、1.0 秒重读缓存。**非空占位行在后续被全部
过滤**是待验证假设，不是已确认根因；当时未保存原始 XTData 帧，不能从现有日志证明该假设。
同一标的在此前诊断中也出现过立即读取为空，历史经过见主报告中的缓存可见性修复记录。

## 代码与证据入口

- [broker.py](../../../apps/qmt-agent/src/quantx_qmt_agent/broker.py)：
  `_read_history_frames`、`_normalize_history_frames`、`_iter_market_data_records_unbounded`、
  `_is_empty_historical_kline_row`；检查返回帧、占位行过滤、最终摘要之间的关系。
- [xtdata_history_download.py](../../../apps/qmt-agent/src/quantx_qmt_agent/xtdata_history_download.py)：
  原生下载完成与本地缓存可读时机。
- [主诊断报告](../deployment/HISTORY_BACKFILL_PERFORMANCE_DIAGNOSIS_20260908.md)：
  13.3 节保留本次并发优化及失败样本，不能把两个问题混为同一根因。
- 本机忽略目录 `.runtime/`：`history-concurrent-stage-production-batch2.json`、
  `history-concurrent-stage-production-redownload_code.json`、
  `history-concurrent-stage-production-pipe1.json` 保存上述结果；
  `logs/qmt-agent.stderr.log` 保存当时 Agent 时序，日志可能轮转。
- `.runtime/diagnostics/history_concurrent_production_benchmark.py` 为当时验收脚本，
  必须换用新的幂等作用域才能创建新请求；复用旧作用域可能只返回历史结果。

本问题发现时回读并发实现为 `772638932`，验收记录为 `e7b07e8c5`；数据库 WAL 为 100 ms。
回读优化只改服务端校验调度，本次没有修改 Agent。没有证据表明是 Influx 少写或并发回读漏键。

## 新任务建议步骤与完成条件

1. 先核实当前生产状态与代码版本，读取上述请求及摘要。使用受管理 Agent，先单标的、再原分组
   做有界复现；不要单独启动 QMT Agent，也不要直接重放旧幂等请求当作新验收。
2. 在诊断中记录原始返回行数、有效行数、被过滤行数、时间范围、下载完成/缓存读取时间和重试
   次数。若需保存原始帧，限定异常标的、大小及保存位置，避免凭据和账户信息进入日志。
3. 区分空帧、非空占位帧、合法无数据、缓存迟到及过滤逻辑异常。先用可控输入验证候选方案，
   再修改业务代码；不得通过删校验、无界重试或直接补写历史基准掩盖问题。
4. 针对实际根因补充测试，保留原生下载串行、资源上限和合法空行情语义。QMT 相关验证使用
   `xtquant-demo`，服务端验证使用 `quantx` Conda 环境，遵守根目录 AGENTS.md。
5. 修复后以明确的新请求重复验证单标的及完整 300 标的样本。每个合格批次应回读 72,300 条，
   逐标的摘要与基准一致；保留所有失败样本。说明复现及重复次数，不以一次恢复声称彻底解决。

本交接仅记录待办；后续定位、代码修改和生产复验在新任务执行。
