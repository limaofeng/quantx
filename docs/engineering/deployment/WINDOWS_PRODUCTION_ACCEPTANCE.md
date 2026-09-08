# Windows 生产切换验收（2026-09-08）

本次验收范围为 Windows 运行端；macOS 主机由开发机按
[macOS 开发环境验收](MACOS_DEV_ACCEPTANCE.md) 单独执行。普通验收不发送真实订单。

## 配置与运行

- 生产配置为 `apps/api/.env.production`，`ENV=production`；共享 `.env` 已移出环境地址和密钥。
- `quantx` Conda 环境运行服务与验证工具，QMT 使用独立 `xtquant-demo`；不使用 `.venv`。
- 生产数据原地保留，PostgreSQL、Redis、InfluxDB、Prefect 由外部管理。
- Windows 入口为 `ops/quantx.ps1`，生产使用 `production/full/live`。
- Web、Docs 已构建，由生产 Caddy 静态提供；Monitor 保持独立生命周期。

```powershell
.\ops\quantx.ps1 status -Environment production
.\ops\quantx.ps1 doctor -Environment production
.\ops\quantx.ps1 verify -Environment production
```

## 已完成的验证

- 受影响环境门禁、启动器、既有 paper 链路、历史传输、研究数据和生命周期测试通过。
- Conda 环境下启动器测试 38 项、数据与研究相关测试 45 项通过；重连修复相关测试 8 项通过。
- Web 类型检查、环境标签测试、生产构建及体积预算通过；Docs 构建通过。
- Caddy 原生配置验证通过，生产 Web、Docs 与 API 存活接口返回 200。
- 最终 `/health/ready` 返回 200，API、数据库、Engine、Worker、QMT、AI Runtime、行情及 Prefect 全部 ready；网关与 GraphQL schema 验证通过。
- 唯一 Engine、唯一 Agent 受管进程树；账户数量为一、协议 1.3、对账已收敛、最终账户快照约 26 秒。Vite/VitePress 端口没有监听。
- 生产迁移达到 `20260908_0059`，schema check 没有缺失表或字段。
- 完整备份恢复验证 `eb928a56818449d4` 通过。迁移前另有完整备份 `20260908T043556Z`。
- 历史导出重复提交得到相同任务 ID；从已有持久化数据重建压缩分片，下载 SHA-256 校验通过，包含 5,166 条 Tick 和一条覆盖摘要。
- 未授权行情请求返回 403；专用行情凭证不用于登录、账户或 Agent 认证。
- 专用行情凭证访问 `/auth/session` 返回 401；非法行情订阅被拒绝。
- 快照连接关闭后立即重连成功；两次快照的序列与源时间一致，没有因重连刷新行情时间。
- 历史任务在生产重启后仍为 READY，重新下载分片的 SHA-256 校验一致。
- 生产定时备份任务为 `QuantX-Production-Daily-Backup`。

## 现场证据

证据位于本机忽略目录，包含运维路径，不随代码复制到开发机：

| 内容 | 路径 |
| --- | --- |
| 迁移与 schema check | `.runtime/production-migrate.log` |
| 最终启动及门禁 | `.runtime/production-up.log` |
| 运行状态 | `.runtime/production-status.log` |
| 外部依赖检查 | `.runtime/production-doctor.log` |
| 行情与历史接口验收 | `.runtime/production-bridge-acceptance.json` |
| 历史下载与数据版本 | `.runtime/production-history-smoke-result.json` |
| 完整恢复验证 | `.runtime/restore-verifications/eb928a56818449d4/status.json` |

运行状态必须确认唯一 Engine 和 Agent、唯一账户、实盘门禁开启、协议匹配、对账就绪，
账户快照小于 90 秒。行情需要保留源时间；午间休市没有增量时不能把重连收到的快照
当作新行情。跨机持续增量、慢消费、网络中断恢复以及 macOS 本地导入查询，按开发机
清单在交易时段验收；本机 HTTP 与分片校验不能替代这些跨机验收。
