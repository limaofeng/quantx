"""
清仓服务单元测试
"""

from unittest.mock import MagicMock, patch

import pytest

from models.position import Position
from services.liquidation_service import (
  LiquidationService,
)


class TestLiquidationService:
  """清仓服务测试类"""

  @pytest.fixture
  def liquidation_service(self):
    """创建清仓服务实例"""
    return LiquidationService()

  @pytest.fixture
  def mock_position(self):
    """模拟持仓数据"""
    position = MagicMock(spec=Position)
    position.stock_code = "000001.SZ"
    position.instrument_name = "平安银行"
    position.volume = 1000
    position.can_use_volume = 1000
    position.market_value = 15000.0
    position.avg_price = 15.0
    return position

  @pytest.fixture
  def mock_zero_position(self):
    """模拟零持仓数据"""
    position = MagicMock(spec=Position)
    position.stock_code = "000002.SZ"
    position.instrument_name = "万科A"
    position.volume = 1000
    position.can_use_volume = 0  # 不可用数量为0
    position.market_value = 20000.0
    position.avg_price = 20.0
    return position

  @pytest.mark.asyncio
  async def test_get_liquidation_summary(self, liquidation_service, mock_position):
    """测试获取清仓概况"""
    with patch.object(
      liquidation_service.position_service, "get_positions"
    ) as mock_get_positions:
      mock_get_positions.return_value = [mock_position]

      result = await liquidation_service.get_liquidation_summary()

      assert result["total_positions"] == 1
      assert result["liquidatable_positions"] == 1
      assert result["total_market_value"] == 15000.0
      assert len(result["positions"]) == 1
      assert result["positions"][0]["stock_code"] == "000001.SZ"

  @pytest.mark.asyncio
  async def test_liquidate_all_positions_without_confirmation(
    self, liquidation_service
  ):
    """测试未确认风险的一键清仓"""
    result = await liquidation_service.liquidate_all_positions(confirm=False)

    assert not result.success
    assert "必须确认风险" in result.message

  @pytest.mark.asyncio
  async def test_liquidate_all_positions_no_liquidatable_positions(
    self, liquidation_service
  ):
    """测试没有可清仓持仓的一键清仓"""
    with patch.object(
      liquidation_service, "_get_liquidatable_positions"
    ) as mock_get_liquidatable:
      mock_get_liquidatable.return_value = []

      result = await liquidation_service.liquidate_all_positions(confirm=True)

      assert result.success
      assert result.total_positions == 0
      assert "没有可清仓的持仓" in result.message

  @pytest.mark.asyncio
  async def test_liquidate_all_positions_success(
    self, liquidation_service, mock_position
  ):
    """测试成功的一键清仓"""
    with (
      patch.object(
        liquidation_service, "_get_liquidatable_positions"
      ) as mock_get_liquidatable,
      patch.object(
        liquidation_service, "_liquidate_single_position"
      ) as mock_liquidate_single,
    ):
      mock_get_liquidatable.return_value = [mock_position]
      mock_liquidate_single.return_value = {
        "success": True,
        "stock_code": "000001.SZ",
        "volume": 1000,
        "order_id": "12345",
        "orders": [{"order_id": "12345"}],
        "message": "清仓成功",
      }

      result = await liquidation_service.liquidate_all_positions(confirm=True)

      assert result.success
      assert result.total_positions == 1
      assert result.liquidated_positions == 1
      assert result.failed_positions == 0
      assert "成功1个" in result.message

  @pytest.mark.asyncio
  async def test_liquidate_position_without_confirmation(self, liquidation_service):
    """测试未确认风险的个股清仓"""
    result = await liquidation_service.liquidate_position(
      stock_code="000001.SZ", confirm=False
    )

    assert not result["success"]
    assert "必须确认风险" in result["message"]

  @pytest.mark.asyncio
  async def test_liquidate_position_not_found(self, liquidation_service):
    """测试不存在持仓的个股清仓"""
    with patch.object(liquidation_service, "_get_position") as mock_get_position:
      mock_get_position.return_value = None

      result = await liquidation_service.liquidate_position(
        stock_code="000001.SZ", confirm=True
      )

      assert not result["success"]
      assert "未找到股票" in result["message"]

  @pytest.mark.asyncio
  async def test_liquidate_position_insufficient_volume(
    self, liquidation_service, mock_zero_position
  ):
    """测试可清仓数量不足的个股清仓"""
    with patch.object(liquidation_service, "_get_position") as mock_get_position:
      mock_get_position.return_value = mock_zero_position

      result = await liquidation_service.liquidate_position(
        stock_code="000002.SZ", confirm=True
      )

      assert not result["success"]
      assert "没有可清仓的持仓数量" in result["message"]

  @pytest.mark.asyncio
  async def test_liquidate_position_success(self, liquidation_service, mock_position):
    """测试成功的个股清仓"""
    with (
      patch.object(liquidation_service, "_get_position") as mock_get_position,
      patch.object(
        liquidation_service, "_liquidate_single_position"
      ) as mock_liquidate_single,
    ):
      mock_get_position.return_value = mock_position
      mock_liquidate_single.return_value = {
        "success": True,
        "stock_code": "000001.SZ",
        "volume": 1000,
        "order_id": "12345",
        "message": "清仓成功",
      }

      result = await liquidation_service.liquidate_position(
        stock_code="000001.SZ", confirm=True
      )

      assert result["success"]
      assert result["stock_code"] == "000001.SZ"
      assert result["volume"] == 1000

  @pytest.mark.asyncio
  async def test_redeem_cleared_position_still_has_position(
    self, liquidation_service, mock_position
  ):
    """测试仍有持仓的资金赎回"""
    with patch.object(liquidation_service, "_get_position") as mock_get_position:
      mock_get_position.return_value = mock_position

      result = await liquidation_service.redeem_cleared_position(stock_code="000001.SZ")

      assert not result["success"]
      assert "仍有持仓" in result["message"]

  @pytest.mark.asyncio
  async def test_redeem_cleared_position_no_redeemable_amount(
    self, liquidation_service
  ):
    """测试没有可赎回金额的资金赎回"""
    with (
      patch.object(liquidation_service, "_get_position") as mock_get_position,
      patch.object(
        liquidation_service, "_calculate_redeemable_amount"
      ) as mock_calculate_amount,
    ):
      mock_get_position.return_value = None  # 没有持仓
      mock_calculate_amount.return_value = 0.0  # 没有可赎回金额

      result = await liquidation_service.redeem_cleared_position(stock_code="000001.SZ")

      assert not result["success"]
      assert "没有可赎回的资金" in result["message"]

  @pytest.mark.asyncio
  async def test_redeem_cleared_position_success(self, liquidation_service):
    """测试成功的资金赎回"""
    with (
      patch.object(liquidation_service, "_get_position") as mock_get_position,
      patch.object(
        liquidation_service, "_calculate_redeemable_amount"
      ) as mock_calculate_amount,
    ):
      mock_get_position.return_value = None  # 没有持仓
      mock_calculate_amount.return_value = 15000.0  # 有可赎回金额

      result = await liquidation_service.redeem_cleared_position(
        stock_code="000001.SZ", amount=10000.0
      )

      assert result["success"]
      assert result["stock_code"] == "000001.SZ"
      assert result["redeemed_amount"] == 10000.0
      assert result["remaining_amount"] == 5000.0

  @pytest.mark.asyncio
  async def test_liquidate_single_position_success(
    self, liquidation_service, mock_position
  ):
    """测试单个持仓清仓成功"""
    with patch.object(
      liquidation_service.trading_service, "place_order"
    ) as mock_place_order:
      mock_place_order.return_value = {
        "success": True,
        "order_id": "12345",
        "message": "下单成功",
      }

      result = await liquidation_service._liquidate_single_position(mock_position)

      assert result["success"]
      assert result["stock_code"] == "000001.SZ"
      assert result["volume"] == 1000
      assert result["order_id"] == "12345"

  @pytest.mark.asyncio
  async def test_liquidate_single_position_failed_with_retry(
    self, liquidation_service, mock_position
  ):
    """测试单个持仓清仓失败并重试"""
    with patch.object(
      liquidation_service.trading_service, "place_order"
    ) as mock_place_order:
      mock_place_order.return_value = {
        "success": False,
        "error": "余额不足",
        "message": "下单失败",
      }

      result = await liquidation_service._liquidate_single_position(
        mock_position, max_retry=2
      )

      assert not result["success"]
      assert result["stock_code"] == "000001.SZ"
      assert "已重试2次" in result["message"]
      assert mock_place_order.call_count == 2

  @pytest.mark.asyncio
  async def test_get_liquidatable_positions_filters_zero_volume(
    self, liquidation_service
  ):
    """测试获取可清仓持仓过滤零数量"""
    positions = [
      MagicMock(can_use_volume=1000),  # 可清仓
      MagicMock(can_use_volume=0),  # 不可清仓
      MagicMock(can_use_volume=500),  # 可清仓
    ]

    with patch.object(
      liquidation_service.position_service, "get_positions"
    ) as mock_get_positions:
      mock_get_positions.return_value = positions

      result = await liquidation_service._get_liquidatable_positions()

      assert len(result) == 2
      assert all(pos.can_use_volume > 0 for pos in result)
