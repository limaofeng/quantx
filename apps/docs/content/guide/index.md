# 选择接入方式

所有客户端都通过 Caddy 暴露的同源 `/graphql` 使用业务能力，但会话、凭证保存
和兼容承诺不同。

| 接入端 | 从这里开始 | 会话 | 兼容边界 |
| --- | --- | --- | --- |
| QuantX Web | [Web 快速开始](./web-quickstart) | HttpOnly Refresh Cookie + 内存 Access Token | 包含 `web-internal` 能力 |
| iOS、Android、桌面端 | [原生客户端](./native-quickstart) | 显式轮换 Token，安全存储 | 使用 `native` 标记能力 |
| 自动化与外部系统 | [第三方 API](./third-party-quickstart) | 显式轮换 Token，独立限权账号 | 只依赖 `third-party` + `supported` 能力 |

接入前共同确认：

1. 只访问 Caddy，不直接连接 API 内部端口 `18081`。
2. 从当前部署版本下载契约，不依赖生产内省。
3. 登录后读取实际权限和授权账户，不在客户端假设管理员能力。
4. Mutation 返回成功不等于委托已报或成交。

下一步可查看[能力地图](./capabilities)或直接下载[契约](../reference/)。
