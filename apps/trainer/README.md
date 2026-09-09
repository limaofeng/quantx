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
