# QuantX Research

`quantx-research` 是 QuantX 的离线只读研究应用。它不属于常规 API、Engine
或 Worker 运行链路，也不会触发行情同步或写入业务数据库。

默认研究配置：

```powershell
apps/research/configs/volume_shock_v1.yaml
```

2026-07-30 全市场正式运行使用冻结窗口配置，避免 `latest` 随运行日期或
最新日线漂移：

```powershell
apps/research/configs/volume_shock_v1_20260730.yaml
```

仓库同时提供一个固定 2 只股票、固定短区间的真实数据冒烟配置：

```powershell
apps/research/configs/volume_shock_smoke.yaml
```

安装工作区依赖后，可以从仓库根目录运行：

```powershell
uv run quantx-research validate --config apps/research/configs/volume_shock_v1.yaml
uv run quantx-research run --config apps/research/configs/volume_shock_v1.yaml
uv run quantx-research render --run-dir <run-directory>
```

本次全市场正式研究直接运行：

```powershell
uv run quantx-research run --config apps/research/configs/volume_shock_v1_20260730.yaml
```

如果 InfluxDB 入库暂时不可用，可以把已有 durable transfer chunks 与后续
QMT 请求统一发布为只读 archive，再让研究直接读取该 archive。这个路径不
修改原 backfill state，也不写 InfluxDB；缺失批次仍通过 PostgreSQL 的
durable request/transfer 审计链与 QMT Agent 取得并终结：

```powershell
uv run --package quantx-research python -m quantx_research.source_backfill `
  --state-file .runtime/research-backfill/full-a-share-v2-20200313-20260729.json `
  --archive-root .runtime/research-source/full-a-share-v2-20200313-20260729

uv run --package quantx-research quantx-research validate `
  --config apps/research/configs/volume_shock_v1_20260730.yaml `
  --market-data-archive .runtime/research-source/full-a-share-v2-20200313-20260729

uv run --package quantx-research quantx-research run `
  --config apps/research/configs/volume_shock_v1_20260730.yaml `
  --market-data-archive .runtime/research-source/full-a-share-v2-20200313-20260729
```

正式 archive loader 只接受 `status=completed` 且
`expected_request_count=effective_job_count=180` 的 ledger；它会重新校验
job plan、汇总数、证券总体指纹、每个 request manifest、chunk 路径边界、
gzip 大小、SHA256、记录数、代码/周期/日期范围和唯一 `(code,time)` 键。
任意内部日期缺口、重复覆盖、运行期间 ledger 漂移都会直接失败。行情按
Worker 入库口径做相同的小数归一，后续仍使用 PostgreSQL 的证券元数据、
复权因子与因子覆盖证明。ledger、request、chunk 证据及实际查询截止日会写入
`manifest.json` 和 `data-quality.json`；因此该路径不依赖 InfluxDB 写入，
但仍保留默认 InfluxDB 数据源作为兼容路径。

默认全市场口径是 QuantX 当前证券主表中代码符合
`^\d{6}\.(SH|SZ)$`、且上市/到期区间与分析窗口相交的全沪深 A 股（暂不含
北交所）。
首次运行会执行较长时间的无缓存只读扫描；可以先运行 smoke 配置验证
PostgreSQL、InfluxDB、统计与报告整条链路。`universe.stock_codes` 省略时
使用该总体，填写时使用固定研究样本。证券主表是当前时点快照；在尚未补齐
历史成分、历史 ST 与退市状态前，这不等同于无生存者偏差的历史全市场样本，
正式报告必须披露这项限制。

正式 `validate` / `run` 在读取全量日线前，会从 PostgreSQL
`market_data_request` 验证复权因子覆盖。每个目标股票以及
`universe.benchmark_code` 都必须被一个或多个已完成的
`qmt-get-divid-factors-v1` 请求完整覆盖；请求还必须满足分片数一致。因子表
是稀疏表，某个代码零行是合法结果，但“没有已完成请求证据”不是合法结果。
本地 campaign state 用于续跑，正式 gate 使用数据库中的持久化请求证据，
避免把 state 文件路径耦合进研究程序。

## 全量运行的内存边界

正式 `validate` / `run` 使用磁盘 staging，而不是把全市场宽面板一次装入
内存：

1. 每个股票 batch 读取完整历史，立即完成标准化、时点可得复权、质量审计
   和特征计算，再写入临时 Parquet。
2. 使用“全部股票交易日与基准交易日的并集”计算个股和沪深 300 outcome；
   全市场等权收益在资格过滤前按日期、期限和收益口径累计 `sum/count`。
3. 第二遍补齐 market excess，并在每只股票的完整历史内计算配置冷却及
   5/20 日敏感性身份。股票不会跨 staging partition，冷却状态不会在 batch
   边界丢失。

最终 `analysis-sample.parquet` 由 PyArrow writer 按股票 batch 流式合并成
原有单文件，`events.parquet` 仍按 `event_date, stock_code` 排序。两个正式
Parquet 都先写同目录临时文件，关闭、核对行数后再原子替换；资源保护或写入
异常不会留下可被误认成完整产物的截断文件。统计阶段不会加载 71 列全样本：
事件统计只加载主身份或 5/20 日冷却实际保留的事件，正常量比较逐 horizon
加载必要 outcome 列，稳健性先过滤候选。回归按不超过 65,536 行的 Parquet
块多遍累计全局中心、日期固定效应 normal equations 和双向聚类 score，不再
物化全样本 17 列，也不构造全样本股票/日期字符串标签。

运行器只检查物理内存，不把 Windows pagefile 当作可用容量。
`runtime.minimum_available_memory_gib` 是每个有界块预计增量之外必须保留的
物理内存，正式配置为 8 GiB。后台每 0.25 秒采样并锁存第一次 reserve breach；
主线程在每个不超过 65,536 行的可控块前后检查，低于门槛会生成明确的
`failed_resource`。这不是操作系统级的进程内存上限，但单次不可中断分配已被
限制在一个有界块内。后台 RSS、最低可用物理内存、reserve breach、分阶段峰值
和 staging 估算会写入 `data-quality.json`，最终摘要也写入 manifest；资源
失败仍保留已知的真实样本数、事件数、数据指纹和质量证据。临时
`.staging-*` 在退出时自动清理。正式全量运行建议额外预留至少 20 GiB 临时
磁盘。

复权 gate 通过后，`validate` 仍会完成三遍数据构造，只是不执行 Bootstrap、
回归和报告；它不是轻量连接检查。

## 正式研究口径

- `analysis-sample.parquet` 保留所有满足历史、时点与未来收益完整性要求的
  阈值前股票日；`events.parquet` 仍只包含 `RVOL >= 1.5` 且经过冷却的冲击
  事件。主对照和主回归不得只使用事件文件。
- 正常量对照在运行前固定为 `0.8 <= RVOL < 1.2`。每个收益口径、周期、
  基准和事前价格位置内，先按交易日分别等权聚合冲击组和正常组，再计算
  `shock - normal`；同时报告各位置差值和高位减低位交互。
- 主对照和主回归中的 shock 身份与 `events.parquet` 一致，均采用配置冷却
  后事件；连续异常量日不会绕过冷却重复进入主事件组。结果同时保存 5 日和
  20 日冷却的正常量对照敏感性。
- 置信区间与 p 值使用按完整有序交易日的 circular moving-block
  bootstrap。区块长度由 `statistics.moving_block_length` 预注册，实际不会
  短于收益周期；有效独立日期少于 `minimum_inference_dates`（正式配置为
  30）时不做推断。焦点对照和回归交互项分别在各自预注册检验族内做
  Benjamini-Hochberg 校正。
- 主回归使用完整阈值前样本，以冲击 dummy、中心化的 T-1 价格位置及交互项
  为核心，只加入 T-1 可得且已中心化的动量、波动率和流动性控制。因变量按
  沪深300超额、全市场等权超额、绝对收益的固定顺序确定，不根据结果或样本
  覆盖临时切换。
- 成交额放大和成交量 z-score 稳健性样本直接从完整阈值前样本定义，不与
  RVOL 冲击事件取交集。
- `event_direction` 使用 T 日收盘收益，仅用于事件发生后的描述性分组，
  不是 T-1 可得的事前筛选条件。

这些结果只描述历史样本中的条件关联，不识别因果效应，也不构成投资建议。

## 运行产物

运行结果保存在 `.runtime/research-runs/`：

- `analysis-sample.parquet`：完整阈值前分析样本；
- `events.parquet`：冷却后的异常放量事件；
- `metrics.json`：分组描述、正常量对照、回归、稳健性与推断字段；
- `tables/`：对应的扁平 CSV；
- `data-quality.json`：原始异常计数，以及仅由去重后有效、正值、OHLC
  内部一致且非停牌行计算的历史与边界覆盖，并保存复权因子请求覆盖证明、
  staging 资源估算和物理内存遥测；
- `report.html`：只负责展示上述结构化事实。

完整设计与研究口径见
[`docs/plans/离线量价事件研究应用实现方案.md`](../../docs/plans/离线量价事件研究应用实现方案.md)。
