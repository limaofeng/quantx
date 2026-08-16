# 能力地图

字段级真源是 [v2 operation policy](/contracts/graphql-operation-policies.v2.json)。
下表用于选择领域入口，不能替代字段级检查。

| 领域 | 读权限 | 写权限 | 主要能力 |
| --- | --- | --- | --- |
| 组合 | `portfolio:read` | `portfolio:write` | 账户、持仓、资产、自选 |
| 市场 | `market:read` | `market:write` | 行情、K 线、财务、日历、研究数据 |
| 订单 | `orders:read` | `orders:write` | 委托、成交、退出计划、清仓 |
| 策略 | `strategy:read` | `strategy:write` | 策略、回测、做 T、打板助手 |
| AI Assistant | `assistant:read` | `assistant:write` | 对话、运行、审批与事件 |
| 系统运维 | `system-status:read` | `operations:write` | 健康、Prefect、运营告警 |
| Agent 管理 | `system-status:read` | `agent:manage` | 登记码、设备列表与撤销 |
| 系统配置 | `system-status:read` | `system-config:write` | 非敏感 AI Runtime 配置 |

`trade:approve` 是附加权限，不替代 `orders:write` 或 `strategy:write`。高风险确认与
实盘激活必须同时满足对应领域写权限和 `trade:approve`。

稳定性含义：

- `supported`：可由 policy 所列客户端依赖，并按版本记录演进。
- `experimental`：允许试用，但可能在小版本调整。
- `web-internal`：供当前 QuantX Web 使用，不向第三方承诺兼容。
