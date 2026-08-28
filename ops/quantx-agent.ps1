[CmdletBinding()]
param(
  [Parameter(Position = 0)]
  [ValidateSet(
    "up",
    "down",
    "status",
    "logs",
    "restart",
    "doctor",
    "enroll",
    "internal-run"
  )]
  [string]$Command = "status",

  [ValidateSet("dev")]
  [string]$Environment = "dev",

  [string]$AccountId = "",

  [string]$ApiUrl = "",

  [string]$Code = "",

  [ValidateRange(1, 5000)]
  [int]$Tail = 100
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ($PSVersionTable.PSEdition -eq "Core" -and -not $IsWindows) {
  throw "quantx-agent.ps1 only supports the Windows QMT execution node."
}

$Root = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$Runtime = Join-Path $Root ".runtime"
$StateDirectory = Join-Path $Runtime "state"
$LogDirectory = Join-Path $Runtime "logs"
$AgentStateDirectory = Join-Path $Runtime "qmt-agent"
$CertificateDirectory = Join-Path $Runtime "certs"
$DefaultCaFile = Join-Path $CertificateDirectory "mac-dev-root.crt"
$ManagedStateFile = Join-Path $StateDirectory "qmt-agent.json"
$SupervisorStateFile = Join-Path $StateDirectory "qmt-agent-supervisor.json"
$LaunchLogFile = Join-Path $LogDirectory "qmt-agent-launch.log"
$TaskName = "QuantX-Dev-QmtAgent"
$DefaultQmtCondaEnvironment = "xtquant-demo"

function Ensure-AgentDirectories {
  foreach ($path in @(
    $Runtime,
    $StateDirectory,
    $LogDirectory,
    $AgentStateDirectory,
    $CertificateDirectory
  )) {
    if (-not (Test-Path -LiteralPath $path -PathType Container)) {
      New-Item -ItemType Directory -Path $path -Force | Out-Null
    }
  }
}

function ConvertFrom-ListSetting {
  param([AllowEmptyString()][string]$Value)

  $setting = $Value.Trim()
  if (-not $setting) {
    return @()
  }
  if ($setting.StartsWith("[")) {
    try {
      return @(
        ($setting | ConvertFrom-Json) |
          ForEach-Object { ([string]$_).Trim() } |
          Where-Object { $_ }
      )
    } catch {
      throw "Invalid JSON list setting."
    }
  }
  return @(
    $setting.Split(",") |
      ForEach-Object { $_.Trim() } |
      Where-Object { $_ }
  )
}

function Import-AgentEnvironment {
  $processOverrides = [Collections.Generic.HashSet[string]]::new(
    [StringComparer]::OrdinalIgnoreCase
  )
  foreach ($existingName in (
    [Environment]::GetEnvironmentVariables("Process").Keys
  )) {
    $null = $processOverrides.Add([string]$existingName)
  }
  foreach ($file in @(
    (Join-Path $Root "apps\api\.env"),
    (Join-Path $Root "apps\api\.env.development")
  )) {
    if (-not (Test-Path -LiteralPath $file -PathType Leaf)) {
      continue
    }
    foreach ($line in Get-Content -LiteralPath $file) {
      $value = $line.Trim()
      if (-not $value -or $value.StartsWith("#") -or -not $value.Contains("=")) {
        continue
      }
      $name, $setting = $value.Split("=", 2)
      $name = $name.Trim()
      if (-not $name -or $processOverrides.Contains($name)) {
        continue
      }
      [Environment]::SetEnvironmentVariable(
        $name,
        $setting.Trim().Trim('"').Trim("'"),
        "Process"
      )
    }
  }
}

function Resolve-QmtPython {
  $configured = [Environment]::GetEnvironmentVariable("QUANTX_QMT_PYTHON_EXE")
  if ($configured) {
    $resolved = [System.IO.Path]::GetFullPath($configured)
    if (-not (Test-Path -LiteralPath $resolved -PathType Leaf)) {
      throw "Configured QMT Python does not exist: $resolved"
    }
    return $resolved
  }

  $candidates = @()
  $activePrefix = [Environment]::GetEnvironmentVariable("CONDA_PREFIX")
  if (
    $activePrefix -and
    (Split-Path -Leaf $activePrefix) -eq $DefaultQmtCondaEnvironment
  ) {
    $candidates += Join-Path $activePrefix "python.exe"
  }
  $userProfile = [Environment]::GetEnvironmentVariable("USERPROFILE")
  if ($userProfile) {
    $candidates += @(
      (Join-Path $userProfile (
        "miniconda3\envs\$DefaultQmtCondaEnvironment\python.exe"
      )),
      (Join-Path $userProfile (
        "anaconda3\envs\$DefaultQmtCondaEnvironment\python.exe"
      ))
    )
  }
  foreach ($candidate in $candidates) {
    if (Test-Path -LiteralPath $candidate -PathType Leaf) {
      return [System.IO.Path]::GetFullPath($candidate)
    }
  }
  throw (
    "QMT Conda environment '$DefaultQmtCondaEnvironment' was not found. " +
    "Set QUANTX_QMT_PYTHON_EXE to its python.exe."
  )
}

function Get-QmtPythonPath {
  return @(
    (Join-Path $Root "apps\qmt-agent\src"),
    (Join-Path $Root "packages\contracts\src")
  ) -join [System.IO.Path]::PathSeparator
}

function Resolve-LiveAccount {
  param([AllowEmptyString()][string]$RequestedAccountId)

  $requested = $RequestedAccountId.Trim()
  if ($requested) {
    return $requested
  }
  $candidates = [Collections.Generic.HashSet[string]]::new(
    [StringComparer]::OrdinalIgnoreCase
  )
  foreach ($name in @(
    "QMT_ACCOUNT_WHITELIST",
    "REAL_TRADING_ACCOUNT_ALLOWLIST",
    "AUTH_BOOTSTRAP_ACCOUNT_IDS"
  )) {
    $setting = [Environment]::GetEnvironmentVariable($name)
    if (-not $setting) {
      continue
    }
    foreach ($value in @(ConvertFrom-ListSetting -Value $setting)) {
      $null = $candidates.Add($value)
    }
  }
  if ($candidates.Count -ne 1) {
    throw (
      "QMT Agent live startup requires exactly one configured account; " +
      "pass -AccountId when the environment is ambiguous."
    )
  }
  return [string](@($candidates)[0])
}

function Set-AgentLiveEnvironment {
  param([Parameter(Mandatory = $true)][string]$LiveAccount)

  $env:ENV = "testing"
  $env:QMT_AGENT_MODE = "live"
  $env:QMT_ACCOUNT_WHITELIST = $LiveAccount
  $env:ENABLE_REAL_TRADING = "true"
  $env:QMT_REAL_TRADING_ENABLED = "true"
  $env:PYTHONPATH = Get-QmtPythonPath
  $env:PYTHONUTF8 = "1"
  $env:PYTHONIOENCODING = "utf-8"
  $env:QUANTX_AGENT_STATE_DIR = $AgentStateDirectory
}

function Mask-Identifier {
  param(
    [AllowEmptyString()][string]$Value,
    [int]$VisibleSuffix = 4
  )

  $normalized = $Value.Trim()
  if (-not $normalized) {
    return "-"
  }
  if ($normalized.Length -le $VisibleSuffix) {
    return "*" * $normalized.Length
  }
  return "***" + $normalized.Substring($normalized.Length - $VisibleSuffix)
}

function Get-DeviceMetadata {
  $path = Join-Path $AgentStateDirectory "device.json"
  if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
    throw "QMT Agent device metadata is missing: $path"
  }
  $metadata = Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
  $apiUrl = ([string]$metadata.api_url).Trim().TrimEnd("/")
  $deviceId = ([string]$metadata.device_id).Trim()
  if (-not $apiUrl -or -not $deviceId) {
    throw "QMT Agent device metadata is incomplete."
  }
  try {
    $uri = [uri]$apiUrl
  } catch {
    throw "QMT Agent api_url is invalid."
  }
  if (
    -not $uri.IsAbsoluteUri -or
    $uri.Scheme -notin @("http", "https") -or
    $uri.AbsolutePath -ne "/" -or
    $uri.Query -or
    $uri.Fragment
  ) {
    throw "QMT Agent api_url must be an HTTP(S) service root."
  }
  return [pscustomobject]@{
    ApiUrl = $apiUrl
    DeviceId = $deviceId
  }
}

function Write-JsonAtomic {
  param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)][object]$Payload
  )

  $temporary = "$Path.tmp"
  $Payload | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $temporary -Encoding UTF8
  Move-Item -LiteralPath $temporary -Destination $Path -Force
}

function Read-JsonState {
  param([Parameter(Mandatory = $true)][string]$Path)

  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
    return $null
  }
  try {
    return Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
  } catch {
    return $null
  }
}

function Get-SupervisorProcess {
  param([object]$ManagedState)

  if ($null -eq $ManagedState -or -not $ManagedState.supervisorPid) {
    return $null
  }
  if (-not $ManagedState.supervisorStartedAt) {
    return $null
  }
  $process = Get-Process -Id ([int]$ManagedState.supervisorPid) `
    -ErrorAction SilentlyContinue
  if (-not $process) {
    return $null
  }
  $expectedStartedAt = if (
    $ManagedState.supervisorStartedAt -is [datetime]
  ) {
    ([datetime]$ManagedState.supervisorStartedAt).ToUniversalTime()
  } else {
    [datetime]::Parse(
      [string]$ManagedState.supervisorStartedAt
    ).ToUniversalTime()
  }
  if (
    [math]::Abs(
      ($process.StartTime.ToUniversalTime() - $expectedStartedAt).TotalSeconds
    ) -gt 1.0
  ) {
    return $null
  }
  $cim = Get-CimInstance Win32_Process `
    -Filter "ProcessId = $($process.Id)" `
    -ErrorAction SilentlyContinue
  $commandLine = if ($cim) { [string]$cim.CommandLine } else { "" }
  if (
    -not $commandLine.Contains("supervise_process.py") -or
    -not $commandLine.Contains("--name qmt-agent")
  ) {
    return $null
  }
  return $process
}

function Assert-QmtRuntimeImports {
  param([Parameter(Mandatory = $true)][string]$Python)

  & $Python -c "import httpx, uvicorn, websockets, xtquant"
  if ($LASTEXITCODE -ne 0) {
    throw (
      "The QMT Python runtime cannot import httpx, uvicorn, websockets, " +
      "and xtquant."
    )
  }
}

function Test-PublicApi {
  param([Parameter(Mandatory = $true)][string]$BaseUrl)

  $response = Invoke-WebRequest `
    -Uri "$($BaseUrl.TrimEnd('/'))/health/live" `
    -UseBasicParsing `
    -TimeoutSec 8
  if ($response.StatusCode -ne 200) {
    throw "Mac public API is not live: HTTP $($response.StatusCode)"
  }
}

function Assert-AgentTransportTrust {
  param([Parameter(Mandatory = $true)][string]$BaseUrl)

  $uri = [uri]$BaseUrl
  if ($uri.Scheme -ne "https") {
    Remove-Item Env:QUANTX_AGENT_CA_FILE -ErrorAction SilentlyContinue
    Remove-Item Env:SSL_CERT_FILE -ErrorAction SilentlyContinue
    return
  }
  if (-not (Test-Path -LiteralPath $DefaultCaFile -PathType Leaf)) {
    throw (
      "HTTPS Agent registration requires the Mac Caddy public root CA at " +
      "$DefaultCaFile."
    )
  }
  $env:QUANTX_AGENT_CA_FILE = $DefaultCaFile
  $env:SSL_CERT_FILE = $DefaultCaFile
}

function Test-QmtPythonApi {
  param(
    [Parameter(Mandatory = $true)][string]$Python,
    [Parameter(Mandatory = $true)][string]$BaseUrl
  )

  & $Python -c (
    "import httpx,sys;" +
    "from quantx_qmt_agent.endpoints import httpx_verify;" +
    "client=httpx.Client(timeout=8,follow_redirects=False,trust_env=False," +
    "verify=httpx_verify(sys.argv[1]));" +
    "response=client.get(sys.argv[1]+'/health/live');" +
    "response.raise_for_status();client.close()"
  ) ($BaseUrl.TrimEnd("/"))
  if ($LASTEXITCODE -ne 0) {
    throw "QMT Python cannot verify or reach the Mac public API."
  }
}

function Invoke-InternalRun {
  Ensure-AgentDirectories
  try {
    Import-AgentEnvironment
    $liveAccount = Resolve-LiveAccount -RequestedAccountId $AccountId
    Set-AgentLiveEnvironment -LiveAccount $liveAccount
    $python = Resolve-QmtPython
    Assert-QmtRuntimeImports -Python $python
    $device = Get-DeviceMetadata
    Assert-AgentTransportTrust -BaseUrl $device.ApiUrl
    Test-PublicApi -BaseUrl $device.ApiUrl
    Test-QmtPythonApi -Python $python -BaseUrl $device.ApiUrl
    & $python -m quantx_qmt_agent.main status *> $null
    if ($LASTEXITCODE -ne 0) {
      throw "The interactive Windows session cannot read QMT Agent credentials."
    }
    Add-Content -LiteralPath $LaunchLogFile -Encoding UTF8 -Value (
      "{0} starting managed QMT Agent device={1} api={2} account={3}" -f
      [datetime]::UtcNow.ToString("o"),
      (Mask-Identifier -Value $device.DeviceId),
      $device.ApiUrl,
      (Mask-Identifier -Value $liveAccount)
    )
    $supervisorArguments = @(
      (Join-Path $Root "ops\supervise_process.py"),
      "--name",
      "qmt-agent",
      "--state-dir",
      $StateDirectory,
      "--",
      $python,
      "-m",
      "quantx_qmt_agent.main",
      "run",
      "--mode",
      "live"
    )
    $previousErrorActionPreference = $ErrorActionPreference
    try {
      $ErrorActionPreference = "Continue"
      $supervisorOutput = @(& $python @supervisorArguments 2>&1)
      $supervisorExitCode = $LASTEXITCODE
    } finally {
      $ErrorActionPreference = $previousErrorActionPreference
    }
    foreach ($line in $supervisorOutput) {
      Add-Content -LiteralPath $LaunchLogFile -Encoding UTF8 -Value (
        "supervisor: $line"
      )
    }
    if ($supervisorExitCode -ne 0) {
      throw "QMT Agent supervisor exited with code $supervisorExitCode."
    }
  } catch {
    Add-Content -LiteralPath $LaunchLogFile -Encoding UTF8 -Value (
      "{0} launch failed: {1}" -f
      [datetime]::UtcNow.ToString("o"),
      $_.Exception.Message
    )
    throw
  }
}

function Register-AgentTask {
  $powershell = Join-Path $PSHOME "powershell.exe"
  if (-not (Test-Path -LiteralPath $powershell -PathType Leaf)) {
    $powershell = (Get-Command powershell.exe -ErrorAction Stop).Source
  }
  $arguments = (
    '-NoProfile -NonInteractive -ExecutionPolicy Bypass ' +
    '-File "{0}" internal-run -Environment dev' -f $PSCommandPath
  )
  if ($AccountId.Trim()) {
    $arguments += ' -AccountId "{0}"' -f $AccountId.Trim()
  }
  $action = New-ScheduledTaskAction `
    -Execute $powershell `
    -Argument $arguments `
    -WorkingDirectory $Root
  $principal = New-ScheduledTaskPrincipal `
    -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) `
    -LogonType Interactive `
    -RunLevel Limited
  $settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([timespan]::Zero) `
    -MultipleInstances IgnoreNew
  $definition = New-ScheduledTask `
    -Action $action `
    -Principal $principal `
    -Settings $settings
  Register-ScheduledTask `
    -TaskName $TaskName `
    -InputObject $definition `
    -Force | Out-Null
}

function Invoke-Up {
  Ensure-AgentDirectories
  Import-AgentEnvironment
  $liveAccount = Resolve-LiveAccount -RequestedAccountId $AccountId
  $device = Get-DeviceMetadata
  if ($ApiUrl.Trim() -and $device.ApiUrl -ne $ApiUrl.Trim().TrimEnd("/")) {
    throw (
      "Registered api_url is '$($device.ApiUrl)', expected " +
      "'$($ApiUrl.Trim().TrimEnd('/'))'. Re-enroll before startup."
    )
  }
  $existingState = Read-JsonState -Path $ManagedStateFile
  $existingProcess = Get-SupervisorProcess -ManagedState $existingState
  $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
  if ($existingProcess -and $task -and $task.State -eq "Running") {
    Write-Host "QMT Agent is already running under the managed task."
    Invoke-Status
    return
  }
  if ($task -and $task.State -eq "Running") {
    throw "The QMT Agent task is running but its supervisor identity is unknown."
  }

  $python = Resolve-QmtPython
  $previousPythonPath = $env:PYTHONPATH
  try {
    $env:PYTHONPATH = Get-QmtPythonPath
    Assert-QmtRuntimeImports -Python $python
  } finally {
    $env:PYTHONPATH = $previousPythonPath
  }
  Test-PublicApi -BaseUrl $device.ApiUrl
  Assert-AgentTransportTrust -BaseUrl $device.ApiUrl
  Test-QmtPythonApi -Python $python -BaseUrl $device.ApiUrl
  if (-not $task -or $AccountId.Trim()) {
    Register-AgentTask
  }
  Remove-Item -LiteralPath $SupervisorStateFile -Force `
    -ErrorAction SilentlyContinue
  Start-ScheduledTask -TaskName $TaskName

  $deadline = [datetime]::UtcNow.AddSeconds(45)
  do {
    Start-Sleep -Milliseconds 500
    $supervisor = Read-JsonState -Path $SupervisorStateFile
    if ($supervisor -and [string]$supervisor.status -eq "RUNNING") {
      $process = Get-Process -Id ([int]$supervisor.supervisorPid) `
        -ErrorAction SilentlyContinue
      if ($process) {
        $state = [pscustomobject]@{
          schemaVersion = 1
          status = "RUNNING"
          supervisorPid = $process.Id
          supervisorStartedAt = $process.StartTime.ToUniversalTime().ToString("o")
          taskName = $TaskName
          deviceId = Mask-Identifier -Value $device.DeviceId
          apiUrl = $device.ApiUrl
          accountId = Mask-Identifier -Value $liveAccount
          mode = "live"
          startedAt = [datetime]::UtcNow.ToString("o")
        }
        Write-JsonAtomic -Path $ManagedStateFile -Payload $state
        Write-Host (
          "Started managed QMT Agent supervisor PID=$($process.Id) " +
          "api_url=$($device.ApiUrl) account=$(Mask-Identifier $liveAccount)"
        ) -ForegroundColor Green
        return
      }
    }
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($task -and $task.State -ne "Running") {
      break
    }
  } while ([datetime]::UtcNow -lt $deadline)

  $details = if (Test-Path -LiteralPath $LaunchLogFile -PathType Leaf) {
    (Get-Content -LiteralPath $LaunchLogFile -Tail 20) -join [Environment]::NewLine
  } else {
    "No launch log was produced."
  }
  throw "QMT Agent did not start in the interactive session.`n$details"
}

function Invoke-Down {
  Ensure-AgentDirectories
  $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
  $state = Read-JsonState -Path $ManagedStateFile
  $process = Get-SupervisorProcess -ManagedState $state
  if ($task -and $task.State -eq "Running") {
    Stop-ScheduledTask -TaskName $TaskName
  }
  $deadline = [datetime]::UtcNow.AddSeconds(15)
  while ($process -and [datetime]::UtcNow -lt $deadline) {
    Start-Sleep -Milliseconds 500
    $process = Get-SupervisorProcess -ManagedState $state
  }
  if ($process) {
    Stop-Process -Id $process.Id -Force
    $process.WaitForExit(5000) | Out-Null
  }
  if ($state) {
    $state.status = "STOPPED"
    $state | Add-Member `
      -NotePropertyName stoppedAt `
      -NotePropertyValue ([datetime]::UtcNow.ToString("o")) `
      -Force
    Write-JsonAtomic -Path $ManagedStateFile -Payload $state
  }
  Write-Host "QMT Agent is stopped." -ForegroundColor Green
}

function Invoke-Status {
  Ensure-AgentDirectories
  $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
  $state = Read-JsonState -Path $ManagedStateFile
  $supervisor = Read-JsonState -Path $SupervisorStateFile
  $process = Get-SupervisorProcess -ManagedState $state
  $taskState = if ($task) { [string]$task.State } else { "NotRegistered" }
  $processState = if ($process) { "RUNNING" } else { "STOPPED" }
  $childPid = if ($supervisor -and $supervisor.childPid) {
    [int]$supervisor.childPid
  } else {
    0
  }
  $device = $null
  try {
    $device = Get-DeviceMetadata
  } catch {
    Write-Warning $_.Exception.Message
  }
  Write-Host (
    "Task=$taskState Supervisor=$processState " +
    "PID=$(if ($process) { $process.Id } else { 0 }) ChildPID=$childPid"
  )
  if ($device) {
    Write-Host (
      "Registration api_url=$($device.ApiUrl) " +
      "device=$(Mask-Identifier -Value $device.DeviceId)"
    )
    try {
      $runtime = Invoke-RestMethod `
        -Uri "$($device.ApiUrl)/health/runtime/live-trading" `
        -TimeoutSec 8
      Write-Host (
        "Server liveTrading=$($runtime.liveTrading.status) " +
        "agentStatus=$($runtime.liveTrading.agentStatus) " +
        "marketStream=$($runtime.liveTrading.marketStreamStatus)"
      )
    } catch {
      Write-Warning "Mac runtime status is unavailable: $($_.Exception.Message)"
    }
  }
}

function Invoke-Logs {
  Ensure-AgentDirectories
  foreach ($path in @(
    $LaunchLogFile,
    (Join-Path $LogDirectory "qmt-agent.stdout.log"),
    (Join-Path $LogDirectory "qmt-agent.stderr.log")
  )) {
    Write-Host "== $path =="
    if (Test-Path -LiteralPath $path -PathType Leaf) {
      Get-Content -LiteralPath $path -Tail $Tail
    } else {
      Write-Host "(not created)"
    }
  }
}

function Invoke-Doctor {
  Ensure-AgentDirectories
  Import-AgentEnvironment
  $python = Resolve-QmtPython
  $previousPythonPath = $env:PYTHONPATH
  try {
    $env:PYTHONPATH = Get-QmtPythonPath
    Assert-QmtRuntimeImports -Python $python
  } finally {
    $env:PYTHONPATH = $previousPythonPath
  }
  $device = Get-DeviceMetadata
  Assert-AgentTransportTrust -BaseUrl $device.ApiUrl
  Test-PublicApi -BaseUrl $device.ApiUrl
  Test-QmtPythonApi -Python $python -BaseUrl $device.ApiUrl
  $liveAccount = Resolve-LiveAccount -RequestedAccountId $AccountId
  Write-Host "QMT runtime=$python" -ForegroundColor Green
  Write-Host (
    "Registration api_url=$($device.ApiUrl) " +
    "device=$(Mask-Identifier -Value $device.DeviceId) " +
    "account=$(Mask-Identifier -Value $liveAccount)"
  ) -ForegroundColor Green
  if ($env:SSH_CONNECTION) {
    Write-Host (
      "Credential Manager check is deferred to the interactive Agent task."
    ) -ForegroundColor Yellow
  } else {
    Set-AgentLiveEnvironment -LiveAccount $liveAccount
    & $python -m quantx_qmt_agent.main status
    if ($LASTEXITCODE -ne 0) {
      throw "QMT Agent credential check failed."
    }
  }
}

function Invoke-Enroll {
  if (-not $ApiUrl.Trim() -or -not $Code.Trim()) {
    throw "enroll requires -ApiUrl and -Code."
  }
  if ($env:SSH_CONNECTION) {
    throw "Enrollment must run in the local Windows console for Credential Manager."
  }
  Ensure-AgentDirectories
  Import-AgentEnvironment
  $python = Resolve-QmtPython
  $env:PYTHONPATH = Get-QmtPythonPath
  $env:QUANTX_AGENT_STATE_DIR = $AgentStateDirectory
  & $python -m quantx_qmt_agent.main enroll --api-url $ApiUrl --code $Code
  if ($LASTEXITCODE -ne 0) {
    throw "QMT Agent enrollment failed."
  }
}

Ensure-AgentDirectories
switch ($Command) {
  "up" { Invoke-Up }
  "down" { Invoke-Down }
  "status" { Invoke-Status }
  "logs" { Invoke-Logs }
  "restart" {
    Invoke-Down
    Invoke-Up
  }
  "doctor" { Invoke-Doctor }
  "enroll" { Invoke-Enroll }
  "internal-run" { Invoke-InternalRun }
}
