from datetime import datetime

import pytest
from quantx_domain.trading.exit_plan import (
  ExitEvaluationContext,
  ExitPlanBook,
  ExitPlanCommand,
  ExitPlanCommandType,
  ExitPlanEvaluator,
  ExitPlanStatus,
  ExitPlanTemplate,
  ExitRuleMatch,
  ExitRuleSpec,
  ExitRuleType,
  ExitSizingMode,
  ExitSizingPolicy,
  ExitStrategyRegistry,
  ExitT1Policy,
)


def template(*rules, version=1):
  return ExitPlanTemplate(
    plan_id="plan-1",
    source_type="TEST_ENTRY",
    source_id="batch-1",
    account_id="account-1",
    instrument_code="600000.SH",
    bucket="swing",
    rules=list(rules),
    run_id="run-1",
    strategy_id="strategy-1",
    config_version=version,
    t1_policy=ExitT1Policy.WAIT_UNTIL_SELLABLE,
    auto_exit_authorized=True,
  )


def context(price, timestamp=None):
  return ExitEvaluationContext(
    timestamp=timestamp or datetime(2026, 7, 30, 10, 0),
    current_price=price,
    bid_price=price - 0.01,
    ask_price=price + 0.01,
  )


def test_plan_chooses_highest_priority_triggered_sell_strategy():
  book = ExitPlanBook()
  book.register_entry_fill(
    template(
      ExitRuleSpec(
        rule_id="profit",
        strategy=ExitRuleType.GROSS_TAKE_PROFIT,
        priority=500,
        parameters={"target_profit_pct": 2.0},
      ),
      ExitRuleSpec(
        rule_id="time",
        strategy=ExitRuleType.TIME_OF_DAY,
        priority=800,
        parameters={"exit_time": "09:45"},
      ),
    ),
    volume=1000,
    price=10.0,
    trade_time=datetime(2026, 7, 30, 9, 35),
  )

  [decision] = book.evaluate("600000.SH", context(10.3))

  assert decision.rule_id == "time"
  assert decision.volume == 1000
  assert decision.reason == "TIME_OF_DAY_REACHED"


def test_trailing_profit_floor_is_stateful_and_never_moves_down():
  book = ExitPlanBook()
  plan = book.register_entry_fill(
    template(
      ExitRuleSpec(
        rule_id="trailing",
        strategy=ExitRuleType.TRAILING_NET_PROFIT,
        parameters={
          "target_profit_pct": 2.0,
          "base_floor_pct": 0.5,
          "initial_gap_pct": 1.5,
          "gap_slope": 0.25,
          "max_gap_pct": 3.0,
        },
      )
    ),
    volume=1000,
    price=10.0,
  )

  assert book.evaluate("600000.SH", context(10.25)) == []
  armed_floor = plan.trailing_floor_pct
  assert armed_floor is not None
  assert book.evaluate("600000.SH", context(10.5)) == []
  raised_floor = plan.trailing_floor_pct
  assert raised_floor >= armed_floor

  [decision] = book.evaluate("600000.SH", context(10.2))

  assert decision.rule_id == "trailing"
  assert plan.trailing_floor_pct == raised_floor


def test_price_trailing_rule_stays_armed_after_current_profit_drops_below_arm_level():
  book = ExitPlanBook()
  book.register_entry_fill(
    template(
      ExitRuleSpec(
        rule_id="price-trailing",
        strategy=ExitRuleType.TRAILING_PRICE_DRAWDOWN,
        parameters={
          "arm_profit_pct": 5.0,
          "drawdown_pct": 3.0,
        },
      )
    ),
    volume=1000,
    price=10.0,
  )

  assert book.evaluate("600000.SH", context(11.0)) == []
  [decision] = book.evaluate("600000.SH", context(10.4))

  assert decision.rule_id == "price-trailing"
  assert decision.metrics["peak_gross_profit_pct"] == pytest.approx(10.0)


def test_staged_once_rule_uses_its_own_sell_sizing():
  book = ExitPlanBook()
  plan = book.register_entry_fill(
    template(
      ExitRuleSpec(
        rule_id="first-stage",
        strategy=ExitRuleType.TARGET_PRICE,
        parameters={"target_price": 11.0},
        sizing=ExitSizingPolicy(
          mode=ExitSizingMode.PERCENT_REMAINING,
          value=50,
        ),
        once=True,
      )
    ),
    volume=1000,
    price=10.0,
  )
  [decision] = book.evaluate("600000.SH", context(11.0))
  book.mark_intent(decision, "intent-1")
  book.apply_exit_fill(plan_id=plan.plan_id, volume=500, price=11.0)
  book.apply_order_event(
    plan_id=plan.plan_id,
    intent_id="intent-1",
    status="FILLED",
    order_id="order-1",
  )

  assert plan.status == ExitPlanStatus.PARTIALLY_EXITED
  assert plan.remaining_volume == 500
  assert "first-stage" in plan.completed_rule_ids
  assert book.evaluate("600000.SH", context(11.2)) == []


def test_filled_order_before_trade_report_keeps_plan_pending_until_fill_arrives():
  book = ExitPlanBook()
  plan = book.register_entry_fill(
    template(
      ExitRuleSpec(
        rule_id="first-stage",
        strategy=ExitRuleType.TARGET_PRICE,
        parameters={"target_price": 11.0},
        sizing=ExitSizingPolicy(
          mode=ExitSizingMode.PERCENT_REMAINING,
          value=50,
        ),
        once=True,
      )
    ),
    volume=1000,
    price=10.0,
  )
  [decision] = book.evaluate("600000.SH", context(11.0))
  book.mark_intent(decision, "intent-1")

  book.apply_order_event(
    plan_id=plan.plan_id,
    intent_id="intent-1",
    status="FILLED",
    order_id="order-1",
  )

  assert plan.status == ExitPlanStatus.EXIT_PENDING
  assert plan.pending_order_terminal is True
  assert book.evaluate("600000.SH", context(11.2)) == []

  book.apply_exit_fill(plan_id=plan.plan_id, volume=500, price=11.0)

  assert plan.status == ExitPlanStatus.PARTIALLY_EXITED
  assert plan.pending_intent_id == ""
  assert "first-stage" in plan.completed_rule_ids
  assert book.evaluate("600000.SH", context(11.2)) == []


def test_partially_filled_once_rule_retries_only_its_unfilled_stage_volume():
  book = ExitPlanBook()
  plan = book.register_entry_fill(
    template(
      ExitRuleSpec(
        rule_id="first-stage",
        strategy=ExitRuleType.TARGET_PRICE,
        parameters={"target_price": 11.0},
        sizing=ExitSizingPolicy(
          mode=ExitSizingMode.PERCENT_REMAINING,
          value=50,
        ),
        once=True,
      )
    ),
    volume=1000,
    price=10.0,
  )
  [first] = book.evaluate("600000.SH", context(11.0))
  book.mark_intent(first, "intent-1")
  book.apply_exit_fill(
    plan_id=plan.plan_id,
    volume=200,
    price=11.0,
    rule_id="first-stage",
  )
  book.apply_order_event(
    plan_id=plan.plan_id,
    intent_id="intent-1",
    status="CANCELLED",
  )

  [retry] = book.evaluate("600000.SH", context(11.2))

  assert retry.volume == 300
  assert plan.rule_target_volumes["first-stage"] == 500
  assert plan.rule_filled_volumes["first-stage"] == 200


def test_late_trade_after_cancel_updates_position_without_leaving_false_pending():
  book = ExitPlanBook()
  plan = book.register_entry_fill(
    template(
      ExitRuleSpec(
        rule_id="first-stage",
        strategy=ExitRuleType.TARGET_PRICE,
        parameters={"target_price": 11.0},
        sizing=ExitSizingPolicy(
          mode=ExitSizingMode.PERCENT_REMAINING,
          value=50,
        ),
        once=True,
      )
    ),
    volume=1000,
    price=10.0,
  )
  [decision] = book.evaluate("600000.SH", context(11.0))
  book.mark_intent(decision, "intent-1")
  book.apply_order_event(
    plan_id=plan.plan_id,
    intent_id="intent-1",
    status="CANCELLED",
  )

  book.apply_exit_fill(
    plan_id=plan.plan_id,
    volume=200,
    price=11.0,
    rule_id="first-stage",
  )

  assert plan.status == ExitPlanStatus.PARTIALLY_EXITED
  assert plan.pending_intent_id == ""
  [retry] = book.evaluate("600000.SH", context(11.2))
  assert retry.volume == 300


def test_rejected_exit_releases_plan_for_a_fresh_evaluation():
  book = ExitPlanBook()
  plan = book.register_entry_fill(
    template(
      ExitRuleSpec(
        rule_id="stop",
        strategy=ExitRuleType.STOP_PRICE,
        parameters={"stop_price": 9.8},
      )
    ),
    volume=100,
    price=10.0,
  )
  [decision] = book.evaluate("600000.SH", context(9.7))
  book.mark_intent(decision, "intent-1")

  book.apply_order_event(
    plan_id=plan.plan_id,
    intent_id="intent-1",
    status="REJECTED",
  )

  assert plan.pending_intent_id == ""
  assert plan.status == ExitPlanStatus.ACTIVE
  assert book.evaluate("600000.SH", context(9.7))


def test_stale_order_report_cannot_mutate_a_released_plan():
  book = ExitPlanBook()
  plan = book.register_entry_fill(
    template(
      ExitRuleSpec(
        rule_id="stop",
        strategy=ExitRuleType.STOP_PRICE,
        parameters={"stop_price": 9.8},
      )
    ),
    volume=100,
    price=10.0,
  )
  [decision] = book.evaluate("600000.SH", context(9.7))
  book.mark_intent(decision, "intent-1")
  book.apply_order_event(
    plan_id=plan.plan_id,
    intent_id="intent-1",
    status="REJECTED",
  )

  book.apply_order_event(
    plan_id=plan.plan_id,
    intent_id="intent-1",
    status="FILLED",
    order_id="late-order",
  )

  assert plan.status == ExitPlanStatus.ACTIVE
  assert plan.pending_order_id == ""


def test_policy_update_preserves_fills_and_replaces_sell_strategies():
  book = ExitPlanBook()
  plan = book.register_entry_fill(
    template(
      ExitRuleSpec(
        rule_id="old",
        strategy=ExitRuleType.TARGET_PRICE,
        parameters={"target_price": 12.0},
      )
    ),
    volume=300,
    price=10.0,
  )
  updated = template(
    ExitRuleSpec(
      rule_id="new",
      strategy=ExitRuleType.HARD_STOP,
      parameters={"stop_loss_pct": -1.0},
    ),
    version=2,
  )

  book.apply_command(
    ExitPlanCommand(
      command=ExitPlanCommandType.UPSERT_POLICY,
      plan_id=plan.plan_id,
      template=updated,
    )
  )

  assert plan.entry_filled_volume == 300
  assert plan.entry_avg_price == 10.0
  assert plan.template.config_version == 2
  assert plan.template.rules[0].rule_id == "new"


def test_custom_sell_strategy_can_be_registered_without_changing_plan_book():
  registry = ExitStrategyRegistry.builtins()
  registry.register(
    "LIMIT_UP_BREAK",
    lambda rule, plan, ctx: ExitRuleMatch(
      bool(rule.parameters.get("broken")),
      "LIMIT_UP_BOARD_BROKEN",
    ),
  )
  rule = ExitRuleSpec(
    rule_id="board-break",
    strategy="LIMIT_UP_BREAK",
    parameters={"broken": True},
  )
  custom_template = template(rule)
  book = ExitPlanBook()
  book.evaluator.registry = registry
  book.register_entry_fill(custom_template, volume=100, price=10.0)

  [decision] = book.evaluate("600000.SH", context(10.0))

  assert decision.rule_type == "LIMIT_UP_BREAK"
  assert decision.reason == "LIMIT_UP_BOARD_BROKEN"


def test_custom_sell_strategy_registry_is_reused_when_plan_book_is_restored():
  registry = ExitStrategyRegistry.builtins()
  registry.register(
    "LIMIT_UP_BREAK",
    lambda rule, plan, ctx: ExitRuleMatch(True, "LIMIT_UP_BOARD_BROKEN"),
  )
  book = ExitPlanBook()
  book.register_entry_fill(
    template(
      ExitRuleSpec(
        rule_id="board-break",
        strategy="LIMIT_UP_BREAK",
      )
    ),
    volume=100,
    price=10.0,
  )

  restored = ExitPlanBook.from_dict(
    book.to_dict(),
    evaluator=ExitPlanEvaluator(registry),
  )

  [decision] = restored.evaluate("600000.SH", context(10.0))
  assert decision.reason == "LIMIT_UP_BOARD_BROKEN"


def test_book_round_trip_preserves_t1_and_execution_state():
  book = ExitPlanBook()
  plan = book.register_entry_fill(
    template(
      ExitRuleSpec(
        rule_id="target",
        strategy=ExitRuleType.TARGET_PRICE,
        parameters={"target_price": 11.0},
      )
    ),
    volume=200,
    price=10.0,
  )
  restored = ExitPlanBook.from_dict(book.to_dict())
  restored_plan = restored.plans[plan.plan_id]

  assert restored_plan.remaining_volume == 200
  assert restored_plan.template.t1_policy == ExitT1Policy.WAIT_UNTIL_SELLABLE
  assert restored_plan.template.auto_exit_authorized is True


def test_pruning_terminal_history_never_removes_active_plans():
  book = ExitPlanBook()
  for index in range(3):
    item = book.register_entry_fill(
      ExitPlanTemplate(
        **{
          **template(
            ExitRuleSpec(
              rule_id=f"target-{index}",
              strategy=ExitRuleType.TARGET_PRICE,
              parameters={"target_price": 11.0},
            )
          ).to_dict(),
          "plan_id": f"plan-{index}",
        }
      ),
      volume=100,
      price=10.0,
    )
    if index < 2:
      item.exited_volume = 100
      item.status = ExitPlanStatus.COMPLETED

  removed = book.prune_terminal(max_terminal=1)

  assert removed == ["plan-0"]
  assert "plan-1" in book.plans
  assert "plan-2" in book.plans
