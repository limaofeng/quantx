from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import quantx_infrastructure.services.limit_up_board_replay_dataset as module
from quantx_infrastructure.services.limit_up_board_replay_dataset import (
  LimitUpBoardReplayDatasetService,
  build_replay_dataset,
  load_replay_dataset_artifact,
)

SHANGHAI = timezone(timedelta(hours=8))


def _frame(at: datetime, *, code: str = "600000.SH", suffix: str = "1") -> dict:
  candidate = {
    "rank_ordinal": 1,
    "code": code,
    "stage": "NEAR_LIMIT",
    "updated_at": at.isoformat(),
    "is_stale": False,
    "blocked_reasons": [],
    "promotion_eligible": True,
    "promotion_score": 88.0,
    "promotion_model_version": "model-v1",
    "exit_policy_version": "exit-v1",
    "cvar95_loss_pct": 5.0,
    "amount": 100_000_000.0,
  }
  return {
    "id": f"frame-{suffix}",
    "snapshot_key": f"key-{suffix}",
    "trade_date": at.date(),
    "observed_at": at,
    "source_max_at": at,
    "schema_version": 1,
    "snapshot_version": f"snapshot-{suffix}",
    "score_version": "score-v1",
    "feature_version": "feature-v1",
    "model_version": "model-v1",
    "exit_policy_version": "exit-v1",
    "candidate_count": 1,
    "eligible_count": 1,
    "payload": {
      "scanner_running": True,
      "candidates": [candidate],
    },
  }


def _tick(at: datetime, *, code: str = "600000.SH", ordinal: int = 0) -> dict:
  return {
    "stock_code": code,
    "period": "tick",
    "time": at,
    "source_time_ms": int(at.timestamp() * 1000),
    "tick_ordinal": ordinal,
    "last_price": 10.9,
    "open": 10.1,
    "high": 10.9,
    "low": 10.0,
    "last_close": 10.0,
    "amount": 100_000_000.0,
    "volume": 10_000_000.0,
    "pvolume": 10_000_000.0,
    "tickvol": 1_000.0,
    "stock_status": 0,
    "open_int": 0,
    "last_settlement_price": 0.0,
    "settlement_price": 0.0,
    "transaction_num": 1000,
    "price_tick": 0.01,
    "up_stop_price": 11.0,
    "down_stop_price": 9.0,
    "ask_price": [10.91, 10.92, 10.93, 10.94, 10.95],
    "bid_price": [10.90, 10.89, 10.88, 10.87, 10.86],
    "ask_vol": [1000, 2000, 3000, 4000, 5000],
    "bid_vol": [1000, 2000, 3000, 4000, 5000],
  }


def _build(
  frames: list[dict],
  ticks: list[dict],
  *,
  settings: dict | None = None,
  start: datetime | None = None,
  end: datetime | None = None,
):
  start = start or frames[0]["observed_at"]
  end = end or frames[-1]["observed_at"]
  return build_replay_dataset(
    frames,
    ticks=ticks,
    settings=settings or {"max_ranked_candidates": 2},
    expected_trading_dates=[start.astimezone(SHANGHAI).date()],
    source="POINT_IN_TIME_UNIVERSE_V1",
    requested_start_time=start,
    requested_end_time=end,
  )


def test_dataset_and_config_fingerprints_are_separate_and_stable() -> None:
  first = datetime(2026, 8, 20, 9, 30, tzinfo=SHANGHAI)
  second = first + timedelta(seconds=5)
  frames = [_frame(first), _frame(second, suffix="2")]
  ticks = [_tick(first), _tick(second)]

  baseline = _build(frames, ticks, start=first, end=second)
  reordered = _build(list(reversed(frames)), list(reversed(ticks)), start=first, end=second)
  changed_config = _build(
    frames,
    ticks,
    settings={"max_ranked_candidates": 1},
    start=first,
    end=second,
  )

  assert baseline.dataset_fingerprint == reordered.dataset_fingerprint
  assert baseline.config_fingerprint == reordered.config_fingerprint
  assert baseline.dataset_fingerprint == changed_config.dataset_fingerprint
  assert baseline.config_fingerprint != changed_config.config_fingerprint
  assert baseline.data_quality["status"] == "OK"
  assert baseline.data_quality["executable"] is True


def test_materialization_is_deterministic_and_loader_checks_both_hashes(
  tmp_path: Path,
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  at = datetime(2026, 8, 20, 9, 30, tzinfo=SHANGHAI)
  dataset = _build([_frame(at)], [_tick(at)])
  monkeypatch.setattr(module, "REPLAY_DATASET_ROOT", tmp_path)

  first = LimitUpBoardReplayDatasetService.persist_artifact(dataset, job_id="job-1")
  second = LimitUpBoardReplayDatasetService.persist_artifact(dataset, job_id="job-2")
  loaded = load_replay_dataset_artifact(first.manifest_path)

  assert loaded["events"] == list(dataset.events)
  assert loaded["ticks"] == list(dataset.ticks)
  assert loaded["dataset_fingerprint"] == dataset.dataset_fingerprint
  assert loaded["config_fingerprint"] == dataset.config_fingerprint
  assert loaded["artifacts"]["candidate_universe"]["content_sha256"]
  assert loaded["artifacts"]["candidate_universe"]["file_sha256"]
  assert loaded["artifacts"]["raw_ticks"]["content_sha256"]
  assert loaded["artifacts"]["raw_ticks"]["file_sha256"]
  assert (
    loaded["artifacts"]["raw_ticks"]["file_sha256"]
    == second.input_manifest["artifacts"]["raw_ticks"]["file_sha256"]
  )

  tick_path = Path(first.manifest_path).parent / module.REPLAY_TICK_ARTIFACT
  damaged = bytearray(tick_path.read_bytes())
  damaged[len(damaged) // 2] ^= 0x01
  tick_path.write_bytes(damaged)
  with pytest.raises(ValueError, match="SHA256|gzip"):
    load_replay_dataset_artifact(first.manifest_path)


@pytest.mark.parametrize(
  ("mutation", "blocker"),
  [
    (lambda row: row.pop("up_stop_price"), "RAW_TICKS_MISSING_NATIVE_PRICE_LIMITS"),
    (lambda row: row.pop("stock_status"), "RAW_TICKS_MISSING_STOCK_STATUS"),
    (lambda row: row.pop("price_tick"), "RAW_TICKS_MISSING_PRICE_TICK"),
    (
      lambda row: row.update({"ask_vol": row["ask_vol"][:4]}),
      "RAW_TICKS_MISSING_FIVE_LEVEL_BOOK",
    ),
  ],
)
def test_missing_execution_fields_are_blockers_and_are_not_filled(
  mutation,
  blocker: str,
) -> None:
  at = datetime(2026, 8, 20, 9, 30, tzinfo=SHANGHAI)
  tick = _tick(at)
  mutation(tick)

  dataset = _build([_frame(at)], [tick])

  assert dataset.data_quality["status"] == "BLOCKED"
  assert dataset.data_quality["executable"] is False
  assert blocker in dataset.data_quality["blockers"]
  if blocker == "RAW_TICKS_MISSING_NATIVE_PRICE_LIMITS":
    assert dataset.ticks[0]["up_stop_price"] is None
  if blocker == "RAW_TICKS_MISSING_FIVE_LEVEL_BOOK":
    assert dataset.ticks[0]["ask_vol"] == [1000, 2000, 3000, 4000]


def test_lunch_break_is_not_counted_as_a_frame_gap() -> None:
  times = [
    datetime(2026, 8, 20, 11, 29, 55, tzinfo=SHANGHAI),
    datetime(2026, 8, 20, 11, 30, 0, tzinfo=SHANGHAI),
    datetime(2026, 8, 20, 13, 0, 0, tzinfo=SHANGHAI),
    datetime(2026, 8, 20, 13, 0, 5, tzinfo=SHANGHAI),
  ]
  frames = [_frame(value, suffix=str(index)) for index, value in enumerate(times)]
  ticks = [_tick(value, ordinal=index) for index, value in enumerate(times)]

  dataset = _build(frames, ticks, start=times[0], end=times[-1])

  coverage = dataset.data_quality["coverage"]
  assert coverage["frame_gaps_over_15_seconds"] == 0
  assert coverage["session_boundary_gaps_over_15_seconds"] == 0
  assert coverage["missing_continuous_sessions"] == []
  assert dataset.data_quality["status"] == "OK"


def test_legacy_sparse_source_is_never_executable() -> None:
  at = datetime(2026, 8, 20, 9, 30, tzinfo=SHANGHAI)
  dataset = build_replay_dataset(
    [_frame(at)],
    ticks=[_tick(at)],
    settings={},
    expected_trading_dates=[at.date()],
    source="LEGACY_SPARSE_PROMOTION_FACTS",
    requested_start_time=at,
    requested_end_time=at,
  )

  assert dataset.data_quality["executable"] is False
  assert "LEGACY_SPARSE_CANDIDATE_FACTS" in dataset.data_quality["blockers"]
