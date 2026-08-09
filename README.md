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
ops/            Caddy、WinSW 与统一运维脚本
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

`web` 启动 Caddy、API、Engine、Vite 和 VitePress；完成设备登记后可使用 `full`
额外启动 Prefect Server、Worker 和默认 `data-only` 的 QMT Agent。
开发 Caddy 在所有本机 IPv4 接口的 `8080` 端口提供统一入口。本机可访问
`http://127.0.0.1:8080`，局域网设备使用 `http://<开发机局域网 IP>:8080`；
API 自身仍只监听 `127.0.0.1:18081`。

开发者文档通过统一入口
[`http://127.0.0.1:8080/docs/`](http://127.0.0.1:8080/docs/) 提供，
不直接访问 API 内部端口。

首次准备固定版本的 Caddy 和 WinSW：

```powershell
.\ops\quantx.ps1 bootstrap
```

PostgreSQL、InfluxDB 和 Redis 是外部持久化服务；脚本只检查它们，不会
安装、启动或停止它们。端口冲突时，`up` 只报告占用者，绝不会终止未受
QuantX 状态文件跟踪的进程。

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
`npm run codegen`。真实交易默认关闭，普通开发和自动测试使用
fake/simulator broker。

架构、进程边界、Agent 登记和部署细节参见
[工程文档](docs/engineering/README.md)；项目约束参见 [AGENTS.md](AGENTS.md)。
