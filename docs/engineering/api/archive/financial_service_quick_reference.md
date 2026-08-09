# save_batch_financial_data 历史快速参考

> 归档说明：本文保留旧单体时期的排障记录，示例会在同一进程同时导入
> 服务端代码和 `xtquant`，不符合当前 QMT Agent 出站架构，禁止作为现行
> 运行指南使用。

## ✅ 测试结论

**函数状态**: 正常可用 ✅
**测试日期**: 2026-01-18
**测试数据**: 600519.SH (贵州茅台) 真实财务数据

---

## 🚀 快速使用

```python
from services.financial_service import FinancialService
from xtquant import xtdata

# 1. 获取数据
xtdata.enable_hello = False
data = xtdata.get_financial_data(['600519.SH'], table_list=[], start_time='20240101')

# 2. 保存到数据库
service = FinancialService()
total_saved = await service.save_batch_financial_data(data)

print(f"成功保存 {total_saved} 条记录")
```

---

## 📊 数据格式

### 输入格式
```python
{
    '股票代码': {
        'Balance': DataFrame,    # 资产负债表
        'Income': DataFrame,     # 利润表
        'CashFlow': DataFrame,   # 现金流量表
        'Capital': DataFrame,    # 股本结构表
    }
}
```

### 关键字段

**日期字段** (必需):
- `m_timetag`: 报告期 (格式: "20250630")
- `m_anntime`: 公告日 (格式: "20250813")

**Balance 表关键字段**:
- `tot_assets`: 总资产
- `total_current_assets`: 流动资产合计
- `tot_liab`: 负债合计
- `total_equity`: 所有者权益合计

**Income 表关键字段**:
- `revenue`: 营业收入
- `net_profit_excl_min_int_inc`: 归母净利润
- `s_fa_eps_basic`: 基本每股收益

**CashFlow 表关键字段**:
- `net_cash_flows_oper_act`: 经营活动现金流
- `cash_cash_equ_end_period`: 期末现金余额

**Capital 表关键字段**:
- `total_capital`: 总股本
- `circulating_capital`: 流通A股
- `freeFloatCapital`: 自由流通股本 (注意驼峰命名!)

---

## 🧪 测试命令

```bash
# 数据转换测试 (无需数据库)
cd backend
python scripts/test_financial_service_simple.py

# 完整集成测试 (需要数据库)
pytest scripts/test_financial_service_pytest.py -v

# 手动测试报告
python scripts/manual_financial_test.py
```

---

## ⚠️ 注意事项

### 1. 数据量限制
- **小批量** (< 100只股票): 直接使用
- **大批量** (≥ 100只股票): 建议分批处理

```python
# 分批处理示例
batch_size = 50
for i in range(0, len(stock_codes), batch_size):
    batch = stock_codes[i:i+batch_size]
    batch_data = {code: data[code] for code in batch}
    await service.save_batch_financial_data(batch_data)
```

### 2. 异常处理

函数会自动处理:
- ✅ 空值 (None, pd.NA, pd.NaT)
- ✅ 无效日期
- ✅ 无效数值
- ✅ 重复数据 (upsert 自动更新)

失败时会:
- 🔴 回滚整个批次
- 🔴 抛出异常 (保留完整堆栈)
- 🔴 记录错误日志

### 3. 性能考虑

**当前实现**:
- 单次事务提交所有数据
- 串行处理,无并发
- 适合中小批量数据

**优化建议** (可选):
- 使用批量 upsert
- 按股票分批提交
- 添加并发处理

---

## 🔍 调试技巧

### 1. 检查数据质量

```python
# 检查数据是否为空
if not financial_data_map:
    print("数据为空")
    return 0

# 检查每个表
for stock_code, tables in financial_data_map.items():
    for table_name, df in tables.items():
        if df.empty:
            print(f"{stock_code}.{table_name} 为空")
```

### 2. 验证数据转换

```python
from services.financial_service import FinancialService

service = FinancialService()

# 测试日期解析
date = service._parse_date("20250630")
print(date)  # 2025-06-30

# 测试数值转换
value = service._safe_decimal(123.45)
print(value)  # 123.45
```

### 3. 查看保存的记录

```python
from repositories.financial_repository import FinancialBalanceSheetRepository
from database.connection import get_async_db

async def check_saved_data(stock_code):
    async for db in get_async_db():
        repo = FinancialBalanceSheetRepository(db)
        records = await repo.find_by_stock_code(stock_code, limit=10)

        for record in records:
            print(f"{record.report_date}: {record.total_assets}")
```

---

## 📈 性能基准

**测试数据**: 600519.SH
- Balance: 6 条记录
- Income: 6 条记录
- CashFlow: 6 条记录
- Capital: 7 条记录
- **总计**: 25 条记录

**转换时间**: < 1 秒
**保存时间**: 预计 < 5 秒 (取决于网络和数据库)

---

## 🆘 常见问题

### Q1: 保存后数据库没有记录?
**A**: 检查数据库连接和事务提交:
```python
async for db in get_async_db():
    # ... 你的操作
    await db.commit()  # 确保提交
```

### Q2: 某些字段为 None?
**A**: 检查 DataFrame 中该字段是否存在:
```python
if 'field_name' in df.columns:
    value = df['field_name'].iloc[0]
else:
    print("字段不存在")
```

### Q3: 日期解析失败?
**A**: 确保日期格式为 "YYYYMMDD":
```python
# 正确
"20250630"  ✅

# 错误
"2025-06-30"  ❌
"2025/06/30"  ❌
```

### Q4: 如何查看详细日志?
**A**: 配置日志级别:
```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

---

## 📚 相关文档

- [完整调试报告](./financial_service_debug_report.md)
- [服务层代码](../services/financial_service.py)
- [Repository 代码](../repositories/financial_repository.py)
- [数据模型](../models/financial.py)

---

**更新时间**: 2026-01-18
**状态**: 测试通过 ✅
