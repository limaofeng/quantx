"""
MCP Tool Handlers for QuantX

This module defines all available MCP tools and their handlers.
Each tool category is organized into a separate class.
"""

import logging
import uuid
from datetime import datetime
from typing import List, Optional

from mcp.types import Tool
from quantx_infrastructure.core.utils import time_utils

logger = logging.getLogger(__name__)


class MarketDataTools:
    """Market data related tools"""
    
    def get_tools(self) -> List[Tool]:
        """Get list of market data tools"""
        return [
            Tool(
                name="market_data_list_instruments",
                description="List all market instruments (stocks, indices, ETFs, funds, etc.)",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "instrument_type": {
                            "type": "string",
                            "description": "Filter by instrument type (stock, index, etf, fund, futures, bond, etc.)",
                            "optional": True
                        },
                        "market": {
                            "type": "string",
                            "description": "Filter by market (SH, SHFE, SZ, etc.)",
                            "optional": True
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum number of results to return",
                            "default": 100
                        },
                        "offset": {
                            "type": "integer",
                            "description": "Number of results to skip",
                            "default": 0
                        }
                    }
                }
            ),
            Tool(
                name="market_data_get_instrument_info",
                description="Get detailed information about a specific instrument",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "symbol": {
                            "type": "string",
                            "description": "Instrument symbol (e.g., 000001.SZ, 000300.SH)"
                        }
                    },
                    "required": ["symbol"]
                }
            ),
            Tool(
                name="market_data_search_instruments",
                description="Search for instruments by name, code, or pinyin",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "keyword": {
                            "type": "string",
                            "description": "Search keyword (supports Chinese name, code, or pinyin)"
                        },
                        "instrument_type": {
                            "type": "string",
                            "description": "Filter by instrument type",
                            "optional": True
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum results to return",
                            "default": 50
                        }
                    },
                    "required": ["keyword"]
                }
            ),
            Tool(
                name="market_data_get_index_constituents",
                description="Get all constituent stocks of an index",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "index_symbol": {
                            "type": "string",
                            "description": "Index symbol (e.g., 000300.SH for 沪深300, 000016.SH for 上证50)"
                        }
                    },
                    "required": ["index_symbol"]
                }
            ),
            Tool(
                name="market_data_get_sector_stocks",
                description="Get stocks in a specific sector or industry",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "sector_name": {
                            "type": "string",
                            "description": "Sector or industry name (e.g., 银行, 科技, 医药)"
                        },
                        "market": {
                            "type": "string",
                            "description": "Filter by market (SH, SZ, or empty for all)",
                            "optional": True
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum results to return",
                            "default": 100
                        }
                    },
                    "required": ["sector_name"]
                }
            ),
            Tool(
                name="market_data_get_realtime",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "symbol": {
                            "type": "string",
                            "description": "Stock symbol (e.g., 000001.SZ for 平安银行)"
                        },
                        "fields": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Fields to retrieve (default: all)",
                            "optional": True
                        }
                    },
                    "required": ["symbol"]
                }
            ),
            Tool(
                name="market_data_get_historical",
                description="Get historical market data",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "symbol": {
                            "type": "string",
                            "description": "Stock symbol"
                        },
                        "start_date": {
                            "type": "string",
                            "description": "Start date (YYYY-MM-DD)"
                        },
                        "end_date": {
                            "type": "string",
                            "description": "End date (YYYY-MM-DD)"
                        },
                        "interval": {
                            "type": "string",
                            "description": "Data interval (1d, 1h, 30m, 15m, 5m, 1m)",
                            "default": "1d"
                        }
                    },
                    "required": ["symbol", "start_date", "end_date"]
                }
            ),
            Tool(
                name="market_data_get_kline",
                description="Get K-line (candlestick) data",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "symbol": {
                            "type": "string",
                            "description": "Stock symbol"
                        },
                        "period": {
                            "type": "string",
                            "description": "K-line period (daily, weekly, monthly, 1min, 5min, etc)"
                        },
                        "count": {
                            "type": "integer",
                            "description": "Number of bars to retrieve",
                            "default": 100
                        }
                    },
                    "required": ["symbol", "period"]
                }
            ),
            Tool(
                name="market_data_subscribe",
                description="Subscribe to real-time market data updates",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "symbols": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "List of symbols to subscribe"
                        }
                    },
                    "required": ["symbols"]
                }
            ),
            Tool(
                name="market_data_unsubscribe",
                description="Unsubscribe from market data updates",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "symbols": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "List of symbols to unsubscribe"
                        }
                    },
                    "required": ["symbols"]
                }
            ),
            Tool(
                name="market_data_search_symbols",
                description="Search for stock symbols by name or code",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "keyword": {
                            "type": "string",
                            "description": "Search keyword (symbol name or pinyin)"
                        },
                        "market": {
                            "type": "string",
                            "description": "Market filter (SZ, SH, or empty for all)",
                            "optional": True
                        }
                    },
                    "required": ["keyword"]
                }
            )
        ]
    
    async def handle(self, name: str, arguments: dict) -> dict:
        """Handle market data tool calls"""
        
        if name == "market_data_list_instruments":
            return await self._list_instruments(arguments)
        
        elif name == "market_data_get_instrument_info":
            return await self._get_instrument_info(arguments)
        
        elif name == "market_data_search_instruments":
            return await self._search_instruments(arguments)
        
        elif name == "market_data_get_index_constituents":
            return await self._get_index_constituents(arguments)
        
        elif name == "market_data_get_sector_stocks":
            return await self._get_sector_stocks(arguments)
        
        elif name == "market_data_get_realtime":
            return await self._get_realtime(arguments)
        
        elif name == "market_data_get_historical":
            return await self._get_historical(arguments)
        
        elif name == "market_data_get_kline":
            return await self._get_kline(arguments)
        
        elif name == "market_data_subscribe":
            return await self._subscribe(arguments)
        
        elif name == "market_data_unsubscribe":
            return await self._unsubscribe(arguments)
        
        elif name == "market_data_search_symbols":
            return await self._search_symbols(arguments)
        
        else:
            return {"error": f"Unknown tool: {name}"}
    
    async def _get_realtime(self, args: dict) -> dict:
        """Get real-time market data"""
        try:
            from quantx_api.market_data_read_service import (
                market_data_read_service as market_data_service,
            )
            
            symbol = args["symbol"]
            fields = args.get("fields", [])
            
            # 读取统一行情网关的最新观测值；服务端不直接连接 QMT。
            tick_data = await market_data_service.get_latest_price(symbol)
            
            if not tick_data:
                return {
                    "status": "error",
                    "error": f"No data found for symbol: {symbol}"
                }
            
            # 转换为字典格式
            data = {
                "symbol": symbol,
                "last_price": tick_data.last_price,
                "volume": tick_data.volume,
                "amount": tick_data.amount,
                "bid_price": tick_data.bid_price,
                "ask_price": tick_data.ask_price,
                "bid_volume": getattr(
                    tick_data, "bid_volume", getattr(tick_data, "bid_vol", [])
                ),
                "ask_volume": getattr(
                    tick_data, "ask_volume", getattr(tick_data, "ask_vol", [])
                ),
                "open_interest": getattr(tick_data, 'open_interest', 0),
                "timestamp": (
                    getattr(tick_data, "timestamp", None)
                    or getattr(tick_data, "time", None)
                ),
            }
            if data["timestamp"] is not None:
                data["timestamp"] = data["timestamp"].isoformat()
            
            # 如果指定了字段，只返回指定字段
            if fields:
                data = {k: v for k, v in data.items() if k in fields or k == "symbol"}
            
            return {
                "status": "success",
                "symbol": symbol,
                "data": data,
                "timestamp": time_utils.now().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Error getting realtime data: {e}", exc_info=True)
            return {
                "status": "error",
                "error": str(e),
                "message": f"Failed to retrieve realtime data for {args.get('symbol', 'unknown')}"
            }
    
    async def _get_historical(self, args: dict) -> dict:
        """Get historical market data"""
        try:
            from quantx_api.market_data_read_service import (
                market_data_read_service as market_data_service,
            )
            
            symbol = args["symbol"]
            start_date = args["start_date"]
            end_date = args["end_date"]
            interval = args.get("interval", "1d")
            
            # 解析日期
            start_dt = datetime.strptime(start_date, "%Y-%m-%d")
            end_dt = datetime.strptime(end_date, "%Y-%m-%d")
            
            # 获取历史K线数据
            bars = await market_data_service.get_bars(
                symbol=symbol,
                start_date=start_dt,
                end_date=end_dt,
                interval=interval
            )
            
            if not bars:
                return {
                    "status": "error",
                    "error": f"No historical data found for {symbol} in the specified date range"
                }
            
            # 转换K线数据为字典列表
            bars_data = []
            for bar in bars:
                bar_time = getattr(bar, "datetime", None) or getattr(
                    bar, "time", None
                )
                bars_data.append({
                    "datetime": bar_time.isoformat() if bar_time else None,
                    "open": bar.open,
                    "high": bar.high,
                    "low": bar.low,
                    "close": bar.close,
                    "volume": bar.volume,
                    "amount": getattr(bar, 'amount', 0)
                })
            
            return {
                "status": "success",
                "data": {
                    "symbol": symbol,
                    "interval": interval,
                    "start_date": start_date,
                    "end_date": end_date,
                    "bars": bars_data,
                    "count": len(bars_data)
                }
            }
            
        except ValueError as e:
            logger.error(f"Invalid date format: {e}")
            return {
                "status": "error",
                "error": f"Invalid date format. Use YYYY-MM-DD. Error: {str(e)}"
            }
        except Exception as e:
            logger.error(f"Error getting historical data: {e}", exc_info=True)
            return {
                "status": "error",
                "error": str(e),
                "message": f"Failed to retrieve historical data for {args.get('symbol', 'unknown')}"
            }
    
    async def _get_kline(self, args: dict) -> dict:
        """Get K-line data"""
        try:
            from datetime import timedelta

            from quantx_infrastructure.services.historical_market_data_service import (
                HistoricalMarketDataService,
            )

            symbol = args["symbol"]
            period = args["period"]
            count = args.get("count", 100)

            # 计算开始时间（根据周期估算回溯时间）
            # 默认往前推足够长的时间以获取足够的K线数据
            start_time = time_utils.now() - timedelta(days=365)  # 默认获取最近1年的数据

            # 获取最近的 K 线数据（使用 desc 排序，获取最新的 count 条）
            klines = await HistoricalMarketDataService().get_kline_data(
                stock_code=symbol,
                period=period,
                start_time=start_time,
                end_time=time_utils.now(),
                limit=count,
                order="desc"  # 获取最新的数据
            )

            if not klines:
                return {
                    "status": "success",
                    "symbol": symbol,
                    "period": period,
                    "count": count,
                    "data": []
                }

            # 转换 K 线数据为字典列表（按时间正序排列）
            bars_data = []
            for kline in reversed(klines):  # 反转数组，让最早的数据在前
                bars_data.append({
                    "datetime": kline.datetime.isoformat() if hasattr(kline, 'datetime') else None,
                    "open": kline.open,
                    "high": kline.high,
                    "low": kline.low,
                    "close": kline.close,
                    "volume": kline.volume,
                    "amount": getattr(kline, 'amount', 0)
                })

            return {
                "status": "success",
                "symbol": symbol,
                "period": period,
                "count": len(bars_data),
                "data": bars_data
            }

        except Exception as e:
            logger.error(f"Error getting K-line data: {e}", exc_info=True)
            return {
                "status": "error",
                "error": str(e),
                "message": f"Failed to retrieve K-line data for {args.get('symbol', 'unknown')}"
            }
    
    async def _subscribe(self, args: dict) -> dict:
        """Subscribe to market data"""
        try:
            symbols = args["symbols"]
            return {
                "status": "unsupported",
                "subscribed": [],
                "message": (
                    "MCP request/response transport does not keep a market-data "
                    f"stream open for {len(symbols)} symbols; use GraphQL subscriptions"
                ),
            }
            
        except Exception as e:
            logger.error(f"Error subscribing: {e}")
            return {"status": "error", "error": str(e)}
    
    async def _unsubscribe(self, args: dict) -> dict:
        """Unsubscribe from market data"""
        try:
            symbols = args["symbols"]
            
            # TODO: Implement actual unsubscription
            return {
                "status": "success",
                "unsubscribed": symbols
            }
            
        except Exception as e:
            logger.error(f"Error unsubscribing: {e}")
            return {"status": "error", "error": str(e)}
    
    async def _list_instruments(self, args: dict) -> dict:
        """List all market instruments"""
        try:
            from quantx_infrastructure.database.types import Pageable, Sort
            from quantx_infrastructure.models.enums import InstrumentType
            from quantx_infrastructure.repositories.instrument_where_builder import (
                InstrumentWhereBuilder,
            )
            from quantx_infrastructure.services.instrument_service import (
                InstrumentService,
            )
            
            instrument_service = InstrumentService()
            instrument_type = args.get("instrument_type")
            market = args.get("market")
            limit = args.get("limit", 100)
            offset = args.get("offset", 0)
            
            # 构建查询条件
            where_builder = None
            if instrument_type or market:
                where_builder = InstrumentWhereBuilder()
                
                if instrument_type:
                    # 转换字符串类型为枚举
                    try:
                        inst_type = InstrumentType(instrument_type.lower())
                        where_builder = where_builder.type_eq(inst_type)
                    except ValueError:
                        return {
                            "status": "error",
                            "error": f"Invalid instrument type: {instrument_type}"
                        }
                
                if market:
                    where_builder = where_builder.market_eq(market)
            
            # 分页查询
            pageable = Pageable(page=offset // limit, size=limit, sort=Sort.by("id"))
            pagination = await instrument_service.find_page(
                pageable=pageable,
                where=where_builder
            )
            
            # 转换为字典格式
            instruments = []
            for inst in pagination.content:
                instruments.append({
                    "symbol": f"{inst.id}.{inst.market}" if inst.market else inst.id,
                    "code": inst.id,
                    "name": inst.name,
                    "type": inst.type.value if inst.type else None,
                    "market": inst.market,
                    "list_date": inst.open_date.isoformat() if inst.open_date else None,
                    "expire_date": inst.expire_date.isoformat() if inst.expire_date else None
                })
            
            return {
                "status": "success",
                "instruments": instruments,
                "total": pagination.total_elements,
                "page": pagination.page,
                "page_size": pagination.page_size,
                "total_pages": pagination.total_pages
            }
            
        except Exception as e:
            logger.error(f"Error listing instruments: {e}", exc_info=True)
            return {
                "status": "error",
                "error": str(e),
                "message": "Failed to retrieve instrument list"
            }
    
    async def _get_instrument_info(self, args: dict) -> dict:
        """Get detailed instrument information"""
        try:
            from quantx_infrastructure.services.instrument_service import (
                InstrumentService,
            )
            
            instrument_service = InstrumentService()
            symbol = args["symbol"]
            
            # 解析symbol (格式: 000001.SZ)
            parts = symbol.split(".")
            code = parts[0]
            market = parts[1] if len(parts) > 1 else None
            
            # 查询标的
            if market:
                # 通过code和market查询
                from quantx_infrastructure.repositories.instrument_where_builder import (
                    InstrumentWhereBuilder,
                )
                where_builder = InstrumentWhereBuilder().code_eq(code).market_eq(market)
                instruments = await instrument_service.find_all(
                    where=where_builder,
                    limit=1
                )
            else:
                # 只通过code查询
                instrument = await instrument_service.find_by_code(code)
                instruments = [instrument] if instrument else []
            
            if not instruments or not instruments[0]:
                return {
                    "status": "error",
                    "error": f"Instrument '{symbol}' not found"
                }
            
            inst = instruments[0]
            
            return {
                "status": "success",
                "instrument": {
                    "symbol": symbol,
                    "code": inst.id,
                    "name": inst.name,
                    "type": inst.type.value if inst.type else None,
                    "market": inst.market,
                    "abbreviation": inst.abbreviation,
                    "extend_name": inst.extend_name,
                    "exchange_code": inst.exchange_code,
                    "list_date": inst.open_date.isoformat() if inst.open_date else None,
                    "expire_date": inst.expire_date.isoformat() if inst.expire_date else None,
                    "pre_close": inst.pre_close,
                    "up_stop_price": inst.up_stop_price,
                    "down_stop_price": inst.down_stop_price,
                    "float_volume": inst.float_volume,
                    "total_volume": inst.total_volume,
                    "product_id": inst.product_id,
                    "product_name": inst.product_name
                }
            }
            
        except Exception as e:
            logger.error(f"Error getting instrument info: {e}", exc_info=True)
            return {
                "status": "error",
                "error": str(e),
                "message": f"Failed to get info for '{args.get('symbol', 'unknown')}'"
            }
    
    async def _search_instruments(self, args: dict) -> dict:
        """Search for instruments"""
        try:
            from quantx_infrastructure.models.enums import InstrumentType
            from quantx_infrastructure.repositories.instrument_where_builder import (
                InstrumentWhereBuilder,
            )
            from quantx_infrastructure.services.instrument_service import (
                InstrumentService,
            )
            
            instrument_service = InstrumentService()
            keyword = args["keyword"]
            instrument_type = args.get("instrument_type")
            limit = args.get("limit", 50)
            
            # 构建查询条件
            where_builder = InstrumentWhereBuilder()
            
            # 关键词搜索（支持代码、名称、拼音）
            where_builder = where_builder.name_contains(keyword)
            where_builder = where_builder.code_contains(keyword)
            where_builder = where_builder.abbreviation_contains(keyword)
            
            # 类型过滤
            if instrument_type:
                try:
                    inst_type = InstrumentType(instrument_type.lower())
                    where_builder = where_builder.type_eq(inst_type)
                except ValueError:
                    pass  # 忽略无效的类型
            
            # 查询
            instruments = await instrument_service.find_all(
                where=where_builder,
                limit=limit
            )
            
            # 转换为字典格式
            results = []
            for inst in instruments:
                results.append({
                    "symbol": f"{inst.id}.{inst.market}" if inst.market else inst.id,
                    "code": inst.id,
                    "name": inst.name,
                    "type": inst.type.value if inst.type else None,
                    "market": inst.market,
                    "abbreviation": inst.abbreviation
                })
            
            return {
                "status": "success",
                "keyword": keyword,
                "count": len(results),
                "results": results
            }
            
        except Exception as e:
            logger.error(f"Error searching instruments: {e}", exc_info=True)
            return {
                "status": "error",
                "error": str(e),
                "message": f"Failed to search for '{args.get('keyword', 'unknown')}'"
            }
    
    async def _get_index_constituents(self, args: dict) -> dict:
        """Get index constituent stocks"""
        try:
            from quantx_infrastructure.models.enums import InstrumentType
            from quantx_infrastructure.repositories.instrument_where_builder import (
                InstrumentWhereBuilder,
            )
            from quantx_infrastructure.services.instrument_service import (
                InstrumentService,
            )
            
            instrument_service = InstrumentService()
            index_symbol = args["index_symbol"]
            
            # 获取指数信息
            parts = index_symbol.split(".")
            code = parts[0]
            market = parts[1] if len(parts) > 1 else None
            
            # 查询指数
            where_builder = InstrumentWhereBuilder().code_eq(code).type_eq(InstrumentType.INDEX)
            if market:
                where_builder = where_builder.market_eq(market)
            
            indices = await instrument_service.find_all(where=where_builder, limit=1)
            
            if not indices:
                return {
                    "status": "error",
                    "error": f"Index '{index_symbol}' not found"
                }
            
            index_info = indices[0]
            
            # TODO: 查询指数成分股
            # 这需要从数据库的成分股表中查询，或者通过特定的API
            # 这里先返回一个示例结构
            constituents = []
            
            # 如果有成分股数据表，应该在这里查询
            # constituents = await index_constituent_service.get_constituents(index_info.id)
            
            return {
                "status": "success",
                "index": {
                    "symbol": index_symbol,
                    "name": index_info.name,
                    "code": index_info.id
                },
                "constituents": constituents,
                "count": len(constituents),
                "message": "Constituent data not yet implemented - please integrate with index constituent data source"
            }
            
        except Exception as e:
            logger.error(f"Error getting index constituents: {e}", exc_info=True)
            return {
                "status": "error",
                "error": str(e),
                "message": f"Failed to get constituents for '{args.get('index_symbol', 'unknown')}'"
            }
    
    async def _get_sector_stocks(self, args: dict) -> dict:
        """Get stocks by sector"""
        try:
            from quantx_infrastructure.models.enums import InstrumentType
            from quantx_infrastructure.repositories.instrument_where_builder import (
                InstrumentWhereBuilder,
            )
            from quantx_infrastructure.services.instrument_service import (
                InstrumentService,
            )
            
            instrument_service = InstrumentService()
            sector_name = args["sector_name"]
            market = args.get("market")
            limit = args.get("limit", 100)
            
            # 构建查询条件 - 股票类型
            where_builder = InstrumentWhereBuilder().type_eq(InstrumentType.STOCK)
            
            if market:
                where_builder = where_builder.market_eq(market)
            
            # TODO: 行业过滤需要从行业分类表中查询
            # 这里先返回一个示例结构
            
            # 查询所有股票（实际应该按行业过滤）
            stocks = await instrument_service.find_all(
                where=where_builder,
                limit=limit
            )
            
            # 过滤行业名称（简单匹配）
            results = []
            for stock in stocks:
                # 检查股票名称或扩展名称是否包含行业关键词
                if sector_name in (stock.name or "") or sector_name in (stock.extend_name or ""):
                    results.append({
                        "symbol": f"{stock.id}.{stock.market}" if stock.market else stock.id,
                        "code": stock.id,
                        "name": stock.name,
                        "market": stock.market
                    })
            
            return {
                "status": "success",
                "sector": sector_name,
                "stocks": results[:limit],
                "count": len(results[:limit]),
                "message": "Sector filtering is basic - integrate with industry classification data for accurate results"
            }
            
        except Exception as e:
            logger.error(f"Error getting sector stocks: {e}", exc_info=True)
            return {
                "status": "error",
                "error": str(e),
                "message": f"Failed to get stocks for sector '{args.get('sector_name', 'unknown')}'"
            }
    
    async def _search_symbols(self, args: dict) -> dict:
        """Search for stock symbols by keyword"""
        return await self._search_instruments(args)


class StrategyTools:
    """Strategy related tools"""

    async def _find_strategy(self, strategy_name: str):
        from quantx_infrastructure.database.connection import get_async_db
        from quantx_infrastructure.repositories.strategy_repository import (
            StrategyRepository,
        )

        async for db in get_async_db():
            repo = StrategyRepository(db)
            strategy = await repo.find_by_name(strategy_name)
            if strategy is None:
                strategy = await repo.find_by_class_name(strategy_name)
            return strategy
        return None

    async def _queue_strategy(
        self,
        strategy_name: str,
        parameters: dict,
        *,
        mode: str,
        auto_start: bool = True,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ) -> dict:
        from quantx_infrastructure.services.engine_command_service import (
            engine_command_service,
        )

        strategy = await self._find_strategy(strategy_name)
        if strategy is None:
            raise ValueError(f"Strategy '{strategy_name}' not found")
        run_id = str(uuid.uuid4())
        normalized_mode = str(mode or "paper").lower()
        symbol = str(
            parameters.get("instrument_code")
            or parameters.get("symbol")
            or ""
        ).upper()
        instruments = [symbol] if symbol else list(parameters.get("stockCodes") or [])
        backtest_id = (
            str(uuid.uuid4()) if normalized_mode == "backtest" else None
        )
        receipt = await engine_command_service.request(
            "STRATEGY_CREATE",
            {
                "run_id": run_id,
                "backtest_id": backtest_id,
                "strategy_id": strategy.id,
                "mode": normalized_mode,
                "instruments": instruments,
                "parameters": parameters,
                "name": f"MCP-{strategy.name}",
                "auto_start": auto_start,
                "backtest_start_time": start_time,
                "backtest_end_time": end_time,
            },
            aggregate_id=run_id,
            idempotency_key=f"strategy-create:{run_id}",
        )
        if receipt.status == "FAILED":
            raise RuntimeError(receipt.error or "Engine rejected strategy command")
        return {
            "run_id": run_id,
            "command_id": receipt.message_id,
            "command_status": receipt.status,
        }
    
    def get_tools(self) -> List[Tool]:
        """Get list of strategy tools"""
        return [
            Tool(
                name="strategy_list",
                description="List all available strategies",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "status": {
                            "type": "string",
                            "description": "Filter by status (active, inactive, all)",
                            "default": "all",
                            "optional": True
                        }
                    }
                }
            ),
            Tool(
                name="strategy_get_info",
                description="Get detailed information about a specific strategy",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "strategy_name": {
                            "type": "string",
                            "description": "Strategy name or ID"
                        }
                    },
                    "required": ["strategy_name"]
                }
            ),
            Tool(
                name="strategy_execute",
                description="Execute a strategy with given parameters",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "strategy_name": {
                            "type": "string",
                            "description": "Strategy to execute"
                        },
                        "parameters": {
                            "type": "object",
                            "description": "Strategy parameters",
                            "properties": {
                                "symbol": {"type": "string"},
                                "interval": {"type": "string"},
                                "capital": {"type": "number"},
                                "additional_params": {"type": "object"}
                            }
                        },
                        "mode": {
                            "type": "string",
                            "description": "Execution mode (backtest, paper, live)",
                            "default": "paper"
                        }
                    },
                    "required": ["strategy_name", "parameters"]
                }
            ),
            Tool(
                name="strategy_start",
                description="Start an automated strategy",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "strategy_name": {
                            "type": "string",
                            "description": "Strategy to start"
                        },
                        "parameters": {
                            "type": "object",
                            "description": "Strategy configuration"
                        }
                    },
                    "required": ["strategy_name"]
                }
            ),
            Tool(
                name="strategy_stop",
                description="Stop a running strategy",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "strategy_id": {
                            "type": "string",
                            "description": "Strategy instance ID to stop"
                        }
                    },
                    "required": ["strategy_id"]
                }
            ),
            Tool(
                name="strategy_get_performance",
                description="Get performance metrics for a strategy",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "strategy_id": {
                            "type": "string",
                            "description": "Strategy instance ID"
                        },
                        "metrics": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Specific metrics to retrieve",
                            "optional": True
                        }
                    },
                    "required": ["strategy_id"]
                }
            ),
            Tool(
                name="strategy_backtest",
                description="Run backtest for a strategy",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "strategy_name": {
                            "type": "string",
                            "description": "Strategy to backtest"
                        },
                        "parameters": {
                            "type": "object",
                            "description": "Strategy parameters"
                        },
                        "start_date": {
                            "type": "string",
                            "description": "Backtest start date (YYYY-MM-DD)"
                        },
                        "end_date": {
                            "type": "string",
                            "description": "Backtest end date (YYYY-MM-DD)"
                        },
                        "initial_capital": {
                            "type": "number",
                            "description": "Initial capital for backtest",
                            "default": 1000000
                        }
                    },
                    "required": ["strategy_name", "start_date", "end_date"]
                }
            )
        ]
    
    async def handle(self, name: str, arguments: dict) -> dict:
        """Handle strategy tool calls"""
        
        if name == "strategy_list":
            return await self._list_strategies(arguments)
        
        elif name == "strategy_get_info":
            return await self._get_strategy_info(arguments)
        
        elif name == "strategy_execute":
            return await self._execute_strategy(arguments)
        
        elif name == "strategy_start":
            return await self._start_strategy(arguments)
        
        elif name == "strategy_stop":
            return await self._stop_strategy(arguments)
        
        elif name == "strategy_get_performance":
            return await self._get_performance(arguments)
        
        elif name == "strategy_backtest":
            return await self._backtest_strategy(arguments)
        
        else:
            return {"error": f"Unknown tool: {name}"}
    
    async def _list_strategies(self, args: dict) -> dict:
        """List all strategies"""
        try:
            from quantx_infrastructure.database.connection import get_async_db
            from quantx_infrastructure.repositories.strategy_repository import (
                StrategyRepository,
            )

            status_filter = args.get("status", "all")
            strategies = []
            async for db in get_async_db():
                rows = await StrategyRepository(db).get_all_strategies()
                break
            for strategy in rows:
                strategy_info = {
                    "name": strategy.name,
                    "class_name": strategy.class_name,
                    "description": strategy.description or "No description",
                    "version": strategy.version or "1.0.0",
                    "parameters": list(
                        dict(strategy.parameter_schema or {})
                        .get("properties", {})
                        .keys()
                    ),
                    "status": "available"
                }
                if status_filter in ["all", "available"]:
                    strategies.append(strategy_info)
            
            return {
                "status": "success",
                "count": len(strategies),
                "strategies": strategies
            }
            
        except Exception as e:
            logger.error(f"Error listing strategies: {e}", exc_info=True)
            return {
                "status": "error",
                "error": str(e),
                "message": "Failed to retrieve strategy list"
            }
    
    async def _get_strategy_info(self, args: dict) -> dict:
        """Get strategy info"""
        try:
            strategy_name = args["strategy_name"]
            strategy = await self._find_strategy(strategy_name)
            if not strategy:
                return {
                    "status": "error",
                    "error": f"Strategy '{strategy_name}' not found"
                }
            return {
                "status": "success",
                "name": strategy.name,
                "description": strategy.description or "No description available",
                "parameters": strategy.parameter_schema or {},
                "version": strategy.version or "1.0.0",
                "author": "QuantX",
                "class_name": strategy.class_name
            }
            
        except Exception as e:
            logger.error(f"Error getting strategy info: {e}", exc_info=True)
            return {
                "status": "error",
                "error": str(e),
                "message": f"Failed to get info for strategy '{args.get('strategy_name', 'unknown')}'"
            }
    
    async def _execute_strategy(self, args: dict) -> dict:
        """Execute strategy"""
        try:
            strategy_name = args["strategy_name"]
            parameters = args["parameters"]
            mode_str = args.get("mode", "paper")
            
            # 构建策略参数
            strategy_params = {
                "symbol": parameters.get("symbol"),
                "interval": parameters.get("interval", "1d"),
                "capital": parameters.get("capital", 1000000)
            }
            
            # 添加其他参数
            for key, value in parameters.items():
                if key not in ["symbol", "interval", "capital"]:
                    strategy_params[key] = value
            
            queued = await self._queue_strategy(
                strategy_name,
                strategy_params,
                mode=mode_str,
            )
            return {
                "status": "success",
                "result": {
                    "execution_id": queued["run_id"],
                    "strategy": strategy_name,
                    "mode": mode_str,
                    "status": queued["command_status"],
                    "command_id": queued["command_id"],
                }
            }
            
        except Exception as e:
            logger.error(f"Error executing strategy: {e}", exc_info=True)
            return {
                "status": "error",
                "error": str(e),
                "message": f"Failed to execute strategy '{args.get('strategy_name', 'unknown')}'"
            }
    
    async def _start_strategy(self, args: dict) -> dict:
        """Start automated strategy"""
        try:
            strategy_name = args["strategy_name"]
            parameters = args.get("parameters", {})
            queued = await self._queue_strategy(
                strategy_name,
                parameters,
                mode=str(args.get("mode") or "paper"),
            )
            return {
                "status": "success",
                "strategy_id": queued["run_id"],
                "strategy_name": strategy_name,
                "message": f"Strategy '{strategy_name}' queued for Engine",
                "command_id": queued["command_id"],
                "parameters": parameters
            }
            
        except Exception as e:
            logger.error(f"Error starting strategy: {e}", exc_info=True)
            return {
                "status": "error",
                "error": str(e),
                "message": f"Failed to start strategy '{args.get('strategy_name', 'unknown')}'"
            }
    
    async def _stop_strategy(self, args: dict) -> dict:
        """Stop strategy"""
        try:
            from quantx_infrastructure.services.engine_command_service import (
                engine_command_service,
            )

            strategy_id = args["strategy_id"]
            receipt = await engine_command_service.request(
                "STRATEGY_STOP",
                {"run_id": strategy_id},
                aggregate_id=strategy_id,
                idempotency_key=f"strategy-stop:{strategy_id}:{uuid.uuid4()}",
            )
            success = receipt.status == "SUCCEEDED" and bool(
                (receipt.result or {}).get("success")
            )
            if not success:
                return {
                    "status": "error",
                    "error": receipt.error or f"Failed to stop strategy with ID '{strategy_id}'"
                }
            
            return {
                "status": "success",
                "strategy_id": strategy_id,
                "message": "Strategy stopped successfully"
            }
            
        except Exception as e:
            logger.error(f"Error stopping strategy: {e}", exc_info=True)
            return {
                "status": "error",
                "error": str(e),
                "message": f"Failed to stop strategy '{args.get('strategy_id', 'unknown')}'"
            }
    
    async def _get_performance(self, args: dict) -> dict:
        """Get strategy performance"""
        try:
            from quantx_infrastructure.database.connection import get_async_db
            from quantx_infrastructure.repositories import StrategyRunRepository
            
            strategy_id = args["strategy_id"]
            metrics_filter = args.get("metrics")
            
            # 获取策略运行记录
            async for db in get_async_db():
                repo = StrategyRunRepository(db)
                strategy_run = await repo.find_run_by_id(strategy_id)
                break
            
            if not strategy_run:
                return {
                    "status": "error",
                    "error": f"Strategy run '{strategy_id}' not found"
                }
            
            # 获取执行指标
            performance = {}
            if strategy_run.metrics:
                metrics = strategy_run.metrics
                performance = {
                    "total_return": metrics.get("total_return", 0.0),
                    "sharpe_ratio": metrics.get("sharpe_ratio", 0.0),
                    "max_drawdown": metrics.get("max_drawdown", 0.0),
                    "win_rate": metrics.get("win_rate", 0.0),
                    "profit_factor": metrics.get("profit_factor", 0.0),
                    "total_trades": metrics.get("total_trades", 0),
                    "winning_trades": metrics.get("winning_trades", 0)
                }
            
            # 过滤指标
            if metrics_filter:
                performance = {k: v for k, v in performance.items() if k in metrics_filter}
            
            return {
                "status": "success",
                "strategy_id": strategy_id,
                "performance": performance,
                "run_status": strategy_run.status.value,
                "created_at": strategy_run.created_at.isoformat() if strategy_run.created_at else None
            }
            
        except Exception as e:
            logger.error(f"Error getting performance: {e}", exc_info=True)
            return {
                "status": "error",
                "error": str(e),
                "message": f"Failed to get performance for strategy '{args.get('strategy_id', 'unknown')}'"
            }
    
    async def _backtest_strategy(self, args: dict) -> dict:
        """Run strategy backtest"""
        try:
            strategy_name = args["strategy_name"]
            parameters = args.get("parameters", {})
            start_date = args["start_date"]
            end_date = args["end_date"]
            initial_capital = args.get("initial_capital", 1000000)
            
            # 构建回测参数
            backtest_params = {
                "symbol": parameters.get("symbol"),
                "interval": parameters.get("interval", "1d"),
                "start_date": start_date,
                "end_date": end_date,
                "initial_capital": initial_capital
            }
            
            # 添加其他参数
            for key, value in parameters.items():
                if key not in ["symbol", "interval"]:
                    backtest_params[key] = value
            
            queued = await self._queue_strategy(
                strategy_name,
                backtest_params,
                mode="backtest",
                start_time=datetime.fromisoformat(start_date),
                end_time=datetime.fromisoformat(end_date),
            )
            backtest_result = {
                "strategy": strategy_name,
                "period": {"start": start_date, "end": end_date},
                "initial_capital": initial_capital,
                "run_id": queued["run_id"],
                "command_id": queued["command_id"],
                "status": queued["command_status"],
            }
            
            return {
                "status": "success",
                "backtest_id": queued["run_id"],
                "result": backtest_result
            }
            
        except Exception as e:
            logger.error(f"Error running backtest: {e}", exc_info=True)
            return {
                "status": "error",
                "error": str(e),
                "message": f"Failed to run backtest for strategy '{args.get('strategy_name', 'unknown')}'"
            }


class AccountTools:
    """Account related tools"""
    
    def get_tools(self) -> List[Tool]:
        """Get list of account tools"""
        return [
            Tool(
                name="account_get_info",
                description="Get account information and balances",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "account_id": {
                            "type": "string",
                            "description": "Account ID (optional, default to current account)",
                            "optional": True
                        }
                    }
                }
            ),
            Tool(
                name="account_get_positions",
                description="Get current positions",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "account_id": {
                            "type": "string",
                            "description": "Account ID",
                            "optional": True
                        }
                    }
                }
            ),
            Tool(
                name="account_get_orders",
                description="Get order history",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "account_id": {
                            "type": "string",
                            "description": "Account ID",
                            "optional": True
                        },
                        "status": {
                            "type": "string",
                            "description": "Filter by status (filled, pending, cancelled, all)",
                            "default": "all",
                            "optional": True
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum number of orders to return",
                            "default": 100,
                            "optional": True
                        }
                    }
                }
            ),
            Tool(
                name="account_get_trades",
                description="Get trade history",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "account_id": {
                            "type": "string",
                            "description": "Account ID",
                            "optional": True
                        },
                        "start_date": {
                            "type": "string",
                            "description": "Start date (YYYY-MM-DD)",
                            "optional": True
                        },
                        "end_date": {
                            "type": "string",
                            "description": "End date (YYYY-MM-DD)",
                            "optional": True
                        }
                    }
                }
            ),
            Tool(
                name="account_get_pnl",
                description="Get profit and loss summary",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "account_id": {
                            "type": "string",
                            "description": "Account ID",
                            "optional": True
                        },
                        "period": {
                            "type": "string",
                            "description": "Time period (today, week, month, year, all)",
                            "default": "all"
                        }
                    }
                }
            )
        ]
    
    async def handle(self, name: str, arguments: dict) -> dict:
        """Handle account tool calls"""
        
        if name == "account_get_info":
            return await self._get_account_info(arguments)
        
        elif name == "account_get_positions":
            return await self._get_positions(arguments)
        
        elif name == "account_get_orders":
            return await self._get_orders(arguments)
        
        elif name == "account_get_trades":
            return await self._get_trades(arguments)
        
        elif name == "account_get_pnl":
            return await self._get_pnl(arguments)
        
        else:
            return {"error": f"Unknown tool: {name}"}
    
    async def _get_account_info(self, args: dict) -> dict:
        """Get account info"""
        try:
            from quantx_infrastructure.database.relational_connection import (
                AsyncSessionLocal,
            )
            from quantx_infrastructure.repositories.account_repository import (
                AccountRepository,
            )

            account_id = str(args.get("account_id") or "").strip()
            if not account_id:
                return {"status": "error", "error": "account_id is required"}
            async with AsyncSessionLocal() as db:
                account = await AccountRepository(db).find_by_account_id(account_id)
            if account is None:
                return {"status": "error", "error": "Account snapshot not found"}
            return {
                "status": "success",
                "account": {
                    "account_id": account.account_id,
                    "total_assets": round(float(account.total_asset or 0), 2),
                    "available_cash": round(float(account.cash or 0), 2),
                    "market_value": round(float(account.market_value or 0), 2),
                    "frozen_cash": round(float(account.frozen_cash or 0), 2),
                    "snapshot_updated_at": account.updated_at,
                },
            }

        except Exception as e:
            logger.error(f"Error getting account info: {e}", exc_info=True)
            return {
                "status": "error",
                "error": str(e),
                "message": "Failed to retrieve account information"
            }
    
    async def _get_positions(self, args: dict) -> dict:
        """Get positions"""
        try:
            from quantx_infrastructure.services.position_service import PositionService

            account_id = str(args.get("account_id") or "").strip()
            if not account_id:
                return {"status": "error", "error": "account_id is required"}
            positions = await PositionService().get_positions(account_id)
            values = [
                {
                    "symbol": position.stock_code,
                    "quantity": position.volume,
                    "available_quantity": position.can_use_volume,
                    "cost_price": float(position.avg_price or 0),
                    "market_value": float(position.market_value or 0),
                }
                for position in positions
            ]
            return {"status": "success", "positions": values, "count": len(values)}

        except Exception as e:
            logger.error(f"Error getting positions: {e}", exc_info=True)
            return {
                "status": "error",
                "error": str(e),
                "message": "Failed to retrieve positions"
            }
    
    async def _get_orders(self, args: dict) -> dict:
        """Get orders"""
        try:
            from quantx_infrastructure.services.order_service import OrderService

            account_id = str(args.get("account_id") or "").strip()
            if not account_id:
                return {"status": "error", "error": "account_id is required"}
            orders = await OrderService(account_id).get_today_orders(account_id)
            return {
                "status": "success",
                "orders": [order.to_dict() for order in orders]
            }
        except Exception as e:
            logger.error(f"Error getting orders: {e}")
            return {"status": "error", "error": str(e)}
    
    async def _get_trades(self, args: dict) -> dict:
        """Get trades"""
        try:
            from quantx_infrastructure.services.trade_service import TradeService

            account_id = str(args.get("account_id") or "").strip()
            if not account_id:
                return {"status": "error", "error": "account_id is required"}
            trades = await TradeService(account_id).get_today_trades(account_id)
            return {
                "status": "success",
                "trades": [trade.to_dict() for trade in trades]
            }
        except Exception as e:
            logger.error(f"Error getting trades: {e}")
            return {"status": "error", "error": str(e)}
    
    async def _get_pnl(self, args: dict) -> dict:
        """Get P&L"""
        return {
            "status": "unavailable",
            "message": "P&L requires reconciled valuation data; no value was fabricated",
        }


class OrderTools:
    """Order related tools"""
    
    def get_tools(self) -> List[Tool]:
        """Get list of order tools"""
        return [
            Tool(
                name="order_create",
                description="Create and submit a new order",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "symbol": {
                            "type": "string",
                            "description": "Stock symbol"
                        },
                        "side": {
                            "type": "string",
                            "description": "Order side (buy/sell)",
                            "enum": ["buy", "sell"]
                        },
                        "type": {
                            "type": "string",
                            "description": "Order type (market/limit)",
                            "enum": ["market", "limit"],
                            "default": "limit"
                        },
                        "quantity": {
                            "type": "number",
                            "description": "Order quantity"
                        },
                        "price": {
                            "type": "number",
                            "description": "Limit price (required for limit orders)",
                            "optional": True
                        },
                        "account_id": {
                            "type": "string",
                            "description": "Account ID"
                        },
                        "idempotency_key": {
                            "type": "string",
                            "description": "Stable caller key for safe retries",
                            "optional": True
                        }
                    },
                    "required": ["symbol", "side", "quantity", "account_id"]
                }
            ),
            Tool(
                name="order_cancel",
                description="Cancel an existing order",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "order_id": {
                            "type": "string",
                            "description": "Order ID to cancel"
                        },
                        "account_id": {
                            "type": "string",
                            "description": "Account ID"
                        },
                        "idempotency_key": {
                            "type": "string",
                            "description": "Stable caller key for safe retries",
                            "optional": True
                        }
                    },
                    "required": ["order_id", "account_id"]
                }
            ),
            Tool(
                name="order_modify",
                description="Modify an existing order",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "order_id": {
                            "type": "string",
                            "description": "Order ID to modify"
                        },
                        "quantity": {
                            "type": "number",
                            "description": "New quantity",
                            "optional": True
                        },
                        "price": {
                            "type": "number",
                            "description": "New limit price",
                            "optional": True
                        }
                    },
                    "required": ["order_id"]
                }
            ),
            Tool(
                name="order_get_status",
                description="Get status of an order",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "order_id": {
                            "type": "string",
                            "description": "Order ID"
                        },
                        "account_id": {
                            "type": "string",
                            "description": "Account ID"
                        }
                    },
                    "required": ["order_id", "account_id"]
                }
            )
        ]
    
    async def handle(self, name: str, arguments: dict) -> dict:
        """Handle order tool calls"""
        
        if name == "order_create":
            return await self._create_order(arguments)
        
        elif name == "order_cancel":
            return await self._cancel_order(arguments)
        
        elif name == "order_modify":
            return await self._modify_order(arguments)
        
        elif name == "order_get_status":
            return await self._get_order_status(arguments)
        
        else:
            return {"error": f"Unknown tool: {name}"}
    
    async def _create_order(self, args: dict) -> dict:
        """Create order"""
        try:
            from decimal import Decimal

            from quantx_infrastructure.database.relational_connection import (
                AsyncSessionLocal,
            )
            from quantx_infrastructure.services.trade_command_service import (
                TradeCommandService,
            )

            account_id = str(args.get("account_id") or "").strip()
            if not account_id:
                return {"status": "error", "error": "account_id is required"}
            async with AsyncSessionLocal() as db:
                queued = await TradeCommandService(db).enqueue_order_for_account(
                    account_id=account_id,
                    instrument_code=str(args["symbol"]),
                    side=str(args["side"]).upper(),
                    order_type=str(args.get("type", "limit")).upper(),
                    limit_price=Decimal(str(args.get("price") or 0)),
                    volume=int(args["quantity"]),
                    strategy_name="mcp",
                    order_remark="queued by MCP",
                    idempotency_key=str(args.get("idempotency_key") or ""),
                )
            return {
                "status": queued.status,
                "client_order_id": queued.client_order_id,
                "message": "Order command queued",
            }

        except Exception as e:
            logger.error(f"Error creating order: {e}", exc_info=True)
            return {
                "status": "error",
                "error": str(e),
                "message": f"Failed to create order for {args.get('symbol', 'unknown')}"
            }
    
    async def _cancel_order(self, args: dict) -> dict:
        """Cancel order"""
        try:
            from quantx_infrastructure.database.relational_connection import (
                AsyncSessionLocal,
            )
            from quantx_infrastructure.services.trade_command_service import (
                TradeCommandService,
            )

            account_id = str(args.get("account_id") or "").strip()
            if not account_id:
                return {"status": "error", "error": "account_id is required"}
            async with AsyncSessionLocal() as db:
                queued = await TradeCommandService(db).enqueue_cancel_for_account(
                    account_id=account_id,
                    broker_order_id=str(args["order_id"]),
                    idempotency_key=str(args.get("idempotency_key") or ""),
                )
            return {
                "status": queued.status,
                "client_order_id": queued.client_order_id,
                "message": "Cancel command queued",
            }
        except Exception as e:
            logger.error(f"Error cancelling order: {e}", exc_info=True)
            return {
                "status": "error",
                "error": str(e),
                "message": f"Failed to cancel order '{args.get('order_id', 'unknown')}'"
            }
    
    async def _modify_order(self, args: dict) -> dict:
        """Modify order"""
        return {
            "status": "rejected",
            "order_id": args["order_id"],
            "message": "Modify is not a supported TradeCommand",
        }
    
    async def _get_order_status(self, args: dict) -> dict:
        """Read the latest reconciled order state from PostgreSQL."""
        try:
            from quantx_infrastructure.services.order_service import OrderService

            account_id = str(args.get("account_id") or "").strip()
            if not account_id:
                return {"status": "error", "error": "account_id is required"}
            raw_order_id = str(args.get("order_id") or "").strip()
            if not raw_order_id.isdigit():
                return {"status": "error", "error": "order_id must be numeric"}
            order = await OrderService(account_id).get_order_by_id(int(raw_order_id))
            if order is None:
                return {"status": "missing", "order_id": raw_order_id}
            return {
                "status": "success",
                "order_id": raw_order_id,
                "order_status": getattr(order.order_status, "name", str(order.order_status)),
                "traded_volume": int(order.traded_volume or 0),
                "traded_price": float(order.traded_price or 0),
            }
        except Exception as e:
            logger.error(f"Error getting order status: {e}")
            return {"status": "error", "error": str(e)}


class AnalysisTools:
    """Analysis related tools"""
    
    def get_tools(self) -> List[Tool]:
        """Get list of analysis tools"""
        return [
            Tool(
                name="analysis_calculate_indicators",
                description="Calculate technical indicators for a symbol",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "symbol": {
                            "type": "string",
                            "description": "Stock symbol"
                        },
                        "indicators": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "List of indicators to calculate (MA, EMA, MACD, RSI, Bollinger, etc)"
                        },
                        "period": {
                            "type": "string",
                            "description": "Data period (1d, 1h, etc)",
                            "default": "1d"
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Number of data points",
                            "default": 100
                        }
                    },
                    "required": ["symbol", "indicators"]
                }
            ),
            Tool(
                name="analysis_scan_market",
                description="Scan market for trading opportunities",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "market": {
                            "type": "string",
                            "description": "Market to scan (SZ, SH, or all)",
                            "default": "all"
                        },
                        "criteria": {
                            "type": "object",
                            "description": "Scanning criteria (e.g., volume surge, price breakout)"
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum results to return",
                            "default": 50
                        }
                    }
                }
            ),
            Tool(
                name="analysis_backtest",
                description="Run a backtest analysis",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "strategy": {
                            "type": "string",
                            "description": "Strategy name or parameters"
                        },
                        "symbol": {
                            "type": "string",
                            "description": "Symbol to backtest"
                        },
                        "start_date": {
                            "type": "string",
                            "description": "Start date (YYYY-MM-DD)"
                        },
                        "end_date": {
                            "type": "string",
                            "description": "End date (YYYY-MM-DD)"
                        }
                    },
                    "required": ["strategy", "symbol", "start_date", "end_date"]
                }
            ),
            Tool(
                name="analysis_get_research_report",
                description="Generate research report for a symbol",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "symbol": {
                            "type": "string",
                            "description": "Stock symbol"
                        },
                        "report_type": {
                            "type": "string",
                            "description": "Type of report (technical, fundamental, comprehensive)",
                            "default": "comprehensive"
                        }
                    },
                    "required": ["symbol"]
                }
            ),
            Tool(
                name="analysis_compare_symbols",
                description="Compare multiple symbols",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "symbols": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "List of symbols to compare"
                        },
                        "metrics": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Metrics to compare (P/E, market cap, technical indicators, etc)"
                        }
                    },
                    "required": ["symbols"]
                }
            )
        ]
    
    async def handle(self, name: str, arguments: dict) -> dict:
        """Handle analysis tool calls"""
        
        if name == "analysis_calculate_indicators":
            return await self._calculate_indicators(arguments)
        
        elif name == "analysis_scan_market":
            return await self._scan_market(arguments)
        
        elif name == "analysis_backtest":
            return await self._backtest(arguments)
        
        elif name == "analysis_get_research_report":
            return await self._get_research_report(arguments)
        
        elif name == "analysis_compare_symbols":
            return await self._compare_symbols(arguments)
        
        else:
            return {"error": f"Unknown tool: {name}"}
    
    async def _calculate_indicators(self, args: dict) -> dict:
        """Calculate technical indicators"""
        try:
            # TODO: Implement actual indicator calculation
            return {
                "status": "success",
                "symbol": args["symbol"],
                "indicators": {}
            }
        except Exception as e:
            logger.error(f"Error calculating indicators: {e}")
            return {"status": "error", "error": str(e)}
    
    async def _scan_market(self, args: dict) -> dict:
        """Scan market"""
        try:
            # TODO: Implement actual market scan
            return {
                "status": "success",
                "opportunities": []
            }
        except Exception as e:
            logger.error(f"Error scanning market: {e}")
            return {"status": "error", "error": str(e)}
    
    async def _backtest(self, args: dict) -> dict:
        """Run backtest"""
        try:
            # TODO: Implement actual backtest
            return {
                "status": "success",
                "result": {}
            }
        except Exception as e:
            logger.error(f"Error running backtest: {e}")
            return {"status": "error", "error": str(e)}
    
    async def _get_research_report(self, args: dict) -> dict:
        """Get research report"""
        try:
            # TODO: Implement actual report generation
            return {
                "status": "success",
                "symbol": args["symbol"],
                "report": {}
            }
        except Exception as e:
            logger.error(f"Error generating report: {e}")
            return {"status": "error", "error": str(e)}
    
    async def _compare_symbols(self, args: dict) -> dict:
        """Compare symbols"""
        try:
            # TODO: Implement actual symbol comparison
            return {
                "status": "success",
                "comparison": {}
            }
        except Exception as e:
            logger.error(f"Error comparing symbols: {e}")
            return {"status": "error", "error": str(e)}
