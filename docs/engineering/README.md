# QuantX 工程文档

工程文档按独立运行单元组织：

- [API](api/README.md)
- [AI Runtime](ai-runtime/README.md)
- [Engine](engine/README.md)
- [Prefect Worker](worker/README.md)
- [QMT Agent](qmt-agent/README.md)
- [部署与运维](deployment/README.md)

共享包位于 `packages/{contracts,domain,application,infrastructure}`。其中
`quantx_domain` 必须保持纯领域层，不能依赖数据库、网络、文件、FastAPI、
Prefect 或 QMT。
