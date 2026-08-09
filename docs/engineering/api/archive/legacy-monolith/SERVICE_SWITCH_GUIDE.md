# 复权服务切换文档

## 🎯 切换目标

将复权因子服务从 **InfluxDB** 切换到 **PostgreSQL**

---

## 📋 切换步骤

### 步骤1: 数据迁移

```bash
# 1.1 创建 PostgreSQL 表
psql -h 192.168.101.4 -p 32432 -U postgres -d quantx -f migrations/create_divid_factors_table.sql

# 1.2 运行迁移脚本
conda activate <your-quantx-env>
python scripts/migrate_divid_factors_to_pg.py

# 1.3 验证数据
psql -h 192.168.101.4 -p 32432 -U postgres -d quantx -c "SELECT COUNT(*) FROM divid_factors;"
```

### 步骤2: 代码切换

#### 方式1: 使用配置文件（推荐）⭐

在 `services/service_config.py` 中修改：

```python
# 复权因子服务版本
USE_POSTGRESQL_DIVID_FACTOR = True  # 改为 True

# 历史数据服务版本
USE_ASYNC_HISTORICAL_SERVICE = True  # 改为 True
```

**优点**：
- ✅ 一键切换
- ✅ 可以快速回滚
- ✅ 便于测试

#### 方式2: 直接替换导入

在需要使用的文件中：

```python
# 旧版本
from services.divid_factor_service import DividFactorService
from services.historical_market_data_service import HistoricalMarketDataService

# 新版本
from services.divid_factor_service_pg import DividFactorServicePG
from services.historical_market_data_service_async import HistoricalMarketDataServiceAsync
```

### 步骤3: 更新调用代码

#### 同步 → 异步

**旧代码（同步）**：
```python
divid_service = DividFactorService()
factors = divid_service.get_divid_factors(
    stock_code="000001.SZ",
    start_time=start,
    end_time=end
)
```

**新代码（异步）**：
```python
divid_service = DividFactorServicePG()
factors = await divid_service.get_divid_factors(
    stock_code="000001.SZ",
    start_time=start,
    end_time=end
)
```

#### 在 GraphQL Resolver 中使用

```python
# gqlapi/queries.py

@strawberry.field
async def adjusted_klines(
    self,
    stock_code: str,
    period: str,
    start_date: datetime,
    end_date: datetime
) -> List[KLineType]:
    """查询复权K线"""

    # 使用异步服务
    market_service = get_historical_market_data_service()

    klines = await market_service.get_adjusted_klines(
        stock_code=stock_code,
        period=period,
        start_time=start_date,
        end_time=end_date,
        dividend_type="front"
    )

    return klines
```

### 步骤4: 测试验证

```bash
# 运行测试脚本
conda activate <your-quantx-env>
python tests/test_service_switch.py
```

**预期输出**：
```
✅ 查询成功: X 条
✅ PostgreSQL 版本工作正常
✅ 异步调用工作正常
```

---

## 📊 API 对比

### DividFactorService

| 方法 | 旧版本 (InfluxDB) | 新版本 (PostgreSQL) |
|------|------------------|-------------------|
| `get_divid_factors()` | 同步 | 异步 (await) |
| `save_divid_factors()` | 同步 | 异步 (await) |
| 返回类型 | InfluxDB 模型 | PostgreSQL 模型 |

### HistoricalMarketDataService

| 方法 | 旧版本 | 新版本 |
|------|------------------|-------------------|
| `get_adjusted_klines()` | 同步 | 异步 (await) |
| `_apply_dividend_adjustment()` | 私有方法 | 异步私有方法 |

---

## 🔧 常见问题

### Q1: 如何判断使用的是哪个版本？

```python
from services.service_config import USE_POSTGRESQL_DIVID_FACTOR, USE_ASYNC_HISTORICAL_SERVICE

print(f"复权服务: {'PostgreSQL' if USE_POSTGRESQL_DIVID_FACTOR else 'InfluxDB'}")
print(f"历史服务: {'异步' if USE_ASYNC_HISTORICAL_SERVICE else '同步'}")
```

### Q2: 如何处理同步/异步混用？

```python
# 如果外层是同步函数，需要运行异步代码
import asyncio

def sync_function():
    async_service = get_divid_factor_service()

    # 运行异步代码
    factors = asyncio.run(async_service.get_divid_factors(...))

    return factors
```

### Q3: 性能差异如何？

- **PostgreSQL + 异步**: 与 InfluxDB 相近或略好
- **支持未来扩展**: 可以 JOIN 其他表
- **更好的事务支持**

### Q4: 如何回滚？

修改 `services/service_config.py`：

```python
USE_POSTGRESQL_DIVID_FACTOR = False  # 改回 False
USE_ASYNC_HISTORICAL_SERVICE = False  # 改回 False
```

---

## 📋 切换检查清单

### 数据迁移
- [ ] PostgreSQL 表已创建
- [ ] 数据迁移成功
- [ ] 数据验证通过

### 代码更新
- [ ] `service_config.py` 已更新
- [ ] 所有导入已更新
- [ ] 同步调用改为异步（添加 await）

### 测试
- [ ] `test_service_switch.py` 通过
- [ ] GraphQL API 测试通过
- [ ] 性能测试通过

### 验证
- [ ] 复权计算正确
- [ ] 无数据丢失
- [ ] 性能符合预期

---

## 🚀 完成切换

### 1. 更新配置

编辑 `services/service_config.py`：

```python
# 启用 PostgreSQL 版本
USE_POSTGRESQL_DIVID_FACTOR = True
USE_ASYNC_HISTORICAL_SERVICE = True
```

### 2. 重启服务

```bash
# 重启 API 服务
conda activate <your-quantx-env>
cd F:\workspace\quantx\backend
python main.py
```

### 3. 验证

```bash
# 运行测试
python tests/test_service_switch.py

# 或使用 API 测试
curl http://localhost:8000/graphql
```

---

## 💡 最佳实践

### 1. 使用配置文件（推荐）

```python
from services.service_config import get_divid_factor_service

# 自动根据配置选择版本
divid_service = get_divid_factor_service()
```

### 2. 统一使用异步

```python
# 新代码全部使用异步
async def my_function():
    divid_service = get_divid_factor_service()
    factors = await divid_service.get_divid_factors(...)
```

### 3. 错误处理

```python
try:
    factors = await divid_service.get_divid_factors(...)
    if not factors:
        # 处理无数据情况
        return []
except Exception as e:
    logger.error(f"查询失败: {e}")
    return []
```

---

## ✅ 切换完成

切换完成后：

1. ✅ 复权因子存储在 PostgreSQL
2. ✅ 支持复杂查询和 JOIN
3. ✅ 代码已更新为异步
4. ✅ 测试通过

**享受新架构的优势！** 🎉

