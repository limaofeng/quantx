import pytest
from pydantic import ValidationError
from quantx_contracts.market_health import MarketGatewayHealth


@pytest.fixture(params=["ready", "not_ready"])
def payload(request):
  ready = request.param == "ready"
  return {
    "component": "market-gateway",
    "protocol": "quantx.market.v2",
    "status": request.param,
    "reasonCode": None if ready else "MARKET_STREAM_OFFLINE",
    "connectedDevices": 1 if ready else 0,
    "sequence": 4 if ready else 0,
    "instrumentCount": 100 if ready else 0,
    "universeCount": 100 if ready else 0,
    "streamAgeSeconds": 0.1 if ready else None,
    "tradingSession": True if ready else None,
  }


def test_explicit_identity_round_trips(payload):
  health = MarketGatewayHealth.model_validate(payload)
  assert health.model_dump(mode="json", by_alias=True) == payload


@pytest.mark.parametrize(
  "missing", [("component",), ("protocol",), ("component", "protocol")]
)
def test_wire_identity_is_required(payload, missing):
  for field in missing:
    del payload[field]
  with pytest.raises(ValidationError) as exc:
    MarketGatewayHealth.model_validate(payload)
  assert {error["loc"][0] for error in exc.value.errors()} == set(missing)
  assert all(error["type"] == "missing" for error in exc.value.errors())


@pytest.mark.parametrize("field", ["component", "protocol"])
def test_wire_identity_rejects_wrong_values(payload, field):
  payload[field] = "another-service-or-protocol"
  with pytest.raises(ValidationError):
    MarketGatewayHealth.model_validate(payload)


def test_schema_requires_wire_identity():
  required = MarketGatewayHealth.model_json_schema()["required"]
  assert {"component", "protocol"} <= set(required)
