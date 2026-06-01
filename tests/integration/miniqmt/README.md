# MiniQMT 数据测试

## 测试说明

本测试用于验证 miniqmt/xtquant 的 tick 数据获取功能。

## 环境要求

- Python 环境：当前 QuantX 项目环境；如使用 Conda，可通过 `QUANTX_CONDA_ENV` 指定
- 已安装 xtquant 库

## 运行方式

### 方式 1: 直接运行（推荐）

双击运行批处理文件：
```
run_test.bat
```

这将：
1. 如设置了 `QUANTX_CONDA_ENV`，自动激活对应 conda 环境
2. 运行测试脚本
3. 将详细日志输出到控制台和日志文件

### 方式 2: 使�� pytest

双击运行：
```
run_pytest.bat
```

### 方式 3: 手动运行

```bash
# 可选：激活你的 QuantX conda 环境
conda activate <your-quantx-env>

# 运行测试
python tests\integration\miniqmt\test_miniqmt_data.py

# 或使用 pytest
pytest tests\integration\miniqmt\test_miniqmt_data.py -v -s
```

## 测试内容

### test_get_current_tick()
- 测试获取指定股票的实时 tick 数据
- 打印详细的 tick 字段信息，包括：
  - 最新价
  - 买一价/卖一价
  - 成交量/成交额
  - 涨跌额/涨跌幅

### test_get_market_data()
- 测试获取沪深 A 股列表
- 获取多只股票的行情数据
- 打印市场行情摘要

## 日志文件

测试运行时会在以下位置生成日志文件：
```
tests/integration/miniqmt/test_miniqmt_data.log
```

日志包含：
- 测试时间戳
- 获取的股票数量
- 每只股票的详细 tick 数据
- 所有关键字段的值

## 测试股票代码

默认测试以下股票：
- 000001.SZ (平安银行)
- 600000.SH (浦发银行)
- 000002.SZ (万科A)

可以在 `test_miniqmt_data.py` 中修改 `stock_codes` 列表来测试其他股票。

## 注意事项

1. 确保在交易时间内运行，否则可能获取不到实时数据
2. 需要已登录 xtquant 账户（如果需要的话）
3. 测试数据会同时输出到控制台和日志文件
