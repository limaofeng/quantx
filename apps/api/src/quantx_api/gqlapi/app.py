"""
GraphQL应用模块
提供GraphQL API服务，支持订阅功能
"""

import asyncio
import uuid
from typing import Any, Dict

from fastapi import FastAPI, Request
from quantx_infrastructure.config.settings import settings
from quantx_infrastructure.database.relational_connection import get_async_db
from starlette.requests import HTTPConnection
from starlette.websockets import WebSocket, WebSocketState
from strawberry.dataloader import DataLoader
from strawberry.exceptions import ConnectionRejectionError
from strawberry.fastapi import GraphQLRouter

from quantx_api.auth.errors import AuthError
from quantx_api.auth.principal import Principal
from quantx_api.auth.service import AuthService
from quantx_api.auth.tokens import utcnow

from .dataloaders.quote_loader import load_quotes
from .schema import schema
from .security import bearer_from_connection_params, extract_bearer


async def _authenticate(token: str):
  async for db in get_async_db():
    return await AuthService(db).authenticate(token)
  raise RuntimeError("数据库会话不可用")


async def get_context(request: HTTPConnection):
  """Build an authenticated context for HTTP or the WebSocket handshake."""
  request_id = str(
    getattr(getattr(request, "state", None), "request_id", "") or uuid.uuid4()
  )[:64]
  context: Dict[str, Any] = {
    "auth_error": None,
    "principal": None,
    "quote_loader": DataLoader(load_fn=load_quotes),
    "request": request,
    "request_id": request_id,
  }
  authorization = request.headers.get("authorization")
  if authorization:
    try:
      context["principal"] = await _authenticate(extract_bearer(authorization))
    except AuthError as exc:
      context["auth_error"] = exc
  elif isinstance(request, Request):
    context["auth_error"] = AuthError(
      "UNAUTHENTICATED", "缺少 Bearer 访问令牌", status_code=401
    )
  return context


class AuthenticatedGraphQLRouter(GraphQLRouter):
  async def run(self, *args, **kwargs):
    context = kwargs.get("context")
    try:
      return await super().run(*args, **kwargs)
    finally:
      if isinstance(context, dict):
        expiry_task = context.get("auth_expiry_task")
        if isinstance(expiry_task, asyncio.Task) and not expiry_task.done():
          expiry_task.cancel()

  async def on_ws_connect(self, context: Dict[str, Any]):
    if context.get("principal") is None:
      try:
        token = bearer_from_connection_params(context.get("connection_params"))
        context["principal"] = await _authenticate(token)
        context["auth_error"] = None
      except AuthError as exc:
        raise ConnectionRejectionError(
          {
            "code": exc.code,
            "message": exc.message,
            "requestId": context.get("request_id"),
            "retryable": exc.retryable,
          }
        ) from None
    principal = context.get("principal")
    websocket = context.get("request")
    if isinstance(principal, Principal) and isinstance(websocket, WebSocket):
      context["auth_expiry_task"] = asyncio.create_task(
        self._close_at_access_expiry(websocket, principal)
      )
    return await super().on_ws_connect(context)

  @staticmethod
  async def _close_at_access_expiry(
    websocket: WebSocket, principal: Principal
  ) -> None:
    delay = max(
      0.0,
      (principal.access_token_expires_at - utcnow()).total_seconds(),
    )
    try:
      await asyncio.sleep(delay)
      if websocket.client_state is not WebSocketState.DISCONNECTED:
        await websocket.close(code=4401, reason="访问令牌已过期")
    except (asyncio.CancelledError, RuntimeError):
      return


def create_graphql_app() -> GraphQLRouter:
  """创建GraphQL应用，支持WebSocket订阅"""
  return AuthenticatedGraphQLRouter(
    schema,
    allow_queries_via_get=False,
    context_getter=get_context,
    graphql_ide="graphiql"
    if settings.graphql_playground and settings.is_development
    else None,
  )


def setup_graphql(app: FastAPI) -> None:
  """为FastAPI应用添加GraphQL支持"""
  graphql_app = create_graphql_app()
  app.include_router(graphql_app, prefix="/graphql")
