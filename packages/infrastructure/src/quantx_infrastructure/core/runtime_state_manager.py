"""
策略运行时状态管理器

负责管理策略运行过程中的状态持久化和恢复，包括：
- 日志持久化（JSONL 文件存储）
- 资金与自定义状态持久化（StrategyRunState 表）
- 持仓持久化（StrategyRunPosition 表）
- 订单、成交和策略算法状态持久化
"""

import asyncio
import json
import logging
import os
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import TYPE_CHECKING, Any, Dict, Optional

from quantx_infrastructure.core.utils import time_utils

if TYPE_CHECKING:
    from quantx_infrastructure.core.backtest_result_storage import (
        BacktestResultStorage,
    )

try:
    import aiofiles
except ModuleNotFoundError:  # pragma: no cover - lightweight test environments
    aiofiles = None


BUCKET_LEDGER_CUSTOM_STATE_KEY = "bucket_ledger_snapshot"
APPLIED_CORPORATE_ACTIONS_KEY = "applied_corporate_actions"
GRID_BOOK_CUSTOM_STATE_KEY = "grid_book_snapshot"


@dataclass
class RuntimeStateManager:
    """策略运行时状态管理器"""

    run_id: str

    # 配置
    snapshot_interval: float = 10.0  # 快照间隔（秒）
    persist_enabled: bool = True  # 是否启用持久化
    log_dir: str = "logs/strategy"  # 日志存储目录
    enable_reserve: bool = False  # 是否启用资金冻结逻辑

    # 回测模式配置
    is_backtest: bool = False  # 是否为回测模式
    backtest_id: Optional[str] = None  # 回测记录ID (StrategyBacktest.id)
    _backtest_storage: Optional["BacktestResultStorage"] = field(default=None, repr=False)

    # 内存状态缓存
    _state: Dict[str, Any] = field(default_factory=lambda: {
        "version": 0,
        "positions": {},       # {code: PositionDict}
        "custom": {},          # 自定义状态
        "account": {
            "cash": 0.0,
            "frozen_cash": 0.0,
            "total_asset": 0.0,
        },
        "bucket_ledger": {},
        "decision_traces": [],
        "trade_intents": {},
        "last_updated": None,
    }, repr=False)

    # 标记是否有未保存的更改
    _dirty: bool = field(default=False, repr=False)

    # 资金冻结（不持久化）
    _reservations: Dict[str, float] = field(default_factory=dict, repr=False)
    _position_reservations: Dict[str, Dict[str, int]] = field(
        default_factory=dict, repr=False
    )
    _bucket_ledger: Any = field(default=None, repr=False)
    _decision_trace_logger: Any = field(default=None, repr=False)

    # 后台任务
    _snapshot_task: Optional[asyncio.Task] = field(default=None, repr=False)
    _running: bool = field(default=False, repr=False)
    _state_queue: Optional[asyncio.Queue] = field(default=None, repr=False)
    _state_sync_task: Optional[asyncio.Task] = field(default=None, repr=False)

    # 文件句柄
    _log_file_path: Optional[str] = field(default=None, repr=False)

    # 日志器
    logger: logging.Logger = field(
        default_factory=lambda: logging.getLogger("RuntimeStateManager"),
        repr=False,
    )

    def __post_init__(self):
        from quantx_domain.trading.bucket_ledger import BucketLedger
        from quantx_domain.trading.decision_trace import DecisionTraceLogger

        self.logger = logging.getLogger(f"StateManager-{self.run_id[:8]}")
        self._bucket_ledger = BucketLedger(run_id=self.run_id)
        self._decision_trace_logger = DecisionTraceLogger()
        
        # 确保日志目录存在
        if self.persist_enabled:
            os.makedirs(self.log_dir, exist_ok=True)
            self._log_file_path = os.path.join(self.log_dir, f"{self.run_id}.jsonl")

    # ==================== 生命周期 ====================

    async def start(self) -> None:
        """启动状态管理器"""
        if self._running:
            return

        self._running = True

        # 启动后台快照任务
        if self.persist_enabled:
            self._snapshot_task = asyncio.create_task(self._snapshot_loop())
            self.logger.info(f"状态管理器已启动: {self.run_id}")

    async def stop(self) -> None:
        """停止状态管理器"""
        self._running = False

        # 取消后台任务
        if self._snapshot_task and not self._snapshot_task.done():
            self._snapshot_task.cancel()
            try:
                await self._snapshot_task
            except asyncio.CancelledError:
                pass

        # 最后一次保存
        await self.save_snapshot()

        self.logger.info(f"状态管理器已停止: {self.run_id}")

    async def start_state_sync(self, strategy) -> None:
        """启动策略状态同步任务（通过订阅事件持久化）"""
        if not strategy or not hasattr(strategy, "subscribe_state"):
            return

        if self._state_sync_task and not self._state_sync_task.done():
            return

        self._state_queue = strategy.subscribe_state()
        self._state_sync_task = asyncio.create_task(
            self._state_sync_loop(),
            name=f"state-sync-{self.run_id[:8]}",
        )

    async def stop_state_sync(self, strategy=None) -> None:
        """停止策略状态同步任务"""
        if self._state_sync_task and not self._state_sync_task.done():
            self._state_sync_task.cancel()
            try:
                await self._state_sync_task
            except asyncio.CancelledError:
                pass
        self._state_sync_task = None

        if self._state_queue and strategy and hasattr(strategy, "unsubscribe_state"):
            strategy.unsubscribe_state(self._state_queue)
        self._state_queue = None

    async def _state_sync_loop(self) -> None:
        """监听策略状态事件并同步到持久化层"""
        queue = self._state_queue
        if not queue:
            return

        while self._running:
            try:
                event = await queue.get()
            except asyncio.CancelledError:
                break

            try:
                persist = getattr(event, "persist", True)
                if not persist:
                    continue

                changes = getattr(event, "changes", None)
                key = getattr(event, "key", None)
                value = getattr(event, "value", None)

                if changes:
                    self.update_custom_state(changes)
                elif key is not None:
                    self.set_custom(key, value)
            except Exception as e:
                self.logger.error(f"策略状态同步失败: {e}")
            finally:
                try:
                    queue.task_done()
                except ValueError:
                    pass

    async def restore(self) -> Dict[str, Any]:
        """从数据库恢复状态"""
        if not self.persist_enabled:
            return self._state

        try:
            from quantx_infrastructure.database.connection import get_async_db
            from quantx_infrastructure.repositories.strategy_run_state_repository import (
                StrategyRunPositionRepository,
                StrategyRunStateRepository,
            )

            async for db in get_async_db():
                # 1. 恢复资金与自定义状态
                state_repo = StrategyRunStateRepository(db)
                state_record = await state_repo.get_state(self.run_id)
                restored_ledger = False

                if state_record:
                    self._state["version"] = state_record.version
                    self._state["custom"] = dict(state_record.custom_state or {})
                    self._state["account"] = {
                        "cash": state_record.cash,
                        "frozen_cash": state_record.frozen_cash,
                        "total_asset": state_record.total_asset,
                    }
                    ledger_snapshot = self._state["custom"].get(BUCKET_LEDGER_CUSTOM_STATE_KEY)
                    if ledger_snapshot:
                        from quantx_domain.trading.bucket_ledger import BucketLedger

                        self._bucket_ledger = BucketLedger.from_dict(ledger_snapshot)
                        if not self._bucket_ledger.run_id:
                            self._bucket_ledger.run_id = self.run_id
                        restored_ledger = True

                # 2. 恢复持仓
                pos_repo = StrategyRunPositionRepository(db)
                positions = await pos_repo.get_all_positions(self.run_id)
                self._state["positions"] = {
                    p.instrument_code: p.to_dict() for p in positions
                }
                if restored_ledger:
                    violations = self._bucket_ledger.validate_invariants(
                        self._state["positions"]
                    )
                    if violations:
                        self._state["custom"]["bucket_ledger_reconcile_required"] = True
                        self._state["custom"]["bucket_ledger_violations"] = violations
                    self._hydrate_positions_from_bucket_ledger()
                    self._state["bucket_ledger"] = self._bucket_ledger.to_dict()
                else:
                    for code, position in self._state["positions"].items():
                        self._bucket_ledger.sync_position(code, position)
                    self._state["bucket_ledger"] = self.get_bucket_ledger_snapshot()

                self.logger.info(f"状态恢复完成: positions={len(positions)}")
                break
            
            # 恢复日志路径
            self._state["log_file"] = self._log_file_path

        except Exception as e:
            self.logger.error(f"状态恢复失败: {e}")

        return self._state

    async def restore_manual_trade_intent(self, intent_id: str):
        """Rebuild one still-pending manual intent from its durable record."""
        if not self.persist_enabled or not intent_id:
            return None

        try:
            from quantx_domain.strategies.base import (
                TradeIntent,
                TradeIntentExecutionMode,
            )

            from quantx_infrastructure.database.connection import get_async_db
            from quantx_infrastructure.repositories.trade_intent_repository import (
                TradeIntentRepository,
            )

            async for db in get_async_db():
                record = await TradeIntentRepository(db).find_by_id(intent_id)
                if record is None or str(record.status or "").upper() != "AWAITING_APPROVAL":
                    return None

                data = record.to_dict()
                metadata = dict(data.get("metadata") or {})
                created_at_raw = metadata.get("intent_created_at")
                created_at = (
                    datetime.fromisoformat(created_at_raw)
                    if isinstance(created_at_raw, str) and created_at_raw
                    else record.created_at
                )
                intent = TradeIntent(
                    strategy_id=str(record.strategy_id or ""),
                    run_id=str(record.strategy_run_id),
                    instrument_code=str(record.instrument_code),
                    direction=str(record.direction),
                    bucket=str(record.bucket or "core"),
                    reason=str(record.reason or ""),
                    priority=str(record.priority or "NORMAL"),
                    intent_type=str(record.intent_type) if record.intent_type else None,
                    confidence=float(record.confidence or 0.0),
                    target_amount=record.target_amount,
                    target_position_pct=record.target_position_pct,
                    target_volume=record.target_volume,
                    limit_price_hint=record.limit_price_hint,
                    execution_mode=TradeIntentExecutionMode.MANUAL_CONFIRM,
                    approval_ttl_ms=metadata.get("approval_ttl_ms"),
                    max_price_deviation_bps=metadata.get("max_price_deviation_bps"),
                    metadata=metadata,
                    trace_id=record.trace_id,
                    intent_id=str(record.id),
                    created_at=created_at or time_utils.now(),
                )
                self._state.setdefault("trade_intents", {})[intent_id] = data
                return intent
        except Exception as e:
            self.logger.error(f"恢复人工确认交易意图失败: intent_id={intent_id}, error={e}")
        return None

    # ==================== 快照管理 ====================

    async def save_snapshot(self) -> bool:
        """保存状态快照到数据库"""
        if not self.persist_enabled or not self._dirty:
            return True

        try:
            from quantx_infrastructure.database.connection import get_async_db
            from quantx_infrastructure.repositories.strategy_run_state_repository import (
                StrategyRunPositionRepository,
                StrategyRunStateRepository,
            )

            self._state["last_updated"] = time_utils.now().isoformat()
            custom_state = dict(self._state.get("custom", {}) or {})
            custom_state[BUCKET_LEDGER_CUSTOM_STATE_KEY] = self.get_bucket_ledger_snapshot()
            
            async for db in get_async_db():
                # 1. 保存资金与自定义状态
                state_repo = StrategyRunStateRepository(db)
                await state_repo.upsert_state(
                    run_id=self.run_id,
                    cash=self._state["account"].get("cash", 0.0),
                    frozen_cash=self._state["account"].get("frozen_cash", 0.0),
                    total_asset=self._state["account"].get("total_asset", 0.0),
                    custom_state=custom_state,
                    expected_version=self._state.get("version"),
                )
                
                # 2. 保存所有持仓（简单起见，逐个保存，可优化为批量）
                pos_repo = StrategyRunPositionRepository(db)
                positions = self._state.get("positions", {})
                for code, pos_data in positions.items():
                    await pos_repo.update_position(
                        run_id=self.run_id,
                        instrument_code=code,
                        long_volume=pos_data.get("long_volume", 0),
                        short_volume=pos_data.get("short_volume", 0),
                        long_avg_price=pos_data.get("long_avg_price", 0.0),
                        short_avg_price=pos_data.get("short_avg_price", 0.0),
                        market_value=pos_data.get("market_value", 0.0),
                        pnl=pos_data.get("pnl", 0.0),
                        last_price=pos_data.get("last_price", 0.0),
                    )
                
                self._dirty = False
                self.logger.debug(f"状态快照已保存: v{self._state.get('version')}")
                break

            return True

        except Exception as e:
            self.logger.error(f"保存快照失败: {e}")
            return False

    async def _snapshot_loop(self) -> None:
        """后台快照循环"""
        while self._running:
            try:
                await asyncio.sleep(self.snapshot_interval)
                if self._dirty:
                    await self.save_snapshot()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"快照循环异常: {e}")

    def _mark_dirty(self) -> None:
        """标记状态已更改"""
        self._dirty = True

    # ==================== 日志管理 (文件存储) ====================

    def append_log(
        self,
        level: str,
        message: str,
        source: str = "strategy",
        timestamp: Optional[datetime] = None,
    ) -> None:
        """追加日志（异步写入文件）"""
        if not self._log_file_path:
            return

        entry = {
            "timestamp": (timestamp or time_utils.now()).isoformat(),
            "level": level,
            "message": message,
            "source": source,
        }
        
        asyncio.create_task(self._write_log_to_file(entry))
        
    async def _write_log_to_file(self, entry: Dict[str, Any]) -> None:
        try:
            line = json.dumps(entry, ensure_ascii=False) + '\n'
            if aiofiles is None:
                def _write_sync() -> None:
                    with open(self._log_file_path, mode='a', encoding='utf-8') as f:
                        f.write(line)

                await asyncio.to_thread(_write_sync)
                return
            async with aiofiles.open(self._log_file_path, mode='a', encoding='utf-8') as f:
                await f.write(line)
        except Exception as e:
            print(f"写入日志文件失败: {e}")

    def get_log_file_path(self) -> Optional[str]:
        """获取当前运行实例的日志文件路径。"""
        return self._log_file_path

    # ==================== 持仓管理 ====================

    def update_position(
        self,
        instrument_code: str,
        **position_data,
    ) -> None:
        """更新持仓（同步）"""
        from quantx_domain.trading.portfolio_state import ensure_position_dict

        positions = self._state.get("positions", {})
        positions[instrument_code] = ensure_position_dict(
            instrument_code,
            {
                "instrument_code": instrument_code,
                **position_data,
            },
        )
        self._state["positions"] = positions
        if self._bucket_ledger:
            self._bucket_ledger.sync_position(instrument_code, positions[instrument_code])
            self._state["bucket_ledger"] = self.get_bucket_ledger_snapshot()
        self._mark_dirty()

    def get_position(self, instrument_code: str) -> Optional[Dict[str, Any]]:
        position = self._state.get("positions", {}).get(instrument_code)
        if position is None:
            return None
        from quantx_domain.trading.portfolio_state import ensure_position_dict

        data = ensure_position_dict(instrument_code, position)
        if self._bucket_ledger:
            data = self._bucket_ledger.decorate_position(instrument_code, data)
        return data

    def get_all_positions(self) -> Dict[str, Dict[str, Any]]:
        from quantx_domain.trading.portfolio_state import ensure_position_dict

        positions = {}
        for code, position in self._state.get("positions", {}).items():
            data = ensure_position_dict(code, position)
            if self._bucket_ledger:
                data = self._bucket_ledger.decorate_position(code, data)
            positions[code] = data
        return positions

    def settle_trading_day(self, trading_date: date) -> None:
        """Make previous trading-day buys sellable and reset intraday counters."""
        from quantx_domain.trading.portfolio_state import settle_position

        positions = self._state.get("positions", {})
        changed = False
        for code, position in list(positions.items()):
            settled = settle_position(position, trading_date)
            if settled != position:
                positions[code] = settled
                changed = True
        if self._bucket_ledger:
            self._bucket_ledger.settle_trading_day(trading_date)
            self._state["bucket_ledger"] = self.get_bucket_ledger_snapshot()
            changed = True
        if changed:
            self._state["positions"] = positions
            if self._bucket_ledger:
                for code, position in positions.items():
                    self._bucket_ledger.sync_position(code, position)
                self._state["bucket_ledger"] = self.get_bucket_ledger_snapshot()
            self._mark_dirty()

    def get_available_volume(self, instrument_code: str) -> int:
        position = self.get_position(instrument_code) or {}
        return int(position.get("available_volume", 0) or 0)

    def apply_corporate_action(
        self,
        instrument_code: str,
        *,
        volume_factor: float = 1.0,
        price_factor: Optional[float] = None,
        cash_dividend_per_share: float = 0.0,
        action_id: Optional[str] = None,
        ex_date: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Apply split/bonus-share/dividend adjustments to positions and buckets."""
        if not self._bucket_ledger or not instrument_code:
            return {}

        custom = self._state.setdefault("custom", {})
        applied_actions = custom.setdefault(APPLIED_CORPORATE_ACTIONS_KEY, [])
        if action_id and action_id in applied_actions:
            return {
                "instrument_code": instrument_code,
                "events": [
                    {"event": "corporate_action_skipped", "action_id": action_id}
                ],
            }

        from quantx_domain.trading.portfolio_state import ensure_position_dict

        positions = self._state.get("positions", {})
        position = ensure_position_dict(instrument_code, positions.get(instrument_code))
        cash_dividend = max(0.0, float(cash_dividend_per_share or 0.0))
        dividend_cash = float(position.get("long_volume", 0) or 0) * cash_dividend

        self._bucket_ledger.sync_position(instrument_code, position)
        patch = self._bucket_ledger.apply_corporate_action(
            instrument_code,
            volume_factor=volume_factor,
            price_factor=price_factor,
            cash_dividend_per_share=cash_dividend,
            action_id=action_id,
            ex_date=ex_date,
        )

        buckets = patch.changed_buckets
        total_volume = sum(
            int(data.get("total_volume", 0) or 0) for data in buckets.values()
        )
        total_market_value = sum(
            float(data.get("market_value", 0.0) or 0.0) for data in buckets.values()
        )
        total_cost = sum(
            float(data.get("avg_price", 0.0) or 0.0)
            * int(data.get("total_volume", 0) or 0)
            for data in buckets.values()
        )
        last_price = next(
            (
                float(data.get("last_price", 0.0) or 0.0)
                for data in buckets.values()
                if float(data.get("last_price", 0.0) or 0.0) > 0
            ),
            0.0,
        )
        position.update(
            {
                "long_volume": total_volume,
                "available_volume": sum(
                    int(data.get("available_volume", 0) or 0)
                    for data in buckets.values()
                ),
                "frozen_volume": sum(
                    int(data.get("frozen_volume", 0) or 0)
                    for data in buckets.values()
                ),
                "today_buy_volume": sum(
                    int(data.get("today_buy_volume", 0) or 0)
                    for data in buckets.values()
                ),
                "long_avg_price": total_cost / total_volume if total_volume > 0 else 0.0,
                "last_price": last_price,
                "market_value": total_market_value,
            }
        )
        position["pnl"] = (
            (position["last_price"] - position["long_avg_price"]) * total_volume
            if total_volume > 0
            else 0.0
        )

        if total_volume <= 0 and int(position.get("short_volume", 0) or 0) <= 0:
            positions.pop(instrument_code, None)
        else:
            positions[instrument_code] = position
        self._state["positions"] = positions

        if dividend_cash > 0:
            account = self._state.get("account", {})
            account["cash"] = float(account.get("cash", 0.0) or 0.0) + dividend_cash
            self._state["account"] = account
        if action_id:
            applied_actions.append(action_id)
        self._state["bucket_ledger"] = self.get_bucket_ledger_snapshot()
        self._recalculate_total_asset()
        self._mark_dirty()
        return patch.to_dict()

    # ==================== 账户管理 ====================

    def update_account(
        self,
        cash: float,
        frozen_cash: float = 0.0,
        total_asset: float = 0.0,
    ) -> None:
        """更新账户信息"""
        self._state["account"] = {
            "cash": cash,
            "frozen_cash": frozen_cash,
            "total_asset": total_asset,
        }
        self._mark_dirty()

    def get_account(self) -> Dict[str, float]:
        return self._state.get("account", {}).copy()

    def _sum_market_value(self) -> float:
        positions = self._state.get("positions", {})
        total = 0.0
        for pos in positions.values():
            if isinstance(pos, dict):
                total += float(pos.get("market_value", 0.0))
        return total

    def _recalculate_total_asset(self) -> None:
        account = self._state.get("account", {})
        cash = float(account.get("cash", 0.0))
        frozen_cash = float(account.get("frozen_cash", 0.0))
        account["total_asset"] = cash + frozen_cash + self._sum_market_value()
        self._state["account"] = account

    def get_account_quota(self) -> Dict[str, float]:
        account = self.get_account()
        cash = float(account.get("cash", 0.0))
        frozen_cash = float(account.get("frozen_cash", 0.0))
        cash_total = cash + frozen_cash
        total_asset = cash_total + self._sum_market_value()
        return {
            "available_cash": cash,
            "frozen_cash": frozen_cash,
            "cash_total": cash_total,
            "total_asset": total_asset,
        }

    def get_reserved_amount(self, order_id: str) -> float:
        return float(self._reservations.get(order_id, 0.0))

    def reserve_cash(self, order_id: str, amount: float) -> bool:
        if not self.enable_reserve:
            return False
        amount = float(amount or 0.0)
        if amount <= 0:
            return False

        account = self._state.get("account", {})
        cash = float(account.get("cash", 0.0))
        if cash < amount:
            return False

        account["cash"] = cash - amount
        account["frozen_cash"] = float(account.get("frozen_cash", 0.0)) + amount
        self._state["account"] = account

        self._reservations[order_id] = self._reservations.get(order_id, 0.0) + amount
        self._recalculate_total_asset()
        self._mark_dirty()
        return True

    def transfer_reservation(self, old_order_id: str, new_order_id: str) -> None:
        """Move temporary reservations from intent id to real broker order id."""
        if old_order_id == new_order_id:
            return
        cash_reserved = self._reservations.pop(old_order_id, 0.0)
        if cash_reserved:
            self._reservations[new_order_id] = (
                self._reservations.get(new_order_id, 0.0) + cash_reserved
            )

        position_reserved = self._position_reservations.pop(old_order_id, None)
        if position_reserved:
            target = self._position_reservations.setdefault(new_order_id, {})
            for code, volume in position_reserved.items():
                target[code] = target.get(code, 0) + int(volume or 0)
        if self._bucket_ledger:
            self._bucket_ledger.transfer_order(old_order_id, new_order_id)
            self._state["bucket_ledger"] = self.get_bucket_ledger_snapshot()

    def consume_cash_reservation(self, order_id: str, amount: float) -> float:
        """Consume frozen cash for a fill and return any unfunded shortfall."""
        amount = float(amount or 0.0)
        if amount <= 0:
            return 0.0
        reserved = float(self._reservations.get(order_id, 0.0))
        if reserved <= 0:
            return amount

        consumed = min(reserved, amount)
        account = self._state.get("account", {})
        account["frozen_cash"] = max(
            0.0, float(account.get("frozen_cash", 0.0)) - consumed
        )
        self._state["account"] = account

        remaining = reserved - consumed
        if remaining <= 1e-8:
            self._reservations.pop(order_id, None)
        else:
            self._reservations[order_id] = remaining

        self._recalculate_total_asset()
        self._mark_dirty()
        return max(0.0, amount - consumed)

    def release_cash(self, order_id: str, amount: Optional[float] = None) -> bool:
        if not self.enable_reserve:
            return False
        reserved = float(self._reservations.get(order_id, 0.0))
        if reserved <= 0:
            return False

        release_amount = reserved if amount is None else min(float(amount), reserved)
        if release_amount <= 0:
            return False

        account = self._state.get("account", {})
        account["cash"] = float(account.get("cash", 0.0)) + release_amount
        account["frozen_cash"] = max(
            0.0, float(account.get("frozen_cash", 0.0)) - release_amount
        )
        self._state["account"] = account

        remaining = reserved - release_amount
        if remaining <= 0:
            self._reservations.pop(order_id, None)
        else:
            self._reservations[order_id] = remaining

        self._recalculate_total_asset()
        self._mark_dirty()
        return True

    def reserve_position(
        self, order_id: str, instrument_code: str, volume: int
    ) -> bool:
        volume = int(volume or 0)
        if volume <= 0:
            return False

        from quantx_domain.trading.portfolio_state import ensure_position_dict

        positions = self._state.get("positions", {})
        position = ensure_position_dict(instrument_code, positions.get(instrument_code))
        available = int(position.get("available_volume", 0) or 0)
        if available < volume:
            return False

        position["available_volume"] = available - volume
        position["frozen_volume"] = int(position.get("frozen_volume", 0) or 0) + volume
        positions[instrument_code] = position
        self._state["positions"] = positions

        reserved = self._position_reservations.setdefault(order_id, {})
        reserved[instrument_code] = reserved.get(instrument_code, 0) + volume
        self._mark_dirty()
        return True

    def release_position(
        self,
        order_id: str,
        instrument_code: Optional[str] = None,
        volume: Optional[int] = None,
    ) -> bool:
        reserved = self._position_reservations.get(order_id)
        if not reserved:
            return False

        from quantx_domain.trading.portfolio_state import ensure_position_dict

        positions = self._state.get("positions", {})
        changed = False
        codes = [instrument_code] if instrument_code else list(reserved.keys())
        for code in codes:
            if not code or code not in reserved:
                continue
            release_volume = (
                reserved[code] if volume is None else min(int(volume or 0), reserved[code])
            )
            if release_volume <= 0:
                continue
            position = ensure_position_dict(code, positions.get(code))
            position["frozen_volume"] = max(
                0, int(position.get("frozen_volume", 0) or 0) - release_volume
            )
            position["available_volume"] = int(position.get("available_volume", 0) or 0) + release_volume
            positions[code] = position

            remaining = reserved[code] - release_volume
            if remaining <= 0:
                reserved.pop(code, None)
            else:
                reserved[code] = remaining
            changed = True

        if not reserved:
            self._position_reservations.pop(order_id, None)
        if changed:
            self._state["positions"] = positions
            self._mark_dirty()
        return changed

    def consume_position_reservation(
        self, order_id: str, instrument_code: str, volume: int
    ) -> int:
        """Consume frozen shares for a sell fill and return unreserved volume."""
        volume = int(volume or 0)
        if volume <= 0:
            return 0
        reserved = self._position_reservations.get(order_id, {})
        reserved_volume = int(reserved.get(instrument_code, 0) or 0)
        consumed = min(volume, reserved_volume)
        if consumed > 0:
            from quantx_domain.trading.portfolio_state import ensure_position_dict

            positions = self._state.get("positions", {})
            position = ensure_position_dict(instrument_code, positions.get(instrument_code))
            position["frozen_volume"] = max(
                0, int(position.get("frozen_volume", 0) or 0) - consumed
            )
            positions[instrument_code] = position
            self._state["positions"] = positions

            remaining = reserved_volume - consumed
            if remaining <= 0:
                reserved.pop(instrument_code, None)
            else:
                reserved[instrument_code] = remaining
            if not reserved and order_id in self._position_reservations:
                self._position_reservations.pop(order_id, None)
            self._mark_dirty()
        return max(0, volume - consumed)

    def release_order_resources(self, order_id: str) -> None:
        self.release_cash(order_id)
        self.release_position(order_id)
        if self._bucket_ledger:
            self._bucket_ledger.rollback_order(order_id, reason="order_released")
            self._state["bucket_ledger"] = self.get_bucket_ledger_snapshot()
            self._mark_dirty()

    def reserve_bucket_order(self, order_id: str, request) -> bool:
        """Reserve bucket inventory for an order request and store pending metadata."""
        if not self._bucket_ledger or not order_id or not request:
            return False
        metadata = dict(getattr(request, "metadata", {}) or {})
        plan = metadata.get("substitution_plan")
        ok = self._bucket_ledger.reserve_order(
            order_id,
            instrument_code=str(getattr(request, "instrument_code", "") or ""),
            order_type=getattr(request, "order_type", None),
            bucket=str(metadata.get("bucket", "core") or "core"),
            volume=int(getattr(request, "volume", 0) or 0),
            price=float(getattr(request, "price", 0.0) or 0.0),
            metadata=metadata,
            substitution_plan=plan,
        )
        if ok:
            self._state["bucket_ledger"] = self.get_bucket_ledger_snapshot()
            self._mark_dirty()
        return ok

    def seed_bucket_positions(
        self,
        instrument_code: str,
        bucket_states: Dict[str, Dict[str, Any]],
    ) -> None:
        """Seed initial bucket attribution for an instrument."""
        if not self._bucket_ledger or not instrument_code:
            return
        self._bucket_ledger.set_instrument_buckets(instrument_code, bucket_states)
        self._state["bucket_ledger"] = self.get_bucket_ledger_snapshot()
        self._mark_dirty()

    def get_bucket_ledger_snapshot(self) -> Dict[str, Any]:
        if not self._bucket_ledger:
            return {}
        for code, position in self._state.get("positions", {}).items():
            self._bucket_ledger.sync_position(code, position)
        return self._bucket_ledger.to_dict()

    def _hydrate_positions_from_bucket_ledger(self) -> None:
        if not self._bucket_ledger:
            return
        from quantx_domain.trading.portfolio_state import ensure_position_dict

        snapshot = self._bucket_ledger.to_dict()
        positions = self._state.get("positions", {})
        for code, buckets in dict(snapshot.get("instruments", {}) or {}).items():
            position = ensure_position_dict(str(code), positions.get(code))
            total_volume = sum(
                int(data.get("total_volume", 0) or 0) for data in buckets.values()
            )
            total_market_value = sum(
                float(data.get("market_value", 0.0) or 0.0)
                for data in buckets.values()
            )
            total_cost = sum(
                float(data.get("avg_price", 0.0) or 0.0)
                * int(data.get("total_volume", 0) or 0)
                for data in buckets.values()
            )
            position.update(
                {
                    "long_volume": total_volume,
                    "available_volume": sum(
                        int(data.get("available_volume", 0) or 0)
                        for data in buckets.values()
                    ),
                    "frozen_volume": sum(
                        int(data.get("frozen_volume", 0) or 0)
                        for data in buckets.values()
                    ),
                    "today_buy_volume": sum(
                        int(data.get("today_buy_volume", 0) or 0)
                        for data in buckets.values()
                    ),
                    "long_avg_price": (
                        total_cost / total_volume if total_volume > 0 else 0.0
                    ),
                    "market_value": total_market_value,
                }
            )
            last_price = next(
                (
                    float(data.get("last_price", 0.0) or 0.0)
                    for data in buckets.values()
                    if float(data.get("last_price", 0.0) or 0.0) > 0
                ),
                float(position.get("last_price", 0.0) or 0.0),
            )
            position["last_price"] = last_price
            positions[str(code)] = position
        self._state["positions"] = positions

    def record_decision_trace(self, trace) -> None:
        if not trace:
            return
        if self._decision_trace_logger:
            self._decision_trace_logger.record(trace)
            traces = self._decision_trace_logger.to_list()
            self._state["decision_traces"] = traces[-500:]
        if self._backtest_storage and hasattr(self._backtest_storage, "add_trace"):
            self._backtest_storage.add_trace(trace.to_dict())
        if self.persist_enabled:
            try:
                asyncio.create_task(self._persist_decision_trace_record(trace))
            except RuntimeError:
                self.logger.warning("决策审计持久化跳过：当前无线程事件循环")
        self._mark_dirty()

    async def _persist_decision_trace_record(self, trace) -> None:
        try:
            from quantx_infrastructure.database.connection import get_async_db
            from quantx_infrastructure.repositories.strategy_decision_trace_repository import (
                StrategyDecisionTraceRepository,
            )

            payload = self._decision_trace_record_data(trace)
            async for db in get_async_db():
                repo = StrategyDecisionTraceRepository(db)
                await repo.create_trace(payload)
                break
        except Exception as e:
            self.logger.error(f"决策审计持久化失败: {e}")

    def _decision_trace_record_data(self, trace) -> Dict[str, Any]:
        trace_dict = trace.to_dict() if hasattr(trace, "to_dict") else dict(trace or {})
        decided_at = getattr(trace, "timestamp", None)
        if decided_at is None:
            decided_at = time_utils.now()
        input_summary = dict(getattr(trace, "input_summary", {}) or trace_dict.get("input_summary") or {})
        output_summary = dict(getattr(trace, "output_summary", {}) or trace_dict.get("output_summary") or {})
        state_patch = dict(getattr(trace, "state_patch", {}) or trace_dict.get("state_patch") or {})
        trade_intents = list(getattr(trace, "trade_intents", []) or trace_dict.get("trade_intents") or [])
        return {
            "id": str(uuid.uuid4()),
            "trace_id": str(getattr(trace, "trace_id", None) or trace_dict.get("trace_id") or uuid.uuid4()),
            "strategy_run_id": str(getattr(trace, "run_id", None) or trace_dict.get("run_id") or self.run_id),
            "strategy_id": str(getattr(trace, "strategy_id", None) or trace_dict.get("strategy_id") or ""),
            "instrument_code": str(getattr(trace, "instrument_code", None) or trace_dict.get("instrument_code") or ""),
            "decided_at": decided_at,
            "input_summary": _json_safe(input_summary),
            "output_summary": _json_safe(output_summary),
            "trade_intents": _json_safe(trade_intents),
            "state_patch": _json_safe(state_patch),
            "decision_trace": _json_safe(trace_dict),
        }

    async def record_trade_intent(self, intent, status: str = "PENDING") -> None:
        """Persist a TradeIntent snapshot before it enters sizing/risk routing."""
        data = self._trade_intent_record_data(intent, status=status)
        intent_id = data["id"]
        self._state.setdefault("trade_intents", {})[intent_id] = data
        if self._backtest_storage and hasattr(self._backtest_storage, "add_trade_intent"):
            self._backtest_storage.add_trade_intent(dict(data))
        await self._upsert_trade_intent_record(data)
        self._mark_dirty()

    async def update_trade_intent_status(
        self, intent_id: Optional[str], status: str, **updates: Any
    ) -> None:
        """Update a persisted TradeIntent lifecycle status."""
        if not intent_id:
            return
        existing = dict(self._state.setdefault("trade_intents", {}).get(intent_id, {}))
        existing.setdefault("id", intent_id)
        existing["status"] = status
        accumulate_executed_volume = bool(updates.pop("accumulate_executed_volume", False))
        if accumulate_executed_volume and updates.get("executed_volume") is not None:
            previous_volume = int(existing.get("executed_volume", 0) or 0)
            fill_volume = int(updates.get("executed_volume", 0) or 0)
            total_volume = previous_volume + fill_volume
            previous_price = float(existing.get("executed_price", 0.0) or 0.0)
            fill_price = float(updates.get("executed_price", 0.0) or 0.0)
            if total_volume > 0:
                if previous_volume > 0 and previous_price > 0 and fill_price > 0:
                    updates["executed_price"] = (
                        previous_price * previous_volume + fill_price * fill_volume
                    ) / total_volume
                elif fill_price <= 0:
                    updates["executed_price"] = previous_price
            updates["executed_volume"] = total_volume
        existing.update({key: value for key, value in updates.items() if value is not None})
        self._state["trade_intents"][intent_id] = existing
        if self._backtest_storage and hasattr(self._backtest_storage, "add_trade_intent"):
            self._backtest_storage.add_trade_intent(dict(existing))
        await self._upsert_trade_intent_record(existing)
        self._mark_dirty()

    async def _upsert_trade_intent_record(self, data: Dict[str, Any]) -> None:
        if not self.persist_enabled:
            return
        try:
            from quantx_infrastructure.database.connection import get_async_db
            from quantx_infrastructure.repositories.trade_intent_repository import (
                TradeIntentRepository,
            )

            payload = self._db_trade_intent_payload(data)
            async for db in get_async_db():
                repo = TradeIntentRepository(db)
                existing = await repo.find_by_id(payload["id"])
                if existing:
                    await repo.update_intent(payload["id"], payload)
                else:
                    await repo.create_intent(payload)
                break
        except Exception as e:
            self.logger.error(f"交易意图持久化失败: {e}")

    def _trade_intent_record_data(self, intent, *, status: str) -> Dict[str, Any]:
        metadata = dict(getattr(intent, "metadata", {}) or {})
        metadata.setdefault(
            "execution_mode", _enum_value(getattr(intent, "execution_mode", "AUTO"))
        )
        metadata.setdefault("approval_ttl_ms", getattr(intent, "approval_ttl_ms", None))
        metadata.setdefault(
            "max_price_deviation_bps",
            getattr(intent, "max_price_deviation_bps", None),
        )
        created_at = getattr(intent, "created_at", None)
        if created_at is not None and hasattr(created_at, "isoformat"):
            metadata.setdefault("intent_created_at", created_at.isoformat())
        return {
            "id": str(getattr(intent, "intent_id", "") or ""),
            "strategy_run_id": str(getattr(intent, "run_id", self.run_id) or self.run_id),
            "strategy_id": str(getattr(intent, "strategy_id", "") or ""),
            "instrument_code": str(getattr(intent, "instrument_code", "") or ""),
            "direction": _enum_value(getattr(intent, "direction", "")),
            "bucket": str(getattr(intent, "bucket", "") or "core"),
            "reason": str(getattr(intent, "reason", "") or ""),
            "priority": _enum_value(getattr(intent, "priority", "NORMAL")),
            "intent_type": _enum_value(getattr(intent, "intent_type", None)),
            "confidence": float(getattr(intent, "confidence", 1.0) or 0.0),
            "target_amount": getattr(intent, "target_amount", None),
            "target_position_pct": getattr(intent, "target_position_pct", None),
            "target_volume": getattr(intent, "target_volume", None),
            "limit_price_hint": getattr(intent, "limit_price_hint", None),
            "trace_id": getattr(intent, "trace_id", None),
            "status": status,
            "metadata": metadata,
            "notes": metadata.get("notes"),
        }

    def _db_trade_intent_payload(self, data: Dict[str, Any]) -> Dict[str, Any]:
        allowed = {
            "id",
            "strategy_run_id",
            "strategy_id",
            "instrument_code",
            "direction",
            "bucket",
            "reason",
            "priority",
            "intent_type",
            "confidence",
            "target_amount",
            "target_position_pct",
            "target_volume",
            "limit_price_hint",
            "trace_id",
            "risk_decision_id",
            "order_id",
            "status",
            "executed_price",
            "executed_volume",
            "executed_time",
            "metadata",
            "notes",
        }
        payload = {key: data.get(key) for key in allowed if key in data}
        payload.setdefault("metadata", {})
        return payload

    def apply_trade(self, trade) -> None:
        """按成交回报更新持仓与资金（策略额度）"""
        from quantx_domain.brokers.base import OrderType
        from quantx_domain.trading.portfolio_state import ensure_position_dict

        instrument_code = getattr(trade, "instrument_code", None)
        if not instrument_code:
            return

        price = float(getattr(trade, "price", 0.0))
        volume = int(getattr(trade, "volume", 0) or 0)
        if volume <= 0:
            return

        amount = float(getattr(trade, "amount", 0.0) or 0.0)
        if amount <= 0 and price > 0:
            amount = price * volume
        commission = float(getattr(trade, "commission", 0.0) or 0.0)
        order_id = str(getattr(trade, "order_id", "") or "")
        order_metadata = {}
        if self._bucket_ledger and order_id:
            order_metadata.update(self._bucket_ledger.pending_metadata(order_id))
        order_metadata.update(dict(getattr(trade, "metadata", {}) or {}))

        positions = self._state.get("positions", {})
        pos = ensure_position_dict(instrument_code, positions.get(instrument_code))

        long_volume = int(pos.get("long_volume", 0))
        short_volume = int(pos.get("short_volume", 0))
        available_volume = int(pos.get("available_volume", 0) or 0)
        long_avg_price = float(pos.get("long_avg_price", 0.0))
        short_avg_price = float(pos.get("short_avg_price", 0.0))

        account = self._state.get("account", {})
        cash = float(account.get("cash", 0.0))

        trade_type = getattr(trade, "trade_type", None)
        if trade_type in [OrderType.BUY, OrderType.BUY_TO_COVER]:
            if trade_type == OrderType.BUY_TO_COVER and short_volume > 0:
                cover_volume = min(volume, short_volume)
                short_volume -= cover_volume
            else:
                total_value = long_avg_price * long_volume + price * volume
                long_volume += volume
                long_avg_price = (
                    total_value / long_volume if long_volume > 0 else 0.0
                )
                pos["today_buy_volume"] = int(pos.get("today_buy_volume", 0) or 0) + volume
            shortfall = (
                self.consume_cash_reservation(order_id, amount + commission)
                if order_id
                else amount + commission
            )
            cash -= shortfall
        elif trade_type == OrderType.SELL:
            unreserved_volume = (
                self.consume_position_reservation(order_id, instrument_code, volume)
                if order_id
                else volume
            )
            reserved_consumed = volume - unreserved_volume
            if reserved_consumed > 0:
                pos["frozen_volume"] = max(
                    0, int(pos.get("frozen_volume", 0) or 0) - reserved_consumed
                )
            sell_volume = min(volume, long_volume)
            long_volume -= sell_volume
            if unreserved_volume > 0:
                available_volume = max(0, available_volume - unreserved_volume)
            cash += amount - commission
        elif trade_type == OrderType.SELL_SHORT:
            total_value = short_avg_price * short_volume + price * volume
            short_volume += volume
            short_avg_price = (
                total_value / short_volume if short_volume > 0 else 0.0
            )
            cash += amount - commission

        pos["long_volume"] = long_volume
        pos["short_volume"] = short_volume
        pos["available_volume"] = min(
            max(0, available_volume),
            max(0, long_volume - int(pos.get("frozen_volume", 0) or 0)),
        )
        pos["long_avg_price"] = long_avg_price
        pos["short_avg_price"] = short_avg_price
        pos["last_price"] = price
        pos["market_value"] = (long_volume - short_volume) * price

        pnl = 0.0
        if long_volume > 0:
            pnl += (price - long_avg_price) * long_volume
        if short_volume > 0:
            pnl += (short_avg_price - price) * short_volume
        pos["pnl"] = pnl

        if long_volume <= 0 and short_volume <= 0 and int(pos.get("frozen_volume", 0) or 0) <= 0:
            positions.pop(instrument_code, None)
        else:
            positions[instrument_code] = pos
        self._state["positions"] = positions

        account["cash"] = cash
        self._state["account"] = account
        self._recalculate_total_asset()
        if self._bucket_ledger:
            self._bucket_ledger.apply_trade(trade, order_metadata)
            self._state["bucket_ledger"] = self.get_bucket_ledger_snapshot()
        self._mark_dirty()

    # ==================== 交易意图管理 ====================

    def set_backtest_mode(
        self,
        backtest_id: str,
        *,
        backtest_version: Optional[int] = None,
    ) -> None:
        """设置为回测模式，初始化文件存储"""
        from quantx_infrastructure.core.backtest_result_storage import (
            BacktestResultStorage,
        )
        self.is_backtest = True
        self.backtest_id = backtest_id
        self._backtest_storage = BacktestResultStorage(
            backtest_id=backtest_id,
            strategy_run_id=self.run_id,
            version=backtest_version,
        )
        self._log_file_path = self._backtest_storage.get_log_file_path()
        self.logger.info(
            f"进入回测模式: backtest_id={backtest_id}, version={backtest_version}"
        )

    async def finalize_backtest(self) -> str:
        """结束回测，将缓冲数据写入文件"""
        if not self._backtest_storage:
            return ""
        path = await self._backtest_storage.flush()
        self.logger.info(f"回测数据已写入: {path}")
        return path

    def get_latest_backtest_grid_book_snapshot(self) -> Optional[Dict[str, Any]]:
        """获取回测期间最后一个 GridBook 快照。"""
        if not self._backtest_storage:
            return None
        return self._backtest_storage.get_latest_grid_book_snapshot()

    def get_backtest_grid_book_snapshot_count(self) -> int:
        """获取回测结果中实际写入的 GridBook 快照数。"""
        if not self._backtest_storage:
            return 0
        return self._backtest_storage.get_grid_book_snapshot_count()

    def get_backtest_grid_book_observed_count(self) -> int:
        """获取回测期间观测到的 GridBook 快照数。"""
        if not self._backtest_storage:
            return 0
        return self._backtest_storage.get_grid_book_observed_count()

    # ==================== 自定义状态（扩展） ====================

    def set_custom(self, key: str, value: Any) -> None:
        """设置策略自定义状态"""
        custom = self._state.get("custom", {})
        custom[key] = value
        self._state["custom"] = custom
        if key == GRID_BOOK_CUSTOM_STATE_KEY and self._backtest_storage:
            self._backtest_storage.add_grid_book_snapshot(dict(value or {}))
        self._mark_dirty()

    def get_custom(self, key: str, default: Any = None) -> Any:
        return self._state.get("custom", {}).get(key, default)

    def get_custom_state(self) -> Dict[str, Any]:
        """获取完整自定义状态"""
        return self._state.get("custom", {}).copy()

    def update_custom_state(self, updates: Dict[str, Any]) -> None:
        """批量更新自定义状态"""
        if not updates:
            return
        custom = self._state.get("custom", {})
        custom.update(updates)
        self._state["custom"] = custom
        if GRID_BOOK_CUSTOM_STATE_KEY in updates and self._backtest_storage:
            self._backtest_storage.add_grid_book_snapshot(
                dict(updates.get(GRID_BOOK_CUSTOM_STATE_KEY) or {})
            )
        self._mark_dirty()

    def set_custom_state(self, state: Dict[str, Any]) -> None:
        """覆盖自定义状态"""
        self._state["custom"] = dict(state or {})
        self._mark_dirty()

    async def force_save(self) -> bool:
        """强制保存"""
        self._mark_dirty()
        return await self.save_snapshot()


def _enum_value(value: Any) -> Optional[str]:
    if value is None:
        return None
    return str(getattr(value, "value", value))


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if hasattr(value, "value"):
        return getattr(value, "value")
    return value
