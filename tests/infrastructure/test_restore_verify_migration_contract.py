import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = ROOT / "ops" / "quantx.ps1"


def _powershell() -> str:
  powershell = shutil.which("pwsh") or shutil.which("powershell")
  assert powershell is not None
  return powershell


def _function_source(start: str, end: str) -> str:
  script = SCRIPT_PATH.read_text(encoding="utf-8")
  return f"function {start}" + script.split(f"function {start}", 1)[1].split(
    f"function {end}",
    1,
  )[0]


def _run_powershell(command: str) -> subprocess.CompletedProcess[str]:
  return subprocess.run(
    [_powershell(), "-NoProfile", "-Command", command],
    capture_output=True,
    text=True,
    encoding="utf-8",
    timeout=30,
    check=False,
  )


@pytest.mark.skipif(os.name != "nt", reason="PowerShell restore verification gate")
@pytest.mark.parametrize(
  ("relation", "expected_commands", "expected_error"),
  (
    (
      "current",
      [
        "-m quantx_infrastructure.database.schema_control status",
        "-m quantx_infrastructure.database.schema_control check",
      ],
      "",
    ),
    (
      "behind",
      [
        "-m quantx_infrastructure.database.schema_control status",
        "-m alembic -c C:\\release\\alembic.ini upgrade head",
        "-m quantx_infrastructure.database.schema_control check",
      ],
      "",
    ),
    (
      "incompatible",
      ["-m quantx_infrastructure.database.schema_control status"],
      "only current or behind revisions are allowed",
    ),
    (
      "unversioned",
      ["-m quantx_infrastructure.database.schema_control status"],
      "only current or behind revisions are allowed",
    ),
    (
      "ahead",
      ["-m quantx_infrastructure.database.schema_control status"],
      "only current or behind revisions are allowed",
    ),
    (
      "unknown",
      ["-m quantx_infrastructure.database.schema_control status"],
      "only current or behind revisions are allowed",
    ),
  ),
)
def test_restore_verify_schema_gate_only_allows_current_or_behind(
  relation: str,
  expected_commands: list[str],
  expected_error: str,
) -> None:
  schema_gate = _function_source(
    "Get-RestoreVerificationSchemaStatus",
    "Remove-RestoreVerificationScratchDatabase",
  )
  status_json = json.dumps({"revision_relation": relation}).replace("'", "''")
  command = f"""
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$script:commands = [Collections.Generic.List[string]]::new()
function Test-Python {{
  param([Parameter(ValueFromRemainingArguments = $true)][object[]]$Remaining)
  $rendered = @($Remaining | ForEach-Object {{ [string]$_ }}) -join " "
  $script:commands.Add($rendered)
  if ($rendered -eq "-m quantx_infrastructure.database.schema_control status") {{
    '{status_json}'
    $global:LASTEXITCODE = 0
    return
  }}
  if ($rendered -like "-m alembic *") {{
    $global:LASTEXITCODE = 0
    return
  }}
  if ($rendered -eq "-m quantx_infrastructure.database.schema_control check") {{
    $global:LASTEXITCODE = 0
    return
  }}
  throw "Unexpected test Python invocation: $rendered"
}}
{schema_gate}
$caughtError = ""
$previousInformationPreference = $InformationPreference
try {{
  $InformationPreference = "SilentlyContinue"
  Invoke-RestoreVerificationSchemaGate `
    -Python "Test-Python" `
    -ApplicationRoot "C:\\release" 6>$null
}} catch {{
  $caughtError = $_.Exception.Message
}} finally {{
  $InformationPreference = $previousInformationPreference
}}
[ordered]@{{
  commands = @($script:commands)
  error = $caughtError
}} | ConvertTo-Json -Compress
"""

  result = _run_powershell(command)

  assert result.returncode == 0, result.stderr
  payload = json.loads(result.stdout.strip())
  assert payload["commands"] == expected_commands
  if expected_error:
    assert expected_error in payload["error"]
  else:
    assert payload["error"] == ""


@pytest.mark.skipif(os.name != "nt", reason="PowerShell restore verification gate")
def test_restore_verify_schema_gate_fails_when_isolated_upgrade_or_check_fails() -> None:
  schema_gate = _function_source(
    "Get-RestoreVerificationSchemaStatus",
    "Remove-RestoreVerificationScratchDatabase",
  )
  command = f"""
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
{schema_gate}
function Invoke-Scenario {{
  param(
    [Parameter(Mandatory = $true)][int]$UpgradeExitCode,
    [Parameter(Mandatory = $true)][int]$CheckExitCode
  )
  $script:commands = [Collections.Generic.List[string]]::new()
  function Test-Python {{
    param([Parameter(ValueFromRemainingArguments = $true)][object[]]$Remaining)
    $rendered = @($Remaining | ForEach-Object {{ [string]$_ }}) -join " "
    $script:commands.Add($rendered)
    if ($rendered -eq "-m quantx_infrastructure.database.schema_control status") {{
      '{{"revision_relation":"behind"}}'
      $global:LASTEXITCODE = 0
      return
    }}
    if ($rendered -like "-m alembic *") {{
      $global:LASTEXITCODE = $UpgradeExitCode
      return
    }}
    if ($rendered -eq "-m quantx_infrastructure.database.schema_control check") {{
      $global:LASTEXITCODE = $CheckExitCode
      return
    }}
    throw "Unexpected test Python invocation: $rendered"
  }}
  $caughtError = ""
  $previousInformationPreference = $InformationPreference
  try {{
    $InformationPreference = "SilentlyContinue"
    Invoke-RestoreVerificationSchemaGate `
      -Python "Test-Python" `
      -ApplicationRoot "C:\\release" 6>$null
  }} catch {{
    $caughtError = $_.Exception.Message
  }} finally {{
    $InformationPreference = $previousInformationPreference
  }}
  return [ordered]@{{
    commands = @($script:commands)
    error = $caughtError
  }}
}}
[ordered]@{{
  upgradeFailure = Invoke-Scenario -UpgradeExitCode 17 -CheckExitCode 0
  checkFailure = Invoke-Scenario -UpgradeExitCode 0 -CheckExitCode 23
}} | ConvertTo-Json -Depth 6 -Compress
"""

  result = _run_powershell(command)

  assert result.returncode == 0, result.stderr
  payload = json.loads(result.stdout.strip())
  assert payload["upgradeFailure"]["commands"] == [
    "-m quantx_infrastructure.database.schema_control status",
    "-m alembic -c C:\\release\\alembic.ini upgrade head",
  ]
  assert "Isolated restored database Alembic upgrade failed" in payload[
    "upgradeFailure"
  ]["error"]
  assert payload["checkFailure"]["commands"] == [
    "-m quantx_infrastructure.database.schema_control status",
    "-m alembic -c C:\\release\\alembic.ini upgrade head",
    "-m quantx_infrastructure.database.schema_control check",
  ]
  assert "Restored database does not match" in payload["checkFailure"]["error"]


@pytest.mark.skipif(os.name != "nt", reason="PowerShell restore verification cleanup")
def test_restore_verify_upgrade_failure_still_drops_only_the_created_scratch_database(
  tmp_path: Path,
) -> None:
  dump = tmp_path / "postgres.dump"
  dump.write_bytes(b"restore verification test archive")
  manifest = {
    "files": [
      {
        "relativePath": dump.name,
        "length": dump.stat().st_size,
        "sha256": hashlib.sha256(dump.read_bytes()).hexdigest(),
      }
    ]
  }
  (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

  restore_functions = _function_source(
    "Test-RestoreVerificationScratchDatabaseName",
    "Invoke-MigrateAtRoot",
  )
  backup_path = str(tmp_path).replace("'", "''")
  root_path = str(ROOT).replace("'", "''")
  command = f"""
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$BackupPath = '{backup_path}'
$Environment = "dev"
$Root = '{root_path}'
$CurrentReleaseLink = ""
{restore_functions}
$script:createCalls = [Collections.Generic.List[string]]::new()
$script:dropCalls = [Collections.Generic.List[string]]::new()
$script:pythonCalls = [Collections.Generic.List[string]]::new()
function Import-QuantXEnvironment {{}}
function Resolve-PostgreSqlTool {{
  param([Parameter(Mandatory = $true)][string]$Name)
  switch ($Name) {{
    "pg_restore" {{ return "Test-PgRestore" }}
    "createdb" {{ return "Test-CreateDatabase" }}
    "dropdb" {{ return "Test-DropDatabase" }}
    default {{ throw "Unexpected PostgreSQL tool: $Name" }}
  }}
}}
function Get-PostgreSqlConnectionParts {{
  return [pscustomobject]@{{
    Host = "127.0.0.1"
    Port = 5432
    User = "quantx"
    Password = "test-password"
  }}
}}
function Resolve-Python {{
  param([switch]$Qmt)
  return "Test-Python"
}}
function Get-WorkspacePythonPath {{ return "" }}
function Get-QmtAgentPythonPath {{ return "" }}
function Test-CreateDatabase {{
  param([Parameter(ValueFromRemainingArguments = $true)][object[]]$Remaining)
  $script:createCalls.Add((@($Remaining | ForEach-Object {{ [string]$_ }}) -join " "))
  $global:LASTEXITCODE = 0
}}
function Test-PgRestore {{
  param([Parameter(ValueFromRemainingArguments = $true)][object[]]$Remaining)
  $global:LASTEXITCODE = 0
}}
function Test-DropDatabase {{
  param([Parameter(ValueFromRemainingArguments = $true)][object[]]$Remaining)
  $script:dropCalls.Add((@($Remaining | ForEach-Object {{ [string]$_ }}) -join " "))
  $global:LASTEXITCODE = 0
}}
function Test-Python {{
  param([Parameter(ValueFromRemainingArguments = $true)][object[]]$Remaining)
  $rendered = @($Remaining | ForEach-Object {{ [string]$_ }}) -join " "
  $script:pythonCalls.Add($rendered)
  if ($rendered -eq "-m quantx_infrastructure.database.schema_control status") {{
    '{{"revision_relation":"behind"}}'
    $global:LASTEXITCODE = 0
    return
  }}
  if ($rendered -like "-m alembic *") {{
    $global:LASTEXITCODE = 31
    return
  }}
  throw "Unexpected test Python invocation: $rendered"
}}
$caughtError = ""
$previousInformationPreference = $InformationPreference
try {{
  $InformationPreference = "SilentlyContinue"
  Invoke-RestoreVerify 6>$null
}} catch {{
  $caughtError = $_.Exception.Message
}} finally {{
  $InformationPreference = $previousInformationPreference
}}
[ordered]@{{
  error = $caughtError
  creates = @($script:createCalls)
  drops = @($script:dropCalls)
  pythonCalls = @($script:pythonCalls)
}} | ConvertTo-Json -Compress
"""

  result = _run_powershell(command)

  assert result.returncode == 0, result.stderr
  payload = json.loads(result.stdout.strip())
  assert "Isolated restored database Alembic upgrade failed" in payload["error"]
  assert len(payload["creates"]) == 1
  assert len(payload["drops"]) == 1
  created_name = payload["creates"][0].split()[-1]
  dropped_name = payload["drops"][0].split()[-1]
  assert created_name == dropped_name
  assert re.fullmatch(r"quantx_restore_verify_[0-9a-f]{16}", created_name)
  assert payload["pythonCalls"] == [
    "-m quantx_infrastructure.database.schema_control status",
    f"-m alembic -c {ROOT}\\alembic.ini upgrade head",
  ]


def test_restore_verify_contract_keeps_the_upgrade_isolated_and_journal_check_afterward() -> None:
  restore = _function_source("Invoke-RestoreVerify", "Invoke-MigrateAtRoot")
  gate = _function_source(
    "Invoke-RestoreVerificationSchemaGate",
    "Remove-RestoreVerificationScratchDatabase",
  )
  cleanup = _function_source(
    "Remove-RestoreVerificationScratchDatabase",
    "Invoke-RestoreVerify",
  )

  assert "Invoke-RestoreVerificationSchemaGate" in restore
  assert "Invoke-MigrateAtRoot" not in gate
  assert "Invoke-Backup" not in gate
  assert "alembic" in gate
  assert "upgrade head" in gate
  assert "schema_control check" in gate
  assert "current or behind revisions" in gate
  assert "are allowed." in gate
  assert restore.index("Invoke-RestoreVerificationSchemaGate") < restore.index(
    "} finally {"
  )
  assert restore.index("Remove-RestoreVerificationScratchDatabase") > restore.index(
    "} finally {"
  )
  assert restore.index("qmt-agent\\idempotency.sqlite3") > restore.index(
    "Remove-RestoreVerificationScratchDatabase"
  )
  assert "$ScratchCreated" in cleanup
  assert "Test-RestoreVerificationScratchDatabaseName" in cleanup
  assert "--if-exists" in cleanup
