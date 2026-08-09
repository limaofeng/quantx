# 服务切换结果

QuantX 已从 API 单体内的服务开关切换为独立进程监管：

- Caddy 是唯一公开入口。
- API、Engine、Prefect Server、Worker 与 QMT Agent 独立运行。
- PostgreSQL、InfluxDB、Redis 只检查，不由 QuantX 启停。
- API 不创建或终止业务子进程。
- 数据库消息箱保证组件独立重启后的恢复。

当前操作以 [../deployment/README.md](../deployment/README.md) 为准。
