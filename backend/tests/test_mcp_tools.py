"""
MCP Tools 测试

测试 MCP 工具的基本功能
"""

import pytest
import asyncio
from types import SimpleNamespace
from unittest.mock import Mock, AsyncMock, patch
from quantx_mcp.tools import (
    MarketDataTools,
    StrategyTools,
    AccountTools,
    OrderTools,
    AnalysisTools
)


class TestMarketDataTools:
    """测试市场数据工具"""
    
    @pytest.fixture
    def market_tools(self):
        return MarketDataTools()
    
    def test_get_tools(self, market_tools):
        """测试获取工具列表"""
        tools = market_tools.get_tools()
        assert len(tools) == 6
        tool_names = [t.name for t in tools]
        assert "market_data_get_realtime" in tool_names
        assert "market_data_get_historical" in tool_names
    
    @pytest.mark.asyncio
    async def test_get_realtime(self, market_tools):
        """测试获取实时数据"""
        with patch('core.data.market_data_service.market_data_service') as mock_service:
            mock_service.get_realtime_data = AsyncMock(return_value={
                "price": 12.50,
                "volume": 100000
            })
            
            result = await market_tools._get_realtime({
                "symbol": "000001.SZ",
                "fields": ["price", "volume"]
            })
            
            assert result["status"] == "success"
            assert "data" in result
    
    @pytest.mark.asyncio
    async def test_search_symbols(self, market_tools):
        """测试搜索股票"""
        result = await market_tools._search_symbols({
            "keyword": "平安"
        })
        
        assert result["status"] == "success"
        assert "results" in result


class TestStrategyTools:
    """测试策略工具"""
    
    @pytest.fixture
    def strategy_tools(self):
        return StrategyTools()
    
    def test_get_tools(self, strategy_tools):
        """测试获取工具列表"""
        tools = strategy_tools.get_tools()
        assert len(tools) == 7
        tool_names = [t.name for t in tools]
        assert "strategy_list" in tool_names
        assert "strategy_execute" in tool_names
    
    @pytest.mark.asyncio
    async def test_list_strategies(self, strategy_tools):
        """测试列出策略"""
        with patch('core.strategy_manager.strategy_manager') as mock_manager:
            result = await strategy_tools._list_strategies({})
            
            assert result["status"] == "success"
            assert "strategies" in result
            assert isinstance(result["strategies"], list)
    
    @pytest.mark.asyncio
    async def test_execute_strategy(self, strategy_tools):
        """测试执行策略"""
        result = await strategy_tools._execute_strategy({
            "strategy_name": "DualThrust",
            "parameters": {
                "symbol": "000001.SZ",
                "interval": "1d"
            },
            "mode": "paper"
        })
        
        assert result["status"] == "success"
        assert "result" in result


class TestAccountTools:
    """测试账户工具"""
    
    @pytest.fixture
    def account_tools(self):
        return AccountTools()
    
    def test_get_tools(self, account_tools):
        """测试获取工具列表"""
        tools = account_tools.get_tools()
        assert len(tools) == 5
        tool_names = [t.name for t in tools]
        assert "account_get_info" in tool_names
        assert "account_get_positions" in tool_names
    
    @pytest.mark.asyncio
    async def test_get_account_info(self, account_tools):
        """测试获取账户信息"""
        result = await account_tools._get_account_info({})
        
        assert result["status"] == "success"
        assert "account" in result

    @pytest.mark.asyncio
    async def test_get_positions(self, account_tools):
        """测试获取持仓信息"""
        pos = SimpleNamespace(
            stock_code="000001.SZ",
            volume=100,
            can_use_volume=80,
            avg_price=10.0,
            last_price=12.0,
            market_value=1200.0,
            profit_rate=0.2,
        )

        with patch("miniqmt.manager_registry.XTTradingManagerRegistry") as mock_registry_cls:
            mock_registry = mock_registry_cls.return_value
            mock_manager = Mock()
            mock_manager.get_positions.return_value = [pos]
            mock_registry.get_manager.return_value = mock_manager

            result = await account_tools._get_positions({})

        assert result["status"] == "success"
        assert result["count"] == 1
        item = result["positions"][0]
        assert item["symbol"] == "000001.SZ"
        assert item["quantity"] == 100
        assert item["available_quantity"] == 80
        assert item["cost_price"] == 10.0
        assert item["current_price"] == 12.0
        assert item["market_value"] == 1200.0
        assert item["pnl"] == 200.0
        assert item["pnl_percent"] == 20.0


class TestOrderTools:
    """测试订单工具"""
    
    @pytest.fixture
    def order_tools(self):
        return OrderTools()
    
    def test_get_tools(self, order_tools):
        """测试获取工具列表"""
        tools = order_tools.get_tools()
        assert len(tools) == 4
        tool_names = [t.name for t in tools]
        assert "order_create" in tool_names
        assert "order_cancel" in tool_names
    
    @pytest.mark.asyncio
    async def test_create_order(self, order_tools):
        """测试创建订单"""
        result = await order_tools._create_order({
            "symbol": "000001.SZ",
            "side": "buy",
            "quantity": 100,
            "type": "limit",
            "price": 12.50
        })
        
        assert result["status"] == "success"
        assert "order_id" in result


class TestAnalysisTools:
    """测试分析工具"""
    
    @pytest.fixture
    def analysis_tools(self):
        return AnalysisTools()
    
    def test_get_tools(self, analysis_tools):
        """测试获取工具列表"""
        tools = analysis_tools.get_tools()
        assert len(tools) == 5
        tool_names = [t.name for t in tools]
        assert "analysis_calculate_indicators" in tool_names
        assert "analysis_scan_market" in tool_names
    
    @pytest.mark.asyncio
    async def test_calculate_indicators(self, analysis_tools):
        """测试计算技术指标"""
        result = await analysis_tools._calculate_indicators({
            "symbol": "000001.SZ",
            "indicators": ["MA", "RSI"],
            "period": "1d",
            "limit": 100
        })
        
        assert result["status"] == "success"
        assert "indicators" in result


class TestMCPServerIntegration:
    """MCP Server 集成测试"""
    
    @pytest.mark.asyncio
    async def test_server_initialization(self):
        """测试服务器初始化"""
        from quantx_mcp.server import create_mcp_server
        
        server = create_mcp_server()
        assert server is not None
        assert server.app is not None
    
    @pytest.mark.asyncio
    async def test_list_tools_endpoint(self):
        """测试列出工具端点"""
        from quantx_mcp.server import create_mcp_server
        
        server = create_mcp_server()
        tools = await server.app.list_tools()
        
        assert len(tools) == 27  # 总共27个工具
        tool_names = [t.name for t in tools]
        
        # 验证各类别工具都存在
        assert any(t.startswith("market_data_") for t in tool_names)
        assert any(t.startswith("strategy_") for t in tool_names)
        assert any(t.startswith("account_") for t in tool_names)
        assert any(t.startswith("order_") for t in tool_names)
        assert any(t.startswith("analysis_") for t in tool_names)


@pytest.mark.integration
class TestMCPRealDataFlow:
    """MCP 实际数据流测试（需要实际服务）"""
    
    @pytest.mark.asyncio
    async def test_real_market_data_query(self):
        """测试真实市场数据查询（需要实际数据服务）"""
        from quantx_mcp.server import create_mcp_server
        
        server = create_mcp_server()
        
        # 模拟调用实时数据工具
        result = await server.app.call_tool(
            "market_data_get_realtime",
            {"symbol": "000001.SZ"}
        )
        
        assert len(result) > 0
        # 注意：这些是占位符实现，实际需要连接真实数据服务


if __name__ == "__main__":
    # 运行测试
    pytest.main([__file__, "-v"])
