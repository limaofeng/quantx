"""GraphQL fields for the existing-position intraday T assistant."""

from datetime import datetime
from typing import List, Optional

import strawberry

from ..resolvers.t_trade import TTradeResolver
from ..types.t_trade_types import (
  TTradeGlobalMonitor,
  TTradeGlobalMutationResult,
  TTradeGlobalSettingsInput,
  TTradeExternalEntryInput,
  TTradeImportedEntry,
  TTradeMutationResult,
  TTradeReplay,
  TTradeReplayCyclePage,
  TTradeReplayMutationResult,
  TTradeReplayPreparation,
  TTradeReplayStartInput,
  TTradeSession,
  TTradeStartInput,
)


@strawberry.type(description="持仓做 T 查询")
class TTradeQuery:
  @strawberry.field(description="查询已纳入自动卖出的来源成交台账")
  async def t_trade_imported_entries(self, account_id: str) -> List[TTradeImportedEntry]:
    return await TTradeResolver.list_imported_entries(account_id)

  @strawberry.field(description="查询账户级全局持仓做 T 监控")
  async def t_trade_global_monitor(
    self, account_id: str
  ) -> TTradeGlobalMonitor:
    return await TTradeResolver.get_global_monitor(account_id)

  @strawberry.field(description="查询单个做 T 会话")
  async def t_trade_session(
    self, run_id: str, stock_code: Optional[str] = None
  ) -> Optional[TTradeSession]:
    return await TTradeResolver.get_session(run_id, stock_code)

  @strawberry.field(description="查询做 T 会话列表")
  async def t_trade_sessions(
    self,
    account_id: Optional[str] = None,
    stock_code: Optional[str] = None,
    active_only: bool = False,
  ) -> List[TTradeSession]:
    return await TTradeResolver.list_sessions(account_id, stock_code, active_only)

  @strawberry.field(description="读取做 T 历史回放所需的初始账户快照")
  async def t_trade_replay_preparation(
    self, account_id: str, start_time: datetime
  ) -> TTradeReplayPreparation:
    return await TTradeResolver.prepare_replay(account_id, start_time)

  @strawberry.field(description="查询单个做 T 历史回放")
  async def t_trade_replay(self, run_id: str) -> Optional[TTradeReplay]:
    return await TTradeResolver.get_replay(run_id)

  @strawberry.field(description="查询账户做 T 历史回放记录")
  async def t_trade_replay_history(
    self, account_id: str, limit: int = 20
  ) -> List[TTradeReplay]:
    return await TTradeResolver.replay_history(account_id, limit)

  @strawberry.field(description="分页查询做 T 历史回放批次")
  async def t_trade_replay_cycles(
    self, run_id: str, offset: int = 0, limit: int = 50
  ) -> TTradeReplayCyclePage:
    return await TTradeResolver.replay_cycles(run_id, offset, limit)


@strawberry.type(description="持仓做 T 操作")
class TTradeMutation:
  @strawberry.mutation(description="保存并协调全局持仓做 T 监控")
  async def save_t_trade_global_monitor(
    self, input: TTradeGlobalSettingsInput
  ) -> TTradeGlobalMutationResult:
    return await TTradeResolver.save_global_monitor(input)

  @strawberry.mutation(description="立即重新同步全局做 T 持仓")
  async def reconcile_t_trade_global_monitor(
    self, account_id: str
  ) -> TTradeGlobalMutationResult:
    return await TTradeResolver.reconcile_global_monitor(account_id)

  @strawberry.mutation(description="启动持仓做 T 会话")
  async def start_t_trade_session(
    self, input: TTradeStartInput
  ) -> TTradeMutationResult:
    return await TTradeResolver.start_session(input)

  @strawberry.mutation(description="确认做 T 买入信号")
  async def approve_t_trade_entry(
    self, run_id: str, intent_id: str
  ) -> TTradeMutationResult:
    return await TTradeResolver.approve_entry(run_id, intent_id)

  @strawberry.mutation(description="忽略做 T 买入信号")
  async def reject_t_trade_entry(
    self, run_id: str, intent_id: str
  ) -> TTradeMutationResult:
    return await TTradeResolver.reject_entry(run_id, intent_id)

  @strawberry.mutation(description="导入外部已成交买单并启用做 T 自动退出")
  async def import_t_trade_external_entry(
    self, input: TTradeExternalEntryInput
  ) -> TTradeMutationResult:
    return await TTradeResolver.import_external_entry(input)

  @strawberry.mutation(description="同步 miniQMT 当日委托到委托表")
  async def sync_t_trade_source_orders(
    self, account_id: str
  ) -> TTradeMutationResult:
    return await TTradeResolver.sync_source_orders(account_id)

  @strawberry.mutation(description="安全停止做 T 会话")
  async def stop_t_trade_session(self, run_id: str) -> TTradeMutationResult:
    return await TTradeResolver.stop_session(run_id)

  @strawberry.mutation(description="启动隔离的做 T 历史回放")
  async def start_t_trade_replay(
    self, input: TTradeReplayStartInput
  ) -> TTradeReplayMutationResult:
    return await TTradeResolver.start_replay(input)

  @strawberry.mutation(description="取消正在执行的做 T 历史回放")
  async def cancel_t_trade_replay(
    self, run_id: str
  ) -> TTradeReplayMutationResult:
    return await TTradeResolver.cancel_replay(run_id)
