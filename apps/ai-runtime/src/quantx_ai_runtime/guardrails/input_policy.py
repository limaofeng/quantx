"""Cheap deterministic checks before a model request is made."""

MAX_MESSAGE_LENGTH = 12_000


def validate_user_text(value: str) -> str:
  normalized = str(value or "").strip()
  if not normalized or len(normalized) > MAX_MESSAGE_LENGTH:
    raise ValueError("AI_INVALID_MESSAGE")
  return normalized
