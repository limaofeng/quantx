"""
持仓服务汇总功能单元测试
"""

import pytest
from decimal import Decimal

from models.position import Position
from services.position_service import PositionService


class TestPositionServiceSummary:
  """测试持仓服务的汇总计算功能"""

  @pytest.fixture
  def position_service(self):
    """创建持仓服务实例"""
    return PositionService()

  @pytest.fixture
  def sample_positions(self):
    """创建测试用的持仓数据"""
    positions = []

    # 持仓1：盈利
    pos1 = Position(
      id="pos1",
      account_id="300000013250",
      stock_code="600000.SH",
      instrument_name="浦发银行",
      volume=1000,
      avg_price=Decimal("10.50"),
      market_value=Decimal("11000.00")
    )

    # 持仓2：盈利
    pos2 = Position(
      id="pos2",
      account_id="300000013250",
      stock_code="000001.SZ",
      instrument_name="平安银行",
      volume=500,
      avg_price=Decimal("15.00"),
      market_value=Decimal("8000.00")
    )

    # 持仓3：亏损
    pos3 = Position(
      id="pos3",
      account_id="300000013250",
      stock_code="600036.SH",
      instrument_name="招商银行",
      volume=200,
      avg_price=Decimal("45.00"),
      market_value=Decimal("8500.00")
    )

    positions.extend([pos1, pos2, pos3])
    return positions

  @pytest.mark.asyncio
  async def test_calculate_portfolio_summary_basic(self, position_service, sample_positions):
    """测试基础汇总计算"""
    account_id = "300000013250"

    result = await position_service.calculate_portfolio_summary(account_id, sample_positions)

    # 验证基础统计
    assert result["position_count"] == 3
    assert result["total_market_value"] == 27500.00  # 11000 + 8000 + 8500

    # 验证盈亏统计
    assert result["profit_position_count"] >= 0
    assert result["loss_position_count"] >= 0
    assert result["profit_position_count"] + result["loss_position_count"] <= 3

    # 验证返回的持仓数据
    positions_with_percent = result["positions_with_percent"]
    assert len(positions_with_percent) == 3

    # 验证按市值排序（第一个应该是最大市值）
    assert positions_with_percent[0]["market_value"] >= positions_with_percent[1]["market_value"]
    assert positions_with_percent[1]["market_value"] >= positions_with_percent[2]["market_value"]

  @pytest.mark.asyncio
  async def test_calculate_portfolio_summary_market_value_percent(self, position_service, sample_positions):
    """测试市值占比计算"""
    account_id = "300000013250"

    result = await position_service.calculate_portfolio_summary(account_id, sample_positions)
    positions_with_percent = result["positions_with_percent"]

    total_percent = sum(pos["market_value_percent"] for pos in positions_with_percent)

    # 市值占比总和应该约等于100%
    assert abs(total_percent - 100.0) < 0.01

    # 每个持仓的占比应该合理
    for pos in positions_with_percent:
      assert 0 <= pos["market_value_percent"] <= 100
      expected_percent = pos["market_value"] / result["total_market_value"] * 100
      assert abs(pos["market_value_percent"] - expected_percent) < 0.01

  @pytest.mark.asyncio
  async def test_calculate_portfolio_summary_empty_positions(self, position_service):
    """测试空持仓列表"""
    account_id = "300000013250"

    result = await position_service.calculate_portfolio_summary(account_id, [])

    assert result["position_count"] == 0
    assert result["profit_position_count"] == 0
    assert result["loss_position_count"] == 0
    assert result["total_market_value"] == 0
    assert result["total_profit_loss"] == 0
    assert result["total_profit_loss_percent"] == 0
    assert len(result["positions_with_percent"]) == 0

  @pytest.mark.asyncio
  async def test_calculate_portfolio_summary_zero_market_value(self, position_service):
    """测试零市值持仓的处理"""
    # 创建零市值的持仓
    zero_position = Position(
      id="zero_pos",
      account_id="300000013250",
      stock_code="999999.SH",
      instrument_name="测试股票",
      volume=0,
      avg_price=Decimal("10.00"),
      market_value=Decimal("0.00")
    )

    result = await position_service.calculate_portfolio_summary("300000013250", [zero_position])

    # 零市值的持仓不应该包含在结果中
    assert result["position_count"] == 1
    assert len(result["positions_with_percent"]) == 0
    assert result["total_market_value"] == 0

  @pytest.mark.asyncio
  async def test_calculate_portfolio_summary_profit_loss_calculation(self, position_service):
    """测试盈亏计算逻辑"""
    # 创建一个明确的盈利持仓
    profit_position = Position(
      id="profit_pos",
      account_id="300000013250",
      stock_code="600000.SH",
      instrument_name="浦发银行",
      volume=1000,
      avg_price=Decimal("10.00"),  # 成本价10元
      market_value=Decimal("11000.00")  # 市值11000，意味着约11元
    )

    result = await position_service.calculate_portfolio_summary("300000013250", [profit_position])

    # 应该有一个盈利持仓
    assert result["position_count"] == 1
    assert result["total_profit_loss"] > 0
    assert result["total_profit_loss_percent"] > 0

    # 检查持仓数据
    positions_with_percent = result["positions_with_percent"]
    assert len(positions_with_percent) == 1

    pos_data = positions_with_percent[0]
    assert pos_data["market_value_percent"] == 100.0  # 唯一持仓，占比100%
    assert pos_data["profit_loss"] > 0  # 应该是盈利的