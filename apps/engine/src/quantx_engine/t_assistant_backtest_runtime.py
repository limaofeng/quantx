"""Single-account RULE_ONLY execution over the public domain/application rules.

The adapter owns BACKTEST facts only. Market input is an ordered, frozen Tick
stream; no service, global session factory or account lookup is used.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, replace
from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum
from math import isfinite
from types import SimpleNamespace

from quantx_application.t_trade_v3.daily_t_valuation import (
  TDailyFill,
  TOpeningPosition,
  TValuationMark,
  value_daily_t_positions,
)
from quantx_application.t_trade_v3.entry_execution_gate import (
  EntryExecutionBinding,
  EntryExecutionGate,
  EntryExecutionGateInput,
  EntryExecutionGatePolicy,
  MarketDataCapabilityManifest,
)
from quantx_application.t_trade_v3.portfolio_allocation import allocate_portfolio
from quantx_application.t_trade_v3.portfolio_snapshot import (
  IndustryTExposure,
  PortfolioEvidenceCut,
  PortfolioTDecisionSnapshot,
  TEnvelopePosition,
  TPortfolioPolicy,
  TTradingEnvelopePolicy,
  build_t_trading_envelope,
)
from quantx_contracts import ExecutionEnvironment, ExecutionOwnerRef
from quantx_domain.brokers.backtest import BacktestBroker
from quantx_domain.brokers.base import (
  OrderRequest,
  OrderStatus,
  OrderType,
  Position,
  PriceType,
)
from quantx_domain.clock import SHANGHAI
from quantx_domain.enums import StrategyRunMode
from quantx_domain.strategies import AshareIntradayTAssistantStrategy
from quantx_domain.strategies.base import (
  ExitPlanIntentOrigin,
  MarketDataContext,
  StrategyCadence,
  StrategyContext,
  StrategyInput,
  TradeIntent,
  TradeIntentDirection,
)
from quantx_domain.trading.bucket_ledger import BucketLedger
from quantx_domain.trading.exit_plan import (
  ExitEvaluationContext,
  ExitPlanBook,
  TradingCostPolicy,
)
from quantx_domain.trading.market_session import classify_market_data_session
from quantx_domain.trading.old_inventory_capacity import allocate_old_inventory_claims
from quantx_domain.trading.order_sizer import OrderSizer
from quantx_domain.trading.risk_checker import TradingRiskChecker
from quantx_domain.trading.risk_increase_admission import (
  RiskIncreaseAdmissionCandidate,
  rank_risk_increase_candidates,
)
from quantx_domain.trading.t_assistant_execution import (
  TAssistantExecution,
  stable_manifest_hash,
)
from quantx_domain.trading.t_assistant_market_state import (
  SymbolDecisionSnapshot,
  SymbolMarketDeltaRing,
  TAssistantSymbolState,
  TDecisionSnapshot,
  decode_candidate_evidence,
)
from quantx_domain.trading.t_order_policy import TEntryOrderPolicy, TExitOrderPolicy
from quantx_domain.trading.t_trade_opportunity_engine import (
  CandidateControl,
  OpportunityGateContext,
  OpportunityReferenceProfile,
)
from quantx_infrastructure.services.t_allocation_candidate_projection import (
  candidate_from_evaluation,
)

from quantx_engine.t_assistant_backtest_timeline import async_tick_frames


def json_value(value):
  if isinstance(value, Enum):
    return value.value
  if isinstance(value, (datetime, date)):
    return value.isoformat()
  if isinstance(value, Decimal):
    return str(value)
  if hasattr(value, "__dataclass_fields__"):
    return json_value(asdict(value))
  if isinstance(value, dict):
    return {
      (k.isoformat() if isinstance(k, date) else k): json_value(v)
      for k, v in value.items()
    }
  if isinstance(value, (tuple, list)):
    return [json_value(v) for v in value]
  if isinstance(value, (set, frozenset)):
    return sorted(json_value(v) for v in value)
  return value


class SharedBacktestBroker(BacktestBroker):
  """Deterministic IDs and a strict BACKTEST ownership boundary."""

  def __init__(self, execution, **parameters):
    slippage = parameters.pop("slippage_rate", 0.0)
    super().__init__(
      account_id=execution.account_id,
      strict_book_depth=True,
      no_queue_credit=True,
      defer_new_orders_until_next_quote=True,
      slippage_rate=0.0,
      strict_book_slippage_rate=slippage,
      **parameters,
    )
    self.execution = execution
    self.order_sequence = self.trade_sequence = 0
    self.exit_plan_owners = set()

  def generate_order_id(self):
    self.order_sequence += 1
    return f"bt:{self.execution.execution_id}:order:{self.order_sequence}"

  def generate_trade_id(self):
    self.trade_sequence += 1
    return f"bt:{self.execution.execution_id}:fill:{self.trade_sequence}"

  async def place_order(self, request):
    if (
      request.environment is not ExecutionEnvironment.BACKTEST
      or not (
        (
          request.execution_ref == self.execution.execution_ref
          and request.order_type is OrderType.BUY
        )
        or (
          request.execution_ref.owner_type == "EXIT_PLAN"
          and request.order_type is OrderType.SELL
          and request.execution_ref.owner_id in self.exit_plan_owners
          and request.metadata.get("exit_plan_id") == request.execution_ref.owner_id
          and request.metadata.get("source_execution_ref")
          == self.execution.execution_ref.to_dict()
        )
      )
      or self.current_time is None
    ):
      raise ValueError("BACKTEST_BROKER_SCOPE_OR_CLOCK_INVALID")
    return await super().place_order(request)


class TAssistantBacktestRuntime:
  def __init__(
    self,
    *,
    execution: TAssistantExecution,
    parameters: dict,
    portfolio_policy: TPortfolioPolicy,
    envelope_policies: dict[str, TTradingEnvelopePolicy],
    industries: dict[str, str],
    profiles: dict[str, OpportunityReferenceProfile],
    gate_policy: EntryExecutionGatePolicy,
    capabilities: MarketDataCapabilityManifest,
    initial_cash: float,
    initial_positions: dict[str, Position],
    initial_buckets: dict,
    broker_parameters: dict,
    trading_days: tuple,
    prior_close_marks: dict,
  ):
    if (
      execution.environment is not ExecutionEnvironment.BACKTEST
      or execution.scorer_mode != "RULE_ONLY"
    ):
      raise ValueError("BACKTEST_RULE_ONLY_EXECUTION_REQUIRED")
    codes = set(initial_positions)
    if not codes or any(
      set(values) != codes
      for values in (envelope_policies, industries, profiles, initial_buckets)
    ):
      raise ValueError("BACKTEST_COMPLETE_UNIVERSE_REQUIRED")
    self.execution, self.parameters = execution, dict(parameters)
    if self.parameters.get("account_id", execution.account_id) != execution.account_id:
      raise ValueError("BACKTEST_PARAMETER_ACCOUNT_CONFLICT")
    self.parameters["account_id"] = execution.account_id
    if (
      not isfinite(initial_cash)
      or initial_cash < 0
      or any(
        p.available_volume != p.long_volume or p.today_buy_volume or p.long_volume < 0
        for p in initial_positions.values()
      )
    ):
      raise ValueError("BACKTEST_INITIAL_ACCOUNT_INVALID")
    self.portfolio_policy, self.envelope_policies = portfolio_policy, envelope_policies
    self.industries, self.profiles = industries, profiles
    self.gate_policy, self.capabilities = gate_policy, capabilities
    self.trading_days, self.prior_close_marks = tuple(trading_days), prior_close_marks
    if tuple(sorted(set(trading_days))) != tuple(trading_days):
      raise ValueError("BACKTEST_ORDERED_CALENDAR_REQUIRED")
    self.day, self.opening_positions = None, ()
    self.broker = SharedBacktestBroker(
      execution, initial_capital=initial_cash, **broker_parameters
    )
    self.broker.configure_initial_portfolio(
      cash=initial_cash,
      total_asset=initial_cash
      + sum(p.market_value for p in initial_positions.values()),
      positions=initial_positions,
    )
    self.broker.positions = deepcopy(initial_positions)
    self.costs = TradingCostPolicy(
      commission_rate=self.broker.commission_rate,
      minimum_commission=self.broker.min_commission,
      stamp_tax_rate=self.broker.stamp_tax_rate,
      transfer_fee_rate=self.broker.transfer_fee_rate,
    )
    if any(not isfinite(v) or v < 0 for v in self.costs.to_dict().values()):
      raise ValueError("BACKTEST_COST_POLICY_INVALID")
    if any(
      self.parameters.get(k, getattr(TradingCostPolicy(), k)) != v
      for k, v in self.costs.to_dict().items()
    ):
      raise ValueError("BACKTEST_STRATEGY_BROKER_COST_MISMATCH")
    self.buckets = BucketLedger(execution.execution_id)
    for code, buckets in initial_buckets.items():
      self.buckets.set_instrument_buckets(code, buckets)
    violations = self.buckets.validate_invariants(
      {c: asdict(p) for c, p in initial_positions.items()}
    )
    if violations:
      raise ValueError("BACKTEST_INITIAL_BUCKETS_NOT_RECONCILED")
    self.plans = ExitPlanBook()
    self.strategy = AshareIntradayTAssistantStrategy(
      StrategyContext(
        mode=StrategyRunMode.BACKTEST,
        instruments=sorted(codes),
        parameters=self.parameters,
        execution_ref=execution.execution_ref,
        environment=ExecutionEnvironment.BACKTEST,
      )
    )
    self.states = {
      code: TAssistantSymbolState.initial(
        execution_id=execution.execution_id,
        instrument_code=code,
        policy_version=execution.policy_version,
        feature_schema_version=execution.feature_schema_version,
      )
      for code in codes
    }
    self.rings = {code: SymbolMarketDeltaRing(code) for code in codes}
    self.latest, self.controls, self.intents, self.evidence = {}, {}, {}, {}
    self.audit, self.frames = [], []
    self.seen_fills, self.seen_orders = set(), {}
    self.initial_cash = initial_cash
    self.initial_volumes = {
      code: p.long_volume for code, p in initial_positions.items()
    }
    self.risk = TradingRiskChecker(
      strict_market_data=True,
      strict_limit_data=False,
      commission_rate=self.costs.commission_rate,
      min_commission=self.costs.minimum_commission,
    )
    self.now = None

  def _holding(self, code):
    return self.buckets.decorate_position(code, asdict(self.broker.positions[code]))

  def _claim(self, code, *, own_plan=""):
    holding = self._holding(code)
    required = sum(
      max(
        0,
        p.remaining_volume
        - sum(
          o.request.volume - o.filled_volume
          for o in self.broker.pending_orders
          if o.request.order_type is OrderType.SELL
          and o.request.metadata.get("exit_plan_id") == p.plan_id
        ),
      )
      for p in self.plans.plans.values()
      if p.template.instrument_code == code and p.plan_id != own_plan
    )
    required += sum(
      o.request.volume - o.filled_volume
      for o in self.broker.pending_orders
      if o.request.instrument_code == code and o.request.order_type is OrderType.BUY
    )
    return allocate_old_inventory_claims(
      available_by_bucket={
        b: holding[f"{b}_available_volume"] for b in ("core", "swing", "locked_core")
      },
      required_claim_qty=required,
      protected_core_floor=self.envelope_policies[code].protected_core_volume,
      allow_core_claim=True,
    )

  async def _converge(self):
    # Broker state is already final for this matching call. Apply each fill once
    # before terminal ORDER convergence can release the pending ExitPlan.
    for fill in self.broker.trades:
      if fill.trade_id in self.seen_fills:
        continue
      if fill.environment is not ExecutionEnvironment.BACKTEST:
        raise ValueError("BACKTEST_RECEIPT_SCOPE_INVALID")
      order = self.broker.orders[fill.order_id]
      metadata = order.request.metadata
      self.buckets.apply_trade(fill, metadata)
      if fill.trade_type is OrderType.BUY:
        plan = self.plans.register_entry_fill(
          metadata["exit_plan_template"],
          volume=fill.volume,
          price=fill.price,
          trade_time=fill.trade_time,
        )
        self.broker.exit_plan_owners.add(plan.plan_id)
      else:
        self.plans.apply_exit_fill(
          plan_id=metadata["exit_plan_id"],
          intent_id=metadata["intent_id"],
          rule_id=metadata["rule_id"],
          volume=fill.volume,
          price=fill.price,
        )
      self.seen_fills.add(fill.trade_id)
      self.audit.append({"type": "FILL", "value": json_value(fill)})
    for order in self.broker.orders.values():
      state = (order.status.value, order.filled_volume)
      if self.seen_orders.get(order.order_id) == state:
        continue
      metadata = order.request.metadata
      if order.request.order_type is OrderType.SELL:
        self.plans.apply_order_event(
          plan_id=metadata["exit_plan_id"],
          intent_id=metadata["intent_id"],
          order_id=order.order_id,
          status=order.status.value,
          cumulative_filled_volume=order.filled_volume,
          timestamp_ms=int(self.now.timestamp() * 1000),
        )
      if order.status in {
        OrderStatus.FILLED,
        OrderStatus.CANCELLED,
        OrderStatus.REJECTED,
        OrderStatus.EXPIRED,
      }:
        self.buckets.rollback_order(order.order_id, reason=order.status.value)
      self.seen_orders[order.order_id] = state
      self.audit.append({"type": "ORDER", "value": json_value(order)})

  async def run(self, events, *, on_frame=None, retain_frames=True, presorted=False):
    self.frame_count = 0
    async for at, frame in async_tick_frames(events, presorted=presorted):
      index = self.frame_count
      if self.now is not None and at <= self.now:
        raise ValueError("BACKTEST_CLOCK_REGRESSION")
      self.now = at.astimezone(SHANGHAI)
      day = self.now.date()
      if day not in self.trading_days or self.trading_days.index(day) == 0:
        raise ValueError("BACKTEST_CALENDAR_PREDECESSOR_REQUIRED")
      if day != self.day:
        self.day = day
        self.opening_positions = tuple(
          TOpeningPosition(p.plan_id, p.template.instrument_code, p.remaining_volume)
          for p in self.plans.plans.values()
          if p.remaining_volume
        )
      self.buckets.settle_trading_day(self.now.date())
      # At cutoff, cancel before the new Tick can match a still-pending BUY.
      if self.now.time().replace(tzinfo=None) >= time(14, 50):
        for order in list(self.broker.pending_orders):
          if order.request.order_type is OrderType.BUY:
            await self.broker.cancel_order(order.order_id)
        await self._converge()
      for item in frame:
        code = item.market.instrument_code
        if code not in self.states:
          raise ValueError("BACKTEST_UNIVERSE_CHANGED")
        if self.profiles[code].as_of_trade_date >= self.now.date().isoformat():
          raise ValueError("BACKTEST_FUTURE_PROFILE")
        self.rings[code].accept(item.tick, capture_time_ms=int(at.timestamp() * 1000))
        self.latest[code] = item
        matching_market = replace(
          item.market,
          is_trading=item.market.is_trading
          and classify_market_data_session(self.now).is_continuous,
        )
        await self.broker.update_market_data(
          code, item.market.price, self.now, market_data=matching_market
        )
        await self._converge()
      for code in sorted({item.market.instrument_code for item in frame}):
        if classify_market_data_session(self.now).is_continuous:
          await self._exit(code)
        await self._converge()
      if len(self.latest) == len(self.states):
        await self._decision(index)
        await self._converge()
      evidence = self._conservation()
      facts = {
        "time": at.isoformat(),
        "evidence": evidence,
        "audit": self.audit,
        "state_hashes": {
          c: stable_manifest_hash(s.to_dict()) for c, s in self.states.items()
        },
      }
      if on_frame is not None:
        on_frame(index, facts)
      if retain_frames:
        self.frames.append(facts)
      self.frame_count = index + 1
      self.audit = []
    return self.frames

  async def _decision(self, index):
    now_ms = int(self.now.timestamp() * 1000)
    tick = max(
      (v.tick for v in self.latest.values()), key=lambda t: t.market_fence_sequence
    )
    cycle_id = f"{self.execution.execution_id}:cycle:{index}"
    session = classify_market_data_session(self.now)
    symbols = tuple(
      SymbolDecisionSnapshot(
        code,
        state,
        self.rings[code].slice_after(
          state.cursor,
          decision_time_ms=now_ms,
          through_accepted_sequence=tick.market_fence_sequence,
        ),
        OpportunityGateContext(
          continuous_session=session.is_continuous, session_code=session.value
        ),
        self.profiles[code],
        candidate_control=self.controls.pop(code, CandidateControl()),
      )
      for code, state in sorted(self.states.items())
    )
    execution = self.execution
    snapshot = TDecisionSnapshot(
      execution.execution_ref,
      self.now,
      self.now.date().isoformat(),
      tick.stream_id,
      tick.sample.continuity_generation,
      tick.market_fence_sequence,
      self.now,
      execution.universe_revision,
      execution.frozen_config_version,
      execution.config_snapshot_hash,
      execution.policy_version,
      execution.feature_schema_version,
      execution.status,
      execution.readiness.readiness,
      execution.readiness.as_of,
      symbols,
    )
    output = await self.strategy.step(
      StrategyInput(
        strategy_id="ashare-intraday-t-assistant",
        timestamp=self.now,
        cadence=StrategyCadence.SNAPSHOT,
        instrument_code=None,
        input_id=cycle_id,
        trace_id=cycle_id,
        market_data=snapshot,
        parameters=self.parameters,
        execution_ref=execution.execution_ref,
        market_data_context=MarketDataContext(
          session=session, trade_date=self.now.date()
        ),
      )
    )
    for patch in output.symbol_state_patches:
      self.states[patch.instrument_code] = TAssistantSymbolState.from_dict(
        patch.patch.set["symbol_state"]
      )
      for event in patch.patch.append_events:
        if event.get("candidate_evidence"):
          witness = event["candidate_evidence"]
          self.evidence[witness["candidate"]["fingerprint"]] = witness
    candidates = []
    for intent in output.trade_intents:
      if intent.intent_id in self.intents:
        continue
      self.intents[intent.intent_id] = intent
      metadata = {
        **intent.metadata,
        "intent_created_at": intent.created_at.isoformat(),
        "approval_ttl_ms": intent.approval_ttl_ms,
        "max_price_deviation_bps": intent.max_price_deviation_bps,
      }
      projection = SimpleNamespace(
        id=intent.intent_id,
        intent_metadata=metadata,
        instrument_code=intent.instrument_code,
        allocation_version=1,
        allocation_next_eligible_at=None,
        target_amount=intent.target_amount,
        limit_price_hint=intent.limit_price_hint,
      )
      candidates.append(
        candidate_from_evaluation(
          projection,
          SimpleNamespace(
            payload={
              "candidate_evidence": self.evidence[
                intent.metadata["candidate_fingerprint"]
              ]
            }
          ),
          now=self.now,
          costs=self.costs,
        )
      )
    if not candidates:
      return
    try:
      portfolio = await self._portfolio(cycle_id)
    except ValueError as exc:
      reason = str(exc)
      if reason not in {"T_VALUATION_MARK_STALE", "T_VALUATION_OPENING_MARK_REQUIRED"}:
        raise
      self.audit.append(
        {"type": "ALLOCATION_BLOCKED", "cycle_id": cycle_id, "reason": reason}
      )
      for candidate in candidates:
        self.controls[candidate.instrument_code] = CandidateControl(
          suppress_candidate_id=candidate.candidate_id
        )
      return
    decisions = allocate_portfolio(
      portfolio, tuple(candidates), allocation_attempt=1, now=self.now
    )
    self.audit.append({"type": "ALLOCATION", "value": json_value(decisions)})
    allowed = {d.intent_id: d for d in decisions if d.action in {"ALLOW", "CAP"}}
    ranked = rank_risk_increase_candidates(
      RiskIncreaseAdmissionCandidate(
        d.intent_id,
        execution.execution_ref.owner_type,
        execution.execution_id,
        self.now,
        stable_manifest_hash(json_value(d)),
        cycle_id,
        d.rank,
        self.now,
      )
      for d in allowed.values()
    )
    self.audit.append({"type": "ADMISSION", "value": json_value(ranked)})
    for admission in ranked:
      decision = allowed[admission.candidate.intent_id]
      await self._entry(self.intents[decision.intent_id], decision)
      await self._converge()
    for d in decisions:
      self.controls[d.instrument_code] = CandidateControl(
        suppress_candidate_id=d.candidate_id
      )

  async def _portfolio(self, cycle_id):
    account = await self.broker.get_account()
    material = {
      "cash": self.broker.cash,
      "orders": json_value(tuple(self.broker.orders.values())),
    }
    digest = stable_manifest_hash(material)
    cut = PortfolioEvidenceCut(
      self.execution.execution_ref,
      ExecutionEnvironment.BACKTEST,
      self.now,
      cycle_id,
      digest,
      self.now,
      digest,
      self.now,
      True,
    )
    envelopes, totals = [], {}
    exposure = Decimal(0)
    for code in sorted(self.states):
      holding = self._holding(code)
      plans = [
        p
        for p in self.plans.plans.values()
        if p.template.instrument_code == code and p.remaining_volume
      ]
      pending = [
        o
        for o in self.broker.pending_orders
        if o.request.instrument_code == code and o.request.order_type is OrderType.BUY
      ]
      amount = Decimal(str(sum(p.remaining_volume * p.entry_avg_price for p in plans)))
      amount += Decimal(
        str(
          sum((o.request.volume - o.filled_volume) * o.request.price for o in pending)
        )
      )
      exposure += amount
      industry = self.industries[code]
      totals[industry] = totals.get(industry, Decimal(0)) + amount
      envelopes.append(
        build_t_trading_envelope(
          cut=cut,
          config_version=self.execution.config_version_id,
          policy=self.envelope_policies[code],
          position=TEnvelopePosition(
            code,
            industry,
            holding["locked_core_volume"],
            holding["core_volume"],
            holding["swing_volume"],
            holding["available_volume"],
            sum(p.remaining_volume for p in plans)
            + sum(o.request.volume - o.filled_volume for o in pending),
            amount,
            Decimal(0),
            bool(plans or pending),
          ),
        )
      )
    daily = self._daily_valuation()
    return PortfolioTDecisionSnapshot(
      cut,
      cycle_id,
      self.execution.config_version_id,
      "ashare-intraday-t-assistant",
      "RULE_ONLY",
      self.portfolio_policy,
      tuple(envelopes),
      tuple(IndustryTExposure(i, v, Decimal(0)) for i, v in totals.items()),
      Decimal(str(max(0, self.broker.cash - self.broker._reserved_pending_buy_cash()))),
      Decimal(str(account.total_asset)),
      Decimal(0),
      exposure,
      daily.realized,
      daily.unrealized,
      len(
        {p.plan_id for p in self.plans.plans.values() if p.remaining_volume}
        | {o.request.metadata["exit_plan_id"] for o in self.broker.pending_orders}
      ),
      True,
      False,
      False,
    )

  def _daily_valuation(self):
    previous = self.trading_days[self.trading_days.index(self.day) - 1]
    fills = tuple(
      TDailyFill(
        fill.trade_id,
        fill.metadata["exit_plan_id"],
        fill.instrument_code,
        fill.trade_type.value,
        fill.volume,
        Decimal(str(fill.price)),
        Decimal(str(fill.commission)),
        fill.trade_time,
        index,
        0,
      )
      for index, fill in enumerate(self.broker.trades, 1)
      if fill.trade_time.astimezone(SHANGHAI).date() == self.day
    )
    return value_daily_t_positions(
      as_of=self.now,
      previous_trading_day=previous,
      opening_positions=self.opening_positions,
      opening_marks=self.prior_close_marks.get(previous, {}),
      current_marks={
        c: TValuationMark(
          c, Decimal(str(v.market.price)), v.market.timestamp, v.source_identity
        )
        for c, v in self.latest.items()
      },
      fills=fills,
      mark_max_age_seconds=max(1, self.gate_policy.quote_max_age_ms // 1000),
    )

  async def _entry(self, intent, allocation):
    candidate, original, cursor = decode_candidate_evidence(
      self.evidence[intent.metadata["candidate_fingerprint"]]
    )
    latest = self.latest[intent.instrument_code]
    binding = EntryExecutionBinding(
      candidate.fingerprint,
      self.execution.config_version_id,
      self.execution.config_snapshot_hash,
      candidate.policy_version,
      self.gate_policy.version,
      candidate.feature_schema_version,
      self.capabilities.version,
    )
    result = EntryExecutionGate().evaluate_backtest(
      EntryExecutionGateInput(
        candidate,
        intent.instrument_code,
        intent.intent_id,
        True,
        True,
        int(intent.created_at.timestamp() * 1000),
        candidate.expires_at_ms,
        int(self.now.timestamp() * 1000),
        ExecutionEnvironment.BACKTEST,
        binding,
        binding,
        self.gate_policy,
        self.capabilities,
        original.stream_id,
        original.sample.continuity_generation,
        original.accepted_sequence,
        cursor.ring_generation,
        cursor.ring_generation,
        latest.tick.accepted_sequence,
        latest.tick,
        self.capabilities.required_fields,
      ),
      execution=self.execution,
    )
    self.audit.append(
      {"type": "GATE", "intent": intent.intent_id, "value": json_value(result)}
    )
    if result.decision != "ALLOW":
      return
    holding = self._holding(intent.instrument_code)
    holding["t_trade_exit_capacity"] = self._claim(
      intent.instrument_code
    ).unclaimed_volume
    await self._submit(
      intent,
      OrderType.BUY,
      intent.limit_price_hint,
      holding,
      amount_cap=allocation.allocated_amount_cap,
    )

  async def _submit(
    self, intent, side, price, holding, *, amount_cap=None, sell_cap=None, decision=None
  ):
    policy = TEntryOrderPolicy() if side is OrderType.BUY else TExitOrderPolicy()
    market = self.latest[intent.instrument_code].market
    order_policy = policy.decide_new(
      now=self.now,
      reference_price=price,
      price_tick=0.01,
      limit_up=market.limit_up,
      limit_down=market.limit_down,
    )
    self.audit.append(
      {
        "type": "ORDER_POLICY",
        "intent": intent.intent_id,
        "value": json_value(order_policy),
      }
    )
    if not order_policy.allowed:
      return
    # P4 entry uses its original candidate limit; exit uses the frozen protected
    # price from TExitOrderPolicy, independent of strategy parameter overrides.
    if side is OrderType.SELL:
      price = float(order_policy.limit_price)
    cash = max(0, self.broker.cash - self.broker._reserved_pending_buy_cash())
    account = {
      "cash": cash,
      "total_asset": (await self.broker.get_account()).total_asset,
    }
    draft = OrderSizer(costs=self.costs).draft_intent(
      intent,
      side,
      price,
      account,
      holding,
      allocated_amount_cap=amount_cap,
      sell_volume_cap=sell_cap,
    )
    if not draft.sized_volume:
      self.audit.append(
        {
          "type": "SIZE_REJECT",
          "intent": intent.intent_id,
          "reasons": draft.size_reason_codes,
        }
      )
      return
    request = OrderRequest(
      intent.instrument_code,
      side,
      PriceType.LIMIT,
      draft.sized_volume,
      intent.execution_ref,
      ExecutionEnvironment.BACKTEST,
      price=price,
      metadata={
        **intent.metadata,
        "bucket": intent.bucket,
        "intent_id": intent.intent_id,
        "order_expire_at_ms": int(intent.created_at.timestamp() * 1000)
        + (intent.approval_ttl_ms or 30000),
      },
    )
    risk = await self.risk.evaluate_order(
      request,
      account=account,
      position=holding,
      market_data=self.latest[intent.instrument_code].market,
      current_time=self.now,
      risk_caps={
        "allow_locked_core_substitution": False,
        "protected_core_volume": self.envelope_policies[
          intent.instrument_code
        ].protected_core_volume,
      },
    )
    risk_fact = json_value(risk)
    risk_fact["risk_decision_id"] = f"{intent.intent_id}:risk:{self.now.isoformat()}"
    self.audit.append({"type": "RISK", "intent": intent.intent_id, "value": risk_fact})
    if not risk.allowed:
      return
    request = replace(request, volume=risk.final_volume)
    if decision:
      self.plans.mark_intent(decision, intent.intent_id)
    order = await self.broker.place_order(request)
    if order.status is not OrderStatus.REJECTED:
      if not self.buckets.reserve_order(
        order.order_id,
        instrument_code=intent.instrument_code,
        order_type=side,
        bucket=intent.bucket,
        volume=request.volume,
        price=price,
        metadata=request.metadata,
        substitution_plan=risk.substitution_plan,
      ):
        raise ValueError("BACKTEST_BUCKET_RESERVATION_FAILED")

  async def _exit(self, code):
    market = self.latest[code].market
    context = ExitEvaluationContext(
      self.now,
      market.price,
      bid_price=market.bid_price[0],
      ask_price=market.ask_price[0],
      limit_up=market.limit_up,
      limit_down=market.limit_down,
    )
    for decision in self.plans.evaluate(code, context):
      intent = TradeIntent(
        strategy_id="ashare-intraday-t-assistant",
        instrument_code=code,
        direction=TradeIntentDirection.SELL,
        bucket="swing",
        reason=decision.reason,
        target_volume=decision.volume,
        intent_id=f"{decision.plan_id}:exit:{len(self.broker.orders)}",
        created_at=self.now,
        execution_ref=ExecutionOwnerRef("EXIT_PLAN", decision.plan_id),
        origin=ExitPlanIntentOrigin(
          plan_id=decision.plan_id, source_execution_ref=self.execution.execution_ref
        ),
        metadata={
          "exit_plan_id": decision.plan_id,
          "rule_id": decision.rule_id,
          "allow_t1_substitution": True,
          "source_execution_ref": self.execution.execution_ref.to_dict(),
        },
      )
      claim = self._claim(code, own_plan=decision.plan_id)
      holding = self._holding(code)
      # Risk and BucketLedger receive only the shared capacity service's free
      # legs, so a substitution cannot choose another plan's protected core.
      for bucket, quantity in claim.unclaimed_by_bucket.items():
        holding[f"{bucket}_available_volume"] = quantity
      await self._submit(
        intent,
        OrderType.SELL,
        market.bid_price[0],
        holding,
        sell_cap=claim.unclaimed_volume,
        decision=decision,
      )

  def _conservation(self):
    expected = self.initial_cash
    volumes = dict(self.initial_volumes)
    cash_flows = {code: 0.0 for code in volumes}
    for fill in self.broker.trades:
      sign = -1 if fill.trade_type is OrderType.BUY else 1
      expected += sign * fill.amount - fill.commission
      cash_flows[fill.instrument_code] += sign * fill.amount - fill.commission
      volumes[fill.instrument_code] -= sign * fill.volume
    error = self.broker.cash - expected
    if abs(error) > 1e-7 or self.broker.cash < -1e-7:
      raise ValueError("BACKTEST_CASH_NOT_CONSERVED")
    if any(
      self.broker.positions[c].long_volume != volume for c, volume in volumes.items()
    ):
      raise ValueError("BACKTEST_INVENTORY_NOT_CONSERVED")
    for code in self.states:
      self._claim(code)
    return {
      "cash": self.broker.cash,
      "cash_error": error,
      "volumes": volumes,
      "fees": sum(t.commission for t in self.broker.trades),
      "fill_count": len(self.broker.trades),
      "incremental_pnl_by_symbol": {
        c: cash_flows[c] + (p.long_volume - self.initial_volumes[c]) * p.last_price
        for c, p in self.broker.positions.items()
      },
      "equity": self.broker.cash
      + sum(p.long_volume * p.last_price for p in self.broker.positions.values()),
      "passive_equity": self.initial_cash
      + sum(
        self.initial_volumes[c] * p.last_price for c, p in self.broker.positions.items()
      ),
    }
