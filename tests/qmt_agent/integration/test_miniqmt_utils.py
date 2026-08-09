"""
MiniQMT 工具函数测试
"""
from datetime import datetime

import numpy as np
import pandas as pd
import pytest


def test_normalize_stock_code():
    """测试股票代码标准化"""
    from quantx_qmt_agent.miniqmt.utils.helpers import normalize_stock_code

    # 测试深圳股票代码
    assert normalize_stock_code('000001') == '000001.SZ'
    assert normalize_stock_code('002415') == '002415.SZ'
    assert normalize_stock_code('300750') == '300750.SZ'

    # 测试上海股票代码
    assert normalize_stock_code('600036') == '600036.SH'
    assert normalize_stock_code('601318') == '601318.SH'
    assert normalize_stock_code('688981') == '688981.SH'

    # 测试北京股票代码
    assert normalize_stock_code('430047') == '430047.BJ'
    assert normalize_stock_code('831865') == '831865.BJ'

    # 测试已包含后缀的代码
    assert normalize_stock_code('000001.SZ') == '000001.SZ'
    assert normalize_stock_code('600036.SH') == '600036.SH'

    # 测试去除空格和转换大小写
    assert normalize_stock_code(' 000001 ') == '000001.SZ'
    assert normalize_stock_code('000001.sz') == '000001.SZ'

def test_batch_normalize_stock_codes():
    """测试批量股票代码标准化"""
    from quantx_qmt_agent.miniqmt.utils.helpers import batch_normalize_stock_codes

    codes = ['000001', '600036', '300750', '000001.SZ']
    expected = ['000001.SZ', '600036.SH', '300750.SZ', '000001.SZ']

    result = batch_normalize_stock_codes(codes)
    assert result == expected

def test_format_timestamp():
    """测试时间戳格式化"""
    from quantx_qmt_agent.miniqmt.utils.helpers import format_timestamp

    # 测试字符串输入
    assert format_timestamp('2023-01-01 09:30:00') == '2023-01-01 09:30:00'

    # 测试 datetime 对象
    dt = datetime(2023, 1, 1, 9, 30, 0)
    assert format_timestamp(dt) == '2023-01-01 09:30:00'

    # 测试秒级时间戳
    timestamp_sec = 1672574200  # 2023-01-01 09:30:00
    result = format_timestamp(timestamp_sec)
    assert '2023-01-01' in result

    # 测试毫秒级时间戳
    timestamp_ms = 1672574200000
    result = format_timestamp(timestamp_ms)
    assert '2023-01-01' in result

def test_calculate_trading_days():
    """测试交易日计算"""
    from quantx_qmt_agent.miniqmt.utils.helpers import calculate_trading_days

    # 测试一周的交易日
    start_date = '2023-01-02'  # 周一
    end_date = '2023-01-06'    # 周五

    days = calculate_trading_days(start_date, end_date)
    assert isinstance(days, int)
    assert days >= 0

def test_get_trading_calendar():
    """测试获取交易日历"""
    from quantx_qmt_agent.miniqmt.utils.helpers import get_trading_calendar

    # 实际函数只接受年份参数
    calendar = get_trading_calendar(2023)
    assert isinstance(calendar, list)
    # 每个元素应该是日期字符串
    for date in calendar[:5]:  # 检查前5个
        assert isinstance(date, str)
        assert len(date) == 10  # YYYY-MM-DD 格式

def test_calculate_returns():
    """测试收益率计算"""
    from quantx_qmt_agent.miniqmt.utils.helpers import calculate_returns

    # 创建测试价格数据
    prices = pd.Series([100, 105, 110, 108, 115])

    returns = calculate_returns(prices)

    assert isinstance(returns, pd.Series)
    assert len(returns) == len(prices)  # 收益率和价格长度相同，第一个是NaN

    # 验证第一个收益率是NaN
    assert pd.isna(returns.iloc[0])
    # 验证第二个收益率
    expected_second_return = (105 - 100) / 100
    assert abs(returns.iloc[1] - expected_second_return) < 1e-6

def test_calculate_cumulative_returns():
    """测试累计收益率计算"""
    from quantx_qmt_agent.miniqmt.utils.helpers import calculate_cumulative_returns

    returns = pd.Series([0.05, 0.03, -0.02, 0.04])

    cum_returns = calculate_cumulative_returns(returns)

    assert isinstance(cum_returns, pd.Series)
    assert len(cum_returns) == len(returns)

    # 验证累计收益率计算
    expected_last = (1.05 * 1.03 * 0.98 * 1.04) - 1
    assert abs(cum_returns.iloc[-1] - expected_last) < 1e-6

def test_calculate_max_drawdown():
    """测试最大回撤计算"""
    from quantx_qmt_agent.miniqmt.utils.helpers import (
        calculate_max_drawdown,
        calculate_returns,
    )

    # 创建有明显回撤的价格序列，然后计算收益率
    prices = pd.Series([100, 120, 110, 90, 95, 130])
    returns = calculate_returns(prices)

    max_dd = calculate_max_drawdown(returns)

    assert isinstance(max_dd, float)
    assert max_dd <= 0  # 最大回撤应该是负数或0
    assert max_dd >= -1  # 最大回撤不应超过-100%

def test_calculate_sharpe_ratio():
    """测试夏普比率计算"""
    from quantx_qmt_agent.miniqmt.utils.helpers import calculate_sharpe_ratio

    # 创建测试收益率数据
    returns = pd.Series([0.01, 0.02, -0.01, 0.015, 0.005, 0.02])
    risk_free_rate = 0.03  # 年化无风险利率

    sharpe = calculate_sharpe_ratio(returns, risk_free_rate)

    assert isinstance(sharpe, float)
    # 夏普比率可能为负值，但应该是有限数值
    assert not pd.isna(sharpe)

def test_calculate_volatility():
    """测试波动率计算"""
    from quantx_qmt_agent.miniqmt.utils.helpers import calculate_volatility

    returns = pd.Series([0.01, 0.02, -0.01, 0.015, 0.005, 0.02])

    # 测试年化波动率（函数只支持年化）
    annual_vol = calculate_volatility(returns)
    assert isinstance(annual_vol, float)
    assert annual_vol >= 0

def test_resample_data():
    """测试数据重采样"""
    from quantx_qmt_agent.miniqmt.utils.helpers import resample_data

    # 创建测试数据
    dates = pd.date_range('2023-01-01', periods=30, freq='D')
    data = pd.DataFrame({
        'close': np.random.randn(30) + 100,
        'volume': np.random.randint(1000, 10000, 30)
    }, index=dates)

    # 测试周重采样
    weekly_data = resample_data(data, freq='W')
    assert isinstance(weekly_data, pd.DataFrame)
    assert len(weekly_data) < len(data)  # 重采样后数据点应该减少

def test_validate_stock_code():
    """测试股票代码验证"""
    from quantx_qmt_agent.miniqmt.utils.helpers import validate_stock_code

    # 测试有效代码
    assert validate_stock_code('000001.SZ')
    assert validate_stock_code('600036.SH')
    assert validate_stock_code('300750.SZ')

    # 测试无效代码
    assert not validate_stock_code('invalid')
    assert not validate_stock_code('')
    assert validate_stock_code('000001')  # 会被标准化为 000001.SZ

def test_batch_validate_stock_codes():
    """测试批量股票代码验证"""
    from quantx_qmt_agent.miniqmt.utils.helpers import batch_validate_stock_codes

    codes = ['000001.SZ', 'invalid', '600036.SH', '']
    results = batch_validate_stock_codes(codes)

    assert isinstance(results, dict)  # 函数返回字典
    assert len(results) == len(codes)
    assert results['000001.SZ']   # 000001.SZ 有效
    assert not results['invalid']    # invalid 无效
    assert results['600036.SH']   # 600036.SH 有效
    assert not results['']           # 空字符串无效

def test_format_money():
    """测试金额格式化"""
    from quantx_qmt_agent.miniqmt.utils.helpers import format_money

    # 测试正常金额
    assert format_money(1234.56) == '¥1234.56'
    assert format_money(50000) == '¥5.00万'
    assert format_money(150000000) == '¥1.50亿'

    # 测试不同货币符号
    assert format_money(1234.56, currency='$') == '$1234.56'

def test_retry_on_exception():
    """测试异常重试装饰器"""
    from quantx_qmt_agent.miniqmt.utils.helpers import retry_on_exception

    # 创建一个会失败几次然后成功的函数
    call_count = {'count': 0}

    @retry_on_exception(max_retries=3, delay=0.01)
    def flaky_function():
        call_count['count'] += 1
        if call_count['count'] < 3:
            raise ValueError("Temporary error")
        return "success"

    result = flaky_function()
    assert result == "success"
    assert call_count['count'] == 3

def test_retry_on_exception_max_retries():
    """测试重试装饰器达到最大重试次数"""
    from quantx_qmt_agent.miniqmt.utils.helpers import retry_on_exception

    @retry_on_exception(max_retries=2, delay=0.01)
    def always_failing_function():
        raise ValueError("Always fails")

    with pytest.raises(ValueError):
        always_failing_function()

class TestDataValidator:
    """数据验证器测试类"""

    def test_data_validator_initialization(self):
        """测试数据验证器初始化"""
        from quantx_qmt_agent.miniqmt.utils.helpers import DataValidator

        validator = DataValidator()
        assert validator is not None

    def test_validate_price_data(self):
        """测试价格数据验证"""
        from quantx_qmt_agent.miniqmt.utils.helpers import DataValidator

        validator = DataValidator()

        # 测试有效价格数据
        valid_data = pd.DataFrame({
            'open': [100, 101, 102],
            'high': [105, 106, 107],
            'low': [98, 99, 100],
            'close': [103, 104, 105],
            'volume': [1000, 1100, 1200]
        })

        assert validator.validate_ohlcv_data(valid_data)

        # 测试无效价格数据（high < low）
        invalid_data = pd.DataFrame({
            'open': [100, 101, 102],
            'high': [95, 96, 97],  # high < low
            'low': [98, 99, 100],
            'close': [103, 104, 105],
            'volume': [1000, 1100, 1200]
        })

        assert not validator.validate_ohlcv_data(invalid_data)

    def test_validate_trade_data(self):
        """测试交易数据验证"""
        from quantx_qmt_agent.miniqmt.utils.helpers import DataValidator

        validator = DataValidator()

        # 测试数据清理功能
        messy_data = pd.DataFrame({
            'open': [100, 101, 102],
            'high': [95, 106, 107],  # 第一行high < open，会被修正
            'low': [98, 99, 100],
            'close': [103, 104, 105],
            'volume': [1000, -100, 1200]  # 第二行volume为负，会被修正
        })

        cleaned_data = validator.clean_ohlcv_data(messy_data)
        assert validator.validate_ohlcv_data(cleaned_data)

class TestUtilsIntegration:
    """工具函数集成测试"""

    def test_stock_code_processing_pipeline(self):
        """测试股票代码处理流水线"""
        from quantx_qmt_agent.miniqmt.utils.helpers import (
            batch_normalize_stock_codes,
            batch_validate_stock_codes,
        )

        # 原始代码列表
        raw_codes = ['000001', '600036', 'invalid', '300750']

        # 标准化
        normalized = batch_normalize_stock_codes(raw_codes)

        # 验证
        validation_results = batch_validate_stock_codes(normalized)

        # 过滤有效代码 - 因为函数返回字典
        valid_codes = [code for code, valid in validation_results.items() if valid]

        assert len(valid_codes) == 3  # 应该有3个有效代码
        assert 'INVALID.SZ' not in valid_codes  # INVALID 不是有效的6位数字代码

    def test_financial_calculations_workflow(self):
        """测试金融计算工作流"""
        from quantx_qmt_agent.miniqmt.utils.helpers import (
            calculate_cumulative_returns,
            calculate_max_drawdown,
            calculate_returns,
            calculate_sharpe_ratio,
            calculate_volatility,
        )

        # 创建模拟价格数据
        np.random.seed(42)  # 确保可重现性
        prices = pd.Series(100 * (1 + np.random.randn(252) * 0.02).cumprod())

        # 计算收益率
        returns = calculate_returns(prices)

        # 计算累计收益率
        cum_returns = calculate_cumulative_returns(returns)

        # 计算最大回撤 - 函数只返回一个值
        max_dd = calculate_max_drawdown(returns)

        # 计算夏普比率
        sharpe = calculate_sharpe_ratio(returns, risk_free_rate=0.02)

        # 计算波动率 - 函数不接受period参数
        volatility = calculate_volatility(returns)

        # 验证所有指标都是有效数值
        assert not pd.isna(cum_returns.iloc[-1])
        assert max_dd <= 0  # 最大回撤应该是负数或0
        assert not pd.isna(sharpe)
        assert volatility > 0
