"""Local fail-closed emergency stop independent of server availability."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class EmergencyStopStore:
  def __init__(self, path: Path) -> None:
    self.path = path

  def status(self) -> dict[str, Any]:
    if not self.path.exists():
      return {"active": False, "reason": "", "activated_at": None}
    try:
      value = json.loads(self.path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
      return {
        "active": True,
        "reason": "emergency state is unreadable",
        "activated_at": None,
      }
    return {
      "active": bool(value.get("active", True)),
      "reason": str(value.get("reason") or ""),
      "activated_at": value.get("activated_at"),
    }

  def activate(self, reason: str) -> dict[str, Any]:
    normalized_reason = str(reason or "").strip()
    if not normalized_reason:
      raise ValueError("emergency stop requires a reason")
    self.path.parent.mkdir(parents=True, exist_ok=True)
    value = {
      "active": True,
      "reason": normalized_reason[:1000],
      "activated_at": datetime.now(timezone.utc).isoformat(),
    }
    temporary = self.path.with_suffix(f"{self.path.suffix}.tmp-{os.getpid()}")
    temporary.write_text(
      json.dumps(value, ensure_ascii=False, indent=2),
      encoding="utf-8",
    )
    temporary.replace(self.path)
    return value

  def clear(self, confirmation: str) -> None:
    if confirmation != "CLEAR-LOCAL-EMERGENCY":
      raise ValueError(
        "clear requires the exact confirmation CLEAR-LOCAL-EMERGENCY"
      )
    self.path.unlink(missing_ok=True)
