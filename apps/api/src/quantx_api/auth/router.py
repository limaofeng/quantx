"""FastAPI REST endpoints for native and browser sessions."""

import uuid
from typing import AsyncGenerator, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from quantx_infrastructure.config.settings import settings
from quantx_infrastructure.database.relational_connection import get_async_db
from sqlalchemy.ext.asyncio import AsyncSession

from .agent_service import AgentAuthService
from .errors import AuthError
from .principal import Principal
from .service import AuthService, SessionGrant
from .tokens import utcnow

_WEB_COOKIE_PATH = "/auth/web/session"


def _to_camel(value: str) -> str:
  first, *rest = value.split("_")
  return first + "".join(part.capitalize() for part in rest)


class APIModel(BaseModel):
  model_config = ConfigDict(
    alias_generator=_to_camel,
    populate_by_name=True,
    extra="forbid",
  )


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


class WebSessionGrantResponse(APIModel):
  access_token: str
  access_token_expires_at: str
  token_type: str = "Bearer"
  device_session_id: str
  user: SessionUserResponse


class SessionStateResponse(APIModel):
  device_session_id: str
  access_token_expires_at: str
  user: SessionUserResponse


class AgentEnrollmentRequest(APIModel):
  name: str = Field(default="QuantX QMT Agent", min_length=1, max_length=120)
  authorized_account_ids: list[str] = Field(default_factory=list)


class AgentEnrollmentResponse(APIModel):
  enrollment_code: str
  expires_at: str


class AgentEnrollmentExchangeRequest(APIModel):
  enrollment_code: SecretStr = Field(min_length=32, max_length=512)


class AgentCredentialResponse(APIModel):
  device_id: str
  device_secret: str


class AgentTokenRequest(APIModel):
  device_id: str = Field(min_length=1, max_length=64)
  device_secret: SecretStr = Field(min_length=32, max_length=512)


class AgentTokenResponse(APIModel):
  access_token: str
  access_token_expires_at: str
  token_type: str = "Bearer"
  device_id: str


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
  _require_native_session(grant.principal)
  return SessionGrantResponse(
    access_token=grant.access_token,
    refresh_token=grant.refresh_token,
    access_token_expires_at=grant.access_token_expires_at.isoformat() + "Z",
    refresh_token_expires_at=grant.refresh_token_expires_at.isoformat() + "Z",
    device_session_id=grant.principal.device_session_id,
    user=_user_response(grant.principal),
  )


def _require_native_session(principal: Principal) -> None:
  if not principal.is_native_session:
    raise AuthError(
      "SESSION_SCOPE_REQUIRED",
      "该令牌不属于原生设备会话",
      status_code=401,
    )


def _web_grant_response(grant: SessionGrant) -> WebSessionGrantResponse:
  return WebSessionGrantResponse(
    access_token=grant.access_token,
    access_token_expires_at=grant.access_token_expires_at.isoformat() + "Z",
    device_session_id=grant.principal.device_session_id,
    user=_user_response(grant.principal),
  )


def _disable_session_caching(response: Response) -> None:
  response.headers["Cache-Control"] = "no-store"
  response.headers["Pragma"] = "no-cache"


def _web_cookie_secure() -> bool:
  configured = settings.auth_web_cookie_secure
  secure = settings.is_production if configured is None else configured
  if settings.is_production and not secure:
    raise AuthError(
      "AUTH_NOT_CONFIGURED",
      "生产环境必须启用安全的 Web 会话 Cookie",
      status_code=503,
    )
  return secure


def _is_same_origin_development_request(request: Request, origin: str) -> bool:
  if not settings.is_development:
    return False

  forwarded_proto = request.headers.get("x-forwarded-proto", "")
  scheme = (forwarded_proto.split(",", 1)[0].strip() or request.url.scheme).lower()
  host = request.headers.get("host", "").strip().lower()
  if scheme not in {"http", "https"} or not host:
    return False
  return origin.lower() == f"{scheme}://{host}"


def _require_web_origin(request: Request) -> None:
  origin = request.headers.get("origin", "").strip().rstrip("/")
  configured_origins = settings.auth_web_allowed_origins or settings.cors_origins
  allowed_origins = {
    str(value).strip().rstrip("/") for value in configured_origins if str(value).strip()
  }
  if origin and (
    origin in allowed_origins or _is_same_origin_development_request(request, origin)
  ):
    return
  raise _http_error(
    AuthError(
      "FORBIDDEN_ORIGIN",
      "当前页面来源无权创建或刷新 Web 会话",
      status_code=403,
    ),
    _request_id(request),
  )


def _set_web_refresh_cookie(response: Response, grant: SessionGrant) -> None:
  max_age = max(
    1,
    int((grant.refresh_token_expires_at - utcnow()).total_seconds()),
  )
  response.set_cookie(
    key=settings.auth_web_refresh_cookie_name,
    value=grant.refresh_token,
    max_age=max_age,
    path=_WEB_COOKIE_PATH,
    secure=_web_cookie_secure(),
    httponly=True,
    samesite="strict",
  )


def _clear_web_refresh_cookie(response: Response) -> None:
  response.delete_cookie(
    key=settings.auth_web_refresh_cookie_name,
    path=_WEB_COOKIE_PATH,
    secure=_web_cookie_secure(),
    httponly=True,
    samesite="strict",
  )


def _web_refresh_token(request: Request) -> str:
  token = request.cookies.get(settings.auth_web_refresh_cookie_name, "").strip()
  if len(token) < 32:
    raise AuthError("UNAUTHENTICATED", "Web 会话不存在或已过期", status_code=401)
  return token


def _web_error_response(error: AuthError, request_id: str) -> JSONResponse:
  response = JSONResponse(
    status_code=error.status_code,
    content={
      "detail": {
        "code": error.code,
        "message": error.message,
        "requestId": request_id,
        "retryable": error.retryable,
      }
    },
  )
  _disable_session_caching(response)
  _clear_web_refresh_cookie(response)
  return response


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


@auth_router.post("/web/session", response_model=WebSessionGrantResponse)
async def create_web_session(
  payload: LoginRequest,
  request: Request,
  response: Response,
  db: AsyncSession = Depends(_database),
) -> WebSessionGrantResponse:
  """Create a browser session without exposing its refresh token to JavaScript."""
  _require_web_origin(request)
  _web_cookie_secure()
  _disable_session_caching(response)
  try:
    grant = await AuthService(db).login(
      payload.username,
      payload.password.get_secret_value(),
      device_name=payload.device_name or "QuantX Web",
      client_fingerprint=_client_fingerprint(request),
      request_id=_request_id(request),
      native_session=False,
    )
    _set_web_refresh_cookie(response, grant)
    return _web_grant_response(grant)
  except AuthError as exc:
    raise _http_error(exc, _request_id(request)) from None


@auth_router.post(
  "/web/session/development",
  response_model=WebSessionGrantResponse,
  responses={
    404: {"description": "Development auto-login is disabled"},
    503: {"description": "Development database user is not configured"},
  },
)
async def create_development_web_session(
  request: Request,
  response: Response,
  db: AsyncSession = Depends(_database),
) -> WebSessionGrantResponse:
  """Create a passwordless browser session for a development-only DB user."""
  if not settings.is_development or not settings.auth_development_auto_login:
    raise _http_error(
      AuthError(
        "DEVELOPMENT_LOGIN_DISABLED",
        "开发自动登录未启用",
        status_code=404,
      ),
      _request_id(request),
    )
  _require_web_origin(request)
  _web_cookie_secure()
  _disable_session_caching(response)
  try:
    grant = await AuthService(db).development_login(
      device_name="QuantX Web Development",
      client_fingerprint=_client_fingerprint(request),
      request_id=_request_id(request),
    )
    _set_web_refresh_cookie(response, grant)
    return _web_grant_response(grant)
  except AuthError as exc:
    raise _http_error(exc, _request_id(request)) from None


@auth_router.post(
  "/web/session/refresh",
  response_model=WebSessionGrantResponse,
  responses={401: {"description": "Web session is missing or expired"}},
)
async def refresh_web_session(
  request: Request,
  response: Response,
  db: AsyncSession = Depends(_database),
):
  """Rotate the HttpOnly refresh cookie and return a short-lived access token."""
  _require_web_origin(request)
  _web_cookie_secure()
  _disable_session_caching(response)
  try:
    grant = await AuthService(db).refresh(
      _web_refresh_token(request), _request_id(request)
    )
    _set_web_refresh_cookie(response, grant)
    return _web_grant_response(grant)
  except AuthError as exc:
    return _web_error_response(exc, _request_id(request))


@auth_router.delete("/web/session", status_code=204)
async def delete_web_session(
  request: Request,
  response: Response,
  db: AsyncSession = Depends(_database),
) -> None:
  """Idempotently revoke the current browser session and clear its cookie."""
  _require_web_origin(request)
  _web_cookie_secure()
  try:
    token = _web_refresh_token(request)
  except AuthError:
    token = ""
  if token:
    await AuthService(db).logout_by_refresh_token(
      token,
      request_id=_request_id(request),
    )
  _clear_web_refresh_cookie(response)
  _disable_session_caching(response)
  response.status_code = 204


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
      payload.refresh_token.get_secret_value(),
      _request_id(request),
      require_scoped_session=True,
    )
    return _grant_response(grant)
  except AuthError as exc:
    raise _http_error(exc, _request_id(request)) from None


@auth_router.get("/session", response_model=SessionStateResponse)
async def get_session(
  request: Request,
  response: Response,
  principal: Principal = Depends(_principal),
) -> SessionStateResponse:
  _disable_session_caching(response)
  try:
    _require_native_session(principal)
    return SessionStateResponse(
      device_session_id=principal.device_session_id,
      access_token_expires_at=principal.access_token_expires_at.isoformat() + "Z",
      user=_user_response(principal),
    )
  except AuthError as exc:
    raise _http_error(exc, _request_id(request)) from None


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


@auth_router.post(
  "/agent/enrollments",
  response_model=AgentEnrollmentResponse,
)
async def create_agent_enrollment(
  payload: AgentEnrollmentRequest,
  principal: Principal = Depends(_principal),
  db: AsyncSession = Depends(_database),
) -> AgentEnrollmentResponse:
  principal.require_permission("agent:manage")
  account_ids = [
    principal.require_account(account_id)
    for account_id in payload.authorized_account_ids
  ]
  if not account_ids:
    account_ids = list(principal.authorized_account_ids)
  enrollment = await AgentAuthService(db).create_enrollment(
    user_id=principal.user_id,
    name=payload.name,
    authorized_account_ids=account_ids,
  )
  return AgentEnrollmentResponse(
    enrollment_code=enrollment.code,
    expires_at=enrollment.expires_at.isoformat() + "Z",
  )


@auth_router.post(
  "/agent/enrollments/exchange",
  response_model=AgentCredentialResponse,
)
async def exchange_agent_enrollment(
  payload: AgentEnrollmentExchangeRequest,
  db: AsyncSession = Depends(_database),
) -> AgentCredentialResponse:
  try:
    credential = await AgentAuthService(db).exchange_enrollment(
      payload.enrollment_code.get_secret_value()
    )
    return AgentCredentialResponse(
      device_id=credential.device_id,
      device_secret=credential.device_secret,
    )
  except AuthError as exc:
    raise _http_error(exc) from None


@auth_router.post("/agent/token", response_model=AgentTokenResponse)
async def create_agent_token(
  payload: AgentTokenRequest,
  db: AsyncSession = Depends(_database),
) -> AgentTokenResponse:
  try:
    grant = await AgentAuthService(db).issue_agent_token(
      device_id=payload.device_id,
      device_secret=payload.device_secret.get_secret_value(),
    )
    return AgentTokenResponse(
      access_token=grant.access_token,
      access_token_expires_at=grant.expires_at.isoformat() + "Z",
      device_id=grant.device.id,
    )
  except AuthError as exc:
    raise _http_error(exc) from None


@auth_router.delete("/agent/devices/{device_id}", status_code=204)
async def revoke_agent_device(
  device_id: str,
  principal: Principal = Depends(_principal),
  db: AsyncSession = Depends(_database),
) -> None:
  principal.require_permission("agent:manage")
  revoked = await AgentAuthService(db).revoke(
    device_id=device_id,
    user_id=principal.user_id,
  )
  if not revoked:
    raise HTTPException(status_code=404, detail="Agent device not found")
