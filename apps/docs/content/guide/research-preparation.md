# 研究训练数据准备

从研究中心的准备度入口进入 **数据管理 → 研究训练数据**。

1. 设置样本日期、股票范围、基准和最低上市天数，保存配置。
2. 检查覆盖。缺少行情时先预览下载范围，再主动提交日线下载。
3. 配置历史证据文件并重新检查。全部认证条件满足后，填写唯一版本并发起认证。
4. 认证完成后选择“使用此数据集预检”。这不会提交训练。

留空股票代码时，历史 ST 文件用于确定历史范围；当前证券列表仅能辅助下载，不能证明历史全市场完整。文件中的历史事实和来源真实性由数据维护者负责，不能用当前证券状态回填。

## 历史证据设置

在 Windows 运行端设置 `QUANTX_RESEARCH_EVIDENCE_ROOT`，默认目录为仓库内 `.runtime/research-evidence`。API 与 Worker 使用同一目录。Web 只选择该目录内的 CSV/Parquet 文件引用，不接受任意绝对路径。

文件名使用英文字母、数字、下划线、点和短横线。每份文件至少包含 `event_date`、`stock_code` 及对应字段：

| 历史证据 | 字段             | 值               |
| -------- | ---------------- | ---------------- |
| ST       | `is_st`          | 布尔值或 0/1     |
| 行业     | `industry`       | 非空历史行业分类 |
| 退市风险 | `delisting_risk` | 布尔值或 0/1     |

日期使用 `YYYY-MM-DD`，股票代码使用 `600000.SH` 等规范形式，日期和股票组合不得重复。缺失历史数据不会自动下载，也不会被填为“正常”。

## 下载与重试

下载仅使用既有 QMT Agent 持久化链路，覆盖股票、基准日线、预热缓冲和次日标签所需日期，不下载分钟/Tick、不触发实时快照计算。

任务状态在数据库中保存。关闭页面不会取消任务；失败后“重试任务”沿用原配置和分块身份。修改设置后要发起新任务。下载后自动复查覆盖，不自动认证。交易关键时段任务可能继续排队。

## 训练能力

部署 `stock-selection-training-capability` 每分钟独立探测 Worker 的实际环境；`research-preparation-dispatch` 执行准备任务。部署定义在 `apps/worker/prefect.yaml`，使用现有 `quantx-pool`。确保 Worker 有空闲进程槽运行能力探测，不能让长训练耗尽全部执行槽。

心跳有效期为 180 秒。读取失败、心跳过期、CPU 不可用、GPU 不合格是不同状态；GPU 不合格不会单独阻止 CPU 训练。

## Windows GPU 准备

在运行端使用现有构建脚本，明确传入 Research/Worker 的 Python：

```powershell
.\ops\windows\build-lightgbm-opencl-wheel.ps1 -SourceDirectory <已核验源码目录> -OutputDirectory <空构建目录> -Python <Research环境python.exe>
```

脚本锁定 LightGBM 4.7.0，要求 Windows C++、CMake、Boost 和 OpenCL 构建依赖。不要在 QMT Python 环境中安装。

安装前保留原依赖 wheel 和版本记录。用目标 Python 执行 `-m pip install --no-deps --force-reinstall <生成的wheel>`，并将 `QUANTX_LIGHTGBM_BUILD_EVIDENCE` 指向同目录构建证据 JSON。证据和 wheel 必须一起保留。恢复时用同一 Python 重新安装原 wheel，并移除旧资格证书；后续依赖同步若替换二进制，资格证书将自动失效。

安装依赖通过 Windows 运维执行，Web 不执行任意命令。页面选择认证数据集后可主动提交 GPU 资格验证；无数据集时显示等待状态。CPU reload、精度、排名、重复性、速度及显存门禁全部满足后才能显示 GPU 可用。

本功能的交付不意味着本机已经下载完整数据或通过真实 GPU 资格验证。
