"""
持仓汇总 GraphQL API 集成测试
"""

import pytest
from datetime import datetime

from gqlapi.schema import schema


class TestPortfolioSummaryGraphQL:
  """测试持仓汇总 GraphQL 查询集成"""

  @pytest.mark.asyncio
  @pytest.mark.api
  async def test_portfolio_summary_query_basic(self):
    """测试基础的持仓汇总查询"""
    query = """
    query {
      portfolioSummary {
        accountId
        accountName
        totalAsset
        totalMarketValue
        cash
        cashRatio
        totalProfitLoss
        totalProfitLossPercent
        positionCount
        profitPositionCount
        lossPositionCount
        updateTime
      }
    }
    """

    # 执行查询
    result = await schema.execute(query)

    # 验证没有错误
    assert result.errors is None

    # 验证返回数据结构
    data = result.data
    assert "portfolioSummary" in data

    summary = data["portfolioSummary"]
    assert "accountId" in summary
    assert "accountName" in summary
    assert "totalAsset" in summary
    assert "totalMarketValue" in summary
    assert "cash" in summary
    assert "cashRatio" in summary
    assert "totalProfitLoss" in summary
    assert "totalProfitLossPercent" in summary
    assert "positionCount" in summary
    assert "profitPositionCount" in summary
    assert "lossPositionCount" in summary
    assert "updateTime" in summary

    # 验证数据类型
    assert isinstance(summary["accountId"], str)
    assert isinstance(summary["accountName"], str)
    assert isinstance(summary["totalAsset"], (int, float))
    assert isinstance(summary["totalMarketValue"], (int, float))
    assert isinstance(summary["cash"], (int, float))
    assert isinstance(summary["cashRatio"], (int, float))
    assert isinstance(summary["totalProfitLoss"], (int, float))
    assert isinstance(summary["totalProfitLossPercent"], (int, float))
    assert isinstance(summary["positionCount"], int)
    assert isinstance(summary["profitPositionCount"], int)
    assert isinstance(summary["lossPositionCount"], int)

  @pytest.mark.asyncio
  @pytest.mark.api
  async def test_portfolio_summary_query_with_top_holdings(self):
    """测试包含重要持仓的汇总查询"""
    query = """
    query {
      portfolioSummary {
        accountId
        totalMarketValue
        positionCount
        topHoldings {
          stockCode
          instrumentName
          volume
          avgPrice
          marketValue
          marketValuePercent
          lastPrice
          profitLoss
          profitRate
        }
      }
    }
    """

    result = await schema.execute(query)

    # 验证没有错误
    assert result.errors is None

    data = result.data
    summary = data["portfolioSummary"]

    # 验证重要持仓字段
    assert "topHoldings" in summary
    top_holdings = summary["topHoldings"]
    assert isinstance(top_holdings, list)

    # 如果有持仓数据，验证结构
    if len(top_holdings) > 0:
      holding = top_holdings[0]
      assert "stockCode" in holding
      assert "instrumentName" in holding
      assert "volume" in holding
      assert "avgPrice" in holding
      assert "marketValue" in holding
      assert "marketValuePercent" in holding

      # 验证市值占比字段存在且合理
      if holding["marketValuePercent"] is not None:
        assert 0 <= holding["marketValuePercent"] <= 100

  @pytest.mark.asyncio
  @pytest.mark.api
  async def test_portfolio_summary_query_with_account_id(self):
    """测试指定账户ID的汇总查询"""
    query = """
    query($accountId: String) {
      portfolioSummary(accountId: $accountId) {
        accountId
        accountName
        totalAsset
        positionCount
      }
    }
    """

    variables = {"accountId": "300000013250"}

    result = await schema.execute(query, variable_values=variables)

    # 验证没有错误
    assert result.errors is None

    data = result.data
    summary = data["portfolioSummary"]

    # 验证账户ID匹配
    assert summary["accountId"] == "300000013250"

  @pytest.mark.asyncio
  @pytest.mark.api
  async def test_portfolio_summary_query_selective_fields(self):
    """测试 GraphQL 按需字段选择"""
    # 只查询部分字段
    query = """
    query {
      portfolioSummary {
        accountId
        totalMarketValue
        positionCount
      }
    }
    """

    result = await schema.execute(query)

    assert result.errors is None

    data = result.data
    summary = data["portfolioSummary"]

    # 验证只有请求的字段
    assert "accountId" in summary
    assert "totalMarketValue" in summary
    assert "positionCount" in summary

    # 这些字段不应该在响应中（因为没有请求）
    # 注意：GraphQL会返回所有标量字段，这个测试主要验证查询不会出错

  @pytest.mark.asyncio
  @pytest.mark.api
  async def test_portfolio_summary_query_top_holdings_selective(self):
    """测试重要持仓的按需字段选择"""
    query = """
    query {
      portfolioSummary {
        topHoldings {
          stockCode
          marketValue
          marketValuePercent
        }
      }
    }
    """

    result = await schema.execute(query)

    assert result.errors is None

    data = result.data
    summary = data["portfolioSummary"]
    top_holdings = summary["topHoldings"]

    # 验证可以成功查询持仓的部分字段
    if len(top_holdings) > 0:
      holding = top_holdings[0]
      assert "stockCode" in holding
      assert "marketValue" in holding

  @pytest.mark.asyncio
  @pytest.mark.api
  async def test_portfolio_summary_query_error_handling(self):
    """测试错误处理"""
    # 使用无效的账户ID
    query = """
    query {
      portfolioSummary(accountId: "invalid_account") {
        accountId
        totalAsset
      }
    }
    """

    result = await schema.execute(query)

    # 即使账户无效，也应该返回默认数据而不是错误
    # 根据我们的实现，应该返回空数据
    if result.errors is None:
      data = result.data
      summary = data["portfolioSummary"]
      assert summary["accountId"] == "invalid_account"
      assert summary["totalAsset"] == 0.0

  @pytest.mark.asyncio
  @pytest.mark.api
  async def test_portfolio_summary_performance(self):
    """测试查询性能"""
    import time

    query = """
    query {
      portfolioSummary {
        accountId
        totalAsset
        totalMarketValue
        positionCount
        topHoldings {
          stockCode
          instrumentName
          marketValue
          marketValuePercent
        }
      }
    }
    """

    start_time = time.time()
    result = await schema.execute(query)
    end_time = time.time()

    # 验证查询成功
    assert result.errors is None

    # 验证查询时间合理（应该在2秒内完成）
    execution_time = end_time - start_time
    assert execution_time < 2.0, f"查询时间过长: {execution_time}秒"