"""Local BACKTEST versions and append-only, hash-chained converged frames.

No session factory, PAPER tables, account discovery or network access belongs
here. A resumed replay must prove the already committed prefix before appending.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4

from quantx_domain.trading.t_assistant_execution import stable_manifest_hash


class TAssistantBacktestStore:
  VERSION = "t-assistant-backtest.v1"

  def __init__(self, directory: Path):
    self.directory = Path(directory)
    self.manifest = self._read(self.directory / "version.json")
    material = self.manifest["material"]
    if (
      self.manifest["hash"] != stable_manifest_hash(material)
      or material["schema_version"] != self.VERSION
      or material["environment"] != "BACKTEST"
      or material["scorer_mode"] != "RULE_ONLY"
    ):
      raise ValueError("BACKTEST_VERSION_INVALID")

  @classmethod
  def create(cls, root: Path, *, frozen: dict) -> "TAssistantBacktestStore":
    required = {"config", "data", "code", "broker", "timeline", "initial_account"}
    if not required <= frozen.keys() or any(not frozen[k] for k in required):
      raise ValueError("BACKTEST_FROZEN_INPUT_REQUIRED")
    execution_id = str(uuid4())
    directory = Path(root) / execution_id
    directory.mkdir(parents=True, exist_ok=False)
    material = {
      "schema_version": cls.VERSION,
      "execution_id": execution_id,
      "environment": "BACKTEST",
      "scorer_mode": "RULE_ONLY",
      "frozen": frozen,
    }
    cls._create(
      directory / "version.json",
      {
        "material": material,
        "hash": stable_manifest_hash(material),
      },
    )
    return cls(directory)

  @staticmethod
  def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))

  @staticmethod
  def _create(path, value):
    # Exclusive publication: an existing result is never silently overwritten.
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
    temporary = path.with_name(f".{path.name}.{uuid4()}.tmp")
    try:
      with temporary.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
      os.link(temporary, path)
    finally:
      temporary.unlink(missing_ok=True)

  def frames(self):
    previous = self.manifest["hash"]
    paths = sorted(self.directory.glob("frame-*.json"))
    for index, path in enumerate(paths):
      if path.name != f"frame-{index:010d}.json":
        raise ValueError("BACKTEST_FRAME_GAP")
      frame = self._read(path)
      if (
        frame["index"] != index
        or frame["previous"] != previous
        or frame["hash"]
        != stable_manifest_hash(
          {
            "index": index,
            "previous": previous,
            "facts": frame["facts"],
          }
        )
      ):
        raise ValueError("BACKTEST_FRAME_HASH_MISMATCH")
      previous = frame["hash"]
      yield frame

  def commit_frame(self, *, index: int, previous: str, facts: dict):
    if type(index) is not int or index < 0:
      raise ValueError("BACKTEST_FRAME_INDEX_INVALID")
    expected = (
      self.manifest["hash"]
      if index == 0
      else self._read(self.directory / f"frame-{index - 1:010d}.json")["hash"]
    )
    if previous != expected:
      raise ValueError("BACKTEST_FRAME_PREFIX_MISMATCH")
    material = {"index": index, "previous": previous, "facts": facts}
    frame = {**material, "hash": stable_manifest_hash(material)}
    path = self.directory / f"frame-{index:010d}.json"
    if path.exists():
      if self._read(path) != frame:
        raise ValueError("BACKTEST_REPLAY_DIVERGED")
    else:
      if (self.directory / "result.json").exists():
        raise ValueError("BACKTEST_RESULT_ALREADY_FROZEN")
      self._create(path, frame)
    return frame["hash"]

  def finish(self, result: dict):
    frames = list(self.frames())
    material = {
      "version_hash": self.manifest["hash"],
      "execution_id": self.manifest["material"]["execution_id"],
      "frame_count": len(frames),
      "last_frame_hash": frames[-1]["hash"] if frames else self.manifest["hash"],
      "result": result,
    }
    value = {"material": material, "hash": stable_manifest_hash(material)}
    path = self.directory / "result.json"
    if path.exists():
      if self._read(path) != value:
        raise ValueError("BACKTEST_RESULT_DIVERGED")
    else:
      self._create(path, value)
    return value
