from pathlib import Path


def test_gateway_startup_waits_for_liveness_before_api_and_agent():
  script = (Path(__file__).resolve().parents[2] / "ops" / "quantx.ps1").read_text(
    encoding="utf-8"
  )
  start = script.index('    Start-ManagedProcess `\n      -Name "market-gateway"')
  api = script.index('    Start-ManagedProcess `\n      -Name "api"', start)
  gateway_boot = script[start:api]
  assert '$MarketGatewayPort/health/live"' in gateway_boot
  assert '$MarketGatewayPort/health/ready"' not in gateway_boot
