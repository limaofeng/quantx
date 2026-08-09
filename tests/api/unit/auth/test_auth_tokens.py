from datetime import datetime, timezone

import pytest
from quantx_api.auth.errors import AuthError
from quantx_api.auth.passwords import hash_password, verify_password
from quantx_api.auth.tokens import decode_access_token, issue_access_token
from quantx_infrastructure.config.settings import Settings


def _settings(**overrides) -> Settings:
  values = {
    "debug": False,
    "database_url": "postgresql+asyncpg://test:test@localhost/test",
    "secret_key": "test-signing-key-" + "x" * 48,
  }
  values.update(overrides)
  return Settings(_env_file=None, **values)


def test_password_hash_is_salted_and_verifiable():
  password = "correct horse battery staple"
  first = hash_password(password)
  second = hash_password(password)

  assert first != second
  assert password not in first
  assert verify_password(password, first)
  assert not verify_password("wrong password", first)


def test_hs256_access_token_round_trip_has_minimal_claims():
  settings = _settings()
  token, expires_at = issue_access_token("user-1", "session-1", settings)
  claims = decode_access_token(token, settings)

  assert claims.user_id == "user-1"
  assert claims.device_session_id == "session-1"
  assert claims.expires_at == expires_at
  assert expires_at > datetime.now(timezone.utc).replace(tzinfo=None)
  assert "account" not in token.lower()


def test_tampered_access_token_is_rejected():
  settings = _settings()
  token, _ = issue_access_token("user-1", "session-1", settings)
  replacement = "A" if token[-1] != "A" else "B"

  with pytest.raises(AuthError) as raised:
    decode_access_token(token[:-1] + replacement, settings)

  assert raised.value.code == "UNAUTHENTICATED"


def test_default_signing_key_fails_closed():
  settings = _settings(secret_key="change-this-secret-key")

  with pytest.raises(AuthError) as raised:
    issue_access_token("user-1", "session-1", settings)

  assert raised.value.code == "AUTH_NOT_CONFIGURED"
  assert raised.value.status_code == 503
