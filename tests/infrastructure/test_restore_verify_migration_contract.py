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
  return (
    f"function {start}"
    + script.split(f"function {start}", 1)[1].split(
      f"function {end}",
      1,
    )[0]
  )


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
def test_restore_verify_schema_gate_fails_when_isolated_upgrade_or_check_fails() -> (
  None
):
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
  assert (
    "Isolated restored database Alembic upgrade failed"
    in payload["upgradeFailure"]["error"]
  )
  assert payload["checkFailure"]["commands"] == [
    "-m quantx_infrastructure.database.schema_control status",
    "-m alembic -c C:\\release\\alembic.ini upgrade head",
    "-m quantx_infrastructure.database.schema_control check",
  ]
  assert "Restored database does not match" in payload["checkFailure"]["error"]


@pytest.mark.skipif(os.name != "nt", reason="PowerShell restore verification cleanup")
@pytest.mark.parametrize("failure_kind", ["schema", "restore", "create"])
def test_restore_verify_upgrade_failure_keeps_scratch_and_resumes_without_import(
  tmp_path: Path,
  failure_kind: str,
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
  journal = tmp_path / "qmt-agent" / "idempotency.sqlite3"
  journal.parent.mkdir()
  journal.write_bytes(b"mock journal")

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
$Runtime = Join-Path $BackupPath 'runtime'
$CurrentReleaseLink = ""
{restore_functions}
$script:createCalls = [Collections.Generic.List[string]]::new()
$script:dropCalls = [Collections.Generic.List[string]]::new()
$script:pythonCalls = [Collections.Generic.List[string]]::new()
$script:restoreCalls = 0
$script:upgradeExit = 31
$script:failureKind = '{failure_kind}'
$script:serverHost = '127.0.0.1'
$env:RESTORE_TEST_TOKEN = 'same-password'
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
    Host = $script:serverHost
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
  $global:LASTEXITCODE = if ($script:failureKind -eq 'create') {{ 9 }} else {{ 0 }}
}}
function Test-PgRestore {{
  param([Parameter(ValueFromRemainingArguments = $true)][object[]]$Remaining)
  if ($Remaining -contains '--dbname') {{ $script:restoreCalls++ }}
  $global:LASTEXITCODE = if ($script:failureKind -eq 'restore' -and $Remaining -contains '--dbname') {{ 7 }} else {{ 0 }}
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
    Write-Output 'migration diagnostic test-password same-password postgresql://user:other-secret@localhost/test'
    $global:LASTEXITCODE = $script:upgradeExit
    return
  }}
  if ($rendered -eq '-m quantx_infrastructure.database.schema_control check' -or $rendered -like '-c *') {{
    $global:LASTEXITCODE = 0
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
$firstState = Get-Content -LiteralPath $script:RestoreProgressPath -Raw | ConvertFrom-Json
$firstDrops = $script:dropCalls.Count
$firstLog = Get-Content -LiteralPath $script:RestoreLogPath -Raw
$script:upgradeExit = 0
$script:failureKind = ''
$rejections = [Collections.Generic.List[string]]::new()
if ('{failure_kind}' -eq 'schema') {{
  $script:serverHost = 'another-server'
  try {{ Invoke-RestoreVerify -VerificationId $firstState.id 6>$null }} catch {{ $rejections.Add($_.Exception.Message) }}
  $script:serverHost = '127.0.0.1'
  $manifestFile = Join-Path $BackupPath 'manifest.json'
  $originalManifest = [IO.File]::ReadAllText($manifestFile)
  [IO.File]::AppendAllText($manifestFile, ' ')
  try {{ Invoke-RestoreVerify -VerificationId $firstState.id 6>$null }} catch {{ $rejections.Add($_.Exception.Message) }}
  [IO.File]::WriteAllText($manifestFile, $originalManifest)
  try {{ Invoke-RestoreVerify -VerificationId '../invalid' 6>$null }} catch {{ $rejections.Add($_.Exception.Message) }}
  $activeLock = [IO.File]::Open((Join-Path (Split-Path $script:RestoreProgressPath) 'active.lock'), 'OpenOrCreate', 'ReadWrite', 'None')
  try {{
    try {{ Invoke-RestoreVerify -VerificationId $firstState.id 6>$null }} catch {{ $rejections.Add($_.Exception.Message) }}
  }} finally {{ $activeLock.Dispose() }}
  Invoke-RestoreVerify -VerificationId $firstState.id 6>$null
}} else {{
  try {{ Invoke-RestoreVerify -VerificationId $firstState.id 6>$null }} catch {{ $rejections.Add($_.Exception.Message) }}
  Invoke-RestoreVerify 6>$null
}}
$finalState = Get-Content -LiteralPath $script:RestoreProgressPath -Raw | ConvertFrom-Json
try {{ Invoke-RestoreVerify -VerificationId $finalState.id 6>$null }} catch {{ $rejections.Add($_.Exception.Message) }}
[ordered]@{{
  error = $caughtError
  creates = @($script:createCalls)
  drops = @($script:dropCalls)
  pythonCalls = @($script:pythonCalls)
  firstState = $firstState
  firstDrops = $firstDrops
  firstLog = $firstLog
  finalState = $finalState
  restoreCalls = $script:restoreCalls
  rejections = @($rejections)
}} | ConvertTo-Json -Compress
"""

  result = _run_powershell(command)

  assert result.returncode == 0, result.stderr
  payload = json.loads(result.stdout.strip())
  assert payload["firstState"]["status"] == "failed"
  assert "test-password" not in payload["firstLog"]
  assert "other-secret" not in payload["firstLog"]
  assert "same-password" not in payload["firstLog"]
  assert payload["finalState"]["status"] == "passed"
  assert payload["finalState"]["retained"] is False
  if failure_kind == "schema":
    assert "failed in phase 'schema'" in payload["error"]
    assert payload["firstState"]["retained"] is True
    assert payload["firstDrops"] == 0
    assert "Isolated restored database Alembic upgrade failed" in payload["firstLog"]
    assert "migration diagnostic" in payload["firstLog"]
    assert payload["restoreCalls"] == 1
    assert len(payload["creates"]) == len(payload["drops"]) == 1
    assert len(payload["rejections"]) == 5
    assert "does not match" in payload["rejections"][0]
    assert "does not match" in payload["rejections"][1]
    assert "Invalid restore verification ID" in payload["rejections"][2]
    assert payload["pythonCalls"][:2] == [
      "-m quantx_infrastructure.database.schema_control status",
      f"-m alembic -c {ROOT}\\alembic.ini upgrade head",
    ]
  else:
    assert "failed in phase 'restore'" in payload["error"]
    assert payload["firstState"]["retained"] is False
    assert len(payload["rejections"]) == 2
    assert "does not match" in payload["rejections"][0]
    assert len(payload["creates"]) == 2
    assert payload["firstDrops"] == (1 if failure_kind == "restore" else 0)
    assert len(payload["drops"]) == (2 if failure_kind == "restore" else 1)
    assert payload["restoreCalls"] == (2 if failure_kind == "restore" else 1)
  created_name = payload["creates"][-1].split()[-1]
  dropped_name = payload["drops"][-1].split()[-1]
  assert created_name == dropped_name
  assert re.fullmatch(r"quantx_restore_verify_[0-9a-f]{16}", created_name)


def test_restore_verify_contract_keeps_the_upgrade_isolated_and_journal_check_afterward() -> (
  None
):
  restore = _function_source("Invoke-RestoreVerifyCore", "Invoke-MigrateAtRoot")
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
