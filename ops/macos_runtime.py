"""macOS application lifecycle; external data services remain independently owned."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

import psutil
from runtime_config import load_environment

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".runtime" / "development"
STATE = RUNTIME / "processes.json"


def tracked(entry: dict) -> psutil.Process | None:
  try:
    process = psutil.Process(entry["pid"])
    return process if process.create_time() == entry["started"] else None
  except psutil.NoSuchProcess:
    return None


def save(entries: list[dict]) -> None:
  temporary = STATE.with_suffix(".tmp")
  temporary.write_text(json.dumps(entries), encoding="utf-8")
  temporary.replace(STATE)


def stop(entries: list[dict]) -> None:
  for entry in reversed(entries):
    process = tracked(entry)
    if process:
      # Each owned child has its own session; PID plus creation time protects reuse.
      os.killpg(process.pid, signal.SIGTERM)
      try:
        process.wait(timeout=15)
      except psutil.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
  save([])


def main() -> None:
  global STATE
  parser = argparse.ArgumentParser()
  parser.add_argument(
    "command", choices=("up", "down", "status", "logs", "doctor", "history")
  )
  parser.add_argument("--environment", choices=("dev",), default="dev")
  parser.add_argument("--profile", choices=("full", "web"), default="full")
  parser.add_argument("--mode", choices=("paper", "data-only"), default="paper")
  parser.add_argument("--component", choices=("monitor",))
  parser.add_argument("--instruments")
  parser.add_argument("--period", choices=("tick", "1m", "1d"))
  parser.add_argument("--start")
  parser.add_argument("--end")
  args = parser.parse_args()
  if sys.platform != "darwin":
    parser.error("This launcher is only for macOS development")
  if not (Path(sys.prefix) / "conda-meta").is_dir():
    parser.error("Activate the quantx Conda environment; venv is not supported")
  RUNTIME.mkdir(parents=True, exist_ok=True)
  if args.component:
    STATE = RUNTIME / "monitor.json"
  import fcntl

  if args.command in {"up", "down"}:
    lock = (RUNTIME / "lifecycle.lock").open("a")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
  entries = json.loads(STATE.read_text()) if STATE.exists() else []
  if args.command == "status":
    for entry in entries:
      print(entry["name"], "RUNNING" if tracked(entry) else "STOPPED")
    return
  if args.command == "logs":
    for path in sorted(RUNTIME.glob("*.log")):
      print(path.name)
      with path.open("rb") as log:
        log.seek(max(0, path.stat().st_size - 65536))
        print("\n".join(log.read().decode(errors="replace").splitlines()[-50:]))
    return
  if args.command == "down":
    stop(entries)
    return
  env = load_environment(ROOT, "development")
  env.update(QMT_AGENT_MODE=args.mode, RUNTIME_PROFILE=args.profile)
  env.update(
    QUANTX_CADDY_BIND="127.0.0.1",
    QUANTX_CADDY_SITE_ADDRESS="http://127.0.0.1:8080",
    VITE_APP_ENV="development",
  )
  env["PYTHONPATH"] = os.pathsep.join(
    str(path)
    for parent in ("apps", "packages")
    for path in (ROOT / parent).glob("*/src")
    if path.parent.name != "qmt-agent"
  )
  for key, default_port in (
    ("DATABASE_URL", 5432),
    ("REDIS_URL", 6379),
    ("INFLUXDB_HOST", 8181),
    ("PREFECT_API_URL", 4200),
  ):
    endpoint = urlsplit(env[key])
    with socket.create_connection(
      (endpoint.hostname, endpoint.port or default_port), timeout=3
    ):
      pass
  if args.command == "doctor":
    print("Development isolation and external service ports: OK")
    return
  if args.command == "history":
    if not all((args.instruments, args.period, args.start, args.end)):
      parser.error("history requires --instruments, --period, --start and --end")
    raise SystemExit(
      subprocess.call(
        [
          sys.executable,
          "-m",
          "quantx_worker.development_history",
          "--instruments",
          args.instruments,
          "--period",
          args.period,
          "--start",
          args.start,
          "--end",
          args.end,
        ],
        env=env,
        cwd=ROOT,
      )
    )
  if any(tracked(entry) for entry in entries):
    parser.error("Managed processes already exist; use status or down")
  node, caddy = shutil.which("node"), shutil.which("caddy")
  if not node or not caddy:
    parser.error("Install Node and Caddy before startup")
  commands = [
    (
      "market-gateway",
      [
        sys.executable,
        "-m",
        "uvicorn",
        "quantx_api.market_gateway:app",
        "--host",
        "127.0.0.1",
        "--port",
        "18082",
      ],
      ROOT,
    ),
    (
      "api",
      [
        sys.executable,
        "-m",
        "uvicorn",
        "quantx_api.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        "18081",
      ],
      ROOT,
    ),
    ("engine", [sys.executable, "-m", "quantx_engine.main"], ROOT),
    ("ai-runtime", [sys.executable, "-m", "quantx_ai_runtime.main"], ROOT),
    (
      "web",
      [
        node,
        str(ROOT / "node_modules/vite/bin/vite.js"),
        "--host",
        "127.0.0.1",
        "--port",
        "5250",
        "--strictPort",
      ],
      ROOT / "apps/web",
    ),
    (
      "docs",
      [
        node,
        str(ROOT / "node_modules/vitepress/bin/vitepress.js"),
        "dev",
        "--host",
        "127.0.0.1",
        "--port",
        "5251",
        "--strictPort",
      ],
      ROOT / "apps/docs",
    ),
    (
      "caddy",
      [
        caddy,
        "run",
        "--config",
        str(ROOT / "ops/caddy/Caddyfile.dev"),
        "--adapter",
        "caddyfile",
      ],
      ROOT,
    ),
  ]
  if args.profile == "full":
    env["PREFECT_WORKER_POOL"] = "quantx-dev-pool"
    commands.insert(3, ("worker", [sys.executable, "-m", "quantx_worker.main"], ROOT))
  if args.component == "monitor":
    env["MONITOR_DATABASE_PATH"] = str(RUNTIME / "monitor.sqlite3")
    commands = [("monitor", [sys.executable, "-m", "quantx_monitor.main"], ROOT)]
  ports = (18083,) if args.component else (18081, 18082, 5250, 5251, 8080)
  for port in ports:
    with socket.socket() as probe:
      probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
      probe.bind(("127.0.0.1", port))
  entries = []
  try:
    for name, command, directory in commands:
      with (RUNTIME / f"{name}.log").open("ab") as log:
        process = subprocess.Popen(
          command,
          cwd=directory,
          env=env,
          stdout=log,
          stderr=log,
          start_new_session=True,
        )
      entries.append(
        {
          "name": name,
          "pid": process.pid,
          "started": psutil.Process(process.pid).create_time(),
        }
      )
      save(entries)
      health = {
        "market-gateway": "http://127.0.0.1:18082/health/live",
        "api": "http://127.0.0.1:18081/health/live",
        "caddy": "http://127.0.0.1:8080/health/live",
        "monitor": "http://127.0.0.1:18083/monitor/health/ready",
      }.get(name)
      if health:
        for attempt in range(60):
          if process.poll() is not None:
            raise RuntimeError(f"{name} exited during startup")
          try:
            with urllib.request.urlopen(health, timeout=1) as response:
              if response.status == 200:
                break
          except OSError:
            pass
          time.sleep(0.5)
        else:
          raise RuntimeError(f"{name} startup health timed out")
    if any(not tracked(entry) for entry in entries):
      raise RuntimeError("A managed process exited during startup; inspect logs")
    print("Development processes started; verify readiness at http://127.0.0.1:8080")
  except BaseException:
    stop(entries)
    raise


if __name__ == "__main__":
  main()
