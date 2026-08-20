# miniQMT 显式全推订阅优化方案

## 1. 目标与结论

QMT Agent 的全市场行情源统一改为一次
`subscribe_whole_quote(a股代码列表 + 沪深指数代码列表)`，不再向 XTData 传入
`["SH", "SZ"]`。约 5,800 个代码是同一次 whole-quote 调用的一个列表参数，
不是 5,800 路单股订阅。

这项改动不能只替换调用参数。显式列表不会自动纳入次日新增代码，因此代码表、
原生订阅、快照和增量流必须作为同一个带版本的数据源管理。

## 2. 已有实测基线

2026-08-19 的隔离 ABBA 测试每轮先预热 10 秒、再统计 90 秒，且任一时刻只有
一个订阅：

| 方式 | 平均回调数 | 回调平均标的数 | 估算解码记录数 | 90 秒进程 CPU | p50 / p95 / p99 源时间年龄 |
| --- | ---: | ---: | ---: | ---: | --- |
| `['SH', 'SZ']` | 2,715 | 66.63 | 180,912 | 3.586 秒 | 109.55 / 966.54 / 1238.96 ms |
| 5,821 个 A 股和指数 | 2,528 | 54.57 | 137,937 | 2.672 秒 | 115.23 / 987.25 / 1226.33 ms |

显式列表将测量期 CPU 降低约 25.5%，估算解码记录数降低约 23.8%；它没有改善
行情源时间年龄。大列表的原生订阅建立耗时约 1.90 秒，而市场代码订阅约
0.002 秒，所以换表必须低频、串行并继续运行在 XTData control worker 中。

## 3. 权威订阅集合

- 代码来源固定为 miniQMT 板块 `沪深A股` 与 `沪深指数` 的并集。
- 只保留后缀为 `.SH` 或 `.SZ` 的合约，规范化、去重并稳定排序。
- ETF、债券、期权、港股等不因属于 SH/SZ 市场而被带入。
- 回调入口仍执行同一代码白名单过滤，作为 SDK 异常数据的防御边界。
- 不按代码拆成多路原生订阅，也不在失败时回退到 `["SH", "SZ"]`。

## 4. Universe 与原生订阅生命周期

每个原生订阅绑定一个不可变的 active universe：

- `trading_date`
- `codes` / `code_set`
- 涨跌停价与最小价位 metadata
- 稳定 fingerprint
- 单调递增的 source generation

运行中发现次日代码表后：

1. 若代码集合不变，只原子更新 metadata 和日期，不重订。
2. 若代码集合变化，将新集合保存为 pending universe；active universe 继续与
   当前原生订阅一致。
3. source generation 递增，现有 capture supervisor 将行情流标为 `STALE`，
   触发连续性重建。
4. supervisor 先使旧流失效，再取消旧 native subscription；取消失败时
   fail-stop，禁止建立第二路。
5. 取消成功后清理旧 capture source，激活 pending universe，并只建立一次
   `subscribe_whole_quote(list(active.codes))`。
6. 使用新 universe 重新执行全量快照、收敛屏障和确认帧，完成后才回到
   `READY`。

旧订阅的回调闭包携带 epoch。退订开始即使 epoch 失效，SDK 延迟送达的旧回调
不得污染新 source。

代码表刷新失败时保留当前 active source，并限频重试；不能高频创建刷新线程，
也不能用空集合覆盖当前 source。

## 5. 快照与连续性约束

- miniQMT 只保证订阅后先返回当前最新全推数据，不保证首个 callback 一次覆盖
  全部请求代码。
- 保留现有 callback 收敛和分批 `get_full_tick` 兜底。
- `whole_market_codes()`、快照白名单和回调过滤在任何时刻都必须读取 active
  universe，不能提前读取 pending universe。
- 切换期间状态必须是 `STALE/SYNCING`；新 SNAPSHOT 提交并完成连续性屏障前，
  不得发布新 universe 的 DELTA。
- 当前通道的业务语义是“最新状态收敛”，不是可重放的逐 tick 日志；若未来要求
  每一条 tick 都不丢，需另建持久化 tick 日志，不能依赖 Redis Pub/Sub。

## 6. 验收标准

自动化测试必须证明：

- native 调用收到的是 A 股和指数显式列表，且只有一次订阅。
- 同日相同代码表不改变 source generation。
- 跨日代码增删只产生 pending universe；旧订阅期间 codes、过滤和快照仍使用
  active universe。
- 严格先退订旧 source，再订阅新 source；旧 epoch 回调被丢弃。
- 刷新失败保留 active source，并受重试间隔限制。
- 新增代码同时进入新 SNAPSHOT 和后续 DELTA，DELTA 不超出 SNAPSHOT universe。
- 现有 whole-market 流、API/Redis/Engine 集成测试继续通过。

实机验收必须：

1. 先通过统一运维入口停止 Agent；不得手工并行启动第二个 Agent。
2. 每个方案只保留一个 native subscription，先预热 10 秒，再统计至少 90 秒。
3. 对比订阅是否成功、代码覆盖率、回调数、解码记录数、CPU、内存及
   p50/p95/p99；延迟仍按 tick 源时间与本机接收系统时间之差统计。
4. 完成后按 Dev `full/live` 标准整体恢复，确认唯一 QMT Agent `ready`、协议
   `1.1`、实盘能力门状态正确，行情流在观测窗口内保持 `READY` 且无持续重同步。

## 7. 非目标

- 不把 100 只候选股订阅当作打板全市场监控的替代方案。
- 不宣称过滤能改善券商服务器到本机的源延迟；本次收益是减少无关证券在 SDK、
  Python 和下游链路中的处理成本。
- 不为旧市场代码订阅保留兼容或降级分支。
