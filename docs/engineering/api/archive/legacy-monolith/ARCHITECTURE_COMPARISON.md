# QuantX 架构对比：迁移前后

## 📊 当前架构（InfluxDB）

```
┌─────────────────────────────────────────────────────────┐
│                    应用层 (Python)                       │
│  ┌───────────────────────────────────────────────────┐  │
│  │  HistoricalMarketDataService                       │  │
│  │  - _apply_dividend_adjustment()                  │  │
│  │  - get_divid_factors()                           │  │
│  └───────────────────────────────────────────────────┘  │
│                         ↓                                │
│  ┌───────────────────────────────────────────────────┐  │
│  │  应用层复权计算 (pandas)                          │  │
│  │  - merge_asof() 时间对齐                          │  │
│  │  - 累积因子计算                                   │  │
│  │  - 复权价格计算                                   │  │
│  └───────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
                         ↓
        ┌────────────────────────────────────────┐
        │         InfluxDB 3.x                   │
        ├────────────────────────────────────────┤
        │  Tables:                                │
        │  - kline_1d (K线数据)                  │
        │  - kline_1m (分钟K线)                  │
        │  - divid_factors (复权因子) ❌         │  ← 不支持 JOIN
        │  - ticks (tick数据)                     │
        └────────────────────────────────────────┘
```

**问题**：
- ❌ 两次数据库查询（K线 + 因子）
- ❌ InfluxDB 不支持 JOIN
- ❌ 应用层计算开销
- ❌ 无法与其他表关联

---

## 🎯 目标架构（PostgreSQL + InfluxDB）

```
┌─────────────────────────────────────────────────────────┐
│              应用层 (Python 异步)                       │
│  ┌───────────────────────────────────────────────────┐  │
│  │  HistoricalMarketDataServiceAsync                  │  │
│  │  - async get_adjusted_klines()                    │  │
│  │  - async _apply_dividend_adjustment()             │  │
│  └───────────────────────────────────────────────────┘  │
│                         ↓                                │
│  ┌───────────────────────────────────────────────────┐  │
│  │  应用层复权计算 (pandas)                          │  │
│  │  - merge_asof() 时间对齐                          │  │
│  │  - 累积因子计算                                   │  │
│  │  - 复权价格计算                                   │  │
│  └───────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
                         ↓
        ┌────────────────────────┐        ┌─────────────────────────┐
        │    PostgreSQL           │        │      InfluxDB 3.x        │
        ├────────────────────────┤        ├─────────────────────────┤
        │  Tables:                │        │  Tables:                 │
        │  - users ✅             │        │  - kline_1d ✅           │
        │  - strategies ✅        │        │  - kline_1m ✅           │
        │  - orders ✅            │        │  - ticks ✅              │
        │  - divid_factors ✅     │  ← 新增│                          │
        │                        │        │  (时序大数据保留)         │
        │  支持:                 │        │                          │
        │  - JOIN ✅              │        │                          │
        │  - 事务 ✅              │        │                          │
        │  - 索引优化 ✅          │        │                          │
        └────────────────────────┘        └─────────────────────────┘
```

**优势**：
- ✅ 复权因子在 PostgreSQL（支持复杂查询）
- ✅ 可以与其他表 JOIN
- ✅ 事务支持
- ✅ K线保留在 InfluxDB（时序优化）
- ✅ 架构清晰，职责分明

---

## 🔄 数据流对比

### 当前流程（InfluxDB）

```
1. 查询 K线数据
   → InfluxDB: SELECT * FROM kline_1d
   → 返回: 100 条 (0.02s)

2. 查询复权因子
   → InfluxDB: SELECT * FROM divid_factors
   → 返回: 5 条 (0.01s)

3. 应用层合并
   → pandas merge_asof()
   → 计算复权价格 (0.01s)

总耗时: 0.04s
```

### 目标流程（PostgreSQL）

```
1. 查询复权因子
   → PostgreSQL: SELECT * FROM divid_factors
   → 返回: 5 条 (0.01s)

2. 查询 K线数据
   → InfluxDB: SELECT * FROM kline_1d
   → 返回: 100 条 (0.02s)

3. 应用层合并
   → pandas merge_asof()
   → 计算复权价格 (0.01s)

总耗时: 0.04s (相近或略好)
```

---

## 💾 存储策略

### PostgreSQL 存储

**业务数据和配置**：
- ✅ 用户和权限
- ✅ 策略配置
- ✅ 订单记录
- ✅ 投资组合
- ✅ **复权因子** (新增)

**优势**：
- 支持复杂查询
- 支持 JOIN
- 事务支持
- 数据一致性

### InfluxDB 保留

**时序大数据**：
- ✅ Tick 数据（高频）
- ✅ 分钟K线（1m, 5m, 15m, 30m, 60m）
- ✅ 日K线（1d）
- ✅ 周K线（1w, 1M）

**优势**：
- 时序数据优化
- 高效压缩
- 快速时间范围查询
- 流式数据处理

---

## 🔍 查询示例对比

### 复权查询

**当前（InfluxDB）**：
```python
# 两次查询
klines = influxdb.query("SELECT * FROM kline_1d WHERE stock_code='...'")
factors = influxdb.query("SELECT * FROM divid_factors WHERE stock_code='...'")

# 应用层合并
result = pd.merge_asof(klines, factors, on='time', direction='backward')
```

**目标（PostgreSQL + InfluxDB）**：
```python
# 异步查询（并行）
klines, factors = await asyncio.gather(
    query_influx_klines(),
    query_postgres_factors()
)

# 应用层合并
result = pd.merge_asof(klines, factors, on='time', direction='backward')
```

### 复杂业务查询

**当前（InfluxDB）**：
```sql
-- ❌ 不支持
SELECT * FROM klines
JOIN divid_factors ON ...
JOIN users ON ...
WHERE users.id = '...'
```

**目标（PostgreSQL）**：
```sql
-- ✅ 支持
SELECT u.name, s.strategy_name, o.*
FROM orders o
JOIN strategies s ON o.strategy_id = s.id
JOIN users u ON o.user_id = u.id
WHERE s.type = 'quantitative'
ORDER BY o.created_at DESC;
```

---

## 📊 性能对比

| 指标 | InfluxDB | PostgreSQL | 差异 |
|------|----------|------------|------|
| 复权因子查询 | 0.01s | 0.01s | 相近 |
| 支持复杂查询 | ❌ | ✅ | PostgreSQL 胜 |
| 支持 JOIN | ❌ | ✅ | PostgreSQL 胜 |
| 事务支持 | ❌ | ✅ | PostgreSQL 胜 |
| K线查询 | ✅ (优化) | ❌ | InfluxDB 胜 |
| Tick 数据处理 | ✅ (优化) | ❌ | InfluxDB 胜 |

---

## 🎯 迁移收益

### 立即收益

1. ✅ **支持复杂查询**
   - 复权因子可以与其他表 JOIN
   - 支持复杂业务逻辑

2. ✅ **事务支持**
   - 更新除权信息时保证一致性
   - 支持回滚

3. ✅ **减少 InfluxDB 压力**
   - 减少文件扫描
   - 降低查询复杂度

### 未来收益

1. ✅ **更好的扩展性**
   - 可以添加更多关联表
   - 支持复杂分析

2. ✅ **数据一致性**
   - 统一在 PostgreSQL 管理
   - 事务保证

---

## ✅ 架构优势

### 职责清晰

- **PostgreSQL**: 业务数据、配置、复权因子
- **InfluxDB**: 时序大数据（K线、tick）

### 优化查询

- **业务查询**: PostgreSQL（JOIN、事务）
- **时序查询**: InfluxDB（时间范围、聚合）

### 渐进式迁移

- 第一步：复权因子 → PostgreSQL
- 未来可选：其他业务数据 → PostgreSQL

---

## 🎯 总结

### 当前架构问题

- ❌ InfluxDB 不支持 JOIN
- ❌ 无法进行复杂业务查询
- ❌ 数据分散

### 目标架构优势

- ✅ 支持 JOIN 和复杂查询
- ✅ 事务保证数据一致性
- ✅ 架构清晰，职责分明
- ✅ 保留 InfluxDB 时序优势

### 迁移价值

**短期**：
- 解决 JOIN 问题
- 提升查询灵活性

**长期**：
- 更好的数据一致性
- 支持复杂业务逻辑
- 架构更合理

---

**准备好迁移了吗？** 🚀

```bash
# 开始 3 步切换
psql -h 192.168.5.6 -p 32432 -U postgres -d quantx -f migrations/create_divid_factors_table.sql
python scripts/migrate_divid_factors_to_pg.py
python scripts/switch_service.py --postgresql --async
```
