# Windows 生产与 macOS 开发

Windows 独占 QMT/XTData/XTTrading，使用 `production/full/live`；macOS 使用
`dev/full/paper` 和本机独立数据服务。两端通过只读行情接口连接，不共享账户、数据库、
Redis、Prefect 工作池或设备密钥。不得把 QMT Agent 同时登记到两个环境。

## 环境配置与启动

Windows 使用 `apps/api/.env.production`，macOS 使用 `.env.development`；
`.env` 只放公共非敏感默认值。启动前必须显式配置四个数据服务地址，缺项即失败，
不回退到另一环境。选定文件覆盖继承的配置值，`ENV` 由启动器确定，不能被文件改写。
配置样例为 `ops/config/production.env.example` 和 `development.env.example`。

本机数据服务运行在 WSL 时，在生产文件设置 `QUANTX_EXTERNAL_DEPENDENCY_HOST=wsl`。
启动器解析当前 WSL eth0 地址并用于四个数据端点，保留端口、数据库名和认证信息；
解析失败即停止启动。Windows 回环端口转发在全市场行情负载下可能延迟过高，不能仅凭
PING 成功认定行情写入性能合格。此配置不启动或停止 WSL 数据服务。

从旧开发环境切换生产时，配置迁移会撤销开发免密登录创建的会话；原密码登录会话
不受影响。已完成配置切换的运行端可使用 Conda Python 执行
`ops/migrate_production_config.py revoke-development-sessions` 补做，操作幂等，随后浏览器
需重新输入密码登录。关闭免密登录入口本身不会使已签发的刷新会话失效。

Windows 根目录入口：

```powershell
.\ops\quantx.ps1 up -Environment production -Profile full -Mode live
.\ops\quantx.ps1 status -Environment production
.\ops\quantx.ps1 logs -Environment production
.\ops\quantx.ps1 down -Environment production
```

`live` 要求生产文件显式启用服务端和 Agent 双开关、唯一账户白名单；保留原有
对账、协议、新鲜快照与执行权限检查。预检失败保持 full/live 请求并显示
DEGRADED / QMT BLOCKED / liveTrading=DISABLED，不得静默切为 data-only。
明确关闭实盘时可传 `-Mode data-only`。Windows 拒绝 `up -Environment dev`；
旧 dev 状态仅用于迁移前停止已有进程。

Web 和 Docs 在更新窗口提前构建到 `apps/web/dist`、`apps/docs/dist`；生产启动
只检查产物，不运行 Vite/VitePress。Caddy 是唯一公开入口，本机 127.0.0.1:8080、
局域网 192.168.5.6:8080；API 18081、Market Gateway 18082、Monitor 18083
仅绑定回环地址。生产默认只允许本机及 192.168.5.0/24。

Monitor 仍通过 `-Component monitor` 独立管理。新增可选的“跨环境行情与补数”
探针，监测导出失败及开发端行情连接；不把开发故障变成生产交易授权来源。
QMT Agent 继续使用 xtquant-demo，服务端和 Worker 使用各自已配置的 Python 环境。

## macOS 本地开发

Python 环境统一使用 Conda，API/Engine/Worker/Research/Monitor 共用独立的
`quantx` 环境，QMT 继续使用 `xtquant-demo`；不得创建或使用项目 `.venv`。

```bash
conda create -n quantx python=3.13 pip setuptools wheel
conda activate quantx
mkdir -p .runtime
uv export --frozen --format requirements-txt --no-hashes --output-file .runtime/conda-requirements.txt
uv pip install --python "$CONDA_PREFIX/bin/python" --no-build-isolation -r .runtime/conda-requirements.txt
```

Windows 使用同一依赖清单，并将 `--python` 指向 Conda 环境中的 `python.exe`。
`uv export` 只生成锁定依赖清单；安装必须显式指定 Conda Python，不能使用默认 `uv sync`。

先安装项目所需 Python、Node 20、uv 和 Caddy，创建 `quantx` Conda 环境、按下述命令安装锁定依赖，并执行根目录
`npm install`。开发数据服务使用独立容器与持久卷，配置位于
`ops/config/compose.development.yaml`，仅绑定本机端口；应用启动器不启停这些服务。

macOS 的 LightGBM 需要 OpenMP 运行库；使用 Homebrew 安装 `caddy libomp`。
使用 nvm 时先在根目录执行 `nvm use`，采用 `.nvmrc` 指定的 Node 版本。
首次启动本机 API/Caddy 后，执行
`CODEGEN_GRAPHQL_ENDPOINT=http://127.0.0.1:8080/graphql npm run codegen`，
生成当前源码对应的 Web 契约；本地 Web 的 `VITE_DEFAULT_ACCOUNT_ID` 应与
开发配置中的模拟账户一致（样例为 `paper-local`）。

```bash
# 先在终端设置 QUANTX_DEV_POSTGRES_PASSWORD，再独立启动开发数据服务。
docker compose -f ops/config/compose.development.yaml up -d
cp ops/config/development.env.example apps/api/.env.development
```

填写本机 PostgreSQL 密码、InfluxDB token、独立应用认证配置，以及 Windows
专用行情 token。开发 PostgreSQL 与 InfluxDB 数据库名必须以 `_dev` 结尾。
按 InfluxDB 官方初始化流程创建 token 和 quantx_dev 数据库；不要使用生产 token。
开发 InfluxDB Core 查询按短时间窗口分批，避免其约 72 小时的单次查询范围限制。

```bash
ENV=development conda run -n quantx python -m alembic -c alembic.ini upgrade head
PREFECT_API_URL=http://127.0.0.1:4200/api conda run -n quantx python -m prefect work-pool create quantx-dev-pool --type process
./ops/quantx.sh doctor --environment dev
./ops/quantx.sh up --environment dev --profile full --mode paper
./ops/quantx.sh status --environment dev
./ops/quantx.sh logs --environment dev
./ops/quantx.sh down --environment dev
```

前端开发配置采用 `VITE_APP_ENV=development`；生产构建采用 `production`。
通过既有认证引导和 paper 配置明确初始化本地模拟账户及冻结 `paper_seed`；
缺少 seed 时保持 PAPER_SEED_REQUIRED，不复制生产资金、持仓或券商账户快照。
Monitor 可用 `./ops/quantx.sh up --component monitor` 单独启动。

## 实时行情与历史补数

生产 `QUANTX_MARKET_DATA_TOKEN` 至少 32 个随机字符，仅授权下述行情接口。
macOS 配置 `QUANTX_MARKET_DATA_URL=http://192.168.5.6:8080` 和
`QUANTX_MARKET_DATA_INSTRUMENTS`（逗号分隔）。当前只开放生产已供给的标的，
开发端不会修改 QMT 订阅。WebSocket 首帧为选择标的的快照，后续为增量；
源时间不变，缺口和慢消费导致断开重同步。开发接收后写本机行情链路。

| 接口 | 用途 |
| --- | --- |
| `WS /market-data/v1/stream` | 首条客户端 JSON 为 instruments 数组；服务端发送现有行情二进制协议 |
| `POST /market-data/v1/history` | 提交 instrument、period、trading_date、adjustment=none，返回任务 id |
| `GET /market-data/v1/history/{id}` | 查询状态、实际覆盖和不可变分片清单 |
| `GET /market-data/v1/history/{id}/chunks/{sha256}` | 下载该任务授权的压缩 JSON 分片 |
| `POST /market-data/v1/history/{id}/retry` | 明确重试失败任务；普通轮询不自动反复下载失败源 |
| `GET /market-data/v1/calendar/{year}` | 已存交易日历，缺失时返回不可用 |
| `GET /market-data/v1/reference/{code}?as_of=YYYY-MM-DD` | 标的基础资料及有覆盖证据的复权因子 |

公共数据接口都要求 `Authorization: Bearer <行情 token>`。它不能用于 Agent
登记、交易或账户接口。响应不含服务器文件路径或设备凭据。

```bash
./ops/quantx.sh history --instruments 600000.SH --period 1m --start 2026-09-01 --end 2026-09-07
```

已有且通过持久化校验的历史数据随时导出；缺口只在北京时间交易日 16:00 至
次日 08:30 或非交易日派发。日历缺失或 Agent 不健康时不派发。开发请求按单标的、
单日、单周期拆分，Agent 既有串行派发优先处理生产请求。盘中提交的缺口持续排队。

生产 Worker 的 development-data-export 与开发 Worker 的 development-data-import
每分钟执行一次；macOS 离线不删除生产任务。分片保留七天，过期后从已有持久化数据
重建；若覆盖证明或源身份不匹配，返回 INCOMPLETE，不伪装成完整数据。
开发端检查 SHA256、协议、范围与行数，幂等导入后再次回读验证；已下载分片可复用。
没有可靠无数据证明的空区间仍视为数据不足。

参考数据仅导出明确的证券、日历和因子字段。复权覆盖沿用原有 schema-v2 证据，
研究读取时再次与本机当前因子逐行核验；财务数据继续使用既有独立研究来源。
回测清单记录初始化时的导入分区版本；实时行情＋paper 成交不等同券商集成验收。

## 首次生产切换与验收

先完成 Windows 代码检查、静态构建和隔离恢复验证，再安排维护窗口。
macOS 验收由开发机单独执行，见 [macOS 验收清单](MACOS_DEV_ACCEPTANCE.md)，不阻塞 Windows 验收。
现有 dev 实盘实例必须先使用 `down -Environment dev` 停止，Monitor 同样单独停止。
`ops/migrate_production_config.py prepare --dependency-host 127.0.0.1 --enable-live`
根据当前 Windows 配置准备生产配置和隔离测试配置，生成独立行情凭证，不打印密钥。
只有已经核对端口转发的本机 WSL 服务才使用上述回环地址。准备阶段不更改生效配置；
停止旧服务及 Monitor 后执行 `ops/migrate_production_config.py apply`。应用前验证源文件
指纹并保留全部原配置。不得在旧 Worker 仍运行时更改其环境配置。

生产更新只使用固定运行目录和已验证版本。环境迁移完成后重新检查双实盘开关、
唯一账户与数据端点，再运行生产 migrate、up、status；不启动第二套 Engine/Agent。
确认 QMT ready、对账就绪、新鲜快照 <90 秒、liveTrading=ENABLED、静态页面和
远程行情正常。普通测试不发送真实订单。首版不包含开机自启或自动重启整套实盘。

外部服务初始化参考：[InfluxDB 3 Core](https://docs.influxdata.com/influxdb3/core/get-started/setup/)、
[InfluxDB 查询范围](https://docs.influxdata.com/influxdb3/core/get-started/query/)、
[Prefect Server](https://docs.prefect.io/v3/get-started/quickstart)。

## 备份、迁移与检查

备份、隔离恢复验证和数据库前向迁移仍属于 生产数据维护：

```powershell
.\ops\quantx.ps1 backup -Environment production
.\ops\quantx.ps1 restore-verify -Environment production -BackupPath <目录>
.\ops\quantx.ps1 migrate -Environment production
.\ops\quantx.ps1 doctor -Environment production
.\ops\quantx.ps1 verify -Environment production
```

`restore-verify` 启动时输出验证 ID；阶段状态与脱敏日志持续写入
`.runtime/restore-verifications/<ID>/status.json` 和 `verification.log`。正常执行期间
等待原进程退出，或按需读取这个小状态文件与日志尾部，不反复读取整库或重启验证。
`COPY bytes_processed` 是当前 COPY 操作的计数，不能当作整库总量或可靠百分比。

数据导入完整、但 schema 检查/升级失败时，保留该隔离数据库供修复后重试：

```powershell
.\ops\quantx.ps1 restore-verify -Environment production -BackupPath <原目录> -RestoreVerificationId <ID>
```

重试要求备份路径、manifest 指纹和数据库服务器一致，并重新校验备份文件校验和。
只跳过已成功的数据导入；重新执行 schema 检查/升级和 QMT journal、Monitor 完整性
检查，不把阶段通过当作整体验收完成。同一个 ID 不能并发执行，也不能重复执行已通过
的验证。部分导入失败会清理本次创建的临时库，不可复用。

schema 验证成功后临时 PostgreSQL 库会清理；后续 journal/Monitor 失败仍留下日志，
但这个已清理的数据库不能按 ID 复用。失败保留的隔离库占用磁盘，需要在成功重试后
自动清理，或在明确放弃该验证时由运维确认清理。不要手工编辑状态文件或对保留库
运行无关写入。重试验证的是当前保留状态，不能替代必须从原始备份重新开始的迁移测试。

先以小规模、覆盖迁移约束的数据运行定向测试，再做最终完整恢复验收。失败先查看
脱敏日志，不用再次全量恢复来获取第一次遗漏的错误输出。

`down` 只停止 `.runtime/state` 中记录且 PID/启动时间匹配的进程，不终止未受
QuantX 管理的进程。

## 验收

```powershell
.\.runtime\tools\caddy\caddy.exe validate `
  --config .\ops\caddy\Caddyfile.production --adapter caddyfile
python -m pytest tests/infrastructure/test_ops_contract.py
```

完整生产实盘验收还应确认 `status` 显示 `profile=full`、`agentMode=live`、
唯一账户、`liveTrading=ENABLED`，且 QMT Agent、对账和行情流稳定为 `ready`。
