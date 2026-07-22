"""Minimal HS256 access tokens and opaque refresh-token digests."""

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Tuple

from config.settings import Settings

from .errors import AuthError, unauthenticated

_DEFAULT_SECRET = "change-this-secret-key"


def _naive_utc_from_timestamp(value: int) -> datetime:
  return datetime.fromtimestamp(value, timezone.utc).replace(tzinfo=None)


def utcnow() -> datetime:
  return datetime.now(timezone.utc).replace(tzinfo=None)


@dataclass(frozen=True)
class AccessClaims:
  user_id: str
  device_session_id: str
  expires_at: datetime


def require_signing_key(settings: Settings) -> bytes:
  secret = settings.secret_key.strip()
  normalized_secret = secret.lower()
  if (
    secret == _DEFAULT_SECRET
    or normalized_secret.startswith("change-this")
    or normalized_secret.startswith("replace-me")
    or len(secret.encode("utf-8")) < 32
  ):
    raise AuthError(
      "AUTH_NOT_CONFIGURED",
      "服务端认证密钥尚未安全配置",
      status_code=503,
    )
  if settings.algorithm.upper() != "HS256":
    raise AuthError(
      "AUTH_NOT_CONFIGURED",
      "服务端仅支持 HS256 访问令牌",
      status_code=503,
    )
  return secret.encode("utf-8")


def _encode_part(value: Dict[str, Any]) -> str:
  raw = json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")
  return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _decode_part(value: str) -> Dict[str, Any]:
  padding = "=" * (-len(value) % 4)
  raw = base64.urlsafe_b64decode((value + padding).encode("ascii"))
  decoded = json.loads(raw.decode("utf-8"))
  if not isinstance(decoded, dict):
    raise ValueError("JWT segment must be an object")
  return decoded


def issue_access_token(
  user_id: str, device_session_id: str, settings: Settings
) -> Tuple[str, datetime]:
  key = require_signing_key(settings)
  now = int(time.time())
  expires = now + max(1, settings.access_token_expire_minutes) * 60
  header = {"alg": "HS256", "typ": "JWT"}
  payload = {
    "aud": settings.auth_audience,
    "exp": expires,
    "iat": now,
    "iss": settings.auth_issuer,
    "jti": secrets.token_hex(16),
    "nbf": now,
    "sid": device_session_id,
    "sub": user_id,
  }
  signing_input = f"{_encode_part(header)}.{_encode_part(payload)}"
  signature = hmac.new(key, signing_input.encode("ascii"), hashlib.sha256).digest()
  encoded_signature = base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")
  return f"{signing_input}.{encoded_signature}", _naive_utc_from_timestamp(expires)


def decode_access_token(token: str, settings: Settings) -> AccessClaims:
  key = require_signing_key(settings)
  try:
    header_part, payload_part, signature_part = token.split(".", 2)
    header = _decode_part(header_part)
    payload = _decode_part(payload_part)
    if header != {"alg": "HS256", "typ": "JWT"}:
      raise ValueError("Unsupported JWT header")
    padding = "=" * (-len(signature_part) % 4)
    signature = base64.urlsafe_b64decode((signature_part + padding).encode("ascii"))
    canonical_signature = (
      base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")
    )
    if not hmac.compare_digest(signature_part, canonical_signature):
      raise ValueError("Non-canonical JWT signature")
    expected = hmac.new(
      key,
      f"{header_part}.{payload_part}".encode("ascii"),
      hashlib.sha256,
    ).digest()
    if not hmac.compare_digest(signature, expected):
      raise ValueError("Invalid JWT signature")

    now = int(time.time())
    expires = int(payload["exp"])
    issued_at = int(payload["iat"])
    not_before = int(payload["nbf"])
    if payload.get("iss") != settings.auth_issuer:
      raise ValueError("Invalid issuer")
    if payload.get("aud") != settings.auth_audience:
      raise ValueError("Invalid audience")
    if now >= expires or issued_at > now + 30 or not_before > now + 30:
      raise ValueError("Expired or not active")
    user_id = str(payload["sub"])
    session_id = str(payload["sid"])
    if not user_id or not session_id:
      raise ValueError("Missing subject")
    return AccessClaims(
      user_id=user_id,
      device_session_id=session_id,
      expires_at=_naive_utc_from_timestamp(expires),
    )
  except AuthError:
    raise
  except (KeyError, TypeError, ValueError, UnicodeError, json.JSONDecodeError):
    raise unauthenticated() from None


def issue_refresh_token() -> str:
  return secrets.token_urlsafe(48)


def digest_refresh_token(token: str, settings: Settings) -> str:
  key = require_signing_key(settings)
  return hmac.new(key, token.encode("utf-8"), hashlib.sha256).hexdigest()


def refresh_expiry(settings: Settings) -> datetime:
  return utcnow() + timedelta(days=max(1, settings.refresh_token_expire_days))
