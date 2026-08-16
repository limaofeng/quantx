"""Transport-neutral GraphQL authentication and top-level authorization."""

import inspect
import logging
import re
import time
import uuid
from typing import Any, Dict, Optional

from graphql import GraphQLError
from strawberry.extensions import SchemaExtension

from quantx_api.auth.errors import AuthError, unauthenticated
from quantx_api.auth.principal import Principal
from quantx_api.auth.tokens import utcnow

from .operation_policy import operation_policy

logger = logging.getLogger(__name__)

_SLOW_RESOLVER_SECONDS = 1.0

_ACCOUNT_KEY = re.compile(r"^account_?id$", re.IGNORECASE)


def required_permissions(operation_name: str, field_name: str) -> tuple[str, ...]:
  return operation_policy(operation_name, field_name).required_permissions


def required_permission(operation_name: str, field_name: str) -> str:
  """Return the primary permission for legacy internal callers."""
  return required_permissions(operation_name, field_name)[0]


def extract_bearer(value: Optional[str]) -> str:
  scheme, separator, token = (value or "").partition(" ")
  if not separator or scheme.lower() != "bearer" or not token.strip():
    raise unauthenticated("缺少 Bearer 访问令牌")
  return token.strip()


def bearer_from_connection_params(params: Any) -> str:
  if not isinstance(params, dict):
    raise unauthenticated("WebSocket connection_init 缺少认证参数")
  value = (
    params.get("Authorization")
    or params.get("authorization")
    or params.get("authToken")
  )
  return extract_bearer(value if isinstance(value, str) else None)


def principal_from_context(context: Dict[str, Any]) -> Principal:
  principal = context.get("principal")
  if isinstance(principal, Principal):
    if principal.access_token_expires_at <= utcnow():
      raise unauthenticated("访问令牌已过期")
    return principal
  error = context.get("auth_error")
  if isinstance(error, AuthError):
    raise error
  raise unauthenticated()


def authorized_account_id(info: Any, requested: Optional[str] = None) -> str:
  return principal_from_context(info.context).require_account(requested)


def _request_id(context: Dict[str, Any]) -> str:
  return str(context.get("request_id") or uuid.uuid4())[:64]


def _graphql_error(error: AuthError, context: Dict[str, Any]) -> GraphQLError:
  return GraphQLError(
    error.message,
    extensions={
      "code": error.code,
      "requestId": _request_id(context),
      "retryable": error.retryable,
    },
  )


def _account_id_from_kwargs(kwargs: Dict[str, Any]) -> Optional[str]:
  for key, value in kwargs.items():
    if _ACCOUNT_KEY.match(key) and isinstance(value, str):
      return value
    if key == "input":
      if isinstance(value, dict):
        nested = value.get("account_id") or value.get("accountId")
      else:
        nested = getattr(value, "account_id", None)
      if isinstance(nested, str):
        return nested
  return None


class AuthorizationExtension(SchemaExtension):
  """Default-deny authorization for every top-level GraphQL field."""

  async def resolve(self, _next, root, info, *args, **kwargs):
    path = getattr(info, "path", None)
    is_top_level = path is not None and path.prev is None
    started_at = time.perf_counter() if is_top_level else None
    operation = getattr(getattr(info, "operation", None), "operation", None)
    operation_name = str(getattr(operation, "value", "")).capitalize()
    context = info.context if isinstance(info.context, dict) else {}
    try:
      if (
        is_top_level
        and operation_name in {"Query", "Mutation", "Subscription"}
        and not info.field_name.startswith("__")
      ):
        principal = principal_from_context(context)
        for permission in required_permissions(operation_name, info.field_name):
          principal.require_permission(permission)
        requested_account_id = _account_id_from_kwargs(kwargs)
        if requested_account_id is not None:
          principal.require_account(requested_account_id)

      result = _next(root, info, *args, **kwargs)
      if inspect.isawaitable(result):
        return await result
      return result
    except AuthError as exc:
      raise _graphql_error(exc, context) from None
    finally:
      if started_at is not None:
        duration = time.perf_counter() - started_at
        if duration >= _SLOW_RESOLVER_SECONDS:
          operation_node = getattr(info, "operation", None)
          operation_definition = getattr(
            getattr(operation_node, "name", None),
            "value",
            None,
          )
          logger.warning(
            "GraphQL slow resolver: operation=%s type=%s field=%s "
            "duration=%.3fs request_id=%s",
            operation_definition or "anonymous",
            operation_name or "Unknown",
            info.field_name,
            duration,
            _request_id(context),
          )
