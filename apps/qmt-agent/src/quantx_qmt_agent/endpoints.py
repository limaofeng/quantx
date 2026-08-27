"""Canonical outbound API endpoints for the Windows QMT Agent."""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit


def normalize_api_url(value: str) -> str:
  raw = str(value or "").strip()
  if not raw or any(character.isspace() for character in raw):
    raise ValueError("api_url 不能为空或包含空白字符")
  parsed = urlsplit(raw)
  scheme = parsed.scheme.lower()
  if scheme not in {"http", "https"}:
    raise ValueError("api_url 仅允许 http:// 或 https://")
  if (
    not parsed.hostname
    or parsed.username is not None
    or parsed.password is not None
    or parsed.query
    or parsed.fragment
    or parsed.path not in {"", "/"}
  ):
    raise ValueError("api_url 必须是无凭据、无路径、无查询参数的服务根地址")
  try:
    port = parsed.port
  except ValueError as exc:
    raise ValueError("api_url 端口无效") from exc
  host = parsed.hostname.lower()
  if ":" in host:
    host = f"[{host}]"
  netloc = f"{host}:{port}" if port is not None else host
  return urlunsplit((scheme, netloc, "", "", ""))


def websocket_url(api_url: str, path: str = "/ws/agent") -> str:
  normalized = normalize_api_url(api_url)
  parsed = urlsplit(normalized)
  scheme = "wss" if parsed.scheme == "https" else "ws"
  normalized_path = "/" + str(path or "").lstrip("/")
  return urlunsplit((scheme, parsed.netloc, normalized_path, "", ""))


def masked_device_id(value: str) -> str:
  normalized = str(value or "").strip()
  if len(normalized) <= 8:
    return "*" * len(normalized)
  return f"{normalized[:4]}…{normalized[-4:]}"


def masked_account_id(value: str) -> str:
  normalized = str(value or "").strip()
  if len(normalized) <= 4:
    return "*" * len(normalized)
  return f"***{normalized[-4:]}"
