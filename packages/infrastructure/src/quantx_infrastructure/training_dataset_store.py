"""Control-side validation of immutable certified dataset files."""

import hashlib
import json
import math
import os
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping

RESEARCH_DATASETS_ENV = "QUANTX_RESEARCH_DATASETS_ROOT"
_HEX_RE = re.compile(r"^[a-f0-9A-F]{64}$")
_SAFE_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,511}$")


def certification_values(
  *,
  dataset_version: str,
  manifest_sha256: str,
  root: Path | None = None,
) -> dict[str, Any]:
  """Project only a complete verified directory for supervisor registration."""
  if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", dataset_version):
    raise ValueError("certification dataset identity is invalid")
  base = root or research_datasets_root()
  path = base / dataset_version / "manifest.json"
  _reject_symlink_components(base, path)
  if path.stat().st_size > 8 * 1024 * 1024:
    raise ValueError("certification manifest is too large")
  manifest = _json_read(path)
  if (
    manifest.get("dataset_version") != dataset_version
    or manifest.get("manifest_sha256") != manifest_sha256
  ):
    raise ValueError("certification result identity differs from generated files")
  values = {
    key: manifest[key]
    for key in (
      "dataset_version",
      "status",
      "source_kind",
      "source_reference",
      "date_start",
      "date_end",
      "universe_spec",
      "indicator_version",
      "factor_set_version",
      "factor_set_hash",
      "label_version",
      "manifest_sha256",
    )
  }
  quality = manifest["quality"]
  values.update(
    {key: quality[key] for key in ("sample_count", "stock_count", "trading_day_count")}
  )
  values["quality_summary"] = quality
  resolve_dataset_directory(values, root=base)
  return values


def _repo_root() -> Path:
  configured = os.environ.get("QUANTX_ROOT", "").strip()
  return (
    Path(configured).expanduser().absolute()
    if configured
    else Path(__file__).resolve().parents[4]
  )


def research_datasets_root() -> Path:
  configured = os.environ.get(RESEARCH_DATASETS_ENV, "").strip()
  root = (
    Path(configured).expanduser()
    if configured
    else _repo_root() / ".runtime" / "research-datasets"
  )
  return root.absolute()


def _safe_relative_key(value: Any) -> str:
  text = str(value or "").strip().replace("\\", "/")
  parts = text.split("/")
  if (
    not text
    or not _SAFE_KEY.fullmatch(text)
    or text.startswith("/")
    or ":" in text
    or any(part in {"", ".", ".."} for part in parts)
  ):
    raise ValueError("dataset source_reference is not a safe relative key")
  return text


def _within(root: Path, candidate: Path) -> bool:
  try:
    candidate.resolve().relative_to(root.resolve())
    return True
  except ValueError:
    return False


def _reject_symlink_components(root: Path, candidate: Path) -> None:
  if _is_link_or_junction(root):
    raise ValueError("dataset source contains a symlink or junction")
  current = root
  try:
    relative = candidate.relative_to(root)
  except ValueError as exc:
    raise ValueError("dataset path escapes research root") from exc
  for part in relative.parts:
    current = current / part
    if _is_link_or_junction(current):
      raise ValueError("dataset source contains a symlink or junction")


def _is_link_or_junction(path: Path) -> bool:
  """Reject Windows reparse-point junctions as well as ordinary links."""

  if path.is_symlink() or os.path.islink(str(path)):
    return True
  is_junction = getattr(path, "is_junction", None)
  if callable(is_junction):
    try:
      return bool(is_junction())
    except OSError as exc:
      raise ValueError("path link type cannot be verified") from exc
  return False


def _sha256_file(path: Path) -> str:
  digest = hashlib.sha256()
  with path.open("rb") as stream:
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
      digest.update(chunk)
  return digest.hexdigest()


def _json_read(path: Path) -> dict[str, Any]:
  try:
    value = json.loads(
      path.read_text(encoding="utf-8"), parse_constant=_reject_json_constant
    )
  except (OSError, UnicodeError, ValueError) as exc:
    raise ValueError(f"invalid dataset or training JSON: {path.name}") from exc
  if not isinstance(value, dict):
    raise ValueError(f"JSON object required: {path.name}")
  return value


def _reject_json_constant(value: str) -> None:
  raise ValueError(f"invalid JSON constant: {value}")


def _manifest_evidence_sha256(manifest: Mapping[str, Any]) -> str:
  evidence = dict(manifest)
  evidence.pop("manifest_sha256", None)
  payload = json.dumps(
    _to_json(evidence),
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
    allow_nan=False,
  )
  return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def resolve_dataset_directory(
  dataset: Any, *, root: Path | None = None
) -> dict[str, Any]:
  """Resolve and verify a certified dataset without trusting DB paths."""

  research_root = (root or research_datasets_root()).absolute()
  dataset_status = _value(dataset, "status")
  if dataset_status is None or str(dataset_status).upper() != "CERTIFIED":
    raise ValueError("training dataset is not CERTIFIED")
  dataset_source_kind = _value(dataset, "source_kind")
  if dataset_source_kind is None or dataset_source_kind != "VERIFIED_PANEL":
    raise ValueError("training dataset source_kind evidence is invalid")
  reference = _safe_relative_key(
    dataset.get("source_reference")
    if isinstance(dataset, Mapping)
    else getattr(dataset, "source_reference", "")
  )
  candidate = research_root / Path(reference)
  if not _within(research_root, candidate):
    raise ValueError("dataset source_reference escapes research root")
  _reject_symlink_components(research_root, candidate)
  if not candidate.is_dir():
    raise ValueError("certified dataset directory is missing")
  manifest_path = candidate / "manifest.json"
  if not manifest_path.is_file():
    raise ValueError("certified dataset manifest is missing")
  _reject_symlink_components(research_root, manifest_path)
  manifest = _json_read(manifest_path)
  dataset_version = str(
    dataset.get("dataset_version")
    if isinstance(dataset, Mapping)
    else getattr(dataset, "dataset_version", "") or ""
  ).strip()
  if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", dataset_version):
    raise ValueError("certified dataset_version is not safe")
  dataset_reference = (
    str(_value(dataset, "source_reference", "") or "").strip().replace("\\", "/")
  )
  if dataset_reference != str(manifest.get("source_reference") or ""):
    raise ValueError("certified dataset source_reference does not match its manifest")
  expected = str(
    dataset.get("manifest_sha256")
    if isinstance(dataset, Mapping)
    else getattr(dataset, "manifest_sha256", "") or ""
  ).lower()
  declared = str(manifest.get("manifest_sha256") or "").lower()
  if not _HEX_RE.fullmatch(expected) or declared != expected:
    raise ValueError("certified dataset manifest SHA-256 mismatch")
  if _manifest_evidence_sha256(manifest) != expected:
    raise ValueError("certified dataset manifest evidence hash mismatch")
  if (
    manifest.get("status") != "CERTIFIED"
    or str(manifest.get("dataset_version") or "") != dataset_version
    or manifest.get("source_kind") != "VERIFIED_PANEL"
    or manifest.get("source_reference") != dataset_version
  ):
    raise ValueError("certified dataset manifest identity is invalid")
  panel_name = str(manifest.get("panel_path") or "")
  if panel_name != "training-panel.parquet":
    raise ValueError("certified dataset panel path is invalid")
  panel_path = candidate / panel_name
  if not _within(candidate, panel_path):
    raise ValueError("certified dataset panel escapes its directory")
  _reject_symlink_components(research_root, panel_path)
  if not panel_path.is_file():
    raise ValueError("certified dataset panel is missing")
  panel_hash = _sha256_file(panel_path)
  files = manifest.get("files")
  if not isinstance(files, Mapping):
    raise ValueError("certified dataset file evidence is missing")
  file_evidence = files.get(panel_name)
  if not isinstance(file_evidence, Mapping):
    raise ValueError("certified dataset panel evidence is missing")
  panel_bytes = panel_path.stat().st_size
  try:
    declared_panel_bytes = int(manifest.get("training_panel_bytes", -1))
    file_panel_bytes = int(file_evidence.get("bytes", -1))
  except (TypeError, ValueError, OverflowError) as exc:
    raise ValueError("certified dataset panel byte evidence is invalid") from exc
  declared_panel_hash = str(manifest.get("training_panel_sha256") or "").lower()
  file_panel_hash = str(file_evidence.get("sha256") or "").lower()
  if (
    not _HEX_RE.fullmatch(declared_panel_hash)
    or not _HEX_RE.fullmatch(file_panel_hash)
    or panel_hash != declared_panel_hash
    or panel_hash != file_panel_hash
    or panel_bytes != declared_panel_bytes
    or panel_bytes != file_panel_bytes
  ):
    raise ValueError("certified dataset panel evidence mismatch")
  quality_path = candidate / "data-quality.json"
  if not quality_path.is_file():
    raise ValueError("certified dataset quality evidence is missing")
  _reject_symlink_components(research_root, quality_path)
  quality = _json_read(quality_path)
  manifest_quality = manifest.get("quality")
  if not isinstance(manifest_quality, Mapping):
    raise ValueError("certified dataset quality evidence is missing")
  if quality.get("sample_count") != manifest_quality.get("sample_count"):
    raise ValueError("certified dataset quality evidence mismatch")
  quality_path_name = manifest.get("quality_path")
  if quality_path_name != quality_path.name:
    raise ValueError("certified dataset quality path is invalid")
  quality_file_evidence = files.get(quality_path.name)
  if not isinstance(quality_file_evidence, Mapping):
    raise ValueError("certified dataset quality evidence is missing")
  quality_bytes = quality_path.stat().st_size
  quality_hash = _sha256_file(quality_path)
  try:
    declared_quality_bytes = int(manifest.get("quality_bytes", -1))
    file_quality_bytes = int(quality_file_evidence.get("bytes", -1))
  except (TypeError, ValueError, OverflowError) as exc:
    raise ValueError("certified dataset quality byte evidence is invalid") from exc
  declared_quality_hash = str(manifest.get("quality_sha256") or "").lower()
  file_quality_hash = str(quality_file_evidence.get("sha256") or "").lower()
  if (
    not _HEX_RE.fullmatch(declared_quality_hash)
    or not _HEX_RE.fullmatch(file_quality_hash)
    or quality_hash != declared_quality_hash
    or quality_hash != file_quality_hash
    or quality_bytes != declared_quality_bytes
    or quality_bytes != file_quality_bytes
  ):
    raise ValueError("certified dataset quality evidence mismatch")
  if _to_json(quality) != _to_json(manifest_quality):
    raise ValueError("certified dataset quality evidence mismatch")
  # Every immutable DB projection must be present in the manifest.  Missing
  # evidence is a hard failure rather than an invitation to trust one side.
  for field, manifest_field in (
    ("date_start", "date_start"),
    ("date_end", "date_end"),
    ("universe_spec", "universe_spec"),
    ("indicator_version", "indicator_version"),
    ("factor_set_version", "factor_set_version"),
    ("factor_set_hash", "factor_set_hash"),
    ("label_version", "label_version"),
    ("sample_count", "quality"),
    ("stock_count", "quality"),
    ("trading_day_count", "quality"),
    ("quality_summary", "quality"),
  ):
    db_value = _value(dataset, field)
    if db_value is None:
      raise ValueError(f"certified dataset {field} evidence is missing")
    if manifest_field not in manifest:
      raise ValueError(f"certified dataset {manifest_field} evidence is missing")
    manifest_value = manifest[manifest_field]
    if manifest_value is None:
      raise ValueError(f"certified dataset {manifest_field} evidence is missing")
    if field in {"sample_count", "stock_count", "trading_day_count"}:
      if not isinstance(manifest_value, Mapping) or field not in manifest_value:
        raise ValueError(f"certified dataset {field} evidence is missing")
      manifest_value = manifest_value[field]
    if field in {"date_start", "date_end"}:
      db_value = str(db_value)[:10]
      manifest_value = str(manifest_value)[:10]
    elif field in {"universe_spec", "quality_summary"}:
      db_value = _to_json(db_value)
      manifest_value = _to_json(manifest_value)
    if db_value != manifest_value:
      raise ValueError(f"certified dataset {field} evidence mismatch")
  return {
    "directory": candidate,
    "manifest_path": manifest_path,
    "manifest": manifest,
    "panel_path": panel_path,
    "manifest_sha256": declared,
    "manifest_file_sha256": _sha256_file(manifest_path),
  }


def _to_json(value: Any) -> Any:
  if isinstance(value, datetime):
    return value.isoformat()
  if isinstance(value, date):
    return value.isoformat()
  if isinstance(value, Mapping):
    return {str(key): _to_json(item) for key, item in value.items()}
  if isinstance(value, (list, tuple)):
    return [_to_json(item) for item in value]
  if isinstance(value, float) and not math.isfinite(value):
    raise ValueError("JSON cannot contain NaN or Infinity")
  return value


def _value(item: Any, name: str, default: Any = None) -> Any:
  if isinstance(item, Mapping):
    return item.get(name, default)
  return getattr(item, name, default)
