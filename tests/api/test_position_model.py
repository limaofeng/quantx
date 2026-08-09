"""
测试 Position 模型的价格字段验证功能
"""

import math

from quantx_infrastructure.models.position import Position


class TestPositionPriceSanitization:
    """测试价格字段清理功能"""

    def test_sanitize_normal_price_value(self):
        """测试正常价格值应该被保留"""
        # 测试正常浮点数
        assert Position._sanitize_price_value(100.5) == 100.5
        assert Position._sanitize_price_value(0.01) == 0.01
        assert Position._sanitize_price_value(9999.99) == 9999.99

        # 测试整数
        assert Position._sanitize_price_value(100) == 100.0
        assert Position._sanitize_price_value(0) == 0.0

    def test_sanitize_infinite_values(self):
        """测试无限值应该被转换为None"""
        # 测试正无穷
        assert Position._sanitize_price_value(float('inf')) is None
        assert Position._sanitize_price_value(math.inf) is None

        # 测试负无穷
        assert Position._sanitize_price_value(float('-inf')) is None
        assert Position._sanitize_price_value(-math.inf) is None

    def test_sanitize_nan_value(self):
        """测试NaN应该被转换为None"""
        assert Position._sanitize_price_value(float('nan')) is None
        assert Position._sanitize_price_value(math.nan) is None

    def test_sanitize_none_value(self):
        """测试None应该保持为None"""
        assert Position._sanitize_price_value(None) is None

    def test_sanitize_invalid_types(self):
        """测试无效类型应该被转换为None"""
        assert Position._sanitize_price_value("invalid") is None
        assert Position._sanitize_price_value("") is None
        assert Position._sanitize_price_value([]) is None
        assert Position._sanitize_price_value({}) is None

    def test_from_dict_with_normal_prices(self):
        """测试from_dict处理正常价格值"""
        data = {
            "account_id": "300000013250",
            "account_type": 48,
            "stock_code": "600036.SH",
            "instrument_name": "招商银行",
            "volume": 1000,
            "can_use_volume": 1000,
            "frozen_volume": 0,
            "on_road_volume": 0,
            "yesterday_volume": 1000,
            "open_price": 35.5,
            "avg_price": 35.2,
            "last_price": 35.8,
            "market_value": 35500.0,
            "direction": 48
        }

        position = Position.from_dict(data)

        assert position.open_price == 35.5
        assert position.avg_price == 35.2
        assert position.last_price == 35.8
        assert position.market_value == 35500.0

    def test_from_dict_with_infinite_open_price(self):
        """测试from_dict处理无限开仓价"""
        data = {
            "account_id": "300000013250",
            "account_type": 48,
            "stock_code": "600036.SH",
            "instrument_name": "招商银行",
            "volume": 1000,
            "can_use_volume": 1000,
            "frozen_volume": 0,
            "on_road_volume": 0,
            "yesterday_volume": 1000,
            "open_price": float('-inf'),  # 负无穷
            "avg_price": 35.2,
            "market_value": 35500.0,
            "direction": 48
        }

        position = Position.from_dict(data)

        # 开仓价应该被转换为None
        assert position.open_price is None
        # 其他价格应该保持正常
        assert position.avg_price == 35.2
        assert position.market_value == 35500.0

    def test_from_dict_with_infinite_avg_price(self):
        """测试from_dict处理无限成本价"""
        data = {
            "account_id": "300000013250",
            "account_type": 48,
            "stock_code": "600036.SH",
            "instrument_name": "招商银行",
            "volume": 1000,
            "can_use_volume": 1000,
            "frozen_volume": 0,
            "on_road_volume": 0,
            "yesterday_volume": 1000,
            "open_price": 35.5,
            "avg_price": float('-inf'),  # 负无穷
            "market_value": 35500.0,
            "direction": 48
        }

        position = Position.from_dict(data)

        # 成本价应该被转换为None
        assert position.avg_price is None
        assert position.open_price == 35.5
        assert position.market_value == 35500.0

    def test_from_dict_with_nan_market_value(self):
        """测试from_dict处理NaN市值"""
        data = {
            "account_id": "300000013250",
            "account_type": 48,
            "stock_code": "600036.SH",
            "instrument_name": "招商银行",
            "volume": 1000,
            "can_use_volume": 1000,
            "frozen_volume": 0,
            "on_road_volume": 0,
            "yesterday_volume": 1000,
            "open_price": 35.5,
            "avg_price": 35.2,
            "market_value": float('nan'),  # NaN
            "direction": 48
        }

        position = Position.from_dict(data)

        # 市值应该被转换为None
        assert position.market_value is None
        assert position.open_price == 35.5
        assert position.avg_price == 35.2

    def test_from_dict_with_multiple_invalid_prices(self):
        """测试from_dict处理多个无效价格值"""
        data = {
            "account_id": "300000013250",
            "account_type": 48,
            "stock_code": "600036.SH",
            "instrument_name": "招商银行",
            "volume": 1000,
            "can_use_volume": 1000,
            "frozen_volume": 0,
            "on_road_volume": 0,
            "yesterday_volume": 1000,
            "open_price": float('-inf'),  # 负无穷
            "avg_price": float('inf'),    # 正无穷
            "market_value": float('nan'),  # NaN
            "direction": 48
        }

        position = Position.from_dict(data)

        # 所有无效价格都应该被转换为None
        assert position.open_price is None
        assert position.avg_price is None
        assert position.market_value is None

    def test_from_dict_with_none_prices(self):
        """测试from_dict处理None价格值"""
        data = {
            "account_id": "300000013250",
            "account_type": 48,
            "stock_code": "600036.SH",
            "instrument_name": "招商银行",
            "volume": 1000,
            "can_use_volume": 1000,
            "frozen_volume": 0,
            "on_road_volume": 0,
            "yesterday_volume": 1000,
            "open_price": None,
            "avg_price": None,
            "market_value": None,
            "direction": 48
        }

        position = Position.from_dict(data)

        # None应该保持为None
        assert position.open_price is None
        assert position.avg_price is None
        assert position.market_value is None

    def test_from_dict_real_world_scenario(self):
        """测试真实场景：QMT API返回包含-inf的数据"""
        # 模拟真实场景的数据（如错误日志所示）
        data = {
            "account_id": "300000013250",
            "account_type": 48,
            "stock_code": "600036.SH",
            "instrument_name": "",
            "volume": 0,
            "can_use_volume": 0,
            "frozen_volume": 0,
            "on_road_volume": 0,
            "yesterday_volume": 1000,
            "open_price": float('-inf'),  # 错误日志中的值
            "avg_price": float('-inf'),   # 错误日志中的值
            "market_value": 0.0,
            "direction": 48
        }

        # 这应该不会抛出异常
        position = Position.from_dict(data)

        # 验证无效价格被清理
        assert position.open_price is None
        assert position.avg_price is None
        assert position.market_value == 0.0

        # 验证其他字段正常
        assert position.account_id == "300000013250"
        assert position.stock_code == "600036.SH"
        assert position.volume == 0
