# 历史涨跌停价生产核查（2026-09-09）

结论：本次样本的缺口在 XTData 历史 Tick 返回层已存在，并非开发端漏映射。
QuantX 当前没有独立采集历史每日涨跌停价的链路。不能据此断言供应商没有历史数据。

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

## P5 后续处理边界

先确认生产客户端对 stoppricedata 的支持及数据授权，再以单标的单日验证实际历史值。
成功后需要接入按标的、交易日持久化及导出的历史执行参考数据，并保留来源与覆盖证明；
随后补齐 P5 样本、重新执行预检。本次只完成核查，不升级券商客户端或实施协议改造。
重复下载普通 Tick 不能解决已确认的字段缺口；不以当前合约详情或固定百分比推算替代。
P5 正式回测仍受阻。
