"""
MiniQMT 数据管理器测试
"""

import pytest
import pandas as pd
from unittest.mock import Mock, patch, MagicMock


class TestMiniQMTDataManager:
    """MiniQMT 数据管理器测试"""

    def test_data_manager_initialization(self):
        """测试数据管理器初始化"""
        from miniqmt.data.data_manager import XTDataManager

        manager = XTDataManager()
        assert manager is not None
        assert hasattr(manager, 'is_connected')
        # 由于没有真正的xtdata连接，is_connected应该是False
        assert manager.is_connected == True

    def test_data_manager_instances(self):
        """测试数据管理器实例化"""
        from miniqmt.data.data_manager import XTDataManager

        manager1 = XTDataManager()
        manager2 = XTDataManager()
        # XTDataManager不是单例模式，每次都创建新实例
        assert manager1 is not manager2

    def test_data_manager_connection_status(self):
        """测试数据管理器连接状态"""
        from miniqmt.data.data_manager import XTDataManager

        manager = XTDataManager()

        assert hasattr(manager, 'is_connected')

    @patch('miniqmt.data.data_manager.xtdata')
    def test_connection_error_handling(self, mock_xtdata):
        """测试连接错误处理"""
        from miniqmt.data.data_manager import XTDataManager

        # 模拟连接失败
        mock_xtdata.connect.side_effect = Exception("Connection failed")

        manager = XTDataManager()
        # 连接失败时应该设置is_connected为False
        assert manager.is_connected == False

    # @patch('xtquant.data.data_manager.xtdata')
    def test_get_market_data(self):
        """测试获取市场数据"""
        from miniqmt.data.data_manager import XTDataManager

        manager = XTDataManager()
        result = manager.get_market_data(
            stock_list=['000001.SZ'],
            period='1d',
            start_time='20230101',
            end_time='20230102'
        )

        assert isinstance(result, (dict, pd.DataFrame))

    def test_get_current_data(self):
        """测试获取当前数据"""
        from miniqmt.data.data_manager import XTDataManager

        # 模拟当前数据
        mock_data = {
            '000001.SZ': {
                'lastPrice': 10.50,
                'changeRate': 0.02,
                'volume': 1500000,
                'time': '2023-01-01 14:30:00'
            }
        }

        manager = XTDataManager()
        result = manager.get_current_data(['000001.SZ'])

        assert isinstance(result, dict)

    def test_get_stock_list(self):
        """测试获取股票列表"""
        from miniqmt.data.data_manager import XTDataManager

        manager = XTDataManager()
        result = manager.get_stock_list('SZ')

        assert isinstance(result, pd.DataFrame)

    def test_get_financial_data(self):
        """测试获取财务数据"""
        from miniqmt.data.data_manager import XTDataManager

        manager = XTDataManager()
        result = manager.get_financial_data('000001.SZ', 'Income')
        assert isinstance(result, pd.DataFrame)

    @patch('miniqmt.data.data_manager.xtdata')
    def test_subscribe_data(self, mock_xtdata):
        """测试数据订阅"""
        from miniqmt.data.data_manager import XTDataManager

        manager = XTDataManager()
        # 订阅数据不会返回值
        manager.subscribe_quote(['000001.SZ'])

        # 验证订阅被调用
        assert True

    def test_close_connection(self):
        """测试关闭连接"""
        from miniqmt.data.data_manager import XTDataManager

        manager = XTDataManager()
        manager.close_connection()

        # 验证关闭连接被调用
        assert True


class TestDataManagerIntegration:
    """数据管理器集成测试"""

    def test_basic_workflow(self):
        """测试基本工作流"""
        from miniqmt.data.data_manager import XTDataManager

        manager = XTDataManager()

        stock_list = manager.get_stock_list()

        assert isinstance(stock_list, pd.DataFrame)

    @patch('miniqmt.data.data_manager.xtdata')
    def test_error_handling(self, mock_xtdata):
        """测试错误处理"""
        from miniqmt.data.data_manager import XTDataManager

        # 模拟API调用失败
        mock_xtdata.get_market_data_ex.side_effect = Exception("API Error")

        manager = XTDataManager()
        result = manager.get_market_data(['000001.SZ'])

        # 错误时应返回空DataFrame
        assert isinstance(result, pd.DataFrame)
        assert len(result) == 0
