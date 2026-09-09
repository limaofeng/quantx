# P5 全量补数与准入准备（2026-09-09）

状态：IN_PROGRESS。用户授权补齐数据、直接修复实现 BUG，设计问题先确认。

## 已执行

- 通过 development 配置及现有 import_partition，逐一处理原 11 标的、8 月 21 交易日的
  231 个 Tick 分区。全部成功完成首次处理；已完成任务复用，其余以 daily-limits-v2 幂等
  身份入队。没有对 INCOMPLETE 调用 retry，没有绕过新 QMT 补采的时段门。
- 提交脚本 `.runtime/p5-august-fill.py` 已结束，exit=0。每分区请求 ID 与初次结果见
  `.codex_screenshots/p5-august-fill.jsonl`。这里的 exit=0 表示全部尝试提交，不代表补齐。
- 11:03:01 只读快照：生产 READY 72、WAITING_SOURCE 48、QUEUED 109、INCOMPLETE 2；
  开发 LOCAL_VERIFIED 29、QUEUED 200、INCOMPLETE 2。后台生产导出/开发导入继续处理。
- 完整 231 个分区及状态见 `.codex_screenshots/p5-august-status.json`，汇总见
  `p5-august-status-summary.log`。`.runtime/p5-august-status.py` 只读复查，不重发任务。

## 明确失败与修复

| 标的/日期 | 分区 ID | 生产错误 |
| --- | --- | --- |
| 000001.SZ / 2026-08-03 | `196359a855cfb5bd547b13442c27996d2cd212f3b1a83c1c91eff92ec3926c07` | ValueError |
| 600036.SH / 2026-08-03 | `ddb5769e923e29db9161be76929ccb053b0faf0d3ecc5608dcb7ea41df55b06d` | ValueError |

生产导出 catch 将所有业务校验错误保存为 type(exc).__name__，导致范围缺数据、覆盖变化、
参考资料不足等错误无法区分。已在开发代码修复：只保留已知错误码白名单，其余仍使用异常
类型，绝不返回原始路径、凭证或任意异常文本。12 项测试、Ruff 通过；日志
`.codex_screenshots/p5-export-reason-tests.log`。

修复提交 `60cec99a` 尚未部署生产，不能恢复已有两条记录被抹掉的原因。生产侧应按上述分区 ID 查询关联
source_request_id 与定向日志，确认根因；不能根据泛化 ValueError 宣称已找到或修复数据缺口。
本机未访问生产数据库、Prefect 或 Agent 私有接口，也未重启生产。

## 待确认设计

- [正式评估配置草案](多标的做T助手P5正式评估配置草案.md)已给出模拟账户、费用/滑点、
  覆盖及样本数、收益/回撤和最坏分组门槛，等待用户确认，尚未写成批准政策。
- 只读查询开发 t_trade_instrument_profiles：11 标的在 2026-08-01 前的画像数为 0。
  证据 `.codex_screenshots/p5-profile-preflight.log`。
- 现有因果画像生成器目标为 20 个完整交易日、最低 10 日。已请求用户决定：补 7 月 Tick
  生成截至 7 月 31 日画像，还是以 8 月前段预热并缩短正式样本。未擅自补新月份、生成
  夹具画像或用未来数据代替历史。行业时点资料、初始估值和策略配置仍需按正式输入核对。

用户后续已选择“8 月前段预热、后段评估”，不补 7 月。按现有画像最低 10 日提出
8 月 3～14 日预热、8 月 17～31 日评估；具体划分及调整后的评估门槛已更新草案，待确认。
此前整月 20 个评估日门槛作废；仍不使用未来数据构建画像。

下一步沿现有任务检查真实补数结果，处理具体错误；待设计答复后准备画像与冻结配置，
最后执行正式资格预检和组合/旧单票对照。当前不标 P5 DONE，不开放实际 P6。
