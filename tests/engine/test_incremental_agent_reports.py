import json
from hashlib import sha256
from types import SimpleNamespace

import pytest
from quantx_engine import report_processor


@pytest.mark.asyncio
async def test_partial_delta_uses_position_delta_without_full_snapshot(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  calls = SimpleNamespace(delta=[], full=[])

  class FakePositionService:
    async def apply_position_delta(self, value, account_id):
      calls.delta.append((value, account_id))

    async def apply_full_snapshot(self, **kwargs):
      calls.full.append(kwargs)

  monkeypatch.setattr(report_processor, "PositionService", FakePositionService)
  monkeypatch.setattr(
    report_processor,
    "_upsert_account",
    lambda value: _async_noop(value),
  )

  await report_processor._process_delta_report(
    "device-1",
    {
      "account_id": "account-1",
      "position_deltas": [
        {
          "stock_code": "600000.SH",
          "volume": 100,
          "can_use_volume": 0,
        }
      ],
      "is_complete": False,
      "sequence": 100,
    },
  )

  assert calls.full == []
  assert calls.delta == [
    (
      {
        "stock_code": "600000.SH",
        "volume": 100,
        "can_use_volume": 0,
      },
      "account-1",
    )
  ]


@pytest.mark.asyncio
async def test_complete_delta_still_applies_authoritative_snapshot(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  calls = SimpleNamespace(delta=[], full=[])

  class FakePositionService:
    async def apply_position_delta(self, value, account_id):
      calls.delta.append((value, account_id))

    async def apply_full_snapshot(self, **kwargs):
      calls.full.append(kwargs)

  class FakeDatabase:
    async def __aenter__(self):
      return self

    async def __aexit__(self, *_):
      return None

    async def get(self, *_, **__):
      return None

    def add(self, _value):
      return None

    async def commit(self):
      return None

  monkeypatch.setattr(report_processor, "PositionService", FakePositionService)
  monkeypatch.setattr(report_processor, "AsyncSessionLocal", FakeDatabase)
  monkeypatch.setattr(
    report_processor,
    "_upsert_account",
    lambda value: _async_noop(value),
  )
  monkeypatch.setattr(
    report_processor,
    "_snapshot_discrepancies",
    lambda *_args, **_kwargs: _async_result(
      {
        "blocking_discrepancies": [],
        "external_orders": [],
        "external_trades": [],
      }
    ),
  )

  payload = {
    "snapshot_id": "snapshot-101",
    "positions_by_account": {"account-1": []},
    "accounts": [{"account_id": "account-1"}],
    "orders": [],
    "trades": [],
    "section_completeness_by_account": {
      "account-1": {
        "account": True,
        "positions": True,
        "orders": True,
        "trades": True,
      }
    },
    "unavailable_accounts": [],
    "is_complete": True,
    "sequence": 101,
  }
  payload["snapshot_hash"] = sha256(
    json.dumps(
      payload,
      sort_keys=True,
      separators=(",", ":"),
      default=str,
    ).encode("utf-8")
  ).hexdigest()

  await report_processor._process_delta_report(
    "device-1",
    payload,
    protocol_version="1.1",
  )

  assert calls.delta == []
  assert len(calls.full) == 1
  assert calls.full[0]["account_id"] == "account-1"
  assert calls.full[0]["is_complete"] is True


async def _async_noop(_value):
  return None


async def _async_result(value):
  return value


@pytest.mark.asyncio
async def test_engine_restart_recovers_every_processing_report(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  statements: list[str] = []

  class FakeDatabase:
    async def __aenter__(self):
      return self

    async def __aexit__(self, *_):
      return None

    async def execute(self, statement):
      statements.append(str(statement))

    async def commit(self):
      return None

  monkeypatch.setattr(report_processor, "AsyncSessionLocal", FakeDatabase)

  await report_processor._recover_stuck_reports()

  assert len(statements) == 1
  assert "processing_status = :processing_status_1" in statements[0]
  assert "received_at" not in statements[0]
