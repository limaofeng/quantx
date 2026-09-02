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

### Web 训练工作台契约

`/research` 的训练工作台是研究操作面。它先读取认证数据集和脱敏 worker
capability，再通过 `previewStockSelectionTraining(input)` 固化一次预检指纹；只有
`blockers` 为空、预检仍为当前配置且解析后的后端明确显示时，才允许提交
`startStockSelectionDevelopmentTraining(input, previewFingerprint, idempotencyKey)`。
请求范围、日期、后端和资源参数使用 GraphQL typed input；模型族、冻结切分和评估
门禁继续由服务端冻结配置决定，不能由浏览器覆盖。

提交后的运行通过 `stockSelectionTrainingRuns` / `stockSelectionTrainingRun` 读取，
页面对活动运行每 5 秒轮询并展示阶段、完成单元、队列原因、可脱敏错误和取消状态。
取消必须提交 `expectedVersion` 与幂等键。`DEVELOPMENT` 成功只代表开发证据完成，
界面必须显式显示它不是最终评估，也不能直接作为日推理 bundle。

只有 `startStockSelectionFinalEvaluation(parentRunId, idempotencyKey)` 产生的
`FINAL_EVALUATION + SUCCEEDED` 运行，且 hash/identity、固定切分、证据和门禁全部
一致，结论为 `SHADOW_ELIGIBLE` 或 `ACTIVE_ELIGIBLE` 并标记 `registerable`，才可
通过 `registerStockSelectionModel(runKey)` 进入人工 registry。DB 中的 `run_key` 是
登记真源；运行目录必须是 `.runtime/research-runs/<run_id>` 的安全子目录。

最终评估详情分开展示 probability、ranking、data、stability、disagreement 和
gates 证据。缺失证据表示“不可用”，不能转换成零或通过门禁。公共 GraphQL 投影
不包含 `source_reference`、本地路径、密钥或原始 worker 异常；只读 evidence JSON
也必须经过白名单化和长度限制。

同坐标对比使用 `stockSelectionTrainingComparison(runIds)`，只接受 2–5 个运行；
坐标、数据集、后端或实验身份不一致时返回 mismatch 并禁止把结果当作同一实验比较。

训练查询使用 `market:read`、`WEB_ONLY`、`web-internal`；训练 mutation 使用
`operations:write`、`WEB_ONLY`、`web-internal`、`NON_TRADING_WRITE`。登记和人工
阶段切换仍为 `ADMIN`，且不会因训练工作台开放而改变交易权限。

从仓库根目录手工执行唯一流程：先认证黄金面板，再（需要 GPU 时）构建并认证
OpenCL wheel，随后用数据库锁定的三个哈希分别运行 DEVELOPMENT 和
FINAL_EVALUATION。哈希必须原样来自已接受的请求；Research 不会依据运行路径或
本地配置重算坐标：

```powershell
uv run --frozen quantx-research certify-next-day-selection-dataset `
  --config apps/research/configs/next_day_selection_v1.yaml `
  --dataset-version next-day-selection-v1

.\ops\windows\build-lightgbm-opencl-wheel.ps1 `
  -SourceDirectory F:\src\LightGBM -OutputDirectory F:\src\LightGBM\dist

uv run --frozen quantx-research qualify-lightgbm-gpu `
  --dataset-dir .runtime\research-datasets\next-day-selection-v1 `
  --build-evidence F:\src\LightGBM\dist\lightgbm-opencl-build-evidence.json

uv run --frozen quantx-research train-next-day-selection `
  --config apps/research/configs/next_day_selection_v1.yaml `
  --run-kind DEVELOPMENT --dataset-dir .runtime\research-datasets\next-day-selection-v1 `
  --output-root .runtime\research-runs --run-id next-day-selection-development `
  --spec-hash <64-lowercase-hex> --coordinate-hash <64-lowercase-hex> `
  --environment-requirement-hash <64-lowercase-hex>

uv run --frozen quantx-research train-next-day-selection `
  --config apps/research/configs/next_day_selection_v1.yaml `
  --run-kind FINAL_EVALUATION --dataset-dir .runtime\research-datasets\next-day-selection-v1 `
  --output-root .runtime\research-runs --run-id next-day-selection-final `
  --parent-run-dir .runtime\research-runs\next-day-selection-development `
  --spec-hash <64-lowercase-hex> --coordinate-hash <64-lowercase-hex> `
  --environment-requirement-hash <64-lowercase-hex> --frozen-test-access-count 1
```

`qualify-lightgbm-gpu` 只接受构建脚本生成的 schema-v1 build-evidence，并要求
LightGBM 4.7.0、`USE_GPU=ON`、wheel SHA-256、真实重复性、CPU reload 和训练期间
显存峰值采样全部满足门禁；无法采样峰值时状态为 `GPU_UNQUALIFIED`。CPU-only
wheel 显式报告 `GPU_UNAVAILABLE_BUILD`：`AUTO` 使用 CPU，`GPU_REQUIRED` 失败，
CPU-only 路径不触碰 GPU 初始化。

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

研究中心的跨研究类型运行列表以 `researchLifecycleRuns` 为唯一权威连接。它在
服务端合并离线研究产物与次日概率训练运行，使用 `ResearchLifecycleRunFilter`
统一按研究类型、阶段、状态、`updatedAt` 的 UTC 日期闭区间和运行标识/版本/数据集
搜索；服务端完成去重、`updatedAt DESC, id ASC` 稳定排序以及全局 `offset/limit`
分页，`total` 是过滤后的完整总数。离线产物使用 `artifact:<key>` 身份并投影为
`RESEARCH_EVIDENCE`，训练运行使用 `training:<runId>` 身份并投影为
`TRAINING_RUN`；同一 `next-day-selection` `runId` 的数据库训练记录优先于磁盘
产物。每行只返回一个类型特定摘要，路径、私有 spec JSON 和原始异常不会进入公共
契约。

接口变化后必须经 Caddy 公共端点运行 `npm run codegen`，再执行根级 check、lint、
test、build 和 `npm run docs:contracts`。
