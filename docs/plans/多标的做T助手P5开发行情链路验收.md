# P5 开发行情链路验收（2026-09-09）

结论：开发端经生产 Caddy 行情专用接口取得历史分片、校验、导入本机独立数据服务并回读的链路通过。
本次测试范围为两个单标的/单交易日分区，不代表全部 231 分区或 P5 策略准入通过。

## 环境与实际结果

- 执行时间：北京时间 08:32–08:35；macOS 使用 `quantx` Conda、显式 development 配置。
- 生产访问仅通过配置的 `QUANTX_MARKET_DATA_URL` 与行情 token；未连接生产数据库、Redis、
  Prefect，未启停或修改生产服务，未调用交易接口，未在开发机加载 miniqmt。
- 入口：`./ops/quantx.sh history --instruments <code> --period tick --start <day> --end <day>`。
- 生产历史状态、2026 日历、皖能电力参考资料接口均 HTTP 200；历史任务从 QUEUED 转为 READY。
- CLI 首轮返回 `timeout / DEVELOPMENT_HISTORY_PENDING`：该 CLI 使用零等待提交语义，
  不代表 HTTP 请求超时或生产失败。皖能电力沿同一幂等身份再次导入；平安银行由后台导入完成。

| 标的 | 日期 | 生产导出 | 本地状态 | 接收/保存/回读验证 Tick |
| --- | --- | --- | --- | --- |
| 000543.SZ 皖能电力 | 2026-08-03 | READY，1 分片 | LOCAL_VERIFIED | 4597 / 4597 / 4597 |
| 000001.SZ 平安银行 | 2026-08-31 | READY，1 分片 | LOCAL_VERIFIED | 4871 / 4871 / 4871 |

两者本地持久化验证均为 `verified`，单次回读通过，共 9468 条。
SHA256、范围、行数验证使用现有正式导入实现。皖能电力额外经 P5 取数入口独立回读 4597 条。

## 可定位请求

| 标的 | 开发请求 ID | 生产源请求 ID |
| --- | --- | --- |
| 000543.SZ | `f3b8eadf-3ae7-7322-1bfd-8d1c7eef4f07` | `31badf4c-4cce-4c45-aeb2-48fc57141336` |
| 000001.SZ | `4693a2ff-4408-ede3-11ae-4c6bad859ff3` | `d3ffe20a-f46f-4a5e-8912-ec8b2da36a84` |

生产 `/market-data/v1/history/{id}` 的分区 ID：

- 皖能电力：`2020ed8fbb01d386ae15784b4d9d97961791d6fcd7616782e77e3d298e58207c`。
- 平安银行：`7e6788592052a87fa8388a13f5b6c5df838227f2baf3b34771c0ed1b942bac41`。

平安银行源请求正是 9 月 8 日留下的未完成句柄，本次已获 READY 分片和本地验证结果；
此前“该请求未返回”不再是当前阻碍。没有调用失败重试接口。

## 需生产行情侧核查：历史涨跌停资料不足

现象与证据：

1. 下载并通过 checksum 验证的生产原始分片中，两组共 9468 条 Tick 均没有有效非零
   `upperLimit` / `lowerLimit`；`priceTick` 则全部有非零值。
2. 开发导入实现将 `upperLimit`、`lowerLimit` 分别映射到 `up_stop_price`、`down_stop_price`。
   因此缺口已存在于生产导出内容，不能归因为开发端漏映射。
3. `/reference/{code}?as_of=...` 的 instrument 字段包含最小价位，但没有对应历史交易日的
   涨跌停价。HTTP 200 不等于已满足 P5 历史执行参考资料要求。
4. 皖能电力 P5 回读有 4503 条连续交易时段记录缺涨跌停价，数据状态为 `REFERENCE_REQUIRED`。
   链路成功不能解除正式回测门。

生产侧建议按上表源请求 ID 核查历史 QMT 返回、上传分片及持久化记录，确认供应端能否提供
对应日期的权威涨跌停参考资料；如能，须保留日期与来源证据并经行情接口提供。如不能，
应明确报告历史参考数据能力缺口。不得用当前证券详情或简单按前收盘乘比例补造历史值。

当前没有发现行情接口不可达、鉴权失败、生产导出失败或 QMT Agent 报错；未读取生产 Agent
日志，无法进一步认定该字段缺口由 Agent、历史供应数据还是更早的存储流程造成。
两组均复用可导出的生产数据，本次不证明“全新缺口→QMT 补采”分支。测试时已过 08:30，
按现有规则，新缺口只在交易日 16:00–次日 08:30 或非交易日派发；没有绕过时段门。

## 本地证据

日志均在 `.codex_screenshots/`，不提交数据正文或凭证：

- `p5-remote-history-probe.log`、`p5-remote-history-import.log`：皖能电力提交与成功导入。
- `p5-remote-probe-status.log`、`p5-remote-probe-status-final.log`：QUEUED → READY 及 HTTP 验证。
- `p5-remote-history-missing-probe.log`：平安银行单日提交。
- `p5-remote-probe-receipts.log`：两组 LOCAL_VERIFIED、源请求 ID 与完整本地验证摘要。
- `p5-remote-history-readback.log`：P5 独立回读及缺字段计数。
- `p5-remote-reference-check.log`：生产原始分片字段核查。

本次仅更新验收记录，不修改运行代码，不运行 E2E 或真实交易。P5 保持 IN_PROGRESS。

## 新缺口探针（2026-09-09 08:39–08:40）

用户要求优先验证新缺口触发 QMT 补采，历史涨跌停价问题已交生产侧处理，本批不展开该问题。

- 单一探针：`600036.SH / tick / 2026-08-31 / adjustment=none`。
- 分区 ID：`6a631eb256bf43eb0f38ab4e5afdbcda415aa6a4984226f1ff1a5b58cc6bfb4f`。
- 08:39:36，生产 history 状态查询为 HTTP 404，证明该分区尚无导出任务；这不等于证明
  生产缓存没有数据。通过开发端正式 `import_partition` 提交一次，返回 QUEUED。
- 08:40:29，同一分区 HTTP 200、WAITING_SOURCE、error=null，无 manifest；本地 QUEUED。
  确认生产导出 Worker 已推进任务，尚未取得分片或入库结果。
- 生产日历 HTTP 200，9 月 9 日不是休市日；代码限制交易日新补采在 16:00 至次日 08:30
  派发。当前处于关闭窗口，没有绕过门禁、调用失败重试或重复提交。
- 状态接口在 manifest 生成前不暴露 source_request_id、QMT 投递/上传状态或等待原因。
  因而不能用 WAITING_SOURCE 或 manifest 缺失断言 Agent 已接单、未接单或发生故障。
  当前阻碍为派发窗口与观察能力限制，未发现接口报错。
- 后续沿同一分区在窗口开放后检查 READY/INCOMPLETE，并核对新源请求的创建时间、
  development-export 幂等范围、Agent 上传与开发 LOCAL_VERIFIED；若只是复用旧源，
  仍不能将新补采分支标为通过。该请求为持久化队列任务，窗口开放后可由正常 Worker 继续。
  本轮未配置 Codex 定时复查，不重复创建请求。
- 证据：`.codex_screenshots/p5-new-gap-submit.log`、`p5-new-gap-status.log`。
  新补采分支当前为“入队及 Worker 消费通过，QMT 补采/上传/开发入库待验证”。
