# 做 T V3 回测加载器复验报告

- 报告日期：`2026-08-24`
- 机读证据：[JSON](t-trade-v3-loader-revalidation-20260824.json)
- 判定：**正式 20 交易日因果回放仍为 BLOCKED，未启动正式执行。**

本报告只记录本轮回测历史数据加载器的复验；不改写既有验收报告，也不以短窗口或数据回放替代 PAPER 验收。

## 已复验的加载器边界

- QMT 的 `tick`、`1m` 下载和读取统一使用 `YYYYMMDD000000..YYYYMMDD235959`；`1d` 保持日期格式。
- 空 Tick 只能在同一精确交易日同时存在 `COMPLETED`、`verified` 的 Tick `XT_DATA_NO_ROWS` 与 `1d` `XT_DATA_NO_ROWS` 时成立。任一同日 `1d` 非零或异常证据均为反证，不能当作停牌或可接受空数据。
- 做 T 回放补数 scope 升级为 v2；离线正式执行禁止当前与旧补数路径。
- 处于 `DELIVERED`/`RECEIVING` 超过 5 分钟的传输会进入恢复处理，避免重连后永久卡住。

相关聚焦 pytest 全部通过；scoped Ruff 与 `git diff --check` 通过。QMT Agent 子验证为 `97 + 12 passed`。

## 运维复验

旧隔离 `BACKTEST` `c4c24454-9de9-4db9-885f-4a3cf11377b8` 原本为 `RUNNING`，进度 `4.0622075%`、处理至 `2026-07-28 10:05:48`。已通过 `TTradeReplayService.cancel` 正常收敛为 `CANCELLED`，未直接修改数据库。

按标准 `full/live` 顺序重启成功；启动时账户快照 age=`2.756s`、`reconciling=0`、Agent=`ready`、marketData=`syncing`，之后曾进入 `ready`。这不是持续健康结论：后续后台财务同步期间出现 `syncing/reconciling` 且快照变旧，故未将其作为最终持续健康验收。

## v2 单点历史 Tick 复验

标的 `605499.SH` 的同日 `1d` 数据存在，故零 Tick 不可归类为停牌。复验结果如下（请求 ID 与 checksum 均为已记录前后缀）：

| 交易日 | 请求 | 结果 |
| --- | --- | --- |
| 2026-07-22 | `a64709f6...` | `0` 行，`verified`，`XT_DATA_NO_ROWS` |
| 2026-07-23 | `12da8ca3...` | `0` 行，`verified`，`XT_DATA_NO_ROWS` |
| 2026-07-24 | `85639671...` | `4911` 行已保存并验证；min=`1784855702000`，max=`1784878257001`，checksum=`ac6b...f6d` |

判定：边界时间修复已生效；7 月 22–23 日的零 Tick 与当前 provider/QMT 可下载历史截断一致（推断），不是日内边界代码失败；本结论并非供应商文档对 retention policy 的证明。

## 20 日只读审计

| 快照 | 持仓 | 窗口 | 完整 | 预期 | 缺口 |
| --- | ---: | --- | ---: | ---: | ---: |
| 2026-07-21 | 9 | 2026-07-22..2026-08-18 | 171 | 180 | 9 |

确切缺口为：`002027.SZ`（7/22）；`605499.SH`、`688552.SH`、`688577.SH`、`689009.SH`（各 7/22、7/23）。因此正式回放为 **BLOCKED**，未执行。

另一次全量 source-identity 审计覆盖 180 个 instrument-day，读取 `440979` 条记录、`104` 页；其中 `67` 个 instrument-day 因 `HistoricalTickPaginationError: missing identity` 失败。主要涉及 `002594.SZ`、`300917.SZ`、`302132.SZ`（至 8/14）以及 `601318.SH`（至 8/7）。因此 `171/180` 的覆盖率也不代表正式可执行：旧行缺少 `source_time_ms`/`tick_ordinal`。

## 五日只读诊断

- 最佳 raw 窗口：快照 `2026-07-22`，`41/50`；另有 `HELD_INSTRUMENT_NOT_REPLAYABLE (787825.SH)` 和 9 个缺口。
- 最佳 replayable 窗口：快照 `2026-07-21`，`36/45`，仍有 9 个缺口。

五日回放只用于诊断，不能等价于 PAPER，也不能通过 formal 门禁。

## Influx 历史数据阻塞与安全路径

Influx 主键为 `time + tagset`，相关 tags 为 `stock_code`、`period`；当前没有受支持的行删除，且同 key 重复写入的覆盖结果不可确定。因此不得 broad delete，也不得原地 hydration。

唯一安全替代需要单独授权：建立独立 canonical archive/database/table，只写入 verified canonical transfers；完成严格全量审计后原子切换 reader/writer，不双读，也不回退 legacy。provider 的 9 个缺口仍需独立历史源或备份解决。

## 未执行项与边界

- 未执行 PAPER、CANARY、LIVE 订单或任何真实交易。
- 未进行 iOS 开发或验证；Windows 环境下 iOS 明确排除。
