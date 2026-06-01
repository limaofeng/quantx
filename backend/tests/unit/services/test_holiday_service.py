"""
节假日服务测试

测试 HolidayService 的业务逻辑方法
"""

import pytest
from datetime import date
from unittest.mock import patch, AsyncMock
from services.holiday_service import HolidayService
from models.holidays import Holiday


@pytest.mark.unit
class TestHolidayService:
    """节假日服务单元测试"""

    @pytest.fixture
    def holiday_service(self):
        """创建节假日服务实例"""
        return HolidayService()

    @pytest.fixture
    def sample_holiday_data(self):
        """示例节假日数据"""
        return {
            "market": "CN",
            "year": 2023,
            "date": date(2023, 1, 1),
            "name": "元旦"
        }

    @pytest.fixture
    def sample_holiday(self, sample_holiday_data):
        """示例节假日对象"""
        holiday = Holiday()
        holiday.id = 1
        holiday.market = sample_holiday_data["market"]
        holiday.year = sample_holiday_data["year"]
        holiday.date = sample_holiday_data["date"]
        holiday.name = sample_holiday_data["name"]
        return holiday

    @pytest.mark.asyncio
    async def test_get_holidays_success(self, holiday_service, sample_holiday):
        """测试成功获取节假日列表"""
        with patch('database.connection.get_async_db') as mock_db:
            mock_session = AsyncMock()
            mock_db.return_value.__aenter__.return_value = mock_session

            with patch('repositories.holiday_repository.HolidayRepository') as mock_repo_class:
                mock_repo = AsyncMock()
                mock_repo.find_all_by_market_and_year.return_value = [sample_holiday]
                mock_repo_class.return_value = mock_repo

                result = await holiday_service.get_holidays("CN", 2023)

                assert len(result) == 1
                assert result[0] == sample_holiday
                mock_repo.find_all_by_market_and_year.assert_called_once_with("CN", 2023)

    @pytest.mark.asyncio
    async def test_get_holidays_empty(self, holiday_service):
        """测试获取空节假日列表"""
        with patch('database.connection.get_async_db') as mock_db:
            mock_session = AsyncMock()
            mock_db.return_value.__aenter__.return_value = mock_session

            with patch('repositories.holiday_repository.HolidayRepository') as mock_repo_class:
                mock_repo = AsyncMock()
                mock_repo.find_all_by_market_and_year.return_value = []
                mock_repo_class.return_value = mock_repo

                result = await holiday_service.get_holidays("CN", 2023)

                assert len(result) == 0
                mock_repo.find_all_by_market_and_year.assert_called_once_with("CN", 2023)

    @pytest.mark.asyncio
    async def test_is_holiday_true(self, holiday_service):
        """测试检查是否为节假日 - 是节假日"""
        with patch('database.connection.get_async_db') as mock_db:
            mock_session = AsyncMock()
            mock_db.return_value.__aenter__.return_value = mock_session

            with patch('repositories.holiday_repository.HolidayRepository') as mock_repo_class:
                mock_repo = AsyncMock()
                mock_repo.exists_by_market_and_date.return_value = True
                mock_repo_class.return_value = mock_repo

                result = await holiday_service.is_holiday("CN", date(2023, 1, 1))

                assert result is True
                mock_repo.exists_by_market_and_date.assert_called_once_with("CN", date(2023, 1, 1))

    @pytest.mark.asyncio
    async def test_is_holiday_false(self, holiday_service):
        """测试检查是否为节假日 - 不是节假日"""
        with patch('database.connection.get_async_db') as mock_db:
            mock_session = AsyncMock()
            mock_db.return_value.__aenter__.return_value = mock_session

            with patch('repositories.holiday_repository.HolidayRepository') as mock_repo_class:
                mock_repo = AsyncMock()
                mock_repo.exists_by_market_and_date.return_value = False
                mock_repo_class.return_value = mock_repo

                result = await holiday_service.is_holiday("CN", date(2023, 1, 2))

                assert result is False
                mock_repo.exists_by_market_and_date.assert_called_once_with("CN", date(2023, 1, 2))

    @pytest.mark.asyncio
    async def test_bulk_save_holidays_success(self, holiday_service, sample_holiday_data):
        """测试批量保存节假日成功"""
        with patch('database.connection.get_async_db') as mock_db:
            mock_session = AsyncMock()
            mock_db.return_value.__aenter__.return_value = mock_session

            with patch('repositories.holiday_repository.HolidayRepository') as mock_repo_class:
                mock_repo = AsyncMock()
                mock_repo.delete_by_market_and_year.return_value = 5  # 删除了5条记录
                mock_repo.create.side_effect = [sample_holiday_data, sample_holiday_data]
                mock_repo_class.return_value = mock_repo

                holidays_input = [
                    {"date": date(2023, 1, 1), "name": "元旦"},
                    {"date": date(2023, 2, 14), "name": "春节"}
                ]

                result = await holiday_service.bulk_save_holidays("CN", 2023, holidays_input)

                assert len(result) == 2
                assert result[0] == sample_holiday_data
                assert result[1] == sample_holiday_data

                # 验证先删除了旧数据
                mock_repo.delete_by_market_and_year.assert_called_once_with("CN", 2023)
                # 验证创建了新数据
                assert mock_repo.create.call_count == 2

    @pytest.mark.asyncio
    async def test_bulk_save_holidays_empty_list(self, holiday_service):
        """测试批量保存空节假日列表"""
        with patch('database.connection.get_async_db') as mock_db:
            mock_session = AsyncMock()
            mock_db.return_value.__aenter__.return_value = mock_session

            with patch('repositories.holiday_repository.HolidayRepository') as mock_repo_class:
                mock_repo = AsyncMock()
                mock_repo.delete_by_market_and_year.return_value = 3
                mock_repo_class.return_value = mock_repo

                result = await holiday_service.bulk_save_holidays("CN", 2023, [])

                assert len(result) == 0
                mock_repo.delete_by_market_and_year.assert_called_once_with("CN", 2023)
                mock_repo.create.assert_not_called()

@pytest.mark.integration
class TestHolidayServiceIntegration:

    @pytest.fixture
    def holiday_service(self):
        """创建节假日服务实例"""
        return HolidayService()

    """
    节假日服务集成测试 - 完全真实的流程测试

    这个测试类直接测试完整的节假日服务流程，
    不使用任何 mock，完全依赖真实的业务逻辑
    """
    @pytest.mark.asyncio
    async def test_bulk_save_holidays_integration(self, holiday_service: HolidayService):
        """批量保存2025节假日"""
        holidays_input = [
            {"date": date(2025, 1, 1), "description": "元旦"},
            {"date": date(2025, 1, 28), "description": "春节(八天)"},
            {"date": date(2025, 1, 29), "description": "春节(八天)"},
            {"date": date(2025, 1, 30), "description": "春节(八天)"},
            {"date": date(2025, 1, 31), "description": "春节(八天)"},
            {"date": date(2025, 2, 1), "description": "春节(八天)"},
            {"date": date(2025, 2, 2), "description": "春节(八天)"},
            {"date": date(2025, 2, 3), "description": "春节(八天)"},
            {"date": date(2025, 2, 4), "description": "春节(八天)"},
            {"date": date(2025, 4, 4), "description": "清明节(三天)"},
            {"date": date(2025, 4, 5), "description": "清明节(三天)"},
            {"date": date(2025, 4, 6), "description": "清明节(三天)"},
            {"date": date(2025, 5, 1), "description": "劳动节(五天)"},
            {"date": date(2025, 5, 2), "description": "劳动节(五天)"},
            {"date": date(2025, 5, 3), "description": "劳动节(五天)"},
            {"date": date(2025, 5, 4), "description": "劳动节(五天)"},
            {"date": date(2025, 5, 5), "description": "劳动节(五天)"},
            {"date": date(2025, 5, 31), "description": "端午节(三天)"},
            {"date": date(2025, 6, 1), "description": "端午节(三天)"},
            {"date": date(2025, 6, 2), "description": "端午节(三天)"},
            {"date": date(2025, 10, 1), "description": "国庆节(八天)"},
            {"date": date(2025, 10, 2), "description": "国庆节(八天)"},
            {"date": date(2025, 10, 3), "description": "国庆节(八天)"},
            {"date": date(2025, 10, 4), "description": "国庆节(八天)"},
            {"date": date(2025, 10, 5), "description": "国庆节(八天)"},
            {"date": date(2025, 10, 6), "description": "国庆节(八天)"},
            {"date": date(2025, 10, 7), "description": "国庆节(八天)"},
            {"date": date(2025, 10, 8), "description": "国庆节(八天)"},
        ]

        result = await holiday_service.bulk_save_holidays("SH", 2025, holidays_input)

        assert len(result) == len(holidays_input)
        assert result[0].market == "SH"
        assert result[0].year == 2025
        assert result[0].date == date(2025, 1, 1)
        assert result[0].description == "元旦"
