"""Deployment safety shared by the server and the Windows-only Agent."""

from __future__ import annotations

import sys


def live_runtime_allowed(environment: str, *, platform: str | None = None) -> bool:
  """Testing still requires the caller's explicit real-trading gates."""
  return (platform or sys.platform) == "win32" and environment in {
    "production",
    "testing",
  }
