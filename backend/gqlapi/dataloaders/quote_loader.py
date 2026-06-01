"""
Quote DataLoader 实现
用于批量加载股票行情数据,解决 N+1 查询问题
"""

from typing import List

from strawberry.dataloader import DataLoader

from core.data.market_data_service import market_data_service
from gqlapi.types.market_data_types import StockQuote


async def load_quotes(codes: List[str]) -> List[StockQuote]:
  """批量加载股票行情数据"""
  # 调用批量接口
  tick_data_map = await market_data_service.get_latest_prices(codes)

  # 保持顺序一致,将 Tick 转换为 StockQuote
  return [
    StockQuote.from_tick(tick_data_map[code]) if code in tick_data_map else None
    for code in codes
  ]


# 创建 DataLoader 实例
quote_loader = DataLoader(load_fn=load_quotes)
