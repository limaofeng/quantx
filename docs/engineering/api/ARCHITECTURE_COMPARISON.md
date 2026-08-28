# 架构切换对比

| 维度 | 旧单体 | 当前 Monorepo |
| --- | --- | --- |
| 启动 | 前后端脚本分散启动 | `ops/quantx.ps1` 统一监管 |
| 入口 | 多个公开端口 | 开发 Caddy `0.0.0.0:8080`，内部服务保持 loopback |
| 策略 | API 进程内 | 独立 Engine + 数据库租约 |
| Prefect | API 管理子进程 | 独立 Server/Worker |
| QMT | 服务端直连 | 出站 QMT Agent |
| 可靠消息 | 内存/调用链 | PostgreSQL outbox/inbox |
| Redis | 可能承担临时状态 | 仅唤醒、广播和缓存 |
| Python 导入 | 依赖工作目录的顶层包 | `quantx_*` 命名空间 |
| 部署监管 | 批处理脚本 | 开发统一启动；生产 Kubernetes 独立工作负载 |
| 恢复 | 组件耦合重启 | 独立重启并从数据库恢复 |

旧单体资料保存在
[archive/legacy-monolith](archive/legacy-monolith/README.md)，不得作为当前
运行指南。
