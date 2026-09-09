# 历史涨跌停价生产核查（2026-09-09）

核查结论：XTData 标准 Tick 和日 K 本来就不包含涨跌停价，并非开发端漏映射或
供应商异常。下列现场证据记录改造前状态；最终采用文末的 MiniQMT 日 K 补充方案。

## 现场证据

使用 `xtquant-demo` Conda Python，通过现有 XTData 本地连接只读查询，未下载数据、
未启动第二个 Agent、未启停服务、未修改数据库或发起交易。

| 标的 | 交易日 | XTData Tick 行数 | 生产导出 Tick 行数 |
| --- | --- | ---: | ---: |
| 000543.SZ | 2026-08-03 | 4597 | 4597 |
| 601318.SH | 2026-08-03 | 5166 | 5166 |
| 000001.SZ | 2026-08-31 | 4871 | 4871 |
| 600000.SH | 2026-09-07 | 5010 | 5010 |

六个压缩分片合计 19644 条 Tick，已排除四条 summary；所有 Tick 的
`upperLimit/lowerLimit` 均无正值，`priceTick` 均为 0.01。
四次 `XTDataManager.get_market_data` 查询直接返回的 DataFrame 均为相同的 20 列，
没有 `upperLimit/lowerLimit/priceTick`，也没有含 limit/stop 的替代列。
行数一致只是本次核查证据，不等同全天完整性或逐字段一致性验收。

结构化证据位于 `.runtime/diagnostics/historical-limit-audit-20260909.json`，
分片位于 `.runtime/data-exports/`，均不提交原始数据。

## 链路解释

- `miniqmt/data/data_manager.py:get_market_data` 使用
  `get_market_data_ex(field_list=[], ...)`，没有主动裁掉源字段。
- `broker.py:_project_historical_bar_record` 保留契约内存在的历史字段；
  涨跌停价和最小价位为 optional，缺失不触发字段异常。现有 Agent stdout/stderr
  定向搜索未发现相关字段错误，这与当前契约行为一致，不证明执行参考数据完整。
- `market_data_transfer_ingestion.py` 已映射
  `upperLimit → up_stop_price`、`lowerLimit → down_stop_price`；源列缺失时分别填 0，
  `price_tick` 填 0.01。因此最小价位看似正常，不能证明它来自历史供应数据。
- `data_exchange_archive.py` 从持久化字段重建导出时保留上述值。
- `data_exchange_reference.py` 的证券参考白名单有 `price_tick`，没有按历史交易日
  保存的涨跌停价。`get_instrument_detail` 中的 UpStopPrice/DownStopPrice 是当日字段，
  不能拿当前值补历史，否则会引入日期错配。

## 可用数据集与当前阻碍

[迅投官方股票数据文档](https://dict.thinktrader.net/dictionary/stock.html)明确提供
`stoppricedata` 历史涨跌停数据集，要求先下载再读取，并标注为 VIP 权限数据。
本机 SDK 也包含该 period，映射为 9506（日线涨跌停）。项目 apps/packages/ops/tests
的 Python 代码尚未接入该 period。

实际对 000543.SZ / 2026-08-03 直接读取该 period，客户端返回：

> 当前客户端未支持此功能，请更新客户端或升级投研版

错误为 `RuntimeError`，`ErrorID=300000`，`ErrorMsg=function not realize`。
这次调用未进入有效数据返回阶段，不能把它记作空缓存或供应商无数据；也不能仅凭此错误
区分版本限制与授权限制。结构化证据中的其他三条包装异常不是三个独立的供应端失败证据：
首个异常会使包装器连接状态变为不可用。随后单独直调 SDK 确认了上述错误。

## 已确定的最小实现（2026-09-09）

按用户要求，只使用 MiniQMT，不接入 Tushare、不新增独立参考表或采集任务；删除未使用
的 tushare_token 配置。不依赖 stoppricedata/VIP，过去没有采集的涨跌停价允许为空。

- 复用收盘后每日行情同步：仅对采集当天的 1d 行，从 get_instrument_detail 取得
  UpStopPrice/DownStopPrice，并核对 get_full_tick 的日期也是当天。旧日 K 不补当前价，
  跨日、陈旧行情、接口失败、无效数值均不生成参考字段。
- 日 K 传输使用 upperLimit/lowerLimit，持久化到 kline_1d 的
  up_stop_price/down_stop_price。未采集时省略字段，读取为空，不用 0 伪装价格。
  Influx 后续历史补采不发送缺失列，以保留同一日 K 已有参考值。
- 日 K 导出从经过覆盖核验的持久化行构建，保留历史上已采集的参考值；导出请求身份
  更新为 daily-limits-v2，重新请求不会复用此前的旧字段分片。
- 历史 Tick 契约、入库和导出不再承载涨跌停字段。实时行情仍保留现有当日详情增强，
  供实盘风控与行情雷达使用；本次不改变实时交易链路。
- P5 取数按标的、交易日关联日 K 的两个参考字段，绝不回退到历史 Tick 上的旧值。
  回测准备文件中的涨跌停值是关联后的执行上下文，不是原始 Tick 字段。
  旧样本可原样归档，缺参考值仍标记 REFERENCE_REQUIRED，不放开正式执行校验。

改造不涉及 GraphQL 接口、新数据源、券商升级或生产重启。已有生产进程需要在正常发布
窗口统一更新 Agent/Worker/API/Engine 后使用新历史契约，开发导入端也须同步代码。

验证：Agent 日期/无效值保护、历史传输入库、日 K 导出往返与 P5 共 209 项定向测试通过；
补齐引用回放关联后，受影响的 15 项回测测试通过（含新增日 K 参考值变更检测）。
Ruff 与 diff 空白检查通过。证据在 `.runtime/diagnostics/daily-limits-*.log`。
