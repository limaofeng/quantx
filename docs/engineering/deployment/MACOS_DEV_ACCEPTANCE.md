# macOS 开发环境验收

本清单由开发机执行，Windows 生产验收不等待本清单。验收期间不调用真实交易接口，
不在 Mac 安装 QMT、不复制生产业务库，也不使用生产登录或设备凭据。

## 1. 准备与隔离

- 使用包含 Windows/macOS 环境改造的同一已提交代码版本，记录提交号。
- 创建 `quantx` Conda 环境，按部署 README 将锁定依赖安装到该环境；执行根目录 `npm install`，准备 Node 20、Caddy、Docker。
- 根据 `ops/config/compose.development.yaml` 单独启动本地数据服务。
- 从 `ops/config/development.env.example` 创建 `apps/api/.env.development`。
  四个数据服务均指向本机，PostgreSQL/InfluxDB 数据库为 `quantx_dev`。
- 使用独立开发应用密钥、本地模拟账户和 Windows 专用行情 token；不使用 Agent 密钥。
- 为普通测试单独配置 `.env.testing`：PostgreSQL 使用 `quantx_test`，Redis 使用独立
 逻辑库，InfluxDB 使用 `quantx_test`，关闭全部实盘开关。测试不得连接 Windows 数据服务。
- 按部署 README 初始化 InfluxDB token/数据库、运行数据库迁移，并创建 `quantx-dev-pool`。

```bash
./ops/quantx.sh doctor --environment dev
./ops/quantx.sh up --environment dev --profile full --mode paper
./ops/quantx.sh status --environment dev
./ops/quantx.sh up --component monitor
```

**通过条件**：应用进程启动，数据服务可达，Web 显示“开发／模拟交易”；Monitor 的
QMT 为禁用，Mac 没有 QMT/XTTrading 进程。生产数据库和生产 Prefect 中没有开发任务。

## 2. 启停与配置失败

1. 重复执行 up，应拒绝重复启动，已有进程保持运行。
2. 执行 down，再执行 up；确认只停止所记录的开发应用进程，容器和 Windows 生产不受影响。
3. 暂时移走开发配置文件，up 必须失败；还原文件。
4. 临时将开发 DATABASE_URL 指向非回环地址，doctor/up 必须拒绝；还原文件。
5. 尝试 `--mode live`，必须拒绝；即使开发配置误设实盘开关，也不能创建 LiveBroker 连接。
6. 停止本地一个数据服务后启动应用，应报告依赖失败，不自动启动服务、不访问生产兜底。

**通过条件**：无重复 Engine、无孤儿启动进程、无生产账户或交易状态变化。

## 3. 实时行情

配置 `QUANTX_MARKET_DATA_INSTRUMENTS` 为 Windows 已供给的一个标的，在交易时段验收。

1. 确认 Mac 收到快照和持续增量，本地行情源为 remote。
2. 比较源行情时间与 Windows 显示时间，确认传输不刷新原始时间。
3. 断开 Mac 网络，行情应变为不可用；恢复网络后重新同步快照，再恢复 ready。
4. 使用一个未供给标的，连接应拒绝或关闭，不能扩大生产 QMT 订阅。
5. 暂停开发消费或连续建立第二连接，应触发限流/断开，Windows 行情保持正常。

**通过条件**：断流缓存不被视为新鲜行情；Mac 重连不会替换生产 Agent 会话。

## 4. 历史数据与恢复

选择一个 Windows 已有且完成覆盖校验的历史区间：

```bash
./ops/quantx.sh history --instruments 600000.SH --period 1m --start 2026-09-01 --end 2026-09-07
```

日期与标的可换成实际已有数据。检查独立 Data Worker 和本机 Data API 的需求状态，
以及本机 `development_data_export` 中对应分区的 `LOCAL_VERIFIED` 和发布数据版本。
切换前先完成迁移至 0083 并停止旧 `development-data-import` deployment 的日程和运行；
新旧推进者不得并行。成功分区在 Worker 重启后不应反复下载、写入或复核。

1. 盘中已有数据应能导入；缺口只能排队，不能触发盘中 QMT 下载。
2. 盘后缺口补齐后，验证本机行数、范围、SHA256 和回读校验结果。
3. 重复同一请求，数据和来源键不能重复；已完成分片不重复下载。
4. 下载中断后重跑，复用已完成分片；损坏分片必须被校验拦截或重新下载。
5. Mac 离线后再上线，生产补数任务仍可查询，本地调度能够继续导入。
6. 数据源失败或不明空区间必须显示 INCOMPLETE，不能伪装为完整覆盖。
7. 对失败源明确调用 retry 接口后再次导入，普通轮询不得无限重试 QMT 下载。
8. 验证过期导出的重新生成；磁盘不足、源身份不符或覆盖变动时必须拒绝完成。

**通过条件**：所有成功分区都有本机持久化回读证明；失败和缺失可定位，生产交易不受影响。

## 5. 回测、研究与 paper

- 使用导入的数据运行一个已有小范围回测，查看结果清单的 `market_data_versions`。
- 使用同样输入重复回测，比较确定性结果；再断开 Windows，确认已有本机数据仍能使用。
- 研究涉及复权时，确认读取的是通过 schema-v2 证据及本机当前行校验的因子。
  缺少因子或日历时应报告数据不足，不使用默认因子伪装完整。
- 跨 72 小时的历史日线查询应分片完成，不能误用 InfluxDB Core 的单次大范围查询。
- 用既有配置显式初始化冻结 `paper_seed`；没有 seed 时保持 PAPER_SEED_REQUIRED。
- 使用固定行情验证模拟委托、部分成交、撤单和重启恢复，资金与持仓只属于本地 paper ledger。

**通过条件**：`StrategyBase.step()` 和现有 paper 链路正常运行；没有真实订单或券商会话。

## 6. 记录结果

记录 macOS/架构、代码提交号、服务版本、各节通过或失败、脱敏日志路径。
截图放 `.codex_screenshots/`，测试日志放 `.runtime/`；不要记录 token、密码、券商账户或设备密钥。
失败时保留原任务及分片，优先定向修复，不反复全量重导。
