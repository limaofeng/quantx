import json
from difflib import unified_diff
from pathlib import Path

from quantx_api.client_contracts import (
  CLIENT_OPENAPI_PATHS,
  build_client_openapi,
  build_contract_files,
)
from quantx_api.gqlapi.schema import schema
from quantx_api.main import app

WORKSPACE_ROOT = Path(__file__).resolve().parents[3]
CONTRACT_DIRECTORY = WORKSPACE_ROOT / "apps" / "docs" / "public" / "contracts"


def test_client_contract_snapshots_are_current():
  generated = build_contract_files(app, schema)
  assert set(generated) == {
    "graphql-permissions.json",
    "graphql-schema.graphql",
    "openapi-client.json",
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
  manual_preview = schema_sdl.split("type ManualOrderPreview {", 1)[1].split(
    "}", 1
  )[0]
  assert "accountId: String!" in manual_input
  assert "requestedVolume: Int!" in manual_preview
  assert "finalVolume: Int!" in manual_preview
  assert "riskDecisionId: String!" in manual_preview
  assert "strawberry.types.field.UNRESOLVED" not in schema_sdl


def test_graphql_contract_uses_only_public_permission_categories():
  permissions = json.loads(
    (CONTRACT_DIRECTORY / "graphql-permissions.json").read_text("utf-8")
  )
  actual = {
    permission
    for operation in permissions.values()
    for permission in operation.values()
  }
  assert actual <= {
    "assistant:read",
    "assistant:write",
    "limit-up:control",
    "liquidation:control",
    "market:read",
    "mutation:write",
    "notification:manage",
    "orders:read",
    "portfolio:read",
    "strategy:read",
    "strategy:control",
    "system-status:read",
    "system-config:write",
    "trade:approve",
    "trade:direct",
    "trade:manual",
    "t-trade:control",
    "watchlist:write",
  }


def test_client_contract_contains_session_bound_push_fields():
  schema_sdl = (CONTRACT_DIRECTORY / "graphql-schema.graphql").read_text("utf-8")
  permissions = json.loads(
    (CONTRACT_DIRECTORY / "graphql-permissions.json").read_text("utf-8")
  )

  for field_name in (
    "registerPushDevice",
    "updatePushPreferences",
    "unregisterPushDevice",
  ):
    assert permissions["Mutation"][field_name] == "notification:manage"
  assert (
    permissions["Query"]["notificationEventRoute"] == "notification:manage"
  )
  assert "deviceToken: String!" in schema_sdl
  registration = schema_sdl.split("type PushDeviceRegistration {", 1)[1].split(
    "}", 1
  )[0]
  assert "deviceToken" not in registration
  assert "notificationEventRoute(eventId: ID!)" in schema_sdl


def test_client_contract_contains_narrow_mobile_control_permissions():
  schema_sdl = (CONTRACT_DIRECTORY / "graphql-schema.graphql").read_text("utf-8")
  permissions = json.loads(
    (CONTRACT_DIRECTORY / "graphql-permissions.json").read_text("utf-8")
  )

  assert permissions["Query"]["orderEntryCapabilities"] == "market:read"
  assert permissions["Mutation"]["previewLiquidation"] == "liquidation:control"
  assert permissions["Mutation"]["confirmLiquidation"] == "liquidation:control"
  assert (
    permissions["Mutation"]["previewExitPlanAuthorization"]
    == "liquidation:control"
  )
  assert (
    permissions["Mutation"]["confirmExitPlanAuthorization"]
    == "liquidation:control"
  )
  assert permissions["Mutation"]["previewStrategyControl"] == "trade:approve"
  assert permissions["Mutation"]["confirmStrategyControl"] == "trade:approve"
  assert permissions["Mutation"]["previewTTradeControl"] == "trade:approve"
  assert permissions["Mutation"]["confirmTTradeControl"] == "trade:approve"
  assert permissions["Mutation"]["pauseTTradeEntries"] == "t-trade:control"

  assert "enum TTradeControlAction" in schema_sdl
  for action in (
    "BEGIN_CONTROLLED_WINDOW",
    "ACTIVATE_CANARY",
    "ACTIVATE_LIVE",
    "KILL_SWITCH",
  ):
    assert action in schema_sdl
  assert "previewTTradeControl(input: TTradeControlPreviewInput!)" in schema_sdl
  assert (
    "confirmTTradeControl(input: TTradeControlConfirmationInput!)" in schema_sdl
  )


def test_client_openapi_contains_only_allowlisted_paths_and_models():
  document = build_client_openapi(app)
  assert set(document["paths"]) == CLIENT_OPENAPI_PATHS
  schemas = document["components"]["schemas"]
  assert set(schemas["NativeLoginRequest"]["required"]) >= {
    "username",
    "password",
    "requestedScopes",
  }
  for response_model in ("SessionGrantResponse", "SessionStateResponse"):
    assert set(schemas[response_model]["required"]) >= {
      "activeAccountId",
      "grantedScopes",
    }
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


def test_runtime_openapi_and_framework_docs_are_disabled_outside_development():
  assert app.docs_url is None
  assert app.redoc_url is None
  assert app.openapi_url is None
