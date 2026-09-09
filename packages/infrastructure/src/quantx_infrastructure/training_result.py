"""Shared public training evidence sanitization."""

from typing import Any, Mapping


def safe_public_details(value: Any) -> dict[str, Any]:
  if hasattr(value, "to_dict"):
    value = value.to_dict()
  if not isinstance(value, Mapping):
    value = {"status": str(value)}
  blocked = {
    "path",
    "root",
    "directory",
    "panel_path",
    "manifest_path",
    "instance_id",
    "device_serial",
    "password",
    "secret",
    "token",
    "credential",
    "api_key",
  }

  def scrub(item: Any, key: str = "") -> Any:
    if key.lower() in blocked:
      return None
    if isinstance(item, Mapping):
      return {
        str(name): scrub(child, str(name))
        for name, child in item.items()
        if str(name).lower() not in blocked
      }
    if isinstance(item, (list, tuple, set)):
      return [scrub(child, key) for child in item]
    return item

  result = scrub(value)
  return result if isinstance(result, dict) else {"status": str(result)}
