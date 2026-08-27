import importlib.util
import sys
from pathlib import Path

import pytest
from quantx_infrastructure.core.data.market_stream_transport import (
  PRODUCTION_MARKET_STREAM_KEYSPACE,
  MarketStreamKeyspace,
)


def load_module():
  path = Path(__file__).resolve().parents[2] / "ops" / "market-stream-load-test.py"
  spec = importlib.util.spec_from_file_location("quantx_market_stream_load_test", path)
  assert spec is not None and spec.loader is not None
  module = importlib.util.module_from_spec(spec)
  sys.modules[spec.name] = module
  spec.loader.exec_module(module)
  return module


@pytest.fixture(scope="module")
def load_test_module():
  return load_module()


def test_production_and_load_keyspaces_are_complete_and_disjoint() -> None:
  load = MarketStreamKeyspace("quantx-loadtest:run-1")

  assert PRODUCTION_MARKET_STREAM_KEYSPACE.prefix == "market-data:whole:v2"
  assert load.batch_channel == "quantx-loadtest:run-1:batches"
  assert load.latest_key == "quantx-loadtest:run-1:latest"
  assert load.state_key == "quantx-loadtest:run-1:state"
  assert load.engine_state_key == "quantx-loadtest:run-1:engine-state"
  assert load.generation_key == "quantx-loadtest:run-1:generation"
  assert load.freshness_key == "quantx-loadtest:run-1:freshness"
  assert load.staging_key("stream-1") == "quantx-loadtest:run-1:staging:stream-1"
  assert set(load.data_keys(stream_ids=("stream-1",))).isdisjoint(
    PRODUCTION_MARKET_STREAM_KEYSPACE.data_keys(stream_ids=("stream-1",))
  )


@pytest.mark.parametrize(
  ("value", "seconds"),
  (("500ms", 0.5), ("30s", 30.0), ("30m", 1800.0), ("2h", 7200.0)),
)
def test_duration_parser(load_test_module, value: str, seconds: float) -> None:
  assert load_test_module.parse_duration(value) == seconds


def test_market_v2_codec_benchmark_is_runnable(load_test_module) -> None:
  result = load_test_module.codec_benchmark(20, 3)

  assert result["protocol"] == "quantx.market.v2"
  assert result["instruments"] == 20
  assert result["batches"] == 3
  assert result["frameBytes"]["max"] > 0
  assert result["encode"]["count"] == 3
  assert result["decode"]["count"] == 3


def test_synthetic_universe_and_digest_are_deterministic(load_test_module) -> None:
  universe = load_test_module.make_universe(12)
  assert len(universe) == 12
  assert universe == tuple(sorted(universe))
  ticks = {
    code: load_test_module.make_tick(code, 4, 1_777_000_000_000)
    for code in universe
  }
  reordered = dict(reversed(tuple(ticks.items())))
  assert load_test_module.ticks_digest(ticks) == load_test_module.ticks_digest(
    reordered
  )


def test_rss_assessment_does_not_compare_different_child_lifetimes(
  load_test_module,
) -> None:
  samples = [
    {"childPid": 1, "rssBytes": 200 * 1024 * 1024},
    {"childPid": 1, "rssBytes": 201 * 1024 * 1024},
    {"childPid": 1, "rssBytes": 202 * 1024 * 1024},
    {"childPid": 2, "rssBytes": 100 * 1024 * 1024},
    {"childPid": 2, "rssBytes": 101 * 1024 * 1024},
    {"childPid": 2, "rssBytes": 102 * 1024 * 1024},
  ]

  growth, slope = load_test_module.rss_assessment(samples)

  assert growth == 1024 * 1024
  assert slope == 0.0


def test_resource_summary_accumulates_cpu_by_child_lifetime(
  load_test_module,
) -> None:
  samples = [
    {"childPid": 1, "rssBytes": 100, "cpuSeconds": 4.0},
    {"childPid": 1, "rssBytes": 120, "cpuSeconds": 5.5},
    {"childPid": 2, "rssBytes": 80, "cpuSeconds": 1.0},
    {"childPid": 2, "rssBytes": 90, "cpuSeconds": 3.25},
  ]

  assert load_test_module.resource_summary(samples) == {
    "sampleCount": 4,
    "childLifetimes": 2,
    "cpuConsumedSeconds": 3.75,
    "maxRssBytes": 120,
  }


class FakeRedis:
  def __init__(self, keys: set[str]):
    self.keys = set(keys)
    self.deleted: list[str] = []

  async def exists(self, key: str) -> int:
    return int(key in self.keys)

  async def delete(self, *keys: str) -> int:
    removed = 0
    for key in keys:
      if key in self.keys:
        self.keys.remove(key)
        removed += 1
      self.deleted.append(key)
    return removed


@pytest.mark.asyncio
async def test_cleanup_only_deletes_owned_keyspace(load_test_module) -> None:
  keyspace = MarketStreamKeyspace("quantx-loadtest:run-2")
  owned = {
    keyspace.state_key,
    keyspace.latest_key,
    keyspace.staging_key("stream-1"),
    f"{keyspace.prefix}:chaos:delay-next",
  }
  production = PRODUCTION_MARKET_STREAM_KEYSPACE.state_key
  redis = FakeRedis(owned | {production})

  removed = await load_test_module.cleanup_keyspace(
    redis,
    keyspace,
    stream_ids={"stream-1"},
  )

  assert removed == len(owned)
  assert redis.keys == {production}
  assert production not in redis.deleted


@pytest.mark.asyncio
async def test_preflight_fails_closed_during_trading_session(
  load_test_module,
  monkeypatch,
) -> None:
  async def health():
    return {
      "api": {"status": "ready"},
      "marketData": {"status": "ready", "tradingSession": True},
    }

  monkeypatch.setattr(load_test_module, "production_health", health)
  with pytest.raises(
    load_test_module.SafetyPreflightError,
    match="outside trading hours",
  ):
    await load_test_module.safety_preflight(True)


def test_production_keyspace_never_uses_a_load_stream_id(load_test_module) -> None:
  production = load_test_module.MarketStreamState(
    status="READY",
    stream_id="production-stream",
  )
  load_streams = {"load-stream-1", "load-stream-2"}

  assert load_test_module.production_keyspace_isolated(
    production.to_bytes(),
    load_stream_ids=load_streams,
    run_id="run-1",
  )
  assert not load_test_module.production_keyspace_isolated(
    load_test_module.MarketStreamState(
      status="READY",
      stream_id="load-stream-1",
    ).to_bytes(),
    load_stream_ids=load_streams,
    run_id="run-1",
  )


def test_macos_rejects_legacy_wsl_dependency_routing(
  load_test_module,
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  monkeypatch.setattr(load_test_module.os, "name", "posix")
  monkeypatch.setenv("QUANTX_DEV_EXTERNAL_DEPENDENCY_HOST", "wsl")

  with pytest.raises(
    load_test_module.SafetyPreflightError,
    match="Windows-only",
  ):
    load_test_module.effective_redis_url()
