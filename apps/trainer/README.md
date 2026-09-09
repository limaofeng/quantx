# Trainer

独立开发训练服务正在实施，完整范围见
[实施方案](../../docs/plans/独立训练服务架构与实施方案.md)。

目前提供无网络副作用的本地配置预检。尚未迁移 Worker 调度、启用训练部署或完成 Windows/GPU 验收，不能将配置预检通过视为服务 ready。

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
```

示例路径和端点需替换为本机实际部署值。生产目录必须如实配置；数据库名后缀与 URL 校验不能证明服务身份或最小权限，调度接入前仍须验证远端身份和研究表权限。

在独立 `quantx-train` Conda 环境安装此包后运行：

```powershell
conda run -n quantx-train quantx-trainer-config-check --config D:\QuantXTraining\trainer.toml
```

该命令验证配置字段、开发目标、代码/状态/生产目录隔离、实际运行解释器及代码目录；不会建立连接、创建 Pool、安装依赖或启动服务。应用通过校验后才能接入后续的权限预检和调度。

子进程环境采用系统必需变量白名单，显式设置开发目标及关闭实盘门，不继承券商参数、设备密钥、生产环境文件或 Python 搜索路径。后续进程启动必须使用完整的该环境映射，不能与父进程环境再次合并。

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
