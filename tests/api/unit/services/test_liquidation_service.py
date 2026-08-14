"""
清仓服务单元测试
"""

from unittest.mock import MagicMock, patch

import pytest
import quantx_infrastructure.services.liquidation_service as liquidation_module
from quantx_infrastructure.models.enums import AccountType, OrderType, PriceType
from quantx_infrastructure.models.liquidation import (
  ConditionalLiquidationOrder,
  ConditionalLiquidationSellMode,
  ConditionalLiquidationStrategy,
)
from quantx_infrastructure.models.position import Position
from quantx_infrastructure.services.liquidation_service import (
  LiquidationError,
  LiquidationService,
)


def make_conditional_order(**overrides):
  data = {
    "id": "condition-1",
    "account_id": "account-a",
    "stock_code": "000001.SZ",
    "enabled": True,
    "status": "ACTIVE",
    "target_profit_pct": 10.0,
    "target_price": None,
    "sell_mode": ConditionalLiquidationSellMode.ALL_AVAILABLE,
    "sell_ratio_pct": None,
    "sell_volume": None,
  }
  data.update(overrides)
  return ConditionalLiquidationOrder(**data)


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
    with patch.object(liquidation_service, "_get_all_positions") as mock_get_positions:
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
        "message": "清仓委托已提交",
      }

      result = await liquidation_service.liquidate_all_positions(confirm=True)

      assert result.success
      assert result.total_positions == 1
      assert result.liquidated_positions == 1
      assert result.failed_positions == 0
      assert "清仓委托提交完成" in result.message
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
        "message": "清仓委托已提交",
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
  async def test_redeem_cleared_position_fails_closed_without_transfer_channel(
    self, liquidation_service
  ):
    """未配置真实划转通道时不得返回模拟成功。"""
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

      assert not result["success"]
      assert result["stock_code"] == "000001.SZ"
      assert "尚未配置" in result["message"]
      assert result.get("redeemed_amount") is None

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
      assert "清仓委托已提交" in result["message"]
      mock_place_order.assert_called_once_with(
        stock_code="000001.SZ",
        order_type=OrderType.SELL,
        order_volume=1000,
        price_type=PriceType.MARKET_CONVERT_5_LIMIT,
        price=0,
        strategy_name="清仓操作",
        order_remark="清仓: 000001.SZ",
        close_position=True,
      )

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

    with patch.object(liquidation_service, "_get_all_positions") as mock_get_positions:
      mock_get_positions.return_value = positions

      result = await liquidation_service._get_liquidatable_positions()

      assert len(result) == 2
      assert all(pos.can_use_volume > 0 for pos in result)

  @pytest.mark.asyncio
  async def test_get_position_filters_by_account_and_stock(self, monkeypatch):
    """测试按账户和股票查询真实持仓"""
    captured = {}
    expected_position = MagicMock(spec=Position)

    async def fake_get_async_db():
      yield object()

    class FakePositionRepository:
      def __init__(self, db):
        self.db = db

      async def find_by_stock_code(
        self, stock_code, account_id=None, account_type=None
      ):
        captured["stock_code"] = stock_code
        captured["account_id"] = account_id
        captured["account_type"] = account_type
        return expected_position

    monkeypatch.setattr(liquidation_module, "get_async_db", fake_get_async_db)
    monkeypatch.setattr(
      liquidation_module, "PositionRepository", FakePositionRepository
    )

    service = LiquidationService.__new__(LiquidationService)
    service.account_id = "account-a"
    service.account_type = AccountType.STOCK

    result = await service._get_position("000001.SZ")

    assert result is expected_position
    assert captured == {
      "stock_code": "000001.SZ",
      "account_id": "account-a",
      "account_type": AccountType.STOCK,
    }

  @pytest.mark.asyncio
  async def test_conditional_order_update_rejects_other_account(self, monkeypatch):
    async def fake_get_async_db():
      yield object()

    class FakeConditionalRepository:
      def __init__(self, db):
        self.db = db

      async def find_by_id(self, order_id):
        return make_conditional_order(id=order_id, account_id="account-b")

    monkeypatch.setattr(liquidation_module, "get_async_db", fake_get_async_db)
    monkeypatch.setattr(
      liquidation_module,
      "ConditionalLiquidationOrderRepository",
      FakeConditionalRepository,
    )

    service = LiquidationService.__new__(LiquidationService)
    service.account_id = "account-a"

    with pytest.raises(LiquidationError, match="不属于当前资金账户"):
      await service.set_conditional_liquidation_order_enabled(
        "condition-b",
        False,
      )

  def test_conditional_order_triggers_on_profit_pct(self, mock_position):
    """测试条件清仓单按收益率触发"""
    service = LiquidationService.__new__(LiquidationService)
    mock_position.avg_price = 10.0
    mock_position.volume = 1000

    triggered, reason, profit_pct = service.is_conditional_order_triggered(
      make_conditional_order(target_profit_pct=10.0),
      mock_position,
      11.0,
    )

    assert triggered
    assert reason == "target_profit_pct_reached"
    assert profit_pct == pytest.approx(10.0)

  def test_conditional_order_triggers_on_target_price(self, mock_position):
    """测试条件清仓单按目标价触发"""
    service = LiquidationService.__new__(LiquidationService)
    mock_position.avg_price = None
    mock_position.volume = 1000

    triggered, reason, profit_pct = service.is_conditional_order_triggered(
      make_conditional_order(target_profit_pct=None, target_price=12.0),
      mock_position,
      12.01,
    )

    assert triggered
    assert reason == "target_price_reached"
    assert profit_pct is None

  def test_conditional_order_triggers_when_either_condition_matches(
    self, mock_position
  ):
    """测试收益率和目标价任一满足即可触发"""
    service = LiquidationService.__new__(LiquidationService)
    mock_position.avg_price = 10.0
    mock_position.volume = 1000

    triggered, reason, profit_pct = service.is_conditional_order_triggered(
      make_conditional_order(target_profit_pct=50.0, target_price=11.0),
      mock_position,
      11.0,
    )

    assert triggered
    assert reason == "target_price_reached"
    assert profit_pct == pytest.approx(10.0)

  def test_conditional_order_missing_price_or_cost_is_conservative(
    self, mock_position
  ):
    """测试缺少现价或成本价时保守不触发"""
    service = LiquidationService.__new__(LiquidationService)
    mock_position.avg_price = None
    mock_position.volume = 1000

    triggered, reason, _ = service.is_conditional_order_triggered(
      make_conditional_order(target_profit_pct=10.0, target_price=None),
      mock_position,
      None,
    )
    assert not triggered
    assert reason == "missing_latest_price"

    triggered, reason, _ = service.is_conditional_order_triggered(
      make_conditional_order(target_profit_pct=10.0, target_price=None),
      mock_position,
      11.0,
    )
    assert not triggered
    assert reason == "missing_avg_price"

  def test_conditional_sell_volume_modes(self, mock_position):
    """测试全部、比例、固定股数三种卖出数量计算"""
    service = LiquidationService.__new__(LiquidationService)
    service.market_rules = liquidation_module.AShareMarketRules()
    mock_position.can_use_volume = 1000

    assert (
      service.calculate_conditional_sell_volume(
        make_conditional_order(
          sell_mode=ConditionalLiquidationSellMode.ALL_AVAILABLE
        ),
        mock_position,
      )
      == 1000
    )
    assert (
      service.calculate_conditional_sell_volume(
        make_conditional_order(
          sell_mode=ConditionalLiquidationSellMode.PERCENT_AVAILABLE,
          sell_ratio_pct=55,
        ),
        mock_position,
      )
      == 500
    )
    assert (
      service.calculate_conditional_sell_volume(
        make_conditional_order(
          sell_mode=ConditionalLiquidationSellMode.FIXED_VOLUME,
          sell_volume=280,
        ),
        mock_position,
      )
      == 200
    )

  def test_conditional_sell_volume_allows_odd_lot_only_for_full_clear(
    self, mock_position
  ):
    """测试零股只允许清仓卖出"""
    service = LiquidationService.__new__(LiquidationService)
    service.market_rules = liquidation_module.AShareMarketRules()
    mock_position.can_use_volume = 150

    assert (
      service.calculate_conditional_sell_volume(
        make_conditional_order(
          sell_mode=ConditionalLiquidationSellMode.ALL_AVAILABLE
        ),
        mock_position,
      )
      == 150
    )
    assert (
      service.calculate_conditional_sell_volume(
        make_conditional_order(
          sell_mode=ConditionalLiquidationSellMode.PERCENT_AVAILABLE,
          sell_ratio_pct=50,
        ),
        mock_position,
      )
      == 0
    )
    assert (
      service.calculate_conditional_sell_volume(
        make_conditional_order(
          sell_mode=ConditionalLiquidationSellMode.FIXED_VOLUME,
          sell_volume=150,
        ),
        mock_position,
      )
      == 100
    )

  @pytest.mark.asyncio
  async def test_conditional_order_submit_once_when_triggered(
    self, liquidation_service, mock_position
  ):
    """测试条件清仓单触发后提交一次并停用"""
    order = make_conditional_order(target_profit_pct=5.0)
    mock_position.avg_price = 10.0
    mock_position.last_price = 11.0
    mock_position.can_use_volume = 1000
    updates = []

    async def fake_update(order_id, payload):
      updates.append((order_id, payload))
      return order

    with (
      patch.object(
        liquidation_service, "_get_position_for_condition", return_value=mock_position
      ),
      patch.object(
        liquidation_service, "_update_conditional_order", side_effect=fake_update
      ),
      patch.object(
        liquidation_service,
        "_liquidate_single_position",
        return_value={
          "success": True,
          "stock_code": "000001.SZ",
          "volume": 1000,
          "order_id": "order-1",
          "message": "条件清仓委托已提交",
        },
      ) as mock_submit,
    ):
      result = await liquidation_service.evaluate_conditional_liquidation_order(order)

    assert result.triggered
    assert result.submitted
    assert result.order_id == "order-1"
    assert mock_submit.call_count == 1
    assert any(payload.get("enabled") is False for _, payload in updates)
    assert any(payload.get("status") == "SUBMITTED" for _, payload in updates)

  @pytest.mark.asyncio
  async def test_conditional_order_no_position_or_volume_does_not_submit(
    self, liquidation_service, mock_position
  ):
    """测试无持仓或无可卖量时不提交委托"""
    order = make_conditional_order(target_profit_pct=5.0)

    with (
      patch.object(liquidation_service, "_get_position_for_condition", return_value=None),
      patch.object(liquidation_service, "_update_conditional_order"),
      patch.object(liquidation_service, "_liquidate_single_position") as mock_submit,
    ):
      result = await liquidation_service.evaluate_conditional_liquidation_order(order)

    assert not result.submitted
    assert result.error == "missing_position"
    mock_submit.assert_not_called()

    mock_position.avg_price = 10.0
    mock_position.last_price = 11.0
    mock_position.volume = 1000
    mock_position.can_use_volume = 0
    with (
      patch.object(
        liquidation_service, "_get_position_for_condition", return_value=mock_position
      ),
      patch.object(liquidation_service, "_update_conditional_order"),
      patch.object(liquidation_service, "_liquidate_single_position") as mock_submit,
    ):
      result = await liquidation_service.evaluate_conditional_liquidation_order(order)

    assert result.triggered
    assert not result.submitted
    assert result.error == "no_legal_sell_volume"
    mock_submit.assert_not_called()

  @pytest.mark.asyncio
  async def test_adaptive_conditional_order_never_uses_immediate_submit_path(
    self, liquidation_service, mock_position
  ):
    order = make_conditional_order(
      strategy=ConditionalLiquidationStrategy.ADAPTIVE_VOLUME_PRICE_TRAILING,
      sell_mode=ConditionalLiquidationSellMode.FIXED_VOLUME,
      sell_volume=300,
    )
    mock_position.avg_price = 10.0
    mock_position.last_price = 12.0

    with (
      patch.object(
        liquidation_service,
        "_get_position_for_condition",
        return_value=mock_position,
      ),
      patch.object(liquidation_service, "_liquidate_single_position") as submit,
    ):
      result = await liquidation_service.evaluate_conditional_liquidation_order(
        order
      )

    assert not result.submitted
    assert result.message == "adaptive_exit_requires_engine_market_context"
    submit.assert_not_called()

  def test_dynamic_strategy_validation_is_explicit(self, liquidation_service):
    assert (
      liquidation_service._normalize_conditional_strategy(
        "adaptive_volume_price_trailing"
      )
      == ConditionalLiquidationStrategy.ADAPTIVE_VOLUME_PRICE_TRAILING
    )
    with pytest.raises(LiquidationError, match="不支持的条件清仓策略"):
      liquidation_service._normalize_conditional_strategy("unknown")
