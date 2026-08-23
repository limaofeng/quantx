from types import SimpleNamespace

from quantx_api.monitoring.metrics import (
  T_TRADE_V3_ACCUMULATOR_STATE,
  T_TRADE_V3_ACTIVE_STREAMS,
  T_TRADE_V3_PROJECTION_VALUE,
  T_TRADE_V3_RUNTIME_VALUE,
  _set_t_trade_v3_engine_metrics,
)


def test_engine_heartbeat_exports_bounded_t_trade_v3_metrics() -> None:
  heartbeat = SimpleNamespace(
    instance_id="engine-test",
    details={
      "tTradeV3": {
        "schemaVersion": 2,
        "activeStreamCount": 2,
        "streamCapacity": 4096,
        "streamEvictionsTotal": 1,
        "seriesCount": 1,
        "seriesCapacity": 1024,
        "seriesOverflowUpdatesTotal": 3,
        "series": [
          {
            "metric": "inputs_total",
            "path": "PULLBACK_REBOUND",
            "health": "READY",
            "detail": "TOTAL",
            "value": 7,
          }
        ],
      },
      "tTradeProjection": {
        "schemaVersion": 1,
        "counters": {"coalesced_replacements_total": 3},
        "pendingNoticeCount": 1,
        "activeNoticeTaskCount": 1,
      },
    },
  )

  _set_t_trade_v3_engine_metrics(heartbeat)

  assert T_TRADE_V3_ACTIVE_STREAMS.labels(engine_instance="ENGINE-TEST")._value.get() == 2
  assert (
    T_TRADE_V3_RUNTIME_VALUE.labels(
      engine_instance="ENGINE-TEST",
      metric="INPUTS_TOTAL",
      path="PULLBACK_REBOUND",
      health="READY",
      detail="TOTAL",
    )._value.get()
    == 7
  )
  assert (
    T_TRADE_V3_ACCUMULATOR_STATE.labels(
      engine_instance="ENGINE-TEST",
      measure="SERIES_OVERFLOW_UPDATES_TOTAL",
    )._value.get()
    == 3
  )
  assert (
    T_TRADE_V3_ACCUMULATOR_STATE.labels(
      engine_instance="ENGINE-TEST",
      measure="STREAM_EVICTIONS_TOTAL",
    )._value.get()
    == 1
  )
  assert (
    T_TRADE_V3_ACCUMULATOR_STATE.labels(
      engine_instance="ENGINE-TEST",
      measure="SNAPSHOT_REJECTED",
    )._value.get()
    == 0
  )
  assert (
    T_TRADE_V3_PROJECTION_VALUE.labels(
      engine_instance="ENGINE-TEST",
      metric="COALESCED_REPLACEMENTS_TOTAL",
    )._value.get()
    == 3
  )
  assert (
    T_TRADE_V3_PROJECTION_VALUE.labels(
      engine_instance="ENGINE-TEST",
      metric="PENDING_NOTICE_COUNT",
    )._value.get()
    == 1
  )


def test_missing_engine_heartbeat_clears_stale_t_trade_metrics() -> None:
  _set_t_trade_v3_engine_metrics(None)
  assert list(T_TRADE_V3_ACTIVE_STREAMS.collect()[0].samples) == []
  assert list(T_TRADE_V3_ACCUMULATOR_STATE.collect()[0].samples) == []
  assert list(T_TRADE_V3_RUNTIME_VALUE.collect()[0].samples) == []
  assert list(T_TRADE_V3_PROJECTION_VALUE.collect()[0].samples) == []


def test_oversized_runtime_snapshot_is_rejected_instead_of_truncated() -> None:
  series = [
    {
      "metric": "inputs_total",
      "path": "PULLBACK_REBOUND",
      "health": "READY",
      "detail": f"DETAIL_{index}",
      "value": 1,
    }
    for index in range(1_025)
  ]
  heartbeat = SimpleNamespace(
    instance_id="engine-oversized",
    details={
      "tTradeV3": {
        "schemaVersion": 2,
        "activeStreamCount": 1,
        "streamCapacity": 4096,
        "streamEvictionsTotal": 0,
        "seriesCount": len(series),
        "seriesCapacity": 1024,
        "seriesOverflowUpdatesTotal": 0,
        "series": series,
      }
    },
  )

  _set_t_trade_v3_engine_metrics(heartbeat)

  assert list(T_TRADE_V3_RUNTIME_VALUE.collect()[0].samples) == []
  assert list(T_TRADE_V3_ACTIVE_STREAMS.collect()[0].samples) == []
  assert (
    T_TRADE_V3_ACCUMULATOR_STATE.labels(
      engine_instance="ENGINE-OVERSIZED",
      measure="SNAPSHOT_REJECTED",
    )._value.get()
    == 1
  )


def test_projection_snapshot_with_unknown_counter_is_rejected_as_a_whole() -> None:
  heartbeat = SimpleNamespace(
    instance_id="engine-projection-invalid",
    details={
      "tTradeV3": {
        "schemaVersion": 2,
        "activeStreamCount": 1,
        "streamCapacity": 4096,
        "streamEvictionsTotal": 0,
        "seriesCount": 1,
        "seriesCapacity": 1024,
        "seriesOverflowUpdatesTotal": 0,
        "series": [
          {
            "metric": "inputs_total",
            "path": "PULLBACK_REBOUND",
            "health": "READY",
            "detail": "TOTAL",
            "value": 2,
          }
        ],
      },
      "tTradeProjection": {
        "schemaVersion": 1,
        "counters": {
          "received_total": 2,
          "unbounded_dynamic_counter": 1,
        },
        "pendingNoticeCount": 1,
        "activeNoticeTaskCount": 0,
      }
    },
  )

  _set_t_trade_v3_engine_metrics(heartbeat)

  assert list(T_TRADE_V3_RUNTIME_VALUE.collect()[0].samples) == []
  assert list(T_TRADE_V3_ACTIVE_STREAMS.collect()[0].samples) == []
  assert list(T_TRADE_V3_PROJECTION_VALUE.collect()[0].samples) == []
  assert (
    T_TRADE_V3_ACCUMULATOR_STATE.labels(
      engine_instance="ENGINE-PROJECTION-INVALID",
      measure="SNAPSHOT_REJECTED",
    )._value.get()
    == 1
  )


def test_missing_projection_rejects_valid_runtime_as_a_whole() -> None:
  heartbeat = SimpleNamespace(
    instance_id="engine-projection-missing",
    details={
      "tTradeV3": {
        "schemaVersion": 2,
        "activeStreamCount": 1,
        "streamCapacity": 4096,
        "streamEvictionsTotal": 0,
        "seriesCount": 0,
        "seriesCapacity": 1024,
        "seriesOverflowUpdatesTotal": 0,
        "series": [],
      }
    },
  )

  _set_t_trade_v3_engine_metrics(heartbeat)

  assert list(T_TRADE_V3_RUNTIME_VALUE.collect()[0].samples) == []
  assert list(T_TRADE_V3_ACTIVE_STREAMS.collect()[0].samples) == []
  assert list(T_TRADE_V3_PROJECTION_VALUE.collect()[0].samples) == []
  assert (
    T_TRADE_V3_ACCUMULATOR_STATE.labels(
      engine_instance="ENGINE-PROJECTION-MISSING",
      measure="SNAPSHOT_REJECTED",
    )._value.get()
    == 1
  )
