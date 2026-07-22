"""FastAPI REST endpoints for native-client sessions."""

import uuid
from typing import AsyncGenerator, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from database.relational_connection import get_async_db

from .errors import AuthError
from .principal import Principal
from .service import AuthService, SessionGrant


def _to_camel(value: str) -> str:
  first, *rest = value.split("_")
  return first + "".join(part.capitalize() for part in rest)


class APIModel(BaseModel):
  model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)


class LoginRequest(APIModel):
  username: str = Field(min_length=1, max_length=80)
  password: SecretStr = Field(min_length=1, max_length=1024)
  device_name: Optional[str] = Field(default=None, max_length=120)


class RefreshRequest(APIModel):
  refresh_token: SecretStr = Field(min_length=32, max_length=512)


class SessionUserResponse(APIModel):
  id: str
  username: str
  display_name: str
  permissions: list[str]
  authorized_account_ids: list[str]


class SessionGrantResponse(APIModel):
  access_token: str
  refresh_token: str
  access_token_expires_at: str
  refresh_token_expires_at: str
  token_type: str = "Bearer"
  device_session_id: str
  user: SessionUserResponse


class SessionStateResponse(APIModel):
  device_session_id: str
  access_token_expires_at: str
  user: SessionUserResponse


auth_router = APIRouter(prefix="/auth", tags=["authentication"])


async def _database() -> AsyncGenerator[AsyncSession, None]:
  async for db in get_async_db():
    yield db


def _request_id(request: Request) -> str:
  return str(getattr(request.state, "request_id", "") or uuid.uuid4())


def _client_fingerprint(request: Request) -> str:
  client = request.client.host if request.client else "unknown"
  agent = request.headers.get("user-agent", "")[:160]
  return f"{client}\n{agent}"


def _bearer_token(request: Request) -> str:
  authorization = request.headers.get("authorization", "")
  scheme, separator, token = authorization.partition(" ")
  if not separator or scheme.lower() != "bearer" or not token.strip():
    raise _http_error(
      AuthError("UNAUTHENTICATED", "缺少 Bearer 访问令牌"), _request_id(request)
    )
  return token.strip()


def _http_error(error: AuthError, request_id: Optional[str] = None) -> HTTPException:
  return HTTPException(
    status_code=error.status_code,
    detail={
      "code": error.code,
      "message": error.message,
      "requestId": request_id,
      "retryable": error.retryable,
    },
  )


async def _principal(
  request: Request, db: AsyncSession = Depends(_database)
) -> Principal:
  try:
    return await AuthService(db).authenticate(_bearer_token(request))
  except AuthError as exc:
    raise _http_error(exc, _request_id(request)) from None


def _user_response(principal: Principal) -> SessionUserResponse:
  return SessionUserResponse(
    id=principal.user_id,
    username=principal.username,
    display_name=principal.display_name,
    permissions=sorted(principal.permissions),
    authorized_account_ids=list(principal.authorized_account_ids),
  )


def _grant_response(grant: SessionGrant) -> SessionGrantResponse:
  return SessionGrantResponse(
    access_token=grant.access_token,
    refresh_token=grant.refresh_token,
    access_token_expires_at=grant.access_token_expires_at.isoformat() + "Z",
    refresh_token_expires_at=grant.refresh_token_expires_at.isoformat() + "Z",
    device_session_id=grant.principal.device_session_id,
    user=_user_response(grant.principal),
  )


def _disable_session_caching(response: Response) -> None:
  response.headers["Cache-Control"] = "no-store"
  response.headers["Pragma"] = "no-cache"


@auth_router.post("/session", response_model=SessionGrantResponse)
async def create_session(
  payload: LoginRequest,
  request: Request,
  response: Response,
  db: AsyncSession = Depends(_database),
) -> SessionGrantResponse:
  _disable_session_caching(response)
  try:
    grant = await AuthService(db).login(
      payload.username,
      payload.password.get_secret_value(),
      device_name=payload.device_name,
      client_fingerprint=_client_fingerprint(request),
      request_id=_request_id(request),
    )
    return _grant_response(grant)
  except AuthError as exc:
    raise _http_error(exc, _request_id(request)) from None


@auth_router.post("/session/refresh", response_model=SessionGrantResponse)
async def refresh_session(
  payload: RefreshRequest,
  request: Request,
  response: Response,
  db: AsyncSession = Depends(_database),
) -> SessionGrantResponse:
  _disable_session_caching(response)
  try:
    grant = await AuthService(db).refresh(
      payload.refresh_token.get_secret_value(), _request_id(request)
    )
    return _grant_response(grant)
  except AuthError as exc:
    raise _http_error(exc, _request_id(request)) from None


@auth_router.get("/session", response_model=SessionStateResponse)
async def get_session(
  response: Response,
  principal: Principal = Depends(_principal),
) -> SessionStateResponse:
  _disable_session_caching(response)
  return SessionStateResponse(
    device_session_id=principal.device_session_id,
    access_token_expires_at=principal.access_token_expires_at.isoformat() + "Z",
    user=_user_response(principal),
  )


@auth_router.delete("/session", status_code=204)
async def delete_session(
  request: Request,
  response: Response,
  all_devices: bool = Query(default=False, alias="allDevices"),
  principal: Principal = Depends(_principal),
  db: AsyncSession = Depends(_database),
) -> None:
  await AuthService(db).logout(
    principal,
    all_devices=all_devices,
    request_id=_request_id(request),
  )
  response.status_code = 204
