# 复权因子迁移指南

## 🎯 迁移目标

将复权因子数据从 **InfluxDB** 迁移到 **PostgreSQL**

## 📋 迁移步骤

### 1. 创建 PostgreSQL 表

```bash
# 方式1: 使用 psql 命令
psql -h 192.168.101.4 -p 32432 -U postgres -d quantx -f migrations/create_divid_factors_table.sql

# 方式2: 使用 Docker（如果 PostgreSQL 在容器中）
docker exec -it postgresql-container psql -U postgres -d quantx -f /path/to/create_divid_factors_table.sql
```

### 2. 运行迁移脚本

```bash
# 激活 conda 环境
conda activate <your-quantx-env>

# 运行迁移脚本
cd F:\workspace\quantx\backend
python scripts/migrate_divid_factors_to_pg.py
```

### 3. 验证数据

```sql
-- 在 PostgreSQL 中验证
SELECT COUNT(*) FROM divid_factors;

-- 查看数据示例
SELECT * FROM divid_factors ORDER BY time DESC LIMIT 10;

-- 检查特定股票
SELECT * FROM divid_factors WHERE stock_code = '601985.SH';
```

### 4. 更新代码

#### 更新 Repository

在 `services/divid_factor_service.py` 中：

```python
# 旧版本（InfluxDB）
from repositories.divid_factor_repository import DividFactorRepository

# 新版本（PostgreSQL）
from repositories.divid_factor_repository_pg import DividFactorRepositoryPG

class DividFactorService:
    def __init__(self):
        # 旧版本
        # self.repo = DividFactorRepository()

        # 新版本
        self.repo = DividFactorRepositoryPG()
```

#### 更新查询方法

```python
# 旧版本（同步）
factors = self.repo.get_divid_factors(
    stock_code="000001.SZ",
    start_time=start,
    end_time=end
)

# 新版本（异步）
factors = await self.repo.get_divid_factors(
    stock_code="000001.SZ",
    start_time=start,
    end_time=end
)
```

### 5. 更新 Service 层

在 `services/historical_market_data_service.py` 中：

```python
class HistoricalMarketDataService:
    def __init__(self):
        # 使用新的 PostgreSQL Repository
        self.divid_factor_service = DividFactorServicePG()

    async def _apply_dividend_adjustment(
        self, klines: List[KLine], stock_code: str, dividend_type: str
    ) -> List[KLine]:
        # 更新为异步查询
        factors = await self.divid_factor_service.get_divid_factors(
            stock_code=stock_code,
            start_time=start_time,
            end_time=end_time,
        )
        ...
```

### 6. 测试验证

```python
# 测试脚本
import asyncio
from services.divid_factor_service_pg import DividFactorServicePG

async def test():
    service = DividFactorServicePG()

    # 测试查询
    factors = await service.get_divid_factors(
        stock_code="601985.SH",
        start_time=datetime(2024, 1, 1),
        end_time=datetime(2024, 12, 31)
    )

    print(f"查询到 {len(factors)} 条复权因子")
    for factor in factors:
        print(f"  {factor.time}: dr={factor.dr}")

asyncio.run(test())
```

### 7. 性能对比

运行性能测试，对比迁移前后：

```bash
# 迁移前（InfluxDB）
python tests/performance/full_test.py

# 迁移后（PostgreSQL）
# 预期性能相近或略好
```

### 8. 清理 InfluxDB 数据（可选）

确认 PostgreSQL 数据正确后，可以删除 InfluxDB 中的数据：

```python
# 警告：不可逆操作！
from influxdb_client import InfluxDBClient3

client = InfluxDBClient3(
    host="http://192.168.101.4:30081",
    token="your_token",
    database="quantx"
)

# 删除数据
client.query("DELETE FROM divid_factors WHERE time > '1970-01-01'")
```

---

## ✅ 迁移检查清单

- [ ] PostgreSQL 表已创建
- [ ] 迁移脚本运行成功
- [ ] 数据验证通过（记录数一致）
- [ ] Repository 代码已更新
- [ ] Service 层代码已更新
- [ ] 异步调用已修改
- [ ] 测试通过
- [ ] 性能测试完成
- [ ] 文档已更新

---

## 🎯 预期效果

### 优点

1. ✅ **支持未来扩展**
   - 可以与其他 PostgreSQL 表 JOIN
   - 事务支持
   - 更好的数据一致性

2. ✅ **减少 InfluxDB 压力**
   - 减少 Parquet 文件扫描
   - 降低查询复杂度

3. ✅ **架构更清晰**
   - PostgreSQL: 业务数据、配置、复权因子
   - InfluxDB: 时序大数据（K线、tick）

### 注意事项

1. ⚠️ **异步调用**
   - PostgreSQL Repository 需要异步调用
   - 更新所有使用到的地方

2. ⚠️ **性能监控**
   - 监控查询性能
   - 必要时添加索引

3. ⚠️ **数据一致性**
   - 确保迁移期间无数据丢失
   - 验证数据完整性

---

## 📞 问题反馈

如遇问题，请检查：
1. PostgreSQL 连接配置（`.env` 文件）
2. 表是否正确创建
3. 数据类型是否匹配
4. 异步调用是否正确

---

**准备迁移了吗？** 🚀

```bash
# 第一步：创建表
psql -h 192.168.101.4 -p 32432 -U postgres -d quantx -f migrations/create_divid_factors_table.sql

# 第二步：运行迁移
conda activate <your-quantx-env>
python scripts/migrate_divid_factors_to_pg.py
```

