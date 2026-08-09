from types import SimpleNamespace

from quantx_engine.report_processor import _broker_order_ids


def test_delta_report_collects_unique_order_ids_for_subscription_wakeups() -> None:
  report = SimpleNamespace(
    message_type="delta_report",
    payload={
      "orders": [
        {"order_id": 11},
        {"broker_order_id": "12"},
      ],
      "trades": [
        {"order_id": 11},
        {"broker_order_id": "not-an-integer"},
      ],
    },
  )

  assert _broker_order_ids(report) == [11, 12]
