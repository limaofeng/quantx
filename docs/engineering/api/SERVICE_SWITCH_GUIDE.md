# 服务切换指南

旧数据库实现和单体服务切换流程已经退役。当前运行单元不通过环境开关在
API 内互相启动；统一使用：

```powershell
.\ops\quantx.ps1 up -Environment dev -Profile web
.\ops\quantx.ps1 up -Environment dev -Profile full
```

组件所有权和故障恢复见 [MODULES.md](MODULES.md) 与
[../deployment/README.md](../deployment/README.md)。历史资料见
[archive/legacy-monolith](archive/legacy-monolith/README.md)。
