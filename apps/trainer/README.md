# Trainer

独立开发训练服务正在实施，完整范围见
[实施方案](../../docs/plans/独立训练服务架构与实施方案.md)。

目前提供本地配置校验和开发控制面只读预检。尚未迁移 Worker 调度、启用训练部署或完成 Windows/GPU 验收，不能将预检通过视为服务 ready。

## Conda 环境安装

Windows 独立训练代码目录中执行：

```powershell
.\ops\quantx.ps1 bootstrap -Environment dev -Component trainer -CondaExecutable C:\Users\limao\miniconda3\Scripts\conda.exe
```

将 Conda 路径替换为本机值。此入口在加载业务环境或创建主链运行目录之前分流，仅创建该 Conda 安装下的 `envs\quantx-train`，使用 conda-forge 的 Python 3.13 和 pip，不继承默认安装包，不更新既有 `quantx` 或券商环境。已存在的完整环境只校验身份；半成品、错误 Python 版本及目录链接均拒绝，不自动覆盖。

该步骤只准备独立解释器，项目依赖同步、GPU 构建资格和 Trainer 常驻服务仍需后续部署。`up/down/status/logs -Component trainer` 尚未实现；生产 `full` 生命周期不包含此环境。

## 独立配置

使用本机私有 TOML 文件显式提供全部字段，不读取 API `.env`，不提交凭据：

```toml
environment = "development"
code_root = 'D:\QuantXTraining\code'
production_root = 'D:\QuantXProduction\code'
state_root = 'D:\QuantXTraining\state'
database_url = "postgresql+asyncpg://trainer:REPLACE_ME@DEV_HOST:5432/quantx_dev"
prefect_api_url = "http://DEV_HOST:4200/api"
prefect_pool = "quantx-train-pool"
prefect_pool_id = "084451cb-a87f-4f06-9eb2-cae3db39804d"
```

示例路径、端点及 Pool UUID 需替换为实际部署值。Pool UUID 来自显式创建的开发专用 Process Pool，不能沿用示例值或生产 Pool。生产目录必须如实配置；数据库名后缀与 URL 校验不能代替远端身份和研究表权限预检。

在独立 `quantx-train` Conda 环境安装此包后运行：

```powershell
conda run -n quantx-train quantx-trainer-config-check --config D:\QuantXTraining\trainer.toml
```

该命令验证配置字段、开发目标、代码/状态/生产目录隔离、实际运行解释器及代码目录；不会建立连接、创建 Pool、安装依赖或启动服务。应用通过校验后才能接入后续的权限预检和调度。

子进程环境采用系统必需变量白名单，显式设置开发目标及关闭实盘门，不继承券商参数、设备密钥、生产环境文件或 Python 搜索路径。后续进程启动必须使用完整的该环境映射，不能与父进程环境再次合并。

## 控制面只读预检

```powershell
conda run -n quantx-train quantx-trainer preflight --config D:\QuantXTraining\trainer.toml
```

先执行相同的本地校验，再用配置中指定的角色连接开发 PostgreSQL，以只读事务查询系统目录。实际数据库、登录角色必须匹配配置；拒绝超级用户、数据库所有者、建库/建角色/复制/RLS 绕过、角色成员身份、数据库 CREATE/TEMP、schema CREATE、可执行的业务 SECURITY DEFINER 函数、序列和其他数据库 CONNECT 权限。

训练角色的有效表权限必须精确为下表，且不具有所有权或授权转授能力。列级授权也参与检查，其他业务表（含交易与模型发布表）不能有读写权限：

| public 表 | 权限 |
| --- | --- |
| stock_selection_dataset_versions | SELECT, INSERT |
| stock_selection_training_specs | SELECT |
| stock_selection_training_runs | SELECT, UPDATE |
| research_preparation_jobs | SELECT, UPDATE |
| runtime_component_heartbeats | SELECT, INSERT, UPDATE |

部署时需审查 PUBLIC 默认权限及现有授权；单独为 Trainer 执行 GRANT 不能抵消已有 PUBLIC 权限。预检不自动创建角色、修改权限或启动外部服务。

数据库预检成功后，才对配置的 Prefect 发起 GET，验证专用 Pool 名称、UUID、Process 类型及未暂停状态；不会跟随重定向或继承 HTTP 代理。成功输出 `PREFLIGHT_PASSED`，拒绝退出码为 `2`，仅输出稳定错误码。此命令不领取任务、不注册部署、不写心跳，也不证明数据传输或 GPU 可用。

## 主机高资源门禁

Research 的研究、数据预检、认证、训练、GPU 探测和资格命令，以及直接训练/准备作业入口，共用 `quantx_infrastructure.training_host_guard`。仅重绘已有报告无需高资源准入。门禁拒绝或运行中保护停止的 CLI 退出码为 `75`，不能按训练成功处理。

机器策略位置固定，不随代码目录、Conda、`ENV` 或用户账户改变：

- Windows：系统 Common AppData 下的 `QuantX\training\policy.toml`（通常为 `C:\ProgramData\QuantX\training\policy.toml`）。
- macOS：`/Library/Application Support/QuantX/training/policy.toml`。
- Linux：`/var/lib/quantx/training/policy.toml`。

部署人员预先创建目录并限制配置修改权限，执行身份需要读取策略及写入锁/运行证据的权限；程序不会在策略缺失时创建宽松默认值。以下是格式示例，资源数值必须根据目标机器验证后确定，并覆盖所有缓存、控制目录和输出所在卷：

```toml
cpu_threads = 2
max_rss_mib = 4096
minimum_available_memory_mib = 4096
minimum_free_disk_mib = 10240
sample_seconds = 2
stop_grace_seconds = 15
disk_roots = ['D:\QuantXTraining\state']

[[windows]]
weekdays = [0, 1, 2, 3, 4, 5, 6] # 星期一为 0
start = "20:00"
end = "23:00"
```

允许窗口采用上海时区、起点包含而终点不包含。跨午夜分为两个窗口；所有工作日 09:15–16:30 始终禁止高资源任务，开发配置和配置窗口均不能覆盖这一保护段。保守保护也包括工作日节假日，不读取生产交易日历。

实际计算进程持有同一 OS 文件锁。运行中持续检查时段、进程树 RSS、CPU 消耗、可用物理内存和配置卷剩余空间，并记录进程创建时间及子进程身份。超限先通过主线程中断请求退出，宽限期后强制退出。它不承诺算法断点续算；任务结果收敛必须读取退出状态与完整制品证据。

异常退出会保留 `owner.json`，即使 OS 已释放锁也不会仅凭 PID 不存在自动再次执行。恢复工具、Windows 进程树身份验收和显存预算仍待后续实现；不得直接删除运行证据来恢复排队任务。门禁通过不表示远程控制面或 GPU 已合格。

## 冻结文件传输契约

`quantx_contracts.training_bundle.TrainingBundle` 定义版本 1 的数据集/结果/发布包文件清单：`kind`、`source_id`、相对文件名、大小和 SHA-256。`bundle_id` 是规范化清单的 SHA-256；条目排序不改变身份，任何文件证据变化都会产生不同身份。此清单是传输完整性证据，不替代现有数据认证、最终评估或发布门禁。

`quantx_infrastructure.training_bundle_store` 提供本地目录和已认证 SFTP 会话的只读通道，以及原子缓存接收。对象只按 `<bundle_id>/<relative_file>` 寻址，不接受 macOS/Windows 服务器绝对路径。文件名规则拒绝路径穿越、盘符、Windows 保留设备名、大小写冲突及文件/目录冲突；本地临时文件也拒绝符号链接与硬链接。

调用方持有主机门禁并显式提供磁盘保留量。接收先写 `<bundle_id>.partial`，每个文件完整校验后才落位；全部条目和目录内容校验通过才原子重命名为 `<bundle_id>`。中断保留暂存文件，重试复用已验证文件。已有完整缓存必须再次校验，损坏时报告错误，不覆盖或重跑训练；这里没有自动缓存清理。

SFTP 适配器接收已验证主机身份的 SFTPClient，由部署层管理受限账号、密钥和 known_hosts；适配器不接受未知主机密钥、不创建 shell 会话，读写有显式超时。

`SFTPBundlePublisher.publish` 先验证本地制品，再上传到远端 `<bundle_id>.partial`。每个文件使用独占创建的临时文件，写完读回校验后通过 OpenSSH `posix-rename` 扩展落位；清单完全匹配后使用不覆盖已有目录的 rename 完成发布，并再次验证可读性。服务端不支持所需原子操作或任何校验失败时保留本地制品并报告失败。重试复用已验证文件；最终 rename 的确认丢失时，下次先验证完整目录，不重复上传。

调用方必须串行化同一制品发布，仅在 `publish` 返回后登记引用，且数据库登记成功前保留本地产物。此模块不修改任务成功状态或模型发布状态。当前完成了清单、接收、发布及协议模拟往返测试；SSH 身份配置、真实服务器原子语义、数据库登记及调度接入仍待实施和跨机器验收。
