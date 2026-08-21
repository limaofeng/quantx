import json
from difflib import unified_diff
from pathlib import Path

from quantx_api.client_contracts import (
  CLIENT_OPENAPI_PATHS,
  WEB_OPENAPI_PATHS,
  build_client_openapi,
  build_contract_files,
  build_web_openapi,
)
from quantx_api.gqlapi.operation_policy import (
  normalize_field_name,
  operation_policy,
  operation_policy_keys,
)
from quantx_api.gqlapi.schema import schema
from quantx_api.main import app

WORKSPACE_ROOT = Path(__file__).resolve().parents[3]
CONTRACT_DIRECTORY = WORKSPACE_ROOT / "apps" / "docs" / "public" / "contracts"


def test_client_contract_snapshots_are_current():
  generated = build_contract_files(app, schema)
  assert set(generated) == {
    "graphql-permissions.json",
    "graphql-operation-policies.v2.json",
    "graphql-schema.graphql",
    "openapi-client.json",
    "openapi-web.json",
  }
  for name, content in generated.items():
    actual = (CONTRACT_DIRECTORY / name).read_bytes()
    assert actual == content, "".join(
      unified_diff(
        actual.decode("utf-8").splitlines(keepends=True),
        content.decode("utf-8").splitlines(keepends=True),
        fromfile=f"checked-in/{name}",
        tofile=f"generated/{name}",
      )
    )
  schema_sdl = generated["graphql-schema.graphql"].decode("utf-8")
  assert "authorizedAccountIds: [String!]! = []" in schema_sdl
  manual_input = schema_sdl.split("input ManualOrderPreviewInput {", 1)[1].split(
    "}", 1
  )[0]
  manual_preview = schema_sdl.split("type ManualOrderPreview {", 1)[1].split("}", 1)[0]
  assert "accountId: String!" in manual_input
  assert "requestedVolume: Int!" in manual_preview
  assert "finalVolume: Int!" in manual_preview
  assert "riskDecisionId: String!" in manual_preview
  assert "strawberry.types.field.UNRESOLVED" not in schema_sdl


def test_graphql_contract_uses_only_explicit_public_permission_categories():
  contract = json.loads(
    (CONTRACT_DIRECTORY / "graphql-operation-policies.v2.json").read_text(
      "utf-8"
    )
  )
  assert contract["schemaVersion"] == 2
  actual = {
    permission
    for operation in contract["operations"].values()
    for policy in operation.values()
    for permission in policy["requiredPermissions"]
  }
  assert actual <= {
    "agent:manage",
    "assistant:read",
    "assistant:write",
    "limit-up:control",
    "liquidation:control",
    "market:read",
    "notification:manage",
    "market:write",
    "operations:write",
    "orders:read",
    "orders:write",
    "portfolio:read",
    "portfolio:write",
    "strategy:read",
    "strategy:control",
    "strategy:write",
    "system-status:read",
    "system-config:write",
    "trade:approve",
    "trade:direct",
    "trade:manual",
    "t-trade:control",
    "watchlist:write",
  }
  assert "mutation:write" not in actual
  for operation in contract["operations"].values():
    for policy in operation.values():
      assert policy["audiences"]
      assert policy["requiredPermissions"]
      assert policy["risk"] in {"READ", "NON_TRADING_WRITE", "TRADING_WRITE", "ADMIN"}
      assert policy["stability"] in {"supported", "experimental", "web-internal"}


def test_every_graphql_root_field_has_policy_and_description():
  policy_keys = operation_policy_keys()
  schema_keys: set[tuple[str, str]] = set()
  for operation_name in ("Query", "Mutation", "Subscription"):
    root_type = schema._schema.get_type(operation_name)
    assert root_type is not None
    for field_name, field in root_type.fields.items():
      schema_keys.add((operation_name, normalize_field_name(field_name)))
      assert field.description, f"missing description: {operation_name}.{field_name}"
  assert schema_keys == policy_keys


def test_t_trade_replay_mutations_are_non_trading_writes():
  for field_name in ("startTTradeReplay", "cancelTTradeReplay"):
    policy = operation_policy("Mutation", field_name)
    assert policy.required_permissions == ("strategy:write",)
    assert policy.risk == "NON_TRADING_WRITE"


def test_client_contract_contains_session_bound_push_fields():
  schema_sdl = (CONTRACT_DIRECTORY / "graphql-schema.graphql").read_text("utf-8")
  policies = json.loads(
    (CONTRACT_DIRECTORY / "graphql-operation-policies.v2.json").read_text("utf-8")
  )["operations"]

  for field_name in (
    "registerPushDevice",
    "updatePushPreferences",
    "unregisterPushDevice",
  ):
    assert policies["Mutation"][field_name]["requiredPermissions"] == [
      "notification:manage"
    ]
  assert policies["Query"]["notificationEventRoute"]["requiredPermissions"] == [
    "notification:manage"
  ]
  assert "deviceToken: String!" in schema_sdl
  registration = schema_sdl.split("type PushDeviceRegistration {", 1)[1].split("}", 1)[
    0
  ]
  assert "deviceToken" not in registration
  assert "notificationEventRoute(eventId: ID!)" in schema_sdl


def test_client_contract_contains_narrow_mobile_control_permissions():
  schema_sdl = (CONTRACT_DIRECTORY / "graphql-schema.graphql").read_text("utf-8")
  policies = json.loads(
    (CONTRACT_DIRECTORY / "graphql-operation-policies.v2.json").read_text("utf-8")
  )["operations"]

  assert policies["Query"]["orderEntryCapabilities"]["requiredPermissions"] == [
    "market:read"
  ]
  assert policies["Mutation"]["previewLiquidation"]["requiredPermissions"] == [
    "liquidation:control"
  ]
  assert policies["Mutation"]["confirmLiquidation"]["requiredPermissions"] == [
    "liquidation:control",
    "trade:approve",
  ]
  assert policies["Mutation"]["previewExitPlanAuthorization"][
    "requiredPermissions"
  ] == ["liquidation:control"]
  assert policies["Mutation"]["confirmExitPlanAuthorization"][
    "requiredPermissions"
  ] == ["liquidation:control", "trade:approve"]
  for field_name, control_permission in (
    ("previewStrategyControl", "strategy:control"),
    ("confirmStrategyControl", "strategy:control"),
    ("previewTTradeControl", "t-trade:control"),
    ("confirmTTradeControl", "t-trade:control"),
  ):
    assert policies["Mutation"][field_name]["requiredPermissions"] == [
      control_permission,
      "trade:approve",
    ]
  assert policies["Mutation"]["pauseTTradeEntries"]["requiredPermissions"] == [
    "t-trade:control"
  ]

  assert "enum TTradeControlAction" in schema_sdl
  for action in (
    "BEGIN_CONTROLLED_WINDOW",
    "ACTIVATE_CANARY",
    "ACTIVATE_LIVE",
    "KILL_SWITCH",
  ):
    assert action in schema_sdl
  assert "previewTTradeControl(input: TTradeControlPreviewInput!)" in schema_sdl
  assert "confirmTTradeControl(input: TTradeControlConfirmationInput!)" in schema_sdl


def test_client_openapi_contains_only_allowlisted_paths_and_models():
  document = build_client_openapi(app)
  assert set(document["paths"]) == CLIENT_OPENAPI_PATHS
  schemas = document["components"]["schemas"]
  assert set(schemas["LoginRequest"]["required"]) == {"username", "password"}
  assert set(schemas["LoginRequest"]["properties"]) == {
    "username",
    "password",
    "deviceName",
  }
  for response_model in ("SessionGrantResponse", "SessionStateResponse"):
    assert "activeAccountId" not in schemas[response_model]["properties"]
    assert "grantedScopes" not in schemas[response_model]["properties"]
  assert (
    document["paths"]["/auth/session"]["post"]["responses"]["200"]["content"][
      "application/json"
    ]["schema"]["$ref"]
    == "#/components/schemas/SessionGrantResponse"
  )
  assert (
    document["paths"]["/auth/session/refresh"]["post"]["responses"]["200"]["content"][
      "application/json"
    ]["schema"]["$ref"]
    == "#/components/schemas/SessionGrantResponse"
  )
  assert (
    document["paths"]["/auth/session"]["get"]["responses"]["200"]["content"][
      "application/json"
    ]["schema"]["$ref"]
    == "#/components/schemas/SessionStateResponse"
  )
  serialized = json.dumps(document, ensure_ascii=False).lower()
  for forbidden in (
    "/auth/web/",
    "/auth/agent/",
    "/agent/",
    "/metrics",
    "/_dev/",
    "agentcredential",
    "devicesecret",
  ):
    assert forbidden not in serialized


def test_web_openapi_contains_all_browser_routes_but_no_agent_credentials():
  document = build_web_openapi(app)
  assert set(document["paths"]) == WEB_OPENAPI_PATHS
  serialized = json.dumps(document, ensure_ascii=False).lower()
  assert "/auth/web/session/development" in serialized
  development_login = document["paths"]["/auth/web/session/development"]["post"]
  assert development_login["x-quantx-stability"] == "development-only"
  assert "/auth/agent/" not in serialized
  assert "devicesecret" not in serialized


def test_runtime_openapi_and_framework_docs_are_disabled_outside_development():
  assert app.docs_url is None
  assert app.redoc_url is None
  assert app.openapi_url is None
