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

全市场公司行为数据通过独立、可恢复且强制 data-only 的
[QMT 复权因子回填](QMT复权因子回填.md) 同步。该文档同时记录 QMT `dr`
方向实测、现有累计复权公式的风险和 `close/pre_close` 回退方案。
