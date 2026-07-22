"""
GraphQL应用模块
提供GraphQL API服务，支持订阅功能
"""

import uuid
from typing import Any, Dict

from fastapi import FastAPI, Request
from starlette.requests import HTTPConnection
from strawberry.dataloader import DataLoader
from strawberry.exceptions import ConnectionRejectionError
from strawberry.fastapi import GraphQLRouter

from auth.errors import AuthError
from auth.service import AuthService
from config.settings import settings
from database.relational_connection import get_async_db

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
    return await super().on_ws_connect(context)


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
