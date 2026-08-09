import json
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
    assert (CONTRACT_DIRECTORY / name).read_bytes() == content
  schema_sdl = generated["graphql-schema.graphql"].decode("utf-8")
  assert "authorizedAccountIds: [String!]! = []" in schema_sdl
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
    "market:read",
    "mutation:write",
    "orders:read",
    "portfolio:read",
    "strategy:read",
    "system-status:read",
  }


def test_client_openapi_contains_only_allowlisted_paths_and_models():
  document = build_client_openapi(app)
  assert set(document["paths"]) == CLIENT_OPENAPI_PATHS
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
