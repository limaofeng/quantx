"""
MiniQMT 交易管理测试
"""
import pytest
import pandas as pd
from unittest.mock import patch, MagicMock

class TestMiniQMTTradingManager:
    """MiniQMT 交易管理器测试"""

    def test_trading_manager_initialization(self):
        """测试交易管理器初始化"""
        from miniqmt.trading.trading_manager import XTTradingManager

        manager = XTTradingManager()
        assert manager is not None
        assert hasattr(manager, 'account_id')
        assert hasattr(manager, 'config')

    def test_trading_manager_factory(self):
        """测试交易管理器工厂函数"""
        from miniqmt.trading.trading_manager import create_trading_manager

        manager = create_trading_manager(account_id="test_account")
        assert manager is not None
        assert manager.account_id == "test_account"

    @patch('xtquant.trading.trading_manager.xttrader')
    def test_connect_to_trading_server(self, mock_xttrader):
        """测试连接到交易服务器"""
        from miniqmt.trading.trading_manager import XTTradingManager

        # 模拟连接成功
        mock_xttrader.connect.return_value = 0  # 0表示成功

        manager = XTTradingManager()
        result = manager.connect()

        assert result == True
        mock_xttrader.connect.assert_called_once()

    @patch('xtquant.trading.trading_manager.xttrader')
    def test_place_order(self, mock_xttrader):
        """测试下单"""
        from miniqmt.trading.trading_manager import XTTradingManager, OrderType, OrderPriceType

        # 模拟下单成功
        mock_order_id = "ORDER_123456"
        mock_xttrader.order_stock.return_value = mock_order_id

        manager = XTTradingManager(account_id="test_account")

        order_id = manager.place_order(
            stock_code="000001.SZ",
            order_type=OrderType.BUY,
            quantity=100,
            price=10.50,
            price_type=OrderPriceType.LIMIT
        )

        assert order_id == mock_order_id
        mock_xttrader.order_stock.assert_called_once()

    @patch('xtquant.trading.trading_manager.xttrader')
    def test_cancel_order(self, mock_xttrader):
        """测试撤单"""
        from miniqmt.trading.trading_manager import XTTradingManager

        # 模拟撤单成功
        mock_xttrader.cancel_order_stock.return_value = 0  # 0表示成功

        manager = XTTradingManager(account_id="test_account")
        result = manager.cancel_order("ORDER_123456")

        assert result == True
        mock_xttrader.cancel_order_stock.assert_called_once()

    @patch('xtquant.trading.trading_manager.xttrader')
    def test_get_account_info(self, mock_xttrader):
        """测试获取账户信息"""
        from miniqmt.trading.trading_manager import XTTradingManager

        # 模拟账户信息
        mock_account_info = {
            'account_id': 'test_account',
            'total_asset': 1000000.0,
            'available_cash': 500000.0,
            'market_value': 500000.0,
            'frozen_cash': 0.0
        }
        mock_xttrader.query_stock_asset.return_value = mock_account_info

        manager = XTTradingManager(account_id="test_account")
        account_info = manager.get_account_info()

        assert isinstance(account_info, dict)
        assert account_info['total_asset'] == 1000000.0
        assert account_info['available_cash'] == 500000.0

    @patch('xtquant.trading.trading_manager.xttrader')
    def test_get_positions(self, mock_xttrader):
        """测试获取持仓信息"""
        from miniqmt.trading.trading_manager import XTTradingManager

        # 模拟持仓数据
        mock_positions = pd.DataFrame({
            'stock_code': ['000001.SZ', '600036.SH'],
            'stock_name': ['平安银行', '招商银行'],
            'volume': [1000, 500],
            'can_use_volume': [1000, 500],
            'open_price': [12.50, 45.80],
            'last_price': [13.20, 47.50],
            'unrealized_pnl': [700.0, 850.0]
        })
        mock_xttrader.query_stock_positions.return_value = mock_positions

        manager = XTTradingManager(account_id="test_account")
        positions = manager.get_positions()

        assert isinstance(positions, pd.DataFrame)
        assert len(positions) == 2
        assert '000001.SZ' in positions['stock_code'].values
        assert '600036.SH' in positions['stock_code'].values

    @patch('xtquant.trading.trading_manager.xttrader')
    def test_get_orders(self, mock_xttrader):
        """测试获取订单信息"""
        from miniqmt.trading.trading_manager import XTTradingManager

        # 模拟订单数据
        mock_orders = pd.DataFrame({
            'order_id': ['ORDER_123456', 'ORDER_123457'],
            'stock_code': ['000001.SZ', '600036.SH'],
            'order_type': ['buy', 'sell'],
            'order_volume': [100, 200],
            'price': [10.50, 47.00],
            'traded_volume': [100, 0],
            'order_status': ['filled', 'pending'],
            'order_time': ['2023-01-01 09:30:00', '2023-01-01 10:00:00']
        })
        mock_xttrader.query_stock_orders.return_value = mock_orders

        manager = XTTradingManager(account_id="test_account")
        orders = manager.get_orders()

        assert isinstance(orders, pd.DataFrame)
        assert len(orders) == 2
        assert 'ORDER_123456' in orders['order_id'].values

    @patch('xtquant.trading.trading_manager.xttrader')
    def test_get_trades(self, mock_xttrader):
        """测试获取成交信息"""
        from miniqmt.trading.trading_manager import XTTradingManager

        # 模拟成交数据
        mock_trades = pd.DataFrame({
            'order_id': ['ORDER_123456', 'ORDER_123456'],
            'stock_code': ['000001.SZ', '000001.SZ'],
            'trade_volume': [50, 50],
            'trade_price': [10.50, 10.52],
            'trade_time': ['2023-01-01 09:30:01', '2023-01-01 09:30:15'],
            'trade_amount': [525.0, 526.0]
        })
        mock_xttrader.query_stock_trades.return_value = mock_trades

        manager = XTTradingManager(account_id="test_account")
        trades = manager.get_trades()

        assert isinstance(trades, pd.DataFrame)
        assert len(trades) == 2
        assert trades['trade_volume'].sum() == 100

    def test_order_validation(self):
        """测试订单验证"""
        from miniqmt.trading.trading_manager import XTTradingManager, OrderType

        manager = XTTradingManager(account_id="test_account")

        # 测试有效订单
        valid_order = {
            'stock_code': '000001.SZ',
            'order_type': OrderType.BUY,
            'quantity': 100,
            'price': 10.50
        }
        assert manager.validate_order(valid_order) == True

        # 测试无效订单 - 负数量
        invalid_order = {
            'stock_code': '000001.SZ',
            'order_type': OrderType.BUY,
            'quantity': -100,
            'price': 10.50
        }
        assert manager.validate_order(invalid_order) == False

        # 测试无效订单 - 零价格
        invalid_price_order = {
            'stock_code': '000001.SZ',
            'order_type': OrderType.BUY,
            'quantity': 100,
            'price': 0
        }
        assert manager.validate_order(invalid_price_order) == False

    def test_risk_control(self):
        """测试风险控制"""
        from miniqmt.trading.trading_manager import XTTradingManager, OrderType

        manager = XTTradingManager(account_id="test_account")

        # 模拟账户信息
        mock_account = {
            'total_asset': 1000000.0,
            'available_cash': 500000.0,
            'market_value': 500000.0
        }

        with patch.object(manager, 'get_account_info', return_value=mock_account):
            # 测试正常订单
            result = manager.check_risk_limits(
                stock_code='000001.SZ',
                order_type=OrderType.BUY,
                quantity=1000,
                price=100.0  # 总额10万，占总资产10%
            )
            assert result == True

            # 测试超过单只股票限制的订单
            result = manager.check_risk_limits(
                stock_code='000001.SZ',
                order_type=OrderType.BUY,
                quantity=3000,
                price=100.0  # 总额30万，超过20%限制
            )
            assert result == False

    def test_position_management(self):
        """测试持仓管理"""
        from miniqmt.trading.trading_manager import XTTradingManager

        manager = XTTradingManager(account_id="test_account")

        # 模拟持仓数据
        mock_positions = pd.DataFrame({
            'stock_code': ['000001.SZ', '600036.SH'],
            'volume': [1000, 500],
            'open_price': [12.50, 45.80],
            'last_price': [13.20, 47.50],
            'market_value': [13200.0, 23750.0]
        })

        with patch.object(manager, 'get_positions', return_value=mock_positions):
            # 测试获取单只股票持仓
            position = manager.get_position('000001.SZ')
            assert position is not None
            assert position['volume'] == 1000

            # 测试获取总持仓价值
            total_value = manager.get_total_position_value()
            assert total_value == 36950.0  # 13200 + 23750

            # 测试获取持仓收益
            total_pnl = manager.get_total_unrealized_pnl()
            assert isinstance(total_pnl, float)

class TestOrderTypes:
    """订单类型测试"""

    def test_order_type_enum(self):
        """测试订单类型枚举"""
        from miniqmt.trading.trading_manager import OrderType

        assert OrderType.BUY == 'buy'
        assert OrderType.SELL == 'sell'

    def test_order_price_type_enum(self):
        """测试订单价格类型枚举"""
        from miniqmt.trading.trading_manager import OrderPriceType

        assert hasattr(OrderPriceType, 'LIMIT')
        assert hasattr(OrderPriceType, 'MARKET')

class TestTradingStrategy:
    """交易策略测试"""

    @patch('xtquant.trading.trading_manager.xttrader')
    def test_simple_buy_strategy(self, mock_xttrader):
        """测试简单买入策略"""
        from miniqmt.trading.trading_manager import XTTradingManager, OrderType, OrderPriceType

        # 模拟成功下单
        mock_xttrader.order_stock.return_value = "ORDER_123456"

        manager = XTTradingManager(account_id="test_account")

        # 模拟策略信号
        buy_signals = [
            {'stock_code': '000001.SZ', 'price': 10.50, 'quantity': 100},
            {'stock_code': '600036.SH', 'price': 47.00, 'quantity': 200}
        ]

        order_ids = []
        for signal in buy_signals:
            order_id = manager.place_order(
                stock_code=signal['stock_code'],
                order_type=OrderType.BUY,
                quantity=signal['quantity'],
                price=signal['price'],
                price_type=OrderPriceType.LIMIT
            )
            order_ids.append(order_id)

        assert len(order_ids) == 2
        assert all(order_id is not None for order_id in order_ids)

    @patch('xtquant.trading.trading_manager.xttrader')
    def test_stop_loss_strategy(self, mock_xttrader):
        """测试止损策略"""
        from miniqmt.trading.trading_manager import XTTradingManager, OrderType

        manager = XTTradingManager(account_id="test_account")

        # 模拟持仓
        mock_position = {
            'stock_code': '000001.SZ',
            'volume': 1000,
            'open_price': 12.50,
            'last_price': 11.50  # 下跌8%
        }

        # 检查是否触发止损（假设止损比例为5%）
        stop_loss_threshold = mock_position['open_price'] * 0.95
        should_stop_loss = mock_position['last_price'] <= stop_loss_threshold

        assert should_stop_loss == True

        if should_stop_loss:
            # 模拟止损卖出
            mock_xttrader.order_stock.return_value = "STOP_LOSS_ORDER"

            order_id = manager.place_order(
                stock_code=mock_position['stock_code'],
                order_type=OrderType.SELL,
                quantity=mock_position['volume'],
                price=mock_position['last_price']
            )

            assert order_id == "STOP_LOSS_ORDER"

class TestTradingIntegration:
    """交易集成测试"""

    @patch('xtquant.trading.trading_manager.xttrader')
    def test_complete_trading_workflow(self, mock_xttrader):
        """测试完整交易工作流"""
        from miniqmt.trading.trading_manager import XTTradingManager, OrderType, OrderPriceType

        # 模拟各种交易操作
        mock_xttrader.connect.return_value = 0
        mock_xttrader.order_stock.return_value = "ORDER_123456"
        mock_xttrader.cancel_order_stock.return_value = 0
        mock_xttrader.query_stock_asset.return_value = {
            'total_asset': 1000000.0,
            'available_cash': 500000.0
        }

        manager = XTTradingManager(account_id="test_account")

        # 1. 连接到交易服务器
        assert manager.connect() == True

        # 2. 检查账户信息
        account_info = manager.get_account_info()
        assert account_info['total_asset'] == 1000000.0

        # 3. 下单
        order_id = manager.place_order(
            stock_code="000001.SZ",
            order_type=OrderType.BUY,
            quantity=100,
            price=10.50,
            price_type=OrderPriceType.LIMIT
        )
        assert order_id == "ORDER_123456"

        # 4. 撤单
        cancel_result = manager.cancel_order(order_id)
        assert cancel_result == True

        # 5. 断开连接
        manager.disconnect()

    def test_trading_with_config(self):
        """测试交易配置集成"""
        from miniqmt.trading.trading_manager import XTTradingManager
        from miniqmt.config.config_manager import XTQuantConfig

        # 创建自定义配置
        config = XTQuantConfig()
        config.set('trading.risk_control.max_position_ratio', 0.8)
        config.set('trading.order_settings.default_price_type', 'market')

        manager = XTTradingManager(account_id="test_account", config=config)

        # 验证配置被正确应用
        assert manager.config.get('trading.risk_control.max_position_ratio') == 0.8
        assert manager.config.get('trading.order_settings.default_price_type') == 'market'

    @patch('xtquant.trading.trading_manager.xttrader')
    def test_error_handling_and_recovery(self, mock_xttrader):
        """测试错误处理和恢复"""
        from miniqmt.trading.trading_manager import XTTradingManager, OrderType

        manager = XTTradingManager(account_id="test_account")

        # 模拟网络错误
        mock_xttrader.order_stock.side_effect = Exception("Network error")

        # 尝试下单，应该捕获异常并返回None
        order_id = manager.place_order(
            stock_code="000001.SZ",
            order_type=OrderType.BUY,
            quantity=100,
            price=10.50
        )

        assert order_id is None

    def test_performance_monitoring(self):
        """测试性能监控"""
        from miniqmt.trading.trading_manager import XTTradingManager

        manager = XTTradingManager(account_id="test_account")

        # 模拟交易历史
        trades_history = pd.DataFrame({
            'trade_time': pd.date_range('2023-01-01', periods=10, freq='D'),
            'stock_code': ['000001.SZ'] * 10,
            'trade_type': ['buy', 'sell'] * 5,
            'trade_price': [10.0, 10.5, 11.0, 10.8, 11.2, 11.5, 12.0, 11.8, 12.5, 12.2],
            'trade_volume': [100] * 10,
            'trade_amount': [1000, 1050, 1100, 1080, 1120, 1150, 1200, 1180, 1250, 1220]
        })

        with patch.object(manager, 'get_trades', return_value=trades_history):
            # 计算交易统计
            stats = manager.get_trading_statistics()

            assert isinstance(stats, dict)
            assert 'total_trades' in stats
            assert 'total_volume' in stats
            assert 'total_amount' in stats

            # 验证统计数据
            assert stats['total_trades'] == 10
            assert stats['total_volume'] == 1000
            assert stats['total_amount'] == 11550  # 总交易金额
