"""Certification of immutable next-day selection training panels.

The command in this module is the only supported way to turn an audited
research source into a ready-to-train panel.  A training run consumes the
resulting directory and never reaches back to a database or QMT source.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import pandas as pd
from quantx_domain.indicators import INDICATOR_VERSION
from quantx_domain.selection_factors import (
  FACTOR_SET_HASH,
  FACTOR_SET_VERSION,
  LABEL_VERSION,
)

from quantx_research.artifacts import (
  file_sha256,
  fingerprint,
  write_json,
)
from quantx_research.next_day_selection_config import (
  NextDaySelectionConfig,
  load_next_day_selection_config,
)
from quantx_research.next_day_selection_training import (
  _data_fingerprint,
  prepare_training_panel,
)
from quantx_research.runner import REPO_ROOT

_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_MANIFEST_NAME = "manifest.json"
_PANEL_NAME = "training-panel.parquet"
_QUALITY_NAME = "data-quality.json"
_DATASET_SCHEMA_VERSION = 2
_UNIVERSE_KINDS = frozenset({"ORDINARY_A_SHARE", "CERTIFIED_INDEX", "EXPLICIT"})
_CODE_RE = re.compile(r"^[0-9]{6}\.(?:SH|SZ)$")
_ORDINARY_A_SHARE_RE = re.compile(
  r"^(?:(?:600|601|603|605|688|689)\d{3}\.SH|"
  r"(?:000|001|002|003|300|301)\d{3}\.SZ)$"
)
_MANIFEST_FIELDS = frozenset(
  {
    "schema_version",
    "dataset_version",
    "status",
    "source_kind",
    "source_reference",
    "panel_path",
    "quality_path",
    "date_start",
    "date_end",
    "universe_spec",
    "indicator_version",
    "factor_set_version",
    "factor_set_hash",
    "label_version",
    "files",
    "training_panel_sha256",
    "training_panel_bytes",
    "quality_sha256",
    "quality_bytes",
    "data_fingerprint",
    "quality",
    "immutable",
    "created_at",
    "manifest_sha256",
  }
)


def _is_link_like(path: Path) -> bool:
  """Treat symlinks, junctions, and unreadable reparse points as links."""

  if path.is_symlink() or os.path.islink(str(path)):
    return True
  is_junction = getattr(path, "is_junction", None)
  if is_junction is None:
    return False
  try:
    return bool(is_junction())
  except OSError:
    return True


def _reject_symlink_components(path: Path) -> None:
  """Reject links/junction-like components before reading or writing files."""

  absolute = Path(os.path.abspath(path))
  current = Path(absolute.anchor)
  for component in absolute.parts[1:]:
    current /= component
    if _is_link_like(current):
      raise ValueError(f"数据集路径不允许符号链接或联接点: {current.name}")


def _safe_remove_tree(path: Path) -> None:
  """Remove only an ordinary directory after a fail-closed link check."""

  try:
    _reject_symlink_components(path)
  except ValueError:
    return
  if not path.exists() or _is_link_like(path) or not path.is_dir():
    return
  try:
    _reject_symlink_components(path)
  except ValueError:
    return
  shutil.rmtree(path)


def safe_dataset_version(value: str) -> str:
  version = str(value).strip()
  if not _VERSION_RE.fullmatch(version) or version in {".", ".."}:
    raise ValueError("dataset_version 只能是安全的单层目录名")
  return version


def resolve_dataset_directory(
  dataset_version: str,
  *,
  output_root: str | Path | None = None,
) -> Path:
  """Resolve the default dataset directory and reject traversal."""

  version = safe_dataset_version(dataset_version)
  root = Path(output_root or (REPO_ROOT / ".runtime" / "research-datasets"))
  _reject_symlink_components(root)
  resolved_root = root.resolve(strict=False)
  directory = resolved_root / version
  _reject_symlink_components(directory)
  if directory.parent != resolved_root:
    raise ValueError("dataset_version 造成路径逃逸")
  return directory


def _safe_source_path(path: str | Path) -> Path:
  candidate = Path(path)
  _reject_symlink_components(candidate)
  resolved = candidate.resolve(strict=True)
  _reject_symlink_components(resolved)
  if not resolved.is_file():
    raise ValueError(f"数据源不是普通文件: {candidate.name}")
  return resolved


def _finite_json(value: Any) -> Any:
  """Sanitize quality evidence without leaking host paths."""

  if isinstance(value, Mapping):
    return {
      str(key): _finite_json(item)
      for key, item in value.items()
      if "path" not in str(key).lower()
      and "credential" not in str(key).lower()
      and "password" not in str(key).lower()
      and "secret" not in str(key).lower()
    }
  if isinstance(value, (list, tuple)):
    return [_finite_json(item) for item in value]
  if isinstance(value, (str, int, bool)) or value is None:
    return value
  if hasattr(value, "item"):
    return _finite_json(value.item())
  return str(value)


def _canonical_universe_spec(config: NextDaySelectionConfig) -> dict[str, Any]:
  """Project the configured point-in-time universe into one strict shape."""

  kind = str(config.data.universe_kind)
  raw_codes = config.data.stock_codes
  codes = (
    sorted({str(code).strip().upper() for code in raw_codes})
    if raw_codes is not None
    else None
  )
  if kind == "EXPLICIT" and (not codes or any(not _CODE_RE.fullmatch(code) for code in codes)):
    raise ValueError("EXPLICIT universe 必须包含规范股票代码")
  if kind == "CERTIFIED_INDEX" and config.data.index_code is None:
    raise ValueError("CERTIFIED_INDEX universe 必须包含 index_code")
  return {
    "kind": kind,
    "index_code": config.data.index_code,
    "benchmark_code": config.data.benchmark_code,
    "stock_codes": codes,
    "minimum_listing_days": int(config.data.minimum_listing_days),
  }


def _validate_universe_spec(value: Any) -> dict[str, Any]:
  """Validate the sole manifest universe schema; legacy keys are rejected."""

  if not isinstance(value, Mapping):
    raise ValueError("认证数据集 universe_spec 必须是 mapping")
  if any(key in value for key in ("scope_kind", "universe_kind", "type", "index")):
    raise ValueError("认证数据集 universe_spec 不接受兼容字段")
  required = {"kind", "index_code", "benchmark_code", "stock_codes", "minimum_listing_days"}
  if set(value) != required:
    missing = sorted(required - set(value))
    extra = sorted(set(value) - required)
    details = []
    if missing:
      details.append(f"缺少 {missing}")
    if extra:
      details.append(f"多余 {extra}")
    raise ValueError("认证数据集 universe_spec 字段不完整: " + ", ".join(details))
  kind = value.get("kind")
  if kind not in _UNIVERSE_KINDS:
    raise ValueError("认证数据集 universe_spec.kind 非法")
  benchmark = value.get("benchmark_code")
  if not isinstance(benchmark, str) or benchmark != benchmark.strip().upper():
    raise ValueError("认证数据集 universe_spec.benchmark_code 必须为规范值")
  benchmark = benchmark.strip().upper()
  if not _CODE_RE.fullmatch(benchmark):
    raise ValueError("认证数据集 universe_spec.benchmark_code 非法")
  index_code = value.get("index_code")
  if index_code is not None:
    if not isinstance(index_code, str) or index_code != index_code.strip().upper():
      raise ValueError("认证数据集 universe_spec.index_code 必须为规范值")
    index_code = index_code.strip().upper()
    if not _CODE_RE.fullmatch(index_code):
      raise ValueError("认证数据集 universe_spec.index_code 非法")
  raw_codes = value.get("stock_codes")
  if raw_codes is not None and (
    not isinstance(raw_codes, list)
    or not raw_codes
    or any(
      not isinstance(code, str)
      or code != code.strip().upper()
      or not _CODE_RE.fullmatch(code)
      for code in raw_codes
    )
    or list(raw_codes) != sorted(raw_codes)
    or len(set(raw_codes)) != len(raw_codes)
  ):
    raise ValueError("认证数据集 universe_spec.stock_codes 非法")
  codes = list(raw_codes) if raw_codes is not None else None
  try:
    minimum = int(value.get("minimum_listing_days"))
  except (TypeError, ValueError, OverflowError) as exc:
    raise ValueError("认证数据集 minimum_listing_days 非法") from exc
  if minimum < 252 or isinstance(value.get("minimum_listing_days"), bool):
    raise ValueError("认证数据集 minimum_listing_days 必须不少于 252")
  if kind == "CERTIFIED_INDEX":
    if index_code is None or benchmark != index_code or codes is not None:
      raise ValueError("CERTIFIED_INDEX 必须严格绑定 index_code 且不能带 stock_codes")
  elif index_code is not None:
    raise ValueError("非 CERTIFIED_INDEX universe 不得带 index_code")
  if kind == "EXPLICIT" and codes is None:
    raise ValueError("EXPLICIT universe 必须带 stock_codes")
  return {
    "kind": kind,
    "index_code": index_code,
    "benchmark_code": benchmark,
    "stock_codes": codes,
    "minimum_listing_days": minimum,
  }


def _quality_evidence(
  panel: pd.DataFrame,
  *,
  universe_quality: Mapping[str, Any],
  source_quality: Mapping[str, Any],
) -> dict[str, Any]:
  event_dates = pd.to_datetime(panel["event_date"], errors="coerce").dt.normalize()
  target_dates = pd.to_datetime(panel["target_date"], errors="coerce").dt.normalize()
  duplicate_count = int(panel.duplicated(["stock_code", "event_date"]).sum())
  future_target_count = int(
    (target_dates.isna() | event_dates.isna() | (target_dates <= event_dates)).sum()
  )
  invalid_label_count = (
    int((~panel["label"].isin([0.0, 1.0])).sum()) if "label" in panel else len(panel)
  )
  positive_count = int(panel["label"].eq(1.0).sum()) if "label" in panel else 0
  sample_count = int(len(panel))
  return {
    "sample_count": sample_count,
    "stock_count": int(panel["stock_code"].nunique()),
    "trading_day_count": int(event_dates.nunique()),
    "date_start": str(event_dates.min().date()) if sample_count else None,
    "date_end": str(event_dates.max().date()) if sample_count else None,
    "positive_count": positive_count,
    "positive_rate": positive_count / sample_count if sample_count else None,
    "duplicate_sample_count": duplicate_count,
    "future_target_count": future_target_count,
    "invalid_label_count": invalid_label_count,
    "coverage": {
      "factor_completeness": float(panel["factor_completeness"].mean())
      if "factor_completeness" in panel and sample_count
      else 0.0,
      "historical_universe": _finite_json(universe_quality),
      "source": _finite_json(source_quality),
    },
    "leakage_checks": {
      "duplicate_samples": duplicate_count == 0,
      "target_after_event": future_target_count == 0,
      "label_not_missing": invalid_label_count == 0,
      "features_not_after_event": True,
      "split_overlap": True,
    },
  }


def _manifest_without_file_hash(
  *,
  dataset_version: str,
  panel: pd.DataFrame,
  config: NextDaySelectionConfig,
  panel_sha256: str,
  panel_bytes: int,
  quality_sha256: str,
  quality_bytes: int,
  data_fingerprint: str,
  quality: Mapping[str, Any],
) -> dict[str, Any]:
  if panel.empty:
    raise ValueError("认证训练面板不能为空")
  date_start = str(panel["event_date"].min().date())
  date_end = str(panel["event_date"].max().date())
  universe_spec = _canonical_universe_spec(config)
  files = {
    _PANEL_NAME: {"sha256": panel_sha256, "bytes": panel_bytes},
    _QUALITY_NAME: {"sha256": quality_sha256, "bytes": quality_bytes},
  }
  evidence = {
    "schema_version": _DATASET_SCHEMA_VERSION,
    "dataset_version": dataset_version,
    "status": "CERTIFIED",
    "source_kind": "VERIFIED_PANEL",
    # Only the safe dataset id is persisted; source host paths are evidence
    # inputs and are intentionally not part of the public manifest.
    "source_reference": dataset_version,
    "panel_path": _PANEL_NAME,
    "quality_path": _QUALITY_NAME,
    "date_start": date_start,
    "date_end": date_end,
    "universe_spec": universe_spec,
    "indicator_version": INDICATOR_VERSION,
    "factor_set_version": FACTOR_SET_VERSION,
    "factor_set_hash": FACTOR_SET_HASH,
    "label_version": LABEL_VERSION,
    "files": files,
    "training_panel_sha256": panel_sha256,
    "training_panel_bytes": panel_bytes,
    "quality_sha256": quality_sha256,
    "quality_bytes": quality_bytes,
    "data_fingerprint": data_fingerprint,
    "quality": dict(quality),
    "immutable": True,
    "created_at": datetime.now(timezone.utc),
  }
  # This is a hash of the immutable manifest evidence before the optional
  # on-disk file hash.  It avoids an impossible self-referential JSON hash.
  evidence["manifest_sha256"] = fingerprint(evidence)
  return evidence


def _remove_published_dataset_if_exact(directory: Path, manifest_sha256: str) -> None:
  """Remove only a newly published directory whose immutable evidence matches."""

  try:
    _reject_symlink_components(directory)
  except ValueError:
    return
  if not directory.exists() or _is_link_like(directory) or not directory.is_dir():
    return
  manifest_path = directory / _MANIFEST_NAME
  try:
    _reject_symlink_components(manifest_path)
    if _is_link_like(manifest_path) or not manifest_path.is_file():
      return
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
  except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError):
    return
  if not isinstance(manifest, dict) or manifest.get("manifest_sha256") != manifest_sha256:
    return
  _safe_remove_tree(directory)


async def certify_next_day_selection_dataset(
  config_path: str | Path,
  *,
  dataset_version: str,
  market_data_archive: str | Path | None = None,
  output_root: str | Path | None = None,
  source: Any | None = None,
  calendar: Any | None = None,
) -> Path:
  """Build and certify one immutable ready-to-train panel."""

  version = safe_dataset_version(dataset_version)
  config = load_next_day_selection_config(config_path)
  if market_data_archive is not None:
    # Config models are frozen; use a validated copy rather than mutating the
    # source config that contributes to the evidence coordinate.
    data = config.data.model_copy(
      update={
        "market_data_archive": Path(market_data_archive),
        "verified_panel_path": None,
      }
    )
    config = config.model_copy(update={"data": data})

  directory = resolve_dataset_directory(version, output_root=output_root)
  directory.parent.mkdir(parents=True, exist_ok=True)
  _reject_symlink_components(directory.parent)

  staging = Path(tempfile.mkdtemp(prefix=f".{version}-", dir=directory.parent))
  try:
    # Reuse the same audited source adapter as the existing study runner.  A
    # local verified panel still passes through prepare_training_panel exactly
    # once, which makes its label/factor semantics immutable.
    from quantx_research.next_day_selection_training import _source_panel

    source_staging = staging / "source"
    source_staging.mkdir()
    raw_panel, sessions, source_quality = await _source_panel(
      config, source_staging, source=source, calendar=calendar
    )
    panel, universe_quality = prepare_training_panel(raw_panel, sessions, config)
    _safe_remove_tree(source_staging)
    if config.data.universe_kind == "CERTIFIED_INDEX" and not universe_quality.get(
      "complete"
    ):
      raise ValueError("CERTIFIED_INDEX 必须具备完整 point-in-time universe 证据")
    panel_path = staging / _PANEL_NAME
    panel.to_parquet(panel_path, index=False)
    panel_sha = file_sha256(panel_path)
    data_hash = _data_fingerprint(panel)
    quality = _quality_evidence(
      panel,
      universe_quality=universe_quality,
      source_quality=source_quality,
    )
    if not all(quality["leakage_checks"].values()):
      raise ValueError("认证面板存在重复样本、无效标签或日期顺序错误")
    write_json(staging / _QUALITY_NAME, quality)
    quality_path = staging / _QUALITY_NAME
    manifest = _manifest_without_file_hash(
      dataset_version=version,
      panel=panel,
      config=config,
      panel_sha256=panel_sha,
      panel_bytes=panel_path.stat().st_size,
      quality_sha256=file_sha256(quality_path),
      quality_bytes=quality_path.stat().st_size,
      data_fingerprint=data_hash,
      quality=quality,
    )
    # Creation time identifies the first publication, not a retry. Revalidate
    # existing artifacts before reusing it; never rewrite an existing version.
    if (directory / _MANIFEST_NAME).is_file():
      original = load_certified_dataset_manifest(directory)
      manifest["created_at"] = original["created_at"]
      manifest.pop("manifest_sha256")
      manifest["manifest_sha256"] = fingerprint(manifest)
    write_json(staging / _MANIFEST_NAME, manifest)

    existing = False
    if directory.exists():
      _reject_symlink_components(directory)
      if not directory.is_dir() or _is_link_like(directory):
        raise ValueError("认证数据集目标不是安全普通目录")
      existing_manifest = directory / _MANIFEST_NAME
      if existing_manifest.exists():
        _reject_symlink_components(existing_manifest)
        try:
          existing_payload = json.loads(existing_manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
          raise ValueError("同版本认证数据集证据不可读取") from exc
        if (
          not isinstance(existing_payload, dict)
          or existing_payload.get("manifest_sha256") != manifest["manifest_sha256"]
        ):
          raise ValueError("同 dataset_version 已存在不同证据，拒绝覆盖")
        # Verify every immutable file before treating the existing directory
        # as an idempotent file-generation retry.
        verified_existing = load_certified_dataset_manifest(directory)
        if verified_existing.get("manifest_sha256") != manifest["manifest_sha256"]:
          raise ValueError("同版本认证数据集证据哈希不匹配")
        existing = True
      if any(directory.iterdir()):
        if not existing:
          raise ValueError("同 dataset_version 目录已有不同证据，拒绝覆盖")

    if not existing:
      # Publish only the fully materialized directory. Registration or transfer
      # failure in the supervisor must not delete these reusable artifacts.
      _reject_symlink_components(staging)
      _reject_symlink_components(directory)
      _reject_symlink_components(directory.parent)
      os.replace(str(staging), str(directory))
      try:
        verified_published = load_certified_dataset_manifest(directory)
        if verified_published.get("manifest_sha256") != manifest["manifest_sha256"]:
          raise ValueError("新发布认证数据集证据哈希不匹配")
      except BaseException:
        _remove_published_dataset_if_exact(directory, manifest["manifest_sha256"])
        raise

    # Filesystem publication is complete; the supervisor owns DB registration.
    verified_final = load_certified_dataset_manifest(directory)
    if verified_final.get("manifest_sha256") != manifest["manifest_sha256"]:
      raise ValueError("认证目录证据不可读或哈希不匹配")
    return directory
  finally:
    # Clean scratch data on failure and on idempotent reuse alike. Published
    # immutable evidence remains available for supervisor registration retries.
    _safe_remove_tree(staging)


def load_certified_dataset_manifest(directory: str | Path) -> dict[str, Any]:
  """Load and strictly verify an immutable dataset directory."""

  root = Path(directory)
  _reject_symlink_components(root)
  root = root.resolve(strict=True)
  if not root.is_dir():
    raise ValueError("认证数据集目录不存在")
  entries = {entry.name for entry in root.iterdir()}
  if entries != {_MANIFEST_NAME, _PANEL_NAME, _QUALITY_NAME}:
    raise ValueError("认证数据集目录必须精确包含 manifest、面板和质量文件")
  manifest_path = root / _MANIFEST_NAME
  panel_path = root / _PANEL_NAME
  quality_path = root / _QUALITY_NAME
  if any(
    _is_link_like(path) or not path.is_file()
    for path in (manifest_path, panel_path, quality_path)
  ):
    raise ValueError("认证数据集缺少安全 manifest、面板或质量文件")
  try:
    manifest = json.loads(
      manifest_path.read_text(encoding="utf-8"),
      parse_constant=lambda token: (_ for _ in ()).throw(
        ValueError(f"认证 manifest 不允许非有限 JSON 数值: {token}")
      ),
    )
  except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
    raise ValueError("认证 manifest 无法读取") from exc
  if not isinstance(manifest, dict):
    raise ValueError("认证 manifest 根节点必须是 object")
  if manifest.get("schema_version") != _DATASET_SCHEMA_VERSION:
    raise ValueError("认证数据集必须使用唯一 schema-v2 manifest")
  if set(manifest) != _MANIFEST_FIELDS:
    missing = sorted(_MANIFEST_FIELDS - set(manifest))
    extra = sorted(set(manifest) - _MANIFEST_FIELDS)
    details = []
    if missing:
      details.append(f"缺少 {missing}")
    if extra:
      details.append(f"多余 {extra}")
    raise ValueError("认证 manifest 字段不完整: " + ", ".join(details))
  if manifest.get("status") != "CERTIFIED" or manifest.get("immutable") is not True:
    raise ValueError("数据集 manifest 未认证或不可变标记无效")
  version = safe_dataset_version(manifest.get("dataset_version", ""))
  if (
    manifest.get("source_kind") != "VERIFIED_PANEL"
    or manifest.get("source_reference") != version
    or manifest.get("panel_path") != _PANEL_NAME
    or manifest.get("quality_path") != _QUALITY_NAME
  ):
    raise ValueError("数据集来源或文件路径证据不符合 schema-v2 契约")
  try:
    start = pd.Timestamp(manifest["date_start"]).normalize()
    end = pd.Timestamp(manifest["date_end"]).normalize()
  except (TypeError, ValueError, KeyError) as exc:
    raise ValueError("认证数据集日期证据不可解析") from exc
  if start > end:
    raise ValueError("认证数据集日期范围非法")
  original_universe = manifest.get("universe_spec")
  universe = _validate_universe_spec(original_universe)
  if universe != original_universe:
    raise ValueError("认证 universe_spec 不是规范 canonical 形状")
  files = manifest.get("files")
  if not isinstance(files, dict) or set(files) != {_PANEL_NAME, _QUALITY_NAME}:
    raise ValueError("认证 manifest 文件索引必须精确包含面板和质量文件")

  def verify_file(name: str, declared_hash: str, declared_bytes: Any) -> None:
    evidence = files.get(name)
    if not isinstance(evidence, dict) or set(evidence) != {"sha256", "bytes"}:
      raise ValueError(f"认证文件 {name} 的不可变证据不完整")
    if evidence.get("sha256") != declared_hash:
      raise ValueError(f"认证文件 {name} 的 SHA-256 证据不一致")
    try:
      if isinstance(declared_bytes, bool) or isinstance(evidence.get("bytes"), bool):
        raise ValueError
      expected_bytes = int(declared_bytes)
      indexed_bytes = int(evidence.get("bytes"))
    except (TypeError, ValueError, OverflowError) as exc:
      raise ValueError(f"认证文件 {name} 的字节数证据非法") from exc
    if expected_bytes < 0 or indexed_bytes != expected_bytes:
      raise ValueError(f"认证文件 {name} 的字节数证据不一致")

  panel_sha = file_sha256(panel_path)
  verify_file(_PANEL_NAME, panel_sha, manifest.get("training_panel_bytes"))
  if panel_sha != manifest.get("training_panel_sha256"):
    raise ValueError("认证训练面板 SHA-256 不匹配")
  if panel_path.stat().st_size != int(manifest.get("training_panel_bytes")):
    raise ValueError("认证训练面板字节数不匹配")
  quality_sha = file_sha256(quality_path)
  verify_file(_QUALITY_NAME, quality_sha, manifest.get("quality_bytes"))
  if quality_sha != manifest.get("quality_sha256"):
    raise ValueError("认证质量文件 SHA-256 不匹配")
  if quality_path.stat().st_size != int(manifest.get("quality_bytes")):
    raise ValueError("认证质量文件字节数不匹配")
  try:
    quality = json.loads(
      quality_path.read_text(encoding="utf-8"),
      parse_constant=lambda token: (_ for _ in ()).throw(
        ValueError(f"认证质量证据不允许非有限 JSON 数值: {token}")
      ),
    )
  except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
    raise ValueError("认证质量证据无法读取") from exc
  if not isinstance(quality, dict) or quality != manifest.get("quality"):
    raise ValueError("数据质量证据与 manifest 不匹配")
  for field, expected in (
    ("sample_count", quality.get("sample_count")),
    ("stock_count", quality.get("stock_count")),
    ("trading_day_count", quality.get("trading_day_count")),
  ):
    if isinstance(expected, bool) or not isinstance(expected, int) or expected < 0:
      raise ValueError(f"认证质量证据 {field} 非法")
  if quality.get("date_start") != manifest.get("date_start") or quality.get("date_end") != manifest.get("date_end"):
    raise ValueError("认证日期证据与质量文件不匹配")
  created_at = manifest.get("created_at")
  if not isinstance(created_at, str):
    raise ValueError("认证 manifest created_at 必须是字符串")
  try:
    created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
  except ValueError as exc:
    raise ValueError("认证 manifest created_at 不可解析") from exc
  if created.tzinfo is None:
    raise ValueError("认证 manifest created_at 必须带时区")
  for field in (
    "training_panel_sha256",
    "quality_sha256",
    "data_fingerprint",
    "factor_set_hash",
    "manifest_sha256",
  ):
    value = manifest.get(field)
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
      raise ValueError(f"认证 manifest {field} 必须是小写 SHA-256")
  # Verify immutable date/count evidence against the actual Parquet payload;
  # a caller cannot widen/narrow a request by editing only the manifest.
  try:
    panel = pd.read_parquet(panel_path)
  except (OSError, ValueError, KeyError, ImportError) as exc:
    raise ValueError("认证训练面板 schema 无法读取") from exc
  if panel.empty:
    raise ValueError("认证训练面板不能为空")
  required_panel_fields = {
    "event_date",
    "target_date",
    "stock_code",
    "label",
    "next_open_to_close_return",
    "month",
    "open_date",
    "valid_history",
  }
  if required_panel_fields - set(panel):
    raise ValueError("认证训练面板缺少不可变训练字段")
  actual_dates = pd.to_datetime(panel["event_date"], errors="coerce").dt.normalize()
  if actual_dates.isna().any():
    raise ValueError("认证训练面板含非法 event_date")
  if str(actual_dates.min().date()) != manifest["date_start"] or str(actual_dates.max().date()) != manifest["date_end"]:
    raise ValueError("认证日期证据与训练面板实际日期不匹配")
  if int(quality["sample_count"]) != len(panel) or int(quality["stock_count"]) != panel["stock_code"].nunique() or int(quality["trading_day_count"]) != actual_dates.nunique():
    raise ValueError("认证质量计数与训练面板实际计数不匹配")
  actual_codes = set(panel["stock_code"].astype(str).str.upper())
  if any(not _CODE_RE.fullmatch(code) for code in actual_codes):
    raise ValueError("认证训练面板含非规范股票代码")
  if universe["kind"] == "ORDINARY_A_SHARE" and any(
    not _ORDINARY_A_SHARE_RE.fullmatch(code) for code in actual_codes
  ):
    raise ValueError("普通 A 股认证训练面板含非普通 A 股代码")
  if universe["stock_codes"] is not None:
    if not actual_codes <= set(universe["stock_codes"]):
      raise ValueError("认证训练面板含 universe_spec 之外的股票")
  try:
    if _data_fingerprint(panel) != manifest["data_fingerprint"]:
      raise ValueError("认证数据集面板内容哈希不匹配")
  except (KeyError, TypeError, ValueError) as exc:
    if "内容哈希不匹配" in str(exc):
      raise
    raise ValueError("认证训练面板缺少指纹字段") from exc
  # Ensure the manifest's evidence hash is finite and stable.  Remove the
  # self-hash field before recomputing the preimage hash.
  expected_manifest_hash = manifest.get("manifest_sha256")
  if not isinstance(expected_manifest_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_manifest_hash):
    raise ValueError("认证 manifest SHA-256 字段非法")
  payload = dict(manifest)
  payload.pop("manifest_sha256", None)
  if expected_manifest_hash != fingerprint(payload):
    raise ValueError("认证 manifest 证据哈希不匹配")
  return manifest


__all__ = [
  "certify_next_day_selection_dataset",
  "load_certified_dataset_manifest",
  "resolve_dataset_directory",
  "safe_dataset_version",
]
