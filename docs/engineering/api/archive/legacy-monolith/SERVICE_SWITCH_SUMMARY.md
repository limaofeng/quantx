# 复权服务完整切换方案 - 总览

## 🎯 切换目标

将复权因子从 **InfluxDB** 迁移到 **PostgreSQL**，并完成代码逻辑切换。

---

## 📦 已创建的文件

### 1. 数据库相关

| 文件 | 说明 |
|------|------|
| `migrations/create_divid_factors_table.sql` | PostgreSQL 建表脚本 |
| `models/divid_factor_pg.py` | PostgreSQL 数据模型 |
| `repositories/divid_factor_repository_pg.py` | PostgreSQL Repository |

### 2. 服务层

| 文件 | 说明 |
|------|------|
| `services/divid_factor_service_pg.py` | PostgreSQL 版本的复权服务（异步） |
| `services/historical_market_data_service_async.py` | 异步版本的历史数据服务 |
| `services/service_config.py` | 服务配置文件（切换开关） |

### 3. 工具脚本

| 文件 | 说明 |
|------|------|
| `scripts/migrate_divid_factors_to_pg.py` | 数据迁移脚本 |
| `scripts/switch_service.py` | 快速切换配置脚本 |
| `tests/test_service_switch.py` | 服务切换测试脚本 |

### 4. 文档

| 文件 | 说明 |
|------|------|
| `docs/MIGRATION_GUIDE.md` | 迁移指南 |
| `docs/SERVICE_SWITCH_GUIDE.md` | 服务切换指南 |

---

## 🚀 快速开始（3步完成切换）

### 步骤1: 数据迁移

```bash
# 1.1 创建表
psql -h 192.168.5.6 -p 32432 -U postgres -d quantx -f migrations/create_divid_factors_table.sql

# 1.2 迁移数据
conda activate <your-quantx-env>
python scripts/migrate_divid_factors_to_pg.py
```

### 步骤2: 代码切换

```bash
# 一键切换到 PostgreSQL + 异步版本
python scripts/switch_service.py --postgresql --async
```

### 步骤3: 测试验证

```bash
# 运行测试
python tests/test_service_switch.py
```

---

## 📊 架构对比

### 切换前（InfluxDB）

```
应用层
  ↓ (查询)
InfluxDB: divid_factors 表
  ↓ (返回)
应用层合并 (pandas)
```

### 切换后（PostgreSQL）

```
应用层 (异步)
  ↓ (await 查询)
PostgreSQL: divid_factors 表
  ↓ (返回)
应用层合并 (pandas)
```

**优势**：
- ✅ 支持复杂查询
- ✅ 可以与其他表 JOIN
- ✅ 事务支持
- ✅ 减少 InfluxDB 压力

---

## 💡 使用方式

### 方式1: 使用配置文件（推荐）

```python
from services.service_config import get_divid_factor_service

# 自动根据配置选择版本
divid_service = get_divid_factor_service()

# 使用（自动适配同步/异步）
factors = await divid_service.get_divid_factors(...)
```

### 方式2: 直接使用

```python
# PostgreSQL 版本
from services.divid_factor_service_pg import DividFactorServicePG

divid_service = DividFactorServicePG()
factors = await divid_service.get_divid_factors(...)
```

---

## 🔄 代码对比

### 旧代码（InfluxDB + 同步）

```python
from services.divid_factor_service import DividFactorService

divid_service = DividFactorService()

# 同步调用
factors = divid_service.get_divid_factors(
    stock_code="000001.SZ",
    start_time=start,
    end_time=end
)
```

### 新代码（PostgreSQL + 异步）

```python
from services.divid_factor_service_pg import DividFactorServicePG

divid_service = DividFactorServicePG()

# 异步调用
factors = await divid_service.get_divid_factors(
    stock_code="000001.SZ",
    start_time=start,
    end_time=end
)
```

---

## ⚠️ 注意事项

### 1. 异步调用

**新版本需要使用 `await`**：

```python
# 错误
factors = divid_service.get_divid_factors(...)

# 正确
factors = await divid_service.get_divid_factors(...)
```

### 2. 在 GraphQL Resolver 中使用

```python
@strawberry.field
async def adjusted_klines(...) -> List[KLineType]:
    # 使用异步服务
    divid_service = get_divid_factor_service()
    factors = await divid_service.get_divid_factors(...)

    return klines
```

### 3. 同步函数中调用异步

```python
def sync_function():
    async_service = get_divid_factor_service()

    # 使用 asyncio.run
    factors = asyncio.run(async_service.get_divid_factors(...))

    return factors
```

---

## 🎯 完成检查清单

### 数据迁移
- [ ] PostgreSQL 表已创建
- [ ] 数据已迁移
- [ ] 数据验证通过

### 代码更新
- [ ] `service_config.py` 已更新
- [ ] 所有调用已改为 `await`
- [ ] GraphQL Resolver 已更新

### 测试
- [ ] `test_service_switch.py` 通过
- [ ] API 测试通过
- [ ] 复权计算正确

### 性能
- [ ] 性能测试完成
- [ ] 与旧版本性能相近

---

## 🔙 回滚方案

如果需要回滚到 InfluxDB 版本：

```bash
# 一键回滚
python scripts/switch_service.py --influxdb --sync

# 或手动编辑 services/service_config.py
# USE_POSTGRESQL_DIVID_FACTOR = False
# USE_ASYNC_HISTORICAL_SERVICE = False
```

---

## 📚 相关文档

- [迁移指南](docs/MIGRATION_GUIDE.md) - 详细迁移步骤
- [切换指南](docs/SERVICE_SWITCH_GUIDE.md) - 代码切换指南
- [SQL 建表脚本](migrations/create_divid_factors_table.sql)
- [数据模型](models/divid_factor_pg.py)
- [Repository 实现](repositories/divid_factor_repository_pg.py)

---

## ✅ 准备好了吗？

### 开始切换

```bash
# 1. 数据迁移
psql -h 192.168.5.6 -p 32432 -U postgres -d quantx -f migrations/create_divid_factors_table.sql
python scripts/migrate_divid_factors_to_pg.py

# 2. 代码切换
python scripts/switch_service.py --postgresql --async

# 3. 测试验证
python tests/test_service_switch.py
```

### 需要帮助？

- 📖 查看 [切换指南](docs/SERVICE_SWITCH_GUIDE.md)
- 🧪 运行测试脚本验证
- 📝 检查配置文件

---

**准备好了吗？开始切换吧！** 🚀
