import pytest
from quantx_infrastructure.database.relational_connection import (
  _serialize_json,
  database_pool_profile,
)


def test_database_json_serializer_handles_report_payload_types() -> None:
  assert _serialize_json({"code": "000001.SZ", "levels": {1: 10.5}}) == (
    '{"code":"000001.SZ","levels":{"1":10.5}}'
  )


def test_database_json_serializer_rejects_unsupported_objects() -> None:
  with pytest.raises(TypeError):
    _serialize_json({"invalid": object()})


def test_relational_pool_profiles_keep_single_account_runtime_bounded() -> None:
  api = database_pool_profile("api")
  market_gateway = database_pool_profile("market-gateway")
  engine = database_pool_profile("engine")
  worker = database_pool_profile("worker")
  ai_runtime = database_pool_profile("ai-runtime")

  assert (api.pool_size, api.max_overflow, api.maximum_connections) == (8, 4, 12)
  assert (
    market_gateway.pool_size,
    market_gateway.max_overflow,
    market_gateway.maximum_connections,
  ) == (1, 1, 2)
  assert (engine.pool_size, engine.max_overflow) == (6, 2)
  assert (worker.pool_size, worker.max_overflow) == (4, 2)
  assert (ai_runtime.pool_size, ai_runtime.max_overflow) == (2, 1)
  assert sum(
    profile.maximum_connections
    for profile in (api, market_gateway, engine, worker, ai_runtime)
  ) == 31


def test_relational_pool_profile_supports_explicit_process_override() -> None:
  profile = database_pool_profile("api", pool_size=3, max_overflow=1)

  assert profile.role == "api"
  assert profile.maximum_connections == 4


def test_unknown_process_role_uses_bounded_tooling_profile() -> None:
  profile = database_pool_profile("unexpected")

  assert profile.role == "tooling"
  assert profile.maximum_connections == 3
