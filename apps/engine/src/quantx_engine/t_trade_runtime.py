"""Engine-owned account-level T-trade monitor instance."""

from quantx_engine.strategy_manager import strategy_manager
from quantx_engine.t_assistant_live_supervisor import TAssistantLiveSupervisor
from quantx_engine.t_assistant_paper_shadow_supervisor import (
  TAssistantPaperShadowSupervisor,
)
from quantx_engine.t_trade_global_monitor import (
  TTradeGlobalMonitorService,
)

t_assistant_live_supervisor = TAssistantLiveSupervisor()
t_assistant_paper_shadow_supervisor = TAssistantPaperShadowSupervisor()
t_trade_global_monitor = TTradeGlobalMonitorService(
  strategy_manager,
  paper_shadow_supervisor=t_assistant_paper_shadow_supervisor,
  live_supervisor=t_assistant_live_supervisor,
)

__all__ = [
  "t_assistant_live_supervisor",
  "t_assistant_paper_shadow_supervisor",
  "t_trade_global_monitor",
]
