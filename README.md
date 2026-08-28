# QuantX

QuantX 是面向 A 股策略研究、回测和本机 QMT 执行的 Monorepo。系统采用
“统一启动与监管、独立进程运行”的结构：Caddy 是唯一公开入口，API、
Engine、Prefect Worker 和 QMT Agent 各自拥有清晰的生命周期。

## 工作区

```text
apps/
  api/          FastAPI、GraphQL、认证与 Agent WebSocket Hub
  docs/         VitePress 原生客户端文档与发布契约
  web/          Vite + React
  engine/       策略运行、条件清仓、全局做 T 与回报收敛
  worker/       Prefect flows、tasks 与部署入口
  qmt-agent/    XTData/XTTrading、本地保护与回报采集
packages/
  contracts/    Agent 协议、DTO 与公共枚举
  domain/       纯交易域、策略、风控、仓位与回测 broker
  application/  用例、端口接口与状态推进
  infrastructure/
                ORM、Repository、数据库适配与持久化消息箱
ops/            Windows Dev Caddy 与统一运维脚本
tests/          按架构边界组织的 Python 测试
```

## 开发运行

Windows 本机从仓库根目录使用唯一入口：

```powershell
.\ops\quantx.ps1 up -Environment dev -Profile web
.\ops\quantx.ps1 status
.\ops\quantx.ps1 logs
.\ops\quantx.ps1 down
```

普通开发 `up`（包括未显式指定模式的 `-Profile web`）统一提升为
`full/live`：启动 Caddy、API、Engine、Vite、VitePress 和 Prefect Worker，并在
本机登记预检通过时启动 QMT Agent；Prefect Server 由外部管理。只有操作者明确
要求纯行情运行时才使用 `-Mode data-only`，任何失败都不得把 live 静默改写为
data-only。QMT 登记或运行时不可用时，统一入口保持 `full/live`，先关闭全部实盘
能力门并清空实盘账户允许列表，再以显式 `DEGRADED / BLOCKED` 状态启动非 QMT
服务；已持久化历史行情的回测继续可用，但该状态绝不代表 QMT `ready`。
开发 Caddy 在所有本机 IPv4 接口的 `8080` 端口提供统一入口。本机可访问
`http://127.0.0.1:8080`，局域网设备统一使用 `http://192.168.5.6:8080`；
API 自身仍只监听 `127.0.0.1:18081`。

开发者文档通过统一入口
[`http://127.0.0.1:8080/docs/`](http://127.0.0.1:8080/docs/) 提供，
不直接访问 API 内部端口。

首次准备 Caddy：

```powershell
.\ops\quantx.ps1 bootstrap
```

PostgreSQL、InfluxDB 和 Redis 是外部持久化服务；脚本只检查它们，不会
安装、启动或停止它们。端口冲突时，`up` 只报告占用者，绝不会终止未受
QuantX 状态文件跟踪的进程。

项目不再维护 production、WinSW、Kubernetes、release 安装或 macOS 运行路径；
个人使用只通过当前 Windows 工作区的 `dev` 启动器运行。

## 验证

```powershell
python -m ruff check apps packages tests
python -m pytest -m "not dangerous and not real_trading and not e2e"
npm run check
npm run lint
npm run test:run
npm run build
```

GraphQL schema 或前端查询变化后，先通过运行中的 Caddy 公共入口执行
`npm run codegen`。普通开发运行保持 `full/live` 连接；是否能够自动下单仍由
账户白名单、快照、对账、灰度阶段、受控窗口与 kill switch 独立决定。自动测试
继续使用 fake/simulator broker，禁止真实交易。

架构、进程边界、Agent 登记和部署细节参见
[工程文档](docs/engineering/README.md)；项目约束参见 [AGENTS.md](AGENTS.md)。
