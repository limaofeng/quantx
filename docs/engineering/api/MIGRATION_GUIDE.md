# Monorepo 迁移说明

旧单体迁移已经切换到以下稳定边界：

| 旧职责 | 当前位置 |
| --- | --- |
| HTTP、GraphQL、认证 | `apps/api` |
| 策略运行与订单收敛 | `apps/engine` |
| Prefect flows/tasks | `apps/worker` |
| XTData/XTTrading | `apps/qmt-agent` |
| 纯交易域 | `packages/domain` |
| 用例与端口 | `packages/application` |
| ORM、Repository、消息箱 | `packages/infrastructure` |

旧目录、根启动脚本和依赖工作目录的顶层导入均不再兼容。数据库迁移必须
前向兼容且非破坏性，不得清空订单、成交、策略状态或 bucket 数据。

历史服务切换资料保存在
[archive/legacy-monolith](archive/legacy-monolith/README.md)。
