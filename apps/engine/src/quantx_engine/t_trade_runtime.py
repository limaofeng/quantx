"""Engine-owned account-level T-trade monitor instance."""

from quantx_engine.strategy_manager import strategy_manager
from quantx_engine.t_trade_global_monitor import (
  TTradeGlobalMonitorService,
)

t_trade_global_monitor = TTradeGlobalMonitorService(strategy_manager)

__all__ = ["t_trade_global_monitor"]
