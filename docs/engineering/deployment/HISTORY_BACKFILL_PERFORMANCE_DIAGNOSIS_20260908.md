# Windows 历史补数慢与 macOS 等待源数据：诊断及优化交接

## 1. 结论与任务边界

诊断日期：2026-09-08，北京时间。代码基线：`52ce6f281778fa9312a96c613c967cd9ae699eb5`。

macOS 请求一个标的一天的分钟数据，Windows 导出任务持续 `WAITING_SOURCE`。
现场已确认三个相互叠加的因素：

1. 本机 XTData SDK 的下载完成轮询有 `sleep(10)` 分支，运行中的历史子进程确实落在该行。
   QuantX 每 20 个标的调用一次下载，300 个标的拆成 15 个工作单元，重复支付粗粒度等待成本。
2. API 把上传结束后的 `UPLOADED/PROCESSING` 也视为 QMT 派发通道占用。
   样本任务入库、回读阶段又阻挡后续任务约 52 秒。
3. 生产请求严格优先于开发请求，且没有等待时长补偿。本轮生产计划共 50 个批次，
   开发小请求可能持续被后续生产批次压后。

本次完成诊断，没有修改 SDK、业务代码、派发优先级或实盘门禁，也没有为诊断启动
第二个 QMT Agent 或发送订单。后续优化在新任务实施。不得把本报告当作已优化完成的验收记录。

## 2. 运行拓扑与问题范围

- Windows：`production/full/live`，唯一 QMT Agent；应用使用 `quantx` Conda 环境，
  QMT 使用 `xtquant-demo`。历史子进程是 Agent 管理的 XTData 适配器，不是第二个 Agent。
- macOS：独立 `dev/paper`，只通过专用行情凭证请求行情和历史导出，不连接生产数据库。
- 开发触发的缺口下载只在允许窗口派发；北京时间交易日 16:00—次日 08:30，以及
  有交易日历依据的非交易日。生产任务优先，开发原生补数并发为一。
- PostgreSQL、Redis、InfluxDB、Prefect 由外部管理。当前生产启动器解析 WSL 地址，
  四项服务使用直连路径；WSL IP 会变化，不应把本次地址作为永久配置。
- 开发数据接口沿用压缩 JSON、摘要和持久化回读验证，不因优化传输速度而改变数据口径。

本次故障表现属于历史任务端到端延迟。`WAITING_SOURCE` 不等于文件损坏、QMT 崩溃，
也不等于 macOS 已进入本地导入；需要检查关联源任务所在阶段。

## 3. 可定位的现场对象

| 对象 | 标识或范围 |
| --- | --- |
| macOS 请求 | `600000.SH`，`2026-09-07`，`1m`，`adjustment=none` |
| 开发导出 ID | `d878355bb02ae1e16fe15b6a53df7ca04c54fb2d39aa51ad3c7dc2c061fe5288` |
| 关联开发源请求 | `cfbcaed0-1385-413d-9abe-0ba0d8268e81` |
| 用于拆解耗时的生产源请求 | `6c9283a8-b8c4-4665-ba83-c63b9e406b46` |
| 生产样本范围 | 300 个标的，`2026-09-08`，`1m` |
| 同组日线请求 | `ba7d9258-b917-4462-b12c-552dbd9b4757` |
| Prefect 生产 Flow Run | `01a07f70-3c38-7c5a-8f50-7e787eeaea0a`，`onyx-wapiti` |
| Flow 名称 | `每日市场数据同步` |

生产 Flow 于 15:04:59 启动，15:05:00 日志记录：
`batches=50 periods=['1m', '1d'] range=20260908..20260908 concurrency=2`。
这是整轮计划的批次数，不是某一时刻数据库内同时排队的请求数；Worker 的并发二也不表示
存在两个原生历史下载通道。

开发源请求在 16:00:00 创建。16:11 左右的检查仍为 `QUEUED`，关联设备未撤销，
会话在线、心跳为 `READY`，允许窗口已打开。它的等待不能归因于未到盘后、错误设备或
macOS 本机存储；本报告不声称该任务现在仍是这个状态，接手时须重新查询。

## 4. 生产样本时间线

下表已将数据库、Prefect 的 UTC 时间统一转换为北京时间。
数据库部分字段为无时区的 UTC 时间，查询时不可混用数据库会话本地时区。

| 北京时间 | 事件 | 证据 |
| --- | --- | --- |
| 16:02:29.100 | 创建生产分钟请求，进入排队 | `market_data_request.created_at`、Flow 日志 |
| 16:05:08.178 | Flow 从排队变为等待 Agent 进度 | Flow 阶段日志，接近派发时刻，不是精确 SDK 进入时间 |
| 16:05:18.603 | 首个分片被服务端接收 | `market_data_transfer.received_at` |
| 16:07:48.290 | 第 15 个分片被接收 | 同上 |
| 16:07:49.148 左右 | 进入传输校验 | Flow 阶段日志 |
| 16:07:50.241 | 进入入库 | Flow 阶段日志 |
| 16:08:27.083 | 进入回读校验 | Flow 阶段日志 |
| 16:08:41.283 | 源请求完成 | `market_data_request.completed_at` |

| 阶段 | 约耗时 | 说明 |
| --- | --- | --- |
| 创建至观察到 Agent 派发 | 159 秒 | 等待其他生产请求 |
| Agent 派发至上传结束 | 161 秒 | 包含 SDK 下载等待、读取、编码、分片与上传 |
| 校验、入库、回读收敛 | 52 秒 | 其中传输校验约 1 秒，入库约 36.8 秒，回读约 14.2 秒 |
| 端到端 | 372 秒，即 6 分 12 秒 | 不能把全部耗时算作下载或 HTTP 上传 |

样本共有 15 个分片，压缩字节总数 **1,362,461**，每片 4,840 条记录（包括摘要）。
相邻分片接收间隔约 10.6 秒，首尾接收跨度约 149.7 秒。
此前“5 分 19 秒”指创建至最后一个分片到达，未包含后续约 52 秒入库校验。

**不能用 1.3 MB / 150 秒推导纯 HTTP 带宽。** 分片是一边生成一边上传，接收间隔混入了
下一工作单元的 SDK 等待；也没有独立记录每个 PUT 的起止时间。

## 5. 已确认原因及证据边界

### 5.1 XTData SDK 的 10 秒完成轮询

本机文件：`C:/Users/limao/miniconda3/envs/xtquant-demo/Lib/site-packages/xtquant/xtdata.py`。
函数：`download_history_data2`，约第 2001—2021 行。

该函数调用 `client.supply_history_data2(...)`，以 `on_progress` 更新完成状态。
在 `if not result` 分支中使用 0.1 秒轮询，另一个分支则执行：

```python
while not status[0] and client.is_connected():
    # 打印进度后，等待十秒才再次检查完成状态
    _TIME_.sleep(10)
```

诊断时文件 SHA-256：
`a6e53353a33f0388a57a9f99c345db30021bcb5583b2340e3e6a3f1c89519bb7`。
未从本机发行包元数据取得可确认的版本号；后续不能假设其他机器或其他 SDK 版本具有相同实现。

运行采样使用 py-spy，45 秒、10 Hz、包含 idle，得到 449 个样本、0 个采样错误：

| 栈顶位置 | 样本数 | 近似采样时长 |
| --- | ---: | ---: |
| `download_history_data2 (xtquant/xtdata.py:2021)` | 232 | 23.2 秒 |
| IPC `_recv_bytes`，历史子进程等待消息 | 213 | 21.3 秒 |
| pandas、读取或其他位置 | 4 | 0.4 秒 |

采样来自同一运行中的历史子进程、后续生产批次执行期间，不是对第 4 节样本请求的完整追踪。
它确认现场确实执行了 SDK 的十秒等待，不能把这个分布当成所有请求的精确阶段占比，
也不能把 IPC 等待全归因于某一种派发阻塞。

QuantX 在 `historical_worker.py` 中把分钟数据按 20 个标的、一天划为工作单元。
300 个标的需要 15 次下载；`broker.py` 每个单元调用 `download_market_data(..., incrementally=False)`。
实测约 10.6 秒一个分片，与每个单元重复经过该等待相符。只含少量记录的日线批次也出现同样节拍，
进一步说明记录体积不是主要解释。

**确定项**：SDK 完成检测粒度粗，现场在等待行消耗大量时间。
**未知项**：每次原生数据何时实际准备好、回调何时宣布完成。
只有补齐这两个时间点，才能区分必要下载时间与完成后仍在睡眠的额外等待。
不能承诺把每个单元的十秒全部消除。

### 5.2 入库状态与原生派发占用耦合

`apps/api/src/quantx_api/agent_api.py` 中：

```python
_MARKET_DATA_ACTIVE_DISPATCH_STATUSES = frozenset(
    {"DELIVERED", "RECEIVING", "UPLOADED", "PROCESSING"}
)
```

`_next_market_data_request()` 检查同设备存在这些状态的请求即停止派发。
这使原生子进程已结束上传后，仍需等待服务端持久化和回读完成。本样本的额外占用约 52 秒。

这项串行约束确实存在，但不能未经验证就只删除两个状态。需要确认 Agent 的请求结束协议、
原生任务是否真正释放、待入库积压、磁盘配额、重连恢复及请求幂等性，才能安全分离占用状态。

### 5.3 严格生产优先使开发小请求缺少等待上界

派发排序为：
`order_by(MarketDataRequest.development_only, MarketDataRequest.created_at)`。
生产 `false` 总排在开发 `true` 前面，创建时间只在同一优先级内部生效。

当前生产 Flow 持续补充后续批次，开发请求即使更早创建也不能越过它们。
这是已实现的生产优先规则，不是 FIFO 故障；但没有公平性预算或等待上界时，
用户看到的 `WAITING_SOURCE` 无法区分“还要等一个批次”与“可能要等整轮”。
尚未取得完整等待时间分布，不应按单批样本直接给出整轮精确 ETA。

### 5.4 次要耗时与尚未证实的解释

- 入库约 37 秒、回读约 14 秒是真实的次要成本。目前写入批量上限为 2,000 条，
  但尚未拆分 DataFrame 转换、序列化、HTTP 写入、持久化可见性等待与查询扫描各自耗时。
- Agent 上传并发为二，限速为 8 MB/s、突发预算 1 MB；这不能解释 90 KB 分片之间十秒级间隔。
  仍需独立 PUT 起止时间才能完全排除个别上传长尾。
- 历史工作单元之间有交易健康 QoS 检查。检查时心跳显示 `historyWorkload=running`、
  原因为空；不能据此断言此前从未被快照、回报、交易命令或重对账暂停。
- 此前 Windows 回环转发导致 Redis、PostgreSQL 延迟偏高的问题已通过 WSL 直连处理。
  本次发现了独立的 SDK 等待证据，没有证据表明此次慢补数是 Redis 崩溃或同一网络问题复发。

## 6. 新任务建议实施顺序

### 阶段一：建立可对照的阶段计时，优先处理 SDK 完成等待

1. 在项目控制的历史适配层记录工作单元开始、原生调用返回、完成回调、读取结束、编码结束，
   以及每片上传开始/结束。以 request ID、unit index、chunk index 关联，使用单调时钟计算耗时。
2. 对照短轮询或完成事件方案，严格保留下载失败、回调异常、断线、超时、返回值和数据校验语义。
   SDK 版本差异必须明确处理；不要直接修改本机 `site-packages` 作为唯一交付，也不要全局替换
   `time.sleep` 或引入不可追踪的运行时补丁。
3. 首先验证一个工作单元，再验证同一 300 标的批次。区分 QMT 本地已缓存与真正缺失数据，
   避免把“第二次下载命中缓存”误认为优化收益。
4. 不先扩大原生调用并发或把 20 标的直接扩大到 300；工作单元大小同时承担超时和暂停边界。

### 阶段二：分离原生占用与服务端入库占用

1. 定义可验证的原生释放事件。收到全部字节、完成清单冻结、Agent 确认结束三者的含义必须分清。
2. 允许有限度的下载/上传与上一请求入库重叠，并设置待入库请求数、压缩/解压字节和磁盘上限。
3. 保留源请求状态和最终完成门槛：上传完成绝不等于数据已持久化或开发导出 READY。
4. 验证重连、重复完成通知、入库失败、取消及服务重启后，不会同时启动两个原生任务或丢失数据。

### 阶段三：明确开发请求等待规则并改进状态说明

1. 保留生产优先及交易健康门禁。先提供原因分类，例如等待盘后窗口、生产原生任务占用、
   服务端入库积压、Agent 健康不满足、交易日历缺失；这些是拟议能力，当前接口未完整提供。
2. 排队时长与各阶段耗时分开显示。ETA 使用近期同类任务分布和区间，缺证据时明确不可估计。
3. 若要为开发小请求引入老化、空隙派发或时间预算，需明确它如何影响原方案的严格生产优先，
   不要把“保留生产优先”暗中改成轮流执行。
4. 已有持久化数据仍应优先走受控导出；缺失覆盖证明时不能假装完整，也不能无条件重复下载。

## 7. 必须保留的约束与验收

| 范围 | 验收要求 |
| --- | --- |
| SDK 等待 | 同缓存条件对照，记录回调完成至函数返回的延迟分布；不再无理由呈十秒阶梯 |
| 数据正确性 | 前后记录数、键摘要、实际覆盖及数据口径一致；失败、空数据和部分成功不得虚报完成 |
| 单实例 | 唯一 Agent，原生历史并发边界明确；不共享 macOS 业务库或券商凭据 |
| 重叠执行 | 可证明下一次原生工作已与上一请求入库重叠；积压有界，失败和重启可收敛 |
| 交易安全 | 不降低快照新鲜度、协议、实盘开关、白名单、对账及 QoS 约束；不以发真实订单验收 |
| 开发窗口 | 保留交易日 16:00—次日 08:30 门禁；日历缺失时不派发开发缺口下载 |
| 优先级 | 记录开发等待上界或明确无上界；如调整严格生产优先，必须有明确批准的规则 |
| macOS | 原请求能进入 READY，分片下载校验、幂等导入、断点恢复、本地查询后再验收回测 |
| 回测 | 必要日历、复权因子或历史基础信息缺失时明确数据不足；记录所用数据版本 |

性能目标需在阶段一基线测量后设定。本报告不预先承诺某个秒数或倍数，不能通过取消持久化
校验、放宽行情新鲜度或抢占生产交易工作来取得漂亮数字。

## 8. 代码、证据与接手清单

代码入口（行号可能随后续提交变化，优先搜索函数名）：

- `apps/qmt-agent/src/quantx_qmt_agent/miniqmt/data/data_manager.py`：`download_market_data`。
- `apps/qmt-agent/src/quantx_qmt_agent/broker.py`：`_iter_market_data_records_unbounded`。
- `apps/qmt-agent/src/quantx_qmt_agent/historical_worker.py`：工作单元拆分、checkpoint、分片准备。
- `apps/qmt-agent/src/quantx_qmt_agent/runtime.py`：`_wait_for_history_dispatch`、
  `_put_market_data_chunk`、`_finalize_market_data_upload`、历史子进程消息循环。
- `apps/api/src/quantx_api/agent_api.py`：`_next_market_data_request`、活跃派发状态集合。
- `apps/worker/src/quantx_worker/prefector/flows/durable_agent_flows.py`：阶段上报及入库、回读。
- `packages/infrastructure/src/quantx_infrastructure/services/market_data_transfer_ingestion.py`：
  `_persist_validated_records`、`ingest_uploaded_bar_request`。
- `apps/worker/src/quantx_worker/prefector/flows/development_data_export_flow.py`：源请求与导出收敛。
- `packages/infrastructure/src/quantx_infrastructure/services/development_history_window.py`：开发下载窗口。

本机原始证据位于忽略目录，不随 Git 推送。新任务若换机器，应以本报告中的脱敏汇总接手，
确需原始文件时再从 Windows 读取，不复制配置文件、账户信息、设备密钥或数据库密码。

| 文件 | 内容 |
| --- | --- |
| `.runtime/historical-child-profile.txt` | 历史子进程原始采样栈 |
| `.runtime/historical-child-profile.log` | 45 秒、10 Hz、449 样本、0 错误的采样元信息 |
| `.runtime/history-diagnostic-flow-log.json` | 生产 Flow 08:00 UTC 后的一页日志，最多 200 条，并非完整历史 |
| `.runtime/history-diagnostic-runs.json` | 诊断查询时相关 Flow Run 摘要 |

只读复查可按第 3 节 ID 查询 `development_data_export`、`market_data_request`、
`market_data_transfer`；时间比较采用 UTC，分片按 `chunk_index` 排序。检查实时心跳时仅取
`historyProgress`、`historyWorkload`、`historyWorkloadReason` 等诊断字段，避免输出完整设备详情。

接手时先确认代码版本、SDK 文件指纹、当前请求终态及是否有其他任务改变实现。
使用 `quantx` Conda 运行验证工具；SDK 调用仍留在 `xtquant-demo` 与现有 Agent 管理的历史子进程内。
本次临时采样器安装于 `.runtime/tools/py-spy`，没有加入项目依赖或券商环境。
生产测试需要通过统一入口管理进程，不独立重启或强杀共享券商运行时。

推荐新任务从“阶段一的完成回调与返回时间计量”开始；完成最小闭环后再处理派发与公平性，
不要同时改 SDK、批次大小、并发和优先级，导致无法判断收益来源。
