# 系统边界

## 物理边界

```text
Web / 原生 / 第三方客户端
  -> VPN 或零信任 HTTPS/WSS
  -> Kubernetes Ingress / Gateway
  -> API / Market Data Service / Monitor
  -> PostgreSQL 状态真源

Windows QMT Agent
  -> 主动出站 WebSocket
  -> miniQMT / XTData / XTTrading
```

- 正式环境由 Kubernetes Ingress 和 Gateway 提供唯一公开入口。
- 开发 Caddy 的 `8080` 监听所有本机 IPv4 接口，允许专用局域网访问。
- 开发 API 只监听 `127.0.0.1:18081`；生产 API 监听 Pod 接口，由 ClusterIP 隔离。
- Windows 不承载正式服务端；它只运行集群外的 QMT Agent 执行节点。
- QMT Agent 不开放局域网监听端口。
- 服务端 API、Engine 和 Worker 禁止导入 `miniqmt` 或 `xtquant`。

## 凭证边界

券商账号、交易密码、资金密码、QMT 路径和设备密钥不得进入：

- 服务端数据库；
- GraphQL、REST 或 WebSocket 业务消息；
- 日志和异常堆栈；
- Web 存储、原生偏好存储或客户端诊断包。

设备密钥只保存在 Windows Credential Manager。Web 只能读取非敏感连接诊断、
创建或取消一次性安全交接，以及撤销设备；不允许远程启动、重连或控制
MiniQMT。原生与第三方客户端不参与 Agent 凭据交换或券商连接。

## 状态真源

| 数据                       | 真源                                                                        |
| -------------------------- | --------------------------------------------------------------------------- |
| 用户、权限、实例与审计     | PostgreSQL                                                                  |
| Redis                      | 唤醒、广播和缓存，不是可靠状态真源                                          |
| 实盘委托、成交、资金和持仓 | miniQMT 回报，经服务端持久化与收敛                                          |
| 客户端本地                 | 只保存会话所需 Token、非敏感偏好和当前会话内存快照；iOS Token 存入 Keychain |

客户端不得根据价格变化推断成交，也不得以本地缓存覆盖服务端返回的保守状态。
失败刷新可以保留最后有效内存快照并明确标记 stale；不完整、旧序列或跨账户
快照不得清空或覆盖更可信状态。短时交易确认令牌只在内存中存在，不能持久化。
