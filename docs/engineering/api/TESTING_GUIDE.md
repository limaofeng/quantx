# QuantX 测试与验收指南

所有命令从仓库根目录运行。默认禁止真实交易 E2E。

## 测试布局

```text
tests/
  contracts/
  domain/
  application/
  infrastructure/
  api/
  engine/
  worker/
  qmt_agent/
  integration/
```

前端测试保留在 `apps/web/src/__tests__` 及各 feature 的测试文件中。

## Python 安全验证

```powershell
$env:ENV="testing"
$env:ENABLE_REAL_TRADING="false"
$env:QMT_REAL_TRADING_ENABLED="false"
python -m ruff check apps packages tests
python -m pytest tests/ -m "not dangerous and not real_trading and not e2e"
```

可按组件缩小范围：

```powershell
python -m pytest tests/contracts tests/domain tests/application
python -m pytest tests/api
python -m pytest tests/engine
python -m pytest tests/worker
python -m pytest tests/qmt_agent
python -m pytest tests/integration/test_agent_trade_pipeline.py
```

根测试覆盖依赖边界、协议序列化、设备凭证、幂等、过期命令、订单状态、
行情分批、Prefect deployment 和运维契约。

## GraphQL 与前端

先启动 `web` profile，再通过 Caddy 执行：

```powershell
$env:CODEGEN_GRAPHQL_ENDPOINT="http://127.0.0.1:8080/graphql"
npm run codegen
npm run check
npm run lint
npm run test:run
npm run build
```

codegen 不允许直接访问 18081，也不能用 `as any` 回避生成类型。

## 运行态冒烟

```powershell
.\ops\quantx.ps1 up -Environment dev -Profile web
.\ops\quantx.ps1 status
Invoke-RestMethod http://127.0.0.1:8080/health/components
.\ops\quantx.ps1 down
```

完成设备登记后再验收 `full`。启动顺序必须是 Prefect health、process pool、
全部 deployment、Worker、data-only/paper QMT Agent；最终
`/health/ready` 返回 200。

## 真实交易硬门禁

真实交易不属于常规验收。只有同时满足以下条件才允许显式启动 live Agent：

1. `ENV=testing`（危险测试）或 `ENV=production`（受控灰度）
2. `ENABLE_REAL_TRADING=true`
3. `QMT_REAL_TRADING_ENABLED=true`
4. `T_TRADE_LIVE_ENABLED=true`（production 必需）
5. Agent 与服务端账户白名单均包含目标账户
6. 账户通过灰度、快照、对账、Agent READY 和策略授权门禁

production 默认仍拒绝，只有完整门禁通过才允许 Canary/Live。
`up -Environment dev -Profile full` 只允许 `data-only` 或 `paper`。
CI 始终设置所有真实交易开关为 false。

## 验收原则

- 不能以 skipped 的可选 MCP 测试证明 MCP 已部署。
- 不能以 `command_ack` 证明订单成交。
- 不运行真实下单 E2E。
- 端口冲突测试不得杀死未受状态文件跟踪的进程。
- 数据库迁移必须保留现有订单、成交、策略和 bucket 数据。
