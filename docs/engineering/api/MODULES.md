# QuantX 模块边界

| 模块 | 拥有的职责 | 禁止拥有的职责 |
| --- | --- | --- |
| `apps/api` | HTTP、GraphQL、认证、Agent Hub、订阅桥接 | Engine、Prefect、QMT 生命周期 |
| `apps/engine` | 策略、风控编排、清仓/做 T、回报收敛 | HTTP 入口、QMT SDK |
| `apps/worker` | Prefect flows、tasks、deployment | 直接调用 QMT |
| `apps/qmt-agent` | XTData/XTTrading、本地保护、回报采集 | 服务端 ORM、Repository、策略 |
| `packages/contracts` | 协议、DTO、版本、枚举 | 基础设施实现 |
| `packages/domain` | 策略、风控、仓位、回测 broker | I/O 和框架 |
| `packages/application` | 用例、端口、命令路由、状态推进 | 具体数据库/QMT 实现 |
| `packages/infrastructure` | ORM、Repository、数据库、消息箱 | API/Engine 运行时所有权 |

跨进程可靠通信以 PostgreSQL outbox/inbox 为真源；Redis 不是持久消息总线。
