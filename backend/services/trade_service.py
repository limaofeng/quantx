"""
成交服务
处理成交记录相关的业务逻辑
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from xtquant.xttype import XtTrade

from database.connection import get_async_db
from database.relational_base import BulkSaveResult
from miniqmt.manager_registry import XTTradingManagerRegistry
from models import Trade
from models.enums import AccountType, OrderType
from repositories.trade_repository import TradeRepository
from core.utils import time_utils

trading_registry = XTTradingManagerRegistry()


class TradeService:
  """成交服务类"""

  def __init__(self, account_id: Optional[str] = None):
    self.account_id = account_id
    self.trading_manager = (
      trading_registry.get_manager(account_id, reconnect=False)
      if account_id
      else None
    )

  async def get_today_trades(self, account_id: str) -> List[Trade]:
    """从 trading_manager 获取当日成交"""
    manager = self.trading_manager
    if manager is None or self.account_id != account_id:
      manager = trading_registry.get_manager(account_id, reconnect=False)
    if not manager:
      return []

    xt_trades = manager.get_trades()
    return [self._convert_xt_trade(xt_trade) for xt_trade in xt_trades]

  async def upsert_xt_trade(self, xt_trade: XtTrade) -> Trade:
    """Persist a broker trade idempotently before downstream reconciliation."""
    trade = self._convert_xt_trade(xt_trade)
    async for db in get_async_db():
      repository = TradeRepository(db)
      saved = await repository.save(trade)
      return saved
    raise RuntimeError("成交数据库不可用")

  async def get_history_trades(
    self, account_id: str, start_date: str, end_date: str
  ) -> List[Trade]:
    """从数据库获取历史成交"""
    async for db in get_async_db():
      trade_repo = TradeRepository(db)
      return await trade_repo.find_all_by_date_range(start_date, end_date, account_id)

  async def get_trades_by_order_id(self, order_id: int) -> List[Trade]:
    """获取指定订单的成交明细(智能查询)"""
    if self.trading_manager:
      try:
        xt_trades = self.trading_manager.get_trades()
        today_trades = [
          self._convert_xt_trade(t) for t in xt_trades if t.order_id == order_id
        ]
        if today_trades:
          return today_trades
      except Exception:
        pass

    async for db in get_async_db():
      trade_repo = TradeRepository(db)
      return await trade_repo.find_all_by_order_id(order_id)

  async def get_trade_by_id(self, trade_id: str) -> Optional[Trade]:
    """根据成交ID获取成交记录"""
    async for db in get_async_db():
      trade_repo = TradeRepository(db)
      return await trade_repo.find_by_trade_id(trade_id)

  async def save_trades(self, trades: List[Trade]) -> BulkSaveResult:
    """批量保存成交数据"""
    async for db in get_async_db():
      trade_repo = TradeRepository(db)
      return await trade_repo.bulk_save(trades)

  async def get_trades_by_date(
    self, trade_date: str, account_id: str = None
  ) -> List[Trade]:
    """根据交易日期获取成交记录"""
    async for db in get_async_db():
      trade_repo = TradeRepository(db)
      return await trade_repo.find_all_by_date(trade_date, account_id)

  async def get_trades_by_account(
    self, account_id: str, limit: int = 100
  ) -> List[Trade]:
    """获取账户成交记录"""
    async for db in get_async_db():
      trade_repo = TradeRepository(db)
      return await trade_repo.find_all_by_account(account_id, limit=limit)

  async def get_trade_summary(
    self, account_id: str, trade_date: str = None
  ) -> Dict[str, Any]:
    """获取成交汇总信息"""
    if not trade_date:
      trade_date = time_utils.now().strftime("%Y-%m-%d")

    async for db in get_async_db():
      trade_repo = TradeRepository(db)
      trades = await trade_repo.find_all_by_date(trade_date, account_id)

      buy_amount = sum(
        float(t.amount) for t in trades if int(t.order_type) == int(OrderType.BUY)
      )
      sell_amount = sum(
        float(t.amount) for t in trades if int(t.order_type) == int(OrderType.SELL)
      )

      return {
        "trade_date": trade_date,
        "account_id": account_id,
        "total_trades": len(trades),
        "buy_count": len(
          [t for t in trades if int(t.order_type) == int(OrderType.BUY)]
        ),
        "sell_count": len(
          [t for t in trades if int(t.order_type) == int(OrderType.SELL)]
        ),
        "buy_amount": buy_amount,
        "sell_amount": sell_amount,
        "net_amount": buy_amount - sell_amount,
        "trades": [t.to_dict() for t in trades],
      }

  def _convert_xt_trade(self, xt_trade: XtTrade) -> Trade:
    """XtTrade → Trade Model"""
    return Trade(
      id=xt_trade.traded_id,
      account_id=xt_trade.account_id,
      account_type=AccountType.from_int(xt_trade.account_type),
      stock_code=xt_trade.stock_code,
      order_id=xt_trade.order_id,
      order_sysid=xt_trade.order_sysid,
      order_type=xt_trade.order_type,
      time=time_utils.to_shanghai(
        datetime.fromtimestamp(xt_trade.traded_time, timezone.utc)
      ),
      price=xt_trade.traded_price,
      volume=xt_trade.traded_volume,
      amount=xt_trade.traded_amount,
      strategy_name=xt_trade.strategy_name,
      order_remark=xt_trade.order_remark,
      direction=getattr(xt_trade, "direction", None),
      offset_flag=getattr(xt_trade, "offset_flag", None),
    )

  def _validate_trade_data(self, trade_data: Dict[str, Any]) -> None:
    """验证成交数据"""
    required_fields = [
      "account_id",
      "stock_code",
      "order_type",
      "traded_price",
      "traded_volume",
      "traded_amount",
      "traded_time",
    ]

    for field in required_fields:
      if field not in trade_data or trade_data[field] is None:
        raise ValueError(f"缺少必需字段: {field}")

    # 验证委托类型
    if int(trade_data["order_type"]) not in [int(OrderType.BUY), int(OrderType.SELL)]:
      raise ValueError(f"无效的委托类型: {trade_data['order_type']}")

    # 验证数值类型
    try:
      float(trade_data["traded_price"])
      int(trade_data["traded_volume"])
      float(trade_data["traded_amount"])
    except (ValueError, TypeError):
      raise ValueError("价格、数量、金额必须为数值类型")

    # 验证交易时间
    if not isinstance(trade_data["traded_time"], (datetime, int, float)):
      raise ValueError("交易时间必须为datetime或时间戳")
