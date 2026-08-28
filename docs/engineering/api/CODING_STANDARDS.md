# QuantX 编码规范

## 命名空间与依赖

- 只使用 `quantx_contracts`、`quantx_domain`、`quantx_application`、
  `quantx_infrastructure`、`quantx_api`、`quantx_engine`、
  `quantx_worker` 和 `quantx_qmt_agent` 命名空间。
- `quantx_domain` 必须保持纯净，不依赖数据库、文件、网络、FastAPI、
  Prefect 或 QMT。
- API、Engine 和 Worker 不得导入 `xtquant` 或 QMT Agent 源码。
- QMT Agent 的内部业务包只依赖 contracts；XTData/XTTrading 适配器留在
  Agent 应用边界内。

## 交易状态

- 策略只实现 `StrategyBase.step(StrategyInput)`，输出 `TradeIntent[]` 与
  `RuntimeStatePatch`。
- 真实可卖量、T+1、资金、涨跌停和整手约束由交易域和风控处理。
- 所有实盘入口先持久化 pending 订单与 `TradeCommand`。
- `command_ack` 只表示投递；订单和成交状态只能由 Agent 回报经 inbox 后
  由 Engine 收敛。
- 数据库是状态真源；Redis 只用于唤醒和实时广播。

## API 与前端契约

- GraphQL schema 与前端 operation 必须同轮更新。
- 前端使用生成的 TypedDocumentNode 和生成类型，不用 `as any` 绕过契约。
- codegen 只访问 Caddy 公共端点
  `http://127.0.0.1:8080/graphql`。

## 验证

```powershell
python -m ruff check apps packages tests
python -m pytest tests/ -m "not dangerous and not real_trading and not e2e"
$env:CODEGEN_GRAPHQL_ENDPOINT="http://127.0.0.1:8080/graphql"
npm run codegen
npm run check
npm run lint
npm run test:run
npm run build
```

真实交易测试默认禁止；个人 Dev 仅在显式 `ENV=testing` 安全门下运行。
