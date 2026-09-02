# QuantX 次日上涨概率网页模型训练与 GPU 加速实施计划

> 文档版本：V1.0
> 更新日期：2026-09-02
> 当前状态：规划待实施
> 适用边界：QuantX 单账户 Windows Dev full/live

## 1. 结论

本需求应实现为“网页发起、Worker 后台训练、证据化审查、人工发布”，不应在
浏览器或 API 进程中直接运行模型训练。

权威流程为：

```text
网页配置
  -> 数据集预检
  -> 锁定配置与数据哈希
  -> Prefect Worker 训练
  -> Walk-forward 验证
  -> 锁定候选配置
  -> 一次性冻结测试
  -> 质量报告
  -> CANDIDATE
  -> SHADOW
  -> 人工 ACTIVE
```

GPU 策略为：

- RTX 4070 可用于 LightGBM 训练加速，但必须先安装并验证 Windows OpenCL GPU 版
  LightGBM。
- Logistic 训练继续使用 CPU，不在首版替换为 cuML、PyTorch 或其他模型实现。
- 每日数千只 A 股的概率推理继续使用 CPU。GPU 传输、初始化和运行时复杂度
  大于这一批量下的可预期收益。
- GPU 不参与 QMT、实盘交易、策略执行或订单链路。

## 2. 当前基线

### 2.1 已有能力

当前 `next-day-selection-v1` 已支持：

- 本地 CLI 手工训练；
- 普通沪深 A 股 T+1 开盘到收盘上涨标签；
- Logistic 与 LightGBM 冻结网格；
- 按月 Walk-forward、概率校准和最后 12 个月冻结测试；
- Brier Skill、ECE、ROC-AUC、PR-AUC、Top20/Top50 排名提升和年度稳定性；
- 安全产物校验、模型登记、CANDIDATE/SHADOW/ACTIVE 阶段管理；
- ACTIVE/SHADOW 模型的每日自动推理。

当前手工训练命令：

```powershell
uv run --frozen quantx-research train-next-day-selection --config apps/research/configs/next_day_selection_v1.yaml
```

### 2.2 当前缺口

- Web 不能创建训练任务。
- 训练配置只是 YAML，没有数据库中的不可变配置快照。
- 没有数据集目录、覆盖率预检、资源估算和后台排队。
- 验证试验与冻结测试尚未分成两次明确操作，存在反复查看测试结果后
  人工过拟合的可能。
- 当前 LightGBM 安装包不支持 GPU。

### 2.3 本机 GPU 现状

2026-09-02 本机实测：

| 项目 | 实测值 |
| --- | --- |
| GPU | NVIDIA GeForce RTX 4070 |
| 显存 | 12,282 MiB |
| Compute Capability | 8.9 |
| NVIDIA Driver | 591.86 |
| CUDA Toolkit / `nvcc` | 未安装或不在 PATH |
| Python | 3.13.9 |
| LightGBM | 4.7.0 |
| scikit-learn | 1.9.0 |
| LightGBM `device_type=gpu` 探针 | 失败，当前构建未启用 GPU Tree Learner |
| LightGBM `device_type=cuda` 探针 | 失败，当前构建未启用 CUDA Tree Learner |

因此，本机硬件满足 GPU 训练条件，但当前软件环境不能直接使用 GPU
训练。

## 3. 目标与非目标

### 3.1 目标

1. 用户可在研究中心创建一次手工训练。
2. 用户可选择经过 QuantX 认证的数据集、股票范围和时间区间。
3. 系统强制训练、校准、Walk-forward 验证和冻结测试四段时间语义。
4. 系统在提交前展示数据覆盖率、泄漏检查、样本规模和资源估算。
5. 训练任务在 Worker/Prefect 中可排队、可观测、可取消、可审计。
6. 质量报告明确区分概率质量、排名效果、数据质量、时间稳定性和发布门禁。
7. 在通过本机资格验证后，允许用 RTX 4070 加速 LightGBM 训练。
8. 任何成功训练均不自动登记、不自动切换 ACTIVE、不生成交易动作。

### 3.2 非目标

- 首版不做定时自动重训。
- 首版不支持上传任意 CSV/Parquet、不接收浏览器传入的服务器本地路径。
- 首版不允许用户上传 Python 代码、Pickle 或 Joblib 模型。
- 首版不引入 cuML、PyTorch、ONNX Runtime GPU 或另一套 Logistic 实现。
- 首版不为日级推理引入 GPU 必要依赖。
- 不允许从 API、Engine 或 QMT Agent 直接启动训练进程。
- 不为未确认的多用户、多账户或集群训练增加抽象。

## 4. 产品流程

### 4.1 入口

在 `/research` 增加“模型训练”工作区，包含：

- 训练任务列表；
- “新建训练”向导；
- 运行详情与阶段进度；
- 质量报告；
- 同数据、同切分模型对比；
- 冻结测试与模型登记操作。

### 4.2 新建训练向导

#### 步骤一：数据集

用户可设置：

- 数据集版本；
- 股票范围：普通沪深 A 股、经认证的指数成分或显式股票列表；
- 总数据起止日期；
- 基准指数；
- 最低上市交易日数。

系统固定：

- 标签为精确 T+1 开盘到收盘收益是否大于零；
- 指标版本、因子集版本和标签版本不可由表单自由输入；
- 历史 ST、行业、退市状态必须使用 point-in-time 数据；
- 数据只来自已持久化、可审计的 QuantX 历史数据，不从训练任务直接访问
  XTData/QMT。

#### 步骤二：时间切分

基础模式只允许选择总区间和冻结测试区间，其余切分由系统产生：

```text
开发区间
  ├─ 至少 30 个月训练
  ├─ 6 个月校准
  └─ 1 个月验证，按月滚动
冻结区间
  └─ 默认最后 12 个月，不参与调参和模型选择
```

第一版继续使用当前 30/6/1/12 月权威结构。界面展示每个 Walk-forward fold，
但不允许拖动单个 fold 制造非对称切分。

强制校验：

- 四类区间严格按时间先后、不重叠；
- 不对股票日样本随机分层拆分；
- 每个样本的指标时间不得晚于 T，标签只能使用精确 T+1；
- 冻结测试区间一旦被最终评估使用，不得回流为当次实验的训练或验证数据。

#### 步骤三：模型与运行资源

首版固定训练 Logistic 和 LightGBM 两个家族，由验证 Brier 和现有简单模型
偏好规则选择家族。

可设置项：

- 执行后端：`AUTO`、`CPU`、`GPU_REQUIRED`；
- Bootstrap 次数；
- 随机种子；
- Worker 批次大小；
- 可选训练备注。

高级参数使用系统预设，不提供任意 JSON 编辑器。ACTIVE 效果门禁由系统
版本化，不得通过表单降低。

#### 步骤四：预检与确认

提交前必须展示：

- 解析后数据集 ID 和 manifest SHA-256；
- 开发区间、冻结区间和 fold 数；
- 样本数、股票数、交易日数和正样本比例；
- 行情、交易日历、指标、标签和历史股票池覆盖率；
- 数据泄漏检查结果；
- 估算内存、显存、磁盘和耗时等级；
- 请求的运行后端和预检解析后的实际后端；
- 只能 SHADOW 的数据质量原因。

`AUTO` 可在预检时解析为 CPU 或 GPU，但必须在用户确认前明示。任务进入队列后，
解析后后端成为不可变配置的一部分；GPU 初始化或运行失败时任务失败，不在同一
运行中静默改用 CPU。

### 4.3 防止冻结测试过拟合

网页训练分为两种运行：

1. `DEVELOPMENT`：只完成训练、校准和 Walk-forward 验证，可反复运行。
2. `FINAL_EVALUATION`：用户选定一个已成功的开发运行，锁定其数据、特征、参数和
   校准器选择后，执行冻结测试并产生可登记模型。

每个实验组记录冻结测试访问次数。默认只允许一个配置作为该实验组的正式
最终评估。若之后修改参数再查看同一测试区间，报告必须显示“测试集已多次使用”，
不得继续宣称为无偏冻结证据。

## 5. 模型质量判定

### 5.1 概率质量

展示：

- Brier Score；
- 全池基线 Brier；
- Brier Skill；
- ECE；
- ROC-AUC；
- PR-AUC；
- 概率校准曲线和每个校准桶的样本数。

### 5.2 排名效果

展示：

- Top20/Top50 平均上涨率；
- 相对全池上涨率提升；
- 平均开盘到收盘收益和收益提升；
- 按交易日 bootstrap 的置信区间；
- 各年度的 Brier、ECE 和 Top20 提升；
- Logistic 与 LightGBM 的概率分歧。

### 5.3 数据质量

展示：

- 行情覆盖率；
- 指标完整率；
- 精确 T+1 标签覆盖率；
- point-in-time ST、行业和退市状态覆盖率；
- 新股、停牌和无效价格的排除数；
- 数据取整后的样本数和正样本比例；
- 数据泄漏、重复样本、未来日期和切分重叠检查。

### 5.4 发布结论

界面不用一个不透明的“综合分”取代证据，而是输出：

| 结论 | 语义 |
| --- | --- |
| `BLOCKED` | 数据、泄漏、产物或基本效果检查不合格 |
| `SHADOW_ELIGIBLE` | 可登记和影子运行，但不具备 ACTIVE 资格 |
| `ACTIVE_ELIGIBLE` | 效果门禁与历史股票池完整性全部合格 |

当前 ACTIVE 效果门禁保持：

- Brier Skill > 0；
- ECE <= 3%；
- Top20 上涨率提升置信区间下界 > 0；
- 历史 ST、行业和退市状态完整。

用户不能为某次运行改写这些门禁。门禁只能作为版本化系统规则整体变更。

### 5.5 模型对比规则

只有以下坐标全部相同时，Web 才显示直接胜负：

- 数据集 manifest 哈希；
- 开发与冻结时间切分哈希；
- 股票池规则哈希；
- 指标版本与因子集哈希；
- 标签版本；
- 评估代码版本。

运行后端 CPU/GPU 作为环境证据显示，但不允许用户忽略数据或切分不同而只比较
某一个指标。

## 6. 系统架构

### 6.1 进程边界

| 组件 | 职责 |
| --- | --- |
| Web | 表单、预检、进度、报告、对比、取消和登记入口 |
| API | GraphQL、权限、参数验证、幂等命令和数据库真源 |
| Worker | 训练排队、资源预检、子进程运行、进度收敛和产物发布 |
| Research | 数据面板构造、切分、训练、校准、评估和安全产物 |
| Infrastructure | 数据集、训练配置、运行状态、幂等和仓储 |
| Engine | 不参与训练，继续仅管理交易执行域 |
| QMT Agent | 不参与训练，不接收训练命令 |

API 不调用 `subprocess` 启动模型训练。API 持久化请求和命令箱，Worker 收敛为
Prefect flow 并在隔离子进程中运行 Research 入口。Redis 可用于唤醒和广播，但不是训练状态
真源。

### 6.2 后台执行原则

- 同时最多一个模型训练运行。
- full/live 连续交易时段不启动新的高资源训练；用户可提交，任务保持
  `QUEUED`，交易时段结束后再执行。
- Worker 训练子进程使用较低系统优先级，不与 QMT、Engine 或日快照任务争用关键
  时间窗口。
- 取消在 fold、模型家族和产物阶段的安全检查点生效。
- 取消或失败任务可保留日志和失败 manifest，但不得产生可登记模型。
- 任务进度使用阶段和 `completed_units/total_units`，不使用伪造的时间百分比。

## 7. 数据模型

### 7.1 `stock_selection_dataset_versions`

用于记录不可变训练数据证据：

- `dataset_version`；
- `status`；
- `source_kind`；
- `date_start` / `date_end`；
- `universe_spec`；
- `indicator_version`；
- `factor_set_version` / `factor_set_hash`；
- `label_version`；
- `manifest_sha256`；
- `sample_count` / `stock_count` / `trading_day_count`；
- `quality_summary`；
- `created_at`。

数据库不暴露原始服务器路径给 Web。数据文件只由 manifest 索引，并沿用现有安全
产物校验。

### 7.2 `stock_selection_training_specs`

用于记录不可变训练配置：

- `spec_id`；
- `dataset_version`；
- `run_kind`；
- `split_spec`；
- `model_spec`；
- `evaluation_spec`；
- `requested_backend`；
- `resolved_backend`；
- `random_seed`；
- `spec_hash`；
- `environment_requirement_hash`；
- `created_by` / `created_at`。

`spec_hash` 覆盖数据、切分、特征、标签、模型、校准和评估语义。硬件型号不混入
`spec_hash`，而是写入独立环境证据，以区分“同一实验语义”与“不同执行环境”。

### 7.3 `stock_selection_training_runs`

运行表字段：

- `run_id` / `run_key`；
- `spec_id`；
- `run_kind`；
- `parent_run_id`；
- `status`；
- `phase`；
- `completed_units` / `total_units`；
- `prefect_flow_run_id`；
- `requested_at` / `started_at` / `completed_at`；
- `cancel_requested_at`；
- `artifact_manifest_sha256`；
- `environment_evidence`；
- `metrics_summary` / `gate_summary`；
- `error_code` / 脱敏 `error_message`；
- `state_version`。

状态只使用：

- `QUEUED`；
- `RUNNING`；
- `SUCCEEDED`；
- `FAILED`；
- `CANCELLED`。

阶段单独使用：

- `PREFLIGHT`；
- `DATASET_BUILD`；
- `WALK_FORWARD`；
- `FINAL_FIT`；
- `CALIBRATION`；
- `FROZEN_TEST`；
- `ARTIFACT_PUBLISH`。

`cancel_requested_at` 是取消请求事实，不为它另增一组容易混乱的业务状态。

### 7.4 与现有模型表的关系

- 只有 `FINAL_EVALUATION + SUCCEEDED` 的运行才能调用现有
  `registerStockSelectionModel`。
- `stock_selection_model_versions.run_key` 必须指向该最终评估运行。
- 登记时继续执行 manifest、SHA-256、字节数、路径、非有限数和模型结构验证。
- 网页训练成功不等于模型登记成功，登记仍是独立人工操作。

## 8. GraphQL 契约

### 8.1 Query

- `stockSelectionTrainingCapabilities`：CPU/GPU 能力、GPU 资格状态和脱敏环境摘要。
- `stockSelectionDatasetVersions`：可选认证数据集。
- `previewStockSelectionTraining`：数据覆盖、切分、泄漏、资源和后端预检。
- `stockSelectionTrainingRuns`：训练列表。
- `stockSelectionTrainingRun`：运行详情、进度、证据和错误。
- `stockSelectionTrainingComparison`：同坐标模型对比。

### 8.2 Mutation

- `startStockSelectionDevelopmentTraining`：以预检指纹和幂等键提交开发运行。
- `startStockSelectionFinalEvaluation`：对已锁定开发运行执行冻结测试。
- `cancelStockSelectionTrainingRun`：请求取消。
- 继续复用 `registerStockSelectionModel` 和 `setStockSelectionModelStage`。

训练发起权限属于非交易研究写入，不复用实盘订单权限。所有写操作必须使用
幂等键和状态版本。

### 8.3 状态更新

首版使用定时重读训练运行真源，不为一个低频长任务新增必要性不足的专用协议。
如使用现有 Prefect 运行通知，通知只触发重读，数据库训练运行表仍是真源。

## 9. GPU 加速设计

### 9.1 Windows 后端选择

LightGBM 官方将 GPU 后端分为：

- `device_type=gpu`：OpenCL 实现，Windows 可用，主要将直方图计算放到 GPU；
- `device_type=cuda`：CUDA 实现，当前 LightGBM 官方不支持 Windows。

因此 QuantX Windows Dev 只实施 `LIGHTGBM_OPENCL_GPU`，不尝试在当前运行形态中启用
`device_type=cuda`。不为 GPU 恢复 WSL/Linux 生产链路或第二套 QuantX 运行形态。

### 9.2 环境准备

GPU 实施阶段需要：

1. 安装与驱动匹配的 NVIDIA CUDA Toolkit/OpenCL 开发环境。
2. 准备 CMake、Visual Studio Build Tools 和 LightGBM Windows GPU 构建所需依赖。
3. 对锁定的 LightGBM 4.7.0 源码执行 `USE_GPU=ON` 构建。
4. 产生本机可重复安装的 wheel，记录 wheel SHA-256、编译器、CMake、Boost、OpenCL 和
   Python ABI 信息。
5. 在 QuantX `.venv` 内安装，不覆盖 QMT 的 `xtquant-demo` Python 环境。
6. 运行微型 GPU 训练探针，必须看到 LightGBM 选中 RTX 4070 并成功产生模型。

普通 `quantx.ps1 up` 不负责编译或修复 GPU 环境。GPU 构建是显式的一次性依赖准备，
失败不得影响 full/live CPU 基线。

### 9.3 后端能力与解析

Worker 对外只报告脱敏能力：

- `CPU_AVAILABLE`；
- `GPU_UNAVAILABLE_BUILD`；
- `GPU_UNAVAILABLE_RUNTIME`；
- `GPU_INSUFFICIENT_MEMORY`；
- `GPU_UNQUALIFIED`；
- `GPU_AVAILABLE`。

`AUTO` 解析规则：

1. GPU 必须为 `GPU_AVAILABLE`；
2. 预估峰值显存不得超过可用显存预算；
3. 当前数据规模必须达到已验证有加速收益的阈值；
4. 解析结果必须在提交确认页展示。

`GPU_REQUIRED` 在任一条件不满足时预检失败。`CPU` 从不尝试初始化 GPU。

### 9.4 LightGBM 参数与精度

LightGBM 官方建议 GPU 使用较小 `max_bin`，且 OpenCL GPU 默认使用 32 位浮点累加。
这两点都可能改变概率输出，因此不允许仅在 GPU 运行中暗中替换参数。

实施规则：

- 将 `max_bin` 显式加入版本化 LightGBM 配置；
- CPU/GPU 对等验证必须使用相同 `max_bin`、参数、数据、切分和种子；
- 分别评估 OpenCL FP32 和 `gpu_use_dp=true`；
- 未通过概率质量对等验证时，GPU 保持 `GPU_UNQUALIFIED`；
- GPU 训练的 LightGBM 文本模型必须能被 CPU 运行时安全加载并产生符合容差的
  预测结果。

### 9.5 CPU/GPU 资格验证

在 RTX 4070 上启用 `GPU_AVAILABLE` 前，使用同一个冻结黄金面板执行：

1. CPU 基线运行；
2. GPU FP32 至少三次重复运行；
3. 必要时 GPU FP64 至少三次重复运行；
4. GPU 训练模型的 CPU 推理复核；
5. 训练耗时、端到端耗时、CPU 占用、内存和显存峰值记录。

初始验收门禁：

- 无 NaN、Inf、概率饱和或模型加载失败；
- CPU/GPU 的 ACTIVE 资格结论不得翻转；
- Brier 相对差异不超过 0.5%；
- ECE 绝对差异不超过 0.002；
- Top20 成分重合率不低于 90%；
- GPU 端到端训练耗时相对 CPU 至少有可重复的 20% 改善，否则不作为
  `AUTO` 推荐后端；
- 峰值显存不超过本机可用显存预算的 80%。

上述数值是 GPU V1 资格验证门禁。若黄金面板实测显示某项容差不合理，必须以
独立证据和文档版本更新门禁，不在代码中增加隐藏容差。

### 9.6 推理决策

V1 推理保持 CPU：

- Logistic 使用现有安全 JSON 系数和 CPU 矩阵计算；
- LightGBM 使用现有文本模型和 CPU `Booster.predict()`；
- 校准、OOD、置信度、排名和候选投影保持 CPU；
- 推理不依赖 CUDA Toolkit、OpenCL 编译环境或 GPU 存在；
- GPU 训练机器重启、GPU 不可用或驱动变更不影响已发布模型的日常推理。

只有将来出现经证据确认的盘中大批量低延迟需求，且 CPU 推理达不到明确 SLA 时，
才单独设计 GPU 推理运行时。不在本计划中预埋 ONNX、Treelite 或 CUDA 分支。

## 10. 环境与产物证据

每次运行 manifest 新增：

- 请求后端与解析后端；
- OS 与 Python 版本；
- LightGBM、scikit-learn、NumPy 版本；
- LightGBM CPU/GPU 构建能力；
- GPU 型号、Compute Capability、脱敏驱动版本；
- OpenCL platform/device ID；
- `max_bin`、`gpu_use_dp` 和实际 LightGBM 参数；
- 训练耗时、端到端耗时、CPU/内存/GPU/显存峰值；
- 资格验证版本。

不写入设备序列号、Windows 凭据、账户信息或原始本地路径。

## 11. 实施阶段

### 阶段 A：CPU 网页训练纵切

目标：不改变当前模型算法，先完成可审计的 Web -> API -> Worker -> Research 闭环。

任务：

1. 新增数据集版本、训练配置和训练运行表及 migration。
2. 在纯域模型中实现时间切分、状态转换和幂等规则。
3. 实现数据集预检和不可变 manifest。
4. 将现有 CLI 训练核心抽成可由 Worker 调用的用例，CLI 继续调用同一用例。
5. 新增 Prefect 训练 flow、并发限制、子进程、进度和取消收敛。
6. 新增 GraphQL 预检、发起、列表、详情和取消契约。
7. 实现 Web 向导、列表和运行详情。
8. 仅开放 CPU 后端。

完成标志：用户可在 Web 提交一次 CPU 开发训练、查看进度、取消并获得验证报告。

### 阶段 B：冻结测试与质量审查

1. 将 `DEVELOPMENT` 与 `FINAL_EVALUATION` 分离。
2. 实现冻结测试使用计数和配置锁定。
3. 展示完整概率、排名、数据和稳定性报告。
4. 实现 `BLOCKED/SHADOW_ELIGIBLE/ACTIVE_ELIGIBLE` 结论。
5. 实现同坐标模型对比。
6. 将成功最终评估接到现有模型登记流程。

完成标志：开发运行不能登记模型；只有已锁定、通过冻结测试和安全产物校验的
运行才可登记 CANDIDATE。

### 阶段 C：Windows OpenCL GPU 资格验证

1. 准备可重复的 LightGBM 4.7.0 OpenCL GPU wheel。
2. 实现后端能力探针和脱敏环境证据。
3. 实现 LightGBM CPU/GPU 后端适配，Logistic 保持 CPU。
4. 建立固定黄金面板和 CPU/GPU 对等性集成测试。
5. 执行 FP32/FP64、精度、候选重合率和性能基准。
6. 只在全部门禁通过后将能力标记为 `GPU_AVAILABLE`。
7. Web 开放 `AUTO` 和 `GPU_REQUIRED`。

完成标志：RTX 4070 上 GPU 训练可重复通过质量与性能门禁，且 CPU 推理可安全加载
GPU 训练模型。

### 阶段 D：运行硬化

1. 验证 full/live 交易时段排队规则。
2. 验证 API/Engine/QMT 重启不中断 Worker 训练真源。
3. 完成失败、取消、幂等重试、产物损坏和 GPU 丢失测试。
4. 完成文档、GraphQL 产物、Web 构建和运维状态验收。

## 12. 预计文件影响

### Domain / Application

- `packages/domain/src/quantx_domain/`：训练配置、切分和状态规则。
- `packages/application/src/quantx_application/`：预检、提交、取消和最终评估用例。

### Infrastructure

- `packages/infrastructure/alembic/versions/`：新增数据集、配置和运行表。
- `packages/infrastructure/src/quantx_infrastructure/models/`：训练持久化模型。
- `packages/infrastructure/src/quantx_infrastructure/repositories/`：幂等、状态转换和取消收敛。

### Research / Worker

- `apps/research/src/quantx_research/next_day_selection_*`：将 CLI 编排与可复用训练核心分离。
- `apps/worker/src/quantx_worker/prefector/flows/`：新增训练 flow 和 GPU 能力探针。

### API / Web

- `apps/api/src/quantx_api/gqlapi/`：训练 GraphQL schema、type、resolver 和权限策略。
- `apps/web/src/features/research/`：训练向导、列表、详情、质量报告和对比。

## 13. 测试计划

### 13.1 纯逻辑

- 时间切分不重叠、无未来数据；
- 精确 T+1 标签；
- 冻结测试不参与调参；
- 配置哈希稳定且字段变更能改变哈希；
- 运行状态转换和取消规则；
- 门禁结论无隐藏阈值。

### 13.2 仓储与 API

- 幂等提交不重复训练；
- 同时最多一个 RUNNING；
- 乐观锁阻止丢失更新；
- 无权用户不能发起或取消训练；
- GraphQL 不返回服务器路径、密钥或未脱敏异常；
- 开发运行不得登记模型。

### 13.3 Worker

- 队列、执行、取消、失败和重试；
- 交易时段不启动高资源训练；
- Worker/API 重启后状态可恢复；
- 失败产物不可登记；
- 进度单调且不超过总单元。

### 13.4 GPU

- 当前 CPU-only wheel 正确报告 `GPU_UNAVAILABLE_BUILD`；
- GPU wheel 能选中 RTX 4070；
- `CPU` 不初始化 GPU；
- `GPU_REQUIRED` 不可用时预检失败；
- `AUTO` 在确认页显示解析结果；
- 已排队运行不静默改变后端；
- CPU/GPU 对等性、重复性、显存峰值和耗时门禁；
- GPU 训练模型的 CPU 推理复核。

### 13.5 Web

- 四步向导的默认值、边界和键盘可达性；
- 时间轴与服务端切分一致；
- 预检失败时不提交；
- 后端解析、排队原因和 SHADOW 限制可见；
- 运行进度、取消和错误不伪装成成功；
- 质量图表对缺失数据显示不可用，不填零；
- 不同数据/切分坐标的模型不宣称直接胜负。

## 14. 验收标准

1. 用户可在 Web 选择认证数据集和时间区间，获得服务端预检。
2. 训练、校准、验证和冻结测试无重叠且可通过 manifest 复现。
3. 网页发起的训练在 Worker/Prefect 后台运行，API 与 Engine 不执行模型计算。
4. 任务可排队、可取消、可审计，进程重启后不丢失真源。
5. 质量页完整展示概率、排名、数据、稳定性和门禁证据。
6. 只有成功的最终评估可登记 CANDIDATE，仍需人工切换 SHADOW/ACTIVE。
7. 当前 CPU-only 环境不会伪报 GPU 可用。
8. RTX 4070 只有通过固定黄金面板的精度、候选、性能和资源门禁后才可被 `AUTO` 选中。
9. GPU 训练失败不会在同一运行中静默改用 CPU。
10. 已发布模型的每日推理不依赖 GPU，full/live、QMT 和 Engine 基线不受 GPU 环境影响。
11. GraphQL 变更后完成公开 Caddy 端点 codegen、check、lint、test 和 build。
12. 根边界测试、研究训练测试、Worker 集成测试和 GPU 可选集成测试全部通过。

## 15. 风险与控制

| 风险 | 控制 |
| --- | --- |
| 用户反复查看测试集后挑选模型 | 开发/最终评估分离，锁定配置，记录测试集访问次数 |
| 数据泄漏 | 时间切分纯函数、T+1 精确日历、point-in-time 股票池和 manifest |
| 用户降低门禁制造好模型 | ACTIVE 门禁系统版本化，表单不可改 |
| 训练拖慢实盘服务 | Worker 隔离子进程、单运行、交易时段排队和低优先级 |
| GPU 安装损坏 CPU 环境 | 锁定 wheel 与 SHA-256，只改 QuantX `.venv`，不改 QMT Python |
| GPU 非确定性改变概率 | CPU/GPU 对等资格验证、多次重复、门禁不翻转 |
| GPU 显存不足 | 提交前估算、80% 预算门禁、不在运行中静默降级 |
| GPU 对小数据反而更慢 | 代表数据性能基准，`AUTO` 只在有证据收益时选 GPU |
| GPU 训练后推理机无 GPU | 产物使用可移植 LightGBM 文本模型，每日 CPU 推理 |

## 16. 已冻结设计决策

1. 首版只使用 QuantX 已认证数据，不上传自定义数据。
2. 网页只手工发起，不自动重训。
3. 训练、校准、Walk-forward 验证和冻结测试分离。
4. 开发运行与最终评估分离。
5. API 不训练，Worker/Prefect 运行隔离子进程。
6. GPU 只加速 LightGBM 训练；Logistic 和日常推理保持 CPU。
7. Windows 只使用 LightGBM OpenCL GPU 后端，不引入新 Linux/WSL 运行形态。
8. GPU 不可用或不合格时显式报告，不伪装加速。
9. 运行开始后不静默更换 CPU/GPU 后端。
10. 训练成功不自动登记或发布模型。

## 17. 参考资料

- [LightGBM Installation Guide](https://lightgbm.readthedocs.io/en/stable/Installation-Guide.html)
- [LightGBM Parameters: device_type and GPU parameters](https://lightgbm.readthedocs.io/en/latest/Parameters.html)
- [LightGBM GPU Tutorial](https://lightgbm.readthedocs.io/en/latest/GPU-Tutorial.html)
- [scikit-learn FAQ: GPU support](https://scikit-learn.org/stable/faq.html#will-you-add-gpu-support)
- `docs/次日上涨概率选股软件_完整功能设计方案.md`
- `docs/engineering/api/NEXT_DAY_SELECTION.md`
