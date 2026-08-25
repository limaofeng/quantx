from datetime import datetime

import pytest
from quantx_domain.trading.exit_plan import ExitPlanTemplate
from quantx_infrastructure.services.exit_plan_replay_service import (
  ExitPlanReplayService,
)


@pytest.mark.asyncio
async def test_buy_fill_origin_activates_after_last_selected_fill(monkeypatch) -> None:
  service = ExitPlanReplayService()
  template = ExitPlanTemplate.from_dict(
    {
      "plan_id": "plan-1",
      "source_type": "MANUAL_EXIT_PLAN",
      "source_id": "source-1",
      "account_id": "account-1",
      "instrument_code": "000001.SZ",
      "bucket": "manual",
      "rules": [
        {
          "rule_id": "rule-1",
          "strategy": "TARGET_PRICE",
          "parameters": {"target_price": 12.0},
        }
      ],
    }
  )
  fills = [
    {
      "order_id": "1",
      "traded_volume": 100,
      "traded_price": 10.0,
      "estimated_buy_fee_cny": 5.01,
      "order_time": datetime(2026, 8, 3, 10, 0),
    },
    {
      "order_id": "2",
      "traded_volume": 200,
      "traded_price": 11.0,
      "estimated_buy_fee_cny": 5.02,
      "order_time": datetime(2026, 8, 4, 11, 0),
    },
  ]

  async def fake_load_buy_orders(**_kwargs):
    return fills

  monkeypatch.setattr(service, "_load_buy_orders", fake_load_buy_orders)

  origin = await service._resolve_origin(
    {"origin": {"mode": "BUY_FILLS", "order_ids": ["1", "2"]}},
    template,
    account_id="account-1",
  )

  assert origin["activation_time"] == datetime(2026, 8, 4, 11, 0)
  assert origin["volume"] == 300
  assert origin["unit_cost"] == pytest.approx((1000 + 2200 + 10.03) / 300)


@pytest.mark.asyncio
async def test_manual_origin_rejects_non_positive_snapshot() -> None:
  service = ExitPlanReplayService()
  template = ExitPlanTemplate.from_dict(
    {
      "plan_id": "plan-1",
      "account_id": "account-1",
      "instrument_code": "000001.SZ",
      "bucket": "manual",
      "rules": [{"strategy": "TARGET_PRICE", "parameters": {}}],
    }
  )

  with pytest.raises(ValueError, match="正数数量"):
    await service._resolve_origin(
      {
        "origin": {
          "mode": "MANUAL_SNAPSHOT",
          "activation_time": "2026-08-03T09:30:00",
          "volume": 0,
          "unit_cost": 10.0,
        }
      },
      template,
      account_id="account-1",
    )

