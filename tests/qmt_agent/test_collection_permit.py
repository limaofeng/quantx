"""Native-unit identities and durable permit fences; no SDK or live trading calls."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError
from quantx_contracts.collection_permit import (
  CollectionPermit,
  CollectionUnit,
  native_payload_sha256,
  plan_historical_work_units,
)
from quantx_qmt_agent.historical_worker import historical_work_units
from quantx_qmt_agent.journal import LocalJournal

NOW = datetime(2026, 9, 9, 10, tzinfo=timezone.utc)
DEVICE = "22222222-2222-4222-8222-222222222222"
REQUEST = "11111111-1111-4111-8111-111111111111"


def payload():
  return {
    "operation": "bars",
    "stock_list": ["600000.SH", "000001.SZ"],
    "periods": ["1m"],
    "start_time": "20260901",
    "end_time": "20260903",
    "download": True,
  }


def permit(unit=None, epoch=1):
  return CollectionPermit(
    permit_id=uuid4(),
    device_id=DEVICE,
    owner_epoch=epoch,
    unit=unit or CollectionUnit.from_payload(REQUEST, 0, payload()),
    issued_at=NOW,
    expires_at=NOW + timedelta(seconds=15),
  )


def test_server_and_agent_share_native_unit_plan_and_stable_identity():
  original = payload()
  server = plan_historical_work_units(original)
  assert server == historical_work_units(original)
  assert len(server) == 3
  assert all(unit["start_time"] == unit["end_time"] for unit in server)
  identities = [
    CollectionUnit.from_payload(REQUEST, i, unit) for i, unit in enumerate(server)
  ]
  assert len({unit.unit_id for unit in identities}) == 3
  decorated = {
    **server[0],
    "request_id": REQUEST,
    "upload_path": "/agent/market-data/x",
    "collection_permit": {"permit_id": "transport-only"},
  }
  assert native_payload_sha256(decorated) == native_payload_sha256(server[0])
  assert (
    native_payload_sha256({**server[0], "download": False})
    != identities[0].payload_sha256
  )
  assert (
    CollectionUnit.model_validate_json(identities[0].model_dump_json()) == identities[0]
  )
  assert original["stock_list"] == ["600000.SH", "000001.SZ"]


def test_split_does_not_bypass_complete_agent_request_budget():
  with pytest.raises(ValueError, match="estimated record count"):
    historical_work_units(
      {
        **payload(),
        "stock_list": [f"{i:06d}.SZ" for i in range(30)],
        "periods": ["tick"],
      }
    )


@pytest.mark.parametrize("delta", [-1, 0, 31])
def test_permit_validity_must_be_short_and_positive(delta):
  data = permit().model_dump()
  data["expires_at"] = NOW + timedelta(seconds=delta)
  with pytest.raises(ValidationError):
    CollectionPermit.model_validate(data)


@pytest.mark.parametrize(
  "now", [NOW - timedelta(microseconds=1), NOW + timedelta(seconds=15)]
)
def test_permit_rejects_future_and_expired_start(now):
  value = permit()
  with pytest.raises(ValueError, match="expired or not active"):
    value.validate_start(device_id=DEVICE, unit=value.unit, now=now)


def test_journal_fence_and_unit_binding_survive_restart(tmp_path):
  path = tmp_path / "journal.sqlite3"
  value = permit(epoch=8)
  journal = LocalJournal(path)
  assert journal.accept_collection_permit(
    value, device_id=DEVICE, unit=value.unit, now=NOW
  )
  assert not journal.accept_collection_permit(
    value, device_id=DEVICE, unit=value.unit, now=NOW
  )
  journal.connection.close()
  journal = LocalJournal(path)
  renewal = permit(epoch=9)
  assert not journal.accept_collection_permit(
    renewal, device_id=DEVICE, unit=renewal.unit, now=NOW
  )
  with pytest.raises(ValueError, match="stale"):
    journal.accept_collection_permit(value, device_id=DEVICE, unit=value.unit, now=NOW)
  changed_unit = CollectionUnit.from_payload(
    REQUEST, 0, {**payload(), "download": False}
  )
  conflict = permit(changed_unit, epoch=10)
  with pytest.raises(ValueError, match="conflicts"):
    journal.accept_collection_permit(
      conflict, device_id=DEVICE, unit=changed_unit, now=NOW
    )
  # A rejected conflict must not advance the durable owner watermark.
  assert not journal.accept_collection_permit(
    renewal, device_id=DEVICE, unit=renewal.unit, now=NOW
  )
  assert journal.connection.execute("SELECT count(*) FROM commands").fetchone()[0] == 0
  assert journal.connection.execute("SELECT count(*) FROM reports").fetchone()[0] == 0
  journal.connection.close()


def test_invalid_scope_cannot_modify_journal(tmp_path):
  journal = LocalJournal(tmp_path / "journal.sqlite3")
  value = permit()
  with pytest.raises(ValueError, match="scope mismatch"):
    journal.accept_collection_permit(
      value, device_id=str(uuid4()), unit=value.unit, now=NOW
    )
  with pytest.raises(ValueError, match="scope mismatch"):
    journal.accept_collection_permit(
      value,
      device_id=DEVICE,
      unit=CollectionUnit.from_payload(REQUEST, 1, payload()),
      now=NOW,
    )
  assert (
    journal.connection.execute(
      "SELECT count(*) FROM history_collection_receipts"
    ).fetchone()[0]
    == 0
  )
  journal.connection.close()


def test_reusing_permit_id_for_another_unit_or_validity_is_rejected(tmp_path):
  journal = LocalJournal(tmp_path / "journal.sqlite3")
  original = permit()
  journal.accept_collection_permit(
    original, device_id=DEVICE, unit=original.unit, now=NOW
  )
  for fields in (
    {"unit": CollectionUnit.from_payload(REQUEST, 1, payload())},
    {"expires_at": NOW + timedelta(seconds=20)},
  ):
    changed = CollectionPermit.model_validate({**original.model_dump(), **fields})
    with pytest.raises(ValueError, match="ID conflicts"):
      journal.accept_collection_permit(
        changed, device_id=DEVICE, unit=changed.unit, now=NOW
      )
  journal.connection.close()


def test_epoch_check_is_serialized_between_journal_connections(tmp_path):
  import threading
  from concurrent.futures import ThreadPoolExecutor

  path = tmp_path / "journal.sqlite3"
  first, second = LocalJournal(path), LocalJournal(path)
  entered, attempted, release = threading.Event(), threading.Event(), threading.Event()

  def pause_after_write_lock(sql):
    if sql.startswith("SELECT value FROM journal_metadata"):
      entered.set()
      assert release.wait(3)

  first.connection.set_trace_callback(pause_after_write_lock)

  def see_second_attempt(sql):
    if sql == "BEGIN IMMEDIATE":
      attempted.set()

  second.connection.set_trace_callback(see_second_attempt)
  low, high = permit(epoch=8), permit(epoch=9)
  with ThreadPoolExecutor(max_workers=2) as workers:
    older = workers.submit(
      first.accept_collection_permit, low, device_id=DEVICE, unit=low.unit, now=NOW
    )
    try:
      assert entered.wait(2)
      newer = workers.submit(
        second.accept_collection_permit, high, device_id=DEVICE, unit=high.unit, now=NOW
      )
      assert attempted.wait(2)
      assert not newer.done()
    finally:
      release.set()
    assert older.result(timeout=3)
    assert not newer.result(timeout=3)
  with pytest.raises(ValueError, match="stale"):
    first.accept_collection_permit(low, device_id=DEVICE, unit=low.unit, now=NOW)
  first.connection.close()
  second.connection.close()


def test_planner_bounds_catalog_before_allocating_all_units():
  request = {
    "operation": "bars",
    "stock_list": ["000001.SZ"],
    "periods": ["tick"],
    "start_time": "20000101",
    "end_time": "20991231",
  }
  with pytest.raises(ValueError, match="bounded catalog"):
    plan_historical_work_units(request)
