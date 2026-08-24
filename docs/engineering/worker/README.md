# QuantX Prefect Worker

`apps/worker` 保存 flows、tasks 和 `prefect.yaml`，由独立 Prefect Worker
运行。Worker 不导入 QMT SDK；需要本地行情时创建持久化
`market_data_request`，等待 QMT Agent 分批上传并由 Worker 收敛。

`full` profile 固定按外部 Prefect Server 健康检查、确认
`quantx-pool` 已存在、部署全部 flow、启动 Worker 的顺序运行。
每个 deployment 在 `prefect.yaml` 中显式绑定该 pool，避免 Prefect CLI
版本或本机默认配置改变执行目标。

Prefect API 由 `PREFECT_API_URL` 指定，默认使用
`http://192.168.101.4:30420/api`；池名由 `PREFECT_WORKER_POOL` 指定，默认
`quantx-pool`。Windows 运行时固定使用 UTF-8，并将 Worker 的 Prefect home
放在仓库忽略提交的 `.runtime/prefect`，隔离本机全局配置。

历史 `tick` 分块收敛时，Worker 要求每条记录带有 0–999 之间的
整数 `tick_ordinal`，并跨分块校验
`(code, period, time, tick_ordinal)` 唯一。同一标的、同一原始
毫秒内的序号必须从 0 连续递增。Worker 将上传的原始毫秒 `time`
保存为普通字段 `source_time_ms`，再把 InfluxDB 存储时间编码为
`原始时间 + tick_ordinal 微秒`。因为来源精度为毫秒，0–999 微秒偏移
不会与另一条原始时间戳冲突；`source_time_ms` 和 `tick_ordinal` 可完整
还原源时间与同毫秒顺序，不应把存储时间中的微秒解读为交易所时间
精度。这里的“顺序”是稳定字段推导出的确定性代理顺序，不是交易所声明的
同毫秒先后。非 `tick` 周期不携带 `tick_ordinal`，仍以原始时间戳作为唯一键。
历史与当天热缓存合并时按 `source_time_ms` 和稳定快照身份处理：跨来源的相同
快照只保留历史记录，同一来源中的多个 occurrence 全部保留；查询上界落在某个
源毫秒时会覆盖该毫秒的全部 0–999 微秒存储槽，避免边界漏读。
稳定身份只采用历史与实时共同拥有的成交、累计量和盘口业务字段，并按入库精度
规范化数值；`tickvol`、`pvolume`、状态、动态估值字段及实时独有的涨跌停元数据
不参与跨来源判重，避免编码差异把同一快照误判为两条。

工作日 15:35 的 `position-sync` 是“收盘时仍持有标的”的完整 Tick 归档，
不等同于盘中实时缓存。Flow 只接受 90 秒内、无错误且数量闭合的唯一券商持仓
全量快照，冻结其中 `volume > 0` 的代码后向同一个新鲜 Agent 请求目标交易日的
`tick`。系统目前只保存最新持仓投影，因此隔日补跑会 fail-closed，不能拿补跑时
现仓冒充目标日收盘持仓；若需要交易审计，还应另行冻结“日初持仓、日末持仓、
当日委托和成交标的”的并集。

工作日 15:50 的 `t-trade-instrument-profile` 在 Tick 归档完成后，为当前做 T
策略运行中的标的生成账户无关、不可变的 D 日画像，供 D+1 机会引擎读取。
未传 `stock_list` 时，Flow 只解析关联做 T 全局配置 `enabled=true` 且
`StrategyRun.status=RUNNING` 的运行标的；传入 `stock_list` 则按显式标的补算。
画像只选取截止 `as_of` 的最近 20 个完整交易日；不足 10 日、上下半场覆盖不足、
累计成交额回退或盘口覆盖不足时不生成画像，策略因此保持 `INSUFFICIENT`。
画像阈值、分时成交基线、数据清单和 SHA-256 指纹写入 PostgreSQL；追加
`as_of` 之后的 Tick 不得改变既有画像。Flow 可用 `stock_list` 和 `as_of_date`
显式补算，但 point-in-time 查询仍严格选择评估交易日之前的最新兼容版本。

历史行情传输采用唯一的 `bar_summary` 契约：每个请求
`code × period` 必须恰好有一条摘要，声明行数、最小/最大源时间、规范键 SHA-256
和显式无数据原因。Worker 第一遍在任何 InfluxDB 写入前校验请求集合、日期、
周期、流顺序、摘要、分片 SHA/记录数及压缩和解压总量；全部通过后第二遍按
最多 2,000 行或 8 MiB 分批写入。只有全部批次被接受，逐标的审计才和
`COMPLETED` 在 PostgreSQL 原子保存；复用幂等请求时必须返回同一份审计。
临时 InfluxDB 故障只释放处理租约回 `UPLOADED`，调用方超时也不终结业务请求。
做 T 严格 Tick 回放不能仅凭 Tick 的零行摘要推断停牌或休市：只有同一
`code × trading_date` 同时拥有已完成、持久化验证过、精确单日的 `tick` 与
`1d` `XT_DATA_NO_ROWS` 审计（两者的 day coverage 和 summary 均为零），才会把
缺失 Tick 标为 `CONFIRMED_EMPTY`。任意已完成且验证过的、同 `code × day` 的
`1d` day coverage 非零或格式异常（包括多日请求）都会否决；无按日键的 summary
仅在精确单日请求中以非零作为反证。缺少 `1d` 正证据、多日正证或查询失败都
fail-closed；Engine 回放门禁与正式验收审计共用这一个持久化查询结论。
每分钟的 `market-data-ingestion-recovery` 还会把超过五分钟没有更新的
`DELIVERED` / `RECEIVING` Agent 投递租约原子退回 `QUEUED`；这只恢复持久化
请求状态，不调用 QMT 或修改任何交易状态。随后在线 Agent 以原请求 ID 幂等续传，
已上传或处理中数据仍只由摄取租约收敛。
`ticks` 所在 InfluxDB database 必须按“完整历史”容量规划为无限 retention，并有
独立备份与容量告警；API gzip staging 不是第二份长期归档，成功后立即清理，失败
分片只保留 24 小时以支持受控重放。

全市场公司行为数据通过独立、可恢复且强制 data-only 的
[QMT 复权因子回填](QMT复权因子回填.md) 同步。该文档同时记录 QMT `dr`
方向实测、现有累计复权公式的风险和 `close/pre_close` 回退方案。
