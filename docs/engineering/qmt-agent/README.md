# QuantX QMT Agent

`apps/qmt-agent` 是唯一允许导入 `xtquant` 的应用。它只建立出站 WebSocket，
不开放局域网监听端口，也不导入服务端 ORM、Repository 或策略实现。

设备密钥保存在 Windows Credential Manager，服务端只保存哈希。运行模式为
`data-only`、`paper` 或 `live`。`live` 只允许在 `ENV=testing` 或
`ENV=production`，且同时显式设置 `ENABLE_REAL_TRADING=true`、
`QMT_REAL_TRADING_ENABLED=true` 和 `QMT_ACCOUNT_WHITELIST` 时启动。
production 还必须设置 `T_TRADE_LIVE_ENABLED=true`；服务端账户白名单、
灰度阶段、快照、对账与策略授权仍会独立阻断不合规命令。`full` profile
不会隐式开启真实交易。开发环境可由统一入口显式启动实盘；启动器保持服务端
为 `development`，只为 QMT Agent 子进程注入 `ENV=testing`、账户白名单和实盘
开关：

```powershell
.\ops\quantx.ps1 up -Environment dev -Profile full -Mode live `
  -AccountId <账户> -ConfirmLive "LIVE:<账户>"
```

控制与订单回报走 WebSocket；批量行情按请求 ID、批次序号、压缩和 SHA256
通过 HTTP 上传。断线重连后 Agent 先上报完整账户快照。

首次运行必须由 Web 创建一次性登记码，再执行：

```powershell
python -m quantx_qmt_agent.main enroll `
  --api-url http://127.0.0.1:8080 `
  --code <一次性登记码>
python -m quantx_qmt_agent.main status
```

本地 SQLite journal 持久化命令幂等记录和待确认回报。相同消息 ID 与不同
载荷会被拒绝；已完成命令在重连后只重放原确认与未确认回报，不重复调用
broker。过期、账户不在白名单和协议版本不兼容的命令都会在本地拒绝。
命令的 `execution_mode` 必须与 Agent 模式完全一致；paper 命令不会进入
live Agent，live 命令也不会降级为模拟成交。
