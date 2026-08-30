"""Version-sealed replay evidence. Signals are a read model, never strategy input."""

import hashlib
import json
import os
from collections.abc import AsyncIterable, Iterator
from pathlib import Path
from typing import Any

import aiofiles

SIGNAL_EVENT_TYPES = frozenset(
  {
    "FSM_TRANSITION",
    "CANDIDATE_LATCHED",
    "CANDIDATE_AWAITING_APPROVAL",
    "CANDIDATE_SUPPRESSED",
    "CANDIDATE_REARMING",
    "CANDIDATE_CLEARED",
    "CANDIDATE_STATE_CHANGED",
    "INTENT_LINKED",
  }
)
OPPORTUNITY_ARTIFACT = "opportunity_evaluations"


class ReplayEvidenceUnavailable(ValueError):
  """A stable, safe reason code; callers must not substitute another version."""


def evaluation_record(row: Any) -> dict[str, Any]:
  """Copy persisted facts without inventing a signal or an intent."""
  fields = (
    "id",
    "event_key",
    "account_id",
    "strategy_run_id",
    "instrument_code",
    "candidate_id",
    "evaluated_at",
    "record_kind",
    "event_type",
    "window_started_at",
    "window_ended_at",
    "coalesced_count",
    "policy_version",
    "schema_version",
    "content_fingerprint",
    "payload",
    "metrics",
  )
  result = {key: getattr(row, key) for key in fields}
  for key in ("evaluated_at", "window_started_at", "window_ended_at"):
    if result[key] is not None:
      result[key] = result[key].isoformat()
  return result


async def write_opportunity_archive(
  directory: str,
  records: AsyncIterable[dict[str, Any]],
  *,
  run_id: str,
  backtest_id: str,
  version: int,
  account_id: str,
) -> dict[str, Any]:
  """Stream an already durable run projection; publish only a complete file."""
  destination = Path(directory) / "opportunity_evaluations.jsonl"
  temporary = destination.with_suffix(".jsonl.tmp")
  digest = hashlib.sha256()
  count = 0
  try:
    async with aiofiles.open(temporary, "wb") as output:
      async for record in records:
        if (
          record.get("strategy_run_id") != run_id
          or record.get("account_id") != account_id
        ):
          raise ValueError("REPLAY_EVIDENCE_IDENTITY_MISMATCH")
        if record.get("record_kind") not in {"MATERIAL", "COALESCED_DIAGNOSTIC"}:
          raise ValueError("REPLAY_EVIDENCE_INVALID_KIND")
        encoded = (
          json.dumps(
            record,
            ensure_ascii=False,
            sort_keys=True,
            allow_nan=False,
            separators=(",", ":"),
          )
          + "\n"
        ).encode("utf-8")
        await output.write(encoded)
        digest.update(encoded)
        count += 1
      await output.flush()
      os.fsync(output.fileno())
    os.replace(temporary, destination)
  except BaseException:
    temporary.unlink(missing_ok=True)
    raise
  return {
    "schema_version": 1,
    "strategy_run_id": run_id,
    "backtest_id": backtest_id,
    "version": version,
    "account_id": account_id,
    "count": count,
    "content_fingerprint": digest.hexdigest(),
  }


def file_fingerprint(path: str | Path) -> str:
  digest = hashlib.sha256()
  try:
    with open(path, "rb") as source:
      for block in iter(lambda: source.read(1024 * 1024), b""):
        digest.update(block)
  except OSError as exc:
    raise ReplayEvidenceUnavailable("ARCHIVE_INTEGRITY_FAILED") from exc
  return digest.hexdigest()


def read_manifest(path: str, *, run_id: str, backtest_id: str, version: int) -> dict:
  try:
    with open(path, encoding="utf-8") as source:
      manifest = json.load(source)
  except (OSError, ValueError) as exc:
    raise ReplayEvidenceUnavailable("ARCHIVE_UNAVAILABLE") from exc
  if not isinstance(manifest, dict) or (
    manifest.get("strategy_run_id") != run_id
    or manifest.get("backtest_id") != backtest_id
    or manifest.get("version") != version
  ):
    raise ReplayEvidenceUnavailable("ARCHIVE_IDENTITY_MISMATCH")
  return manifest


def artifact_path(manifest_path: str, manifest: dict, key: str) -> Path:
  name = (manifest.get("artifacts") or {}).get(key)
  if not isinstance(name, str) or Path(name).name != name or name in {".", ".."}:
    raise ReplayEvidenceUnavailable("ARCHIVE_UNAVAILABLE")
  path = Path(manifest_path).resolve().parent / name
  if (
    not path.is_file() or path.resolve().parent != Path(manifest_path).resolve().parent
  ):
    raise ReplayEvidenceUnavailable("ARCHIVE_UNAVAILABLE")
  return path


def sealed_opportunity_path(
  manifest_path: str,
  manifest: dict,
  *,
  account_id: str,
) -> Path:
  if manifest.get("schema_version") != 4:
    raise ReplayEvidenceUnavailable("SIGNAL_ARCHIVE_NOT_RECORDED")
  if manifest.get("sealed") is not True:
    raise ReplayEvidenceUnavailable("ARCHIVE_NOT_SEALED")
  metadata = manifest.get(OPPORTUNITY_ARTIFACT) or {}
  if (
    metadata.get("account_id") != account_id
    or metadata.get("strategy_run_id") != manifest.get("strategy_run_id")
    or metadata.get("backtest_id") != manifest.get("backtest_id")
    or metadata.get("version") != manifest.get("version")
    or metadata.get("schema_version") != 1
  ):
    raise ReplayEvidenceUnavailable("ARCHIVE_IDENTITY_MISMATCH")
  path = artifact_path(manifest_path, manifest, OPPORTUNITY_ARTIFACT)
  if file_fingerprint(path) != metadata.get("content_fingerprint"):
    raise ReplayEvidenceUnavailable("ARCHIVE_INTEGRITY_FAILED")
  return path


def iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
  try:
    with path.open(encoding="utf-8") as source:
      for line in source:
        if line.strip():
          record = json.loads(line)
          if not isinstance(record, dict):
            raise ValueError("Expected an evidence record")
          yield record
  except (OSError, ValueError) as exc:
    raise ReplayEvidenceUnavailable("ARCHIVE_INTEGRITY_FAILED") from exc
