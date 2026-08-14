# 系统边界

## 物理边界

```text
iOS / 第三方客户端
  -> VPN 或零信任 HTTPS/WSS
  -> Windows Caddy
  -> FastAPI / Strawberry
  -> PostgreSQL 状态真源

Windows QMT Agent
  -> 主动出站 WebSocket
  -> miniQMT / XTData / XTTrading
```

- Caddy 是唯一公开入口。
- 开发 Caddy 的 `8080` 监听所有本机 IPv4 接口，允许专用局域网访问。
- API 只监听 `127.0.0.1:18081`。
- 生产端口 8080 保持 loopback，由上游安全入口转发。
- QMT Agent 不开放局域网监听端口。
- 服务端 API、Engine 和 Worker 禁止导入 `miniqmt` 或 `xtquant`。

## 凭证边界

券商账号、交易密码、资金密码、QMT 路径和设备密钥不得进入：

- 服务端数据库；
- GraphQL、REST 或 WebSocket 业务消息；
- 日志和异常堆栈；
- iOS App、UserDefaults 或客户端诊断包。

设备密钥只保存在 Windows Credential Manager，iOS 客户端不参与 Agent
登记或券商连接。

## 状态真源

| 数据 | 真源 |
| --- | --- |
| 用户、权限、实例与审计 | PostgreSQL |
| Redis | 唤醒、广播和缓存，不是可靠状态真源 |
| 实盘委托、成交、资金和持仓 | miniQMT 回报，经服务端持久化与收敛 |
| iOS 本地 | Keychain Token、非敏感偏好和当前会话内存快照 |

客户端不得根据价格变化推断成交，也不得以本地缓存覆盖服务端返回的保守状态。
失败刷新可以保留最后有效内存快照并明确标记 stale；不完整、旧序列或跨账户
快照不得清空或覆盖更可信状态。短时交易确认令牌只在内存中存在，不能持久化。
