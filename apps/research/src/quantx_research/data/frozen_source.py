"""File-only research inputs exported by a caller-owned read-only source."""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from .normalization import (
  normalize_daily_bars,
  normalize_dividend_factors,
  normalize_instruments,
)


def _plain(value):
  if isinstance(value, (datetime, date)):
    return value.isoformat()
  if value is pd.NA or value is pd.NaT:
    return None
  if hasattr(value, "item"):
    return value.item()
  raise TypeError(f"Unsupported frozen evidence type: {type(value).__name__}")


def _json(path, value):
  path.write_text(
    json.dumps(value, default=_plain, allow_nan=False, sort_keys=True), encoding="utf-8"
  )


def _digest(path, *, cancel=None):
  digest = hashlib.sha256()
  with path.open("rb") as stream:
    while True:
      if cancel is not None and cancel.is_set():
        raise ValueError("Frozen input verification cancelled")
      block = stream.read(1024 * 1024)
      if not block:
        return digest.hexdigest()
      digest.update(block)


def _no_links(path):
  for part in (path, *path.parents):
    if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
      raise ValueError("Frozen input paths must not contain links")


async def export_frozen_source(
  source, calendar, directory, *, start, end, batch_size=300, stock_codes=None, benchmark_code=None
):
  """Export an explicit inclusive window; caller owns source/snapshot lifetime.

  Include the training warmup and next-session label date in this window.
  No feature computation, database registration or default clients occur here.
  """
  if batch_size <= 0 or start > end:
    raise ValueError("Invalid frozen export window or batch size")
  directory = Path(directory).absolute()
  _no_links(directory)
  if directory.exists():
    raise FileExistsError(directory)
  directory.parent.mkdir(parents=True, exist_ok=True)
  staging = Path(tempfile.mkdtemp(prefix=".source-", dir=directory.parent))
  try:
    if benchmark_code is None:
      instruments = normalize_instruments(
        await source.list_instruments(instrument_types=("stock", "index"))
      )
    else:
      stocks = normalize_instruments(await source.list_instruments(instrument_types=("stock",), codes=stock_codes))
      benchmark = normalize_instruments(await source.list_instruments(instrument_types=("index",), codes=[benchmark_code]))
      stocks = stocks[stocks.instrument_type.eq("stock")]
      if stock_codes is not None:
        stocks = stocks[stocks.stock_code.isin(stock_codes)]
      benchmark = benchmark[benchmark.instrument_type.eq("index") & benchmark.stock_code.eq(benchmark_code)]
      instruments = pd.concat([stocks, benchmark], ignore_index=True).drop_duplicates("stock_code")
      if benchmark.empty or (stock_codes is not None and set(stock_codes) - set(stocks.stock_code)):
        raise ValueError("Frozen source is missing requested instrument metadata")
    codes = sorted(set(instruments.stock_code.dropna().astype(str)))
    if not codes:
      raise ValueError("Frozen source requires instruments")
    instruments.to_parquet(staging / "instruments.parquet", index=False)
    sessions = await calendar.get_trading_calendar("SH", start, end)
    sessions = [pd.Timestamp(value).date().isoformat() for value in sessions]
    if not sessions or sessions != sorted(set(sessions)):
      raise ValueError("Frozen calendar must contain ordered unique sessions")
    if sessions[0] < start.isoformat() or sessions[-1] > end.isoformat():
      raise ValueError("Frozen calendar exceeds export window")
    _json(staging / "calendar.json", sessions)
    coverage = await source.load_dividend_factor_coverage(codes, start=start, end=end)
    coverage = coverage.astype(object).where(coverage.notna(), None)
    _json(staging / "coverage.json", coverage.to_dict(orient="records"))
    factors = normalize_dividend_factors(
      await source.load_dividend_factors(codes, start=start, end=end)
    )
    factors.to_parquet(staging / "factors.parquet", index=False)
    batches = []
    for offset in range(0, len(codes), batch_size):
      selected = codes[offset : offset + batch_size]
      name = f"bars-{len(batches):05d}.parquet"
      bars = normalize_daily_bars(
        await source.load_daily_bars(selected, start, end, batch_size=batch_size)
      )
      bars.to_parquet(staging / name, index=False)
      batches.append({"name": name, "codes": selected})
    files = {
      path.name: {"size": path.stat().st_size, "sha256": _digest(path)}
      for path in sorted(staging.iterdir())
    }
    _json(
      staging / "manifest.json",
      {
        "schema_version": 1,
        "kind": "research-source",
        "start": start.isoformat(),
        "end": end.isoformat(),
        "codes": codes,
        "batches": batches,
        "files": files,
      },
    )
    FrozenResearchDataSource(staging)
    # rename refuses an existing nonempty export, preserving published inputs.
    if directory.exists():
      raise FileExistsError(directory)
    staging.rename(directory)
    return directory
  finally:
    if staging.exists():
      shutil.rmtree(staging)


class FrozenResearchDataSource:
  """Strictly bounded source and SH calendar, without infrastructure imports."""

  def __init__(self, directory, *, cancel=None, input_manifest_sha256=None):
    self.input_manifest_sha256 = input_manifest_sha256
    self.cancel = cancel
    self.directory = Path(directory).absolute()
    _no_links(self.directory)
    path = self.directory / "manifest.json"
    _no_links(path)
    manifest_bytes = path.read_bytes()
    self.manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    self.manifest = json.loads(manifest_bytes)
    manifest = self.manifest
    if manifest.get("schema_version") != 1 or manifest.get("kind") != "research-source":
      raise ValueError("Unsupported frozen source manifest")
    self.start, self.end = (
      pd.Timestamp(manifest["start"]),
      pd.Timestamp(manifest["end"]),
    )
    if self.start > self.end:
      raise ValueError("Invalid frozen source window")
    self.codes = frozenset(manifest["codes"])
    expected = {
      "instruments.parquet",
      "factors.parquet",
      "calendar.json",
      "coverage.json",
    }
    covered = []
    for index, batch in enumerate(manifest["batches"]):
      if batch["name"] != f"bars-{index:05d}.parquet":
        raise ValueError("Invalid frozen bar file")
      expected.add(batch["name"])
      covered.extend(batch["codes"])
    if (
      not self.codes or len(covered) != len(set(covered)) or set(covered) != self.codes
    ):
      raise ValueError("Frozen bar inventory does not match instruments")
    if set(manifest["files"]) != expected:
      raise ValueError("Invalid frozen source file inventory")
    if {p.name for p in self.directory.iterdir()} != expected | {"manifest.json"}:
      raise ValueError("Unexpected frozen source files")
    for name in expected:
      self._file(name)
    sessions = json.loads(self._file("calendar.json").read_text(encoding="utf-8"))
    self.sessions = pd.DatetimeIndex(sessions)
    if (
      self.sessions.empty
      or self.sessions.hasnans
      or not self.sessions.is_unique
      or not self.sessions.is_monotonic_increasing
      or self.sessions[0] < self.start
      or self.sessions[-1] > self.end
    ):
      raise ValueError("Invalid frozen calendar")

  @property
  def provenance(self):
    return {
      "kind": "frozen-research-source",
      "manifest_sha256": self.manifest_sha256,
      "start": self.start.date().isoformat(),
      "end": self.end.date().isoformat(),
      **({"input_manifest_sha256": self.input_manifest_sha256}
         if self.input_manifest_sha256 is not None else {}),
    }

  def _file(self, name):
    path = self.directory / name
    _no_links(path)
    evidence = self.manifest["files"][name]
    if (
      not path.is_file()
      or path.stat().st_size != evidence["size"]
      or _digest(path, cancel=self.cancel) != evidence["sha256"]
    ):
      raise ValueError(f"Frozen source file integrity mismatch: {name}")
    return path

  def _query(self, codes, start=None, end=None):
    codes = {str(code).strip().upper() for code in codes}
    if not codes.issubset(self.codes):
      raise ValueError("Requested instruments are outside frozen source")
    start = self.start if start is None else pd.Timestamp(start)
    end = self.end if end is None else pd.Timestamp(end)
    if start < self.start or end > self.end or start > end:
      raise ValueError("Requested dates are outside frozen source")
    return codes, start, end

  async def list_instruments(self, *, instrument_types=("stock",), codes=None):
    selected, _, _ = self._query(self.codes if codes is None else codes)
    frame = pd.read_parquet(self._file("instruments.parquet"))
    return frame[
      frame.stock_code.isin(selected) & frame.instrument_type.isin(instrument_types)
    ].copy()

  async def load_daily_bars(self, stock_codes, start, end, *, batch_size=300):
    if batch_size <= 0:
      raise ValueError("batch_size must be positive")
    selected, start, end = self._query(stock_codes, start, end)
    parts = []
    for batch in self.manifest["batches"]:
      if selected.intersection(batch["codes"]):
        frame = pd.read_parquet(self._file(batch["name"]))
        parts.append(
          frame[frame.stock_code.isin(selected) & frame.time.between(start, end)]
        )
    return normalize_daily_bars(pd.concat(parts, ignore_index=True) if parts else None)

  async def load_dividend_factors(self, stock_codes, *, start=None, end=None):
    selected, start, end = self._query(stock_codes, start, end)
    frame = pd.read_parquet(self._file("factors.parquet"))
    return frame[
      frame.stock_code.isin(selected) & frame.time.between(start, end)
    ].copy()

  async def load_dividend_factor_coverage(self, stock_codes, *, start, end):
    selected, _, _ = self._query(stock_codes, start, end)
    rows = json.loads(self._file("coverage.json").read_text(encoding="utf-8"))
    # Preserve whole evidence records; the existing gate checks interval coverage.
    return pd.DataFrame(
      [row for row in rows if selected.intersection(row.get("stock_codes", []))],
      dtype=object,
    )

  async def get_next_trading_date(self, market="SH", from_date=None):
    if market != "SH" or from_date is None:
      raise ValueError("Frozen calendar requires SH and an explicit date")
    _, start, _ = self._query([], from_date, from_date)
    following = self.sessions[self.sessions > start]
    if following.empty:
      raise ValueError("Next session is outside frozen calendar")
    return following[0].date()
