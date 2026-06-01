# QuantX 项目文档

欢迎使用 QuantX 量化交易系统文档。本文档提供了系统的完整技术说明和使用指南。

## 文档导航

### 🏗️ 系统设计
- [**系统架构**](./ARCHITECTURE.md) - 整体架构设计、技术栈说明、数据流程
- [**功能模块**](./MODULES.md) - 各模块详细功能说明和职责划分
- [**API文档**](./API.md) - GraphQL Schema、查询/变更/订阅接口
- [**交易事件系统**](./TRADING_EVENTS.md) - 实时事件发布订阅机制、GraphQL订阅、最佳实践

### 🤖 策略系统
- [**策略系统指南**](./STRATEGY.md) - 策略开发、注册、参数配置、动态加载完整指南
- [**执行器架构**](./EXECUTOR_ARCHITECTURE.md) - 策略执行引擎设计、API和使用方法

### 💻 开发指南
- [**编码规范**](./CODING_STANDARDS.md) - Python编码标准、命名约定、最佳实践
- [**测试指南**](./TESTING_GUIDE.md) - 测试策略、规范和执行方法
- [**使用示例**](./EXAMPLES.md) - 常见场景的代码示例和使用方法

### 🚀 运维部署
- [**部署指南**](./DEPLOYMENT.md) - 环境配置、部署步骤、性能优化
- [**故障排查**](./TROUBLESHOOTING.md) - 常见问题解决方案、调试技巧
- [**版本记录**](./CHANGELOG.md) - 版本更新历史、升级指南

### 🤝 集成接口
- [**MCP Server 指南**](./MCP.md) - MCP 集成、配置与排错

## 快速链接

### 项目结构
```
api/
├── core/              # 核心交易引擎
├── gqlapi/            # GraphQL API层
├── prefector/         # Prefect工作流
├── database/          # 数据库层
├── services/          # 业务服务层
├── repositories/      # 数据仓储层
├── models/            # 数据模型
├── miniqmt/           # XTQuant集成
├── tests/             # 测试套件
└── docs/              # 项目文档
```

### 核心特性
- 📊 **实时市场数据** - WebSocket推送，毫秒级延迟
- 🤖 **策略引擎** - 灵活的策略框架，支持自定义指标
- 📈 **技术指标库** - 完整的技术分析指标实现
- 🔄 **工作流编排** - Prefect驱动的自动化任务
- 💾 **多数据库架构** - PostgreSQL + InfluxDB + Redis
- 🔌 **XTQuant集成** - 专业量化交易接口

### 开发环境
- Python 3.9+
- FastAPI + Strawberry GraphQL
- PostgreSQL 13+
- InfluxDB 3.x
- Redis 6+
- Prefect 3.x

### 快速开始

1. **克隆项目**
```bash
git clone https://github.com/yourusername/quantx.git
cd quantx/backend
```

2. **安装依赖**
```bash
# 使用 pyproject.toml 安装依赖
pip install -e .

# 或者使用 Poetry（推荐）
poetry install
```

3. **配置环境**
```bash
cp .env.example .env
# 编辑 .env 文件配置数据库连接
```

4. **启动服务**
```bash
python main.py
```

5. **访问接口**
- GraphQL Playground: http://localhost:8000/graphql
- 健康检查: http://localhost:8000/health
- Prometheus指标: http://localhost:8000/metrics

## 文档约定

### 图标说明
- 📚 文档相关
- 🏗️ 架构设计
- 💻 代码实现
- 🚀 部署运维
- ⚡ 性能优化
- 🔧 配置管理
- 🐛 调试排错
- ✅ 测试相关
- 📊 数据分析
- 🔌 集成接口

### 代码示例格式
所有代码示例都使用语法高亮，并标注文件路径：

```python
# file: services/strategy_service.py
from core.strategies.base import BaseStrategy

class MyStrategy(BaseStrategy):
    """自定义策略示例"""
    pass
```

### 版本说明
- 当前版本：1.0.0
- 最后更新：2025-09-26
- 维护团队：QuantX Development Team

## 相关资源

- [测试指南](./TESTING_GUIDE.md) - 完整的测试指南和安全规范
- [环境配置](../.env.example) - 环境变量示例
- [CLAUDE.md](../CLAUDE.md) - AI助手使用指南
- [性能优化](./PERFORMANCE.md) - 系统性能调优指南

## 贡献指南

欢迎提交 Issue 和 Pull Request。请确保：
1. 遵循[编码规范](./CODING_STANDARDS.md)
2. 添加相应的[测试](./TESTING_GUIDE.md)
3. 更新相关文档
4. 通过所有测试

## 许可证

本项目采用 MIT 许可证。详见 LICENSE 文件。
