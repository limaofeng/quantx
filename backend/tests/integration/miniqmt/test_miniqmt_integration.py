"""
MiniQMT 模块集成测试
"""
import pytest
import pandas as pd
import numpy as np
from unittest.mock import patch, MagicMock

class TestMiniQMTIntegration:
    """MiniQMT 模块集成测试"""

    def test_module_imports(self):
        """测试模块导入"""
        # 测试主模块导入
        from miniqmt import __version__, __author__

        assert __version__ == "1.0.0"
        assert __author__ == "QuantX Team"

        # 测试子模块导入
        from miniqmt.config.config_manager import XTQuantConfig, xt_config
        from miniqmt.data.data_manager import XTDataManager, xt_data_manager
        from miniqmt.trading.trading_manager import XTTradingManager, create_trading_manager
        # technical_indicators 已经移除，现在使用独立的指标模块
        # from core.indicators.technical_indicators import TechnicalIndicators, add_technical_indicators
        from miniqmt.utils.helpers import normalize_stock_code, DataValidator

        # 验证类可以实例化
        assert XTQuantConfig() is not None
        assert XTDataManager() is not None
        assert XTTradingManager() is not None
        assert TechnicalIndicators(pd.DataFrame({'close': [1, 2, 3]})) is not None

    def test_config_integration(self):
        """测试配置集成"""
        from miniqmt.config.config_manager import xt_config
        from miniqmt.data.data_manager import XTDataManager
        from miniqmt.trading.trading_manager import XTTradingManager

        # 测试配置在不同模块中的使用
        data_manager = XTDataManager(config=xt_config)
        trading_manager = XTTradingManager(config=xt_config)

        assert data_manager.config is xt_config
        assert trading_manager.config is xt_config

    @patch('xtquant.data.data_manager.xtdata')
    @patch('xtquant.trading.trading_manager.xttrader')
    def test_complete_workflow(self, mock_xttrader, mock_xtdata):
        """测试完整工作流程"""
        from miniqmt.config.config_manager import XTQuantConfig
        from miniqmt.data.data_manager import XTDataManager
        from miniqmt.trading.trading_manager import XTTradingManager, OrderType
        # from core.indicators.technical_indicators import TechnicalIndicators
        from miniqmt.utils.helpers import normalize_stock_code

        # 1. 配置初始化
        config = XTQuantConfig()
        config.set('trading.risk_control.max_position_ratio', 0.8)

        # 2. 数据管理器
        mock_xtdata.connect.return_value = True
        mock_market_data = pd.DataFrame({
            'close': [10, 11, 12, 11, 10, 13, 12, 14, 13, 15],
            'high': [10.5, 11.5, 12.5, 11.5, 10.5, 13.5, 12.5, 14.5, 13.5, 15.5],
            'low': [9.5, 10.5, 11.5, 10.5, 9.5, 12.5, 11.5, 13.5, 12.5, 14.5],
            'volume': [1000, 1100, 1200, 1050, 950, 1300, 1150, 1400, 1250, 1500]
        })
        mock_xtdata.get_market_data.return_value = mock_market_data

        data_manager = XTDataManager(config=config)
        assert data_manager.connect() == True

        # 3. 获取和处理数据
        stock_code = normalize_stock_code('000001')
        market_data = data_manager.get_market_data([stock_code], '1d')
        assert isinstance(market_data, pd.DataFrame)
        assert len(market_data) == 10

        # 4. 技术指标计算
        indicators = TechnicalIndicators(market_data)
        sma_5 = indicators.sma(period=5)
        rsi = indicators.rsi(period=5)

        assert isinstance(sma_5, pd.Series)
        assert isinstance(rsi, pd.Series)

        # 5. 交易管理器
        mock_xttrader.connect.return_value = 0
        mock_xttrader.order_stock.return_value = "ORDER_123"
        mock_xttrader.query_stock_asset.return_value = {
            'total_asset': 1000000.0,
            'available_cash': 500000.0
        }

        trading_manager = XTTradingManager(account_id="test_account", config=config)
        assert trading_manager.connect() == True

        # 6. 获取账户信息
        account_info = trading_manager.get_account_info()
        assert account_info['total_asset'] == 1000000.0

        # 7. 交易决策和执行
        latest_price = market_data['close'].iloc[-1]
        latest_sma = sma_5.iloc[-1]

        if not pd.isna(latest_sma) and latest_price > latest_sma:
            # 价格高于移动平均线，买入信号
            order_id = trading_manager.place_order(
                stock_code=stock_code,
                order_type=OrderType.BUY,
                quantity=100,
                price=latest_price
            )
            assert order_id == "ORDER_123"

    def test_error_handling_integration(self):
        """测试集成错误处理"""
        from miniqmt.config.config_manager import XTQuantConfig
        from miniqmt.data.data_manager import XTDataManager
        from miniqmt.utils.helpers import DataValidator

        # 配置错误处理
        config = XTQuantConfig()
        config.set('invalid.config.path', 'test')  # 设置无效配置

        # 数据验证错误处理
        validator = DataValidator()

        invalid_data = pd.DataFrame({
            'open': [10, 11, 12],
            'high': [9, 10, 11],  # high < open (错误)
            'low': [8, 9, 10],
            'close': [9.5, 10.5, 11.5]
        })

        assert validator.validate_price_data(invalid_data) == False

        # 数据管理器错误处理
        with patch('xtquant.data.data_manager.xtdata') as mock_xtdata:
            mock_xtdata.connect.side_effect = Exception("Connection failed")

            data_manager = XTDataManager()
            # 应该捕获异常而不是崩溃
            result = data_manager.connect()
            assert result == False or result is None

    def test_performance_integration(self):
        """测试性能集成"""
        from core.indicators.technical_indicators import add_technical_indicators
        from miniqmt.utils.helpers import batch_normalize_stock_codes, batch_validate_stock_codes

        # 批量处理股票代码
        stock_codes = ['000001', '000002', '600036', '300750'] * 25  # 100个代码

        normalized_codes = batch_normalize_stock_codes(stock_codes)
        validation_results = batch_validate_stock_codes(normalized_codes)

        assert len(normalized_codes) == 100
        assert len(validation_results) == 100
        assert all(validation_results)  # 所有代码应该都有效

        # 批量计算技术指标
        np.random.seed(42)
        large_data = pd.DataFrame({
            'close': 100 + np.random.randn(252) * 5,  # 一年的数据
            'high': 105 + np.random.randn(252) * 5,
            'low': 95 + np.random.randn(252) * 5,
            'volume': np.random.randint(10000, 100000, 252)
        })

        # 批量添加技术指标
        enriched_data = add_technical_indicators(large_data)

        assert isinstance(enriched_data, pd.DataFrame)
        assert len(enriched_data) == 252
        assert 'sma_20' in enriched_data.columns
        assert 'ema_12' in enriched_data.columns
        assert 'rsi_14' in enriched_data.columns

class TestXTQuantUseCases:
    """XTQuant 使用案例测试"""

    @patch('xtquant.data.data_manager.xtdata')
    def test_quantitative_strategy_case(self, mock_xtdata):
        """测试量化策略使用案例"""
        from miniqmt.data.data_manager import XTDataManager
        from core.indicators.technical_indicators import TechnicalIndicators
        from miniqmt.utils.helpers import normalize_stock_code

        # 模拟历史数据
        np.random.seed(42)
        mock_data = pd.DataFrame({
            'close': 100 * (1 + np.random.randn(60) * 0.02).cumprod(),
            'high': 0,  # 将在后面设置
            'low': 0,   # 将在后面设置
            'volume': np.random.randint(10000, 50000, 60)
        })
        mock_data['high'] = mock_data['close'] * (1 + np.random.uniform(0, 0.05, 60))
        mock_data['low'] = mock_data['close'] * (1 - np.random.uniform(0, 0.05, 60))
        mock_xtdata.get_market_data.return_value = mock_data

        # 1. 获取数据
        data_manager = XTDataManager()
        stock_code = normalize_stock_code('000001')
        data = data_manager.get_market_data([stock_code], '1d', '2023-01-01', '2023-03-01')

        # 2. 计算技术指标
        indicators = TechnicalIndicators(data)
        sma_short = indicators.sma(period=5)
        sma_long = indicators.sma(period=20)
        rsi = indicators.rsi(period=14)

        # 3. 生成交易信号
        buy_signal = (
            (sma_short > sma_long) &
            (sma_short.shift(1) <= sma_long.shift(1)) &
            (rsi < 70)
        )

        sell_signal = (
            (sma_short < sma_long) &
            (sma_short.shift(1) >= sma_long.shift(1)) &
            (rsi > 30)
        )

        # 4. 回测验证
        signals = pd.DataFrame({
            'price': data['close'],
            'buy': buy_signal,
            'sell': sell_signal
        })

        buy_points = signals[signals['buy']].index
        sell_points = signals[signals['sell']].index

        # 验证信号生成
        assert isinstance(buy_points, pd.Index)
        assert isinstance(sell_points, pd.Index)
        assert len(signals) == 60

    @patch('xtquant.trading.trading_manager.xttrader')
    def test_risk_management_case(self, mock_xttrader):
        """测试风险管理使用案例"""
        from miniqmt.trading.trading_manager import XTTradingManager, OrderType
        from miniqmt.config.config_manager import XTQuantConfig

        # 配置风险参数
        config = XTQuantConfig()
        config.set('trading.risk_control.max_position_ratio', 0.6)
        config.set('trading.risk_control.max_single_stock_ratio', 0.1)
        config.set('trading.risk_control.stop_loss_ratio', 0.05)

        # 模拟账户和持仓信息
        mock_account = {
            'total_asset': 1000000.0,
            'available_cash': 400000.0,
            'market_value': 600000.0
        }

        mock_positions = pd.DataFrame({
            'stock_code': ['000001.SZ', '600036.SH'],
            'volume': [5000, 2000],
            'open_price': [20.0, 50.0],
            'last_price': [19.0, 52.0],  # 000001下跌5%，600036上涨4%
            'market_value': [95000.0, 104000.0]
        })

        mock_xttrader.query_stock_asset.return_value = mock_account
        mock_xttrader.query_stock_positions.return_value = mock_positions
        mock_xttrader.order_stock.return_value = "RISK_ORDER"

        trading_manager = XTTradingManager(account_id="test_account", config=config)

        # 1. 检查账户状态
        account_info = trading_manager.get_account_info()
        positions = trading_manager.get_positions()

        # 2. 风险控制检查
        # 检查是否需要止损
        for _, position in positions.iterrows():
            price_change = (position['last_price'] - position['open_price']) / position['open_price']
            stop_loss_threshold = -config.get('trading.risk_control.stop_loss_ratio', 0.05)

            if price_change <= stop_loss_threshold:
                # 触发止损
                order_id = trading_manager.place_order(
                    stock_code=position['stock_code'],
                    order_type=OrderType.SELL,
                    quantity=position['volume'],
                    price=position['last_price']
                )
                assert order_id == "RISK_ORDER"

        # 3. 检查新建仓位风险
        new_order_amount = 150000  # 15万的新订单
        current_position_ratio = mock_account['market_value'] / mock_account['total_asset']
        new_position_ratio = (mock_account['market_value'] + new_order_amount) / mock_account['total_asset']

        max_position_ratio = config.get('trading.risk_control.max_position_ratio', 0.95)

        if new_position_ratio > max_position_ratio:
            # 超过最大仓位比例，拒绝下单
            risk_check_passed = False
        else:
            risk_check_passed = True

        assert risk_check_passed == False  # 应该被风控拒绝

    def test_data_analysis_case(self):
        """测试数据分析使用案例"""
        from miniqmt.utils.helpers import (
            calculate_returns,
            calculate_cumulative_returns,
            calculate_max_drawdown,
            calculate_sharpe_ratio,
            calculate_volatility
        )

        # 模拟价格数据
        np.random.seed(42)
        prices = pd.Series(100 * (1 + np.random.randn(252) * 0.015).cumprod())

        # 1. 收益率分析
        returns = calculate_returns(prices)
        cum_returns = calculate_cumulative_returns(returns)

        # 2. 风险指标分析
        max_drawdown, peak_date, trough_date = calculate_max_drawdown(prices)
        sharpe_ratio = calculate_sharpe_ratio(returns, risk_free_rate=0.03)
        annual_volatility = calculate_volatility(returns, period='annual')

        # 3. 生成分析报告
        analysis_report = {
            'total_return': cum_returns.iloc[-1],
            'annual_return': (1 + cum_returns.iloc[-1]) ** (252 / len(returns)) - 1,
            'max_drawdown': max_drawdown,
            'sharpe_ratio': sharpe_ratio,
            'volatility': annual_volatility,
            'total_trades': len(returns),
            'win_rate': len(returns[returns > 0]) / len(returns[returns != 0])
        }

        # 验证分析结果
        assert isinstance(analysis_report['total_return'], float)
        assert isinstance(analysis_report['max_drawdown'], float)
        assert isinstance(analysis_report['sharpe_ratio'], float)
        assert 0 <= analysis_report['win_rate'] <= 1
        assert analysis_report['max_drawdown'] >= 0

class TestXTQuantDocumentation:
    """XTQuant 文档和示例测试"""

    def test_readme_examples(self):
        """测试 README 中的示例代码"""
        # 这里可以添加 README 中示例代码的测试
        # 确保文档中的代码示例是可运行的
        pass

    def test_api_documentation(self):
        """测试 API 文档的完整性"""
        from miniqmt.config.config_manager import XTQuantConfig
        from miniqmt.data.data_manager import XTDataManager
        from miniqmt.trading.trading_manager import XTTradingManager
        from core.indicators.technical_indicators import TechnicalIndicators

        # 检查主要类是否有文档字符串
        assert XTQuantConfig.__doc__ is not None
        assert XTDataManager.__doc__ is not None
        assert XTTradingManager.__doc__ is not None
        # assert TechnicalIndicators.__doc__ is not None

        # 检查主要方法是否有文档字符串
        assert XTDataManager.get_market_data.__doc__ is not None
        assert XTTradingManager.place_order.__doc__ is not None
        # assert TechnicalIndicators.sma.__doc__ is not None
