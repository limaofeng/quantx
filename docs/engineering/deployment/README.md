# Windows 生产与 macOS 开发

GPU 训练依赖修复与验收见 [Windows LightGBM GPU 训练](LIGHTGBM_GPU.md)。

Windows 独占 QMT/XTData/XTTrading，使用 `production/full/live`；macOS 使用
`dev/full/paper` 和本机独立数据服务。两端通过只读行情接口连接，不共享账户、数据库、
Redis、Prefect 工作池或设备密钥。不得把 QMT Agent 同时登记到两个环境。

## 生产机 SSH 与开发工具

以下信息于 2026-09-09 核实，版本和安装路径变更后应重新确认。

| 项目 | 已确认信息 |
| --- | --- |
| SSH | `ssh limao@192.168.5.6`，使用开发机本地 SSH 密钥，无需复制私钥到远端 |
| 远端主机 / 用户 | Windows `MyPC` / `mypc\limao`，默认终端为 PowerShell |
| 生产项目目录 | `F:\Workspace\quantx`，核实时分支为 `main` |
| 运维入口 | `F:\Workspace\quantx\ops\quantx.ps1` |
| Codex CLI（npm 全局安装） | `C:\Users\limao\AppData\Local\nvm\v22.21.1\codex.cmd` |
| Codex CLI 升级记录 | `0.133.0` → `0.153.4`，已在生产项目目录验证启动与版本 |
| 其他 Codex 安装 | 桌面应用内置 `0.153.4`；VS Code 扩展内置 `0.153.0`，此次未修改 |

连接并在项目目录启动 Codex：

```bash
ssh -o BatchMode=yes -o ConnectTimeout=10 limao@192.168.5.6
```

若 SSH agent 报签名通信失败，本地已授权密钥可用时可显式绕过 agent；2026-09-10 已验证以下方式连接和传输成功，不需修改 SSH 全局配置或复制私钥：

```bash
ssh -o BatchMode=yes -o ConnectTimeout=10 -o IdentityAgent=none -o IdentitiesOnly=yes -i ~/.ssh/id_rsa limao@192.168.5.6
```

在远端 PowerShell 执行：

```powershell
Set-Location F:\Workspace\quantx
& C:\Users\limao\AppData\Local\nvm\v22.21.1\codex.cmd --version
& C:\Users\limao\AppData\Local\nvm\v22.21.1\codex.cmd
```

核实时 SSH 会话无法通过 PATH 直接找到 `codex` 和 `npm`，完整路径可用；
该 PATH 问题尚未修复。项目没有单独的 `node_modules/@openai/codex` 安装。
上面的 Node 路径仅用于已安装的 Codex CLI，项目 Node 版本仍遵循 `.nvmrc`。

本次升级使用下列命令；今后升级应先核实当前版本与最新稳定版，并取得升级授权：

```powershell
& C:\Users\limao\AppData\Local\nvm\v22.21.1\npm.cmd install -g @openai/codex@latest --registry=https://registry.npmjs.org
```

SSH 会话可能在任务结束后失效，后续操作按需重新连接。连接或开发工具维护不等于
授权生产部署、服务重启或真实交易；本次检查和升级未修改项目代码或重启生产服务。

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
局域网 192.168.5.6:8080；API 18081、Market Gateway 18082、Monitor 18083、Data API 18085
仅绑定回环地址。生产默认只允许本机及 192.168.5.0/24。

行情重构版本的统一启动器先启动单进程 Data API 和常驻 Data Worker，再启动 Gateway
及业务服务。所选 `.env.development` / `.env.production` 必须配置至少 32 字符的
`QUANTX_MARKET_DATA_INTERNAL_TOKEN`，不能放入共享 `.env`，也不能复用公共行情 token。
启动器使用 Bearer 认证检查 18085 的 `/health/ready`（仓储结构）与 `/health/worker`
（未过期的 Worker 租约）；租约就绪不代表行情覆盖证明或全部依赖已就绪。
Caddy 将 `/market-data/internal/v1/*` 和 `/agent/market-data/*` 转发至 18085；
其余公共行情接口由 Gateway 承接，交易 Agent 控制仍在业务 API。历史上传要求新版
Agent 使用独立历史令牌，不能只切路由而保留旧 Agent。暂存清理由 Data Worker 执行，
完整上传但摄取失败的 manifest 保留供原任务恢复。上线前须先完成数据库迁移与协调切换清单，不能仅更新
旧进程。当前仅验证启动装配、路由和探针，实际常驻启动及 Windows 停止流程尚待验收。

Monitor 仍通过 `-Component monitor` 独立管理。新增可选的“跨环境行情与补数”
探针，监测导出失败及开发端行情连接；不把开发故障变成生产交易授权来源。
QMT Agent 继续使用 xtquant-demo，服务端和 Worker 使用各自已配置的 Python 环境。

## 独立 Trainer

Trainer 使用专用 `quantx-train` 环境和显式开发配置，运行命令为 `ops/quantx.ps1 <命令> -Component trainer -Environment dev -TrainerPython <独立 Python 绝对路径> -TrainerConfig <配置绝对路径>`。支持 up/down/status/logs/doctor/drain/resume，logs 的 `-Tail` 为 1–1000；普通服务启停不代管 Trainer。macOS 使用 `ops/quantx.sh` 的对应 `--component trainer`、`--trainer-python`、`--trainer-config` 参数。具体配置与尚未完成的运行端验收见 [Trainer 说明](../../../apps/trainer/README.md)。

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

该标准入口也供开发回测补数使用。结果包含 `expected_partitions`、
`verified_partitions` 及逐分区状态、任务 ID、原因。单个 `INCOMPLETE` 不会阻止
其余分区提交；存在失败时退出码为 2。异步等待结果不表示数据已完整，
`LOCAL_VERIFIED` 才表示该分区已导入并回读校验。失败源不会自动重试。

Tick 补数后，用标准回测数据准备入口检查本机持久化结果：

```bash
conda run --no-capture-output -n quantx python ops/t-assistant-backtest-data.py \
  --environment development --instruments 600000.SH \
  --start 2026-09-01 --end 2026-09-07 --output .runtime/backtests/data-check
```

检查包含分页耗尽、源身份、时间顺序、盘口字段和逐分区缺口；生成的 manifest
还记录 `continuous-minute-coverage.v1` 连续竞价分钟覆盖及原始 `stock_status_counts`。
分钟覆盖沿用回测准入的交易时段定义（当前每日 237 分钟），同分钟多条 Tick
只计一次；100% 分钟覆盖不等于证明交易所每条 Tick 都已取得。
空分区没有可靠无数据证明时仍为 `INCOMPLETE`。历史日 K 涨跌停价允许为空，
不计为 Tick 数据缺失。未知整数证券状态保留在数据中，回测不得据此授权交易；
数据归档成功与策略准入通过是两个独立结论。

已有且通过持久化校验的历史数据随时导出。系统设置 → 行情数据
（`/settings/market-data`）控制开发端触发的历史补采，默认全天允许，包含盘中。
自定义模式支持最多 12 个不重叠的北京时间时段，包含起点、不包含终点，支持跨午夜；
例如 `11:30–13:00` 和 `16:00–次日08:30`。可选择非交易日全天允许：周末或
已保存的沪市休市日可全天补采；日历未知时不扩大到全天，仍按配置时段判断。
配置保存在 PostgreSQL，Worker 创建请求和 API 派发请求前重新读取；保存后无需重启，
不取消已派发请求。配置读取失败时禁止新补采。Agent 健康门和生产请求优先级保持不变。
开发请求按单标的、单日、单周期拆分，窗口外的缺口继续排队。

生产导出和开发导入均由独立 Data Worker 的对应循环推进，每轮最多一个行情分区；
CLI/历史 Flow 通过本机 Data API 提交并查询需求。生产导出使用独立发布锁并校验
Worker 租约，源请求创建与交付关联同事务提交，取消时等文件操作结束再释放锁。
上线本批前应用迁移至 `20260910_0087`，并停止、移除旧 Prefect deployment
`development-data-import`、`development-data-export`，确认旧执行已经退出，再启动新
Data Worker；删除代码中的日程不会自动删除 Prefect Server 上已注册的 deployment。此处是切换步骤，尚未执行。
macOS 离线不删除生产任务。下载资格保留七天；目录仍引用的原分片继续保留。
过期后的新交付从已有持久化数据重建；若覆盖证明或源身份不匹配，返回 INCOMPLETE，不伪装成完整数据。
开发端检查 SHA256、协议、范围与行数，幂等导入后再次回读验证；已下载分片可复用。
开发 Worker 不重复扫描 LOCAL_VERIFIED、BLOCKED 或 INCOMPLETE；恢复只处理到期分区。
完成后只读复核最多保留 4 次累计尝试，每次在 IO 前持久化预留，取消、重启或成功均不
返还；耗尽返回 DELIVERY_PROOF_BUDGET_EXHAUSTED，需要显式恢复处理，不自动重开预算。
年度日历与独立因子导入也通过本机 Data API 持久化提交，由 Data Worker 推进；每轮
最多一个参考请求和一个行情交付，避免参考积压独占全部推进机会。空开发库首先出现
DEVELOPMENT_REFERENCE_PENDING 属于等待日历导入，不应另启旧导入 Flow 或在调用方写库。
没有可靠无数据证明的空区间仍视为数据不足。

Engine 启动在取得单实例锁后，通过同一专用数据库连接登记持久化归档代次；
迁移 0084 未应用或登记失败时，在启动行情和策略组件之前退出。代次记录不可降级删除；
Engine 停止需物理关闭租约连接，不可归还池中继续使用。这是实时归档修订排序的前置
接线。迁移 0085 增加归档待办及版本证明，Data API 就绪检查包含这两张表，Data Worker
默认消费已接受归档。Data API 默认 1m 历史读端已按发布目录选择归档版本，保持整日
历史结果优先和原有分页边界。原生整日优先要求上海时间 15:01 后发起的 download
请求与覆盖/内容证明，盘中采集或仅缓存读取不能遮蔽后来归档。迁移 0087 增加持久化
恢复范围，API 接收修订前强制检查范围，就绪检查包含范围表；若已有旧归档证据，
迁移明确拒绝并要求先完成原证据的范围映射，不自行猜测崩溃前的起点。
Engine 队列已实现但默认生命周期、其他消费者和缺口对账仍待接入，不能据此宣称实时
writer 已完成迁移；范围登记和迁移均不等同于完成恢复验收。

参考数据仅导出明确的证券、日历和因子字段。复权覆盖沿用原有 schema-v2 证据，
研究读取时再次与本机当前因子逐行核验；财务数据继续使用既有独立研究来源。
参考接口的 `factor_coverage.status=UNVERIFIED` 同时返回 `reason`：
`AUDIT_MISSING` 表示无候选审计，`AUDIT_INVALID` 表示审计未通过 schema-v2 校验，
`COVERAGE_END_BEFORE_AS_OF` 表示证明结束日期不足（附 `audited_end_date`），
`CURRENT_ROWS_MISMATCH` 表示当前因子与审计不一致。日期不足时尚未核验当前行，
不能据此断言数据一致。历史行情导出完成不代表因子证明也已更新。
Windows 生产维护应通过现有 `divid_factor_sync_flow` 为所需标的刷新证明，
显式指定 `stock_list`、`start_time` 和 `end_time`；起点应覆盖 Mac 研究所需历史，
终点至少覆盖其 `as_of`。当前接口返回单条证明，不能只补短区间后假定可拼接。
同步完成后检查分片、schema-v2 审计与当前因子，并从实际 Caddy 参考接口验证
`VERIFIED`，再由 Mac 重试导入。推进验收日期前须重新检查证明截止日期；此接口
本身不会派发生产因子同步，也不能通过手工延长审计日期代替真实同步。
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

历史补数性能排查与优化交接见
[2026-09-08 历史补数慢诊断报告](HISTORY_BACKFILL_PERFORMANCE_DIAGNOSIS_20260908.md)。

```powershell
.\.runtime\tools\caddy\caddy.exe validate `
  --config .\ops\caddy\Caddyfile.production --adapter caddyfile
python -m pytest tests/infrastructure/test_ops_contract.py
```

完整生产实盘验收还应确认 `status` 显示 `profile=full`、`agentMode=live`、
唯一账户、`liveTrading=ENABLED`，且 QMT Agent、对账和行情流稳定为 `ready`。
