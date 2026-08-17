"""Synthetic 5,000-symbol market.v1 codec benchmark (no network or trading)."""

from __future__ import annotations

import json
import statistics
import time
import tracemalloc
from datetime import datetime, timezone

from quantx_contracts import (
  AgentEnvelope,
  AgentMessageType,
  MarketBatchKind,
  MarketStreamBatch,
)


def percentile(values: list[float], quantile: float) -> float:
  ordered = sorted(values)
  index = min(len(ordered) - 1, round((len(ordered) - 1) * quantile))
  return ordered[index]


def make_ticks(count: int) -> dict[str, dict]:
  result = {}
  for index in range(count):
    market = "SH" if index % 2 == 0 else "SZ"
    result[f"{index:06d}.{market}"] = {
      "time": 1_777_000_000_000,
      "lastPrice": 10.0 + index / 10_000,
      "lastClose": 9.9,
      "volume": index * 100,
      "amount": index * 1_000.0,
      "bidPrice": [9.99, 9.98, 9.97, 9.96, 9.95],
      "askPrice": [10.01, 10.02, 10.03, 10.04, 10.05],
      "bidVol": [100, 200, 300, 400, 500],
      "askVol": [100, 200, 300, 400, 500],
      "upperLimit": 10.89,
      "priceTick": 0.01,
    }
  return result


def main() -> None:
  ticks = make_ticks(5_000)
  encode_ms: list[float] = []
  decode_ms: list[float] = []
  frame_sizes: list[int] = []
  legacy_encode_ms: list[float] = []
  legacy_decode_ms: list[float] = []
  legacy_frame_sizes: list[int] = []
  tracemalloc.start()
  cpu_started = time.process_time()
  for sequence in range(1, 31):
    batch = MarketStreamBatch(
      stream_id="benchmark-stream",
      sequence=sequence,
      kind=(
        MarketBatchKind.SNAPSHOT
        if sequence == 1
        else MarketBatchKind.DELTA
      ),
      captured_at=datetime.now(timezone.utc),
      instrument_count=len(ticks),
      data=ticks,
    )
    started = time.perf_counter()
    payload = batch.to_bytes()
    encode_ms.append((time.perf_counter() - started) * 1_000)
    frame_sizes.append(len(payload))
    started = time.perf_counter()
    MarketStreamBatch.from_bytes(payload)
    decode_ms.append((time.perf_counter() - started) * 1_000)
    legacy = AgentEnvelope(
      message_type=AgentMessageType.MARKET_EVENT,
      payload={
        "subscription_id": "legacy-whole",
        "kind": "whole",
        "stock_code": "",
        "period": "tick",
        "data": ticks,
      },
    )
    started = time.perf_counter()
    legacy_payload = legacy.model_dump_json().encode("utf-8")
    legacy_encode_ms.append((time.perf_counter() - started) * 1_000)
    legacy_frame_sizes.append(len(legacy_payload))
    started = time.perf_counter()
    AgentEnvelope.model_validate_json(legacy_payload)
    legacy_decode_ms.append((time.perf_counter() - started) * 1_000)
  cpu_seconds = time.process_time() - cpu_started
  _, peak_bytes = tracemalloc.get_traced_memory()
  tracemalloc.stop()

  def summary(values: list[float]) -> dict[str, float]:
    return {
      "mean": round(statistics.mean(values), 3),
      "p50": round(percentile(values, 0.50), 3),
      "p95": round(percentile(values, 0.95), 3),
      "p99": round(percentile(values, 0.99), 3),
    }

  print(
    json.dumps(
      {
        "instruments": len(ticks),
        "batches": len(encode_ms),
        "market_v1": {
          "encoding": "orjson-binary",
          "encode_ms": summary(encode_ms),
          "decode_ms": summary(decode_ms),
          "frame_bytes": {
            "min": min(frame_sizes),
            "max": max(frame_sizes),
          },
        },
        "legacy_whole": {
          "encoding": "agent-envelope-json-text",
          "encode_ms": summary(legacy_encode_ms),
          "decode_ms": summary(legacy_decode_ms),
          "frame_bytes": {
            "min": min(legacy_frame_sizes),
            "max": max(legacy_frame_sizes),
          },
        },
        "p95_improvement_pct": {
          "encode": round(
            (
              percentile(legacy_encode_ms, 0.95)
              - percentile(encode_ms, 0.95)
            )
            / percentile(legacy_encode_ms, 0.95)
            * 100,
            3,
          ),
          "decode": round(
            (
              percentile(legacy_decode_ms, 0.95)
              - percentile(decode_ms, 0.95)
            )
            / percentile(legacy_decode_ms, 0.95)
            * 100,
            3,
          ),
        },
        "cpu_seconds": round(cpu_seconds, 3),
        "peak_memory_mib": round(peak_bytes / 1024 / 1024, 3),
      },
      ensure_ascii=False,
      indent=2,
    )
  )


if __name__ == "__main__":
  main()
