"""
持仓汇总 GraphQL 类型单元测试
"""

import pytest
from datetime import datetime
from decimal import Decimal

from gqlapi.types.portfolio_types import Position, PortfolioSummary
from models.position import Position as PositionModel


class TestPortfolioSummaryTypes:
  """测试持仓汇总相关的 GraphQL 类型"""

  def test_position_with_market_value_percent(self):
    """测试 Position 类型包含市值占比字段"""
    # 创建测试用的 PositionModel
    position_model = PositionModel(
      id="test_pos",
      account_id="300000013250",
      stock_code="600000.SH",
      instrument_name="浦发银行",
      volume=1000,
      avg_price=Decimal("10.50"),
      market_value=Decimal("11000.00")
    )

    # 测试转换，包含市值占比
    position = Position.from_model(
      position_model,
      last_price=11.20,
      market_value_percent=35.5
    )

    assert position.stock_code == "600000.SH"
    assert position.instrument_name == "浦发银行"
    assert position.volume == 1000
    assert position.avg_price == 10.50
    assert position.market_value == 11000.00
    assert position.last_price == 11.20
    assert position.market_value_percent == 35.5

  def test_position_without_market_value_percent(self):
    """测试 Position 类型不包含市值占比的情况"""
    position_model = PositionModel(
      id="test_pos",
      account_id="300000013250",
      stock_code="600000.SH",
      instrument_name="浦发银行",
      volume=1000,
      avg_price=Decimal("10.50"),
      market_value=Decimal("11000.00")
    )

    # 不提供市值占比
    position = Position.from_model(position_model, last_price=11.20)

    assert position.market_value_percent is None

  def test_portfolio_summary_creation(self):
    """测试 PortfolioSummary 对象创建"""
    # 创建测试持仓
    position_model = PositionModel(
      id="test_pos",
      account_id="300000013250",
      stock_code="600000.SH",
      instrument_name="浦发银行",
      volume=1000,
      avg_price=Decimal("10.50"),
      market_value=Decimal("11000.00")
    )

    position = Position.from_model(
      position_model,
      last_price=11.20,
      market_value_percent=100.0
    )

    # 创建汇总对象
    summary = PortfolioSummary(
      account_id="300000013250",
      account_name="测试账户",
      total_asset=50000.0,
      total_market_value=11000.0,
      cash=39000.0,
      cash_ratio=78.0,
      total_profit_loss=700.0,
      total_profit_loss_percent=6.67,
      today_profit_loss=None,
      today_profit_loss_percent=None,
      position_count=1,
      profit_position_count=1,
      loss_position_count=0,
      top_holdings=[position],
      update_time=datetime.now()
    )

    # 验证汇总数据
    assert summary.account_id == "300000013250"
    assert summary.account_name == "测试账户"
    assert summary.total_asset == 50000.0
    assert summary.total_market_value == 11000.0
    assert summary.cash == 39000.0
    assert summary.cash_ratio == 78.0
    assert summary.total_profit_loss == 700.0
    assert summary.total_profit_loss_percent == 6.67
    assert summary.today_profit_loss is None
    assert summary.today_profit_loss_percent is None
    assert summary.position_count == 1
    assert summary.profit_position_count == 1
    assert summary.loss_position_count == 0
    assert len(summary.top_holdings) == 1
    assert summary.top_holdings[0].stock_code == "600000.SH"
    assert summary.top_holdings[0].market_value_percent == 100.0

  def test_portfolio_summary_empty_holdings(self):
    """测试空持仓的汇总"""
    summary = PortfolioSummary(
      account_id="300000013250",
      account_name="测试账户",
      total_asset=50000.0,
      total_market_value=0.0,
      cash=50000.0,
      cash_ratio=100.0,
      total_profit_loss=0.0,
      total_profit_loss_percent=0.0,
      today_profit_loss=None,
      today_profit_loss_percent=None,
      position_count=0,
      profit_position_count=0,
      loss_position_count=0,
      top_holdings=[],
      update_time=datetime.now()
    )

    assert summary.position_count == 0
    assert summary.total_market_value == 0.0
    assert summary.cash_ratio == 100.0
    assert len(summary.top_holdings) == 0

  def test_portfolio_summary_multiple_holdings(self):
    """测试多个持仓的汇总"""
    # 创建多个测试持仓
    positions = []

    for i in range(3):
      position_model = PositionModel(
        id=f"test_pos_{i}",
        account_id="300000013250",
        stock_code=f"60000{i}.SH",
        instrument_name=f"测试股票{i}",
        volume=1000,
        avg_price=Decimal("10.00"),
        market_value=Decimal("11000.00")
      )

      position = Position.from_model(
        position_model,
        last_price=11.00,
        market_value_percent=33.33
      )
      positions.append(position)

    summary = PortfolioSummary(
      account_id="300000013250",
      account_name="测试账户",
      total_asset=50000.0,
      total_market_value=33000.0,
      cash=17000.0,
      cash_ratio=34.0,
      total_profit_loss=3000.0,
      total_profit_loss_percent=10.0,
      today_profit_loss=None,
      today_profit_loss_percent=None,
      position_count=3,
      profit_position_count=3,
      loss_position_count=0,
      top_holdings=positions,
      update_time=datetime.now()
    )

    assert summary.position_count == 3
    assert len(summary.top_holdings) == 3
    assert summary.total_market_value == 33000.0

    # 验证每个持仓的市值占比
    for position in summary.top_holdings:
      assert position.market_value_percent == 33.33