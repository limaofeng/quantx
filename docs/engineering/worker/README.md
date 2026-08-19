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

全市场公司行为数据通过独立、可恢复且强制 data-only 的
[QMT 复权因子回填](QMT复权因子回填.md) 同步。该文档同时记录 QMT `dr`
方向实测、现有累计复权公式的风险和 `close/pre_close` 回退方案。
