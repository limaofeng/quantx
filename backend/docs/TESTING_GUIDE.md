# 🧪 QuantX 测试指南

QuantX 项目提供了完整的测试套件，支持多种测试方式和配置，同时包含严格的安全防护机制。

## 📋 目录

- [测试目录结构](#测试目录结构)
- [测试安全指南](#测试安全指南)
- [快速开始测试](#快速开始测试)
- [使用测试运行器](#使用测试运行器)
- [按标记运行测试](#按标记运行测试)
- [XTQuant 模块测试](#xtquant-模块测试)
- [Prefect 流程测试](#prefect-流程测试)
- [测试模块说明](#测试模块说明)
- [特定测试运行](#特定测试运行)
- [覆盖率报告](#覆盖率报告)
- [测试配置](#测试配置)
- [测试环境准备](#测试环境准备)
- [持续集成](#持续集成)

## 📁 测试目录结构

### 目录组织

```
tests/
├── conftest.py                    # pytest 全局配置和共享固件
├── unit/                          # 单元测试
│   ├── core/                      # 核心模块单元测试
│   │   ├── test_executor.py       # 策略执行器测试
│   │   ├── test_indicators.py     # 技术指标测试
│   │   ├── test_parameter_manager.py  # 参数管理器测试
│   │   ├── test_strategy_base.py  # 策略基类测试
│   │   └── test_strategy_modes.py # 策略模式测试
│   ├── database/                  # 数据库层单元测试
│   │   └── test_timeseries_optimized.py  # 时序数据库测试
│   ├── services/                  # 服务层单元测试
│   │   ├── test_holiday_service.py    # 节假日服务测试
│   │   └── test_market_data_service.py  # 市场数据服务测试
│   └── test_config.py             # 配置模块测试
├── integration/                   # 集成测试
│   ├── api/                       # API 集成测试
│   │   ├── test_graphql.py        # GraphQL API 测试
│   │   ├── test_middleware.py     # 中间件测试
│   │   ├── test_monitoring.py     # 监控端点测试
│   │   └── test_subscription.py   # WebSocket 订阅测试
│   ├── prefector/                 # 工作流集成测试
│   │   ├── test_batch_stock_flow.py
│   │   ├── test_bond_repo_flow.py
│   │   ├── test_comprehensive_market_flow.py
│   │   ├── test_daily_market_data_sync_flow.py
│   │   ├── test_daily_trading_sync_flow.py
│   │   ├── test_flow_error_handling.py
│   │   ├── test_flow_integration.py
│   │   ├── test_flow_scheduling.py
│   │   ├── test_market_indices_flow.py
│   │   ├── test_realtime_price_flow.py
│   │   ├── test_sector_data_flow.py
│   │   └── test_single_stock_flow.py
│   ├── test_xtquant/              # XTQuant 集成测试
│   │   ├── test_xtquant_config.py
│   │   ├── test_xtquant_data.py
│   │   ├── test_xtquant_integration.py
│   │   ├── test_xtquant_trading.py
│   │   └── test_xtquant_utils.py
│   ├── test_main.py               # 主应用集成测试
│   └── test_strategy_integration.py  # 策略集成测试
└── e2e/                           # 端到端测试（预留）
```

### 测试分类说明

#### 单元测试 (unit/)
- **目的**：测试单个模块或类的功能，与外部依赖隔离
- **特点**：使用 Mock 对象，运行速度快
- **范围**：核心业务逻辑、工具函数、数据处理

#### 集成测试 (integration/)
- **目的**：测试多个模块之间的交互
- **特点**：可能需要真实数据库或外部服务
- **范围**：API 端点、数据库操作、工作流执行

#### 端到端测试 (e2e/)
- **目的**：测试完整的用户场景
- **特点**：模拟真实用户操作
- **范围**：完整业务流程（预留）

### 测试标记

使用 pytest 标记来分类测试：
- `@pytest.mark.unit` - 单元测试
- `@pytest.mark.integration` - 集成测试
- `@pytest.mark.api` - API 测试
- `@pytest.mark.slow` - 慢速测试
- `@pytest.mark.asyncio` - 异步测试

### 注意事项

1. **命名冲突**：避免测试目录名与源代码模块名冲突（如 xtquant）
2. **导入路径**：conftest.py 已配置项目根目录到 Python 路径
3. **异步测试**：使用 `@pytest.mark.asyncio` 标记异步测试函数
4. **固件共享**：通用固件定义在 conftest.py 中
5. **测试隔离**：每个测试应独立运行，不依赖其他测试的状态

## 🚨 测试安全指南

### 重要安全警告

QuantX 系统包含会执行**真实金融交易**的测试。不当使用可能导致**资金损失**！

### 测试分层架构

#### 1. 单元测试 (Unit Tests) - ✅ 安全
**位置**: `tests/unit/`
**特征**: 使用 Mock 对象，不接触真实系统
**运行**: `python run_tests.py prefect --unit`

```python
# 示例：完全使用Mock，安全
@patch('prefector.flows.bond_repo_flow.TradingService')
async def test_trading_logic_with_mock(mock_service):
    # 测试业务逻辑，不会执行真实交易
```

#### 2. 集成测试 (Integration Tests) - ✅ 相对安全
**位置**: `tests/integration/`
**特征**: 使用 MockTradingService，测试组件集成但避免真实交易
**运行**: `python run_tests.py prefect --integration`

```python
# 示例：Mock关键服务，安全测试集成逻辑
mock_trading_service = MockTradingService()
with patch('services.trading_service.TradingService', return_value=mock_trading_service):
    result = await bond_repo_auto_trade_flow()  # 安全：不会真实下单
```

#### 3. E2E测试 (End-to-End Tests) - ⚠️ 危险
**位置**: `tests/e2e/`
**特征**: 执行真实的业务操作，包括真实交易
**运行**: `python run_tests.py prefect --e2e`

### 🔒 安全防护机制

#### 环境变量控制
E2E测试需要明确启用：
```bash
export ENABLE_REAL_TRADING=true
export ENV=testing  # 不能是 production
```

#### 环境检查
```python
@pytest.fixture(autouse=True)
def check_environment():
    if os.getenv("ENABLE_REAL_TRADING") != "true":
        pytest.skip("需要设置 ENABLE_REAL_TRADING=true")

    if os.getenv("ENV") == "production":
        pytest.skip("禁止在生产环境运行真实交易测试")
```

### 🎯 测试标记系统

| 标记 | 用途 | 安全等级 | 说明 |
|------|------|----------|------|
| `@pytest.mark.unit` | 单元测试 | ✅ 安全 | 纯Mock，无外部依赖 |
| `@pytest.mark.integration` | 集成测试 | ✅ 相对安全 | Mock关键服务 |
| `@pytest.mark.e2e` | 端到端测试 | ⚠️ 危险 | 真实操作 |
| `@pytest.mark.dangerous` | 危险操作 | 🚨 极危险 | 会消耗真实资金 |

### 📋 最佳实践

#### ✅ 推荐做法

1. **日常开发使用单元测试和集成测试**
   ```bash
   python run_tests.py prefect --unit        # 快速验证逻辑
   python run_tests.py prefect --integration # 验证组件集成
   ```

2. **为新功能同时编写多层测试**
   - 单元测试：验证核心逻辑
   - 集成测试：验证组件协作
   - E2E测试：验证完整流程（谨慎使用）

3. **使用Mock服务模拟外部依赖**
   ```python
   # 好的做法：Mock交易服务
   mock_trading = MockTradingService()
   with patch('services.trading_service.TradingService', return_value=mock_trading):
       # 测试业务逻辑
   ```

#### ❌ 禁止行为

1. **绝不在生产环境运行E2E测试**
2. **不要在CI/CD流水线中包含E2E测试**
3. **不要在开发过程中频繁运行E2E测试**
4. **不要忽略环境变量检查**

### 🔧 MockTradingService 使用

MockTradingService 提供完整的交易服务模拟：

```python
from tests.mocks import MockTradingService

mock_service = MockTradingService()

# 模拟账户信息
account = await mock_service.get_account_info()
print(f"模拟资金: {account.cash}")

# 模拟下单（不会真实执行）
result = await mock_service.place_order(
    stock_code="131810.SZ",
    order_type=OrderType.SELL,
    order_volume=100,
    price_type=PriceType.FIX_PRICE,
    price=2.5
)
print(f"模拟订单ID: {result['order_id']}")
```

### 🚧 E2E测试执行检查清单

运行E2E测试前必须确认：

- [ ] 当前环境是专门的测试环境
- [ ] 已设置 `ENABLE_REAL_TRADING=true`
- [ ] 已设置 `ENV=testing`
- [ ] 测试账户有充足资金
- [ ] 了解测试可能的资金影响
- [ ] 确认不是生产环境
- [ ] 准备好监控交易结果

### 📞 问题报告

如果发现测试安全问题：

1. **立即停止相关测试**
2. **记录问题详情**
3. **联系开发团队**
4. **更新安全防护机制**

**记住：安全第一，测试第二。宁可多写Mock，也不要冒险执行真实交易！** 🛡️

## 🚀 快速开始测试

### 基础测试命令

```bash
# 进入API目录
cd api

# 运行所有测试
python -m pytest tests/

# 详细输出模式
python -m pytest tests/ -v

# 生成覆盖率报告
python -m pytest tests/ --cov=. --cov-report=html --cov-report=term
```

### 运行测试的不同方式

```bash
# 运行所有测试
python -m pytest tests/

# 仅运行单元测试
python -m pytest tests/unit/

# 仅运行集成测试
python -m pytest tests/integration/

# 运行特定模块的测试
python -m pytest tests/unit/core/
python -m pytest tests/integration/api/

# 运行带覆盖率的测试
python -m pytest tests/ --cov=. --cov-report=html

# 使用项目测试脚本
python run_tests.py unit
python run_tests.py integration
```

## 🎯 使用测试运行器

项目提供了便捷的测试运行器脚本：

```bash
# 基础测试
python run_tests.py

# 详细模式
python run_tests.py -v

# 生成覆盖率报告
python run_tests.py --coverage

# 并行执行测试
python run_tests.py --parallel 4

# 快速测试（排除慢速测试）
python run_tests.py quick
```

## 🏷️ 按标记运行测试

```bash
# 运行单元测试
python run_tests.py unit

# 运行集成测试
python run_tests.py integration

# 运行API测试
python run_tests.py api

# 运行中间件测试
python run_tests.py middleware

# 运行GraphQL测试
python run_tests.py graphql

# 使用pytest直接运行
python -m pytest -m "unit"
python -m pytest -m "integration"
python -m pytest -m "api"
```

## 🔗 XTQuant 模块测试

项目集成了 XTQuant 量化交易模块，提供专门的测试命令：

```bash
# 运行所有XTQuant测试
python run_tests.py xtquant

# 运行特定模块测试
python run_tests.py xtquant --module config
python run_tests.py xtquant --module utils
python run_tests.py xtquant --module data
python run_tests.py xtquant --module trading
python run_tests.py xtquant --module indicators
python run_tests.py xtquant --module integration

# 运行性能测试
python run_tests.py xtquant --performance

# 详细输出
python run_tests.py xtquant -v

# 生成覆盖率报告
python run_tests.py xtquant --module config -v
```

**XTQuant 模块说明**：
- `config` - 配置管理、参数验证
- `utils` - 工具函数、数据处理
- `data` - 数据获取、缓存管理
- `trading` - 交易接口、订单管理
- `indicators` - 技术指标计算
- `integration` - 集成测试、端到端测试

## 🔄 Prefect 流程测试

项目集成了 Prefect 工作流编排系统，提供强大的后台任务管理和调度功能：

```bash
# 运行所有Prefect测试
python run_tests.py prefect

# 运行特定流程测试（支持多种输入格式）
python run_tests.py prefect --flow daily_stock_flow
python run_tests.py prefect --flow realtime_price_flow
python run_tests.py prefect --flow comprehensive_market_flow

# 简化输入（自动补全_flow后缀）
python run_tests.py prefect --flow daily_stock
python run_tests.py prefect --flow realtime_price
python run_tests.py prefect --flow batch_stock

# 运行集成测试
python run_tests.py prefect --integration

# 详细输出模式
python run_tests.py prefect --flow daily_stock_flow -v

# 生成覆盖率报告
python run_tests.py prefect --cov-report=term
```

**Prefect 流程说明**：
- `daily_stock_flow` - 每日股票数据同步流程
- `realtime_price_flow` - 实时价格更新流程
- `comprehensive_market_flow` - 综合市场数据同步流程
- `batch_stock_flow` - 批量股票数据处理流程
- `single_stock_flow` - 单只股票数据同步流程
- `sector_data_flow` - 板块数据同步流程
- `bond_repo_flow` - 债券回购数据同步流程
- `market_indices_flow` - 市场指数数据同步流程

**Prefect 集成测试**：
- `test_flow_integration.py` - 流程集成测试
- `test_flow_scheduling.py` - 调度功能测试
- `test_flow_error_handling.py` - 错误处理测试

## 📂 测试模块说明

| 测试模块 | 测试数量 | 描述 |
|---------|---------|------|
| **核心API模块** | | |
| `test_config.py` | 6个 | 配置管理、环境变量解析 |
| `test_main.py` | 7个 | 主应用、健康检查、端点测试 |
| `test_middleware.py` | 7个 | 中间件、错误处理、日志记录 |
| `test_monitoring.py` | 5个 | 监控指标、Prometheus集成 |
| `test_graphql.py` | 3个 | GraphQL查询、Schema验证 |
| `test_subscription.py` | 2个 | WebSocket订阅、实时数据 |
| **XTQuant量化模块** | | |
| `test_xtquant_config.py` | 14个 | XTQuant配置管理、参数验证 |
| `test_xtquant_utils.py` | 21个 | 工具函数、股票代码处理、金融计算 |
| `test_xtquant_data.py` | 12个 | 数据管理器、行情获取、缓存机制 |
| `test_xtquant_trading.py` | 20个 | 交易接口、订单管理、风控 |
| `test_xtquant_indicators.py` | 18个 | 技术指标计算、性能测试 |
| `test_xtquant_integration.py` | 8个 | 集成测试、端到端工作流 |
| **Prefect工作流模块** | | |
| `test_daily_stock_flow.py` | 3个 | 每日股票数据同步流程测试 |
| `test_realtime_price_flow.py` | 3个 | 实时价格更新流程测试 |
| `test_comprehensive_market_flow.py` | 3个 | 综合市场数据同步流程测试 |
| `test_batch_stock_flow.py` | 3个 | 批量股票数据处理流程测试 |
| `test_single_stock_flow.py` | 3个 | 单只股票数据同步流程测试 |
| `test_sector_data_flow.py` | 3个 | 板块数据同步流程测试 |
| `test_bond_repo_flow.py` | 3个 | 债券回购数据同步流程测试 |
| `test_market_indices_flow.py` | 3个 | 市场指数数据同步流程测试 |
| `test_flow_integration.py` | 7个 | 流程集成测试、调度验证 |
| `test_flow_scheduling.py` | 3个 | 任务调度功能测试 |
| `test_flow_error_handling.py` | 3个 | 错误处理和恢复机制测试 |

## 🔍 特定测试运行

```bash
# 运行特定测试文件
python -m pytest tests/test_config.py -v

# 运行特定测试方法
python -m pytest tests/test_main.py::test_health_check -v

# 运行匹配模式的测试
python -m pytest -k "health" -v

# 停止在第一个失败
python -m pytest tests/ -x

# 显示最慢的10个测试
python -m pytest tests/ --durations=10
```

### 🎯 XTQuant 专项测试示例

```bash
# 快速验证核心模块
python run_tests.py xtquant --module config -v
python run_tests.py xtquant --module utils -v
python run_tests.py xtquant --module data -v

# 运行特定测试类
python -m pytest tests/test_xtquant_utils.py::TestDataValidator -v

# 运行特定测试方法
python -m pytest tests/test_xtquant_config.py::test_config_default_values -v

# 运行匹配模式的XTQuant测试
python -m pytest tests/ -k "xtquant and config" -v

# 跳过慢速测试
python -m pytest tests/test_xtquant_indicators.py -m "not slow" -v

# 仅运行失败的测试
python -m pytest tests/ --lf  # --last-failed
```

### 🎯 Prefect 专项测试示例

```bash
# 快速验证核心流程
python run_tests.py prefect --flow daily_stock_flow -v
python run_tests.py prefect --flow realtime_price_flow -v
python run_tests.py prefect --flow comprehensive_market_flow -v

# 运行特定测试类
python -m pytest tests/prefector/test_daily_stock_flow.py::TestDailyStockFlow -v

# 运行特定测试方法
python -m pytest tests/prefector/test_flow_integration.py::test_flow_execution -v

# 运行匹配模式的Prefect测试
python -m pytest tests/ -k "prefect and daily" -v

# 运行所有Prefect流程测试
python -m pytest tests/prefector/ -v

# 跳过集成测试（只运行单元测试）
python -m pytest tests/prefector/ -m "not integration" -v

# 运行Prefect集成测试
python run_tests.py prefect --integration -v
```

## 📈 覆盖率报告

生成详细的覆盖率报告：

```bash
# 生成HTML覆盖率报告
python -m pytest tests/ --cov=. --cov-report=html

# 查看报告（生成在 htmlcov/ 目录）
# 在浏览器中打开 htmlcov/index.html

# 终端显示覆盖率
python -m pytest tests/ --cov=. --cov-report=term

# XML格式（适合CI/CD）
python -m pytest tests/ --cov=. --cov-report=xml
```

## 🔧 测试配置

测试配置位于 `pyproject.toml` 文件中：

```toml
[tool.pytest.ini_options]
minversion = "6.0"
addopts = "-ra -q --tb=short"
testpaths = ["tests"]
asyncio_mode = "auto"
markers = [
    "unit: 单元测试",
    "integration: 集成测试",
    "api: API测试",
    "middleware: 中间件测试",
    "graphql: GraphQL测试",
    "slow: 慢速测试"
]
```

## 🛠️ 测试环境准备

确保安装所有测试依赖：

```bash
poetry install --extras test
```

或使用 requirements.txt：

```bash
poetry install
```

## 🔄 持续集成

在CI/CD管道中运行测试：

```bash
# 完整测试套件（适合CI/CD）
python -m pytest tests/ \
  --cov=. \
  --cov-report=xml \
  --cov-report=term \
  --cov-fail-under=70 \
  --junit-xml=test-results.xml

# XTQuant 核心模块验证（推荐用于快速验证）
python run_tests.py xtquant --module config
python run_tests.py xtquant --module utils
python run_tests.py xtquant --module data

# Prefect 核心流程验证（推荐用于后台任务验证）
python run_tests.py prefect --flow daily_stock_flow
python run_tests.py prefect --flow realtime_price_flow
python run_tests.py prefect --integration

# 测试单个流程函数
python -m pytest tests/prefector/test_sector_data_flow.py::TestSectorDataSyncFlow::test_sector_data_sync_flow_success -q -s  -W ignore::DeprecationWarning

python -m pytest tests/prefector/test_batch_stock_flow.py::TestBatchStockSyncFlow::test_batch_stock_sync_flow_success -q -s -W ignore::DeprecationWarning

# 国债逆回购数据同步
python -m pytest tests/integration/prefector/test_bond_repo_flow.py::TestBatchTrrSyncFlow::test_batch_trr_sync_flow -q -s -W ignore::DeprecationWarning

# 沪深指数数据同步
python -m pytest tests/prefector/test_market_indices_flow.py::TestMarketIndicesSyncFlowIntegration::test_market_indices_sync_flow -q -s -W ignore::DeprecationWarning

# 批量保存节假日
python -m pytest tests\services\test_holiday_service.py::TestHolidayServiceIntegration::test_bulk_save_holidays_integration -q -s -W ignore::DeprecationWarning

# 测试保存 KLine Data
python -m pytest tests\services\test_market_data_service.py::TestMarketDataServiceIntegration::test_get_kline_data_integration -q -s -W ignore::DeprecationWarning

# 国债逆回购下单测试
python -m pytest tests/prefector/test_bond_repo_flow.py::TestBondRepoAutoTradeFlowIntegration::test_bond_repo_auto_trade_flow_complete_real_flow -q -s -W ignore::DeprecationWarning

# 每日交易数据同步
python -m pytest tests/prefector/test_daily_trading_sync_flow.py::TestDailyTradingSyncFlowIntegration::test_daily_trading_sync_flow_complete_real_flow -q -s -W ignore::DeprecationWarning

# 同步市场数据
python -m pytest tests/prefector/test_daily_market_data_sync_flow.py::TestDailyMarketDataSyncFlowIntegration::test_daily_market_data_sync_flow_complete_real_flow -q -s -W ignore::DeprecationWarning

python -m pytest tests/integration/prefector/test_daily_market_data_sync_flow.py::TestDailyMarketDataSyncFlowIntegration::test_daily_market_data_sync_flow_specific_stocks -q -s -W ignore::DeprecationWarning

# 完整XTQuant测试（包含所有模块）
python run_tests.py xtquant --cov-report=xml

# 完整Prefect测试（包含所有流程）
python run_tests.py prefect --cov-report=xml
```

---

**相关文档**：
- [系统架构](./ARCHITECTURE.md)
- [功能模块](./MODULES.md)
- [API接口文档](./API.md)
- [编码规范](./CODING_STANDARDS.md)

*此文档包含了QuantX项目完整的测试指南和最佳实践。如有问题，请参考主README.md文件。*
