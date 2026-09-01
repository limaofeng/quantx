# QuantX Research

`quantx-research` 是 QuantX 的离线只读研究应用。它不属于常规 API、Engine
或 Worker 运行链路，也不会触发行情同步或写入业务数据库。

## 日级指标与条件交集研究

`study: indicator-study` 使用 `quantx_domain.indicators` 的同一份版本化定义、
指标计算和条件比较。每日快照与历史研究不再各自解释“量比”“连续下跌”或
交叉指标。首版覆盖目录中 `research_supported=true` 的量价指标；财务和
换手率指标暂不具备已核验的历史覆盖，指定它们时会明确拒绝研究配置。

```powershell
uv run --no-sync quantx-research validate --config apps/research/configs/indicator_study_smoke.yaml --market-data-archive .runtime/research-source/full-a-share-v2-20200313-20260729
uv run --no-sync quantx-research run --config apps/research/configs/indicator_study_smoke.yaml --market-data-archive .runtime/research-source/full-a-share-v2-20200313-20260729
uv run --no-sync quantx-research run --config apps/research/configs/indicator_study_v1_20260729.yaml --market-data-archive .runtime/research-source/full-a-share-v2-20200313-20260729
uv run --no-sync quantx-research run --config apps/research/configs/indicator_study_v1_20260729.yaml --resume-run-dir .runtime/research-runs/indicator-study-v1/<failed-run-id>
uv run --no-sync quantx-research render --run-dir <indicator-study-run-directory>
```

`indicator_study_v1.yaml` 默认研究最近五年；`latest` 根据已经持久化的基准日线
解析，不采用电脑当天日期。运行前将实际起止日冻结在 `resolved-config.yaml`
并纳入配置指纹。`indicator_study_v1_20260729.yaml` 固定使用现有 archive 可验证
窗口，包含全部首批量价指标的单独报告及一个明确标注为验收示例的交集报告；
示例阈值不表示最优条件或投资建议。

最小配置如下；`indicator_ids` 指定要生成总体分组报告的指标，`conditions`
指定一个精确条件交集，可以只填其中之一。

```yaml
study: indicator-study
version: v1
indicator_ids: [volume_ratio, change_pct]
conditions:
  - {indicator_id: volume_ratio, operator: between, value: 0.8, value_to: 1.5}
  - {indicator_id: change_pct, operator: lt, value: 0}
universe:
  instrument_type: stock
  exclude_st: false
  include_industries: []
  exclude_industries: []
```

- 数值操作符为 `gte/lte/gt/lt/eq/between`；区间两端包含，二值指标仅允许
  `eq: 0/1`。零是合法阈值。条件排序及重复不改变规范化身份。
- 默认观察未来连续 1–20 个交易日，可配置 `outcomes.horizons`，上限 60。
  主口径是 `C(T+h)/C(T)-1`，辅助口径是 `C(T+h)/O(T+1)-1`；第 1 日的
  辅助口径是次日开盘到次日收盘，不是再持有一天。
- 为保证页面有界读取，单报告最多 12,000 统计行，单次研究预估最多 60,000
  行、结构化统计最多 64 MiB；超出时明确要求拆分配置，不发布无法查看的报告。
- 全市场交易日历对齐，不把停牌后的下一条行情顺延成次日；每个期限独立
  判断收益是否完整，不因缺少 20 日结果排除已有的 1 日结果。
- 单指标按每日截面五分位分组，相同值不拆组；常量截面只有一个有效分组。
  二值指标按真假分组。取值分布的上下限是跨日观测范围，并非固定条件阈值。
- 联合报告比较交集、各单独条件及基准。全部条件先使用共同有效指标样本，
  再为每个收益期限限定交集可观察的日期，各组采用同一日期支持。
- 报告同时提供股票日合并上涨比例、均值和精确中位数，以及日期等权上涨
  比例、均值、同日基准差异、样本股票数和日期数。置信区间针对日期等权的
  配对差异，而非把每个股票日当成相互独立的投票。
- 移动区块 Bootstrap 的块长不短于收益周期；每个端点分别在本次运行所有
  报告/分组/周期/收益起点的完整检验族内做 BH 校正。样本不足保留描述统计，
  不输出推断结论。年度分段和截至数据末日的滚动 12 个月只作稳定性检查。
  Bootstrap 抽样按不超过 32 MiB 目标增量的受控块执行，每块分配前先走物理
  内存保护；配置最多 20,000 次。每个推断行记录实际有效抽样数，避免重复保存
  可由 `1/(n+1)` 推导的字段；`metrics.json.inference_resolution` 汇总配置次数、
  各族有效次数范围、实际 Monte Carlo 分辨率、完整检验族大小，以及孤立最小 p
  值对应的 BH q 值下限。
  默认 1,000 次抽样面对大检验族时分辨率偏粗；“未显著”不得解释为指标无效，
  也不会为得到显著结果而在运行后临时增加抽样次数。
- 历史 ST 和行业分类未核验，配置中 `exclude_st=true` 或行业条件会明确
  拒绝；总体报告只能作为相应当前筛选的参考，不能声称精确匹配。固定股票
  列表或额外上市天数限制也会标记 `coverage.restricted_universe=true`。

研究仍使用现有只读数据适配器、复权覆盖证明和物理内存保护。按完整股票
历史分批计算，按月保存窄投影；报告逐个计算，精确中位数使用临时数值文件，
不将整个全市场宽面板装入内存。完整五年研究建议预留至少 20 GiB 临时磁盘，
运行时间取决于指标数量、股票数量和 Bootstrap 配置。

关系库研究读取使用只读 `REPEATABLE READ` 快照，并在整个读取事务持有复权因子
共享事务锁。复权覆盖只接受 schema-v2 的逐代码审计，且要求当前 10 列因子行数
与内容摘要仍和持久化证据一致；仅有请求级完成状态或旧 schema 不能证明覆盖。
`data-quality.json.dividend_factor_coverage` 会记录
`evidence_schema_version=2`、`verified_code_window_count` 和
`evidence_content_sha256`，便于复核本次冻结样本实际使用的覆盖证据。

统计阶段每完成一个报告，会先在运行目录的 `statistics-checkpoints/` 原子
持久化原始 p 值报告；检查点严格绑定冻结配置指纹、数据指纹、完整
`analysis-sample.parquet` SHA256、样本行数和指标定义版本，还绑定统计引擎
schema/version、关键统计源码的逐文件与汇总 SHA256，以及 Python、NumPy、
Pandas、PyArrow 版本。上述身份同时显示在 manifest；源码或依赖身份变化时
拒绝复用旧检查点。旧 manifest 缺少统计引擎身份时，仅允许在检查点目录不存在
或严格为空、没有任何统计进度、表格、报告、临时/最终产物或相关 artifact 索引，
且旧统计输入其余字段与当前冻结输入完全一致时升级并从头重算；不因旧
`statistics_input` 是否存在而放宽派生产物检查，避免新旧统计世代混表。
升级会在 manifest 和最终报告警告中留审计记录。只有全部报告收集
完成后，才统一对完整检验族做 BH 校正并生成最终产物。`failed_resource` 或
普通 `failed` 运行可使用上面的 `--resume-run-dir` 原目录恢复：恢复会重新核验
冻结配置、data-quality artifact 哈希及其中完整且内部一致的 schema-v2 逐代码复权
覆盖身份、Parquet footer、完整样本哈希和已有检查点，不读取行情、不重算指标或
收益。旧 schema、缺少证据摘要、虚假 complete 或代码集合不一致均拒绝恢复。
缺少检查点时只从冻结样本重建临时月分区；已完成报告直接复用。
恢复命令不能同时传 `--market-data-archive` 或 `--output-root`，且仍严格执行配置
中的物理内存保留门槛。运行目录使用包含主机、PID、进程启动时间和 attempt id
的独占租约防止并发写入；`running` 只有在租约证明确切原进程已不存在时才允许
安全接管，活动进程、其他主机或无法核验的租约一律拒绝。Ctrl+C 会先把本次
attempt 收敛为 `failed`，硬终止留下的 stale lease 则由下一次恢复验证后替换。

`indicator-study` 在统计身份和三项恢复输入（冻结样本、resolved config、数据质量）
共同写入 manifest 后，将 `data-quality.json` 视为不可变恢复证据。后续恢复警告
和逐次运行物理内存遥测只进入最终报告或 manifest，不再重写该文件；即使进程在
最终 artifact 重建前异常退出，旧 manifest 中的恢复输入 SHA256 仍然有效。重新
渲染成功运行时先核验已索引的 `metrics.json`、运行身份和完整报告族，并且只更新
`report.html` 的 artifact 项，不会为其他被外部修改的文件重算并认可新哈希。

数据库读取与 CPU 阶段分离：完成股票特征及来源证据读取后立即退出只读连接，
再执行全局交易日对齐、前向收益和统计，避免长时间闲置事务在退出时超时。
计算指标时同样传入基准交易日历；物理缺失的一根行情与显式不可用行情保持
一致，不把缺失交易日两边的数据拼成一个完整滚动窗口。

产物包括一个不可变运行身份、配置/数据指纹、`analysis-sample.parquet`、
包含 `reports[]` 的 `metrics.json`、逐报告 CSV、质量报告及 HTML。每个报告
有独立 `report_id`，包含定义版本、规范化条件、覆盖、分组和多周期统计。
页面只查找已有产物，不创建研究任务，也不显示校准后的个股预测概率。

## 次日上涨概率模型研究

`study: next-day-selection` 是独立的手工训练入口。它复用认证日级指标，构造
横截面模型因子，并预测 T+1 开盘到收盘收益是否大于零。它不由 Worker 自动
训练，不发布策略或交易信号。

```powershell
uv run --frozen quantx-research train-next-day-selection --config apps/research/configs/next_day_selection_v1.yaml
```

默认配置使用五年窗口，最后 12 个月为冻结测试集；之前 48 个月按至少
30 月训练、6 月校准、1 月验证进行逐月 walk-forward。Logistic 与 LightGBM
按验证 Brier 选择，差异不超过 0.5% 时优先 Logistic。Platt 是默认校准器；
只有正样本至少 20,000 且 Isotonic 相对改善 Brier 至少 1% 时才使用 Isotonic。

成功运行只生成 JSON、LightGBM 文本和 Parquet 安全产物。模型登记会重新核验
manifest 的文件大小与 SHA-256，拒绝路径链接、Pickle/Joblib、非有限模型参数和
越界校准器；暴露到研究中心的指标与数据质量经过白名单投影，不包含本地数据路径。

训练输出安全 JSON、LightGBM 原生文本、Parquet 和哈希 manifest，禁止 pickle、
joblib 等 Python 对象反序列化。冻结测试至少记录 Brier/BSS、Log Loss、ECE、
ROC AUC、PR AUC、日期等权 Top20/Top50、区块 Bootstrap 区间和年度稳定性。
模型只能在研究中心人工登记和变更阶段；历史 ST、行业、退市状态未完整时，
即使效果门禁通过也只能用于 CANDIDATE/SHADOW，不能晋级 ACTIVE。

完整契约见
[`docs/次日上涨概率选股软件_完整功能设计方案.md`](../../docs/次日上涨概率选股软件_完整功能设计方案.md)。

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
和 staging 估算在量价事件研究中会写入 `data-quality.json`，最终摘要也写入
manifest；indicator-study 按上文不可变恢复证据规则仅写 manifest。资源
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
  内部一致且非停牌行计算的历史与边界覆盖，并保存复权因子请求覆盖证明和
  staging 资源估算；量价事件研究还保存物理内存遥测，indicator-study 的逐次遥测
  保存在 manifest；
- `report.html`：只负责展示上述结构化事实。

完整设计与研究口径见
[`docs/plans/离线量价事件研究应用实现方案.md`](../../docs/plans/离线量价事件研究应用实现方案.md)。
