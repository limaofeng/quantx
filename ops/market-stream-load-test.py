"""Isolated market.v2 codec and end-to-end load/chaos test harness."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import math
import os
import re
import socket
import subprocess
import sys
import time
import uuid
from collections import defaultdict
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
import orjson
import psutil
import redis.asyncio as aioredis
import uvicorn
import websockets
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from quantx_api.agent_api import (
  MARKET_STREAM_REDIS_CLEANUP_TIMEOUT_SECONDS,
  _MarketCommitState,
  _request_market_resync,
  _run_market_commit_pipeline,
)
from quantx_contracts import (
  MARKET_STREAM_MARKETS,
  MARKET_STREAM_SUBPROTOCOL,
  MarketBatchKind,
  MarketControlType,
  MarketStreamBatch,
  MarketStreamControl,
)
from quantx_infrastructure.config.settings import settings
from quantx_infrastructure.core.data.market_stream_transport import (
  PRODUCTION_MARKET_STREAM_KEYSPACE,
  MarketStreamKeyspace,
  MarketStreamState,
  MarketStreamStore,
)

ROOT = Path(__file__).resolve().parents[1]
REPORT_ROOT = ROOT / ".runtime" / "reports" / "market-stream-load-test"
LOAD_RUNTIME_ROOT = ROOT / ".runtime" / "load-tests"
INSTRUMENTS = 5_822
INITIAL_AVAILABLE = 5_781
PRODUCTION_HEALTH_URL = "http://127.0.0.1:8080/health/components"
PRODUCTION_LIVE_URL = "http://127.0.0.1:8080/health/live"
logger = logging.getLogger("quantx.market_load_test")


class SafetyPreflightError(RuntimeError):
  pass


def percentile(values: list[float], quantile: float) -> float:
  if not values:
    return 0.0
  ordered = sorted(values)
  index = min(len(ordered) - 1, math.ceil((len(ordered) - 1) * quantile))
  return ordered[index]


def latency_summary(values: list[float]) -> dict[str, float]:
  return {
    "count": len(values),
    "meanMs": round(mean(values), 3) if values else 0.0,
    "p50Ms": round(percentile(values, 0.50), 3),
    "p95Ms": round(percentile(values, 0.95), 3),
    "p99Ms": round(percentile(values, 0.99), 3),
    "maxMs": round(max(values), 3) if values else 0.0,
  }


def parse_duration(value: str) -> float:
  raw = value.strip().lower()
  multiplier = 1.0
  if raw.endswith("ms"):
    multiplier = 0.001
    raw = raw[:-2]
  elif raw.endswith("s"):
    raw = raw[:-1]
  elif raw.endswith("m"):
    multiplier = 60.0
    raw = raw[:-1]
  elif raw.endswith("h"):
    multiplier = 3600.0
    raw = raw[:-1]
  try:
    seconds = float(raw) * multiplier
  except ValueError as exc:
    raise argparse.ArgumentTypeError("duration must be like 30s, 30m, or 2h") from exc
  if seconds <= 0:
    raise argparse.ArgumentTypeError("duration must be positive")
  return seconds


def make_universe(count: int = INSTRUMENTS) -> tuple[str, ...]:
  sh_count = count // 2
  codes = [f"{600_000 + index:06d}.SH" for index in range(sh_count)]
  codes.extend(f"{index:06d}.SZ" for index in range(count - sh_count))
  return tuple(sorted(codes))


def make_tick(code: str, sequence: int, timestamp_ms: int) -> dict[str, Any]:
  seed = int(code[:6]) % 10_000
  price = round(8.0 + seed / 10_000 + sequence / 100_000, 4)
  return {
    "time": timestamp_ms,
    "lastPrice": price,
    "lastClose": round(price - 0.05, 4),
    "volume": sequence * 100 + seed,
    "amount": round((sequence * 100 + seed) * price, 2),
    "bidPrice": [round(price - 0.01 * level, 4) for level in range(1, 6)],
    "askPrice": [round(price + 0.01 * level, 4) for level in range(1, 6)],
    "bidVol": [100 * level + seed % 17 for level in range(1, 6)],
    "askVol": [100 * level + seed % 19 for level in range(1, 6)],
    "upperLimit": round(price * 1.1, 4),
    "priceTick": 0.01,
  }


def ticks_digest(ticks: dict[str, dict[str, Any]]) -> str:
  digest = hashlib.sha256()
  for code in sorted(ticks):
    digest.update(code.encode("utf-8"))
    digest.update(b"\0")
    digest.update(orjson.dumps(ticks[code], option=orjson.OPT_SORT_KEYS))
    digest.update(b"\n")
  return digest.hexdigest()


def codec_benchmark(instruments: int, batches: int) -> dict[str, Any]:
  universe = make_universe(instruments)
  ticks = {
    code: make_tick(code, 1, int(time.time() * 1000)) for code in universe
  }
  encode_ms: list[float] = []
  decode_ms: list[float] = []
  frame_sizes: list[int] = []
  cpu_started = time.process_time()
  for sequence in range(1, batches + 1):
    batch = MarketStreamBatch(
      stream_id="codec-benchmark",
      sequence=sequence,
      kind=MarketBatchKind.SNAPSHOT if sequence == 1 else MarketBatchKind.DELTA,
      captured_at=datetime.now(timezone.utc),
      instrument_count=len(ticks),
      universe_codes=universe if sequence == 1 else (),
      data=ticks,
    )
    started = time.perf_counter()
    payload = batch.to_bytes()
    encode_ms.append((time.perf_counter() - started) * 1000)
    frame_sizes.append(len(payload))
    started = time.perf_counter()
    MarketStreamBatch.from_bytes(payload)
    decode_ms.append((time.perf_counter() - started) * 1000)
  return {
    "protocol": MARKET_STREAM_SUBPROTOCOL,
    "instruments": instruments,
    "batches": batches,
    "encode": latency_summary(encode_ms),
    "decode": latency_summary(decode_ms),
    "frameBytes": {"min": min(frame_sizes), "max": max(frame_sizes)},
    "cpuSeconds": round(time.process_time() - cpu_started, 3),
  }


def effective_redis_url() -> str:
  """Apply the same dev external-host routing used by ops/quantx.ps1."""
  host_override = os.environ.get("QUANTX_DEV_EXTERNAL_DEPENDENCY_HOST", "").strip()
  if not host_override:
    environment_file = ROOT / "apps" / "api" / ".env.development"
    if environment_file.exists():
      for line in environment_file.read_text(encoding="utf-8").splitlines():
        name, separator, value = line.partition("=")
        if separator and name.strip() == "QUANTX_DEV_EXTERNAL_DEPENDENCY_HOST":
          host_override = value.strip().strip("\"'")
          break
  if not host_override:
    return settings.redis_url
  if host_override.lower() == "wsl":
    output = subprocess.check_output(
      ["wsl.exe", "-e", "sh", "-lc", "ip -4 -o addr show dev eth0"],
      text=True,
      timeout=10,
    )
    match = re.search(r"\binet\s+(\d{1,3}(?:\.\d{1,3}){3})/", output)
    if not match:
      raise SafetyPreflightError("could not resolve the WSL dependency host")
    host_override = match.group(1)
  parsed = urlsplit(settings.redis_url)
  userinfo = ""
  if parsed.username:
    userinfo = parsed.username
    if parsed.password:
      userinfo += f":{parsed.password}"
    userinfo += "@"
  port = f":{parsed.port}" if parsed.port else ""
  return urlunsplit(
    (parsed.scheme, f"{userinfo}{host_override}{port}", parsed.path, parsed.query, parsed.fragment)
  )


def new_redis_client() -> aioredis.Redis:
  return aioredis.Redis.from_url(
    effective_redis_url(),
    password=settings.redis_password or None,
    decode_responses=False,
    socket_timeout=settings.redis_socket_timeout,
    socket_connect_timeout=settings.redis_socket_connect_timeout,
    max_connections=6,
  )


class ChaosMarketStreamStore(MarketStreamStore):
  @property
  def delay_key(self) -> str:
    return f"{self.keyspace.prefix}:chaos:delay-next"

  async def write_batch(self, *args: Any, **kwargs: Any) -> MarketStreamState:
    redis = await self.redis()
    delay = await redis.get(self.delay_key)
    if delay:
      await redis.delete(self.delay_key)
      await asyncio.sleep(float(delay.decode("ascii")))
    return await super().write_batch(*args, **kwargs)


async def no_op_device_validator(_: str) -> None:
  return None


def create_load_gateway(run_id: str, prefix: str) -> FastAPI:
  keyspace = MarketStreamKeyspace(prefix)
  store = ChaosMarketStreamStore(new_redis_client(), keyspace=keyspace)

  @asynccontextmanager
  async def lifespan(_: FastAPI):
    try:
      yield
    finally:
      await store.close()

  app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

  @app.get("/health/live")
  async def health_live() -> dict[str, str]:
    return {"status": "alive", "component": "market-load-gateway", "runId": run_id}

  @app.websocket("/ws/load/market")
  async def load_market(websocket: WebSocket) -> None:
    offered = set(websocket.scope.get("subprotocols") or [])
    if MARKET_STREAM_SUBPROTOCOL not in offered:
      await websocket.close(code=4406, reason="market subprotocol required")
      return
    await websocket.accept(subprotocol=MARKET_STREAM_SUBPROTOCOL)
    stream_id = str(uuid.uuid4())
    commit_state = _MarketCommitState()
    reason = "load websocket disconnected"
    try:
      generation = await store.allocate_generation()
      await store.mark_syncing(
        stream_id,
        generation=generation,
        reason=f"load test {run_id}",
      )
      await websocket.send_text(
        MarketStreamControl(
          type=MarketControlType.START,
          stream_id=stream_id,
          markets=MARKET_STREAM_MARKETS,
        ).model_dump_json()
      )
      await _run_market_commit_pipeline(
        websocket,
        stream_id=stream_id,
        device_id=f"load-{run_id}",
        session_expires_at=datetime.now(timezone.utc).replace(tzinfo=None)
        + timedelta(days=1),
        commit_state=commit_state,
        store=store,
        validate_device=no_op_device_validator,
      )
    except WebSocketDisconnect:
      pass
    except Exception as exc:
      reason = f"{exc.__class__.__name__}: {exc}"
      logger.exception(
        "Load gateway stream failed: run_id=%s stream_id=%s sequence=%s",
        run_id,
        stream_id,
        commit_state.last_sequence,
      )
      await _request_market_resync(
        websocket,
        stream_id=stream_id,
        sequence=commit_state.last_sequence,
        reason=reason,
      )
      try:
        await websocket.close(code=1011, reason="load stream resync required")
      except Exception:
        pass
    finally:
      try:
        await asyncio.wait_for(
          store.mark_offline(stream_id, reason=reason),
          timeout=MARKET_STREAM_REDIS_CLEANUP_TIMEOUT_SECONDS,
        )
      except Exception:
        pass

  return app


@dataclass
class RunMetrics:
  ack_ms: dict[str, list[float]] = field(default_factory=dict)
  frames: int = 0
  ticks: int = 0
  bytes: int = 0
  expected_resyncs: int = 0
  unexpected_resyncs: int = 0
  reconnects: int = 0
  health_ms: list[float] = field(default_factory=list)
  health_failures: int = 0
  redis_ping_ms: list[float] = field(default_factory=list)
  samples: list[dict[str, Any]] = field(default_factory=list)

  def record_ack(self, phase: str, latency_ms: float, batch: MarketStreamBatch) -> None:
    self.ack_ms.setdefault(phase, []).append(latency_ms)
    self.frames += 1
    self.ticks += batch.instrument_count
    self.bytes += len(batch.to_bytes())


@dataclass
class LoadContext:
  run_id: str
  keyspace: MarketStreamKeyspace
  redis: aioredis.Redis
  port: int
  run_dir: Path
  supervisor: subprocess.Popen[bytes] | None = None
  expected: dict[str, dict[str, Any]] = field(default_factory=dict)
  metrics: RunMetrics = field(default_factory=RunMetrics)
  stream_ids: set[str] = field(default_factory=set)

  @property
  def state_path(self) -> Path:
    return self.run_dir / "state" / f"market-load-{self.run_id}-supervisor.json"

  @property
  def url(self) -> str:
    return f"ws://127.0.0.1:{self.port}/ws/load/market"

  @property
  def health_url(self) -> str:
    return f"http://127.0.0.1:{self.port}/health/live"


def reserve_port() -> int:
  with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
    probe.bind(("127.0.0.1", 0))
    return int(probe.getsockname()[1])


async def existing_keys(
  redis: aioredis.Redis,
  keyspace: MarketStreamKeyspace,
  *,
  stream_ids: set[str] | None = None,
) -> list[str]:
  candidates = list(
    keyspace.data_keys(stream_ids=tuple(sorted(stream_ids or set())))
  )
  candidates.append(f"{keyspace.prefix}:chaos:delay-next")
  return [key for key in candidates if await redis.exists(key)]


async def cleanup_keyspace(
  redis: aioredis.Redis,
  keyspace: MarketStreamKeyspace,
  *,
  stream_ids: set[str],
) -> int:
  if keyspace == PRODUCTION_MARKET_STREAM_KEYSPACE:
    raise SafetyPreflightError("refusing to clean the production market keyspace")
  keys = await existing_keys(redis, keyspace, stream_ids=stream_ids)
  removed = 0
  for offset in range(0, len(keys), 128):
    removed += int(await redis.delete(*keys[offset : offset + 128]))
  return removed


async def production_health() -> dict[str, Any]:
  async with httpx.AsyncClient(timeout=5.0) as client:
    response = await client.get(PRODUCTION_HEALTH_URL)
    response.raise_for_status()
    return dict(response.json().get("components") or {})


async def safety_preflight(allow_shared_redis: bool) -> dict[str, Any]:
  if not allow_shared_redis:
    raise SafetyPreflightError("shared Redis requires --allow-shared-redis")
  components = await production_health()
  market = dict(components.get("marketData") or {})
  if market.get("tradingSession") is not False:
    raise SafetyPreflightError("load test is only allowed outside trading hours")
  if str(components.get("api", {}).get("status") or "").lower() != "ready":
    raise SafetyPreflightError("production API is not ready")
  return components


def supervisor_state(context: LoadContext) -> dict[str, Any]:
  try:
    return json.loads(context.state_path.read_text(encoding="utf-8"))
  except (FileNotFoundError, json.JSONDecodeError):
    return {}


async def wait_for_gateway(context: LoadContext, timeout: float = 15.0) -> float:
  started = time.monotonic()
  async with httpx.AsyncClient(timeout=1.0) as client:
    while time.monotonic() - started < timeout:
      try:
        response = await client.get(context.health_url)
        if response.status_code == 200 and response.json().get("runId") == context.run_id:
          return time.monotonic() - started
      except (httpx.HTTPError, ValueError):
        pass
      await asyncio.sleep(0.1)
  raise TimeoutError("load gateway did not become healthy")


def start_supervisor(context: LoadContext) -> None:
  state_dir = context.run_dir / "state"
  state_dir.mkdir(parents=True, exist_ok=True)
  command = [
    sys.executable,
    str(ROOT / "ops" / "supervise_process.py"),
    "--name",
    f"market-load-{context.run_id}",
    "--state-dir",
    str(state_dir),
    "--",
    sys.executable,
    str(Path(__file__).resolve()),
    "serve",
    "--run-id",
    context.run_id,
    "--prefix",
    context.keyspace.prefix,
    "--port",
    str(context.port),
  ]
  flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
  context.supervisor = subprocess.Popen(command, creationflags=flags)


def stop_supervisor(context: LoadContext) -> None:
  process = context.supervisor
  if process is None or process.poll() is not None:
    return
  process.terminate()
  try:
    process.wait(timeout=15)
  except subprocess.TimeoutExpired:
    process.kill()
    process.wait(timeout=5)


async def open_stream(context: LoadContext) -> tuple[Any, str]:
  socket_client = await websockets.connect(
    context.url,
    subprotocols=[MARKET_STREAM_SUBPROTOCOL],
    max_size=64 * 1024 * 1024,
    ping_interval=20,
    ping_timeout=30,
  )
  control = MarketStreamControl.model_validate_json(await socket_client.recv())
  if control.type is not MarketControlType.START:
    await socket_client.close()
    raise RuntimeError("load gateway did not send START")
  context.stream_ids.add(control.stream_id)
  return socket_client, control.stream_id


async def send_batch(
  context: LoadContext,
  socket_client: Any,
  phase: str,
  batch: MarketStreamBatch,
) -> MarketStreamControl:
  payload = batch.to_bytes()
  started = time.monotonic()
  await socket_client.send(payload)
  raw = await asyncio.wait_for(socket_client.recv(), timeout=16.0)
  latency_ms = (time.monotonic() - started) * 1000
  control = MarketStreamControl.model_validate_json(raw)
  if control.type is MarketControlType.RESYNC:
    raise RuntimeError(f"unexpected RESYNC: {control.reason}")
  if control.type is not MarketControlType.ACK or control.sequence != batch.sequence:
    raise RuntimeError("market ACK does not match sent batch")
  context.metrics.record_ack(phase, latency_ms, batch)
  return control


async def send_pipelined_batches(
  context: LoadContext,
  socket_client: Any,
  phase: str,
  batches: list[MarketStreamBatch],
) -> None:
  pending: list[tuple[MarketStreamBatch, float]] = []
  for batch in batches:
    await socket_client.send(batch.to_bytes())
    pending.append((batch, time.monotonic()))
  for batch, sent_at in pending:
    raw = await asyncio.wait_for(socket_client.recv(), timeout=16.0)
    control = MarketStreamControl.model_validate_json(raw)
    if control.type is MarketControlType.RESYNC:
      raise RuntimeError(f"unexpected RESYNC: {control.reason}")
    if control.type is not MarketControlType.ACK or control.sequence != batch.sequence:
      raise RuntimeError("market ACK does not match pipelined batch")
    context.metrics.record_ack(
      phase,
      (time.monotonic() - sent_at) * 1000,
      batch,
    )


async def prime_stream(
  context: LoadContext,
  socket_client: Any,
  stream_id: str,
  *,
  partial: bool,
  phase: str,
) -> int:
  universe = make_universe()
  available = universe[:INITIAL_AVAILABLE] if partial else universe
  timestamp_ms = int(time.time() * 1000)
  snapshot_data = {code: make_tick(code, 1, timestamp_ms) for code in available}
  context.expected = dict(snapshot_data)
  for sequence, kind, data in (
    (1, MarketBatchKind.SNAPSHOT, snapshot_data),
    (2, MarketBatchKind.DELTA, {}),
    (3, MarketBatchKind.DELTA, {}),
  ):
    batch = MarketStreamBatch(
      stream_id=stream_id,
      sequence=sequence,
      kind=kind,
      captured_at=datetime.now(timezone.utc),
      instrument_count=len(data),
      universe_codes=universe if sequence == 1 else (),
      data=data,
    )
    await send_batch(context, socket_client, phase, batch)
  return 3


def make_delta(
  context: LoadContext,
  stream_id: str,
  sequence: int,
  frame_index: int,
) -> MarketStreamBatch:
  universe = make_universe()
  if frame_index and frame_index % 30 == 0:
    data: dict[str, dict[str, Any]] = {}
  else:
    now_ms = int(time.time() * 1000)
    data = {}
    for index, code in enumerate(universe):
      if frame_index % 10 == 0 and index % 10 == 0 and code in context.expected:
        tick = context.expected[code]
      elif frame_index % 20 == 0 and index % 20 == 0 and code in context.expected:
        old = context.expected[code]
        tick = make_tick(code, sequence, int(old["time"]) - 1)
      else:
        tick = make_tick(code, sequence, now_ms)
        context.expected[code] = tick
      data[code] = tick
  return MarketStreamBatch(
    stream_id=stream_id,
    sequence=sequence,
    kind=MarketBatchKind.DELTA,
    captured_at=datetime.now(timezone.utc),
    instrument_count=len(data),
    data=data,
  )


async def run_phase(
  context: LoadContext,
  socket_client: Any,
  stream_id: str,
  sequence: int,
  *,
  phase: str,
  seconds: float,
  cadence: float,
  frame_index: int,
  pipeline_depth: int = 1,
) -> tuple[int, int]:
  deadline = time.monotonic() + seconds
  while time.monotonic() < deadline:
    cycle_started = time.monotonic()
    batches: list[MarketStreamBatch] = []
    for _ in range(pipeline_depth):
      sequence += 1
      frame_index += 1
      batches.append(make_delta(context, stream_id, sequence, frame_index))
    await send_pipelined_batches(context, socket_client, phase, batches)
    target_cycle = cadence * pipeline_depth
    await asyncio.sleep(max(0.0, target_cycle - (time.monotonic() - cycle_started)))
  return sequence, frame_index


async def sampler(context: LoadContext, stop: asyncio.Event) -> None:
  async with httpx.AsyncClient(timeout=2.0) as client:
    while not stop.is_set():
      sample: dict[str, Any] = {"at": datetime.now(timezone.utc).isoformat()}
      started = time.monotonic()
      try:
        await context.redis.ping()
        ping_ms = (time.monotonic() - started) * 1000
        context.metrics.redis_ping_ms.append(ping_ms)
        sample["redisPingMs"] = round(ping_ms, 3)
      except Exception as exc:
        sample["redisError"] = exc.__class__.__name__
      started = time.monotonic()
      try:
        response = await client.get(PRODUCTION_LIVE_URL)
        response.raise_for_status()
        health_ms = (time.monotonic() - started) * 1000
        context.metrics.health_ms.append(health_ms)
        sample["productionHealthMs"] = round(health_ms, 3)
      except Exception as exc:
        context.metrics.health_failures += 1
        sample["productionHealthError"] = exc.__class__.__name__
      state = supervisor_state(context)
      child_pid = int(state.get("childPid") or 0)
      if child_pid:
        try:
          child = psutil.Process(child_pid)
          process_tree = [child, *child.children(recursive=True)]
          sample["childPid"] = child_pid
          sample["processTreePids"] = [process.pid for process in process_tree]
          sample["rssBytes"] = sum(
            process.memory_info().rss for process in process_tree
          )
          sample["cpuSeconds"] = round(
            sum(
              process.cpu_times().user + process.cpu_times().system
              for process in process_tree
            ),
            3,
          )
        except (psutil.Error, OSError):
          pass
      context.metrics.samples.append(sample)
      try:
        await asyncio.wait_for(stop.wait(), timeout=1.0)
      except asyncio.TimeoutError:
        pass


async def inject_commit_timeout(context: LoadContext, socket_client: Any, stream_id: str, sequence: int) -> float:
  delay_key = f"{context.keyspace.prefix}:chaos:delay-next"
  await context.redis.set(delay_key, b"6", ex=30)
  batch = make_delta(context, stream_id, sequence + 1, 1)
  started = time.monotonic()
  await socket_client.send(batch.to_bytes())
  raw = await asyncio.wait_for(socket_client.recv(), timeout=10.0)
  control = MarketStreamControl.model_validate_json(raw)
  if control.type is not MarketControlType.RESYNC:
    raise RuntimeError("commit delay did not produce RESYNC")
  context.metrics.expected_resyncs += 1
  return time.monotonic() - started


async def kill_load_child(context: LoadContext) -> float:
  state = supervisor_state(context)
  child_pid = int(state.get("childPid") or 0)
  if child_pid <= 0:
    raise RuntimeError("load gateway child PID is unavailable")
  child = psutil.Process(child_pid)
  command = " ".join(child.cmdline())
  if context.run_id not in command or "serve" not in command:
    raise SafetyPreflightError("refusing to terminate an unverified process")
  child.terminate()
  started = time.monotonic()
  while time.monotonic() - started < 10.0:
    next_state = supervisor_state(context)
    if int(next_state.get("childPid") or 0) not in {0, child_pid}:
      await wait_for_gateway(context, timeout=10.0)
      return time.monotonic() - started
    await asyncio.sleep(0.1)
  raise TimeoutError("supervisor did not restart load gateway within 10 seconds")


def rss_assessment(samples: list[dict[str, Any]]) -> tuple[int, float]:
  segments: list[list[tuple[int, int]]] = []
  active_pid = 0
  for index, sample in enumerate(samples):
    child_pid = int(sample.get("childPid") or 0)
    if not child_pid or "rssBytes" not in sample:
      continue
    if child_pid != active_pid:
      segments.append([])
      active_pid = child_pid
    segments[-1].append((index, int(sample["rssBytes"])))
  segments = [segment for segment in segments if len(segment) >= 3]
  if not segments:
    return 0, 0.0
  longest = max(segments, key=len)
  warm = longest[min(len(longest) - 1, max(1, len(longest) // 10)) :]
  growth = max(0, warm[-1][1] - warm[0][1])
  elapsed_seconds = warm[-1][0] - warm[0][0]
  if elapsed_seconds < 300 or len(warm) < 3:
    return growth, 0.0
  x_mean = mean(point[0] for point in warm)
  y_mean = mean(point[1] for point in warm)
  denominator = sum((point[0] - x_mean) ** 2 for point in warm)
  slope_bytes_second = (
    sum((x - x_mean) * (y - y_mean) for x, y in warm) / denominator
    if denominator
    else 0.0
  )
  slope_mib_minute = max(0.0, slope_bytes_second * 60 / 1024 / 1024)
  return growth, slope_mib_minute


def resource_summary(samples: list[dict[str, Any]]) -> dict[str, Any]:
  """Summarize resources without joining separate child lifetimes."""

  lifetimes: dict[int, list[dict[str, Any]]] = defaultdict(list)
  for sample in samples:
    child_pid = int(sample.get("childPid") or 0)
    if child_pid:
      lifetimes[child_pid].append(sample)
  cpu_consumed = 0.0
  max_rss = 0
  for lifetime in lifetimes.values():
    cpu_values = [
      float(item["cpuSeconds"])
      for item in lifetime
      if "cpuSeconds" in item
    ]
    rss_values = [
      int(item["rssBytes"])
      for item in lifetime
      if "rssBytes" in item
    ]
    if cpu_values:
      cpu_consumed += max(cpu_values) - min(cpu_values)
    if rss_values:
      max_rss = max(max_rss, max(rss_values))
  return {
    "sampleCount": len(samples),
    "childLifetimes": len(lifetimes),
    "cpuConsumedSeconds": round(cpu_consumed, 3),
    "maxRssBytes": max_rss,
  }


def production_keyspace_isolated(
  state_payload: bytes | None,
  *,
  load_stream_ids: set[str],
  run_id: str,
) -> bool:
  state = MarketStreamState.from_bytes(state_payload)
  return bool(
    state is not None
    and state.stream_id not in load_stream_ids
    and not state.stream_id.startswith(run_id)
  )


async def validate_final(context: LoadContext) -> dict[str, Any]:
  store = MarketStreamStore(context.redis, keyspace=context.keyspace)
  loaded = await store.load_snapshot()
  if loaded is None:
    raise RuntimeError("load-test Redis snapshot is not READY")
  state, ticks = loaded
  return {
    "status": state.status,
    "streamId": state.stream_id,
    "sequence": state.sequence,
    "commitPhase": state.commit_phase,
    "universeCount": state.universe_count,
    "instrumentCount": state.instrument_count,
    "actualDigest": ticks_digest(ticks),
    "expectedDigest": ticks_digest(context.expected),
  }


async def run_load_test(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
  components_before = await safety_preflight(args.allow_shared_redis)
  run_id = uuid.uuid4().hex[:12]
  keyspace = MarketStreamKeyspace(f"quantx-loadtest:{run_id}")
  client = new_redis_client()
  context = LoadContext(
    run_id=run_id,
    keyspace=keyspace,
    redis=client,
    port=reserve_port(),
    run_dir=LOAD_RUNTIME_ROOT / run_id,
  )
  report: dict[str, Any] = {
    "runId": run_id,
    "protocol": MARKET_STREAM_SUBPROTOCOL,
    "profile": args.profile,
    "durationSeconds": args.duration,
    "startedAt": datetime.now(timezone.utc).isoformat(),
    "keyspace": keyspace.prefix,
  }
  stop_sample = asyncio.Event()
  sample_task: asyncio.Task[None] | None = None
  socket_client: Any | None = None
  exit_code = 2
  try:
    if await existing_keys(client, keyspace):
      raise SafetyPreflightError("generated load-test keyspace is not empty")
    production_before = {
      "state": await client.get(PRODUCTION_MARKET_STREAM_KEYSPACE.state_key),
      "generation": await client.get(PRODUCTION_MARKET_STREAM_KEYSPACE.generation_key),
    }
    start_supervisor(context)
    startup_seconds = await wait_for_gateway(context)
    sample_task = asyncio.create_task(sampler(context, stop_sample))
    socket_client, stream_id = await open_stream(context)
    sequence = await prime_stream(context, socket_client, stream_id, partial=True, phase="warmup")
    frame_index = 0
    fractions = {"warmup": 0.10, "steady": 0.40, "burst": 1 / 6, "chaos": 1 / 6, "recovery": 1 / 6}
    warmup_seconds = args.duration * fractions["warmup"]
    steady_seconds = args.duration * fractions["steady"]
    burst_seconds = args.duration * fractions["burst"]
    chaos_budget = args.duration * fractions["chaos"]
    recovery_seconds = max(1.0, args.duration - warmup_seconds - steady_seconds - burst_seconds - chaos_budget)
    sequence, frame_index = await run_phase(context, socket_client, stream_id, sequence, phase="warmup", seconds=warmup_seconds, cadence=3.0, frame_index=frame_index)
    sequence, frame_index = await run_phase(context, socket_client, stream_id, sequence, phase="steady", seconds=steady_seconds, cadence=3.0, frame_index=frame_index)
    sequence, frame_index = await run_phase(
      context,
      socket_client,
      stream_id,
      sequence,
      phase="burst",
      seconds=burst_seconds,
      cadence=1.5,
      frame_index=frame_index,
      pipeline_depth=2,
    )
    chaos_deadline = time.monotonic() + chaos_budget
    timeout_seconds = await inject_commit_timeout(context, socket_client, stream_id, sequence)
    await socket_client.close()
    socket_client = None
    reconnect_started = time.monotonic()
    socket_client, stream_id = await open_stream(context)
    context.metrics.reconnects += 1
    sequence = await prime_stream(context, socket_client, stream_id, partial=False, phase="chaos-reconnect")
    resync_recovery_seconds = time.monotonic() - reconnect_started
    before_kill_seconds = max(0.0, (chaos_deadline - time.monotonic()) / 2)
    sequence, frame_index = await run_phase(
      context,
      socket_client,
      stream_id,
      sequence,
      phase="chaos",
      seconds=before_kill_seconds,
      cadence=3.0,
      frame_index=frame_index,
    )
    child_restart_seconds = await kill_load_child(context)
    try:
      await socket_client.recv()
    except Exception:
      pass
    socket_client = None
    reconnect_started = time.monotonic()
    socket_client, stream_id = await open_stream(context)
    context.metrics.reconnects += 1
    sequence = await prime_stream(context, socket_client, stream_id, partial=False, phase="supervisor-reconnect")
    supervisor_recovery_seconds = time.monotonic() - reconnect_started
    sequence, frame_index = await run_phase(
      context,
      socket_client,
      stream_id,
      sequence,
      phase="chaos",
      seconds=max(0.0, chaos_deadline - time.monotonic()),
      cadence=3.0,
      frame_index=frame_index,
    )
    sequence, frame_index = await run_phase(context, socket_client, stream_id, sequence, phase="recovery", seconds=recovery_seconds, cadence=3.0, frame_index=frame_index)
    final = await validate_final(context)
    growth_bytes, slope_mib_minute = rss_assessment(context.metrics.samples)
    all_delta = [value for phase, values in context.metrics.ack_ms.items() if phase != "warmup" for value in values]
    checks = {
      "noUnexpectedResync": context.metrics.unexpected_resyncs == 0,
      "deltaP99Within2s": percentile(all_delta, 0.99) <= 2_000,
      "deltaMaxUnder5s": max(all_delta, default=0.0) < 5_000,
      "snapshotUnder10s": max(context.metrics.ack_ms.get("warmup", [0.0])) < 10_000,
      "oneExpectedResync": context.metrics.expected_resyncs == 1,
      "commitTimeoutObserved": 5.0 <= timeout_seconds < 10.0,
      "childRestartWithin10s": child_restart_seconds <= 10.0,
      "streamRecoveryWithin30s": max(resync_recovery_seconds, supervisor_recovery_seconds) <= 30.0,
      "finalUniverseComplete": final["universeCount"] == INSTRUMENTS and final["instrumentCount"] == INSTRUMENTS,
      "finalDigestMatches": final["actualDigest"] == final["expectedDigest"],
      "commitIdle": final["commitPhase"] == "IDLE",
      "rssGrowthBounded": growth_bytes <= 128 * 1024 * 1024 and slope_mib_minute <= 1.0,
      "productionHealthNoFailures": context.metrics.health_failures == 0,
      "productionHealthP99Within2s": percentile(context.metrics.health_ms, 0.99) < 2_000,
    }
    production_after = {
      "state": await client.get(PRODUCTION_MARKET_STREAM_KEYSPACE.state_key),
      "generation": await client.get(PRODUCTION_MARKET_STREAM_KEYSPACE.generation_key),
    }
    checks["productionKeyspaceIsolated"] = production_keyspace_isolated(
      production_after["state"],
      load_stream_ids=context.stream_ids,
      run_id=context.run_id,
    )
    production_stream_changed = production_after != production_before
    components_after = await production_health()
    checks["productionReadyAfter"] = (
      str(components_after.get("qmtAgent", {}).get("status") or "").lower()
      == "ready"
      and str(components_after.get("marketData", {}).get("status") or "").lower()
      == "ready"
    )
    report.update({
      "finishedAt": datetime.now(timezone.utc).isoformat(),
      "startupSeconds": round(startup_seconds, 3),
      "ack": {phase: latency_summary(values) for phase, values in context.metrics.ack_ms.items()},
      "redisPing": latency_summary(context.metrics.redis_ping_ms),
      "productionHealth": latency_summary(context.metrics.health_ms),
      "healthFailures": context.metrics.health_failures,
      "frames": context.metrics.frames,
      "ticks": context.metrics.ticks,
      "bytes": context.metrics.bytes,
      "timeoutSeconds": round(timeout_seconds, 3),
      "childRestartSeconds": round(child_restart_seconds, 3),
      "resyncRecoverySeconds": round(resync_recovery_seconds, 3),
      "supervisorRecoverySeconds": round(supervisor_recovery_seconds, 3),
      "rssGrowthBytes": growth_bytes,
      "rssSlopeMiBPerMinute": round(slope_mib_minute, 3),
      "resources": resource_summary(context.metrics.samples),
      "resourceSamples": [
        {
          key: sample[key]
          for key in (
            "at",
            "childPid",
            "processTreePids",
            "rssBytes",
            "cpuSeconds",
          )
          if key in sample
        }
        for sample in context.metrics.samples
      ],
      "final": final,
      "checks": checks,
      "passed": all(checks.values()),
      "productionBefore": components_before,
      "productionAfter": components_after,
      "productionStreamChanged": production_stream_changed,
    })
    exit_code = 0 if report["passed"] else 2
  except Exception as exc:
    report.update(
      {
        "finishedAt": datetime.now(timezone.utc).isoformat(),
        "passed": False,
        "error": f"{exc.__class__.__name__}: {exc}",
      }
    )
    exit_code = 2
  finally:
    if socket_client is not None:
      try:
        await socket_client.close()
      except Exception:
        pass
    stop_sample.set()
    if sample_task is not None:
      await asyncio.gather(sample_task, return_exceptions=True)
    stop_supervisor(context)
    removed = await cleanup_keyspace(
      client,
      keyspace,
      stream_ids=context.stream_ids,
    )
    remaining = len(
      await existing_keys(client, keyspace, stream_ids=context.stream_ids)
    )
    report["cleanup"] = {"removedKeys": removed, "remainingKeys": remaining}
    report.setdefault("checks", {})["keyspaceCleaned"] = remaining == 0
    if not report["checks"]["keyspaceCleaned"]:
      exit_code = 2
      report["passed"] = False
    await client.aclose()
  return exit_code, report


def write_report(report: dict[str, Any]) -> Path:
  REPORT_ROOT.mkdir(parents=True, exist_ok=True)
  path = REPORT_ROOT / f"{report['runId']}.json"
  path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
  return path


def serve(args: argparse.Namespace) -> int:
  app = create_load_gateway(args.run_id, args.prefix)
  uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False, log_level="warning")
  return 0


def build_parser() -> argparse.ArgumentParser:
  parser = argparse.ArgumentParser(description=__doc__)
  subparsers = parser.add_subparsers(dest="command", required=True)
  codec = subparsers.add_parser("codec", help="run market.v2 codec benchmark")
  codec.add_argument("--instruments", type=int, default=INSTRUMENTS)
  codec.add_argument("--batches", type=int, default=30)
  run = subparsers.add_parser("run", help="run isolated WebSocket/Redis load test")
  run.add_argument("--profile", choices=("standard",), default="standard")
  run.add_argument("--duration", type=parse_duration, default=parse_duration("30m"))
  run.add_argument("--allow-shared-redis", action="store_true")
  server = subparsers.add_parser("serve", help=argparse.SUPPRESS)
  server.add_argument("--run-id", required=True)
  server.add_argument("--prefix", required=True)
  server.add_argument("--port", type=int, required=True)
  return parser


def main() -> int:
  args = build_parser().parse_args()
  if args.command == "codec":
    print(json.dumps(codec_benchmark(args.instruments, args.batches), ensure_ascii=False, indent=2))
    return 0
  if args.command == "serve":
    return serve(args)
  try:
    exit_code, report = asyncio.run(run_load_test(args))
  except SafetyPreflightError as exc:
    print(json.dumps({"passed": False, "error": str(exc)}, ensure_ascii=False, indent=2))
    return 3
  except KeyboardInterrupt:
    return 130
  report_path = write_report(report)
  print(json.dumps({"passed": report.get("passed", False), "report": str(report_path), "checks": report.get("checks", {})}, ensure_ascii=False, indent=2))
  return exit_code


if __name__ == "__main__":
  raise SystemExit(main())
