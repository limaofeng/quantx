"""
TradingTimeService 单元测试
"""

import asyncio
import pytest
from datetime import date, datetime, time
from unittest.mock import AsyncMock, MagicMock, patch

from services.trading_time_service import TradingTimeService


class TestTradingTimeService:
    """TradingTimeService 测试类"""

    @pytest.fixture
    def trading_time_service(self):
        """创建 TradingTimeService 实例"""
        return TradingTimeService()

    @pytest.fixture
    def mock_holiday_service(self):
        """模拟 HolidayService"""
        mock = AsyncMock()
        return mock

    @pytest.mark.asyncio
    async def test_is_trading_day_weekday_not_holiday(self, trading_time_service):
        """测试工作日非节假日的情况"""
        # 模拟 HolidayService
        with patch.object(trading_time_service.holiday_service, 'is_holiday', return_value=False):
            # 2024-01-02 是周二
            test_date = date(2024, 1, 2)
            result = await trading_time_service.is_trading_day("SH", test_date)
            assert result is True

    @pytest.mark.asyncio
    async def test_is_trading_day_weekend(self, trading_time_service):
        """测试周末的情况"""
        # 2024-01-06 是周六
        test_date = date(2024, 1, 6)
        result = await trading_time_service.is_trading_day("SH", test_date)
        assert result is False

        # 2024-01-07 是周日
        test_date = date(2024, 1, 7)
        result = await trading_time_service.is_trading_day("SH", test_date)
        assert result is False

    @pytest.mark.asyncio
    async def test_is_trading_day_holiday(self, trading_time_service):
        """测试节假日的情况"""
        # 模拟 HolidayService 返回节假日
        with patch.object(trading_time_service.holiday_service, 'is_holiday', return_value=True):
            # 2024-01-01 是周一但是节假日
            test_date = date(2024, 1, 1)
            result = await trading_time_service.is_trading_day("SH", test_date)
            assert result is False

    @pytest.mark.asyncio
    async def test_is_trading_hours_trading_day_trading_time(self, trading_time_service):
        """测试交易日交易时间的情况"""
        # 模拟是交易日
        with patch.object(trading_time_service, 'is_trading_day', return_value=True):
            # 2024-01-02 10:00 (上午交易时间)
            test_datetime = datetime(2024, 1, 2, 10, 0)
            result = await trading_time_service.is_trading_hours("SH", test_datetime)
            assert result is True

            # 2024-01-02 14:00 (下午交易时间)
            test_datetime = datetime(2024, 1, 2, 14, 0)
            result = await trading_time_service.is_trading_hours("SH", test_datetime)
            assert result is True

    @pytest.mark.asyncio
    async def test_is_trading_hours_trading_day_non_trading_time(self, trading_time_service):
        """测试交易日非交易时间的情况"""
        # 模拟是交易日
        with patch.object(trading_time_service, 'is_trading_day', return_value=True):
            # 2024-01-02 08:00 (开盘前)
            test_datetime = datetime(2024, 1, 2, 8, 0)
            result = await trading_time_service.is_trading_hours("SH", test_datetime)
            assert result is False

            # 2024-01-02 12:00 (午休时间)
            test_datetime = datetime(2024, 1, 2, 12, 0)
            result = await trading_time_service.is_trading_hours("SH", test_datetime)
            assert result is False

            # 2024-01-02 16:00 (收盘后)
            test_datetime = datetime(2024, 1, 2, 16, 0)
            result = await trading_time_service.is_trading_hours("SH", test_datetime)
            assert result is False

    @pytest.mark.asyncio
    async def test_is_trading_hours_non_trading_day(self, trading_time_service):
        """测试非交易日的情况"""
        # 模拟是非交易日
        with patch.object(trading_time_service, 'is_trading_day', return_value=False):
            # 即使在交易时间段，非交易日也不是交易时间
            test_datetime = datetime(2024, 1, 6, 10, 0)  # 周六10:00
            result = await trading_time_service.is_trading_hours("SH", test_datetime)
            assert result is False

    def test_get_trading_hours(self, trading_time_service):
        """测试获取交易时间段"""
        # 测试上海市场
        sh_hours = trading_time_service.get_trading_hours("SH")
        expected_hours = [
            (time(9, 30), time(11, 30)),
            (time(13, 0), time(15, 0))
        ]
        assert sh_hours == expected_hours

        # 测试深圳市场
        sz_hours = trading_time_service.get_trading_hours("SZ")
        assert sz_hours == expected_hours

        # 测试未知市场（使用默认配置）
        unknown_hours = trading_time_service.get_trading_hours("UNKNOWN")
        assert unknown_hours == expected_hours

    @pytest.mark.asyncio
    async def test_get_next_trading_day(self, trading_time_service):
        """测试获取下一个交易日"""
        # 模拟 is_trading_day 的返回值
        # 假设 2024-01-02 (周二) 是交易日，2024-01-01 (周一) 不是
        def mock_is_trading_day(market, check_date):
            if check_date == date(2024, 1, 2):
                return True
            return False

        with patch.object(trading_time_service, 'is_trading_day', side_effect=mock_is_trading_day):
            # 从 2024-01-01 开始找下一个交易日
            from_date = date(2024, 1, 1)
            next_trading_day = await trading_time_service.get_next_trading_day("SH", from_date)
            assert next_trading_day == date(2024, 1, 2)

    @pytest.mark.asyncio
    async def test_get_previous_trading_day(self, trading_time_service):
        """测试获取上一个交易日"""
        # 模拟 is_trading_day 的返回值
        def mock_is_trading_day(market, check_date):
            if check_date == date(2024, 1, 1):
                return True
            return False

        with patch.object(trading_time_service, 'is_trading_day', side_effect=mock_is_trading_day):
            # 从 2024-01-02 开始找上一个交易日
            from_date = date(2024, 1, 2)
            prev_trading_day = await trading_time_service.get_previous_trading_day("SH", from_date)
            assert prev_trading_day == date(2024, 1, 1)

    @pytest.mark.asyncio
    async def test_cache_functionality(self, trading_time_service):
        """测试缓存功能"""
        # 模拟 HolidayService 返回一致的结果
        with patch.object(trading_time_service.holiday_service, 'is_holiday', return_value=False):
            test_date = date(2024, 1, 2)  # 周二

            # 第一次调用，会进行实际查询并缓存
            result1 = await trading_time_service.is_trading_day("SH", test_date)
            assert result1 is True

            # 验证结果已缓存
            cache_key = f"SH_{test_date}"
            assert cache_key in trading_time_service._trading_day_cache
            assert trading_time_service._trading_day_cache[cache_key] is True

            # 第二次调用，应该直接从缓存返回
            result2 = await trading_time_service.is_trading_day("SH", test_date)
            assert result2 is True

    @pytest.mark.asyncio
    async def test_auto_cache_cleanup(self, trading_time_service):
        """测试自动缓存清理功能"""
        with patch.object(trading_time_service.holiday_service, 'is_holiday', return_value=False):
            # 模拟今天的日期
            today = date(2024, 1, 15)

            with patch('services.trading_time_service.date') as mock_date:
                mock_date.today.return_value = today
                mock_date.fromisoformat = date.fromisoformat

                # 手动添加一些过期的缓存数据
                old_date = date(2024, 1, 1)  # 14天前，超过7天保留期
                future_date = date(2024, 2, 1)  # 17天后，超过7天保留期
                recent_date = date(2024, 1, 12)  # 3天前，在保留期内

                trading_time_service._trading_day_cache = {
                    f"SH_{old_date}": True,
                    f"SH_{future_date}": True,
                    f"SH_{recent_date}": True,
                    f"SH_{today}": True
                }

                # 调用 is_trading_day 触发自动清理
                await trading_time_service.is_trading_day("SH", today)

                # 验证过期数据被清理，保留期内数据保留
                assert f"SH_{old_date}" not in trading_time_service._trading_day_cache
                assert f"SH_{future_date}" not in trading_time_service._trading_day_cache
                assert f"SH_{recent_date}" in trading_time_service._trading_day_cache
                assert f"SH_{today}" in trading_time_service._trading_day_cache

    def test_get_cache_info(self, trading_time_service):
        """测试获取缓存信息"""
        # 添加一些测试数据
        trading_time_service._trading_day_cache = {
            "SH_2024-01-01": True,
            "SZ_2024-01-02": False
        }

        cache_info = trading_time_service.get_cache_info()

        assert cache_info["cache_size"] == 2
        assert "SH_2024-01-01" in cache_info["cache_keys"]
        assert "SZ_2024-01-02" in cache_info["cache_keys"]
        assert cache_info["retention_days"] == 7

    def test_set_trading_hours(self, trading_time_service):
        """测试设置交易时间"""
        custom_hours = [
            (time(9, 0), time(12, 0)),
            (time(13, 30), time(16, 0))
        ]

        # 设置自定义交易时间
        trading_time_service.set_trading_hours("CUSTOM", custom_hours)

        # 验证设置是否生效
        result_hours = trading_time_service.get_trading_hours("CUSTOM")
        assert result_hours == custom_hours

    @pytest.mark.asyncio
    async def test_error_handling_get_next_trading_day(self, trading_time_service):
        """测试获取下一个交易日的错误处理"""
        # 模拟所有日期都不是交易日
        with patch.object(trading_time_service, 'is_trading_day', return_value=False):
            from_date = date(2024, 1, 1)

            # 应该抛出 ValueError
            with pytest.raises(ValueError, match="未能在30天内找到下一个交易日"):
                await trading_time_service.get_next_trading_day("SH", from_date)

    @pytest.mark.asyncio
    async def test_error_handling_get_previous_trading_day(self, trading_time_service):
        """测试获取上一个交易日的错误处理"""
        # 模拟所有日期都不是交易日
        with patch.object(trading_time_service, 'is_trading_day', return_value=False):
            from_date = date(2024, 1, 1)

            # 应该抛出 ValueError
            with pytest.raises(ValueError, match="未能在30天内找到上一个交易日"):
                await trading_time_service.get_previous_trading_day("SH", from_date)

    @pytest.mark.asyncio
    async def test_default_parameters(self, trading_time_service):
        """测试默认参数"""
        # 测试不传入日期时使用今天
        with patch.object(trading_time_service.holiday_service, 'is_holiday', return_value=False):
            with patch('services.trading_time_service.date') as mock_date:
                mock_date.today.return_value = date(2024, 1, 2)  # 周二
                mock_date.return_value = date(2024, 1, 2)

                result = await trading_time_service.is_trading_day("SH")
                assert result is True

        # 测试不传入时间时使用当前时间
        with patch.object(trading_time_service, 'is_trading_day', return_value=True):
            with patch('services.trading_time_service.datetime') as mock_datetime:
                mock_datetime.now.return_value = datetime(2024, 1, 2, 10, 0)

                result = await trading_time_service.is_trading_hours("SH")
                assert result is True