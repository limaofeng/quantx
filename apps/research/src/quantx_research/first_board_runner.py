"""Reproducible CLI runner for first-board policy research archives."""

from __future__ import annotations

from dataclasses import fields
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Any

import pandas as pd
import yaml
from pydantic import BaseModel, ConfigDict, Field
from quantx_domain.trading.first_board_policy import (
  FirstBoardEntryPolicy,
  FirstBoardExitPolicy,
)

from quantx_research.artifacts import (
  artifact_index,
  create_run_directory,
  directory_fingerprint,
  file_sha256,
  fingerprint,
  git_state,
  runtime_metadata,
  write_json,
  write_yaml,
)
from quantx_research.studies.first_board_promotion import (
  FirstBoardPromotionStudy,
  FirstBoardResearchConfig,
)
from quantx_research.studies.first_board_replay import FirstBoardReplayConfig

REPO_ROOT = Path(__file__).resolve().parents[4]


class FirstBoardOfflineConfig(BaseModel):
  model_config = ConfigDict(extra="forbid", frozen=True)

  study: str = "first-board-promotion"
  version: str = "v2"
  feature_snapshots: str
  tick_archive: str
  output_root: str = ".runtime/research-runs"
  minimum_replay_coverage_ratio: float = Field(default=0.8, gt=0, le=1)
  entry_volume: int = Field(default=100, ge=1)
  entry_order_ttl_ms: int = Field(default=15_000, ge=1_000)
  book_depth_participation_pct: float = Field(default=0.25, gt=0, le=1)
  entry_policy: dict[str, Any] = Field(default_factory=dict)
  exit_policy: dict[str, Any] = Field(default_factory=dict)
  model: dict[str, Any] = Field(default_factory=dict)


def load_first_board_config(path: str | Path) -> tuple[FirstBoardOfflineConfig, Path]:
  config_path = Path(path).resolve()
  payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
  if not isinstance(payload, dict):
    raise ValueError("研究配置根节点必须是 YAML mapping")
  config = FirstBoardOfflineConfig.model_validate(payload)
  if config.study != "first-board-promotion":
    raise ValueError(f"unsupported first-board study: {config.study}")
  return config, config_path


async def validate_first_board_study(config_path: str | Path) -> dict[str, Any]:
  config, path = load_first_board_config(config_path)
  _study(config)
  snapshots, ticks, sources = _load_inputs(config, path)
  # Validation deliberately runs the deterministic replay: row counts alone
  # cannot prove that orders and exits have executable depth.
  from quantx_research.studies.first_board_replay import FirstBoardPolicyReplay

  replay_result = FirstBoardPolicyReplay(_replay_config(config)).run(snapshots, ticks)
  errors = _preflight_errors(config, replay_result.quality.to_dict())
  return {
    "valid": not errors,
    "study_id": config.study,
    "version": config.version,
    "candidate_snapshot_count": len(snapshots),
    "tick_count": len(ticks),
    "data_quality": replay_result.quality.to_dict(),
    "sources": sources,
    "errors": errors,
  }


async def run_first_board_study(
  config_path: str | Path,
  *,
  output_root: str | Path | None = None,
  now: datetime | None = None,
) -> Path:
  config, path = load_first_board_config(config_path)
  resolved = config.model_dump(mode="json")
  config_hash = fingerprint(resolved)
  root = _resolve_path(path, str(output_root or config.output_root))
  run_dir = create_run_directory(
    root, config.study, config.version, config_hash, now=now
  )
  started = datetime.now(timezone.utc)
  snapshots, ticks, sources = _load_inputs(config, path)
  shared_policy_path = (
    REPO_ROOT / "packages/domain/src/quantx_domain/trading/first_board_policy.py"
  )
  promotion_path = (
    REPO_ROOT / "packages/domain/src/quantx_domain/trading/first_board_promotion.py"
  )
  manifest = {
    "run_id": run_dir.name,
    "study_id": config.study,
    "version": config.version,
    "status": "running",
    "started_at": started,
    "config_hash": config_hash,
    "code_fingerprint": directory_fingerprint(REPO_ROOT / "apps/research"),
    "shared_policy_fingerprint": fingerprint(
      {
        "first_board_policy": file_sha256(shared_policy_path),
        "first_board_promotion": file_sha256(promotion_path),
      }
    ),
    "lockfile_sha256": file_sha256(REPO_ROOT / "uv.lock"),
    "sources": sources,
    "git": git_state(REPO_ROOT),
    "runtime": runtime_metadata(),
  }
  write_yaml(run_dir / "resolved-config.yaml", resolved)
  write_json(run_dir / "manifest.json", manifest)
  try:
    result = _study(config).run(snapshots, ticks=ticks)
    quality = result.replay_quality or {}
    errors = _preflight_errors(config, quality)
    write_json(run_dir / "data-quality.json", quality)
    if errors:
      from quantx_research.runner import ResearchPreflightError

      failed = {
        **manifest,
        "status": "failed_preflight",
        "completed_at": datetime.now(timezone.utc),
        "errors": errors,
        "artifacts": artifact_index(run_dir),
      }
      write_json(run_dir / "manifest.json", failed)
      raise ResearchPreflightError("；".join(errors), run_dir=run_dir)
    trade_details = (
      result.replay_trades if result.replay_trades is not None else result.predictions
    )
    trade_details.to_parquet(run_dir / "trade-details.parquet", index=False)
    if result.replay_decisions is not None:
      result.replay_decisions.to_parquet(
        run_dir / "signal-decisions.parquet", index=False
      )
    result.predictions.to_parquet(run_dir / "model-predictions.parquet", index=False)
    metrics = result.release_evidence()
    write_json(run_dir / "metrics.json", metrics)
    completed = datetime.now(timezone.utc)
    manifest = {
      **manifest,
      "status": "completed",
      "completed_at": completed,
      "elapsed_seconds": (completed - started).total_seconds(),
      "data_fingerprint": fingerprint(sources),
      "candidate_snapshot_count": quality.get("candidate_snapshot_count", 0),
      "completed_trade_count": quality.get("completed_trade_count", 0),
      "model_version": metrics.get("model_version"),
      "exit_policy_version": metrics.get("exit_policy_version"),
    }
    _render_first_board_report(run_dir, metrics, quality, manifest)
    manifest["artifacts"] = artifact_index(run_dir)
    write_json(run_dir / "manifest.json", manifest)
    return run_dir
  except Exception as exc:
    from quantx_research.runner import ResearchPreflightError

    if isinstance(exc, ResearchPreflightError):
      raise
    failed = {
      **manifest,
      "status": "failed",
      "completed_at": datetime.now(timezone.utc),
      "errors": [f"{type(exc).__name__}: {exc}"],
      "artifacts": artifact_index(run_dir),
    }
    write_json(run_dir / "manifest.json", failed)
    raise


def render_first_board_existing(run_dir: str | Path) -> Path:
  directory = Path(run_dir)
  import json

  metrics = json.loads((directory / "metrics.json").read_text(encoding="utf-8"))
  quality = json.loads((directory / "data-quality.json").read_text(encoding="utf-8"))
  manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
  return _render_first_board_report(directory, metrics, quality, manifest)


def _study(config: FirstBoardOfflineConfig) -> FirstBoardPromotionStudy:
  allowed = {item.name for item in fields(FirstBoardResearchConfig)}
  unknown = sorted(set(config.model) - allowed)
  if unknown:
    raise ValueError(f"unknown first-board model settings: {unknown}")
  return FirstBoardPromotionStudy(
    FirstBoardResearchConfig(**config.model),
    replay_config=_replay_config(config),
  )


def _replay_config(config: FirstBoardOfflineConfig) -> FirstBoardReplayConfig:
  return FirstBoardReplayConfig(
    entry_volume=config.entry_volume,
    entry_order_ttl_ms=config.entry_order_ttl_ms,
    book_depth_participation_pct=config.book_depth_participation_pct,
    entry_policy=FirstBoardEntryPolicy.from_parameters(config.entry_policy),
    exit_policy=FirstBoardExitPolicy.from_parameters(config.exit_policy),
  )


def _load_inputs(
  config: FirstBoardOfflineConfig, config_path: Path
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
  snapshot_path = _resolve_path(config_path, config.feature_snapshots)
  tick_path = _resolve_path(config_path, config.tick_archive)
  snapshots = _read_frame(snapshot_path)
  ticks = _read_frame(tick_path)
  return (
    snapshots,
    ticks,
    {
      "feature_snapshots": {
        "path": snapshot_path.as_posix(),
        "sha256": file_sha256(snapshot_path),
        "rows": len(snapshots),
      },
      "tick_archive": {
        "path": tick_path.as_posix(),
        "sha256": file_sha256(tick_path),
        "rows": len(ticks),
      },
    },
  )


def _read_frame(path: Path) -> pd.DataFrame:
  if not path.is_file():
    raise ValueError(f"research archive does not exist: {path}")
  if path.suffix.lower() in {".parquet", ".pq"}:
    return pd.read_parquet(path)
  if path.suffix.lower() == ".csv":
    return pd.read_csv(path)
  raise ValueError(f"unsupported research archive format: {path.suffix}")


def _resolve_path(config_path: Path, value: str) -> Path:
  path = Path(value)
  return path.resolve() if path.is_absolute() else (config_path.parent / path).resolve()


def _preflight_errors(
  config: FirstBoardOfflineConfig, quality: dict[str, Any]
) -> list[str]:
  errors = []
  if int(quality.get("market_signal_count", 0) or 0) <= 0:
    errors.append("没有产生与生产策略一致的首板市场信号")
  coverage = float(quality.get("coverage_ratio", 0.0) or 0.0)
  if coverage < config.minimum_replay_coverage_ratio:
    errors.append(
      f"完整 Tick/盘口回放覆盖率 {coverage:.2%} 低于门槛 "
      f"{config.minimum_replay_coverage_ratio:.2%}"
    )
  return errors


def _render_first_board_report(
  run_dir: Path,
  metrics: dict[str, Any],
  quality: dict[str, Any],
  manifest: dict[str, Any],
) -> Path:
  report = run_dir / "report.html"
  report.write_text(
    "<!doctype html><html lang='zh-CN'><meta charset='utf-8'>"
    "<title>首板策略一致性研究</title><body>"
    "<h1>首板策略一致性研究</h1>"
    f"<p>策略版本：{escape(str(metrics.get('model_version', '')))}</p>"
    f"<p>退出版本：{escape(str(metrics.get('exit_policy_version', '')))}</p>"
    f"<p>完整回放覆盖率：{float(quality.get('coverage_ratio', 0.0)):.2%}</p>"
    f"<p>完成交易：{int(quality.get('completed_trade_count', 0) or 0)}</p>"
    f"<p>运行状态：{escape(str(manifest.get('status', 'completed')))}</p>"
    "</body></html>\n",
    encoding="utf-8",
  )
  return report


__all__ = [
  "FirstBoardOfflineConfig",
  "load_first_board_config",
  "render_first_board_existing",
  "run_first_board_study",
  "validate_first_board_study",
]
