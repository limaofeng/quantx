# Trainer

独立开发训练服务正在实施，完整范围见
[实施方案](../../docs/plans/独立训练服务架构与实施方案.md)。

训练调度、能力心跳及正常/恢复制品回传已迁入本包。运行端启停、准备任务交接和 Windows/GPU 验收仍未完成，不能将预检或本地测试通过视为服务 ready。

数据认证已拆分为 Research 生成不可变文件、监督端核验并登记两步。公共目录校验与认证字段投影位于 `quantx_infrastructure.training_dataset_store`；当前准备任务仍由 Worker 监督，后续 Trainer 复用同一验证边界。数据库登记失败保留生成文件，文件校验失败则拒绝登记。准备任务的进度、心跳和终态写入必须匹配领取时的 flow_run_id；心跳超时仅表示未知，不能自动释放任务供重试。Worker 的失败收敛等待本次工作停止；直接准备子进程先终止并等待 5 秒，再强制结束并等待 5 秒。退出未确认则保持 RUNNING，不开放重试；Windows 后代进程仍需独立约束与验收。准备进程持久化退出证据与 Trainer 领取交接仍待实现。

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

服务端使用独立受限 SFTP 账号，只开放指定数据和制品目录，允许暂存、读回、删除临时文件和原子 rename。Trainer 既读取冻结数据，也需回传其构建认证的数据集；`DATASET` 发布到数据根目录，`RESULT`/`RELEASE` 发布到制品根目录，已完成 bundle 不覆盖。账号的文件系统隔离、禁止 shell/转发及权限配置属于部署验收，客户端预检不能证明这些服务端限制已生效。两个远端根目录必须独立且不嵌套。

`quantx_trainer.transfer.open_store` 连接现有 bundle 适配器，提供 `fetch` 和 `publish`；会话退出或失败均关闭连接。连接、认证、SFTP 子系统握手和文件 I/O 有显式超时。协议行为以 [Paramiko SSHClient 文档](https://docs.paramiko.org/en/stable/api/client.html) 及锁定依赖的实现为依据；额外的握手截止时间覆盖其 SFTP 建链前未应用通道读写超时的窗口。

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

`apps/trainer/prefect.yaml` 定义训练、能力心跳和 GPU 准备三项部署，仅使用 `quantx-train-pool`，参数 `config_path` 从显式 `QUANTX_TRAINER_CONFIG` 渲染。代码已从 Worker 删除这两项入口与清单；已存在的远端 Prefect 部署需要在运行端排空后单独处理，本地文件变化不会自动删除它们。独立 Worker 生命周期入口尚未实现，当前清单尚未部署验收。

每次 flow 进入 `training_session` 时验证实际 Conda 前缀、代码目录与控制面权限；数据库引擎只使用该配置中的开发 URL。运行配置保存在任务上下文中，不改写父进程环境。计算子进程通过 `research_environment` 取得文件根目录与最小系统环境，移除数据库、Prefect 和 ambient SSH 凭据；管理进程的 `child_environment` 则保留显式开发控制面目标。

训练数据位于 `state/datasets`，控制证据位于 `state/control`，结果位于 `state/runs`。主机资源保护由独立机器策略控制，与开发 ENV 或旧 Worker 的 full/live 标记无关。准备任务暂时保留其原有 Worker 时间窗口检查，尚待职责交接。

### 冻结数据集发布与下载

在已认证目录位于本机 Trainer `state_root/datasets/<dataset_version>` 时执行：

```powershell
conda run -n quantx-train python -m quantx_trainer.main publish-dataset --config C:\Users\limao\QuantXTraining\state\trainer.toml --dataset-version <dataset_version>
```

命令重新核验认证证据，发布并读回 bundle 后，才登记不可替换的 `source_bundle`。迁移 `20260910_0074` 与开发角色的数据集 UPDATE 权限必须先由运维应用；本地尚未部署。旧数据集缺失传输清单时不可训练，需要先完成发布。准备流程自动发布与持久化交接仍待接入。

调度领取后按清单从受限 SFTP 拉取至 `state_root/dataset-cache/<bundle_id>`，逐文件校验后原子完成，再核对认证清单与数据库投影，成功后才启动 Research。磁盘保留空间来自主机策略；下载和核验共享运行归属/取消/心跳监督，部分文件可供后续重试复用。最终评估按父运行的 `artifact_bundle` 自动下载至 `state_root/parent-cache/<bundle_id>`，校验数据库 manifest 哈希、父运行身份和 Research 内部文件清单后传给计算子进程。Research 以认证清单和开发锁核验父制品，不再要求目录名等于 run_id。输入阶段建立按执行归属隔离的监督者/请求记录，后续调度只有在监督者确定退出、没有任何计算进程记录、数据库仍为原归属 PREFLIGHT 且尚无进度时才重新排队；已有取消请求则收敛为 CANCELLED。新领取复用完整/部分缓存并保留旧诊断证据。领取事务持有队列行锁时先落盘输入监督者证据，再提交 RUNNING；写入失败回滚，提交确认丢失仍保留证据。常驻进程中，本次尝试退出会在所有输入 I/O 结束后由原监督者写入结束标记；后续调度可恢复领取确认丢失、元数据断联、输入异常及取消所遗留的任务，不必等待整个进程退出。已有计算记录仍排除输入重排；结束标记无法写入则保留待核验。跨机器与进程级有界终止验收尚未完成。

### GPU 资格准备

`trainer-gpu-preparation` 仅领取 GPU 类型准备任务，使用同一显式运行配置与 `quantx-train-pool`。Worker 仅领取 COVERAGE、DOWNLOAD、CERTIFY；构建认证的导出交接尚未完成。Trainer 从已登记 bundle 拉取数据集，GPU wheel 证据固定为 `state_root/gpu/official-wheel/lightgbm-4.6.0-py3-none-win_amd64.whl`，资格结果写入 `state_root/gpu/qualification.json`，能力探测读取同一路径。Research 子进程仅接收文件路径和隔离环境，不接收数据库/Prefect 凭据。

准备监督端每 10 秒核对归属并写心跳，保存请求及计算进程身份，退出未确认保持 RUNNING。异步启动返回句柄前被打断时保留 STARTING，不开放重试。资格计算完成后若数据库登记失败，保持 RUNNING；后续调度在新领取与计算门禁之前重试登记。正常执行与恢复共用尝试锁，恢复要求明确零退出码、原请求绑定和完整结果，可在原监督进程仍存活时收敛，不重跑资格基准。主机门禁退出码 75 在记录退出后按原归属重新排队，后续重新领取生成新归属；其他明确的非零退出记录失败，即使存在 ready 结果也不登记成功。未知退出继续保留运行态。GPU 输入下载阶段同样在领取提交前写入监督者证据；本次尝试结束后写结束标记。只在无计算记录且输入确认停止时按原归属重排，复用原缓存；写盘失败回滚领取。缺少退出记录的中途崩溃收敛、运行端部署与真实 GPU 资格验收仍待完成。
