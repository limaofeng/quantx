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

`e2e` 标记默认由根测试配置跳过。只有在已确认外部服务、数据写入范围和风险后，
才显式传入 `--quantx-run-e2e`（或设置 `QUANTX_RUN_E2E=true`）；该开关本身不
替代真实交易所需的环境、账户白名单与 QMT 实盘开关。

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
全部 deployment、Worker、QMT Agent；最终 `/health/ready` 返回 200。开发
`full` 默认使用 `live` Agent 读取真实账户，但账户仍保持 `SHADOW` 准备阶段，
不会因此取得自动下单权限；纯行情验收显式使用 `-Mode data-only`。
未登记时可验收受支持的降级 `full/live`：启动命令成功，非 QMT 进程保持运行，
`status` 与 `/health/components` 明确返回 QMT/行情 `BLOCKED`、实盘能力关闭，
而 `/health/ready` 保持非就绪。该场景只验收非 QMT 功能及已持久化历史回放，
不得当作完整实盘或纯行情验收。

## 真实交易硬门禁

真实交易不属于常规验收。只有同时满足以下条件才允许显式启动 live Agent：

1. `ENV=testing`（危险测试）或 `ENV=production`（受控灰度）
2. `ENABLE_REAL_TRADING=true`
3. `QMT_REAL_TRADING_ENABLED=true`
4. `T_TRADE_LIVE_ENABLED=true`（production 必需）
5. Agent 与服务端账户白名单均包含目标账户
6. 账户通过灰度、快照、对账、Agent READY 和策略授权门禁

production 默认仍拒绝，只有完整门禁通过才允许 Canary/Live。开发 `full`
启动 live Agent 仅用于账户观察和手工交易共存，账户级自动命令仍由
`SHADOW -> CANARY -> LIVE` 门禁独立控制。CI 始终设置所有真实交易开关为
false。

## 验收原则

- 验收分成“服务健康”“准备阶段”“自动交易授权”三层，不以自动交易
  `READY` 代替前两层。
- `SHADOW` 准备验收允许 QMT 手工交易，但必须证明外部委托/成交已分类、完整
  快照新鲜、当前死信已闭环且账户事实可持续收敛。
- `CANARY / LIVE` 自动交易验收必须使用无外部活动的账户实盘窗口；准备阶段显示
  `PREPARING` 是预期结果，不是服务故障。
- `BLOCKED` 只表示准备链路本身不安全；备份缺失、实盘开关未开或当前存在
  手工活动只阻止自动授权，不应把健康的观察链路显示成故障。

- 不能以 skipped 的可选 MCP 测试证明 MCP 已部署。
- 不能以 `command_ack` 证明订单成交。
- 不运行真实下单 E2E。
- 端口冲突测试不得杀死未受状态文件跟踪的进程。
- 数据库迁移必须保留现有订单、成交、策略和 bucket 数据。
