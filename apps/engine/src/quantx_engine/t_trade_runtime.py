"""Engine-owned account-level T-trade monitor instance."""

from quantx_engine.strategy_manager import strategy_manager
from quantx_engine.t_assistant_paper_shadow_supervisor import (
  TAssistantPaperShadowSupervisor,
)
from quantx_engine.t_trade_global_monitor import (
  TTradeGlobalMonitorService,
)

t_assistant_paper_shadow_supervisor = TAssistantPaperShadowSupervisor()
t_trade_global_monitor = TTradeGlobalMonitorService(
  strategy_manager,
  paper_shadow_supervisor=t_assistant_paper_shadow_supervisor,
)

__all__ = [
  "t_assistant_paper_shadow_supervisor",
  "t_trade_global_monitor",
]
