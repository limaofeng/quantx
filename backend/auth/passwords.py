"""Password hashing based on the standard-library scrypt KDF."""

import base64
import hashlib
import hmac
import secrets

_N = 2**14
_R = 8
_P = 1
_DKLEN = 64
_MAX_MEMORY = 64 * 1024 * 1024


def _derive(password: str, salt: bytes) -> bytes:
  if not password or len(password.encode("utf-8")) > 1024:
    raise ValueError("密码长度无效")
  return hashlib.scrypt(
    password.encode("utf-8"),
    salt=salt,
    n=_N,
    r=_R,
    p=_P,
    dklen=_DKLEN,
    maxmem=_MAX_MEMORY,
  )


def hash_password(password: str) -> str:
  salt = secrets.token_bytes(16)
  digest = _derive(password, salt)
  return "$".join(
    [
      "scrypt",
      str(_N),
      str(_R),
      str(_P),
      base64.urlsafe_b64encode(salt).decode("ascii"),
      base64.urlsafe_b64encode(digest).decode("ascii"),
    ]
  )


def verify_password(password: str, encoded: str) -> bool:
  try:
    algorithm, n_value, r_value, p_value, salt_value, digest_value = encoded.split(
      "$", 5
    )
    if (
      algorithm != "scrypt"
      or int(n_value) != _N
      or int(r_value) != _R
      or int(p_value) != _P
    ):
      return False
    salt = base64.urlsafe_b64decode(salt_value.encode("ascii"))
    expected = base64.urlsafe_b64decode(digest_value.encode("ascii"))
    actual = _derive(password, salt)
    return hmac.compare_digest(actual, expected)
  except (TypeError, ValueError, UnicodeError):
    return False
