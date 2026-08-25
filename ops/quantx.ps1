[CmdletBinding()]
param(
  [Parameter(Position = 0)]
  [ValidateSet(
    "up",
    "down",
    "status",
    "logs",
    "bootstrap",
    "install",
    "uninstall",
    "doctor",
    "backup",
    "restore-verify",
    "migrate",
    "verify",
    "rollback",
    "agent-mode"
  )]
  [string]$Command = "status",

  [ValidateSet("dev", "production")]
  [string]$Environment = "dev",

  [ValidateSet("web", "full")]
  [string]$Profile = "full",

  [string]$Component = "",

  [ValidateRange(1, 5000)]
  [int]$Tail = 100,

  [ValidateSet("data-only", "paper", "live")]
  [string]$Mode = "data-only",

  [string]$AccountId = "",

  [string]$ConfirmLive = "",

  [string]$Reason = "",

  [string]$BackupPath = "",

  [string]$ReleasePath = "",

  [switch]$SkipExternal,

  [switch]$StampExisting
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$script:ModeWasExplicitlySpecified = $PSBoundParameters.ContainsKey("Mode")

function Resolve-PhysicalDirectoryPath {
  param([Parameter(Mandatory = $true)][string]$Path)

  $resolved = [System.IO.Path]::GetFullPath($Path)
  $visited = [Collections.Generic.HashSet[string]]::new(
    [StringComparer]::OrdinalIgnoreCase
  )
  for ($hop = 0; $hop -lt 16; $hop++) {
    if (-not $visited.Add($resolved)) {
      throw "Directory link cycle detected while resolving '$Path'."
    }
    $item = Get-Item -LiteralPath $resolved -Force
    if (-not ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
      return $resolved
    }

    $targetProperty = $item.PSObject.Properties["Target"]
    [array]$targets = if ($targetProperty -and $targetProperty.Value) {
      @($targetProperty.Value)
    } else {
      @()
    }
    if ($targets.Count -ne 1 -or -not ([string]$targets[0]).Trim()) {
      throw "Directory link target is unavailable for '$resolved'."
    }

    $target = ([string]$targets[0]).Trim()
    if (-not [System.IO.Path]::IsPathRooted($target)) {
      $target = Join-Path (Split-Path -Parent $resolved) $target
    }
    $resolved = [System.IO.Path]::GetFullPath($target)
  }
  throw "Directory link chain is too deep while resolving '$Path'."
}

$InvokedScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = Resolve-PhysicalDirectoryPath -Path (
  Join-Path $InvokedScriptRoot ".."
)
$ScriptRoot = Join-Path $Root "ops"
$Runtime = Join-Path $Root ".runtime"
$StateDirectory = Join-Path $Runtime "state"
$LogDirectory = Join-Path $Runtime "logs"
$StateFile = Join-Path $StateDirectory "dev-processes.json"
$ToolsDirectory = Join-Path $Runtime "tools"
$ServiceDirectory = Join-Path $Runtime "services"
$BackupDirectory = Join-Path $Runtime "backups"
$ReleaseDirectory = Join-Path $Runtime "releases"
$CurrentReleaseLink = Join-Path $Runtime "current"
$ReleaseStateFile = Join-Path $Runtime "release-state.json"
$ProductionConfigFile = Join-Path $Runtime "config\.env.production"
$AgentModeFile = Join-Path $StateDirectory "qmt-agent-mode.json"
$DefaultPrefectApiUrl = "http://192.168.101.4:30420/api"
$DefaultPrefectWorkerPool = "quantx-pool"
$ApiPort = 18081
$MarketGatewayPort = 18082
$AgentWebSocketPingTimeoutSeconds = 960
$script:RuntimeProfile = ""
$script:RuntimeAgentMode = ""
$script:RuntimeConfiguredAccount = ""
$script:RuntimeQmtLaunchState = "NOT_REQUESTED"
$script:RuntimeQmtReasonCode = ""
$script:RuntimeQmtLaunchStartedAt = ""
$script:RuntimeLiveTradingEnabled = $false

function Ensure-RuntimeDirectories {
  foreach ($path in @(
    $Runtime,
    $StateDirectory,
    $LogDirectory,
    $ToolsDirectory,
    $BackupDirectory,
    $ReleaseDirectory,
    (Split-Path -Parent $ProductionConfigFile)
  )) {
    if (-not (Test-Path -LiteralPath $path)) {
      New-Item -ItemType Directory -Path $path -Force | Out-Null
    }
  }
}

function Resolve-Python {
  param([switch]$Qmt)

  $configured = if ($Qmt) {
    [Environment]::GetEnvironmentVariable("QUANTX_QMT_PYTHON_EXE")
  } else {
    [Environment]::GetEnvironmentVariable("QUANTX_PYTHON_EXE")
  }
  if ($configured) {
    $resolved = [System.IO.Path]::GetFullPath($configured)
    if (-not (Test-Path -LiteralPath $resolved -PathType Leaf)) {
      throw "Configured Python executable does not exist: $resolved"
    }
    return $resolved
  }
  if (
    -not $Qmt -and
    $Environment -eq "production" -and
    (Test-Path -LiteralPath $CurrentReleaseLink -PathType Container)
  ) {
    $releasePython = Join-Path $CurrentReleaseLink ".venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $releasePython -PathType Leaf)) {
      throw "The current production release has no server virtual environment."
    }
    return [System.IO.Path]::GetFullPath($releasePython)
  }
  if ($Qmt) {
    $sharedPython = [Environment]::GetEnvironmentVariable("QUANTX_PYTHON_EXE")
    if ($sharedPython) {
      $resolved = [System.IO.Path]::GetFullPath($sharedPython)
      if (-not (Test-Path -LiteralPath $resolved -PathType Leaf)) {
        throw "Configured Python executable does not exist: $resolved"
      }
      return $resolved
    }
  }
  $condaEnvironment = [Environment]::GetEnvironmentVariable("CONDA_ENV_NAME")
  if ($condaEnvironment) {
    $environmentCandidates = @()
    $activeCondaPrefix = [Environment]::GetEnvironmentVariable("CONDA_PREFIX")
    if (
      $activeCondaPrefix -and
      (Split-Path -Leaf $activeCondaPrefix) -eq $condaEnvironment
    ) {
      $environmentCandidates += Join-Path $activeCondaPrefix "python.exe"
    }
    $userProfile = [Environment]::GetEnvironmentVariable("USERPROFILE")
    if ($userProfile) {
      $environmentCandidates += @(
        (Join-Path $userProfile "miniconda3\envs\$condaEnvironment\python.exe"),
        (Join-Path $userProfile "anaconda3\envs\$condaEnvironment\python.exe")
      )
    }
    $programData = [Environment]::GetEnvironmentVariable("ProgramData")
    if ($programData) {
      $environmentCandidates += @(
        (Join-Path $programData "miniconda3\envs\$condaEnvironment\python.exe"),
        (Join-Path $programData "anaconda3\envs\$condaEnvironment\python.exe")
      )
    }
    foreach ($candidate in $environmentCandidates) {
      if (Test-Path -LiteralPath $candidate -PathType Leaf) {
        return [System.IO.Path]::GetFullPath($candidate)
      }
    }

    $conda = Get-Command conda -ErrorAction SilentlyContinue
    $condaCommand = if ($conda) {
      # Get-Command may return an ApplicationInfo, AliasInfo, or FunctionInfo
      # depending on whether the current shell loaded Conda's PowerShell hook.
      # All of those command types can be invoked reliably by name.
      $conda.Name
    } else {
      $condaExecutable = [Environment]::GetEnvironmentVariable("CONDA_EXE")
      if (
        $condaExecutable -and
        (Test-Path -LiteralPath $condaExecutable -PathType Leaf)
      ) {
        [System.IO.Path]::GetFullPath($condaExecutable)
      } else {
        $null
      }
    }
    if ($condaCommand) {
      try {
        $environments = (& $condaCommand env list --json | ConvertFrom-Json).envs
        foreach ($environmentPath in @($environments)) {
          if ((Split-Path -Leaf $environmentPath) -ne $condaEnvironment) {
            continue
          }
          $candidate = Join-Path $environmentPath "python.exe"
          if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return [System.IO.Path]::GetFullPath($candidate)
          }
        }
      } catch {
        throw (
          "Failed to resolve configured Conda environment " +
          "'$condaEnvironment': $($_.Exception.Message)"
        )
      }
    }
    throw (
      "Configured Conda environment '$condaEnvironment' was not found. " +
      "Set QUANTX_PYTHON_EXE to an explicit interpreter."
    )
  }
  $python = Get-Command python -ErrorAction SilentlyContinue
  if (-not $python) {
    throw "Python was not found. Set QUANTX_PYTHON_EXE."
  }
  return $python.Source
}

function Resolve-AiRuntimePython {
  $configured = [Environment]::GetEnvironmentVariable(
    "QUANTX_AI_RUNTIME_PYTHON_EXE"
  )
  if ($configured) {
    $resolved = [System.IO.Path]::GetFullPath($configured)
    if (-not (Test-Path -LiteralPath $resolved -PathType Leaf)) {
      throw "Configured AI Runtime Python executable does not exist: $resolved"
    }
    return $resolved
  }
  if ($Environment -ne "production") {
    $workspacePython = Join-Path $Root ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $workspacePython -PathType Leaf) {
      return [System.IO.Path]::GetFullPath($workspacePython)
    }
  }
  return Resolve-Python
}

function Resolve-Node {
  $node = Get-Command node -ErrorAction SilentlyContinue
  if (-not $node) {
    throw "Node.js was not found in PATH."
  }
  return $node.Source
}

function Get-WorkspacePythonPath {
  if ($Environment -eq "production") {
    return ""
  }
  $entries = @(
    (Join-Path $Root "apps\api\src"),
    (Join-Path $Root "apps\ai-runtime\src"),
    (Join-Path $Root "apps\engine\src"),
    (Join-Path $Root "apps\worker\src"),
    (Join-Path $Root "packages\contracts\src"),
    (Join-Path $Root "packages\domain\src"),
    (Join-Path $Root "packages\application\src"),
    (Join-Path $Root "packages\infrastructure\src")
  )
  return ($entries -join [System.IO.Path]::PathSeparator)
}

function Get-QmtAgentPythonPath {
  param([string]$ServiceRoot = "")

  if ($Environment -eq "production") {
    $releaseRoot = if ($ServiceRoot.Trim()) {
      $ServiceRoot
    } elseif (Test-Path -LiteralPath $CurrentReleaseLink -PathType Container) {
      $CurrentReleaseLink
    } else {
      $Root
    }
    return (Join-Path $releaseRoot "qmt-site-packages")
  }
  $entries = @(
    (Join-Path $Root "apps\qmt-agent\src"),
    (Join-Path $Root "packages\contracts\src")
  )
  return ($entries -join [System.IO.Path]::PathSeparator)
}

function Initialize-PythonEnvironment {
  # Redirected Windows process streams otherwise inherit the active ANSI code
  # page, which makes Chinese audit messages unreadable when logs are consumed
  # as UTF-8.
  $env:PYTHONUTF8 = "1"
  $env:PYTHONIOENCODING = "utf-8"
}

function Get-PrefectApiUrl {
  $configured = if ($env:PREFECT_API_URL) {
    $env:PREFECT_API_URL.Trim()
  } else {
    $DefaultPrefectApiUrl
  }
  $normalized = $configured.TrimEnd("/")
  if (-not $normalized.EndsWith("/api")) {
    $normalized += "/api"
  }
  try {
    $uri = [uri]$normalized
  } catch {
    throw "PREFECT_API_URL is not a valid absolute URL: $configured"
  }
  if (-not $uri.IsAbsoluteUri -or $uri.Scheme -notin @("http", "https")) {
    throw "PREFECT_API_URL must be an absolute HTTP(S) URL: $configured"
  }
  return $normalized
}

function Get-PrefectWorkerPool {
  $pool = if ($env:PREFECT_WORKER_POOL) {
    $env:PREFECT_WORKER_POOL.Trim()
  } else {
    $DefaultPrefectWorkerPool
  }
  if (-not $pool) {
    throw "PREFECT_WORKER_POOL must not be empty."
  }
  return $pool
}

function Initialize-PrefectEnvironment {
  $env:PREFECT_API_URL = Get-PrefectApiUrl
  $env:PREFECT_WORKER_POOL = Get-PrefectWorkerPool
  $env:PREFECT_HOME = Join-Path $Runtime "prefect"
  Initialize-PythonEnvironment
  if (-not (Test-Path -LiteralPath $env:PREFECT_HOME)) {
    New-Item -ItemType Directory -Path $env:PREFECT_HOME -Force | Out-Null
  }
}

function Initialize-CaddyEnvironment {
  $env:XDG_CONFIG_HOME = Join-Path $Runtime "caddy-config"
  $env:XDG_DATA_HOME = Join-Path $Runtime "caddy-data"
  foreach ($path in @($env:XDG_CONFIG_HOME, $env:XDG_DATA_HOME)) {
    if (-not (Test-Path -LiteralPath $path)) {
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

function Test-QmtAgentEnrollment {
  param(
    [string]$Python,
    [string]$ServiceRoot = ""
  )

  $workspacePythonPath = $env:PYTHONPATH
  try {
    $env:PYTHONPATH = Get-QmtAgentPythonPath -ServiceRoot $ServiceRoot
    & $Python -m quantx_qmt_agent.main status *> $null
    return $LASTEXITCODE -eq 0
  } catch {
    return $false
  } finally {
    $env:PYTHONPATH = $workspacePythonPath
  }
}

function Assert-QmtAgentEnrollment {
  param(
    [string]$Python,
    [string]$ServiceRoot = ""
  )

  if (-not (Test-QmtAgentEnrollment -Python $Python -ServiceRoot $ServiceRoot)) {
    throw (
      "QMT Agent is not enrolled. Create a one-time code in the UI, then run " +
      "'python -m quantx_qmt_agent.main enroll --code <code>' with the QMT " +
      "Agent PYTHONPATH before starting the full profile."
    )
  }
}

function Import-QuantXEnvironment {
  $processOverrides = [Collections.Generic.HashSet[string]]::new(
    [StringComparer]::OrdinalIgnoreCase
  )
  foreach ($existingName in (
    [Environment]::GetEnvironmentVariables("Process").Keys
  )) {
    $null = $processOverrides.Add([string]$existingName)
  }
  $environmentName = if ($Environment -eq "dev") {
    "development"
  } else {
    $Environment
  }
  $configurationRoot = if (
    $Environment -eq "production" -and
    (Test-Path -LiteralPath $CurrentReleaseLink -PathType Container)
  ) {
    $CurrentReleaseLink
  } else {
    $Root
  }
  $files = @(
    (Join-Path $configurationRoot "apps\api\.env"),
    (Join-Path $configurationRoot "apps\api\.env.$environmentName")
  )
  if ($Environment -eq "production") {
    $files += $ProductionConfigFile
    $env:QUANTX_ENV_FILE = $ProductionConfigFile
    $env:QUANTX_ROOT = $configurationRoot
    $env:QUANTX_AGENT_STATE_DIR = Join-Path $Runtime "qmt-agent"
  }
  foreach ($file in $files) {
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
      $setting = $setting.Trim().Trim('"').Trim("'")
      [Environment]::SetEnvironmentVariable($name, $setting, "Process")
    }
  }
  Set-DevExternalDependencyHost
}

function Set-DevExternalDependencyHost {
  if ($Environment -ne "dev") {
    return
  }
  $hostName = [string]$env:QUANTX_DEV_EXTERNAL_DEPENDENCY_HOST
  $hostName = $hostName.Trim()
  if (-not $hostName) {
    return
  }
  if ($hostName -ieq "wsl") {
    $wsl = Get-Command wsl.exe -ErrorAction SilentlyContinue
    if (-not $wsl) {
      throw "WSL dependency routing requires wsl.exe."
    }
    $eth0 = (& $wsl.Source -e sh -lc "ip -4 -o addr show dev eth0" 2>$null) `
      -join " "
    $addressMatch = [regex]::Match(
      $eth0,
      "\binet\s+(?<address>\d{1,3}(?:\.\d{1,3}){3})/"
    )
    if (-not $addressMatch.Success) {
      throw "Could not resolve the WSL eth0 address."
    }
    $hostName = $addressMatch.Groups["address"].Value
  }
  foreach ($name in @(
    "DATABASE_URL",
    "REDIS_URL",
    "INFLUXDB_HOST",
    "PREFECT_API_URL"
  )) {
    $value = [Environment]::GetEnvironmentVariable($name, "Process")
    if (-not $value) {
      continue
    }
    try {
      $builder = [UriBuilder]::new($value)
      $builder.Host = $hostName
      [Environment]::SetEnvironmentVariable(
        $name,
        $builder.Uri.AbsoluteUri,
        "Process"
      )
    } catch {
      throw "$name cannot use the dev dependency host '$hostName'."
    }
  }
  $env:REDIS_HOST = $hostName
}

function Read-State {
  if (-not (Test-Path -LiteralPath $StateFile)) {
    return @()
  }
  $raw = Get-Content -LiteralPath $StateFile -Raw
  if (-not $raw.Trim()) {
    return @()
  }
  $parsed = ConvertFrom-Json -InputObject $raw
  if ($parsed -is [System.Array]) {
    foreach ($entry in $parsed) {
      $entry
    }
    return
  }
  return @($parsed)
}

function Write-State {
  param([object[]]$Processes)

  Ensure-RuntimeDirectories
  $serialized = ConvertTo-Json -InputObject @($Processes) -Depth 5
  $temporaryStateFile = Join-Path $StateDirectory (
    "dev-processes.{0}.{1}.tmp" -f $PID, [guid]::NewGuid().ToString("N")
  )
  $replacementBackupFile = Join-Path $StateDirectory (
    "dev-processes.{0}.{1}.replace-backup.tmp" -f
      $PID,
      [guid]::NewGuid().ToString("N")
  )
  try {
    [IO.File]::WriteAllText(
      $temporaryStateFile,
      $serialized,
      [Text.UTF8Encoding]::new($false)
    )
    if (Test-Path -LiteralPath $StateFile -PathType Leaf) {
      [IO.File]::Replace(
        $temporaryStateFile,
        $StateFile,
        $replacementBackupFile,
        $true
      )
    } else {
      [IO.File]::Move($temporaryStateFile, $StateFile)
    }
  } finally {
    if (Test-Path -LiteralPath $temporaryStateFile -PathType Leaf) {
      Remove-Item -LiteralPath $temporaryStateFile -Force
    }
    if (Test-Path -LiteralPath $replacementBackupFile -PathType Leaf) {
      Remove-Item -LiteralPath $replacementBackupFile -Force
    }
  }
}

function Get-DevRuntimeConfiguration {
  param([object[]]$Entries)

  $runtimeEntry = @(
    $Entries | Where-Object {
      $null -ne $_.PSObject.Properties["runtimeProfile"]
    }
  ) | Select-Object -First 1
  $agentEntry = @(
    $Entries | Where-Object { [string]$_.name -eq "qmt-agent" }
  ) | Select-Object -First 1

  $profile = if ($runtimeEntry) {
    [string]$runtimeEntry.runtimeProfile
  } elseif (@($Entries | Where-Object { [string]$_.name -eq "worker" }).Count -gt 0) {
    "full"
  } else {
    "web"
  }
  $agentMode = if ($runtimeEntry) {
    [string]$runtimeEntry.agentMode
  } elseif ($agentEntry) {
    $arguments = @($agentEntry.arguments | ForEach-Object { [string]$_ })
    $modeIndex = [array]::IndexOf($arguments, "--mode")
    if ($modeIndex -ge 0 -and $modeIndex + 1 -lt $arguments.Count) {
      $arguments[$modeIndex + 1]
    } else {
      "unknown"
    }
  } else {
    "unknown"
  }
  return [pscustomobject]@{
    profile = $profile
    agentMode = $agentMode
    configuredAccount = if ($runtimeEntry) {
      [string]$runtimeEntry.configuredAccount
    } else {
      ""
    }
    qmtLaunchState = if ($runtimeEntry) {
      [string]$runtimeEntry.qmtLaunchState
    } else {
      "UNKNOWN"
    }
    qmtReasonCode = if ($runtimeEntry) {
      [string]$runtimeEntry.qmtReasonCode
    } else {
      ""
    }
    qmtLaunchStartedAt = if ($runtimeEntry) {
      [string]$runtimeEntry.qmtLaunchStartedAt
    } else {
      ""
    }
    liveTradingEnabled = if ($runtimeEntry) {
      [bool]$runtimeEntry.liveTradingEnabled
    } else {
      $false
    }
  }
}

function Get-TrackedProcess {
  param([object]$Entry)

  $process = Get-Process -Id ([int]$Entry.pid) -ErrorAction SilentlyContinue
  if (-not $process) {
    return $null
  }
  try {
    $actual = $process.StartTime.ToUniversalTime()
    $expected = if ($Entry.startedAt -is [datetime]) {
      ([datetime]$Entry.startedAt).ToUniversalTime()
    } else {
      [datetime]::Parse(
        [string]$Entry.startedAt,
        [Globalization.CultureInfo]::InvariantCulture,
        [Globalization.DateTimeStyles]::RoundtripKind
      ).ToUniversalTime()
    }
    if ([math]::Abs(($actual - $expected).TotalSeconds) -gt 2) {
      return $null
    }
  } catch {
    return $null
  }
  return $process
}

function Get-PortOwner {
  param([int]$Port)

  $netstat = Join-Path $env:SystemRoot "System32\netstat.exe"
  $listenerLine = & $netstat -ano -p tcp |
    Where-Object {
      $_ -match "^\s*TCP\s+\S+:$Port\s+\S+\s+LISTENING\s+(?<pid>\d+)\s*$"
    } |
    Select-Object -First 1
  if (-not $listenerLine) {
    return $null
  }
  $null = $listenerLine -match "LISTENING\s+(?<pid>\d+)\s*$"
  $ownerPid = [int]$Matches.pid
  $processInfo = Get-Process -Id $ownerPid -ErrorAction SilentlyContinue
  $commandLineProperty = if ($processInfo) {
    $processInfo.PSObject.Properties["CommandLine"]
  } else {
    $null
  }
  $pathProperty = if ($processInfo) {
    $processInfo.PSObject.Properties["Path"]
  } else {
    $null
  }
  $commandLine = if ($commandLineProperty -and $commandLineProperty.Value) {
    [string]$commandLineProperty.Value
  } elseif ($pathProperty -and $pathProperty.Value) {
    "$($pathProperty.Value) (arguments unavailable)"
  } else {
    "<unavailable>"
  }
  return [pscustomobject]@{
    port = $Port
    pid = $ownerPid
    commandLine = $commandLine
  }
}

function Assert-PortsAvailable {
  param([int[]]$Ports)

  $conflicts = @()
  foreach ($port in $Ports) {
    $owner = Get-PortOwner -Port $port
    if ($owner) {
      $conflicts += $owner
    }
  }
  if ($conflicts.Count -gt 0) {
    foreach ($conflict in $conflicts) {
      Write-Host (
        "Port {0} is already listening: PID={1} command={2}" -f
        $conflict.port,
        $conflict.pid,
        $conflict.commandLine
      ) -ForegroundColor Red
    }
    throw "QuantX did not stop or replace untracked port owners."
  }
}

function Test-TcpEndpoint {
  param(
    [string]$HostName,
    [int]$Port,
    [int]$TimeoutMilliseconds = 700
  )

  $client = [Net.Sockets.TcpClient]::new()
  try {
    $task = $client.ConnectAsync($HostName, $Port)
    return $task.Wait($TimeoutMilliseconds) -and $client.Connected
  } catch {
    return $false
  } finally {
    $client.Dispose()
  }
}

function Show-ExternalDependencies {
  param([string]$Python)

  if ($Python) {
    $previousPythonPath = $env:PYTHONPATH
    try {
      $env:PYTHONPATH = Get-WorkspacePythonPath
      $diagnostics = & $Python -m `
        quantx_infrastructure.diagnostics.external_dependencies 2>$null
      if ($LASTEXITCODE -eq 0 -and $diagnostics) {
        $values = $diagnostics | ConvertFrom-Json
        foreach ($name in @("PostgreSQL", "InfluxDB", "Redis")) {
          $value = $values.$name
          $versionProperty = $value.PSObject.Properties["version"]
          $version = if ($versionProperty -and $versionProperty.Value) {
            $versionProperty.Value
          } else {
            "unknown"
          }
          $color = if ($value.status -eq "reachable") { "Green" } else { "Yellow" }
          Write-Host (
            "{0,-12} {1} {2} version={3} (externally managed)" -f
            $name,
            $value.endpoint,
            $value.status,
            $version
          ) -ForegroundColor $color
        }
        return
      }
    } catch {
      Write-Verbose "Version diagnostics unavailable: $($_.Exception.Message)"
    } finally {
      $env:PYTHONPATH = $previousPythonPath
    }
  }

  Write-Warning "Falling back to TCP-only external dependency checks."
  $postgresHost = "127.0.0.1"
  $postgresPort = 5432
  if (
    $env:DATABASE_URL -and
    $env:DATABASE_URL -match "^(?:[^:]+://)?(?:[^@]+@)?(?<host>\[[^\]]+\]|[^:/]+)(?::(?<port>\d+))?"
  ) {
    $postgresHost = $Matches.host.Trim("[", "]")
    if ($Matches.port) {
      $postgresPort = [int]$Matches.port
    }
  }
  $redisHost = if ($env:REDIS_HOST) { $env:REDIS_HOST } else { "127.0.0.1" }
  $redisPort = if ($env:REDIS_PORT) { [int]$env:REDIS_PORT } else { 6379 }
  $influxHost = "127.0.0.1"
  $influxPort = 8086
  if ($env:INFLUXDB_HOST) {
    try {
      $influxUri = [uri]$env:INFLUXDB_HOST
      $influxHost = $influxUri.Host
      $influxPort = $influxUri.Port
    } catch {
      $influxHost = $env:INFLUXDB_HOST
    }
  }
  $checks = @(
    @{ name = "PostgreSQL"; host = $postgresHost; port = $postgresPort },
    @{ name = "InfluxDB"; host = $influxHost; port = $influxPort },
    @{ name = "Redis"; host = $redisHost; port = $redisPort }
  )
  foreach ($check in $checks) {
    $available = Test-TcpEndpoint `
      -HostName $check.host `
      -Port $check.port
    $status = if ($available) { "reachable" } else { "not reachable" }
    $color = if ($available) { "Green" } else { "Yellow" }
    Write-Host (
      "{0,-12} {1}:{2} {3} (externally managed)" -f
      $check.name,
      $check.host,
      $check.port,
      $status
    ) -ForegroundColor $color
  }
}

function Start-ManagedProcess {
  param(
    [string]$Name,
    [string]$Executable,
    [string[]]$Arguments,
    [string]$WorkingDirectory
  )

  Ensure-RuntimeDirectories
  $stdout = Join-Path $LogDirectory "$Name.stdout.log"
  $stderr = Join-Path $LogDirectory "$Name.stderr.log"
  $process = Start-Process `
    -FilePath $Executable `
    -ArgumentList $Arguments `
    -WorkingDirectory $WorkingDirectory `
    -RedirectStandardOutput $stdout `
    -RedirectStandardError $stderr `
    -WindowStyle Hidden `
    -PassThru
  Start-Sleep -Milliseconds 350
  if ($process.HasExited) {
    $message = if (Test-Path -LiteralPath $stderr) {
      (Get-Content -LiteralPath $stderr -Tail 30) -join [Environment]::NewLine
    } else {
      "No stderr was captured."
    }
    throw "$Name exited during startup.`n$message"
  }
  $entry = [pscustomobject]@{
    name = $Name
    pid = $process.Id
    startedAt = $process.StartTime.ToUniversalTime().ToString("o")
    executable = $Executable
    arguments = @($Arguments)
    workingDirectory = $WorkingDirectory
    stdout = $stdout
    stderr = $stderr
    runtimeProfile = $script:RuntimeProfile
    agentMode = $script:RuntimeAgentMode
    configuredAccount = $script:RuntimeConfiguredAccount
    qmtLaunchState = $script:RuntimeQmtLaunchState
    qmtReasonCode = $script:RuntimeQmtReasonCode
    qmtLaunchStartedAt = $script:RuntimeQmtLaunchStartedAt
    liveTradingEnabled = $script:RuntimeLiveTradingEnabled
  }
  $script:ManagedProcesses += $entry
  Write-State -Processes $script:ManagedProcesses
  Write-Host "Started $Name (PID $($process.Id))." -ForegroundColor Green
}

function Wait-HttpReady {
  param(
    [string]$Name,
    [string]$Url,
    [int]$TimeoutSeconds = 60
  )

  $deadline = [datetime]::UtcNow.AddSeconds($TimeoutSeconds)
  do {
    try {
      $response = Invoke-WebRequest -Uri $Url -TimeoutSec 3 -UseBasicParsing
      if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 400) {
        return
      }
    } catch {
      Start-Sleep -Milliseconds 500
    }
  } while ([datetime]::UtcNow -lt $deadline)
  throw "$Name did not become ready at $Url within $TimeoutSeconds seconds."
}

function Wait-PortReady {
  param(
    [string]$Name,
    [int]$Port,
    [int]$TimeoutSeconds = 45
  )

  $deadline = [datetime]::UtcNow.AddSeconds($TimeoutSeconds)
  do {
    if (Test-TcpEndpoint -HostName "127.0.0.1" -Port $Port) {
      return
    }
    Start-Sleep -Milliseconds 300
  } while ([datetime]::UtcNow -lt $deadline)
  throw "$Name did not listen on port $Port within $TimeoutSeconds seconds."
}

function Request-ManagedProcessShutdown {
  param(
    [object]$Entry,
    [System.Diagnostics.Process]$Process
  )

  switch ([string]$Entry.name) {
    "caddy" {
      & ([string]$Entry.executable) stop --address "127.0.0.1:2019" *> $null
      if ($LASTEXITCODE -ne 0) {
        throw "Caddy admin stop request failed."
      }
      return $true
    }
    "api" {
      Invoke-WebRequest `
        -Method Post `
        -Uri "http://127.0.0.1:$ApiPort/_dev/shutdown" `
        -TimeoutSec 3 `
        -UseBasicParsing *> $null
      return $true
    }
    default {
      # GUI-capable processes receive WM_CLOSE. Hidden console-only processes
      # have no graceful channel, so report that immediately instead of making
      # every component look hung for the full graceful-stop window.
      return [bool]$Process.CloseMainWindow()
    }
  }
}

function Stop-TrackedProcesses {
  param(
    [object[]]$Entries,
    [object[]]$PreservedEntries = @()
  )

  $reversed = @($Entries)
  [array]::Reverse($reversed)
  $remaining = @()
  foreach ($entry in $reversed) {
    $process = Get-TrackedProcess -Entry $entry
    if (-not $process) {
      Write-Host "Skipped stale $($entry.name) state (PID $($entry.pid))." `
        -ForegroundColor Yellow
      continue
    }
    Write-Host "Stopping $($entry.name) (PID $($entry.pid))..."
    try {
      $shutdownRequested = $false
      try {
        $shutdownRequested = [bool](
          Request-ManagedProcessShutdown -Entry $entry -Process $process
        )
      } catch {
        Write-Verbose (
          "Graceful stop request for $($entry.name) failed: " +
          "$($_.Exception.Message)"
        )
      }
      $gracefulWaitMilliseconds = if ($shutdownRequested) { 10000 } else { 0 }
      if (-not $process.WaitForExit($gracefulWaitMilliseconds)) {
        if ($shutdownRequested) {
          Write-Warning (
            "$($entry.name) did not exit after the graceful stop window; " +
            "terminating its verified PID."
          )
        } else {
          Write-Host (
            "$($entry.name) has no graceful dev stop channel; " +
            "terminating its verified PID."
          ) -ForegroundColor DarkYellow
        }
        # Stop-Process can throw a PowerShell ProcessManager NullReferenceException
        # on Windows even when the verified process is still alive. Use the
        # already validated System.Diagnostics.Process handle directly.
        $process.Kill()
        $terminationDeadline = [datetime]::UtcNow.AddSeconds(10)
        do {
          Start-Sleep -Milliseconds 100
          $trackedAfterStop = Get-TrackedProcess -Entry $entry
        } while (
          $trackedAfterStop -and
          [datetime]::UtcNow -lt $terminationDeadline
        )
        if ($trackedAfterStop) {
          throw "$($entry.name) did not exit after termination."
        }
      }
    } catch {
      Write-Warning "Could not stop $($entry.name): $($_.Exception.Message)"
    }
    if (Get-TrackedProcess -Entry $entry) {
      $remaining += $entry
    }
  }
  [array]::Reverse($remaining)
  [array]$nextState = @($PreservedEntries) + @($remaining)
  Write-State -Processes $nextState
  if ($remaining.Count -gt 0) {
    throw (
      "Could not stop $($remaining.Count) managed process(es); " +
      "their verified PID/start-time entries remain in the state file."
    )
  }
}

function Start-DevCaddy {
  param([string]$Executable)

  $env:QUANTX_ROOT = $Root.Replace("\", "/")
  Initialize-CaddyEnvironment
  Start-ManagedProcess `
    -Name "caddy" `
    -Executable $Executable `
    -Arguments @(
      "run",
      "--config", (Join-Path $Root "ops\caddy\Caddyfile.dev"),
      "--adapter", "caddyfile"
    ) `
    -WorkingDirectory $Root
}

function Get-DevGatewayUrls {
  $urls = @("http://127.0.0.1:8080")
  try {
    $lanUrls = @(
      [System.Net.Dns]::GetHostAddresses([System.Net.Dns]::GetHostName()) |
        Where-Object {
          $_.AddressFamily -eq [System.Net.Sockets.AddressFamily]::InterNetwork -and
          -not [System.Net.IPAddress]::IsLoopback($_) -and
          -not $_.IPAddressToString.StartsWith("169.254.")
        } |
        ForEach-Object { "http://$($_.IPAddressToString):8080" }
    )
    $urls += $lanUrls
  } catch {
    # The loopback URL remains usable if hostname resolution is unavailable.
  }
  return @($urls | Select-Object -Unique)
}

function Wait-DevCaddyReady {
  param([bool]$RequireRuntimeReadiness = $true)

  Wait-HttpReady -Name "Caddy" -Url "http://127.0.0.1:8080/health/live"
  if ($RequireRuntimeReadiness) {
    Wait-HttpReady `
      -Name "QuantX readiness" `
      -Url "http://127.0.0.1:8080/health/ready" `
      -TimeoutSeconds 90
  }
  Wait-HttpReady `
    -Name "Developer docs" `
    -Url "http://127.0.0.1:8080/docs/"
}

function Show-QmtAgentRuntimeHealth {
  try {
    $runtime = Invoke-RestMethod `
      -Uri "http://127.0.0.1:8080/health/components" `
      -TimeoutSec 5
    $qmt = $runtime.components.qmtAgent
    $marketData = $runtime.components.marketData
    if ($null -eq $qmt) {
      return
    }
    $summary = (
      "QMT runtime: agent={0}, marketData={1}, connectedDevices={2}, " +
      "onlineDevices={3}, reconcilingDevices={4}, modes={5}, accounts={6}, " +
      "protocols={7}, snapshotAgeSeconds={8}"
    ) -f @(
      $qmt.status,
      $marketData.status,
      $qmt.connectedDevices,
      $qmt.onlineDevices,
      $qmt.reconcilingDevices,
      (@($qmt.modes) -join ","),
      (@($qmt.accountIds) -join ","),
      (@($qmt.protocolVersions) -join ","),
      $qmt.latestSnapshotAgeSeconds
    )
    if ([string]$qmt.status -eq "ready") {
      Write-Host $summary -ForegroundColor Green
    } else {
      Write-Warning (
        "$summary. The Agent process may be running while MiniQMT trading " +
        "is not ready; complete the MiniQMT login and it will reconnect " +
        "automatically."
      )
    }
  } catch {
    Write-Verbose (
      "Could not read QMT runtime health: $($_.Exception.Message)"
    )
  }
}

function Wait-QmtAgentRuntimeReady {
  param(
    [Parameter(Mandatory = $true)][string]$AccountId,
    [Parameter(Mandatory = $true)][object]$ProcessEntry,
    [Parameter(Mandatory = $true)][datetime]$LaunchStartedAt,
    [int]$TimeoutSeconds = 60
  )

  $launchBoundary = $LaunchStartedAt.ToUniversalTime()
  $deadline = [datetime]::UtcNow.AddSeconds($TimeoutSeconds)
  do {
    if (-not (Get-TrackedProcess -Entry $ProcessEntry)) {
      Write-Warning (
        "The QMT Agent process for this launch is no longer alive; a prior " +
        "database heartbeat cannot make the runtime READY."
      )
      return $false
    }
    try {
      $runtime = Invoke-RestMethod `
        -Uri "http://127.0.0.1:8080/health/components" `
        -TimeoutSec 5
      $qmt = $runtime.components.qmtAgent
      $modes = @($qmt.modes | ForEach-Object { [string]$_ })
      $protocols = @(
        $qmt.protocolVersions | ForEach-Object { [string]$_ }
      )
      $accounts = @($qmt.accountIds | ForEach-Object { [string]$_ })
      $snapshotAge = if ($null -eq $qmt.latestSnapshotAgeSeconds) {
        [double]::PositiveInfinity
      } else {
        [double]$qmt.latestSnapshotAgeSeconds
      }
      $latestReadyHeartbeatAt = $null
      if ($qmt.latestReadyHeartbeatAt) {
        try {
          $latestReadyHeartbeatAt = [datetimeoffset]::Parse(
            [string]$qmt.latestReadyHeartbeatAt,
            [Globalization.CultureInfo]::InvariantCulture,
            [Globalization.DateTimeStyles]::AssumeUniversal
          ).UtcDateTime
        } catch {
          $latestReadyHeartbeatAt = $null
        }
      }
      if (
        [string]$qmt.status -eq "ready" -and
        [int]$qmt.readyDevices -ge 1 -and
        $modes -contains "live" -and
        $protocols -contains "1.1" -and
        $accounts -contains $AccountId -and
        $snapshotAge -le 90 -and
        $null -ne $latestReadyHeartbeatAt -and
        $latestReadyHeartbeatAt -ge $launchBoundary -and
        (Get-TrackedProcess -Entry $ProcessEntry)
      ) {
        Show-QmtAgentRuntimeHealth
        return $true
      }
    } catch {
      Write-Verbose "Waiting for live QMT runtime: $($_.Exception.Message)"
    }
    Start-Sleep -Seconds 2
  } while ([datetime]::UtcNow -lt $deadline)

  Write-Warning (
    "The tracked QMT Agent did not publish a READY heartbeat for this launch " +
    "at or after $($launchBoundary.ToString('o')) within $TimeoutSeconds " +
    "seconds; prior database heartbeats were ignored."
  )
  return $false
}

function Invoke-CaddyRecovery {
  if ($Profile -ne "full") {
    throw "Caddy component recovery is limited to the dev/full profile."
  }

  Import-QuantXEnvironment
  Initialize-PythonEnvironment
  $existing = @(Read-State)
  $runtimeConfiguration = Get-DevRuntimeConfiguration -Entries $existing
  $qmtLaunchBlocked = $runtimeConfiguration.qmtLaunchState -eq "BLOCKED"
  $requiredNames = @(
    "api",
    "market-gateway",
    "ai-runtime",
    "engine",
    "web",
    "docs",
    "worker"
  )
  if (-not $qmtLaunchBlocked) {
    $requiredNames += "qmt-agent"
  }
  $allowedNames = @($requiredNames) + @("caddy")
  $unexpected = @(
    $existing | Where-Object { [string]$_.name -notin $allowedNames }
  )
  if ($unexpected.Count -gt 0) {
    throw (
      "Caddy recovery found unexpected managed component state: " +
      (($unexpected | ForEach-Object { [string]$_.name }) -join ", ")
    )
  }

  $preserved = @()
  foreach ($name in $requiredNames) {
    $matches = @($existing | Where-Object { [string]$_.name -eq $name })
    if ($matches.Count -ne 1) {
      throw (
        "Caddy recovery requires exactly one managed $name entry; " +
        "found $($matches.Count)."
      )
    }
    if (-not (Get-TrackedProcess -Entry $matches[0])) {
      throw "Caddy recovery requires managed $name to be running."
    }
    $preserved += $matches[0]
  }

  $caddyEntries = @(
    $existing | Where-Object { [string]$_.name -eq "caddy" }
  )
  if ($caddyEntries.Count -gt 1) {
    throw "Caddy recovery found duplicate managed Caddy entries."
  }
  if (
    $caddyEntries.Count -eq 1 -and
    (Get-TrackedProcess -Entry $caddyEntries[0])
  ) {
    throw "Managed Caddy is already running; component recovery is refused."
  }

  Assert-PortsAvailable -Ports @(8080)
  $caddy = Join-Path $ToolsDirectory "caddy\caddy.exe"
  if (-not (Test-Path -LiteralPath $caddy -PathType Leaf)) {
    throw "Caddy is missing. Run: .\ops\quantx.ps1 bootstrap"
  }

  Initialize-PrefectEnvironment
  $env:ENV = "development"
  $env:RUNTIME_PROFILE = $Profile
  $env:PREFECT_ENABLED = "true"
  $env:PYTHONPATH = Get-WorkspacePythonPath
  $script:RuntimeProfile = $runtimeConfiguration.profile
  $script:RuntimeAgentMode = $runtimeConfiguration.agentMode
  $script:RuntimeConfiguredAccount = $runtimeConfiguration.configuredAccount
  $script:RuntimeQmtLaunchState = $runtimeConfiguration.qmtLaunchState
  $script:RuntimeQmtReasonCode = $runtimeConfiguration.qmtReasonCode
  $script:RuntimeQmtLaunchStartedAt = (
    $runtimeConfiguration.qmtLaunchStartedAt
  )
  $script:RuntimeLiveTradingEnabled = (
    $runtimeConfiguration.liveTradingEnabled
  )
  $env:QMT_AGENT_LAUNCH_STATE = $script:RuntimeQmtLaunchState
  $env:QMT_AGENT_LAUNCH_REASON = $script:RuntimeQmtReasonCode
  $env:QMT_AGENT_LAUNCH_STARTED_AT = $script:RuntimeQmtLaunchStartedAt
  $script:ManagedProcesses = @($preserved)
  try {
    Start-DevCaddy -Executable $caddy
    Wait-DevCaddyReady -RequireRuntimeReadiness (-not $qmtLaunchBlocked)
    Write-State -Processes $script:ManagedProcesses
    Write-Host (
      "Recovered managed Caddy for QuantX dev/$Profile at " +
      ((Get-DevGatewayUrls) -join ", ")
    ) -ForegroundColor Cyan
  } catch {
    $startupError = $_
    $startedCaddy = @(
      $script:ManagedProcesses |
        Where-Object { [string]$_.name -eq "caddy" }
    )
    if ($startedCaddy.Count -gt 0) {
      try {
        Stop-TrackedProcesses `
          -Entries $startedCaddy `
          -PreservedEntries $preserved
      } catch {
        Write-Warning (
          "Caddy recovery rollback failed: $($_.Exception.Message)"
        )
      }
    } else {
      Write-State -Processes $preserved
    }
    throw $startupError
  }
}

function Stop-OnFailure {
  if ($script:ManagedProcesses.Count -gt 0) {
    Write-Warning "Startup failed; rolling back only processes started by this run."
    Stop-TrackedProcesses -Entries $script:ManagedProcesses
  }
}

function Invoke-BoundedCliCommand {
  param(
    [string]$Name,
    [string]$Executable,
    [string[]]$Arguments,
    [int]$TimeoutSeconds,
    [string]$WorkingDirectory = $Root
  )

  Ensure-RuntimeDirectories
  $safeName = $Name.ToLowerInvariant() -replace "[^a-z0-9-]", "-"
  $stdout = Join-Path $LogDirectory "$safeName.stdout.log"
  $stderr = Join-Path $LogDirectory "$safeName.stderr.log"
  $stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
  Write-Host "Running $Name (timeout: ${TimeoutSeconds}s)..."
  $process = Start-Process `
    -FilePath $Executable `
    -ArgumentList $Arguments `
    -WorkingDirectory $WorkingDirectory `
    -RedirectStandardOutput $stdout `
    -RedirectStandardError $stderr `
    -WindowStyle Hidden `
    -PassThru
  if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
    Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
    $process.WaitForExit(10000) | Out-Null
    throw (
      "$Name timed out after $TimeoutSeconds seconds. " +
      "See $stdout and $stderr."
    )
  }
  $stopwatch.Stop()
  Write-Host (
    "$Name completed in {0:n1}s (exit $($process.ExitCode))." -f
    $stopwatch.Elapsed.TotalSeconds
  )
  return [int]$process.ExitCode
}

function Invoke-PrefectPreparation {
  param(
    [string]$Python,
    [string]$ApplicationRoot = $Root
  )

  Initialize-PrefectEnvironment
  $poolName = Get-PrefectWorkerPool
  $inspectExitCode = Invoke-BoundedCliCommand `
    -Name "Prefect work-pool inspect" `
    -Executable $Python `
    -Arguments @("-m", "prefect", "work-pool", "inspect", $poolName) `
    -TimeoutSeconds 30
  if ($inspectExitCode -ne 0) {
    throw (
      "Required external Prefect work pool '$poolName' was not found at " +
      "$env:PREFECT_API_URL."
    )
  }
  $cleanupExitCode = Invoke-BoundedCliCommand `
    -Name "Prefect legacy snapshot deployment cleanup" `
    -Executable $Python `
    -Arguments @(
      (Join-Path $Root (
        "apps\worker\scripts\cleanup_legacy_snapshot_deployments.py"
      ))
    ) `
    -TimeoutSeconds 60
  if ($cleanupExitCode -ne 0) {
    throw "Legacy Prefect snapshot deployment cleanup failed."
  }
  $deployExitCode = Invoke-BoundedCliCommand `
    -Name "Prefect deploy" `
    -Executable $Python `
    -Arguments @(
      "-m", "prefect", "deploy",
      "--prefect-file", (Join-Path $ApplicationRoot "apps\worker\prefect.yaml"),
      "--all"
    ) `
    -TimeoutSeconds 180
  if ($deployExitCode -ne 0) {
    throw "Prefect deployment failed."
  }
}

function Resolve-DevLaunchProfile {
  param(
    [string]$RequestedProfile,
    [bool]$ModeExplicitlySpecified,
    [string]$RequestedMode
  )

  if (
    $RequestedProfile -eq "web" -and
    (
      -not $ModeExplicitlySpecified -or
      $RequestedMode -ne "data-only"
    )
  ) {
    return "full"
  }
  return $RequestedProfile
}

function Set-DevTradingModeEnvironment {
  if ($Profile -ne "full") {
    if ($script:ModeWasExplicitlySpecified -and $Mode -ne "data-only") {
      throw "QMT Agent modes require the dev/full profile."
    }
    $env:QMT_AGENT_MODE = "data-only"
    $env:QMT_ACCOUNT_WHITELIST = ""
    $env:ENABLE_REAL_TRADING = "false"
    $env:QMT_REAL_TRADING_ENABLED = "false"
    $env:T_TRADE_LIVE_ENABLED = "false"
    $env:REAL_TRADING_ACCOUNT_ALLOWLIST = "[]"
    return "data-only"
  }

  if ($script:ModeWasExplicitlySpecified -and $Mode -eq "paper") {
    throw (
      "Dev up supports full/live by default and -Mode data-only as the " +
      "only non-live entry; paper mode is not supported by dev up."
    )
  }

  $agentMode = if ($script:ModeWasExplicitlySpecified) { $Mode } else { "live" }
  $account = if ($agentMode -eq "data-only") {
    ""
  } else {
    Resolve-DevTradingAccountId -RequestedAccountId $AccountId
  }

  $env:QMT_AGENT_MODE = $agentMode
  $env:QMT_ACCOUNT_WHITELIST = if ($agentMode -eq "data-only") {
    ""
  } else {
    $account
  }

  if ($agentMode -eq "live") {
    $env:ENABLE_REAL_TRADING = "true"
    $env:QMT_REAL_TRADING_ENABLED = "true"
    $env:T_TRADE_LIVE_ENABLED = "true"
    $env:REAL_TRADING_ACCOUNT_ALLOWLIST = ConvertTo-Json `
      -InputObject @($account) `
      -Compress
  } else {
    $env:ENABLE_REAL_TRADING = "false"
    $env:QMT_REAL_TRADING_ENABLED = "false"
    $env:T_TRADE_LIVE_ENABLED = "false"
    $env:REAL_TRADING_ACCOUNT_ALLOWLIST = "[]"
  }

  return $agentMode
}

function Disable-DevLiveTradingCapability {
  # Keep QMT_AGENT_MODE and the selected account as requested runtime intent,
  # but make every server- and Agent-side execution gate fail closed.
  $env:ENABLE_REAL_TRADING = "false"
  $env:QMT_REAL_TRADING_ENABLED = "false"
  $env:T_TRADE_LIVE_ENABLED = "false"
  $env:REAL_TRADING_ACCOUNT_ALLOWLIST = "[]"
}

function Resolve-DevTradingAccountId {
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

  if ($candidates.Count -eq 1) {
    return [string](@($candidates)[0])
  }
  if ($candidates.Count -eq 0) {
    throw (
      "Dev full defaults to live mode. Configure one account in " +
      "QMT_ACCOUNT_WHITELIST or pass -AccountId."
    )
  }
  throw (
    "Multiple development trading accounts are configured; pass -AccountId " +
    "to select one."
  )
}

function Invoke-Up {
  if ($Environment -ne "dev") {
    throw "Use install for local production services; up supports dev only."
  }
  if ($Component) {
    if ($Component -ne "caddy") {
      throw "up -Component only supports fail-closed Caddy recovery."
    }
    Invoke-CaddyRecovery
    return
  }
  Import-QuantXEnvironment
  $resolvedProfile = Resolve-DevLaunchProfile `
    -RequestedProfile $Profile `
    -ModeExplicitlySpecified $script:ModeWasExplicitlySpecified `
    -RequestedMode $Mode
  if ($resolvedProfile -ne $Profile) {
    Write-Host (
      "Dev web without an explicit mode is promoted to full/live so the " +
      "single-account trading runtime remains connected. Use " +
      "-Profile web -Mode data-only for an explicit non-trading launch."
    ) -ForegroundColor Cyan
  }
  $Profile = $resolvedProfile
  Initialize-PythonEnvironment
  $existing = @(Read-State)
  $live = @($existing | Where-Object { Get-TrackedProcess -Entry $_ })
  if ($live.Count -gt 0) {
    throw "QuantX already has managed development processes. Run status or down."
  }
  Write-State -Processes @()
  Assert-PortsAvailable -Ports @(8080, $ApiPort, $MarketGatewayPort, 5250, 5251)
  $python = Resolve-Python
  $aiRuntimePython = Resolve-AiRuntimePython
  Show-ExternalDependencies -Python $python
  $node = Resolve-Node
  $qmtPython = $null
  $agentMode = Set-DevTradingModeEnvironment
  $qmtAgentLaunchAllowed = $false
  $qmtReasonCode = ""
  $liveAccount = ""
  if ($agentMode -eq "live") {
    $liveAccounts = @(ConvertFrom-ListSetting -Value $env:QMT_ACCOUNT_WHITELIST)
    if ($liveAccounts.Count -ne 1) {
      throw "Live development startup requires exactly one resolved account."
    }
    $liveAccount = [string]$liveAccounts[0]
  }
  if ($Profile -eq "full") {
    # API health aggregation and Prefect CLI/Worker must share the same
    # canonical API base from the moment each process is spawned.
    Initialize-PrefectEnvironment
    try {
      $qmtPython = Resolve-Python -Qmt
    } catch {
      $qmtReasonCode = "QMT_RUNTIME_UNAVAILABLE"
      Write-Verbose "QMT runtime preflight failed: $($_.Exception.Message)"
    }
    if ($qmtPython) {
      if (Test-QmtAgentEnrollment -Python $qmtPython) {
        $qmtAgentLaunchAllowed = $true
      } else {
        $qmtReasonCode = "QMT_ENROLLMENT_REQUIRED"
      }
    }
    if (-not $qmtAgentLaunchAllowed) {
      Disable-DevLiveTradingCapability
      Write-Warning (
        "QMT Agent launch is BLOCKED (reason=$qmtReasonCode). The requested " +
        "profile=$Profile and agentMode=$agentMode are preserved, but all " +
        "live-trading capability gates are disabled and no QMT process will " +
        "be started. API, Engine, Web, Worker, Caddy, and backtests over " +
        "persisted history will continue to start. Enroll/fix the Agent and " +
        "restart the managed runtime to restore QMT capability."
      )
    }
  }
  $script:RuntimeProfile = $Profile
  $script:RuntimeAgentMode = $agentMode
  $script:RuntimeConfiguredAccount = $liveAccount
  $script:RuntimeQmtLaunchState = if ($Profile -ne "full") {
    "NOT_REQUESTED"
  } elseif ($qmtAgentLaunchAllowed) {
    "LAUNCH_ALLOWED"
  } else {
    "BLOCKED"
  }
  $script:RuntimeQmtReasonCode = $qmtReasonCode
  $script:RuntimeQmtLaunchStartedAt = if ($qmtAgentLaunchAllowed) {
    [datetime]::UtcNow.ToString("o")
  } else {
    ""
  }
  $script:RuntimeLiveTradingEnabled = (
    $env:ENABLE_REAL_TRADING -eq "true" -and
    $env:QMT_REAL_TRADING_ENABLED -eq "true"
  )
  $env:QMT_AGENT_LAUNCH_STATE = $script:RuntimeQmtLaunchState
  $env:QMT_AGENT_LAUNCH_REASON = $script:RuntimeQmtReasonCode
  $env:QMT_AGENT_LAUNCH_STARTED_AT = $script:RuntimeQmtLaunchStartedAt
  $caddy = Join-Path $ToolsDirectory "caddy\caddy.exe"
  if (-not (Test-Path -LiteralPath $caddy -PathType Leaf)) {
    throw "Caddy is missing. Run: .\ops\quantx.ps1 bootstrap"
  }
  $vite = Join-Path $Root "node_modules\vite\bin\vite.js"
  if (-not (Test-Path -LiteralPath $vite -PathType Leaf)) {
    throw "Frontend dependencies are missing. Run npm install at the repository root."
  }
  $vitePress = Join-Path $Root "node_modules\vitepress\bin\vitepress.js"
  $generateDocs = Join-Path `
    $Root `
    "apps\docs\scripts\generate-graphql-reference.mjs"
  if (
    -not (Test-Path -LiteralPath $vitePress -PathType Leaf) -or
    -not (Test-Path -LiteralPath $generateDocs -PathType Leaf)
  ) {
    throw "Documentation dependencies are missing. Run npm install at the repository root."
  }

  $env:ENV = "development"
  $env:RUNTIME_PROFILE = $Profile
  $env:PREFECT_ENABLED = if ($Profile -eq "full") { "true" } else { "false" }
  $env:PYTHONPATH = Get-WorkspacePythonPath
  $processSupervisor = Join-Path $Root "ops\supervise_process.py"
  $script:ManagedProcesses = @()
  $liveRuntimeReady = $true
  $qmtProcessEntry = $null
  $qmtProcessLaunchStartedAt = $null
  try {
    Start-ManagedProcess `
      -Name "market-gateway" `
      -Executable $python `
      -Arguments @(
        $processSupervisor,
        "--name", "market-gateway",
        "--state-dir", $StateDirectory,
        "--",
        $python,
        "-m", "uvicorn", "quantx_api.market_gateway:app",
        "--host", "127.0.0.1",
        "--port", [string]$MarketGatewayPort,
        "--ws-max-size", "67108864",
        "--ws-ping-interval", "20",
        "--ws-ping-timeout", [string]$AgentWebSocketPingTimeoutSeconds
      ) `
      -WorkingDirectory $Root
    Wait-HttpReady `
      -Name "Market Gateway" `
      -Url "http://127.0.0.1:$MarketGatewayPort/health/ready"

    Start-ManagedProcess `
      -Name "api" `
      -Executable $python `
      -Arguments @(
        "-m", "uvicorn", "quantx_api.main:app",
        "--host", "127.0.0.1",
        "--port", [string]$ApiPort,
        "--ws-max-size", "67108864",
        "--ws-ping-interval", "20",
        "--ws-ping-timeout", [string]$AgentWebSocketPingTimeoutSeconds
      ) `
      -WorkingDirectory $Root
    Wait-HttpReady `
      -Name "API" `
      -Url "http://127.0.0.1:$ApiPort/health/live"

    Start-ManagedProcess `
      -Name "engine" `
      -Executable $python `
      -Arguments @("-m", "quantx_engine.main") `
      -WorkingDirectory $Root

    Start-ManagedProcess `
      -Name "ai-runtime" `
      -Executable $aiRuntimePython `
      -Arguments @("-m", "quantx_ai_runtime.main") `
      -WorkingDirectory $Root

    Start-ManagedProcess `
      -Name "web" `
      -Executable $node `
      -Arguments @($vite, "--host", "127.0.0.1", "--port", "5250") `
      -WorkingDirectory (Join-Path $Root "apps\web")
    Wait-PortReady -Name "Vite" -Port 5250

    & $node $generateDocs
    if ($LASTEXITCODE -ne 0) {
      throw "GraphQL documentation reference generation failed."
    }
    Start-ManagedProcess `
      -Name "docs" `
      -Executable $node `
      -Arguments @(
        $vitePress,
        "dev",
        "--host", "127.0.0.1",
        "--port", "5251",
        "--strictPort"
      ) `
      -WorkingDirectory (Join-Path $Root "apps\docs")
    Wait-PortReady -Name "VitePress" -Port 5251

    if ($Profile -eq "full") {
      Wait-HttpReady `
        -Name "External Prefect Server" `
        -Url "$env:PREFECT_API_URL/health" `
        -TimeoutSeconds 90
      Invoke-PrefectPreparation -Python $python
      $poolName = Get-PrefectWorkerPool
      Start-ManagedProcess `
        -Name "worker" `
        -Executable $python `
        -Arguments @(
          "-m", "prefect", "worker", "start",
          "--pool", $poolName
        ) `
        -WorkingDirectory $Root

      if ($qmtAgentLaunchAllowed) {
        $workspacePythonPath = $env:PYTHONPATH
        $serverEnvironment = $env:ENV
        try {
          $env:PYTHONPATH = Get-QmtAgentPythonPath
          if ($agentMode -eq "live") {
            # Keep API/Engine in development while satisfying the QMT Agent's
            # explicit real-trading environment gate for this child process.
            $env:ENV = "testing"
          }
          $qmtProcessLaunchStartedAt = [datetime]::UtcNow
          Start-ManagedProcess `
            -Name "qmt-agent" `
            -Executable $qmtPython `
            -Arguments @(
              $processSupervisor,
              "--name", "qmt-agent",
              "--state-dir", $StateDirectory,
              "--",
              $qmtPython,
              "-m", "quantx_qmt_agent.main", "run",
              "--mode", $agentMode
            ) `
            -WorkingDirectory $Root
          $qmtProcessEntry = @(
            $script:ManagedProcesses |
              Where-Object { [string]$_.name -eq "qmt-agent" }
          ) | Select-Object -Last 1
          if (-not $qmtProcessEntry) {
            throw "QMT Agent process state was not recorded after launch."
          }
        } finally {
          $env:ENV = $serverEnvironment
          $env:PYTHONPATH = $workspacePythonPath
        }
      }
    }

    Start-DevCaddy -Executable $caddy
    Wait-DevCaddyReady -RequireRuntimeReadiness ($Profile -ne "full")
    if ($Profile -eq "full") {
      if ($qmtAgentLaunchAllowed -and $agentMode -eq "live") {
        $liveRuntimeReady = Wait-QmtAgentRuntimeReady `
          -AccountId $liveAccount `
          -ProcessEntry $qmtProcessEntry `
          -LaunchStartedAt $qmtProcessLaunchStartedAt
      } elseif ($qmtAgentLaunchAllowed) {
        Show-QmtAgentRuntimeHealth
      }
    }
    $modeLabel = if ($Profile -eq "full") { " ($agentMode)" } else { "" }
    Write-Host (
      "QuantX dev/$Profile$modeLabel is available at " +
      ((Get-DevGatewayUrls) -join ", ")
    ) -ForegroundColor Cyan
  } catch {
    Stop-OnFailure
    throw
  }
  if (-not $liveRuntimeReady) {
    throw (
      "QuantX services remain running, but the live QMT Agent did not become " +
      "READY with a fresh snapshot within 60 seconds. Complete the MiniQMT " +
      "login and run .\ops\quantx.ps1 status; then rerun " +
      ".\ops\quantx.ps1 up -Environment dev -Profile web if the processes " +
      "were stopped manually."
    )
  }
  if (
    $Profile -eq "full" -and
    $agentMode -eq "live" -and
    $env:ENABLE_REAL_TRADING -eq "true" -and
    $env:QMT_REAL_TRADING_ENABLED -eq "true"
  ) {
    $null = Register-DevBackupMaintenance
  }
}

function Invoke-Down {
  $entries = @(Read-State)
  if ($entries.Count -eq 0) {
    Write-Host "No managed QuantX development processes were recorded."
    return
  }
  Stop-TrackedProcesses -Entries $entries
}

function ConvertTo-LocalStatusTimestamp {
  param([AllowNull()][object]$Value)

  if ($null -eq $Value -or -not ([string]$Value).Trim()) {
    return ""
  }
  try {
    $instant = if ($Value -is [datetimeoffset]) {
      [datetimeoffset]$Value
    } elseif ($Value -is [datetime]) {
      [datetimeoffset]([datetime]$Value)
    } else {
      [datetimeoffset]::Parse(
        [string]$Value,
        [Globalization.CultureInfo]::InvariantCulture,
        [Globalization.DateTimeStyles]::RoundtripKind
      )
    }
    return $instant.ToLocalTime().ToString(
      "yyyy-MM-dd HH:mm:ss zzz",
      [Globalization.CultureInfo]::InvariantCulture
    )
  } catch {
    # Preserve legacy/unparseable state values instead of breaking status.
    return [string]$Value
  }
}

function Invoke-Status {
  Import-QuantXEnvironment
  $entries = @(Read-State)
  $qmtHealthQueryAllowed = $true
  if ($entries.Count -eq 0) {
    Write-Host "No managed development processes."
  } else {
    $rows = foreach ($entry in $entries) {
      $process = Get-TrackedProcess -Entry $entry
      [pscustomobject]@{
        Component = $entry.name
        PID = $entry.pid
        State = if ($process) { "RUNNING" } else { "STALE" }
        StartedAt = ConvertTo-LocalStatusTimestamp -Value $entry.startedAt
      }
    }
    $rows | Format-Table -AutoSize
    $runtimeConfiguration = Get-DevRuntimeConfiguration -Entries $entries
    $liveTradingLabel = if ($runtimeConfiguration.liveTradingEnabled) {
      "ENABLED"
    } else {
      "DISABLED"
    }
    Write-Host (
      (
        "Runtime profile={0}, agentMode={1}, configuredAccounts={2}, " +
        "liveTrading={3}"
      ) -f @(
        $runtimeConfiguration.profile,
        $runtimeConfiguration.agentMode,
        $runtimeConfiguration.configuredAccount,
        $liveTradingLabel
      )
    )
    if ($runtimeConfiguration.qmtLaunchState -eq "BLOCKED") {
      $qmtHealthQueryAllowed = $false
      Write-Warning (
        "Runtime state=DEGRADED: QMT Agent launch is BLOCKED " +
        "(reason=$($runtimeConfiguration.qmtReasonCode)); the Agent was not " +
        "started and must not be reported as READY. Broker/order execution " +
        "is fail-closed. Non-QMT services and backtests over persisted " +
        "history remain available."
      )
    }
  }
  foreach ($port in @(8080, $ApiPort, $MarketGatewayPort, 5250, 5251)) {
    $owner = Get-PortOwner -Port $port
    if ($owner) {
      Write-Host (
        "Port {0}: PID={1} command={2}" -f
        $owner.port,
        $owner.pid,
        $owner.commandLine
      )
    }
  }
  $python = $null
  try {
    $python = Resolve-Python
  } catch {
    Write-Verbose "Python unavailable for dependency version checks."
  }
  Show-ExternalDependencies -Python $python
  if ($qmtHealthQueryAllowed) {
    Show-QmtAgentRuntimeHealth
  } else {
    Write-Host (
      "QMT runtime: agent=blocked/offline, liveTrading=DISABLED, " +
      "ready=false (local preflight blocked launch)"
    ) -ForegroundColor Yellow
  }
}

function Invoke-Logs {
  $entries = @(Read-State)
  [array]$selected = @(
    if ($Component) {
      $entries | Where-Object { $_.name -eq $Component }
    } else {
      $entries
    }
  )
  if ($selected.Count -eq 0) {
    throw "No matching managed component logs were found."
  }
  foreach ($entry in $selected) {
    foreach ($stream in @("stdout", "stderr")) {
      $path = [string]$entry.$stream
      if (Test-Path -LiteralPath $path) {
        Write-Host "[$($entry.name) $stream] $path" -ForegroundColor Cyan
        Get-Content -LiteralPath $path -Tail $Tail
      }
    }
  }
}

function Install-LockedTool {
  param(
    [string]$Name,
    [object]$Spec
  )

  $targetDirectory = Join-Path $ToolsDirectory $Name
  $target = Join-Path $targetDirectory ([string]$Spec.executable)
  if (Test-Path -LiteralPath $target -PathType Leaf) {
    $installedHash = if (
      $Spec.PSObject.Properties.Name -contains "installedSha256"
    ) {
      [string]$Spec.installedSha256
    } else {
      [string]$Spec.sha256
    }
    $existingHash = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash
    if ($existingHash -eq $installedHash) {
      Write-Host "$Name $($Spec.version) is already verified."
      return
    }
  }

  $downloads = Join-Path $Runtime "downloads"
  New-Item -ItemType Directory -Path $downloads -Force | Out-Null
  $downloadName = if ([bool]$Spec.archive) {
    "$Name.zip"
  } else {
    [string]$Spec.executable
  }
  $download = Join-Path $downloads $downloadName
  Write-Host "Downloading $Name $($Spec.version)..."
  Invoke-WebRequest -Uri ([string]$Spec.url) -OutFile $download
  $actualHash = (Get-FileHash -LiteralPath $download -Algorithm SHA256).Hash
  if ($actualHash -ne ([string]$Spec.sha256)) {
    throw (
      "$Name SHA256 mismatch. expected=$($Spec.sha256) actual=$actualHash"
    )
  }
  New-Item -ItemType Directory -Path $targetDirectory -Force | Out-Null
  if ([bool]$Spec.archive) {
    Expand-Archive -LiteralPath $download -DestinationPath $targetDirectory -Force
  } else {
    Copy-Item -LiteralPath $download -Destination $target -Force
  }
  if (-not (Test-Path -LiteralPath $target -PathType Leaf)) {
    throw "$Name archive did not contain $($Spec.executable)."
  }
  $installedHash = if (
    $Spec.PSObject.Properties.Name -contains "installedSha256"
  ) {
    [string]$Spec.installedSha256
  } else {
    [string]$Spec.sha256
  }
  $actualInstalledHash = (
    Get-FileHash -LiteralPath $target -Algorithm SHA256
  ).Hash
  if ($actualInstalledHash -ne $installedHash) {
    throw (
      "$Name installed SHA256 mismatch. " +
      "expected=$installedHash actual=$actualInstalledHash"
    )
  }
  Write-Host "Installed and verified $Name $($Spec.version)." -ForegroundColor Green
}

function Invoke-Bootstrap {
  Import-QuantXEnvironment
  Ensure-RuntimeDirectories
  $lock = Get-Content `
    -LiteralPath (Join-Path $ScriptRoot "tools.lock.json") `
    -Raw |
    ConvertFrom-Json
  Install-LockedTool -Name "caddy" -Spec $lock.tools.caddy
  Install-LockedTool -Name "winsw" -Spec $lock.tools.winsw

  $python = Resolve-Python
  & $python -c "import _cffi_backend"
  if ($LASTEXITCODE -ne 0) {
    Write-Warning "Python _cffi_backend is broken; reinstalling cffi."
    & $python -m pip install --upgrade --force-reinstall cffi
    if ($LASTEXITCODE -ne 0) {
      throw "Failed to repair cffi in $python."
    }
  }
  $editableProjects = @(
    "packages\contracts",
    "packages\domain",
    "packages\application",
    "packages\infrastructure",
    "apps\api",
    "apps\ai-runtime",
    "apps\engine",
    "apps\worker"
  )
  foreach ($project in $editableProjects) {
    & $python -m pip install `
      --no-build-isolation `
      --no-deps `
      -e (Join-Path $Root $project)
    if ($LASTEXITCODE -ne 0) {
      throw "Failed to install editable project: $project"
    }
  }
  $qmtPython = Resolve-Python -Qmt
  & $qmtPython -m pip install `
    "httpx>=0.24.0" `
    "keyring>=25.0" `
    "websockets>=11.0"
  if ($LASTEXITCODE -ne 0) {
    throw "Failed to install QMT Agent runtime dependencies."
  }
  & $qmtPython -m pip install `
    --no-build-isolation `
    --no-deps `
    -e (Join-Path $Root "packages\contracts") `
    -e (Join-Path $Root "apps\qmt-agent")
  if ($LASTEXITCODE -ne 0) {
    throw "Failed to install the QMT Agent environment."
  }
  Write-Host "Bootstrap completed." -ForegroundColor Green
}

function Assert-Administrator {
  $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
  $principal = [Security.Principal.WindowsPrincipal]::new($identity)
  if (-not $principal.IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator
  )) {
    throw "Windows service installation requires an Administrator shell."
  }
}

function New-ServiceConfigurations {
  param(
    [string]$Python,
    [string]$QmtPython,
    [string]$ServiceRoot = $Root
  )

  New-Item -ItemType Directory -Path $ServiceDirectory -Force | Out-Null
  $winsw = Join-Path $ToolsDirectory "winsw\WinSW-x64.exe"
  if (-not (Test-Path -LiteralPath $winsw -PathType Leaf)) {
    throw "WinSW is missing. Run bootstrap first."
  }
  $pythonPath = Get-WorkspacePythonPath
  $qmtPythonPath = Get-QmtAgentPythonPath -ServiceRoot $ServiceRoot
  $agentMode = "data-only"
  $agentAccountWhitelist = ""
  if (Test-Path -LiteralPath $AgentModeFile -PathType Leaf) {
    $agentModeState = Get-Content -LiteralPath $AgentModeFile -Raw |
      ConvertFrom-Json
    $agentMode = [string]$agentModeState.mode
    $agentAccountWhitelist = [string]$agentModeState.accountId
  }
  $realTradingEnabled = if ($env:ENABLE_REAL_TRADING) {
    $env:ENABLE_REAL_TRADING
  } else {
    "false"
  }
  $qmtRealTradingEnabled = if ($env:QMT_REAL_TRADING_ENABLED) {
    $env:QMT_REAL_TRADING_ENABLED
  } else {
    "false"
  }
  $tTradeLiveEnabled = if ($env:T_TRADE_LIVE_ENABLED) {
    $env:T_TRADE_LIVE_ENABLED
  } else {
    "false"
  }
  $qmtUserdataPath = if ($env:QMT_USERDATA_PATH) {
    $env:QMT_USERDATA_PATH
  } else {
    ""
  }
  $caddyRootCertificate = Join-Path `
    $Runtime `
    "caddy-data\caddy\pki\authorities\local\root.crt"
  $templateDirectory = Join-Path $ServiceRoot "ops\windows"
  foreach ($template in Get-ChildItem `
    -LiteralPath $templateDirectory `
    -Filter "*.xml") {
    $content = Get-Content -LiteralPath $template.FullName -Raw
    $content = $content.
      Replace("{{ROOT}}", [Security.SecurityElement]::Escape($ServiceRoot)).
      Replace("{{RUNTIME}}", [Security.SecurityElement]::Escape($Runtime)).
      Replace("{{PYTHON}}", [Security.SecurityElement]::Escape($Python)).
      Replace("{{QMT_PYTHON}}", [Security.SecurityElement]::Escape($QmtPython)).
      Replace(
        "{{PYTHONPATH}}",
        [Security.SecurityElement]::Escape($pythonPath)
      ).
      Replace(
        "{{QMT_PYTHONPATH}}",
        [Security.SecurityElement]::Escape($qmtPythonPath)
      ).
      Replace(
        "{{PREFECT_API_URL}}",
        [Security.SecurityElement]::Escape((Get-PrefectApiUrl))
      ).
      Replace(
        "{{PREFECT_WORKER_POOL}}",
        [Security.SecurityElement]::Escape((Get-PrefectWorkerPool))
      ).
      Replace(
        "{{QMT_AGENT_MODE}}",
        [Security.SecurityElement]::Escape($agentMode)
      ).
      Replace(
        "{{QMT_ACCOUNT_WHITELIST}}",
        [Security.SecurityElement]::Escape($agentAccountWhitelist)
      ).
      Replace(
        "{{QMT_USERDATA_PATH}}",
        [Security.SecurityElement]::Escape($qmtUserdataPath)
      ).
      Replace(
        "{{CADDY_ROOT_CERT}}",
        [Security.SecurityElement]::Escape($caddyRootCertificate)
      ).
      Replace(
        "{{ENABLE_REAL_TRADING}}",
        [Security.SecurityElement]::Escape($realTradingEnabled)
      ).
      Replace(
        "{{QMT_REAL_TRADING_ENABLED}}",
        [Security.SecurityElement]::Escape($qmtRealTradingEnabled)
      ).
      Replace(
        "{{T_TRADE_LIVE_ENABLED}}",
        [Security.SecurityElement]::Escape($tTradeLiveEnabled)
      )
    Set-Content `
      -LiteralPath (Join-Path $ServiceDirectory $template.Name) `
      -Value $content `
      -Encoding utf8
    # WinSW 2.12 uses bundled mode: the wrapper executable and configuration
    # must be adjacent and share the same base name.
    Copy-Item `
      -LiteralPath $winsw `
      -Destination (Join-Path $ServiceDirectory "$($template.BaseName).exe") `
      -Force
  }
}

function Get-ServiceConfigurationNames {
  return @(
    "quantx-api.xml",
    "quantx-market-gateway.xml",
    "quantx-engine.xml",
    "quantx-ai-runtime.xml",
    "quantx-worker.xml",
    "quantx-caddy.xml",
    "quantx-qmt-agent.xml"
  )
}

function Test-ReleaseContents {
  param([Parameter(Mandatory = $true)][string]$Directory)

  $root = [System.IO.Path]::GetFullPath($Directory)
  $manifestPath = Join-Path $root "manifest.json"
  $checksumsPath = Join-Path $root "checksums.json"
  if (
    -not (Test-Path -LiteralPath $manifestPath -PathType Leaf) -or
    -not (Test-Path -LiteralPath $checksumsPath -PathType Leaf)
  ) {
    throw "Release manifest or checksum ledger is missing."
  }
  $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
  if (
    [string]$manifest.product -cne "QuantX" -or
    [string]$manifest.version -notmatch
      "^[0-9A-Za-z][0-9A-Za-z._-]{0,63}$"
  ) {
    throw "Release manifest identity is invalid."
  }
  foreach ($requiredFile in @(
    "apps\web\dist\index.html",
    "apps\docs\dist\index.html",
    "apps\docs\dist\contracts\graphql-schema.graphql",
    "apps\docs\dist\contracts\graphql-permissions.json",
    "apps\docs\dist\contracts\openapi-client.json"
  )) {
    if (-not (
      Test-Path -LiteralPath (Join-Path $root $requiredFile) -PathType Leaf
    )) {
      throw "Release content is missing: $requiredFile"
    }
  }
  foreach ($entry in @(
    Get-Content -LiteralPath $checksumsPath -Raw | ConvertFrom-Json
  )) {
    $relative = ([string]$entry.path).Replace("/", "\")
    $candidate = [System.IO.Path]::GetFullPath((Join-Path $root $relative))
    if (
      -not $candidate.StartsWith(
        $root + [System.IO.Path]::DirectorySeparatorChar,
        [StringComparison]::OrdinalIgnoreCase
      ) -or
      -not (Test-Path -LiteralPath $candidate -PathType Leaf)
    ) {
      throw "Release checksum path is invalid: $relative"
    }
    $file = Get-Item -LiteralPath $candidate
    $hash = (
      Get-FileHash -LiteralPath $candidate -Algorithm SHA256
    ).Hash.ToLowerInvariant()
    if (
      $file.Length -ne [long]$entry.size -or
      $hash -cne ([string]$entry.sha256).ToLowerInvariant()
    ) {
      throw "Release checksum mismatch: $relative"
    }
  }
  return $manifest
}

function Install-WorkspaceWheels {
  param(
    [Parameter(Mandatory = $true)][string]$Python,
    [Parameter(Mandatory = $true)][string]$WheelDirectory,
    [Parameter(Mandatory = $true)][string[]]$PackageNames,
    [string]$Target = "",
    [string]$LogPath = ""
  )

  $selected = @()
  foreach ($packageName in $PackageNames) {
    $normalized = $packageName.Replace("-", "_")
    $matches = @(
      Get-ChildItem -LiteralPath $WheelDirectory -Filter "$normalized-*.whl"
    )
    if ($matches.Count -ne 1) {
      throw "Expected exactly one wheel for $packageName."
    }
    $selected += $matches[0].FullName
  }
  $installArguments = @("-m", "pip", "install", "--no-index", "--no-deps")
  if ($Target.Trim()) {
    New-Item -ItemType Directory -Path $Target -Force | Out-Null
    $installArguments += @(
      "--target",
      $Target,
      "--upgrade",
      "--ignore-installed"
    )
  }
  $installArguments += $selected
  if ($LogPath.Trim()) {
    & $Python @installArguments *>> $LogPath
  } else {
    & $Python @installArguments
  }
  if ($LASTEXITCODE -ne 0) {
    throw "QuantX workspace wheel installation failed."
  }
}

function Expand-ReleaseBundle {
  if (-not $ReleasePath.Trim()) {
    throw "Production install requires -ReleasePath <release.zip>."
  }
  $archive = [System.IO.Path]::GetFullPath($ReleasePath)
  if (-not (Test-Path -LiteralPath $archive -PathType Leaf)) {
    throw "Release archive does not exist: $archive"
  }
  $outerChecksum = "$archive.sha256"
  if (Test-Path -LiteralPath $outerChecksum -PathType Leaf) {
    $expected = (
      (Get-Content -LiteralPath $outerChecksum -Raw).Trim() -split "\s+"
    )[0]
    $actual = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash
    if ($actual -cne $expected) {
      throw "Release archive SHA256 verification failed."
    }
  } else {
    throw "Release archive companion SHA256 file is missing."
  }

  $temporary = [System.IO.Path]::GetFullPath(
    (Join-Path $ReleaseDirectory ".extract-$([guid]::NewGuid().ToString('N'))")
  )
  if (-not $temporary.StartsWith(
    [System.IO.Path]::GetFullPath($ReleaseDirectory) +
      [System.IO.Path]::DirectorySeparatorChar,
    [StringComparison]::OrdinalIgnoreCase
  )) {
    throw "Temporary release path escaped the release directory."
  }
  New-Item -ItemType Directory -Path $temporary -Force | Out-Null
  try {
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [System.IO.Compression.ZipFile]::OpenRead($archive)
    try {
      foreach ($entry in $zip.Entries) {
        $candidate = [System.IO.Path]::GetFullPath(
          (Join-Path $temporary $entry.FullName)
        )
        if (-not (
          $candidate -eq $temporary -or
          $candidate.StartsWith(
            $temporary + [System.IO.Path]::DirectorySeparatorChar,
            [StringComparison]::OrdinalIgnoreCase
          )
        )) {
          throw "Release archive contains an unsafe path."
        }
      }
    } finally {
      $zip.Dispose()
    }
    Expand-Archive -LiteralPath $archive -DestinationPath $temporary -Force
    $manifest = Test-ReleaseContents -Directory $temporary
    $version = [string]$manifest.version
    $target = [System.IO.Path]::GetFullPath(
      (Join-Path $ReleaseDirectory $version)
    )
    if (-not $target.StartsWith(
      [System.IO.Path]::GetFullPath($ReleaseDirectory) +
        [System.IO.Path]::DirectorySeparatorChar,
      [StringComparison]::OrdinalIgnoreCase
    )) {
      throw "Release version resolved outside the release directory."
    }
    if (Test-Path -LiteralPath $target) {
      $installedManifest = Test-ReleaseContents -Directory $target
      if ([string]$installedManifest.version -cne $version) {
        throw "Installed release version identity is inconsistent."
      }
      return [pscustomobject]@{
        Root = $target
        Manifest = $installedManifest
      }
    }

    if (-not (Test-Path -LiteralPath $ProductionConfigFile -PathType Leaf)) {
      Copy-Item `
        -LiteralPath (Join-Path $temporary "apps\api\.env.production") `
        -Destination $ProductionConfigFile `
        -Force
      throw (
        "Created the persistent production configuration at " +
        "$ProductionConfigFile. Fill required secrets and endpoints, then " +
        "run install again."
      )
    }
    Move-Item -LiteralPath $temporary -Destination $target
    return [pscustomobject]@{
      Root = $target
      Manifest = $manifest
    }
  } finally {
    if (Test-Path -LiteralPath $temporary) {
      Remove-Item -LiteralPath $temporary -Recurse -Force
    }
  }
}

function Install-ReleasePythonEnvironments {
  param(
    [Parameter(Mandatory = $true)][string]$ReleaseRoot,
    [Parameter(Mandatory = $true)][string]$BasePython,
    [Parameter(Mandatory = $true)][string]$QmtPython
  )

  foreach ($runtime in @($BasePython, $QmtPython)) {
    $runtimeVersion = & $runtime -c "import platform; print(platform.python_version())"
    if (
      $LASTEXITCODE -ne 0 -or
      [version]$runtimeVersion -ne [version]"3.13.9"
    ) {
      throw "Production server and QMT runtimes must both be Python 3.13.9."
    }
  }
  $dependencyLog = Join-Path $ReleaseRoot "install-dependencies.log"
  Set-Content -LiteralPath $dependencyLog -Value "" -Encoding utf8
  $serverPython = Join-Path $ReleaseRoot ".venv\Scripts\python.exe"
  & $BasePython -m venv (Join-Path $ReleaseRoot ".venv")
  if ($LASTEXITCODE -ne 0) {
    throw "Failed to create the production server virtual environment."
  }
  $serverRequirements = Join-Path $ReleaseRoot "requirements\server.lock"
  $serverWheelhouse = Join-Path $ReleaseRoot "wheelhouse\server"
  & $serverPython -m pip install `
    --no-index `
    --find-links $serverWheelhouse `
    --requirement $serverRequirements *>> $dependencyLog
  if ($LASTEXITCODE -ne 0) {
    throw "Offline server dependency installation failed."
  }
  Install-WorkspaceWheels `
    -Python $serverPython `
    -WheelDirectory (Join-Path $ReleaseRoot "wheels") `
    -PackageNames @(
      "quantx-api",
      "quantx-ai-runtime",
      "quantx-engine",
      "quantx-worker",
      "quantx-application",
      "quantx-contracts",
      "quantx-domain",
      "quantx-infrastructure"
    ) `
    -LogPath $dependencyLog

  $qmtTarget = Join-Path $ReleaseRoot "qmt-site-packages"
  New-Item -ItemType Directory -Path $qmtTarget -Force | Out-Null
  $qmtInstallerRoot = Join-Path $ReleaseRoot ".qmt-installer"
  $qmtInstallerPython = Join-Path $qmtInstallerRoot "Scripts\python.exe"
  & $BasePython -m venv $qmtInstallerRoot
  if ($LASTEXITCODE -ne 0) {
    throw "Failed to create the isolated QMT dependency installer."
  }
  try {
    & $qmtInstallerPython -m pip install `
      --no-index `
      --find-links (Join-Path $ReleaseRoot "wheelhouse\qmt") `
      --requirement (Join-Path $ReleaseRoot "requirements\qmt-agent.lock") `
      --target $qmtTarget `
      --upgrade `
      --ignore-installed *>> $dependencyLog
    if ($LASTEXITCODE -ne 0) {
      throw "Offline QMT Agent dependency installation failed."
    }
    Install-WorkspaceWheels `
      -Python $qmtInstallerPython `
      -WheelDirectory (Join-Path $ReleaseRoot "wheels") `
      -PackageNames @("quantx-contracts", "quantx-qmt-agent") `
      -Target $qmtTarget `
      -LogPath $dependencyLog
  } finally {
    if (Test-Path -LiteralPath $qmtInstallerRoot -PathType Container) {
      Remove-Item -LiteralPath $qmtInstallerRoot -Recurse -Force
    }
  }
  return $serverPython
}

function Install-ReleaseTools {
  param([Parameter(Mandatory = $true)][string]$ReleaseRoot)

  foreach ($name in @("caddy", "winsw")) {
    $source = Join-Path $ReleaseRoot "tools\$name"
    $destination = Join-Path $ToolsDirectory $name
    New-Item -ItemType Directory -Path $destination -Force | Out-Null
    Copy-Item -Path (Join-Path $source "*") -Destination $destination `
      -Recurse -Force
  }
}

function Set-CurrentRelease {
  param(
    [Parameter(Mandatory = $true)][string]$ReleaseRoot,
    [Parameter(Mandatory = $true)][string]$Version
  )

  $previousVersion = ""
  if (Test-Path -LiteralPath $ReleaseStateFile -PathType Leaf) {
    $oldState = Get-Content -LiteralPath $ReleaseStateFile -Raw |
      ConvertFrom-Json
    $previousVersion = [string]$oldState.current
  }
  if (Test-Path -LiteralPath $CurrentReleaseLink) {
    $item = Get-Item -LiteralPath $CurrentReleaseLink -Force
    if (-not ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
      throw "Refusing to replace a non-link current path."
    }
    Remove-Item -LiteralPath $CurrentReleaseLink -Force
  }
  New-Item -ItemType Junction -Path $CurrentReleaseLink -Target $ReleaseRoot |
    Out-Null
  [pscustomobject]@{
    current = $Version
    previous = $previousVersion
    activatedAt = [datetime]::UtcNow.ToString("o")
  } | ConvertTo-Json | Set-Content -LiteralPath $ReleaseStateFile -Encoding utf8
}

function Install-AndStartServices {
  param(
    [Parameter(Mandatory = $true)][string]$Python,
    [Parameter(Mandatory = $true)][string]$QmtPython,
    [Parameter(Mandatory = $true)][string]$ServiceRoot
  )

  New-ServiceConfigurations `
    -Python $Python `
    -QmtPython $QmtPython `
    -ServiceRoot $ServiceRoot
  $configs = Get-ServiceConfigurationNames
  $configs = @(
    $configs
  )
  $installed = @()
  try {
    foreach ($config in $configs) {
      $wrapper = Join-Path `
        $ServiceDirectory `
        "$([System.IO.Path]::GetFileNameWithoutExtension($config)).exe"
      & $wrapper install
      if ($LASTEXITCODE -ne 0) {
        throw "Failed to install service config $config."
      }
      $installed += $wrapper
    }
  } catch {
    [array]::Reverse($installed)
    foreach ($wrapper in $installed) {
      & $wrapper uninstall
    }
    throw
  }
  $started = @()
  try {
    Initialize-PrefectEnvironment
    Wait-HttpReady `
      -Name "External Prefect Server" `
      -Url "$env:PREFECT_API_URL/health" `
      -TimeoutSeconds 90
    Invoke-PrefectPreparation `
      -Python $Python `
      -ApplicationRoot $ServiceRoot
    foreach ($config in $configs) {
      $wrapper = Join-Path `
        $ServiceDirectory `
        "$([System.IO.Path]::GetFileNameWithoutExtension($config)).exe"
      & $wrapper start
      if ($LASTEXITCODE -ne 0) {
        throw "Failed to start service config $config."
      }
      $started += $wrapper
    }
  } catch {
    [array]::Reverse($started)
    foreach ($wrapper in $started) {
      & $wrapper stop
    }
    [array]::Reverse($installed)
    foreach ($wrapper in $installed) {
      & $wrapper uninstall
    }
    throw
  }
  Write-Host "QuantX WinSW services were installed." -ForegroundColor Green
}

function Register-ProductionMaintenance {
  $scheduledTaskCommand = Get-Command Register-ScheduledTask `
    -ErrorAction SilentlyContinue
  if (-not $scheduledTaskCommand) {
    Write-Warning "ScheduledTasks module is unavailable; register backup manually."
    return
  }
  $powerShellCommand = Get-Command pwsh -ErrorAction SilentlyContinue
  if ($powerShellCommand) {
    $powerShell = $powerShellCommand.Source
  } else {
    $powerShell = (Get-Command powershell -ErrorAction Stop).Source
  }
  $arguments = (
    '-NoProfile -ExecutionPolicy Bypass -File "{0}" backup ' +
    '-Environment production'
  ) -f $PSCommandPath
  $action = New-ScheduledTaskAction `
    -Execute $powerShell `
    -Argument $arguments `
    -WorkingDirectory $Root
  $trigger = New-ScheduledTaskTrigger `
    -Daily `
    -At "16:30"
  $settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2)
  Register-ScheduledTask `
    -TaskName "QuantX-Daily-Backup" `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -RunLevel Highest `
    -Force |
    Out-Null
}

function Register-DevBackupMaintenance {
  $scheduledTaskCommand = Get-Command Register-ScheduledTask `
    -ErrorAction SilentlyContinue
  if (-not $scheduledTaskCommand) {
    Write-Warning (
      "ScheduledTasks module is unavailable; the dev live backup task was " +
      "not registered. Automatic trading will remain fail-closed when the " +
      "last successful backup exceeds 24 hours."
    )
    return $false
  }

  try {
    $powerShellCommand = Get-Command pwsh -ErrorAction SilentlyContinue
    if ($powerShellCommand) {
      $powerShell = $powerShellCommand.Source
    } else {
      $powerShell = (Get-Command powershell -ErrorAction Stop).Source
    }
    $arguments = (
      '-NoProfile -ExecutionPolicy Bypass -File "{0}" backup ' +
      '-Environment dev'
    ) -f $PSCommandPath
    $action = New-ScheduledTaskAction `
      -Execute $powerShell `
      -Argument $arguments `
      -WorkingDirectory $Root
    $trigger = New-ScheduledTaskTrigger -Daily -At "16:30"
    $settings = New-ScheduledTaskSettingsSet `
      -StartWhenAvailable `
      -MultipleInstances IgnoreNew `
      -ExecutionTimeLimit (New-TimeSpan -Hours 2)
    Register-ScheduledTask `
      -TaskName "QuantX-Dev-Daily-Backup" `
      -Action $action `
      -Trigger $trigger `
      -Settings $settings `
      -RunLevel Limited `
      -Force |
      Out-Null
    Write-Host (
      "QuantX dev daily backup task is registered for 16:30."
    ) -ForegroundColor Green
    return $true
  } catch {
    Write-Warning (
      "Failed to register QuantX-Dev-Daily-Backup: " +
      $_.Exception.Message
    )
    return $false
  }
}

function Install-CaddyRootCertificate {
  $certificate = Join-Path `
    $Runtime `
    "caddy-data\caddy\pki\authorities\local\root.crt"
  $deadline = [datetime]::UtcNow.AddSeconds(60)
  while (
    -not (Test-Path -LiteralPath $certificate -PathType Leaf) -and
    [datetime]::UtcNow -lt $deadline
  ) {
    Start-Sleep -Seconds 1
  }
  if (-not (Test-Path -LiteralPath $certificate -PathType Leaf)) {
    throw "Caddy local CA root was not generated in time."
  }
  $importCommand = Get-Command Import-Certificate -ErrorAction SilentlyContinue
  if ($importCommand) {
    Import-Certificate `
      -FilePath $certificate `
      -CertStoreLocation "Cert:\LocalMachine\Root" |
      Out-Null
  } else {
    & certutil.exe -addstore -f Root $certificate *> $null
    if ($LASTEXITCODE -ne 0) {
      throw "Caddy local CA trust installation failed."
    }
  }
}

function Invoke-Install {
  if ($Environment -ne "production") {
    throw "WinSW release installation requires -Environment production."
  }
  Assert-Administrator
  Import-QuantXEnvironment
  if (Test-Path -LiteralPath $AgentModeFile -PathType Leaf) {
    $agentState = Get-Content -LiteralPath $AgentModeFile -Raw |
      ConvertFrom-Json
    if ([string]$agentState.mode -eq "live") {
      throw "Set agent-mode to data-only before installing a release."
    }
  }
  $release = Expand-ReleaseBundle
  $releaseRoot = [string]$release.Root
  try {
    $basePython = Resolve-Python
    $qmtPython = Resolve-Python -Qmt
    $serverPython = Install-ReleasePythonEnvironments `
      -ReleaseRoot $releaseRoot `
      -BasePython $basePython `
      -QmtPython $qmtPython
    Install-ReleaseTools -ReleaseRoot $releaseRoot

    $env:QUANTX_ROOT = $releaseRoot
    $env:QUANTX_ENV_FILE = $ProductionConfigFile
    $env:QUANTX_AGENT_STATE_DIR = Join-Path $Runtime "qmt-agent"
    & $serverPython -c (
      "from quantx_infrastructure.config.settings import settings; " +
      "settings.validate_production(); print('configuration=valid')"
    )
    if ($LASTEXITCODE -ne 0) {
      throw "Production configuration validation failed."
    }
    Assert-QmtAgentEnrollment `
      -Python $qmtPython `
      -ServiceRoot $releaseRoot
    Invoke-MigrateAtRoot -Python $serverPython -ApplicationRoot $releaseRoot

    if (Test-Path -LiteralPath (
      Join-Path $ServiceDirectory "quantx-api.exe"
    )) {
      Invoke-Uninstall
    }
    Set-CurrentRelease `
      -ReleaseRoot $releaseRoot `
      -Version ([string]$release.Manifest.version)
    Initialize-PrefectEnvironment
    Install-AndStartServices `
      -Python $serverPython `
      -QmtPython $qmtPython `
      -ServiceRoot $CurrentReleaseLink

    Wait-TcpReady -Name "Caddy HTTPS" -Port 8080 -TimeoutSeconds 60
    Install-CaddyRootCertificate
    Invoke-Verify
    Register-ProductionMaintenance
  } catch {
    Write-Warning (
      "Release files were retained for diagnosis, but activation did not " +
      "complete: $($_.Exception.Message)"
    )
    throw
  }
  Write-Host (
    "QuantX release $($release.Manifest.version) is installed and verified."
  ) -ForegroundColor Green
}

function Invoke-Uninstall {
  Assert-Administrator
  $winswSource = Join-Path $ToolsDirectory "winsw\WinSW-x64.exe"
  if (-not (Test-Path -LiteralPath $winswSource -PathType Leaf)) {
    throw "WinSW is missing. Run bootstrap first."
  }
  $configs = @(
    "quantx-qmt-agent.xml",
    "quantx-caddy.xml",
    "quantx-market-gateway.xml",
    "quantx-worker.xml",
    "quantx-prefect-server.xml",
    "quantx-ai-runtime.xml",
    "quantx-engine.xml",
    "quantx-api.xml"
  )
  foreach ($config in $configs) {
    $path = Join-Path $ServiceDirectory $config
    $wrapper = Join-Path `
      $ServiceDirectory `
      "$([System.IO.Path]::GetFileNameWithoutExtension($config)).exe"
    if (
      (Test-Path -LiteralPath $path -PathType Leaf) -and
      (Test-Path -LiteralPath $wrapper -PathType Leaf)
    ) {
      & $wrapper stop
      & $wrapper uninstall
    }
  }
  Write-Host "QuantX WinSW services were uninstalled." -ForegroundColor Green
}

function Invoke-Doctor {
  Import-QuantXEnvironment
  $python = Resolve-Python
  $version = & $python -c "import platform; print(platform.python_version())"
  if ($LASTEXITCODE -ne 0) {
    throw "Python runtime could not be inspected."
  }
  $parsed = [version]$version
  if ($parsed -lt [version]"3.11" -or $parsed -ge [version]"3.14") {
    throw "Python $version is unsupported; expected >=3.11,<3.14."
  }
  if ($Environment -eq "production" -and $parsed -ne [version]"3.13.9") {
    throw "Production requires the validated Python 3.13.9 runtime."
  }

  $nodeVersion = ""
  if ($Environment -eq "production") {
    if (-not (Test-Path -LiteralPath $CurrentReleaseLink -PathType Container)) {
      throw "No current versioned production release is active."
    }
    $manifest = Test-ReleaseContents -Directory $CurrentReleaseLink
    try {
      $releaseNodeVersion = [version][string]$manifest.node
    } catch {
      throw "Release build runtime manifest contains an invalid Node version."
    }
    if (
      [string]$manifest.python -cne "3.13.9" -or
      $releaseNodeVersion.Major -ne 20
    ) {
      throw "Release build runtime manifest is not production-approved."
    }
    $nodeVersion = "packaged-$($releaseNodeVersion.ToString())"
  } else {
    $node = Resolve-Node
    $nodeVersion = (& $node --version).TrimStart("v")
    if (([version]$nodeVersion).Major -ne 20) {
      Write-Warning "Node 20.x is the supported build runtime; found $nodeVersion."
    }
    foreach ($required in @("pyproject.toml", "uv.lock", "package-lock.json")) {
      if (-not (
        Test-Path -LiteralPath (Join-Path $Root $required) -PathType Leaf
      )) {
        throw "Required locked workspace file is missing: $required"
      }
    }
  }

  $previousPythonPath = $env:PYTHONPATH
  try {
    $env:PYTHONPATH = Get-WorkspacePythonPath
    & $python -c (
      "from quantx_infrastructure.config.settings import settings; " +
      "settings.validate_production(); print('configuration=valid')"
    )
    if ($LASTEXITCODE -ne 0) {
      throw "QuantX configuration validation failed."
    }
  } finally {
    $env:PYTHONPATH = $previousPythonPath
  }

  if (-not $SkipExternal) {
    Show-ExternalDependencies -Python $python
  }
  Write-Host (
    "Doctor passed: environment={0} python={1} node={2}" -f
    $Environment,
    $version,
    $nodeVersion
  ) -ForegroundColor Green
}

function Get-PostgreSqlConnectionParts {
  if (-not $env:DATABASE_URL) {
    throw "DATABASE_URL is required."
  }
  $normalized = $env:DATABASE_URL -replace (
    "^postgresql\+asyncpg://",
    "postgresql://"
  )
  try {
    $uri = [uri]$normalized
  } catch {
    throw "DATABASE_URL is not a valid PostgreSQL URL."
  }
  if ($uri.Scheme -ne "postgresql" -or -not $uri.Host) {
    throw "DATABASE_URL must be a PostgreSQL URL."
  }
  $userInfo = $uri.UserInfo.Split(":", 2)
  if ($userInfo.Count -lt 1 -or -not $userInfo[0]) {
    throw "DATABASE_URL must include a database user."
  }
  return [pscustomobject]@{
    Host = $uri.Host
    Port = if ($uri.Port -gt 0) { $uri.Port } else { 5432 }
    Database = [uri]::UnescapeDataString($uri.AbsolutePath.TrimStart("/"))
    User = [uri]::UnescapeDataString($userInfo[0])
    Password = if ($userInfo.Count -eq 2) {
      [uri]::UnescapeDataString($userInfo[1])
    } else {
      ""
    }
  }
}

function Resolve-PostgreSqlTool {
  param(
    [ValidateSet("pg_dump", "pg_restore", "createdb", "dropdb")]
    [string]$Name
  )

  $command = Get-Command $Name -ErrorAction SilentlyContinue
  if ($command) {
    return $command.Source
  }
  $programFiles = [Environment]::GetFolderPath(
    [Environment+SpecialFolder]::ProgramFiles
  )
  $postgresRoot = Join-Path $programFiles "PostgreSQL"
  if (Test-Path -LiteralPath $postgresRoot -PathType Container) {
    $candidate = @(
      Get-ChildItem -LiteralPath $postgresRoot -Directory |
        Sort-Object Name -Descending |
        ForEach-Object { Join-Path $_.FullName "bin\$Name.exe" } |
        Where-Object { Test-Path -LiteralPath $_ -PathType Leaf }
    ) | Select-Object -First 1
    if ($candidate) {
      return [System.IO.Path]::GetFullPath($candidate)
    }
  }
  $wslWrapper = Join-Path $PSScriptRoot "tools\postgresql-wsl\$Name.ps1"
  $wsl = Get-Command wsl.exe -ErrorAction SilentlyContinue
  if ($wsl -and (Test-Path -LiteralPath $wslWrapper -PathType Leaf)) {
    & $wsl.Source sh -lc "command -v '$Name' >/dev/null 2>&1"
    if ($LASTEXITCODE -eq 0) {
      return [System.IO.Path]::GetFullPath($wslWrapper)
    }
  }
  throw "$Name was not found. Install PostgreSQL client tools."
}

function Invoke-Backup {
  Import-QuantXEnvironment
  Ensure-RuntimeDirectories
  $python = Resolve-Python
  $qmtPython = Resolve-Python -Qmt
  $connection = Get-PostgreSqlConnectionParts
  if (-not $connection.Database) {
    throw "DATABASE_URL must include a database name."
  }
  $destination = if ($BackupPath.Trim()) {
    [System.IO.Path]::GetFullPath($BackupPath)
  } else {
    Join-Path $BackupDirectory (
      [datetime]::UtcNow.ToString("yyyyMMddTHHmmssZ")
    )
  }
  if (Test-Path -LiteralPath $destination) {
    if (Get-ChildItem -LiteralPath $destination -Force | Select-Object -First 1) {
      throw "Backup destination must be empty: $destination"
    }
  } else {
    New-Item -ItemType Directory -Path $destination -Force | Out-Null
  }

  $databaseBackup = Join-Path $destination "postgres.dump"
  $pgDump = Resolve-PostgreSqlTool -Name "pg_dump"
  $previousPassword = $env:PGPASSWORD
  try {
    $env:PGPASSWORD = $connection.Password
    & $pgDump `
      --host $connection.Host `
      --port ([string]$connection.Port) `
      --username $connection.User `
      --dbname $connection.Database `
      --format custom `
      --no-owner `
      --no-privileges `
      --file $databaseBackup
    if ($LASTEXITCODE -ne 0) {
      throw "pg_dump failed with exit code $LASTEXITCODE."
    }
  } finally {
    $env:PGPASSWORD = $previousPassword
  }

  $agentDestination = Join-Path $destination "qmt-agent"
  $previousPythonPath = $env:PYTHONPATH
  try {
    $env:PYTHONPATH = Get-QmtAgentPythonPath
    & $qmtPython -m quantx_qmt_agent.main backup-state `
      --destination $agentDestination
    if ($LASTEXITCODE -ne 0) {
      throw "QMT Agent journal backup failed."
    }
    & $qmtPython -m quantx_qmt_agent.main prune-journal `
      --retention-days 90
    if ($LASTEXITCODE -ne 0) {
      Write-Warning "Backup succeeded, but QMT journal retention failed."
    }
  } finally {
    $env:PYTHONPATH = $previousPythonPath
  }

  $files = @(
    Get-ChildItem -LiteralPath $destination -File -Recurse |
      Sort-Object FullName |
      ForEach-Object {
        [pscustomobject]@{
          relativePath = [System.IO.Path]::GetRelativePath(
            $destination,
            $_.FullName
          ).Replace("\", "/")
          length = $_.Length
          sha256 = (
            Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256
          ).Hash.ToLowerInvariant()
        }
      }
  )
  $manifestPath = Join-Path $destination "manifest.json"
  [pscustomobject]@{
    formatVersion = 1
    createdAt = [datetime]::UtcNow.ToString("o")
    database = $connection.Database
    deviceSecretsExported = $false
    files = $files
  } | ConvertTo-Json -Depth 6 |
    Set-Content -LiteralPath $manifestPath -Encoding utf8

  $previousPythonPath = $env:PYTHONPATH
  try {
    $env:PYTHONPATH = Get-WorkspacePythonPath
    & $python -m quantx_infrastructure.database.backup_registry $manifestPath
    if ($LASTEXITCODE -ne 0) {
      throw "Backup files were created, but the successful backup was not recorded."
    }
  } finally {
    $env:PYTHONPATH = $previousPythonPath
  }
  Write-Host "Backup completed: $destination" -ForegroundColor Green
}

function Test-RestoreVerificationScratchDatabaseName {
  param([Parameter(Mandatory = $true)][string]$Name)

  return $Name -cmatch "^quantx_restore_verify_[0-9a-f]{16}$"
}

function Get-RestoreVerificationSchemaStatus {
  param([Parameter(Mandatory = $true)][string]$Python)

  [array]$statusOutput = @(
    & $Python -m quantx_infrastructure.database.schema_control status
  )
  if ($LASTEXITCODE -ne 0) {
    throw "Restored database schema revision inspection failed."
  }
  $statusPayload = [string]::Join(
    [Environment]::NewLine,
    @($statusOutput | ForEach-Object { [string]$_ })
  )
  if (-not $statusPayload.Trim()) {
    throw "Restored database schema revision inspection returned no status."
  }
  try {
    $parsed = @($statusPayload | ConvertFrom-Json -ErrorAction Stop)
  } catch {
    throw "Restored database schema revision inspection returned invalid JSON."
  }
  if ($parsed.Count -ne 1 -or $null -eq $parsed[0]) {
    throw "Restored database schema revision inspection returned an invalid status."
  }
  $status = $parsed[0]
  $relationProperty = $status.PSObject.Properties["revision_relation"]
  if (
    $null -eq $relationProperty -or
    $relationProperty.Value -isnot [string] -or
    -not ([string]$relationProperty.Value).Trim()
  ) {
    throw "Restored database schema revision inspection omitted revision_relation."
  }
  return $status
}

function Invoke-RestoreVerificationSchemaGate {
  param(
    [Parameter(Mandatory = $true)][string]$Python,
    [Parameter(Mandatory = $true)][string]$ApplicationRoot
  )

  $status = Get-RestoreVerificationSchemaStatus -Python $Python
  $relation = [string]$status.revision_relation
  switch -CaseSensitive ($relation) {
    "current" {
      Write-Host "Restored database schema is already at this release's head."
      break
    }
    "behind" {
      Write-Host (
        "Restored database schema is behind this release; upgrading only " +
        "the isolated verification database."
      )
      & $Python -m alembic `
        -c (Join-Path $ApplicationRoot "alembic.ini") upgrade head
      if ($LASTEXITCODE -ne 0) {
        throw (
          "Isolated restored database Alembic upgrade failed; the " +
          "production database was not modified."
        )
      }
      break
    }
    default {
      throw (
        "Restored database revision relation '$relation' is not eligible " +
        "for forward restore verification; only current or behind revisions " +
        "are allowed."
      )
    }
  }

  & $Python -m quantx_infrastructure.database.schema_control check
  if ($LASTEXITCODE -ne 0) {
    throw "Restored database does not match this release's Alembic head."
  }
}

function Remove-RestoreVerificationScratchDatabase {
  param(
    [Parameter(Mandatory = $true)][string]$DropDatabase,
    [Parameter(Mandatory = $true)][psobject]$Connection,
    [Parameter(Mandatory = $true)][string]$ScratchDatabase,
    [Parameter(Mandatory = $true)][bool]$ScratchCreated
  )

  if (-not $ScratchCreated) {
    return
  }
  if (-not (Test-RestoreVerificationScratchDatabaseName -Name $ScratchDatabase)) {
    Write-Warning (
      "Refusing to remove restore verification database with unsafe name: " +
      "$ScratchDatabase"
    )
    return
  }
  & $DropDatabase `
    --host $Connection.Host `
    --port ([string]$Connection.Port) `
    --username $Connection.User `
    --if-exists `
    $ScratchDatabase
  if ($LASTEXITCODE -ne 0) {
    Write-Warning (
      "Could not remove isolated verification database " +
      "$ScratchDatabase; remove it manually."
    )
  }
}

function Invoke-RestoreVerify {
  Import-QuantXEnvironment
  if (-not $BackupPath.Trim()) {
    throw "restore-verify requires -BackupPath."
  }
  $source = [System.IO.Path]::GetFullPath($BackupPath)
  $manifestPath = Join-Path $source "manifest.json"
  if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
    throw "Backup manifest is missing: $manifestPath"
  }
  $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
  foreach ($file in @($manifest.files)) {
    $path = Join-Path $source ([string]$file.relativePath)
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
      throw "Backup file is missing: $($file.relativePath)"
    }
    $length = (Get-Item -LiteralPath $path).Length
    $hash = (
      Get-FileHash -LiteralPath $path -Algorithm SHA256
    ).Hash.ToLowerInvariant()
    if (
      $length -ne [long]$file.length -or
      $hash -cne ([string]$file.sha256).ToLowerInvariant()
    ) {
      throw "Backup checksum mismatch: $($file.relativePath)"
    }
  }

  $archive = Join-Path $source "postgres.dump"
  $pgRestore = Resolve-PostgreSqlTool -Name "pg_restore"
  & $pgRestore --list $archive *> $null
  if ($LASTEXITCODE -ne 0) {
    throw "PostgreSQL backup archive validation failed."
  }

  $connection = Get-PostgreSqlConnectionParts
  $createdb = Resolve-PostgreSqlTool -Name "createdb"
  $dropdb = Resolve-PostgreSqlTool -Name "dropdb"
  $scratchDatabase = "quantx_restore_verify_$(
    [guid]::NewGuid().ToString('N').Substring(0, 16)
  )"
  if (-not (Test-RestoreVerificationScratchDatabaseName -Name $scratchDatabase)) {
    throw "Generated restore verification database name is unsafe."
  }
  $python = Resolve-Python
  $applicationRoot = if (
    $Environment -eq "production" -and
    (Test-Path -LiteralPath $CurrentReleaseLink -PathType Container)
  ) {
    $CurrentReleaseLink
  } else {
    $Root
  }
  $previousPassword = $env:PGPASSWORD
  $previousDatabaseUrl = $env:DATABASE_URL
  $previousPythonPath = $env:PYTHONPATH
  $previousRoot = $env:QUANTX_ROOT
  $scratchCreated = $false
  try {
    $env:PGPASSWORD = $connection.Password
    & $createdb `
      --host $connection.Host `
      --port ([string]$connection.Port) `
      --username $connection.User `
      --template template0 `
      --encoding UTF8 `
      $scratchDatabase
    if ($LASTEXITCODE -ne 0) {
      throw "Could not create the isolated restore verification database."
    }
    $scratchCreated = $true

    & $pgRestore `
      --host $connection.Host `
      --port ([string]$connection.Port) `
      --username $connection.User `
      --dbname $scratchDatabase `
      --exit-on-error `
      --no-owner `
      --no-privileges `
      $archive
    if ($LASTEXITCODE -ne 0) {
      throw "PostgreSQL backup could not be restored into isolation."
    }

    $encodedUser = [uri]::EscapeDataString($connection.User)
    $encodedPassword = [uri]::EscapeDataString($connection.Password)
    $hostPart = if ($connection.Host.Contains(":")) {
      "[$($connection.Host)]"
    } else {
      $connection.Host
    }
    $credentials = if ($connection.Password) {
      "$encodedUser`:$encodedPassword"
    } else {
      $encodedUser
    }
    $env:DATABASE_URL = (
      "postgresql+asyncpg://{0}@{1}:{2}/{3}" -f
      $credentials,
      $hostPart,
      $connection.Port,
      $scratchDatabase
    )
    $env:PYTHONPATH = if ($Environment -eq "production") {
      ""
    } else {
      Get-WorkspacePythonPath
    }
    $env:QUANTX_ROOT = $applicationRoot
    Invoke-RestoreVerificationSchemaGate `
      -Python $python `
      -ApplicationRoot $applicationRoot
  } finally {
    $env:DATABASE_URL = $previousDatabaseUrl
    $env:PYTHONPATH = $previousPythonPath
    $env:QUANTX_ROOT = $previousRoot
    Remove-RestoreVerificationScratchDatabase `
      -DropDatabase $dropdb `
      -Connection $connection `
      -ScratchDatabase $scratchDatabase `
      -ScratchCreated $scratchCreated
    $env:PGPASSWORD = $previousPassword
  }

  $journal = Join-Path $source "qmt-agent\idempotency.sqlite3"
  if (-not (Test-Path -LiteralPath $journal -PathType Leaf)) {
    throw "QMT Agent journal backup is missing."
  }
  $qmtPython = Resolve-Python -Qmt
  $previousPythonPath = $env:PYTHONPATH
  try {
    $env:PYTHONPATH = Get-QmtAgentPythonPath
    & $qmtPython -c (
      "import pathlib,sys;" +
      "from quantx_qmt_agent.journal import LocalJournal;" +
      "j=LocalJournal(pathlib.Path(sys.argv[1]));" +
      "j.integrity_check();print(j.stats())"
    ) $journal
    if ($LASTEXITCODE -ne 0) {
      throw "QMT Agent journal integrity validation failed."
    }
  } finally {
    $env:PYTHONPATH = $previousPythonPath
  }
  Write-Host (
    "Backup restored in isolation and verified: $source"
  ) -ForegroundColor Green
}

function Invoke-MigrateAtRoot {
  param(
    [Parameter(Mandatory = $true)][string]$Python,
    [Parameter(Mandatory = $true)][string]$ApplicationRoot
  )

  $previousPythonPath = $env:PYTHONPATH
  $previousRoot = $env:QUANTX_ROOT
  try {
    $env:PYTHONPATH = if ($Environment -eq "production") {
      ""
    } else {
      Get-WorkspacePythonPath
    }
    $env:QUANTX_ROOT = $ApplicationRoot
    $statusText = & $Python -m `
      quantx_infrastructure.database.schema_control status
    if ($LASTEXITCODE -ne 0) {
      throw "Database schema status inspection failed."
    }
    $status = $statusText | ConvertFrom-Json
    if ($status.revision_relation -eq "incompatible") {
      throw (
        "Database revision is unknown or ahead of this release: " +
        "$($status.current_heads -join ', ')"
      )
    }
    if ($status.revision_relation -eq "current") {
      Write-Host "Database schema is already at head." -ForegroundColor Green
      return
    }
    if ([int]$status.table_count -gt 0) {
      Invoke-Backup
    }
    if (
      $status.revision_relation -eq "unversioned" -and
      [int]$status.table_count -gt 0
    ) {
      if (-not $StampExisting) {
        throw (
          "Existing unversioned database requires -StampExisting after " +
          "schema doctor review."
        )
      }
      & $Python -m quantx_infrastructure.database.schema_control doctor
      if ($LASTEXITCODE -ne 0) {
        throw "Existing database does not match the QuantX baseline."
      }
      & $Python -m alembic -c (Join-Path $ApplicationRoot "alembic.ini") `
        stamp "20260729_0001"
      if ($LASTEXITCODE -ne 0) {
        throw "Alembic baseline stamp failed."
      }
    }
    & $Python -m alembic `
      -c (Join-Path $ApplicationRoot "alembic.ini") upgrade head
    if ($LASTEXITCODE -ne 0) {
      throw "Alembic upgrade failed. Restore from the pre-upgrade backup."
    }
    & $Python -m quantx_infrastructure.database.schema_control check
    if ($LASTEXITCODE -ne 0) {
      throw "Database did not reach the expected Alembic head."
    }
  } finally {
    $env:PYTHONPATH = $previousPythonPath
    $env:QUANTX_ROOT = $previousRoot
  }
  Write-Host "Database migration completed." -ForegroundColor Green
}

function Invoke-Migrate {
  Import-QuantXEnvironment
  $python = Resolve-Python
  $applicationRoot = if (
    $Environment -eq "production" -and
    (Test-Path -LiteralPath $CurrentReleaseLink -PathType Container)
  ) {
    $CurrentReleaseLink
  } else {
    $Root
  }
  Invoke-MigrateAtRoot `
    -Python $python `
    -ApplicationRoot $applicationRoot
}

function Invoke-Verify {
  Import-QuantXEnvironment
  $baseUrl = if ($Environment -eq "production") {
    "https://127.0.0.1:8080"
  } else {
    "http://127.0.0.1:8080"
  }
  foreach ($endpoint in @("/health/live", "/health/ready")) {
    $response = Invoke-WebRequest `
      -Uri "$baseUrl$endpoint" `
      -TimeoutSec 10 `
      -UseBasicParsing
    if ($response.StatusCode -ne 200) {
      throw "Verification failed for $endpoint."
    }
  }
  $body = '{"query":"query VerifyGateway { __typename }"}'
  $graphql = Invoke-WebRequest `
    -Method Post `
    -Uri "$baseUrl/graphql" `
    -ContentType "application/json" `
    -Body $body `
    -TimeoutSec 10 `
    -UseBasicParsing
  if ($graphql.StatusCode -ne 200) {
    throw "GraphQL gateway verification failed."
  }
  if ($Environment -eq "production") {
    $python = Resolve-Python
    $previousPythonPath = $env:PYTHONPATH
    try {
      $env:PYTHONPATH = Get-WorkspacePythonPath
      & $python -m quantx_infrastructure.database.schema_control check
      if ($LASTEXITCODE -ne 0) {
        throw "Production database revision verification failed."
      }
    } finally {
      $env:PYTHONPATH = $previousPythonPath
    }
  }
  Write-Host "Gateway and schema verification passed." -ForegroundColor Green
}

function Invoke-Rollback {
  if ($Environment -ne "production") {
    throw "Rollback requires -Environment production."
  }
  Assert-Administrator
  if (-not (Test-Path -LiteralPath $ReleaseStateFile -PathType Leaf)) {
    throw "No versioned release state exists; rollback is unavailable."
  }
  if (Test-Path -LiteralPath $AgentModeFile -PathType Leaf) {
    $agentState = Get-Content -LiteralPath $AgentModeFile -Raw |
      ConvertFrom-Json
    if ([string]$agentState.mode -eq "live") {
      throw "Set agent-mode to data-only before rollback."
    }
  }
  $state = Get-Content -LiteralPath $ReleaseStateFile -Raw | ConvertFrom-Json
  if (-not $state.previous) {
    throw "No previous release is retained."
  }
  $previous = [System.IO.Path]::GetFullPath(
    (Join-Path $ReleaseDirectory ([string]$state.previous))
  )
  $releaseRoot = [System.IO.Path]::GetFullPath($ReleaseDirectory)
  if (
    -not $previous.StartsWith(
      $releaseRoot + [System.IO.Path]::DirectorySeparatorChar,
      [StringComparison]::OrdinalIgnoreCase
    ) -or
    -not (Test-Path -LiteralPath $previous -PathType Container)
  ) {
    throw "Previous release target is invalid."
  }
  $manifest = Test-ReleaseContents -Directory $previous
  $previousPython = Join-Path $previous ".venv\Scripts\python.exe"
  if (-not (Test-Path -LiteralPath $previousPython -PathType Leaf)) {
    throw "Previous release server environment is missing."
  }
  Import-QuantXEnvironment
  $oldRoot = $env:QUANTX_ROOT
  try {
    $env:QUANTX_ROOT = $previous
    & $previousPython -m quantx_infrastructure.database.schema_control check
    if ($LASTEXITCODE -ne 0) {
      throw (
        "Previous release is not compatible with the current database " +
        "revision; automatic database downgrade is forbidden."
      )
    }
  } finally {
    $env:QUANTX_ROOT = $oldRoot
  }

  Invoke-Uninstall
  Set-CurrentRelease `
    -ReleaseRoot $previous `
    -Version ([string]$manifest.version)
  $qmtPython = Resolve-Python -Qmt
  Install-AndStartServices `
    -Python (Join-Path $CurrentReleaseLink ".venv\Scripts\python.exe") `
    -QmtPython $qmtPython `
    -ServiceRoot $CurrentReleaseLink
  Invoke-Verify
  Write-Host "Release rolled back and verified: $($state.previous)" `
    -ForegroundColor Green
}

function Invoke-AgentMode {
  Import-QuantXEnvironment
  if ($Mode -ne "data-only" -and -not $AccountId.Trim()) {
    throw "$Mode mode requires -AccountId."
  }
  if ($Mode -eq "live") {
    if ($Environment -ne "production") {
      throw "Managed live mode requires -Environment production."
    }
    $expected = "LIVE:$($AccountId.Trim())"
    if ($ConfirmLive -cne $expected) {
      throw "Live mode requires -ConfirmLive '$expected'."
    }
    foreach ($name in @(
      "ENABLE_REAL_TRADING",
      "QMT_REAL_TRADING_ENABLED",
      "T_TRADE_LIVE_ENABLED"
    )) {
      $flag = [Environment]::GetEnvironmentVariable($name)
      if (-not $flag -or $flag.Trim().ToLowerInvariant() -ne "true") {
        throw "$name=true is required for managed live mode."
      }
    }
    $allowlist = @(
      ConvertFrom-ListSetting -Value (
        [Environment]::GetEnvironmentVariable(
          "REAL_TRADING_ACCOUNT_ALLOWLIST"
        )
      )
    )
    if ($AccountId.Trim() -notin $allowlist) {
      throw "Account is not present in REAL_TRADING_ACCOUNT_ALLOWLIST."
    }
    Invoke-Doctor
  }

  Ensure-RuntimeDirectories
  [pscustomobject]@{
    mode = $Mode
    accountId = if ($Mode -eq "data-only") { "" } else { $AccountId.Trim() }
    changedAt = [datetime]::UtcNow.ToString("o")
    reason = $Reason.Trim()
  } | ConvertTo-Json | Set-Content -LiteralPath $AgentModeFile -Encoding utf8

  $qmtWrapper = Join-Path $ServiceDirectory "quantx-qmt-agent.exe"
  if (Test-Path -LiteralPath $qmtWrapper -PathType Leaf) {
    Assert-Administrator
    & $qmtWrapper stop
    $python = Resolve-Python
    $qmtPython = Resolve-Python -Qmt
    $serviceRoot = if (
      $Environment -eq "production" -and
      (Test-Path -LiteralPath $CurrentReleaseLink -PathType Container)
    ) {
      $CurrentReleaseLink
    } else {
      $Root
    }
    New-ServiceConfigurations `
      -Python $python `
      -QmtPython $qmtPython `
      -ServiceRoot $serviceRoot
    & $qmtWrapper start
    if ($LASTEXITCODE -ne 0) {
      throw "QMT Agent service failed to restart in $Mode mode."
    }
  }
  Write-Host "QMT Agent mode configured as $Mode." -ForegroundColor Green
}

Ensure-RuntimeDirectories
if ($Component -and $Command -notin @("up", "logs")) {
  throw "-Component is only supported by up and logs."
}
switch ($Command) {
  "up" { Invoke-Up }
  "down" { Invoke-Down }
  "status" { Invoke-Status }
  "logs" { Invoke-Logs }
  "bootstrap" { Invoke-Bootstrap }
  "install" { Invoke-Install }
  "uninstall" { Invoke-Uninstall }
  "doctor" { Invoke-Doctor }
  "agent-mode" { Invoke-AgentMode }
  "backup" { Invoke-Backup }
  "restore-verify" { Invoke-RestoreVerify }
  "migrate" { Invoke-Migrate }
  "verify" { Invoke-Verify }
  "rollback" { Invoke-Rollback }
}
