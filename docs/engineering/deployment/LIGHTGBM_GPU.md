# Windows LightGBM GPU 训练

Research 和 Worker 锁定 LightGBM 4.6.0，使用官方 Windows wheel 的 OpenCL 后端。
2026-09-09 本机 RTX 4070 验证中，4.7.0 PyPI Windows wheel 不含 GPU learner；
Conda-forge 4.7.0 `cpu_h66f53fe_8` 虽识别 GPU，但在 OpenCL 内核构建时崩溃。
4.6.0 官方 Windows wheel 已通过实际 GPU 训练。不要直接升级或重装 4.7.0。

依赖只安装到 `quantx` Conda 环境；不修改 QMT 的 `xtquant-demo` 环境。
标准依赖同步使用仓库锁文件及显式 Conda Python；需要修复 LightGBM 时：

```powershell
conda run -n quantx python -m pip download lightgbm==4.6.0 --only-binary=:all: --no-deps --index-url https://pypi.org/simple --dest .runtime/research-gpu/official-wheel
conda run -n quantx python -m pip install --force-reinstall --no-deps .runtime/research-gpu/official-wheel/lightgbm-4.6.0-py3-none-win_amd64.whl
conda run -n quantx python -m quantx_research.cli probe-lightgbm-gpu
```

Windows wheel SHA-256 为
`37089ee95664b6550a7189d887dbf098e3eadab03537e411f52c63c121e3ba4b`。
资格验证同时核对 wheel 哈希、版本和实际加载 DLL 的字节，不能用别的安装包冒充。
源码构建证据继续使用独立的 schema-v1；官方 wheel 使用 schema-v2，记录实际来源，
不伪造本机编译器或构建记录。

GPU 能运行与模型训练资格是两个条件。使用现有认证面板完成资格验证：

```powershell
conda run -n quantx python -m quantx_research.cli qualify-lightgbm-gpu --dataset-dir .runtime/research-datasets/next-day-selection-v1 --build-evidence .runtime/research-gpu/official-wheel/lightgbm-4.6.0-py3-none-win_amd64.whl
```

验证使用正式训练的特征白名单，排除次日价格、次日收益等结果列，比较 CPU 与各三次
GPU FP32/FP64 运行的预测质量、速度、显存和 CPU 模型加载结果。
`next-day-selection-gpu-v2` 会使旧特征口径产生的资格证书失效。
通过后证书位于 `.runtime/research-gpu/lightgbm-qualification.json`。
网页“GPU 资格验证”使用上述默认位置的官方 wheel；自定义构建可通过
`QUANTX_LIGHTGBM_BUILD_EVIDENCE` 显式指定证据路径。
Worker 每分钟启动独立探测进程，下一次心跳会读取新依赖和证书，无需重启交易进程。

训练选择 `AUTO` 时，在资格通过、样本规模及显存满足要求后自动使用 GPU；
显式选择 `CPU` 仍用 CPU。没有训练任务时 GPU 无需持续高占用。

上游资料：[安装指南](https://lightgbm.readthedocs.io/en/v4.6.0/Installation-Guide.html)、
[4.6.0 官方包](https://pypi.org/project/lightgbm/4.6.0/)。
