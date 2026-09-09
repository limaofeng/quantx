# P5 标准补数与完整性验收（2026-09-09）

## 范围与结果

开发环境通过标准 `ops/quantx.sh history` 入口调用生产行情 HTTP 接口，补取原 11 只标的 2026-08-10 至 2026-09-08 的 Tick。没有直连生产数据服务、部署生产代码或显式重试失败任务。

- 22 个交易日 × 11 只标的，共 242 个分区；全部已提交。
- 标准导入确认 191 个 `LOCAL_VERIFIED`，比首次提交时 175 个增加 16 个。
- 标准回测数据准备入口完整扫描 242 个分区：191 个有数据，共 865,618 条 Tick；51 个为空，没有未尝试分区。
- 191 个非空分区均通过源身份、分页、顺序、盘口字段检查，连续竞价分钟覆盖均为 237/237。此指标不证明交易所逐笔全量无丢失。
- 11 标的共同完整日期：8 月 10–14、17–21、25–28、31 日，共 15 日。前 10 日可供预热，之后共同完整日仅 5 日；不能据此宣布正式 P5 准入完成。
- 最后一次只读生产状态查询（北京时间 16:10）：50 个 `WAITING_SOURCE`，1 个 `INCOMPLETE / ValueError`。所有查询 HTTP 200，因此 HTTP 链路通畅，源侧完成证据尚未返回。
- 最终数据集状态 `INCOMPLETE`，标准补数与数据检查命令均以退出码 2 明确暴露未完成结果。

## 生产侧阻碍与排查要求

`000543.SZ / 2026-08-24` 的导出任务返回 `INCOMPLETE / ValueError`，无 manifest。同期参考接口有证券资料，因子证明 `VERIFIED`。当前接口信息不足以判定这是行情无源、覆盖证明不足还是导出异常；不能套用此前 8 月 3 日的历史权限结论。

其他 50 个分区为 9 月 1–4 日的全部 11 只标的，以及 9 月 7–8 日的 `000001.SZ`、`002027.SZ`、`600036.SH`。已过常规 16:00 派发窗口，仍在等待源数据。需生产端根据下面的导出任务 ID 检查 Worker 派发、关联源任务、Agent 返回行数及持久化覆盖证明。公开响应未提供 `source_request_id`，不能仅据此断言内部没有源任务。请提供脱敏的具体异常与关联任务 ID；当前未重复触发失败源下载。

## 本次标准功能修复

1. 区间补数不再遇到第一个失败就提前返回，后续分区仍全部提交；返回预期/已验证分区数及各分区状态、ID、原因。
2. CLI 保留完整进度，已知失败返回非零退出码；异步等待不伪装为验证成功。
3. 数据准备输出与正式准入一致的连续竞价分钟覆盖，并统计原始证券状态。
4. 修复历史 Tick `stock_status` 被误限制为日 K 停牌枚举 `-1/0/1` 的问题。整数状态保留归档；未知状态保守禁止交易，不把数据档案有效等同于可执行。

实际源包含大量状态 `3`，当前取得的 [XTData 官方字段说明](https://dict.thinktrader.net/nativeApi/xtdata.html) 未解释该值的交易语义。正式回测前仍需生产端确认实际 Tick 状态编码及其映射来源；不能擅自把 `3` 视作正常交易。历史 Tick 不要求涨跌停字段，历史日 K 涨跌停价为空仍允许通过数据检查，实时风控未变。

验证：相关基础设施、缓存采集、参考资料、准入、评估、运行时及边界测试共 50 项通过，修改的 Python 文件 Ruff 检查通过。

## 复现与证据

```bash
./ops/quantx.sh history --instruments 000001.SZ,000543.SZ,002027.SZ,002594.SZ,302132.SZ,600036.SH,605499.SH,688213.SH,688552.SH,688577.SH,689009.SH --period tick --start 2026-08-10 --end 2026-09-08
conda run --no-capture-output -n quantx python ops/t-assistant-backtest-data.py --environment development --instruments 000001.SZ,000543.SZ,002027.SZ,002594.SZ,302132.SZ,600036.SH,605499.SH,688213.SH,688552.SH,688577.SH,689009.SH --start 2026-08-10 --end 2026-09-08 --output .runtime/backtests/p5-0810-0908
```

本机证据（运行产物不提交）：

- `.codex_screenshots/p5-standard-history-0810-0908-final.log`
- `.codex_screenshots/p5-standard-completeness-0810-0908.log`
- `.codex_screenshots/p5-standard-status.log`
- `.codex_screenshots/p5-standard-final-tests.log`
- 数据集：`.runtime/backtests/p5-0810-0908/8b8f518e56ad7a423411dc0c5c410e3de0229d1947c9f82aa7d1378c2296587d`

## 未完成分区任务 ID

| 标的 | 日期 | 生产状态 | 导出任务 ID |
| --- | --- | --- | --- |
| 000543.SZ | 2026-08-24 | INCOMPLETE | `33d7a49ea492b05100eba9ee6f7921d9405fc4ae665dd8b870264a62465ff34c` |
| 000001.SZ | 2026-09-01 | WAITING_SOURCE | `3be377a903c9b2a4be73ad9e60c1d635fdb3207f6fe42f31860c247ba6339f42` |
| 000543.SZ | 2026-09-01 | WAITING_SOURCE | `db4585d57c0d3fe1648a4f1321d0fac71d9196a55f66a0dd7c5c2b2ff4dded51` |
| 002027.SZ | 2026-09-01 | WAITING_SOURCE | `85afd2d2f5bf8764625301037d046a710f9094a2a258235e46c5155c117b8ccc` |
| 002594.SZ | 2026-09-01 | WAITING_SOURCE | `537584938e82280235f7bc054e791dc7a64e115203b59ac4f7fe80fa3418b1ee` |
| 302132.SZ | 2026-09-01 | WAITING_SOURCE | `e2a816aaa0f4686f97a11dc577768200c39b84ccce050efd4d8839eba687515a` |
| 600036.SH | 2026-09-01 | WAITING_SOURCE | `21da80089964e6d8a624717887b952b3e1f060dea11ee126cfeaeaafb57bbdc1` |
| 605499.SH | 2026-09-01 | WAITING_SOURCE | `9eb956bacaf63e5988e8c44f87c5b9532cb3c0a02abf4887b8069f5e82ccfe94` |
| 688213.SH | 2026-09-01 | WAITING_SOURCE | `e9331e583bbe362bf5aee75acf60e160aa24933290cd032217a131fbc7eb0cdf` |
| 688552.SH | 2026-09-01 | WAITING_SOURCE | `854feb4953dfbcf73ce5ae77e50d937c0ef27b99315fbcae0f8826e5e0999626` |
| 688577.SH | 2026-09-01 | WAITING_SOURCE | `b40e094dec2c19352a2993857a0b1a69c57c5f9d9b8c58564f507803fb5e08de` |
| 689009.SH | 2026-09-01 | WAITING_SOURCE | `3c15fc84db80b02712c2eb4de7349f7ef317df696c7111debb91365410db1b46` |
| 000001.SZ | 2026-09-02 | WAITING_SOURCE | `8b872cc902613c388933d0569d24e7eba0009ed0fcd6e247ac1d8c187567d5a8` |
| 000543.SZ | 2026-09-02 | WAITING_SOURCE | `f2da454209540b6d5b0232171683334ca6c5977071e7b20d70043dc8ae53720d` |
| 002027.SZ | 2026-09-02 | WAITING_SOURCE | `69546ad7916bc2e226b122f0a8aadf2d424846ad80c9761a9a02a67334c31a76` |
| 002594.SZ | 2026-09-02 | WAITING_SOURCE | `ab9d28edc364b4d52d077caf896f9caabd562affe4def686966546f388a4bfe7` |
| 302132.SZ | 2026-09-02 | WAITING_SOURCE | `43a83301b30cfa886751bc06a563410df95cb6decb2a7d86da28c357228cbc45` |
| 600036.SH | 2026-09-02 | WAITING_SOURCE | `8eb81876473c38008d8fe326c9fcb0f6fa12f959c4942fe71d729b90828b43bb` |
| 605499.SH | 2026-09-02 | WAITING_SOURCE | `0d7be637b26a16685bc646277cb8c307c3b3145ee19e37b918dad9c539ee8392` |
| 688213.SH | 2026-09-02 | WAITING_SOURCE | `801d5466359cb7b8108dc915e4f6e6f8f18e9278a3358d11b8d1d3e6181383b9` |
| 688552.SH | 2026-09-02 | WAITING_SOURCE | `a4ac59aed85382de7d6f6451d995bb91ad253a50f6df7bba1861517ee581f09f` |
| 688577.SH | 2026-09-02 | WAITING_SOURCE | `40f292baeb68ee59498b6c1ad1ed696cf2ad4dacf28832532eea012202128f00` |
| 689009.SH | 2026-09-02 | WAITING_SOURCE | `a234ab18bfae9c7a05017f2ccd6228e1f633200c57ab9ed032e47a9557c51489` |
| 000001.SZ | 2026-09-03 | WAITING_SOURCE | `8e5c434fb3a314ae783a0338b3554f959ce6d819eb242a5938e58ede58945678` |
| 000543.SZ | 2026-09-03 | WAITING_SOURCE | `ec0099cc9293aa0ccb7a9c53553b0da11e6efd3aede925af08a6c63fcf026659` |
| 002027.SZ | 2026-09-03 | WAITING_SOURCE | `d71452deabc416cc85bfc0f7b3472e48fc604e1ef83cd836b7454329ee1ce89f` |
| 002594.SZ | 2026-09-03 | WAITING_SOURCE | `4d9778db778d288d1b7a0b17b29bcbef69aff3bddf452ea3addd62b80a03277c` |
| 302132.SZ | 2026-09-03 | WAITING_SOURCE | `c7008726c54e964762795c6530beea402113f7903fb322a305c8a6553c2b4a39` |
| 600036.SH | 2026-09-03 | WAITING_SOURCE | `d71c8d40c938b0700b245e8dee3d55a32d1445f39b78056599d66f804ba095c1` |
| 605499.SH | 2026-09-03 | WAITING_SOURCE | `3c460a5a72ca0500261b0a0cb12616fbfd74ef82e9cd56563a53bdee796dd505` |
| 688213.SH | 2026-09-03 | WAITING_SOURCE | `1b17a6050836d4f1b6fc736d517849a5981f06acdb5cc716606e1f774801f10f` |
| 688552.SH | 2026-09-03 | WAITING_SOURCE | `27bbfa052524649baea886387e925fb4d055dcbae58094c340ae7182faedaf12` |
| 688577.SH | 2026-09-03 | WAITING_SOURCE | `d9fcf26545aca075a2f72b49c0aac28ebd795d1cbde3675cd2b72fbdf508d4ac` |
| 689009.SH | 2026-09-03 | WAITING_SOURCE | `63d3741ec2304de9ee3691503d226eeec1bf12994b23ef4af3020307140c55af` |
| 000001.SZ | 2026-09-04 | WAITING_SOURCE | `4889bf68a189dfb9c6f66f2607929e0ea9b7d670e9f730645ea4a36e02d84c44` |
| 000543.SZ | 2026-09-04 | WAITING_SOURCE | `e4dcfebdea62730adcd09b4066352d45e2978ee2c626d954ff79b5f4cee48b7a` |
| 002027.SZ | 2026-09-04 | WAITING_SOURCE | `e881ee0345c0160a3e3b53dc780e602c1148fa6903d290f77d24dd8f5e3713a9` |
| 002594.SZ | 2026-09-04 | WAITING_SOURCE | `402f1c3e4a1180a5b4be7d1c2052af9098dad9aa0aed7d691ff8ee884b4fd45c` |
| 302132.SZ | 2026-09-04 | WAITING_SOURCE | `35aedee2abf01dd6c553908f3c7e5df7220fa545dc7d872164ea58a80b85abb0` |
| 600036.SH | 2026-09-04 | WAITING_SOURCE | `fc9b6fc90a4d575bc1a10f822fa5db30951917e1ed600dd1705495c04fc68ad3` |
| 605499.SH | 2026-09-04 | WAITING_SOURCE | `3f302d86cdeb8e432411d8620929adf34f69eb94517ea08f55c86dd11fe4a739` |
| 688213.SH | 2026-09-04 | WAITING_SOURCE | `50bf4afdbff287233e6736bae9bf68d3a7deb1803154281742a575defdcf0d29` |
| 688552.SH | 2026-09-04 | WAITING_SOURCE | `dab608a67cf2e4e462619faef6075809f479c9c59adda48a5a1addfc053923ba` |
| 688577.SH | 2026-09-04 | WAITING_SOURCE | `e43f57d4023df6cb5ade2c402cdb46a8046f737c6c24f2c3d100753db1cdd30e` |
| 689009.SH | 2026-09-04 | WAITING_SOURCE | `f98110512aa278b7fd62cad19d7aaa2c2ed15825ee1961466a328868a860ca2d` |
| 000001.SZ | 2026-09-07 | WAITING_SOURCE | `a0d3d6ef7141d7cd5907a3980cda32d9976204d261a88c88157766420b80a933` |
| 002027.SZ | 2026-09-07 | WAITING_SOURCE | `6ed1e5e753903c5630a49e08f915551cb17420ca10755af860985ab63e9997a2` |
| 600036.SH | 2026-09-07 | WAITING_SOURCE | `972434e46ad2961b76c0899472423269d4ebab4ac074953f5686be21d88e5372` |
| 000001.SZ | 2026-09-08 | WAITING_SOURCE | `a4511bf5b09f4db54fbac637f72088c029fca1c031e13f512956947d3c074f57` |
| 002027.SZ | 2026-09-08 | WAITING_SOURCE | `5da93a73425e853397332bff635e0541aae649eb43ac5a5fc3453b7419165869` |
| 600036.SH | 2026-09-08 | WAITING_SOURCE | `e4c272d761745fe909b0ec37d468cc63d4a1568752002e53a2944924e6075c85` |
