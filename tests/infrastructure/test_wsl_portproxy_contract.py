from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "ops" / "windows" / "sync-wsl-portproxy.ps1"


def test_wsl_portproxy_targets_only_managed_external_dependencies() -> None:
  script = SCRIPT.read_text(encoding="utf-8")

  assert '$ListenAddress = "0.0.0.0"' in script
  assert "$ManagedPorts = @(30081, 30420, 30179, 32432)" in script
  assert "59337" not in script
  assert '"show", "dev", "eth0"' in script
  assert "interface portproxy $verb v4tov4" in script
  assert "interface portproxy reset" not in script


def test_wsl_portproxy_refresh_is_idempotent_and_persistent() -> None:
  script = SCRIPT.read_text(encoding="utf-8")

  assert "if ($currentTarget -eq $desiredTarget)" in script
  assert '$verb = if ($null -eq $currentTarget) { "add" } else { "set" }' in script
  assert '$TaskName = "QuantX-WSL-PortProxy"' in script
  assert "New-ScheduledTaskTrigger -AtLogOn" in script
  assert "-RepetitionInterval (New-TimeSpan -Minutes $RefreshMinutes)" in script
  assert "-RunLevel Highest" in script
