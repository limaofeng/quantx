from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
OPS = ROOT / "ops" / "quantx.ps1"


def test_dev_live_start_registers_a_daily_non_elevated_backup_task():
  script = OPS.read_text(encoding="utf-8")
  function = script.split("function Register-DevBackupMaintenance", 1)[1].split(
    "function Install-CaddyRootCertificate",
    1,
  )[0]
  invoke_up = script.split("function Invoke-Up", 1)[1].split(
    "function Invoke-Down",
    1,
  )[0]

  assert 'TaskName "QuantX-Dev-Daily-Backup"' in function
  assert "-Environment dev" in function
  assert "New-ScheduledTaskTrigger -Daily" in function
  assert '-At "16:30"' in function
  assert "-StartWhenAvailable" in function
  assert "-MultipleInstances IgnoreNew" in function
  assert "-RunLevel Limited" in function
  assert "-RunLevel Highest" not in function
  assert '$Profile -eq "full"' in invoke_up
  assert '$agentMode -eq "live"' in invoke_up
  assert '$env:ENABLE_REAL_TRADING -eq "true"' in invoke_up
  assert '$env:QMT_REAL_TRADING_ENABLED -eq "true"' in invoke_up
  assert '$env:T_TRADE_LIVE_ENABLED -eq "true"' in invoke_up
  assert invoke_up.index("if (-not $liveRuntimeReady)") < invoke_up.index(
    "Register-DevBackupMaintenance"
  )


def test_backup_registration_failure_is_not_reported_as_success():
  script = OPS.read_text(encoding="utf-8")
  backup = script.split("function Invoke-Backup", 1)[1].split(
    "function Invoke-RestoreVerify",
    1,
  )[0]

  assert "successful backup was not recorded" in backup
  assert "Write-Warning \"Backup completed but database backup age" not in backup


def test_production_backup_schedule_matches_the_24_hour_gate():
  script = OPS.read_text(encoding="utf-8")
  function = script.split("function Register-ProductionMaintenance", 1)[1].split(
    "function Register-DevBackupMaintenance",
    1,
  )[0]

  assert "-Daily" in function
  assert "-DaysOfWeek" not in function
  assert "-MultipleInstances IgnoreNew" in function
