# QuantX 本机实盘生产运行手册

## 发布、安装与迁移

发布构建固定使用 Windows、Python 3.13.9 和 Node 20：

```powershell
npm ci
npm run check
npm run lint:strict
npm run test:run
$env:VITE_APP_ENV = "production"
$env:QUANTX_DOCS_VERSION = "v1.0.0"
npm run build
.\ops\build-release.ps1 -Version v1.0.0 `
  -PythonExecutable C:\Path\To\Python313\python.exe
```

发布包包含服务器 wheels、两套离线依赖 wheelhouse、Web 与 Docs `dist`、
客户端 API 契约、Alembic、运维脚本、Caddy/WinSW、配置模板、manifest 和
逐文件 SHA256。生产服务只引用
`.runtime/releases/<version>`，`.runtime/current` 是唯一激活指针。

目标机必须预装 PostgreSQL 16 客户端工具，并确保 `pg_dump`、`pg_restore`、
`createdb`、`dropdb` 在 `PATH` 中；数据库服务本身仍由外部环境管理。

管理员首次安装时：

```powershell
.\ops\quantx.ps1 install -Environment production `
  -ReleasePath .\.runtime\release-artifacts\quantx-v1.0.0-windows-x64.zip
```

首次运行会生成 `.runtime/config/.env.production` 并失败关闭。填写强密钥、
PostgreSQL、Redis、InfluxDB 和精确的 loopback HTTPS 配置后重试。安装器会：

1. 校验外层及包内 SHA256，拒绝路径穿越；
2. 创建版本专属 server venv，以 `--no-index` 安装锁定 wheel；
3. 用一次性干净 venv 把 QMT Agent、contracts 和非厂商依赖安装到版本目录的
   `qmt-site-packages`；不读取、不修改 XTQuant 厂商 site-packages；
4. 在服务启动前执行 schema doctor、备份和显式 Alembic 升级；
5. 原子切换 `current`、安装 WinSW、信任 Caddy 本地 CA并验证 HTTPS；
6. 注册每天（含周末）16:30 的收盘后备份任务；严格 24 小时门禁要求周末与
   长假期间也持续保留新鲜恢复点。

生产启动本身只检查 Alembic revision，不执行 DDL。手工操作入口：

```powershell
.\ops\quantx.ps1 doctor -Environment production
.\ops\quantx.ps1 migrate -Environment production
.\ops\quantx.ps1 backup -Environment production
.\ops\quantx.ps1 restore-verify -Environment production `
  -BackupPath <隔离副本>
.\ops\quantx.ps1 verify -Environment production
.\ops\quantx.ps1 rollback -Environment production
```

回滚只切换到数据库 revision 兼容的上一代码版本；永不自动执行破坏性数据库
downgrade。每月至少把最近备份复制到隔离环境，完成一次真实
`pg_restore`、Agent journal 完整性检查和只读启动验证。

### 0016 会话迁移

`20260815_0016` 为原生会话增加唯一主账户、设备权限和成对约束。由于旧数据
无法区分 native/Web，迁移会在服务停止期内一次性撤销所有尚未撤销的旧
会话。它只为符合 `revoked_at IS NULL` 且两个新作用域字段为 SQL NULL 的行设置
撤销时间；不删除会话或审计记录，重复执行也不会改写已撤销的时间。
发布窗口必须预告原生与 Web 用户升级后需要重新登录。

## 本地紧急停止

API、数据库或网络不可用时，在 QMT 主机直接执行：

```powershell
python -m quantx_qmt_agent.main emergency-stop --reason "<原因>"
python -m quantx_qmt_agent.main emergency-status
python -m quantx_qmt_agent.main emergency-clear `
  --confirmation CLEAR-LOCAL-EMERGENCY
```

本地 emergency stop 始终拒绝新委托，但允许撤单。损坏或不可读的 emergency
状态文件按“已触发”处理。服务端 hard kill 会冻结策略、取消未投递的新下单并
向券商工作中委托发撤单；迟到成交仍须落库并重新对账。

## 上线节奏

影子/准备阶段连续 10 个交易日：账户级 rollout 保持 `SHADOW`，真实行情和
账户只读，不由 QuantX 提交委托。用户仍可在 QMT 客户端手工交易；所有这类
委托与成交必须被分类为外部活动并完整入账。要求零未分类活动、零无法解释的
账实差异、零重复业务键、终态零回退；期间至少完成一次主机重启与恢复演练。
此阶段 readiness 显示 `PREPARING` 是正常结果。

CANARY 连续 5 个交易日：

- 仅一个白名单账户和一个标的；
- 同时仅一个批次；
- 每单固定 100 股，单笔不超过 20,000 元；
- 每日收盘人工核对券商、PostgreSQL、Agent journal、告警和备份。
- 选择不再从 QMT 客户端手工下单的受控窗口；若出现外部活动，QuantX 自动
  退回暂停/准备状态并重新对账。

切换模式必须使用精确确认：

```powershell
.\ops\quantx.ps1 agent-mode -Environment production -Mode live `
  -AccountId <账户> -ConfirmLive "LIVE:<账户>" -Reason "<审批记录>"
```

正式运行前必须同时满足：无未解决 Sev-1/Sev-2、备份小于 24 小时、完整账户
快照小于 90 秒、对账 READY、协议 1.1、策略政策已确认、kill switch 演练通过。

## 立即停止条件

任何未知委托、重复提交、账实差异、快照超时或终态回退都立即 hard kill：

1. 禁止新开仓，冻结策略；
2. 取消未投递下单，并向所有券商工作中委托发撤单；
3. 保存迟到回报，生成 Sev-1 告警并重新取得完整快照；
4. 人工核对券商侧安全后，才允许代码回滚或 emergency clear。

自动化流水线只使用 simulator/paper；禁止自动化真实交易 E2E。
