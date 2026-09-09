# Trainer

独立开发训练服务正在实施，完整范围见
[实施方案](../../docs/plans/独立训练服务架构与实施方案.md)。

训练调度、能力心跳及正常/恢复制品回传已迁入本包。独立启停、准备任务交接和停止恢复已有实现；Windows/GPU 与跨机器验收仍未完成，不能将预检或本地测试通过视为服务 ready。

Worker 负责行情补数和冻结输入导出，发布回读后按原执行归属交接；Trainer 领取已交接认证任务及 GPU 准备任务，监督 Research 计算、核验和登记。公共目录校验与认证字段投影位于 `quantx_infrastructure.training_dataset_store`。进度、心跳和终态必须匹配领取时的 flow_run_id；两端保留输入尝试和计算进程身份、退出证据，心跳超时不代表进程停止。退出未确认保持 RUNNING，不开放重试；成功结果发布失败可恢复而不重算。Windows 子进程包含、组退出证明和数据库恢复已有实现，原生运行端验收仍待完成。

## 固定提交代码包

开发机使用显式 Conda Python 执行 `ops/trainer/package_code.py --revision <提交 SHA> --output <新输出目录>`。入口先解析一次提交，再从 Git 对象生成 `code.zip` 和 `manifest.json`，不复制工作区改动；输出目录已存在时拒绝覆盖。包内包含根 `pyproject.toml`、`uv.lock`、apps、packages、ops 和 tests，以保留完整 workspace 元数据及验收代码。这份源码包不会自动安装其中的应用。

清单记录提交、锁文件哈希、ZIP 大小/哈希及逐文件大小/哈希/权限；ZIP 顺序、时间戳和权限规范化，同一提交可重复生成。拒绝链接、特殊文件、路径穿越、Windows 保留名及大小写冲突。输出目录旁的 `.package-lock` 排除同目标并发打包；异常中止遗留锁时，先确认打包进程已退出，再由运维移除该锁。代码包只冻结部署输入，后续仍需核验、安装和运行目录写入保护；不代表依赖环境或 GPU 已验收。

解包时执行同一入口 `--bundle <包目录> --manifest-sha256 <打包端可信清单摘要> --output <新暂存代码目录>`；摘要是打包端 `manifest.json` 文件的 SHA256，应通过可信渠道传递，不能仅用收到的文件重新计算摘要作为可信依据。入口校验清单摘要、ZIP 摘要和精确文件集合、逐文件大小/哈希/权限，再原子发布新代码目录。校验失败清理暂存文件，既有目录拒绝覆盖。此命令只准备新目录，不切换当前服务；更新现用代码与依赖仍必须先完成排空及停止验收。

依赖导出使用 `--bundle <包目录> --manifest-sha256 <可信摘要> --export-dependencies --uv <uv绝对路径> --python <Conda Python绝对路径> --output <新依赖输出目录>`。入口在临时目录验证并解包源码，使用 `uv export --locked --offline` 导出 Trainer 外部依赖，禁止解释器下载、移除环境中的 UV 参数覆盖，不安装依赖。离线缓存不足时失败，不自动联网。生成的 `requirements.txt` 保留精确版本、环境标记和哈希；`dependencies.json` 绑定代码提交、代码清单摘要、锁文件摘要、依赖摘要及条目数。workspace 包仍需从同一代码包安装，GPU 构建资格仍需运行端验证，不能把这份外部依赖清单当作完整环境验收。

## Conda 环境安装

Windows 独立训练代码目录中执行：

```powershell
.\ops\quantx.ps1 bootstrap -Environment dev -Component trainer -CondaExecutable C:\Users\limao\miniconda3\Scripts\conda.exe
```

将 Conda 路径替换为本机值。此入口在加载业务环境或创建主链运行目录之前分流，仅创建该 Conda 安装下的 `envs\quantx-train`，使用 conda-forge 的 Python 3.13 和 pip，不继承默认安装包，不更新既有 `quantx` 或券商环境。已存在的完整环境只校验身份；半成品、错误 Python 版本及目录链接均拒绝，不自动覆盖。

该步骤只准备独立解释器，完整依赖、GPU 构建资格和常驻服务仍需部署验收。`up/down/status/logs -Component trainer` 已接入独立入口，必须显式传入 Trainer 配置和解释器；生产 `full` 生命周期不包含此环境。具体命令及状态语义见下文生命周期章节。

GPU 构建探针同样在微型拟合前检查显存并使用准入线程预算。资格测试遇到主机门禁拒绝立即中止，CLI 和准备入口保留保护退出码 `75`，不把它转换为普通拟合失败后继续下一轮试验。

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
transfer_config = 'D:\QuantXTraining\state\transfer.toml'
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
| stock_selection_dataset_versions | SELECT, INSERT, UPDATE |
| stock_selection_training_specs | SELECT |
| stock_selection_training_runs | SELECT, UPDATE |
| research_preparation_jobs | SELECT, UPDATE |
| runtime_component_heartbeats | SELECT, INSERT, UPDATE |

部署时需审查 PUBLIC 默认权限及现有授权；单独为 Trainer 执行 GRANT 不能抵消已有 PUBLIC 权限。预检不自动创建角色、修改权限或启动外部服务。

数据库预检成功后，才对配置的 Prefect 发起 GET，验证专用 Pool 名称、UUID、Process 类型及未暂停状态；不会跟随重定向或继承 HTTP 代理。随后验证 SFTP 身份与两个远端根目录的可读元数据。成功输出 `PREFLIGHT_PASSED`，拒绝退出码为 `2`，仅输出稳定错误码。此命令不领取任务、不注册部署、不写心跳或探测写权限，也不证明完整跨机器传输或 GPU 可用。

## SFTP 身份与存储配置

`transfer_config` 必须位于独立状态目录内，内容如下（替换为实际部署值）：

```toml
host = "dev-store.example.invalid"
port = 22
username = "quantx_trainer"
private_key = 'D:\QuantXTraining\state\identity\store_key'
known_hosts = 'D:\QuantXTraining\state\identity\known_hosts'
datasets_root = "/datasets"
artifacts_root = "/artifacts"
timeout_seconds = 10
```

密钥和 known_hosts 必须是状态目录内的普通文件，不接受符号链接、junction 或硬链接。部署时为服务身份设置私钥文件 ACL，并从可信通道核对服务端主机密钥后写入指定 known_hosts；非标准 SSH 端口使用 `[host]:port` 条目。不会自动接受或更新主机密钥，也不使用 SSH agent、默认私钥、相邻 OpenSSH 证书、密码或交互式口令回退。

服务端使用独立受限 SFTP 账号，只开放指定数据和制品目录，允许暂存、读回、删除临时文件和原子 rename。Trainer 既读取冻结数据，也需回传其构建认证的数据集；`DATASET`/`CERTIFICATION_INPUT` 发布到数据根目录，`RESULT`/`RELEASE` 发布到制品根目录，已完成 bundle 不覆盖。账号的文件系统隔离、禁止 shell/转发及权限配置属于部署验收，客户端预检不能证明这些服务端限制已生效。两个远端根目录必须独立且不嵌套。

`quantx_infrastructure.training_transfer.open_store` 连接现有 bundle 适配器，提供 `fetch` 和 `publish`；会话退出或失败均关闭连接。连接、认证、SFTP 子系统握手和文件 I/O 有显式超时。协议行为以 [Paramiko SSHClient 文档](https://docs.paramiko.org/en/stable/api/client.html) 及锁定依赖的实现为依据；额外的握手截止时间覆盖其 SFTP 建链前未应用通道读写超时的窗口。

## 主机高资源门禁

Research 的研究、数据预检、认证、训练、GPU 探测和资格命令，以及直接训练/准备作业入口，共用 `quantx_infrastructure.training_host_guard`。仅重绘已有报告无需高资源准入。门禁拒绝或运行中保护停止的 CLI 退出码为 `75`，不能按训练成功处理。

机器策略位置固定，不随代码目录、Conda、`ENV` 或用户账户改变：

- Windows：系统 Common AppData 下的 `QuantX\training\policy.toml`（通常为 `C:\ProgramData\QuantX\training\policy.toml`）。
- macOS：`/Library/Application Support/QuantX/training/policy.toml`。
- Linux：`/var/lib/quantx/training/policy.toml`。

部署人员预先创建目录并限制配置修改权限，执行身份需要读取策略及写入锁/运行证据的权限；程序不会在策略缺失时创建宽松默认值。以下是格式示例，资源数值必须根据目标机器验证后确定，并覆盖所有缓存、控制目录和输出所在卷：

```toml
cpu_threads = 2
gpu_max_memory_fraction = 0.8
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

`gpu_max_memory_fraction` 为必填，范围 `(0, 1]`，限制单 GPU 的整卡已用显存比例（包含其他进程），示例值需按 4070 实测确定。训练与资格基准只有进入 GPU 路径才注册显存检查；开始计算前检查一次，随后由主机门禁持续采样。超限记录 `HOST_GPU_MEMORY_BUDGET`，读数缺失/异常记录 `HOST_GPU_MEMORY_STATE_UNKNOWN`，沿用停止宽限期及强制退出流程。CPU 路径不注册该采样器；旧策略缺少字段会拒绝加载，部署前需显式补齐。

实际计算进程持有同一 OS 文件锁。运行中持续检查时段、进程树 RSS、CPU 消耗、可用物理内存和配置卷剩余空间，并记录进程创建时间及子进程身份。LightGBM 训练与资格基准显式使用当前进程准入策略的 `cpu_threads`，不以 `n_jobs=-1` 覆盖预算；未进入准入上下文的独立库计算使用单线程，不能据此绕过 CLI 准入。超限先通过主线程中断请求退出，宽限期后强制退出。它不承诺算法断点续算；任务结果收敛必须读取退出状态与完整制品证据。

异常退出会保留 `owner.json`，即使 OS 已释放锁也不会仅凭 PID 不存在自动再次执行。恢复工具、Windows 进程树身份验收和显存预算仍待后续实现；不得直接删除运行证据来恢复排队任务。门禁通过不表示远程控制面或 GPU 已合格。

## 冻结文件传输契约

`quantx_contracts.training_bundle.TrainingBundle` 定义版本 1 的数据集/结果/发布包文件清单：`kind`、`source_id`、相对文件名、大小和 SHA-256。`bundle_id` 是规范化清单的 SHA-256；条目排序不改变身份，任何文件证据变化都会产生不同身份。此清单是传输完整性证据，不替代现有数据认证、最终评估或发布门禁。

`quantx_infrastructure.training_bundle_store` 提供本地目录和已认证 SFTP 会话的只读通道，以及原子缓存接收。对象只按 `<bundle_id>/<relative_file>` 寻址，不接受 macOS/Windows 服务器绝对路径。文件名规则拒绝路径穿越、盘符、Windows 保留设备名、大小写冲突及文件/目录冲突；本地临时文件也拒绝符号链接与硬链接。

调用方持有主机门禁并显式提供磁盘保留量。接收先写 `<bundle_id>.partial`，每个文件完整校验后才落位；全部条目和目录内容校验通过才原子重命名为 `<bundle_id>`。中断保留暂存文件，重试复用已验证文件。已有完整缓存必须再次校验，损坏时报告错误，不覆盖或重跑训练；这里没有自动缓存清理。

SFTP 适配器接收已验证主机身份的 SFTPClient，由部署层管理受限账号、密钥和 known_hosts；适配器不接受未知主机密钥、不创建 shell 会话，读写有显式超时。

`SFTPBundlePublisher.publish` 先验证本地制品，再上传到远端 `<bundle_id>.partial`。每个文件使用独占创建的临时文件，写完读回校验后通过 OpenSSH `posix-rename` 扩展落位；清单完全匹配后使用不覆盖已有目录的 rename 完成发布，并再次验证可读性。服务端不支持所需原子操作或任何校验失败时保留本地制品并报告失败。重试复用已验证文件；最终 rename 的确认丢失时，下次先验证完整目录，不重复上传。

调用方必须串行化同一制品发布，仅在 `publish` 返回后登记引用，且数据库登记成功前保留本地产物。此模块不修改任务成功状态或模型发布状态。当前完成了清单、接收、发布、显式身份会话及本地真实 SSH/SFTP 协议往返测试；正常调度及数据库登记已接入；受限服务器部署、跨机器原子语义和完整运行验收仍待完成。

## 执行归属

### 制品恢复回传

在执行迁移 `20260910_0070`、完成隔离配置与受限存储部署后，Trainer 状态目录中已完成的 Research 结果可通过以下入口恢复回传：

```powershell
conda run -n quantx-train quantx-trainer publish-result --config D:\QuantXTraining\trainer.toml --run-id RUN_ID --owner ORIGINAL_FLOW_RUN_ID
```

运行归属取数据库原始 `prefect_flow_run_id`。入口使用 `state/runs/<run_id>` 的完整结果，以及 `state/control/<run_id>` 的 `request.json` 和 `process.json`；先完成本地与远端预检，并要求持久化证据确认原监督者及 Research 均已退出。每个 run 的 OS 文件锁排除并发回传。正常 Trainer 调度使用相同状态目录；运行端旧控制目录的核对与切换仍属于部署退出门。

回传前核对 Research 成功状态、冻结 spec 哈希、必需模型与元数据、全部文件清单和内容哈希；将 bundle 及原归属原子冻结为 `publication.json`。完成 SFTP 发布和读回后，仓储在归属约束下登记 `artifact_bundle`，再收敛成功。上传、登记或提交确认中断时保留全部本地文件，重复命令复用相同清单及远端完整 bundle；确认成功前不会删除本地产物。退出码 `3` 表示尚未完成，错误信息不包含连接凭据。该入口操作开发训练结果，生产模型的人工评估、导入与发布仍由独立链路处理。

已登记清单不可替换，成功 manifest 哈希必须与其中的 `manifest.json` 一致；领取归属变化或取消会阻止登记和成功写入。正常调度复用相同发布协调器，并在计算已结束时核验当前监督者自己的成功退出记录；每次调度先恢复已完成制品，再决定是否领取计算。上传中断保留 RUNNING，后续只恢复回传。

训练仓储复用已有 `prefect_flow_run_id` 记录领取归属，拒绝空值和截断标识。进度、成功、失败、取消收敛都必须传入领取时冻结的 `expected_flow_run_id`；仓储在行锁内刷新数据库状态并校验归属，旧执行者不能用会话缓存或终态幂等分支绕过校验。轮询也刷新状态，以观察外部取消。

Trainer 调度器使用同一约束：每次独立调用使用独立标识，发现归属或运行状态变化后，停止自己的子进程并返回 `OWNERSHIP_LOST`，不改写该任务的新状态。正常停止超过 5 秒后强制终止，再有界等待退出。执行心跳独立于进度每 10 秒持久化，不改变用户取消所依据的状态版本。恢复核对持久化的监督者和 Research 进程身份，证据未知时保留待核验。本地制品核验及上传开始前、执行期间每 10 秒也核对归属、取消状态并持久化心跳；控制面断联时发出取消并关闭传输。目录遍历、逐块哈希及上传前源文件复核响应同一取消信号，实际 I/O 线程退出后才释放发布锁，保留本地文件和已有清单。成功提交确认丢失后的重试只核对终态，不对已完成记录写运行心跳。操作系统文件 I/O 卡死的进程级有界退出、孤儿后代核对及运行端调度切换仍需完成。

## 调度配置与运行边界

`apps/trainer/prefect.yaml` 定义训练、能力心跳和准备任务三项部署，仅使用 `quantx-train-pool`，参数 `config_path` 从显式 `QUANTX_TRAINER_CONFIG` 渲染。准备入口处理已交接认证和 GPU 资格；相关调度代码已迁出通用 Worker，serve 注册独立部署并启动专用 ProcessWorker。已存在的远端旧部署仍需排空后处理，本地文件变化不会自动删除它们；运行端部署和切换尚未验收。

每次 flow 进入 `training_session` 时验证实际 Conda 前缀、代码目录与控制面权限；数据库引擎只使用该配置中的开发 URL。运行配置保存在任务上下文中，不改写父进程环境。计算子进程通过 `research_environment` 取得文件根目录与最小系统环境，移除数据库、Prefect 和 ambient SSH 凭据；管理进程的 `child_environment` 则保留显式开发控制面目标。

训练数据位于 `state/datasets`，控制证据位于 `state/control`，结果位于 `state/runs`。主机资源保护由独立机器策略控制，与开发 ENV 或旧 Worker 的 full/live 标记无关。准备任务暂时保留其原有 Worker 时间窗口检查，尚待职责交接。

### 冻结数据集发布与下载

在已认证目录位于本机 Trainer `state_root/datasets/<dataset_version>` 时执行：

```powershell
conda run -n quantx-train python -m quantx_trainer.main publish-dataset --config C:\Users\limao\QuantXTraining\state\trainer.toml --dataset-version <dataset_version>
```

命令重新核验认证证据，发布并读回 bundle 后，才登记不可替换的 `source_bundle`。迁移 `20260910_0074` 与开发角色的数据集 UPDATE 权限必须先由运维应用；本地尚未部署。旧数据集缺失传输清单时不可训练，需要先完成发布。Trainer 侧认证结果自动发布与恢复、Worker 冻结输入发布与原子交接已接入；运行端配置和跨机器验收尚未完成。

调度领取后按清单从受限 SFTP 拉取至 `state_root/dataset-cache/<bundle_id>`，逐文件校验后原子完成，再核对认证清单与数据库投影，成功后才启动 Research。磁盘保留空间来自主机策略；下载和核验共享运行归属/取消/心跳监督，部分文件可供后续重试复用。最终评估按父运行的 `artifact_bundle` 自动下载至 `state_root/parent-cache/<bundle_id>`，校验数据库 manifest 哈希、父运行身份和 Research 内部文件清单后传给计算子进程。Research 以认证清单和开发锁核验父制品，不再要求目录名等于 run_id。输入阶段建立按执行归属隔离的监督者/请求记录，后续调度只有在监督者确定退出、没有任何计算进程记录、数据库仍为原归属 PREFLIGHT 且尚无进度时才重新排队；已有取消请求则收敛为 CANCELLED。新领取复用完整/部分缓存并保留旧诊断证据。领取事务持有队列行锁时先落盘输入监督者证据，再提交 RUNNING；写入失败回滚，提交确认丢失仍保留证据。常驻进程中，本次尝试退出会在所有输入 I/O 结束后由原监督者写入结束标记；后续调度可恢复领取确认丢失、元数据断联、输入异常及取消所遗留的任务，不必等待整个进程退出。已有计算记录仍排除输入重排；结束标记无法写入则保留待核验。跨机器与进程级有界终止验收尚未完成。

### GPU 资格准备

`trainer-preparation` 领取 GPU 及已登记 `certification_input` 的 CERTIFY 任务，使用同一显式运行配置与 `quantx-train-pool`。Worker 只领取 COVERAGE、DOWNLOAD 和尚未交接的 CERTIFY；Worker 的 CERTIFY 路径只导出冻结输入并发布，不再执行认证计算。发布回读成功后按原归属原子交接；传输失败保留文件供重试。认证输入下载至 `state_root/certification-cache/<bundle_id>`，计算只使用冻结文件，结果登记与发布完成后才写入成功。Trainer 从已登记 bundle 拉取数据集，GPU wheel 证据固定为 `state_root/gpu/official-wheel/lightgbm-4.6.0-py3-none-win_amd64.whl`，资格结果写入 `state_root/gpu/qualification.json`，能力探测读取同一路径。Research 子进程仅接收文件路径和隔离环境，不接收数据库/Prefect 凭据。

准备监督端每 10 秒核对归属并写心跳，保存请求及计算进程身份，退出未确认保持 RUNNING。异步启动返回句柄前被打断时保留 STARTING，不开放重试。资格或认证计算完成后若数据库登记、制品发布或终态写入失败，保持 RUNNING；后续调度在新领取与计算门禁之前恢复结果处理。认证恢复核对原请求、零退出码、输入清单和结果制品，再幂等登记与发布，不重复计算。正常执行与恢复共用尝试锁，恢复要求明确零退出码、原请求绑定和完整结果，可在原监督进程仍存活时收敛，不重跑资格基准。主机门禁退出码 75 在记录退出后按原归属重新排队，后续重新领取生成新归属；其他明确的非零退出记录失败，即使存在 ready 结果也不登记成功。未知退出继续保留运行态。GPU 与认证输入下载阶段同样在领取提交前写入监督者证据；本次尝试结束后写结束标记。只在无计算记录且输入确认停止时按原归属重排，复用原缓存；写盘失败回滚领取。Worker 已支持成功导出的发布恢复；缺少或不完整计算退出记录的收敛、运行端部署与真实 GPU 资格验收仍待完成。


Worker 认证导出只允许 `environment=development`。启动开发 Worker 前，显式设置 `QUANTX_RESEARCH_TRANSFER_CONFIG`，指向 `<代码根目录>/.runtime/research-preparation/` 内的绝对 TOML 路径；字段与上述 SFTP 配置相同，私钥及 known_hosts 也必须位于该状态根目录内。认证输入与 Trainer 使用同一数据根目录；受限账号和服务端权限需单独部署。配置缺失会在导出前拒绝执行。导出按认证股票范围和基准指数冻结行情、元数据、复权证据及历史文件；已有完整输入可在同一任务重试中复用。Worker 心跳失败或取消会等待上传线程退出，再开放失败重试；交接确认丢失会先读取持久化交接状态，避免用旧归属写失败。

Worker 为每次领取生成独立执行归属，将认证请求与进程记录保存为作业目录内的 `request-<归属哈希>.json` 和 `process-<归属哈希>.json`。启动前落盘 STARTING，确认退出后保存退出码；重复归属不会覆盖既有证据。正常执行与恢复共用作业锁。调度先检查原请求与成功退出记录，重新核验并发布已有冻结输入，再交接 Trainer；锁占用、未知退出或已归 Trainer 的任务不会进入此恢复路径。

Worker 认证导出在确认退出码 75 后重新排队，并保留既有输入；其他明确非零退出或布尔 `ready=false` 会记录失败，正常执行与恢复保持一致。重新排队必须仍为原归属的未交接认证任务。非布尔结果、未知退出或原请求不匹配不会用于恢复写入。

Worker 领取前会在行锁内写入输入监督者证据，写盘失败回滚领取；尝试结束并等待工作退出后，原监督者写入结束标记。只有当前归属未交接认证任务、无任何计算记录、输入监督者已退出或尝试结束已确认，才可恢复输入阶段并重新领取。该机制覆盖领取确认丢失及启动前中断；任何计算记录（包括损坏/不完整记录）都会阻止输入重排。


### 停止新领取与恢复领取

在独立 `quantx-train` 环境使用同一显式配置执行：

```powershell
python -m quantx_trainer.main drain --config C:\Users\limao\QuantXTraining\state\trainer.toml
python -m quantx_trainer.main admission-status --config C:\Users\limao\QuantXTraining\state\trainer.toml
python -m quantx_trainer.main resume --config C:\Users\limao\QuantXTraining\state\trainer.toml
```

这些命令仅操作本地状态，不要求数据库、Prefect 或 SFTP 在线。`drain` 设置持久化标记，训练、认证及 GPU 准备的领取事务在写执行证据前检查标记；被拒绝的事务保持排队。标记操作与领取前回调由同一操作系统文件锁串行化，锁占用时拒绝新领取，命令返回失败可重试。已通过回调的领取可能在 `drain` 返回后完成提交，作为已有任务继续执行；结果恢复仍可发布和收敛。

`admission-status` 仅返回 `OPEN` / `DRAINING` 和 `execution_state=NOT_INSPECTED`，不证明任务或进程已退出，不能单凭它升级代码或依赖。完整排空核验和进程树有界退出仍在实施中。`resume` 显式删除排空标记，恢复新领取。


Windows 的 Research CLI 与准备计算现在经 `quantx_trainer.contained_process` 启动，在导入 Research 前将当前计算进程加入 Job Object。该 Job 仅启用 `KILL_ON_JOB_CLOSE`，不允许后代脱离；唯一句柄不可继承并保留至进程退出。创建、设置限制或加入失败均拒绝计算；保留计算进程原 PID、父进程和退出码，已有进程证据不变。机制依据 [Microsoft Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects)。macOS 保持直接计算入口。

真实 Windows 正常退出/强制终止后的后代清理测试位于 `tests/trainer/test_contained_process.py`，macOS 上跳过。Job 接入不等于独立服务停止验收：所有后代退出的确认、Trainer 本身崩溃、完整排空和运行端故障测试仍需完成，不能仅凭直接子进程退出开放升级。


### 前台常驻入口

完成独立环境、开发角色、专用 Pool 和 SFTP 配置后，在 `quantx-train` 环境执行：

```powershell
python -m quantx_trainer.main serve --config C:\Users\limao\QuantXTraining\state\trainer.toml
```

`serve` 获取 `state_root/service` 的操作系统单实例锁，隔离环境变量和 Prefect 本地配置目录，执行全部预检，再登记 `trainer-preparation`、`stock-selection-training-dispatch`、`stock-selection-training-capability` 三个每分钟部署。登记全部成功才启动专用 ProcessWorker；不存在 Pool 时不自动创建。部署仅保存显式配置文件路径与工作目录，不保存数据库连接信息。服务允许三个流程并行，实际计算仍由各流程的领取与主机资源门禁控制。

Windows 服务进程在加载 Worker 前加入退出清理的 Job Object。`serve` 保留已有排空标记；预检/登记/运行失败退出，单实例锁随进程退出释放。它是前台运行入口；后台 up/down/status/logs 已另行接入，完整排空和停止验收仍需完成。当前仅完成本地 SDK 部署契约与故障测试，未启动远端 Worker。


服务运行时可在另一终端执行 `python -m quantx_trainer.main status --config <同一配置绝对路径>`。查询不连接开发控制面，返回 `ALIVE`、`OFFLINE`、`STALE` 或 `UNKNOWN`。只有单实例锁占用、主机/PID/创建时间/解释器/配置哈希匹配且本地心跳不超过 30 秒，才报告 `ALIVE`；证据每 10 秒刷新。`phase` 区分预检、部署注册、进入 Worker 循环和退出阶段；不代表控制面或 GPU 健康。`OFFLINE` 表示查询时服务锁可获取，残留状态文件不会让服务显示在线。

没有适用组退出证明的服务状态携带 `execution_state=NOT_INSPECTED`，不能用作计算排空或升级许可。`UNKNOWN` / `STALE` 的命令退出码为 3；明确的本地在线/离线状态退出码为 0。


使用 `python -m quantx_trainer.main logs --config <同一配置绝对路径> --lines 100` 离线读取服务生命周期事件，行数范围为 1–1000。文件位于 `state_root/service/events.jsonl`，每个文件最多 1 MiB，保留三个轮转备份。事件仅包含时间、实例标识和固定阶段/失败代码，不收集配置、异常原文或任意输出；心跳刷新不重复写阶段事件。读取拒绝链接和非预期字段，损坏时返回稳定错误，不输出损坏原文。

追加 `--run-id <训练运行标识>` 可离线读取 `state_root/control/<run-id>` 的 stdout/stderr。每个流最多读取尾部 128 KiB、返回 `--lines` 行，按流分别输出带 run_id、stream、message 的 JSON；不推断两个流的时间顺序。路径与凭据按共用规则脱敏，截断读取丢弃首条残行；路径穿越、链接、非普通文件和读取错误被拒绝。准备任务使用 `--job-id <任务标识> --owner <执行归属>`，不能同时传入 `--run-id`。Trainer 将准备子进程 stdout/stderr 保存至该归属的独立尝试目录，查询按归属哈希定位并复用相同限额及脱敏规则；输出包含 job_id 和 owner，不混合不同重试。历史已丢弃的准备输出无法补回。

该入口查询服务生命周期日志；逐次计算的 stdout/stderr 仍位于对应运行控制目录，尚未统一到此查询入口。完整退出验收仍在实施中。


### 后台启动

在独立 `quantx-train` 环境执行 `python -m quantx_trainer.main up --config <配置绝对路径>`。命令启动后台 `serve`，使用明确的 Conda 解释器、代码目录和隔离环境，不继承终端输入输出；诊断通过上述结构化服务日志与状态入口读取。

启动前保存请求哈希和 STARTING 证据，启动后记录子进程身份。`up` 最多观察 10 秒；`ALIVE` 仅表示本地服务身份与心跳已确认，仍应检查阶段和控制面能力。超时返回 `START_PENDING`、退出码 3，保留原进程。再次调用先检查服务和上次启动证据；未知启动不会重复创建进程，只有确认旧尝试退出后才允许新的启动。已在线的服务直接返回当前状态，排空标记不会被清除。`state_root/service-launches` 保存逐次启动证据。

`up` 与协作式 `down` 已接入根运维脚本，Windows 完整停止/后代退出验收仍需完成。


### 协作停止

执行 `python -m quantx_trainer.main down --config <配置绝对路径>`，先持久化关闭新领取，再向已验证的服务实例写入停止请求。服务每秒检查请求，进入 STOPPING，取消并等待 Worker 清理；旧实例请求不会影响新实例。`down` 先等待最多 30 秒协作退出；macOS 确认服务锁释放返回 OFFLINE，Windows 按下述 Job 核验继续确认。无法确认时返回 STOP_PENDING 和退出码 3。配置不匹配或未知身份不会用于发送停止请求；心跳陈旧但进程身份已核验的实例仍可进入停止流程，也不会按猜测的 PID 终止进程。

此命令保留排空标记，后续启动仍需显式 resume 才恢复领取。返回结果的 `execution_state=NOT_INSPECTED` 表示尚未证明所有计算后代退出和数据库状态收敛；不能直接作为升级许可。Windows 超时强制终止与 Job 计数确认已接入，运行端实测和数据库状态收敛仍待完成。


### 根运维入口

Windows 使用独立解释器与配置；`up` 可替换为 `down`、`status`、`logs`、`doctor`、`drain` 或 `resume`，其中 `doctor` 执行 Trainer preflight：

```powershell
.\ops\quantx.ps1 up -Component trainer -Environment dev -TrainerPython C:\Users\limao\miniconda3\envs\quantx-train\python.exe -TrainerConfig C:\Users\limao\QuantXTraining\state\trainer.toml
```

路径须替换为本机实际独立环境与配置的绝对路径。`logs` 可指定 `-Tail 1..1000`。bootstrap 仍使用原来的 `-CondaExecutable`；运行命令不创建或安装环境。Trainer 不接受生产环境、交易模式或账户参数，普通生产 up/down 不代管 Trainer。

macOS 对应入口为 `./ops/quantx.sh status --component trainer --trainer-python /实际路径/quantx-train/bin/python --trainer-config /实际路径/trainer.toml`，日志行数使用 `--tail`。路由在普通服务状态目录和进程管理之前返回；两端均使用 Python `-I` 隔离搜索路径，应用再次校验真实 Conda、代码根目录和开发配置身份。


Windows `serve` 使用绑定实例标识的全局命名 Job，命名冲突拒绝启动，不打开并修改既有 Job。Windows `down` 在发送停止请求前打开该 Job，通过同一进程句柄核对创建时间和 Job 归属。协作宽限期后终止已核验的 Job，再等待最多 5 秒；仅活动进程数归零且服务锁释放时返回 `execution_state=GROUP_EXITED`，同时保留 `database_state=NOT_RECONCILED`。Job 不存在、身份/归属不明、查询/终止失败或计数未归零都保持待确认；不会把 API 接受终止当作退出证明。

命名与计数语义依据 [CreateJobObjectW](https://learn.microsoft.com/en-us/windows/win32/api/jobapi2/nf-jobapi2-createjobobjectw) 和 [Job 基本统计](https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-jobobject_basic_accounting_information)。真实 Windows 身份核验、组终止和计数归零测试位于 `tests/trainer/test_windows_service_job.py`，当前 macOS 未执行；不能据本地替身测试宣称运行端退出验收完成。


Windows 确认 Job 活动数归零后，在关闭控制句柄前原子保存 `service/group-exit-<实例>.json`，绑定实例、主机、配置哈希、观察时间和是否强制终止。后续离线 status 可读取同一实例的证明并返回 GROUP_EXITED；旧实例、配置变化、损坏记录或非零活动数不会被使用，已有证明不可覆盖。该证明仍不意味着数据库运行状态已收敛或可以升级代码。


Windows `down` 获得适用的 GROUP_EXITED 证明后，会在领取保持关闭且生命周期锁可获取时，使用显式开发配置执行已有训练/准备恢复逻辑；这可能重试已完成制品的发布，不重新计算。恢复仍逐条核验进程证据及执行归属，未知执行不会被批量标记失败。命令返回 recovered/pending 运行与准备任务标识；还有 Trainer 的 RUNNING 任务或控制面不可用时，返回 `database_state=PENDING` 和退出码 3，可在控制面恢复后再次 down 重试。Worker 尚未交接的认证导出不计入 Trainer 待处理集合。

`RECONCILED` 只说明这次开发数据库查询未发现剩余 Trainer RUNNING 任务；离线 status 仍只展示持久化组退出证明，不缓存数据库结论，也不自动恢复领取或授权升级。


### 锁定外部依赖

代码包制作前先使用明确 Conda Python 校验/导出根锁文件，避免元数据迁移后锁文件滞后：

```powershell
uv export --locked --package quantx-trainer --no-dev --no-emit-workspace --no-header --python C:\实际Conda路径\envs\quantx-train\python.exe --output-file requirements.txt
```

该清单保留每项外部依赖的版本和分发文件哈希，排除工作区源码包；不得把它当作完整代码包或 CUDA wheel 资格证明。本地已导出 `.runtime/trainer-deployment/requirements.txt` 及记录锁文件/清单 SHA256 的 `dependencies.json`，共 161 条依赖，无包版本升级或环境安装。独立代码打包、安装与运行期间冻结仍需完成。
