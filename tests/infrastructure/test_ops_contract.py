import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
OPS = ROOT / "ops"


def test_legacy_layout_and_root_launchers_are_removed() -> None:
  for relative_path in (
    "backend",
    "frontend",
    "start-dev.bat",
    "stop-dev.bat",
  ):
    assert not (ROOT / relative_path).exists()


def test_active_guidance_does_not_reference_legacy_layout() -> None:
  documents = [ROOT / "README.md", ROOT / "AGENTS.md"]
  documents.extend(
    path for path in (ROOT / "docs").rglob("*.md") if "archive" not in path.parts
  )
  forbidden = (
    "backend/",
    "backend\\",
    "\\backend",
    "frontend/",
    "frontend\\",
    "\\frontend",
    "start-dev.bat",
    "stop-dev.bat",
    "LocalAgent",
  )
  violations = {
    str(path.relative_to(ROOT)): token
    for path in documents
    for token in forbidden
    if token in path.read_text(encoding="utf-8")
  }
  assert violations == {}


def test_active_engineering_guidance_uses_monorepo_imports() -> None:
  documents = [
    path
    for path in (ROOT / "docs" / "engineering").rglob("*.md")
    if "archive" not in path.parts
  ]
  forbidden = (
    "from core.",
    "from services.",
    "from database.",
    "from repositories.",
    "from models.",
    "from miniqmt.",
    "import miniqmt",
  )
  violations = {
    str(path.relative_to(ROOT)): token
    for path in documents
    for token in forbidden
    if token in path.read_text(encoding="utf-8")
  }
  assert violations == {}


def test_runtime_resolves_repository_junction_before_deriving_paths() -> None:
  script = (OPS / "quantx.ps1").read_text(encoding="utf-8")
  startup = script.split("function Ensure-RuntimeDirectories", 1)[0]

  assert "function Resolve-PhysicalDirectoryPath" in startup
  assert "[IO.FileAttributes]::ReparsePoint" in startup
  assert '$item.PSObject.Properties["Target"]' in startup
  assert "$Root = Resolve-PhysicalDirectoryPath -Path" in startup
  assert '$ScriptRoot = Join-Path $Root "ops"' in startup
  assert startup.index("$Root = Resolve-PhysicalDirectoryPath -Path") < startup.index(
    '$Runtime = Join-Path $Root ".runtime"'
  )


@pytest.mark.skipif(os.name != "nt", reason="Windows Junction regression")
def test_physical_directory_resolver_follows_windows_junction(
  tmp_path: Path,
) -> None:
  script = (OPS / "quantx.ps1").read_text(encoding="utf-8")
  resolver = (
    "function Resolve-PhysicalDirectoryPath"
    + script.split(
      "function Resolve-PhysicalDirectoryPath",
      1,
    )[1].split("$InvokedScriptRoot", 1)[0]
  )
  powershell = shutil.which("pwsh") or shutil.which("powershell")
  assert powershell is not None

  target = tmp_path / "physical-root"
  junction = tmp_path / "workspace-link"
  target.mkdir()

  def quote(path: Path) -> str:
    return str(path).replace("'", "''")

  command = f"""
$ErrorActionPreference = "Stop"
{resolver}
New-Item -ItemType Junction -Path '{quote(junction)}' `
  -Target '{quote(target)}' | Out-Null
try {{
  Resolve-PhysicalDirectoryPath -Path '{quote(junction)}'
}} finally {{
  if (Test-Path -LiteralPath '{quote(junction)}') {{
    Remove-Item -LiteralPath '{quote(junction)}' -Force
  }}
}}
"""
  result = subprocess.run(
    [powershell, "-NoProfile", "-Command", command],
    capture_output=True,
    text=True,
    encoding="utf-8",
    timeout=30,
    check=False,
  )

  assert result.returncode == 0, result.stderr
  assert Path(result.stdout.strip()).resolve() == target.resolve()


def test_caddy_is_the_only_public_http_entrypoint() -> None:
  development = (OPS / "caddy" / "Caddyfile.dev").read_text(encoding="utf-8")

  assert "reverse_proxy @api 127.0.0.1:18081" in development
  assert "@monitor path /monitor/*" in development
  assert "reverse_proxy @monitor 127.0.0.1:18083" in development
  for path in (
    "/graphql*",
    "/auth*",
    "/health*",
    "/metrics*",
    "/ws/agent*",
    "/agent/*",
  ):
    assert path in development

  assert "reverse_proxy 127.0.0.1:5250" in development
  assert "reverse_proxy @docs 127.0.0.1:5251" in development
  assert "redir @docs_root /docs/ 308" in development
  assert "{$QUANTX_CADDY_SITE_ADDRESS:http://:8080}" in development
  assert "bind {$QUANTX_CADDY_BIND:0.0.0.0}" in development
  assert "import {$QUANTX_CADDY_TLS_SNIPPET:tls_disabled}" in development
  assert "@trusted remote_ip {$QUANTX_CADDY_TRUSTED_IPS:0.0.0.0/0}" in development
  assert "admin 127.0.0.1:2019" in development


def test_only_windows_dev_deployment_surface_remains() -> None:
  script = (OPS / "quantx.ps1").read_text(encoding="utf-8")
  command_contract = script.split(")[string]$Command", 1)[0]
  tools = json.loads((OPS / "tools.lock.json").read_text(encoding="utf-8"))

  assert '[ValidateSet("dev")]' in script
  for command in ("install", "uninstall", "rollback", "agent-mode"):
    assert f'"{command}"' not in command_contract
  for path in (
    OPS / "build-release.ps1",
    OPS / "caddy" / "Caddyfile.prod",
    OPS / "config" / "production.env.example",
    OPS / "quantx",
    OPS / "quantx.py",
    OPS / "quantx-agent.ps1",
    ROOT / ".github" / "workflows" / "release.yml",
  ):
    assert not path.exists(), path
  assert not list((OPS / "windows").glob("*.xml"))
  assert set(tools["tools"]) == {"caddy"}


def test_dev_runtime_defaults_to_full_profile() -> None:
  script = (OPS / "quantx.ps1").read_text(encoding="utf-8")

  assert '[string]$Profile = "full",' in script
  assert "function Resolve-AiRuntimePython" in script
  assert '"QUANTX_AI_RUNTIME_PYTHON_EXE"' in script
  assert 'Join-Path $Root ".venv\\Scripts\\python.exe"' in script
  assert "-Executable $aiRuntimePython" in script


@pytest.mark.skipif(os.name != "nt", reason="PowerShell dev launch matrix")
def test_dev_launch_profile_mode_and_allowlist_matrix() -> None:
  script = (OPS / "quantx.ps1").read_text(encoding="utf-8")
  powershell = shutil.which("pwsh") or shutil.which("powershell")
  assert powershell is not None

  list_setting = (
    "function ConvertFrom-ListSetting"
    + script.split(
      "function ConvertFrom-ListSetting",
      1,
    )[1].split("function Assert-QmtAgentEnrollment", 1)[0]
  )
  launch_functions = (
    "function Resolve-DevLaunchProfile"
    + script.split(
      "function Resolve-DevLaunchProfile",
      1,
    )[1].split("function Invoke-Up", 1)[0]
  )
  command = f"""
$ErrorActionPreference = "Stop"
{list_setting}
{launch_functions}

$profiles = [ordered]@{{
  webDefault = Resolve-DevLaunchProfile -RequestedProfile "web" `
    -ModeExplicitlySpecified $false -RequestedMode "data-only"
  webDataOnly = Resolve-DevLaunchProfile -RequestedProfile "web" `
    -ModeExplicitlySpecified $true -RequestedMode "data-only"
  webLive = Resolve-DevLaunchProfile -RequestedProfile "web" `
    -ModeExplicitlySpecified $true -RequestedMode "live"
  fullDefault = Resolve-DevLaunchProfile -RequestedProfile "full" `
    -ModeExplicitlySpecified $false -RequestedMode "data-only"
}}

function Reset-TestAccountEnvironment {{
  $env:QMT_ACCOUNT_WHITELIST = ""
  $env:REAL_TRADING_ACCOUNT_ALLOWLIST = ""
  $env:AUTH_BOOTSTRAP_ACCOUNT_IDS = ""
}}

Reset-TestAccountEnvironment
$Profile = "full"
$script:ModeWasExplicitlySpecified = $false
$Mode = "data-only"
$AccountId = "ACCOUNT-1"
$defaultMode = Set-DevTradingModeEnvironment
$defaultLive = [ordered]@{{
  mode = $defaultMode
  qmtMode = $env:QMT_AGENT_MODE
  qmtAccount = $env:QMT_ACCOUNT_WHITELIST
  allowlist = $env:REAL_TRADING_ACCOUNT_ALLOWLIST
  server = $env:ENABLE_REAL_TRADING
  qmt = $env:QMT_REAL_TRADING_ENABLED
  tTrade = $env:T_TRADE_LIVE_ENABLED
}}

Reset-TestAccountEnvironment
$Profile = "full"
$script:ModeWasExplicitlySpecified = $true
$Mode = "data-only"
$AccountId = "IGNORED"
$explicitDataOnlyMode = Set-DevTradingModeEnvironment
$explicitDataOnly = [ordered]@{{
  mode = $explicitDataOnlyMode
  qmtAccount = $env:QMT_ACCOUNT_WHITELIST
  allowlist = $env:REAL_TRADING_ACCOUNT_ALLOWLIST
  server = $env:ENABLE_REAL_TRADING
  qmt = $env:QMT_REAL_TRADING_ENABLED
  tTrade = $env:T_TRADE_LIVE_ENABLED
}}

Reset-TestAccountEnvironment
$Profile = "web"
$script:ModeWasExplicitlySpecified = $true
$Mode = "data-only"
$AccountId = ""
$webDataOnlyMode = Set-DevTradingModeEnvironment

Reset-TestAccountEnvironment
$env:QMT_ACCOUNT_WHITELIST = "ACCOUNT-2"
$Profile = "full"
$script:ModeWasExplicitlySpecified = $true
$Mode = "live"
$AccountId = ""
$uniqueMode = Set-DevTradingModeEnvironment
$uniqueAccount = [ordered]@{{
  mode = $uniqueMode
  account = $env:QMT_ACCOUNT_WHITELIST
  allowlist = $env:REAL_TRADING_ACCOUNT_ALLOWLIST
}}

$env:ENABLE_REAL_TRADING = "true"
$env:QMT_REAL_TRADING_ENABLED = "true"
$env:T_TRADE_LIVE_ENABLED = "true"
Disable-DevLiveTradingCapability
$degradedLive = [ordered]@{{
  mode = $env:QMT_AGENT_MODE
  account = $env:QMT_ACCOUNT_WHITELIST
  allowlist = $env:REAL_TRADING_ACCOUNT_ALLOWLIST
  server = $env:ENABLE_REAL_TRADING
  qmt = $env:QMT_REAL_TRADING_ENABLED
  tTrade = $env:T_TRADE_LIVE_ENABLED
}}

Reset-TestAccountEnvironment
$Profile = "full"
$script:ModeWasExplicitlySpecified = $true
$Mode = "paper"
$AccountId = "ACCOUNT-2"
$paperModeError = ""
try {{
  $null = Set-DevTradingModeEnvironment
}} catch {{
  $paperModeError = $_.Exception.Message
}}

Reset-TestAccountEnvironment
$env:QMT_ACCOUNT_WHITELIST = "ACCOUNT-A"
$env:AUTH_BOOTSTRAP_ACCOUNT_IDS = "ACCOUNT-B"
$Profile = "full"
$script:ModeWasExplicitlySpecified = $true
$Mode = "live"
$AccountId = ""
$multipleAccountError = ""
try {{
  $null = Set-DevTradingModeEnvironment
}} catch {{
  $multipleAccountError = $_.Exception.Message
}}

[ordered]@{{
  profiles = $profiles
  defaultLive = $defaultLive
  explicitDataOnly = $explicitDataOnly
  webDataOnlyMode = $webDataOnlyMode
  uniqueAccount = $uniqueAccount
  degradedLive = $degradedLive
  paperModeError = $paperModeError
  multipleAccountError = $multipleAccountError
}} | ConvertTo-Json -Depth 6 -Compress
"""
  result = subprocess.run(
    [powershell, "-NoProfile", "-Command", command],
    capture_output=True,
    text=True,
    encoding="utf-8",
    timeout=30,
    check=False,
  )

  assert result.returncode == 0, result.stderr
  payload = json.loads(result.stdout.strip())
  assert payload["profiles"] == {
    "webDefault": "full",
    "webDataOnly": "web",
    "webLive": "full",
    "fullDefault": "full",
  }
  assert payload["defaultLive"] == {
    "mode": "live",
    "qmtMode": "live",
    "qmtAccount": "ACCOUNT-1",
    "allowlist": '["ACCOUNT-1"]',
    "server": "true",
    "qmt": "true",
    "tTrade": "true",
  }
  assert payload["explicitDataOnly"] == {
    "mode": "data-only",
    "qmtAccount": "",
    "allowlist": "[]",
    "server": "false",
    "qmt": "false",
    "tTrade": "false",
  }
  assert payload["webDataOnlyMode"] == "data-only"
  assert payload["uniqueAccount"] == {
    "mode": "live",
    "account": "ACCOUNT-2",
    "allowlist": '["ACCOUNT-2"]',
  }
  assert payload["degradedLive"] == {
    "mode": "live",
    "account": "ACCOUNT-2",
    "allowlist": "[]",
    "server": "false",
    "qmt": "false",
    "tTrade": "false",
  }
  assert "only non-live entry" in payload["paperModeError"]
  assert "Multiple development trading accounts" in payload["multipleAccountError"]


@pytest.mark.skipif(os.name != "nt", reason="PowerShell runtime metadata")
def test_degraded_full_state_preserves_requested_live_mode() -> None:
  script = (OPS / "quantx.ps1").read_text(encoding="utf-8")
  powershell = shutil.which("pwsh") or shutil.which("powershell")
  assert powershell is not None

  runtime_configuration = (
    "function Get-DevRuntimeConfiguration"
    + script.split(
      "function Get-DevRuntimeConfiguration",
      1,
    )[1].split("function Get-TrackedProcess", 1)[0]
  )
  command = f"""
$ErrorActionPreference = "Stop"
{runtime_configuration}
$entries = @(
  [pscustomobject]@{{
    name = "api"
    runtimeProfile = "full"
    agentMode = "live"
    configuredAccount = "ACCOUNT-1"
    qmtLaunchState = "BLOCKED"
    qmtReasonCode = "QMT_ENROLLMENT_REQUIRED"
    qmtLaunchStartedAt = ""
    liveTradingEnabled = $false
  }},
  [pscustomobject]@{{ name = "worker" }}
)
Get-DevRuntimeConfiguration -Entries $entries |
  ConvertTo-Json -Compress
"""
  result = subprocess.run(
    [powershell, "-NoProfile", "-Command", command],
    capture_output=True,
    text=True,
    encoding="utf-8",
    timeout=30,
    check=False,
  )

  assert result.returncode == 0, result.stderr
  assert json.loads(result.stdout.strip()) == {
    "profile": "full",
    "agentMode": "live",
    "configuredAccount": "ACCOUNT-1",
    "qmtLaunchState": "BLOCKED",
    "qmtReasonCode": "QMT_ENROLLMENT_REQUIRED",
    "qmtLaunchStartedAt": "",
    "liveTradingEnabled": False,
  }


def test_degraded_full_status_is_explicit_and_never_reports_data_only() -> None:
  script = (OPS / "quantx.ps1").read_text(encoding="utf-8")
  status = script.split("function Invoke-Status", 1)[1].split(
    "function Invoke-Logs",
    1,
  )[0]
  process_start = script.split("function Start-ManagedProcess", 1)[1].split(
    "function Wait-HttpReady",
    1,
  )[0]

  assert "Get-DevRuntimeConfiguration -Entries $entries" in status
  assert "Runtime state=DEGRADED" in status
  assert "liveTrading={3}" in status
  assert "must not be reported as READY" in status
  assert "$qmtHealthQueryAllowed = $false" in status
  assert "if ($qmtHealthQueryAllowed)" in status
  assert "agent=blocked/offline" in status
  assert 'agentMode = "data-only"' not in status
  for field in (
    "runtimeProfile",
    "agentMode",
    "configuredAccount",
    "qmtLaunchState",
    "qmtReasonCode",
    "qmtLaunchStartedAt",
    "liveTradingEnabled",
  ):
    assert field in process_start


@pytest.mark.skipif(os.name != "nt", reason="PowerShell status timestamp formatting")
def test_status_formats_managed_process_start_time_in_local_timezone() -> None:
  script = (OPS / "quantx.ps1").read_text(encoding="utf-8")
  powershell = shutil.which("pwsh") or shutil.which("powershell")
  assert powershell is not None
  formatter = (
    "function ConvertTo-LocalStatusTimestamp"
    + script.split(
      "function ConvertTo-LocalStatusTimestamp",
      1,
    )[1].split("function Invoke-Status", 1)[0]
  )
  command = f"""
$ErrorActionPreference = "Stop"
{formatter}
$source = "2026-08-20T05:33:53Z"
[ordered]@{{
  actual = ConvertTo-LocalStatusTimestamp -Value $source
  expected = ([datetimeoffset]::Parse($source)).ToLocalTime().ToString(
    "yyyy-MM-dd HH:mm:ss zzz",
    [Globalization.CultureInfo]::InvariantCulture
  )
}} | ConvertTo-Json -Compress
"""
  result = subprocess.run(
    [powershell, "-NoProfile", "-Command", command],
    capture_output=True,
    text=True,
    encoding="utf-8",
    timeout=30,
    check=False,
  )

  assert result.returncode == 0, result.stderr
  payload = json.loads(result.stdout.strip())
  assert payload["actual"] == payload["expected"]
  status = script.split("function Invoke-Status", 1)[1].split(
    "function Invoke-Logs",
    1,
  )[0]
  assert "ConvertTo-LocalStatusTimestamp -Value $entry.startedAt" in status


@pytest.mark.skipif(os.name != "nt", reason="PowerShell degraded status branch")
def test_degraded_status_does_not_query_stale_qmt_health() -> None:
  script = (OPS / "quantx.ps1").read_text(encoding="utf-8")
  powershell = shutil.which("pwsh") or shutil.which("powershell")
  assert powershell is not None
  formatter_function = (
    "function ConvertTo-LocalStatusTimestamp"
    + script.split(
      "function ConvertTo-LocalStatusTimestamp",
      1,
    )[1].split("function Invoke-Status", 1)[0]
  )
  status_function = (
    "function Invoke-Status"
    + script.split(
      "function Invoke-Status",
      1,
    )[1].split("function Invoke-Logs", 1)[0]
  )
  command = f"""
$ErrorActionPreference = "Stop"
$ApiPort = 18081
function Import-QuantXEnvironment {{}}
function Read-State {{
  [pscustomobject]@{{ name = "api"; pid = 1; startedAt = "now" }}
}}
function Get-TrackedProcess {{ $null }}
function Get-DevRuntimeConfiguration {{
  [pscustomobject]@{{
    profile = "full"
    agentMode = "live"
    configuredAccount = "ACCOUNT-1"
    qmtLaunchState = "BLOCKED"
    qmtReasonCode = "QMT_ENROLLMENT_REQUIRED"
    liveTradingEnabled = $false
  }}
}}
function Get-PortOwner {{ $null }}
function Resolve-Python {{ $null }}
function Show-ExternalDependencies {{}}
$script:qmtHealthCalls = 0
function Show-QmtAgentRuntimeHealth {{ $script:qmtHealthCalls++ }}
{formatter_function}
{status_function}
$rendered = (& {{ Invoke-Status }} *>&1 | Out-String)
[ordered]@{{
  qmtHealthCalls = $script:qmtHealthCalls
  output = $rendered
}} | ConvertTo-Json -Compress
"""
  result = subprocess.run(
    [powershell, "-NoProfile", "-Command", command],
    capture_output=True,
    text=True,
    encoding="utf-8",
    timeout=30,
    check=False,
  )

  assert result.returncode == 0, result.stderr
  payload = json.loads(result.stdout.strip())
  assert payload["qmtHealthCalls"] == 0
  assert "agent=blocked/offline" in payload["output"]
  assert "liveTrading=DISABLED" in payload["output"]


@pytest.mark.skipif(os.name != "nt", reason="PowerShell QMT launch identity")
def test_qmt_ready_wait_requires_live_process_and_current_launch_heartbeat() -> None:
  script = (OPS / "quantx.ps1").read_text(encoding="utf-8")
  powershell = shutil.which("pwsh") or shutil.which("powershell")
  assert powershell is not None
  wait_function = (
    "function Wait-QmtAgentRuntimeReady"
    + script.split(
      "function Wait-QmtAgentRuntimeReady",
      1,
    )[1].split("function Invoke-CaddyRecovery", 1)[0]
  )
  command = f"""
$ErrorActionPreference = "Stop"
$WarningPreference = "SilentlyContinue"
$script:processAlive = $false
$script:healthCalls = 0
$script:healthPayload = $null
function Get-TrackedProcess {{
  param([object]$Entry)
  if ($script:processAlive) {{
    return [pscustomobject]@{{ Id = [int]$Entry.pid }}
  }}
  return $null
}}
function Invoke-RestMethod {{
  param([string]$Uri, [int]$TimeoutSec)
  $script:healthCalls++
  return $script:healthPayload
}}
function Show-QmtAgentRuntimeHealth {{}}
function Start-Sleep {{ param([int]$Seconds) }}
{wait_function}
$launchStartedAt = [datetime]::Parse(
  "2026-08-20T04:00:00Z"
).ToUniversalTime()
$entry = [pscustomobject]@{{
  name = "qmt-agent"
  pid = 4242
  startedAt = "2026-08-20T04:00:00Z"
}}
function Set-TestHealth {{
  param([string]$HeartbeatAt)
  $script:healthPayload = [pscustomobject]@{{
    components = [pscustomobject]@{{
      qmtAgent = [pscustomobject]@{{
        status = "ready"
        readyDevices = 1
        modes = @("live")
        protocolVersions = @("1.1")
        accountIds = @("ACCOUNT-1")
        latestSnapshotAgeSeconds = 1
        latestReadyHeartbeatAt = $HeartbeatAt
      }}
    }}
  }}
}}
Set-TestHealth -HeartbeatAt "2026-08-20T04:00:01Z"
$dead = Wait-QmtAgentRuntimeReady `
  -AccountId "ACCOUNT-1" `
  -ProcessEntry $entry `
  -LaunchStartedAt $launchStartedAt `
  -TimeoutSeconds 0
$script:processAlive = $true
Set-TestHealth -HeartbeatAt "2026-08-20T03:59:59Z"
$prior = Wait-QmtAgentRuntimeReady `
  -AccountId "ACCOUNT-1" `
  -ProcessEntry $entry `
  -LaunchStartedAt $launchStartedAt `
  -TimeoutSeconds 0
Set-TestHealth -HeartbeatAt "2026-08-20T04:00:01Z"
$current = Wait-QmtAgentRuntimeReady `
  -AccountId "ACCOUNT-1" `
  -ProcessEntry $entry `
  -LaunchStartedAt $launchStartedAt `
  -TimeoutSeconds 0
[ordered]@{{
  dead = $dead
  prior = $prior
  current = $current
  healthCalls = $script:healthCalls
}} | ConvertTo-Json -Compress
"""
  result = subprocess.run(
    [powershell, "-NoProfile", "-Command", command],
    capture_output=True,
    text=True,
    encoding="utf-8",
    timeout=30,
    check=False,
  )

  assert result.returncode == 0, result.stderr
  assert json.loads(result.stdout.strip()) == {
    "dead": False,
    "prior": False,
    "current": True,
    "healthCalls": 2,
  }


def test_agent_websocket_timeout_exceeds_native_watchdog() -> None:
  script = (OPS / "quantx.ps1").read_text(encoding="utf-8")

  assert "$AgentWebSocketPingTimeoutSeconds = 960" in script
  assert '"--ws-ping-interval", "20"' in script
  assert '"--ws-ping-timeout", [string]$AgentWebSocketPingTimeoutSeconds' in script


def test_server_runtime_path_excludes_qmt_agent_source() -> None:
  script = (OPS / "quantx.ps1").read_text(encoding="utf-8")
  workspace_path = script.split(
    "function Get-WorkspacePythonPath",
    1,
  )[1].split("function Get-QmtAgentPythonPath", 1)[0]
  qmt_path = script.split(
    "function Get-QmtAgentPythonPath",
    1,
  )[1].split("function Import-QuantXEnvironment", 1)[0]

  assert "apps\\qmt-agent\\src" not in workspace_path
  assert "apps\\ai-runtime\\src" in workspace_path
  assert "apps\\qmt-agent\\src" in qmt_path
  assert "packages\\contracts\\src" in qmt_path
  assert "packages\\infrastructure\\src" not in qmt_path


def test_down_only_targets_pid_and_start_time_from_state() -> None:
  script = (OPS / "quantx.ps1").read_text(encoding="utf-8")
  stop_tracked = script.split("function Stop-TrackedProcesses", 1)[1].split(
    "function Start-DevCaddy",
    1,
  )[0]

  assert "function Get-TrackedProcess" in script
  assert "[string]$Entry.startedAt" in script
  assert "$Entry.startedAt -is [datetime]" in script
  assert "([datetime]$Entry.startedAt).ToUniversalTime()" in script
  assert "Stop-TrackedProcesses -Entries" in script
  assert "[object[]]$PreservedEntries = @()" in script
  assert "Write-State -Processes $nextState" in script
  assert "their verified PID/start-time entries remain in the state file" in script
  assert "QuantX did not stop or replace untracked port owners." in script
  assert "function Request-ManagedProcessShutdown" in script
  assert "$ApiPort = 18081" in script
  assert "127.0.0.1:$ApiPort/_dev/shutdown" in script
  assert 'stop --address "127.0.0.1:2019"' in script
  assert (
    "$gracefulWaitMilliseconds = if ($shutdownRequested) { 10000 } else { 0 }" in script
  )
  assert "has no graceful dev stop channel" in script
  assert "did not exit after the graceful stop window" in script
  assert "$process.Kill()" in stop_tracked
  assert "Stop-Process -Id $process.Id" not in stop_tracked


def test_dev_components_keep_caddy_recovery_and_monitor_lifecycle_separate() -> None:
  script = (OPS / "quantx.ps1").read_text(encoding="utf-8")
  recovery = script.split("function Invoke-CaddyRecovery", 1)[1].split(
    "function Invoke-BoundedCliCommand",
    1,
  )[0]
  invoke_up = script.split("function Invoke-Up", 1)[1].split(
    "function Invoke-Down",
    1,
  )[0]

  assert 'if ($Component -eq "monitor")' in invoke_up
  assert "Invoke-MonitorUp" in invoke_up
  assert 'if ($Component -ne "caddy")' in invoke_up
  assert "Invoke-CaddyRecovery" in invoke_up
  assert '$Command -notin @("up", "down", "status", "logs")' in script
  assert "-Component is only supported by up, down, status, and logs." in script
  assert '$Component -notin @("caddy", "monitor")' in script
  assert "Caddy component recovery is limited to the dev/full profile." in recovery
  for component in (
    "api",
    "engine",
    "web",
    "docs",
    "worker",
    "qmt-agent",
  ):
    assert f'"{component}"' in recovery
  assert "Get-TrackedProcess -Entry $matches[0]" in recovery
  assert "Managed Caddy is already running" in recovery
  assert "duplicate managed Caddy entries" in recovery
  assert "Assert-PortsAvailable -Ports @(8080)" in recovery
  assert "Start-DevCaddy -Executable $caddy" in recovery
  assert "Wait-DevCaddyReady" in recovery
  assert "-PreservedEntries $preserved" in recovery
  assert "Write-State -Processes $script:ManagedProcesses" in recovery


def test_dev_caddy_start_and_readiness_are_shared_and_state_is_atomic() -> None:
  script = (OPS / "quantx.ps1").read_text(encoding="utf-8")
  start = script.split("function Start-DevCaddy", 1)[1].split(
    "function Wait-DevCaddyReady",
    1,
  )[0]
  readiness = script.split("function Wait-DevCaddyReady", 1)[1].split(
    "function Invoke-CaddyRecovery",
    1,
  )[0]
  writer = script.split("function Write-State", 1)[1].split(
    "function Read-MonitorState",
    1,
  )[0]
  ordinary_up = script.split("function Invoke-Up", 1)[1].split(
    "function Invoke-Down",
    1,
  )[0]

  assert '$env:QUANTX_ROOT = $Root.Replace("\\", "/")' in start
  assert "Initialize-CaddyEnvironment" in start
  assert '-Name "caddy"' in start
  assert '"run",' in start
  assert r'"ops\caddy\Caddyfile.dev"' in start
  assert '"--adapter", "caddyfile"' in start
  assert "Start-DevCaddy -Executable $caddy" in ordinary_up
  assert "Wait-DevCaddyReady" in ordinary_up
  assert "Stop-OnFailure" in ordinary_up
  for endpoint in (
    "http://127.0.0.1:8080/health/live",
    "http://127.0.0.1:8080/health/ready",
    "http://127.0.0.1:8080/docs/",
  ):
    assert endpoint in readiness
  assert "dev-processes.{0}.{1}.tmp" in writer
  assert "dev-processes.{0}.{1}.replace-backup.tmp" in writer
  assert "[IO.File]::Replace(" in writer
  assert "$replacementBackupFile," in writer
  assert "$null" not in writer
  assert "Remove-Item -LiteralPath $replacementBackupFile -Force" in writer
  assert "[IO.File]::Move(" in writer


def test_full_profile_preflights_agent_and_uses_external_prefect() -> None:
  script = (OPS / "quantx.ps1").read_text(encoding="utf-8")
  dev_mode = script.split("function Set-DevTradingModeEnvironment", 1)[1].split(
    "function Invoke-Up", 1
  )[0]

  assert "function Test-QmtAgentEnrollment" in script
  assert "function Assert-QmtAgentEnrollment" in script
  assert "Test-QmtAgentEnrollment -Python $qmtPython" in script
  assert "function Set-DevTradingModeEnvironment" in script
  assert "function Disable-DevLiveTradingCapability" in script
  assert "$agentMode = Set-DevTradingModeEnvironment" in script
  assert (
    '$script:ModeWasExplicitlySpecified = $PSBoundParameters.ContainsKey("Mode")'
    in script
  )
  assert 'if ($script:ModeWasExplicitlySpecified) { $Mode } else { "live" }' in script
  assert "function Resolve-DevTradingAccountId" in dev_mode
  assert '"QMT_ACCOUNT_WHITELIST"' in dev_mode
  assert "ConfirmLive" not in dev_mode
  assert "$env:QMT_ACCOUNT_WHITELIST = if" in script
  assert "$env:REAL_TRADING_ACCOUNT_ALLOWLIST = ConvertTo-Json" in script
  assert '$env:ENV = "testing"' in script
  assert "$env:ENV = $serverEnvironment" in script
  assert '$DefaultPrefectApiUrl = "http://192.168.5.6:30420/api"' in script
  assert '$DefaultPrefectWorkerPool = "quantx-pool"' in script
  assert "PREFECT_SERVER_UI_STATIC_DIRECTORY" not in script
  assert '$env:PYTHONUTF8 = "1"' in script
  assert "function Initialize-CaddyEnvironment" in script
  assert "Initialize-CaddyEnvironment" in script
  assert "function Invoke-BoundedCliCommand" in script
  assert '-Name "Prefect work-pool inspect"' in script
  assert '-Name "Prefect deploy"' in script
  assert "-TimeoutSeconds 180" in script
  assert "timed out after $TimeoutSeconds seconds" in script

  invoke_up = script.split("function Invoke-Up", 1)[1].split("function Invoke-Down", 1)[
    0
  ]
  assert "function Enable-DevServerTrading" not in script
  assert '$env:ENABLE_REAL_TRADING = "true"' in script
  assert '$env:T_TRADE_LIVE_ENABLED = "true"' in script
  assert "Resolve-DevLaunchProfile" in invoke_up
  assert "-ModeExplicitlySpecified $script:ModeWasExplicitlySpecified" in invoke_up
  assert "-RequestedMode $Mode" in invoke_up
  assert "$Profile = $resolvedProfile" in invoke_up
  assert "Wait-QmtAgentRuntimeReady" in invoke_up
  assert "if ($qmtAgentLaunchAllowed)" in invoke_up
  assert "Disable-DevLiveTradingCapability" in invoke_up
  assert '"QMT_ENROLLMENT_REQUIRED"' in invoke_up
  assert '"QMT_RUNTIME_UNAVAILABLE"' in invoke_up
  assert '"persisted history will continue to start.' in invoke_up
  assert "$script:RuntimeAgentMode = $agentMode" in invoke_up
  assert "$script:RuntimeQmtLaunchState = if" in invoke_up
  assert "$env:QMT_AGENT_LAUNCH_STATE = $script:RuntimeQmtLaunchState" in invoke_up
  assert "$env:QMT_AGENT_LAUNCH_REASON = $script:RuntimeQmtReasonCode" in invoke_up
  assert (
    "$env:QMT_AGENT_LAUNCH_STARTED_AT = $script:RuntimeQmtLaunchStartedAt" in invoke_up
  )
  assert invoke_up.index("$env:QMT_AGENT_LAUNCH_STATE") < invoke_up.index(
    "Start-ManagedProcess"
  )
  assert invoke_up.index("$env:QMT_AGENT_LAUNCH_REASON") < invoke_up.index(
    "Start-ManagedProcess"
  )
  assert invoke_up.index("$env:QMT_AGENT_LAUNCH_STARTED_AT") < invoke_up.index(
    "Start-ManagedProcess"
  )
  qmt_launch = invoke_up.index("$qmtProcessLaunchStartedAt = [datetime]::UtcNow")
  qmt_process = invoke_up.index('-Name "qmt-agent"')
  assert qmt_launch < qmt_process
  assert "-ProcessEntry $qmtProcessEntry" in invoke_up
  assert "-LaunchStartedAt $qmtProcessLaunchStartedAt" in invoke_up
  assert '$env:QMT_AGENT_MODE = "data-only"' not in invoke_up
  assert '$protocols -contains "1.1"' in script
  assert "[int]$qmt.readyDevices -ge 1" in script

  assert '$env:PREFECT_HOME = Join-Path $Runtime "prefect"' in script
  assert "$env:PREFECT_API_URL = Get-PrefectApiUrl" in script
  assert "$env:PREFECT_WORKER_POOL = Get-PrefectWorkerPool" in script


def test_dev_guidance_forbids_silent_data_only_fallback() -> None:
  agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
  readme = (ROOT / "README.md").read_text(encoding="utf-8")
  examples = (ROOT / "docs" / "engineering" / "api" / "EXAMPLES.md").read_text(
    encoding="utf-8"
  )
  deployment = (ROOT / "docs" / "engineering" / "deployment" / "README.md").read_text(
    encoding="utf-8"
  )

  for document in (agents, readme, examples, deployment):
    assert "full/live" in document
    assert "不得" in document
    assert "data-only" in document
  assert "默认 `data-only`" not in readme
  assert "默认 data-only" not in examples


def test_all_python_processes_force_utf8_logs() -> None:
  script = (OPS / "quantx.ps1").read_text(encoding="utf-8")

  assert "function Initialize-PythonEnvironment" in script
  invoke_up = script.split("function Invoke-Up", 1)[1].split(
    "function Install-LockedTool",
    1,
  )[0]
  assert "Initialize-PythonEnvironment" in invoke_up

  initializer = script.split("function Initialize-PythonEnvironment", 1)[1].split(
    "function Initialize-PrefectEnvironment",
    1,
  )[0]
  assert '$env:PYTHONUTF8 = "1"' in initializer
  assert '$env:PYTHONIOENCODING = "utf-8"' in initializer


def test_public_caddy_origins_are_allowed_for_web_sessions() -> None:
  settings_source = (
    ROOT
    / "packages"
    / "infrastructure"
    / "src"
    / "quantx_infrastructure"
    / "config"
    / "settings.py"
  ).read_text(encoding="utf-8")
  environment_examples = (ROOT / "apps" / "api" / ".env.example").read_text(
    encoding="utf-8"
  )

  for origin in ("http://127.0.0.1:8080", "http://localhost:8080"):
    assert origin in settings_source
    assert origin in environment_examples


def test_conda_resolution_supports_powershell_hook_commands() -> None:
  script = (OPS / "quantx.ps1").read_text(encoding="utf-8")

  assert "$conda.Name" in script
  assert "$conda.Path" not in script
  assert r"miniconda3\envs\$condaEnvironment\python.exe" in script
  assert r"anaconda3\envs\$condaEnvironment\python.exe" in script
  assert '"CONDA_EXE"' in script


def test_node_resolution_supports_an_explicit_noninteractive_runtime() -> None:
  script = (OPS / "quantx.ps1").read_text(encoding="utf-8")
  resolver = script.split("function Resolve-Node", 1)[1].split(
    "function Get-WorkspacePythonPath",
    1,
  )[0]

  assert 'GetEnvironmentVariable("QUANTX_NODE_EXE")' in resolver
  assert "Configured Node.js executable does not exist" in resolver
  assert "Set QUANTX_NODE_EXE or add it to PATH" in resolver


@pytest.mark.skipif(os.name != "nt", reason="Windows Conda runtime resolution")
def test_qmt_runtime_defaults_to_original_xtquant_conda_environment(
  tmp_path: Path,
) -> None:
  script = (OPS / "quantx.ps1").read_text(encoding="utf-8")
  assert '$DefaultQmtCondaEnvironment = "xtquant-demo"' in script
  resolver = (
    "function Resolve-Python"
    + script.split(
      "function Resolve-Python",
      1,
    )[1].split("function Resolve-AiRuntimePython", 1)[0]
  )
  powershell = shutil.which("pwsh") or shutil.which("powershell")
  assert powershell is not None

  profile = tmp_path / "profile"
  qmt_python = profile / "miniconda3" / "envs" / "xtquant-demo" / "python.exe"
  shared_python = tmp_path / "workspace-python.exe"
  qmt_python.parent.mkdir(parents=True)
  qmt_python.touch()
  shared_python.touch()

  def quote(path: Path) -> str:
    return str(path).replace("'", "''")

  command = f"""
$ErrorActionPreference = "Stop"
$Environment = "dev"
$CurrentReleaseLink = ""
$DefaultQmtCondaEnvironment = "xtquant-demo"
$env:QUANTX_QMT_PYTHON_EXE = ""
$env:QUANTX_PYTHON_EXE = '{quote(shared_python)}'
$env:CONDA_ENV_NAME = ""
$env:CONDA_PREFIX = ""
$env:CONDA_EXE = ""
$env:USERPROFILE = '{quote(profile)}'
{resolver}
Resolve-Python -Qmt
"""
  result = subprocess.run(
    [powershell, "-NoProfile", "-Command", command],
    capture_output=True,
    text=True,
    encoding="utf-8",
    timeout=30,
    check=False,
  )

  assert result.returncode == 0, result.stderr
  assert Path(result.stdout.strip()).resolve() == qmt_python.resolve()


def test_environment_precedence_keeps_process_values_and_later_files_win() -> None:
  script = (OPS / "quantx.ps1").read_text(encoding="utf-8")
  importer = script.split(
    "function Import-QuantXEnvironment",
    1,
  )[1].split("function Read-State", 1)[0]

  assert '$environmentName = "development"' in importer
  assert '"development"' in importer
  assert importer.index(r'apps\api\.env"') < importer.index(
    r"apps\api\.env.$environmentName"
  )
  assert "$processOverrides.Contains($name)" in importer
  assert ".env.production" not in importer


def test_process_state_reader_flattens_json_arrays_before_pid_checks() -> None:
  script = (OPS / "quantx.ps1").read_text(encoding="utf-8")
  reader = script.split("function Read-State", 1)[1].split(
    "function Write-State",
    1,
  )[0]

  assert "$parsed = ConvertFrom-Json -InputObject $raw" in reader
  assert "if ($parsed -is [System.Array])" in reader
  assert "foreach ($entry in $parsed)" in reader


def test_port_owner_lookup_is_safe_on_windows_powershell_51() -> None:
  script = (OPS / "quantx.ps1").read_text(encoding="utf-8")
  lookup = script.split("function Get-PortOwner", 1)[1].split(
    "function Assert-PortsAvailable",
    1,
  )[0]

  assert '$processInfo.PSObject.Properties["CommandLine"]' in lookup
  assert '$processInfo.PSObject.Properties["Path"]' in lookup
  assert "$processInfo.CommandLine" not in lookup


def test_every_prefect_deployment_targets_the_external_process_pool() -> None:
  configuration = yaml.safe_load(
    (ROOT / "apps" / "worker" / "prefect.yaml").read_text(encoding="utf-8")
  )

  deployments = configuration["deployments"]
  assert deployments
  assert {deployment["work_pool"]["name"] for deployment in deployments} == {
    "quantx-pool"
  }
  assert {deployment["work_pool"]["work_queue_name"] for deployment in deployments} == {
    "default"
  }


def test_web_ci_uses_the_root_workspace_lockfile() -> None:
  runtime = (OPS / "quantx.ps1").read_text(encoding="utf-8")
  ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
  checks = (ROOT / ".github" / "workflows" / "pr-checks.yml").read_text(
    encoding="utf-8"
  )

  assert "cache-dependency-path: package-lock.json" in ci
  assert "apps/web/package-lock.json" not in ci
  assert "apps/web/package-lock.json" not in checks
  assert "root package-lock.json" in checks
  assert r"node_modules\vite\bin\vite.js" in runtime
  assert r"node_modules\vitepress\bin\vitepress.js" in runtime
  assert r"apps\web\node_modules\vite\bin\vite.js" not in runtime


def test_web_package_metadata_targets_the_monorepo() -> None:
  root_package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
  web_package = json.loads(
    (ROOT / "apps" / "web" / "package.json").read_text(encoding="utf-8")
  )
  lockfile = (ROOT / "package-lock.json").read_text(encoding="utf-8")

  assert root_package["workspaces"] == ["apps/web", "apps/docs"]
  assert web_package["name"] == "@quantx/web"
  assert web_package["repository"]["directory"] == "apps/web"
  assert web_package["repository"]["url"].endswith("/quantx.git")
  assert all(
    "quantx-frontend" not in command for command in root_package["scripts"].values()
  )
  assert "QuantFrontend" not in lockfile
  assert "quantx-frontend" not in lockfile


def test_monorepo_ci_enforces_root_python_lint_gate() -> None:
  workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

  assert "uv run ruff check apps packages tests" in workflow
  assert "npm run lint:strict" in workflow
  assert "--fail-under=85" in workflow
  assert "diff-cover coverage.xml" in workflow
  assert "--fail-under=80" in workflow


def test_monorepo_ci_result_cannot_hide_failed_or_cancelled_prerequisites() -> None:
  workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
  result_job = workflow.split("  result:", 1)[1]

  for prerequisite in (
    "changes",
    "python-boundaries",
    "windows-runtime",
    "python-coverage",
    "web",
  ):
    assert prerequisite in result_job
  assert 'needs.changes.result }}" = "success"' in result_job
  assert "success|skipped)" in result_job
  assert '!= "failure"' not in result_job


def test_external_dependencies_are_checked_without_lifecycle_ownership() -> None:
  script = (OPS / "quantx.ps1").read_text(encoding="utf-8")
  diagnostic = (
    ROOT
    / "packages"
    / "infrastructure"
    / "src"
    / "quantx_infrastructure"
    / "diagnostics"
    / "external_dependencies.py"
  ).read_text(encoding="utf-8")

  assert "quantx_infrastructure.diagnostics.external_dependencies" in script
  assert "market_data_transfer" in diagnostic
  assert "strategy_backtests" in diagnostic
  assert "version={3} (externally managed)" in script
  assert '"SHOW server_version"' in diagnostic
  assert 'client.info("server")' in diagnostic
  assert 'client.get(f"{host}/health")' in diagnostic
  assert "subprocess" not in diagnostic


def test_monitor_has_an_independent_dev_state_file() -> None:
  script = (OPS / "quantx.ps1").read_text(encoding="utf-8")
  ordinary_up = script.split("function Invoke-Up", 1)[1].split(
    "function Invoke-MonitorUp",
    1,
  )[0]
  monitor_up = script.split("function Invoke-MonitorUp", 1)[1].split(
    "function Invoke-Down",
    1,
  )[0]
  monitor_state = script.split("function Read-MonitorState", 1)[1].split(
    "function Get-DevRuntimeConfiguration",
    1,
  )[0]

  assert '$MonitorStateFile = Join-Path $MonitorRuntime "dev-process.json"' in script
  assert "Start-Process" in monitor_up
  assert '"quantx_monitor.main"' in monitor_up
  assert "Write-MonitorState -Entry $entry" in monitor_up
  assert "$MonitorStateFile" in monitor_state
  for port in ("8080", "$ApiPort", "$MarketGatewayPort", "5250", "5251"):
    assert (
      port
      in ordinary_up.split("Assert-PortsAvailable -Ports @(", 1)[1].split(
        ")",
        1,
      )[0]
    )
