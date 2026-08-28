# QuantX Kubernetes 正式环境运行手册

## 部署边界

正式服务端只运行在 Kubernetes。Windows 不安装 QuantX API、行情服务、Monitor、
Engine、Worker、AI Runtime 或 Gateway；`ops/quantx.ps1 up` 仅用于开发环境。

唯一例外是集群外的 QMT Agent 执行节点。它必须运行在能够访问 miniQMT/XTQuant
的 Windows 交互式会话中。交易控制、行情、历史上传和报告链路只通过公开
HTTPS/WSS 地址向集群建立出站连接；同一 Agent 进程额外开放固定只读健康端点
`0.0.0.0:18084`，供 Monitor 通过可路由的私有网络直连。券商配置、设备密钥和
QMT 用户目录不得进入镜像、Kubernetes Secret 或服务端数据库。

## 发布产物

Git tag 发布流水线执行 Python、前端和客户端契约检查，并向 GHCR 推送两个镜像：

- `ghcr.io/limaofeng/quantx-server:<tag>`；
- `ghcr.io/limaofeng/quantx-gateway:<tag>`。

部署必须固定镜像 digest；tag 只用于选择版本，不能作为运行中的可变引用。服务镜像
供 API、Market Data Service、Engine、Worker、AI Runtime 和 Monitor 共用，各组件
仍是独立 Pod 和独立生命周期。Gateway 镜像包含 Caddy、Web 与 Docs 的静态产物。

## 首次配置

1. 准备支持 `ReadWriteOnce` 的默认 StorageClass，供 Monitor 独立 PVC 使用。
2. 确认集群能访问外部 PostgreSQL、Redis、InfluxDB 和 Prefect Server。
3. 复制并填写非敏感配置：

   ```bash
   cp ops/k8s/production/runtime-config.example.yaml /secure/quantx-runtime-config.yaml
   ```

4. 通过 External Secrets、Sealed Secrets 或集群密钥管理方案创建
   `quantx-runtime-secrets`。仓库中的 Secret 文件只是键名示例，不得填值后提交。
5. 根据实际 Ingress Controller 复制
   `ops/k8s/production/ingress.example.yaml`，配置受 Windows QMT 主机信任的 TLS
   证书。`PUBLIC_URL`、CORS、Web 认证来源和 Monitor 公共探测地址必须是同一个
   HTTPS origin。
   `MONITOR_QMT_AGENT_HEALTH_URL` 必须填写为集群 Monitor Pod 可路由访问的
   Windows 私有 DNS 名或保留地址，例如 `http://windows-qmt.internal:18084`；
   不得填写 `127.0.0.1`，也不得从 WebSocket remote address 自动推导。
6. 从对应 GitHub Release 下载流水线生成的 `quantx-k8s.yaml`。该文件已经固定两个
   多架构镜像的 OCI digest；部署前必须确认不含 `replace-me`，并把 digest 写入
   投产记录。直接使用仓库 base 时，必须通过受控 Kustomize overlay 写入同一 digest。

生产 API 必须监听 Pod 接口 `0.0.0.0:18081`。外部只公开 Ingress；API `18081`、
行情服务 `18082` 和 Monitor `18083` 均保持 ClusterIP，不创建 NodePort。

## 数据库迁移

迁移必须作为一次性受控 Job 运行，并使用与目标工作负载完全相同的 server image
digest。迁移前先由数据库平台创建可恢复备份，记录备份 ID 并完成恢复点检查，
随后执行：

```bash
cp ops/k8s/production/migration-job.example.yaml /secure/quantx-migration-job.yaml
# 把 Job 名称和 server image digest 替换为本次发布值后：
kubectl apply -f /secure/quantx-migration-job.yaml
kubectl -n quantx wait --for=condition=complete job/quantx-migrate-<version> \
  --timeout=15m
```

实际 Job 必须注入 `quantx-platform-config`、`quantx-runtime-config` 和
`quantx-runtime-secrets`；不要直接把数据库 URL 放进命令行。迁移完成后运行
`python -m quantx_infrastructure.database.schema_control check`。数据库 downgrade
永久禁止；回滚只能选择与当前 revision 兼容的旧镜像。

## 发布步骤

```bash
kubectl diff -f /secure/quantx-runtime-config.yaml
kubectl diff -f /secure/quantx-runtime-secrets.yaml
kubectl diff -f /secure/quantx-k8s.yaml

kubectl apply -f /secure/quantx-runtime-config.yaml
kubectl apply -f /secure/quantx-runtime-secrets.yaml
kubectl apply -f /secure/quantx-k8s.yaml
kubectl apply -f /secure/quantx-ingress.yaml
```

按以下顺序等待：行情服务、API、Engine、Worker、AI Runtime、Monitor、Gateway。
API Kubernetes readiness 使用 `/health/service-ready`，只验证 API 基础服务与
PostgreSQL；`/health/ready` 是更严格的交易业务门禁，在 QMT Agent 尚未重连时返回
503 是预期行为，不能因此把 API Pod 从 Service 摘除。

```bash
kubectl -n quantx rollout status deployment/market-data-service --timeout=5m
kubectl -n quantx rollout status deployment/api --timeout=5m
kubectl -n quantx rollout status deployment/engine --timeout=5m
kubectl -n quantx rollout status deployment/worker --timeout=5m
kubectl -n quantx rollout status deployment/ai-runtime --timeout=5m
kubectl -n quantx rollout status statefulset/monitor --timeout=5m
kubectl -n quantx rollout status deployment/gateway --timeout=5m
```

## QMT Agent 连接

在 Windows 执行节点复制生产配置模板到
`.runtime/qmt-agent/.env.production`，在本机控制台完成登记：

```powershell
.\ops\quantx-agent.ps1 enroll -Environment production `
  -ApiUrl https://quantx.example.com -Code <一次性登记码>
.\ops\quantx-agent.ps1 doctor -Environment production -AccountId <账户>
.\ops\quantx-agent.ps1 up -Environment production -AccountId <账户>
.\ops\quantx-agent.ps1 status -Environment production
```

生产入口必须使用 Windows 系统信任的 TLS 证书。不得把开发 Caddy 根证书、集群
内部 CA 或 Ingress 私钥复制到 QMT 节点。

`quantx-agent.ps1 up` 会校验并幂等维护名为
`QuantX-QMT-Agent-Health-18084` 的入站规则：只对 Windows `Private` 网络配置文件
开放 TCP `18084`，远端地址为 `Any`，不绑定单一 Monitor IP。创建或修复规则需要
管理员 PowerShell；规则已正确时普通重启不需要提升权限。不得把规则扩大到
`Public`/`Domain`。启动后先在 Windows 本机验证：

```powershell
Invoke-RestMethod http://127.0.0.1:18084/health/live
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:18084/health/ready
```

再从 Monitor Pod 验证直连与真实 RTT；HTTP 503 仍表示端点可达，只是 Agent 本地
readiness 未满足：

```bash
kubectl -n quantx exec monitor-0 -- python -c \
  "import httpx; r=httpx.get('http://windows-qmt.internal:18084/health/ready', timeout=5, follow_redirects=False, trust_env=False); print(r.status_code, r.elapsed.total_seconds()*1000)"
```

## 备份标准

- PostgreSQL 由数据库平台执行自动备份和时间点恢复，最近一次可恢复备份不得超过
  24 小时；每次 schema 迁移前另建恢复点并记录备份 ID。
- Monitor PVC 必须使用 CSI VolumeSnapshot 或在 Monitor Pod 内调用
  `python -m quantx_monitor.main backup --destination <挂载路径>`，随后把备份复制到
  PVC 之外的受控对象存储。只有 PVC 内的副本不算灾备。
- Windows QMT 节点使用 `python -m quantx_qmt_agent.main backup-state` 备份 journal，
  并校验 `idempotency.sqlite3` 完整性。设备密钥仍由 Credential Manager 管理，
  不得写入备份清单。
- PostgreSQL、Monitor 和 QMT journal 必须分别记录时间、大小、SHA-256 和恢复验证
  结果。任一项失败不得刷新“最近成功备份”门禁。

## 分阶段启用实盘

首次部署和每次重大升级都先保持服务端 `ENABLE_REAL_TRADING=false`、
`QMT_REAL_TRADING_ENABLED=false`，并保持账户在 `SHADOW`。Windows Agent 可以用
本机 production 安全门连接、上报账户与行情，但服务端不得下发真实委托。

在开启服务端实盘总门前，必须完成新鲜完整快照、Engine 对账、行情 READY、24 小时
内成功备份和 kill switch 演练。随后只为唯一账户配置
`REAL_TRADING_ACCOUNT_ALLOWLIST`，通过 Web 的账户执行控制建立无外部活动的实盘窗口
并单独授予增仓能力。生产灰度必须经过 `CANARY`，初始只允许一个标的、一个并发批次、
每单 100 股且单笔不超过 20,000 元；不得从 `SHADOW` 直接切到 `LIVE`。

CANARY 的委托、撤单、成交、迟到回报和重连对账全部核对无误后，才可在 Web 使用
`trade:approve` 权限和精确确认 `LIVE:<账户>` 提升到 `LIVE`。任何外部手工活动、
快照过期、行情断流或账实差异都会使窗口失效并暂停自动执行，恢复时必须重新建立
新鲜窗口，不能仅重启 Pod 绕过。

## 上线验收

至少验证：

```bash
curl --fail https://quantx.example.com/health/live
curl --fail https://quantx.example.com/monitor/health/ready
curl --fail https://quantx.example.com/monitor/api/v1/summary
kubectl -n quantx get pods
kubectl -n quantx logs deployment/market-data-service --tail=100
kubectl -n quantx logs statefulset/monitor --tail=100
```

验收条件：

- 行情服务 `/health/ready` 返回 Redis `ready`；
- Monitor 第一轮探测完成后才进入 ready，`market-data-service` 为直接目标且具有
  实际延迟，不出现启动阶段 `N/A`；
- API `/health/components` 同时展示 `marketDataService` 服务健康和 `marketData`
  业务数据新鲜度；
- QMT Agent 协议为 `1.1`、完整快照小于 90 秒、对账 READY；
- PostgreSQL 备份小于 24 小时，Monitor PVC 已纳入 CSI 快照或文件级备份；
- 未解决 Sev-1/Sev-2 为零，kill switch 演练通过。

## 扩缩容约束

当前是个人单账户系统。API、行情服务、Engine、Worker、AI Runtime 和 Monitor
默认都保持一个副本，不为假设中的多租户流量预先扩容。Engine 的 PostgreSQL 租约
仍是唯一执行所有者；Monitor 的 SQLite PVC 只允许一个写实例。Gateway 可以滚动
运行两个副本。任何提高业务组件副本数的变更都必须先证明 Agent 会话、命令投递、
Prefect deployment 和状态收敛具备对应的一致性语义。

## 回滚与紧急停止

代码回滚通过把 Kustomize 镜像改为上一兼容 digest 后重新 apply；禁止执行 Alembic
downgrade。若新代码已经依赖不可向后兼容的 schema，必须前向修复，不能强行回滚。

任何未知委托、重复提交、账实差异、快照超时或终态回退都立即 hard kill：停止新开仓、
撤销工作中委托、保存迟到回报并重新对账。API 或集群不可用时，在 QMT 主机执行：

```powershell
python -m quantx_qmt_agent.main emergency-stop --reason "<原因>"
python -m quantx_qmt_agent.main emergency-status
```

自动化流水线只允许 simulator/paper；禁止自动化真实交易 E2E。
