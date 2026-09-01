# 次日上涨概率只读候选

此模块预测普通沪深 A 股下一交易日开盘到收盘收益是否大于零。它是研究与人工
决策工具，不进入 `StrategyBase.step`、`TradeIntent`、OrderSizer、Broker 或 QMT
委托链路，也不会自动训练、登记或切换模型。

## 训练契约

- 指标版本固定为 `daily-indicator-v1`；模型因子版本、哈希和标签版本分别写入
  `factor-schema.json` 与 manifest。
- 标签只使用交易日历中的精确 T+1；股票缺少该日有效开盘或收盘时标签为空，
  不向后寻找下一条可用记录。
- 最近 5 年中最后 12 个月是冻结测试；之前 48 个月执行至少 30 月训练、6 月
  校准、1 月验证的逐月 walk-forward。
- Logistic 与 LightGBM 使用冻结网格；验证 Brier 相差不超过 0.5% 时选择
  Logistic。默认 Platt，Isotonic 只有在正样本不少于 20,000 且相对改善 Brier
  不少于 1% 时可用。
- ACTIVE 效果门禁为 Brier Skill > 0、ECE ≤ 3%、Top20 上涨率提升区间下界 > 0，
  并要求 point-in-time 历史 ST、行业、退市状态完整。

从仓库根目录手工训练：

```powershell
uv run --frozen quantx-research train-next-day-selection --config apps/research/configs/next_day_selection_v1.yaml
```

## 安全产物

运行目录只接受 manifest 索引且 SHA-256 与字节数都匹配的普通文件。路径穿越、
符号链接、联接点、Pickle/Joblib、非有限 JSON 数值、非有限模型系数、非正预处理
尺度、越界或非单调校准器均被拒绝。运行时只加载 JSON、LightGBM 文本和 Parquet；
不得反序列化 Python 对象。

注册表只保存白名单化的冻结测试指标和数据质量投影，本地数据路径不会进入
GraphQL。研究详情的 `selectionMetrics` 使用同一严格加载器，不绕过注册校验。

## 发布与每日推理

模型阶段为 `CANDIDATE → SHADOW → ACTIVE`，以及受控的 `SUSPENDED/RETIRED`。
阶段切换通过 `stateVersion` 乐观锁，并按模型版本排序锁定注册表；系统最多保留
一个 ACTIVE 和两个 SHADOW。激活新模型会在同一事务内暂停旧 ACTIVE。

每日指标快照全市场成功后：

1. 没有 ACTIVE/SHADOW 时跳过；
2. 仅保留普通沪深 A 股，并在横截面变换前排除 ST、停牌、退市风险、历史不足和
   因子完整度不足标的；
3. 写入带 SHA-256 的不可变因子 Parquet；
4. 计算两家族概率、选定家族校准概率、OOD、校准桶和几何置信度；
5. 全池按概率降序、代码升序排名，A 仅取总排名 1–20，B 仅取 21–50，不回填
   资格失败留下的名次；
6. 预测、候选和成功终态在一个数据库事务内发布。

候选规则显式固化最低校准概率、最低置信度、最低 OOD fit、最低因子完整度、
有效历史长度和 A/B 数量；运行时不得另藏常量阈值。

每次运行固化候选规则版本。模型阶段在推理期间变化会使该运行失败。同一模型
同日重跑保留完整审计。对外先确定当前模型阶段的最新尝试，仅当该尝试成功时
读取其批次；失败或运行中的最新尝试绝不回退到更早成功候选。

252 日门禁只读取日快照中截至 T 日固化的 `valid_history_count`，不能使用会随未来
日期增长的 Instrument 累计上市天数。旧快照缺少该证据时必须重算并安全排除。

## GraphQL 与界面

- `stockSelectionModels` / `stockSelectionModel`：模型、冻结测试证据与门禁；
- `registerStockSelectionModel`：从成功研究运行手工登记 CANDIDATE；
- `setStockSelectionModelStage`：带状态版本的人工阶段切换；
- `stockPredictionRunStatus`：运行终态、规则版本与因子快照哈希；
- `stockProbabilityCandidates`：A/B、概率、置信度、两模型分歧、校准支持、运行和
  因子追溯字段。

`/screening` 的“次日概率”与指标、盘中模式明确分开；模型选择默认 ACTIVE
优先，也可显式查看任一 SHADOW。SHADOW 始终显示非交易横幅。`/research`
同时展示安全训练证据和模型人工发布注册表。

接口变化后必须经 Caddy 公共端点运行 `npm run codegen`，再执行根级 check、lint、
test、build 和 `npm run docs:contracts`。
