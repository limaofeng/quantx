"""Singleton account-level limit-up board assistant coordinator."""

from quantx_engine.limit_up_board_assistant import LimitUpBoardAssistantService
from quantx_engine.strategy_manager import strategy_manager

limit_up_board_assistant = LimitUpBoardAssistantService(strategy_manager)

__all__ = ["limit_up_board_assistant"]
