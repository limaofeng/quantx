"""Device metadata and Windows Credential Manager integration."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .endpoints import normalize_api_url

KEYRING_SERVICE = "QuantX QMT Agent"


def state_directory() -> Path:
  configured = os.environ.get("QUANTX_AGENT_STATE_DIR", "").strip()
  if configured:
    return Path(configured).expanduser().resolve()
  local_app_data = Path(os.environ.get("LOCALAPPDATA", Path.home()))
  return local_app_data / "QuantX" / "qmt-agent"


@dataclass(frozen=True)
class DeviceConfiguration:
  api_url: str
  device_id: str


class DeviceCredentialStore:
  def __init__(self, directory: Optional[Path] = None) -> None:
    self.directory = directory or state_directory()
    self.config_path = self.directory / "device.json"

  def save(self, *, api_url: str, device_id: str, device_secret: str) -> None:
    import keyring

    self.directory.mkdir(parents=True, exist_ok=True)
    keyring.set_password(KEYRING_SERVICE, device_id, device_secret)
    self.config_path.write_text(
      json.dumps(
        {"api_url": normalize_api_url(api_url), "device_id": device_id},
        indent=2,
      ),
      encoding="utf-8",
    )

  def load(self) -> tuple[DeviceConfiguration, str]:
    import keyring

    if not self.config_path.exists():
      raise RuntimeError("QMT Agent 尚未登记，请先运行 enroll")
    raw = json.loads(self.config_path.read_text(encoding="utf-8"))
    configuration = DeviceConfiguration(
      api_url=normalize_api_url(str(raw["api_url"])),
      device_id=str(raw["device_id"]),
    )
    secret = keyring.get_password(KEYRING_SERVICE, configuration.device_id)
    if not secret:
      raise RuntimeError("Windows Credential Manager 中不存在设备密钥")
    return configuration, secret

  def revoke_local(self) -> None:
    import keyring

    if not self.config_path.exists():
      return
    raw = json.loads(self.config_path.read_text(encoding="utf-8"))
    device_id = str(raw.get("device_id", ""))
    if device_id:
      try:
        keyring.delete_password(KEYRING_SERVICE, device_id)
      except keyring.errors.PasswordDeleteError:
        pass
    self.config_path.unlink(missing_ok=True)
