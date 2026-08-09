# QuantX 故障排查

## 统一诊断

```powershell
.\ops\quantx.ps1 status
.\ops\quantx.ps1 logs
Invoke-RestMethod http://127.0.0.1:8080/health/components
```

`up` 遇到端口占用会报告 PID 和命令行，不会杀死未受 QuantX 状态文件跟踪
的进程。`down` 只停止 PID 与启动时间都匹配的受管进程。

## 常见问题

- **8080 不通**：检查 Caddy 状态与日志，再检查 API 18081、Vite 5250。
- **ready 失败**：查看 `/health/components` 中数据库和 Engine 心跳。
- **Prefect 离线**：确认 `PREFECT_API_URL` 的 `/health`、`quantx-pool`、
  deployment 和 Worker 心跳；不要在本机另起 Prefect Server。
- **QMT Agent 离线**：先检查设备是否登记、凭证是否在 Windows Credential
  Manager，再检查出站 WebSocket；不要在服务端写券商配置。
- **消息积压**：检查 outbox/inbox backlog、Engine 租约和数据库连接，
  Redis 不可用时仍应由轮询恢复。
- **前端契约错误**：经 Caddy 重跑 codegen、TypeScript、lint、test 和
  build。

真实交易默认关闭。testing 危险测试和 production 灰度都必须通过 Agent
本地开关与账户白名单；production 还必须通过服务端开关、账户白名单、
Agent READY、Engine 租约、完整新鲜快照、无未知委托、对账、策略授权和
kill switch 检查。
