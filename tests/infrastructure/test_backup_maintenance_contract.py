from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OPS = ROOT / "ops" / "quantx.ps1"


def test_dev_live_start_registers_a_daily_non_elevated_backup_task():
  script = OPS.read_text(encoding="utf-8")
  function = script.split("function Register-DevBackupMaintenance", 1)[1].split(
    "function Invoke-Doctor",
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
  assert '$env:T_TRADE_LIVE_ENABLED -eq "true"' not in invoke_up
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
  assert 'Write-Warning "Backup completed but database backup age' not in backup


def test_backup_and_restore_verify_include_monitor_history_when_present():
  script = OPS.read_text(encoding="utf-8")
  backup = script.split("function Invoke-Backup", 1)[1].split(
    "function Test-RestoreVerificationScratchDatabaseName",
    1,
  )[0]
  restore = script.split("function Invoke-RestoreVerify", 1)[1].split(
    "function Invoke-MigrateAtRoot",
    1,
  )[0]

  assert "quantx_monitor.main backup" in backup
  assert '"monitor\\quantx-monitor.sqlite3"' in backup
  assert "QuantX Monitor history backup failed" in backup
  assert 'Join-Path $source "monitor\\quantx-monitor.sqlite3"' in restore
  assert "PRAGMA integrity_check" in restore
  assert "QuantX Monitor history integrity validation failed" in restore


def test_production_backup_is_owned_by_the_kubernetes_platform():
  script = OPS.read_text(encoding="utf-8")
  runbook = (
    ROOT / "docs" / "engineering" / "deployment" / "PRODUCTION_RUNBOOK.md"
  ).read_text(encoding="utf-8")

  assert "Register-ProductionMaintenance" not in script
  assert "数据库平台创建可恢复备份" in runbook
  assert "Monitor PVC 已纳入 CSI 快照或文件级备份" in runbook
