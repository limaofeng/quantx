# GraphQL WebSocket

实时订阅使用与 HTTP 相同的 `/graphql` 地址和
`graphql-transport-ws` 子协议。

## 建立连接

```text
wss://quantx.example.internal/graphql
Sec-WebSocket-Protocol: graphql-transport-ws
```

第一条初始化消息必须包含当前 Access Token：

```json
{
  "type": "connection_init",
  "payload": {
    "Authorization": "Bearer <access-token>"
  }
}
```

缺少、无效或已吊销的 Token 会拒绝连接。服务端在 Access Token 到期时使用
关闭码 `4401` 和原因“访问令牌已过期”关闭连接。

## Token 轮换

WebSocket 连接不会自动继承 HTTP 客户端刚刷新的 Token：

1. Access Token 轮换后主动关闭旧连接。
2. 使用新 Token 建立连接并完成 `connection_init`。
3. 重新查询关键快照。
4. 再恢复订阅并对增量去重。

不要在同一连接上发送新的认证参数，也不要无限重连一个已过期 Token。

## 前后台与断线恢复

- 原生 App 进入后台后暂停或关闭订阅，不依赖后台常驻 WebSocket。
- 回到前台先验证会话并拉取快照。
- VPN、Wi-Fi 或蜂窝网络切换后，把连接视为可能丢失事件。
- 使用指数退避重连，并在未认证错误时先刷新会话。
- UI 必须显示最后更新时间与 stale 状态。

## 订阅边界

订阅只提供增量通知。账户资金、持仓、订单和策略的恢复真源是重新执行查询；
实盘成交真源仍是 miniQMT 回报经服务端收敛后的结果。
