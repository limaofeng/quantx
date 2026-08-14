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

logger = logging.getLogger(__name__)

_SLOW_RESOLVER_SECONDS = 1.0

_PORTFOLIO_FIELDS = {
  "account",
  "closedpositioncycles",
  "currentaccount",
  "dailyassetsnapshots",
  "dailyassetsnapshotspage",
  "portfoliooverview",
  "portfolio_summary",
  "portfoliosummary",
  "position",
  "positions",
  "watchlist",
}
_ORDER_FIELDS = {
  "conditionalliquidationorders",
  "exitplan",
  "exitplancapabilities",
  "exitplanevents",
  "exitplanholdingcapacity",
  "exitplans",
  "historyorders",
  "historytrades",
  "liquidationorder",
  "liquidationorders",
  "liquidationsummary",
  "order",
  "redemptionrecords",
  "todayorders",
  "todaytrades",
  "trade",
}
_STRATEGY_FIELDS = {
  "backtesthistory",
  "firstboardpromotiondesk",
  "firstboardpromotionupdates",
  "strategies",
  "strategy",
  "strategybucketledger",
  "strategydecisionhistory",
  "strategydefinitions",
  "strategyexecutionlogs",
  "strategyexecutiontrace",
  "strategyexitplans",
  "strategygridbook",
  "strategyinstance",
  "strategyinstancemobileparameters",
  "strategyinstances",
  "strategypendingtradeintents",
  "strategyperformance",
  "limitupboardassistant",
  "limitupboardassistantupdates",
  "strategyrun",
  "strategyruns",
  "ttradeglobalmonitor",
  "ttradebatches",
  "ttradebatchevents",
  "ttradebatcheventspage",
  "ttradebatchespage",
  "ttradeimportedentries",
  "ttradereplay",
  "ttradereplaycycles",
  "ttradereplayhistory",
  "ttradereplaypreparation",
  "ttradesession",
  "ttradesessions",
  "ttradesignalhistory",
  "ttradesignalhistorypage",
  "validatettradelivereadiness",
  "livesafetystatus",
  "operationalalerts",
}
_SYSTEM_FIELDS = {
  "schema",
  "type",
  "agentdevices",
  "airuntimesettings",
  "flowrun",
  "flowruns",
  "getdeploymentbyid",
  "getdeploymentbyname",
  "intradaywarmcachestatus",
  "listdeployments",
}
_SYSTEM_CONFIG_MUTATION_FIELDS = {"updateairuntimesettings"}
_MARKET_FIELDS = {
  "dividfactors",
  "financialoverview",
  "financialreports",
  "financialstatements",
  "financialsummary",
  "holidays",
  "instrument",
  "instruments",
  "instrumentsconnection",
  "intradayvolumescreen",
  "klines",
  "klinespage",
  "latestmarketquotes",
  "limituplifecycle",
  "marketindexintradaytrend",
  "limitupradar",
  "rootsectors",
  "researchrun",
  "researchruns",
  "sector",
  "sectors",
  "sectorstats",
  "stockdisclosuresummary",
  "stockscreen",
  "stockscreensnapshotstatus",
  "stocksignalssnapshotmeta",
  "stocksignalsnapshotmeta",
  "stocksectors",
  "ticks",
  "tradingcalendar",
}
_ASSISTANT_QUERY_FIELDS = {
  "aiassistantcapabilities",
  "aiassistantmessages",
  "aiassistantthread",
  "aiassistantthreads",
}
_ASSISTANT_MUTATION_FIELDS = {
  "cancelaiassistantrun",
  "createaiassistantthread",
  "deleteaiassistantthread",
  "resolveaiassistantapproval",
  "retryaiassistantrun",
  "sendaiassistantmessage",
  "updateaiassistantthread",
}
_ASSISTANT_SUBSCRIPTION_FIELDS = {"aiassistantevents"}
_ACCOUNT_KEY = re.compile(r"^account_?id$", re.IGNORECASE)
_TRADE_APPROVAL_MUTATION_FIELDS = {
  "confirmstrategytradeintentapproval",
  "confirmexitintent",
  "confirmttradeentryapproval",
  "previewstrategytradeintentapproval",
  "previewexitintent",
  "previewttradeentryapproval",
}
_MANUAL_TRADE_MUTATION_FIELDS = {
  "cancelorder",
  "confirmmanualorder",
  "previewmanualorder",
}
_DIRECT_TRADE_MUTATION_FIELDS = {"placeorder"}
_WATCHLIST_MUTATION_FIELDS = {
  "addwatchlistitem",
  "removewatchlistitem",
  "replacewatchlist",
  "reorderwatchlist",
}
_STRATEGY_CONTROL_MUTATION_FIELDS = {
  # These lifecycle operations do not create a broker order.  Mobile parameter
  # editing remains on the legacy permission until its allowlist/version
  # contract is enforced by the resolver.
  "pausestrategyinstance",
  "resumestrategyinstance",
  "rejectstrategytradeintent",
  "updatestrategyinstanceparameters",
}
_T_TRADE_CONTROL_MUTATION_FIELDS = {
  "acknowledgeoperationalalert",
  "cancelttradeorder",
  "reconcilettradeglobalmonitor",
  "rejectttradeentry",
  "resolveoperationalalert",
  "savettradeglobalmonitor",
  "startttradesession",
  "stopttradesession",
}
_LIMIT_UP_CONTROL_MUTATION_FIELDS = {
  "armlimitupboardcandidate",
  "disarmlimitupboardcandidate",
  "reconcilelimitupboardassistant",
  "savefirstboardassistant",
  "savelimitupboardassistant",
  "setfirstboardcandidatepreference",
}
_LEGACY_WEB_MUTATION_COMPAT_PERMISSIONS = frozenset(
  {
    "limit-up:control",
    "liquidation:control",
    "notification:manage",
    "strategy:control",
    "t-trade:control",
    "watchlist:write",
  }
)

_NORMALIZED_PORTFOLIO_FIELDS = {
  re.sub(r"[^a-z0-9]", "", value.lower()) for value in _PORTFOLIO_FIELDS
}


def normalize_field_name(value: str) -> str:
  return re.sub(r"[^a-z0-9]", "", value.lower())


def required_permission(operation_name: str, field_name: str) -> str:
  normalized = normalize_field_name(field_name)
  if operation_name == "Mutation":
    if normalized in _SYSTEM_CONFIG_MUTATION_FIELDS:
      return "system-config:write"
    if normalized in _ASSISTANT_MUTATION_FIELDS:
      return "assistant:write"
    if normalized in _TRADE_APPROVAL_MUTATION_FIELDS:
      return "trade:approve"
    if normalized in _MANUAL_TRADE_MUTATION_FIELDS:
      return "trade:manual"
    if normalized in _DIRECT_TRADE_MUTATION_FIELDS:
      return "trade:direct"
    if normalized in _WATCHLIST_MUTATION_FIELDS:
      return "watchlist:write"
    if normalized in _STRATEGY_CONTROL_MUTATION_FIELDS:
      return "strategy:control"
    if normalized in _T_TRADE_CONTROL_MUTATION_FIELDS:
      return "t-trade:control"
    if normalized in _LIMIT_UP_CONTROL_MUTATION_FIELDS:
      return "limit-up:control"
    return "mutation:write"
  if normalized in _NORMALIZED_PORTFOLIO_FIELDS:
    return "portfolio:read"
  if normalized in _ORDER_FIELDS:
    return "orders:read"
  if normalized in _STRATEGY_FIELDS:
    return "strategy:read"
  if normalized in _SYSTEM_FIELDS:
    return "system-status:read"
  if normalized in _MARKET_FIELDS:
    return "market:read"
  if normalized in _ASSISTANT_QUERY_FIELDS:
    return "assistant:read"
  if operation_name == "Subscription":
    if normalized in _ASSISTANT_SUBSCRIPTION_FIELDS:
      return "assistant:read"
    if normalized in {"tradingevents"}:
      return "orders:read"
    if normalized.startswith("strategy") or normalized == "ttradeupdates":
      return "strategy:read"
    if normalized.startswith("market"):
      return "market:read"
    return "system-status:read"
  return "query:unclassified"


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
        permission = required_permission(operation_name, info.field_name)
        legacy_web_write_compatible = (
          operation_name == "Mutation"
          and principal.active_account_id is None
          and permission in _LEGACY_WEB_MUTATION_COMPAT_PERMISSIONS
          and "mutation:write" in principal.permissions
        )
        if not legacy_web_write_compatible:
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
