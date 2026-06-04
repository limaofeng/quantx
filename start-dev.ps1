param(
  [int]$BackendPort = 8080,
  [int]$FrontendPort = 5250,
  [int[]]$LegacyFrontendPorts = @(5173),
  [int]$StopTimeoutSeconds = 20,
  [int]$StartTimeoutSeconds = 90,
  [string]$CondaEnvName = "xtquant-demo",
  [string]$PrefectWorkerPool = "quantx-pool",
  [string]$BackendPython = "",
  [switch]$StopOnly,
  [switch]$Hidden,
  [switch]$DryRun
)

$ErrorActionPreference = "Stop"

$RootDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$BackendDir = Join-Path $RootDir "backend"
$FrontendDir = Join-Path $RootDir "frontend"
$StateDir = Join-Path $RootDir ".quantx-dev"
$BackendPidFile = Join-Path $StateDir "backend.pid"
$FrontendPidFile = Join-Path $StateDir "frontend.pid"
$BackendRunner = Join-Path $StateDir "run-backend.bat"
$FrontendRunner = Join-Path $StateDir "run-frontend.bat"

function Write-Info {
  param([string]$Message)
  Write-Host "[INFO] $Message"
}

function Write-Warn {
  param([string]$Message)
  Write-Host "[WARN] $Message" -ForegroundColor Yellow
}

function Write-Ok {
  param([string]$Message)
  Write-Host "[OK] $Message" -ForegroundColor Green
}

function Test-RequiredPath {
  param(
    [string]$Path,
    [string]$Description
  )

  if (-not (Test-Path -LiteralPath $Path)) {
    throw "$Description not found: $Path"
  }
}

function Get-ListeningPids {
  param([int]$Port)

  try {
    $connections = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop
    return @(
      $connections |
        Select-Object -ExpandProperty OwningProcess -Unique |
        Where-Object { $_ -and $_ -ne 0 }
    )
  } catch {
    $lines = netstat -ano -p tcp | Select-String -Pattern "LISTENING"
    $pids = foreach ($line in $lines) {
      $parts = @($line.ToString() -split "\s+" | Where-Object { $_ })
      if ($parts.Count -ge 5 -and $parts[1] -match ":$Port$") {
        [int]$parts[$parts.Count - 1]
      }
    }

    return @($pids | Sort-Object -Unique)
  }
}

function Get-ProcessDetails {
  param([int]$ProcessIdValue)

  Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessIdValue" -ErrorAction SilentlyContinue
}

function Get-ChildProcessIds {
  param([int]$ParentProcessId)

  @(
    Get-CimInstance Win32_Process -Filter "ParentProcessId = $ParentProcessId" -ErrorAction SilentlyContinue |
      Select-Object -ExpandProperty ProcessId
  )
}

function Format-CommandLine {
  param([string]$CommandLine)

  if (-not $CommandLine) {
    return ""
  }

  $compact = $CommandLine -replace "\s+", " "
  if ($compact.Length -le 180) {
    return $compact
  }

  return $compact.Substring(0, 180) + "..."
}

function Normalize-TextForMatch {
  param([string]$Text)

  if (-not $Text) {
    return ""
  }

  return ($Text -replace "/", "\").ToLowerInvariant()
}

function Get-DecodedPowerShellCommand {
  param([string]$CommandLine)

  if (-not $CommandLine) {
    return ""
  }

  if ($CommandLine -match "(?i)-encodedcommand\s+([A-Za-z0-9+/=]+)") {
    try {
      return [Text.Encoding]::Unicode.GetString(
        [Convert]::FromBase64String($Matches[1])
      )
    } catch {
      return ""
    }
  }

  return ""
}

function Test-QuantXServiceProcess {
  param($ProcessInfo)

  if ($null -eq $ProcessInfo -or -not $ProcessInfo.CommandLine) {
    return $false
  }

  if ($ProcessInfo.ProcessId -eq $PID) {
    return $false
  }

  $commandLine = Normalize-TextForMatch -Text $ProcessInfo.CommandLine
  $decodedCommand = Normalize-TextForMatch -Text (
    Get-DecodedPowerShellCommand -CommandLine $ProcessInfo.CommandLine
  )
  $combinedCommand = "$commandLine`n$decodedCommand"

  $backendDirText = Normalize-TextForMatch -Text $BackendDir
  $frontendDirText = Normalize-TextForMatch -Text $FrontendDir
  $backendRunnerText = Normalize-TextForMatch -Text $BackendRunner
  $frontendRunnerText = Normalize-TextForMatch -Text $FrontendRunner
  $condaEnvText = [regex]::Escape($CondaEnvName.ToLowerInvariant())
  $prefectPoolText = [regex]::Escape($PrefectWorkerPool.ToLowerInvariant())

  if (
    $combinedCommand.Contains($backendRunnerText) -or
    $combinedCommand.Contains($frontendRunnerText)
  ) {
    return $true
  }

  if (
    $combinedCommand.Contains("quantx backend") -or
    $combinedCommand.Contains("quantx frontend")
  ) {
    return $true
  }

  if (
    $combinedCommand -match 'python(?:\.exe)?"?\s+main\.py(?:\s|$)' -or
    (
      $combinedCommand.Contains($backendDirText) -and
      $combinedCommand.Contains("main.py") -and
      $combinedCommand.Contains("python")
    )
  ) {
    return $true
  }

  if (
    $combinedCommand -match "prefect\s+worker\s+start\s+--pool\s+$prefectPoolText" -or
    $combinedCommand -match "conda(?:\.exe)?\s+run\s+-n\s+$condaEnvText\s+python\s+-m\s+prefect\s+worker\s+start\s+--pool\s+$prefectPoolText"
  ) {
    return $true
  }

  if (
    $combinedCommand.Contains("quantx_mcp") -or
    $combinedCommand.Contains("quantx-mcp")
  ) {
    return $true
  }

  $frontendPorts = @($FrontendPort) + $LegacyFrontendPorts
  foreach ($port in $frontendPorts | Sort-Object -Unique) {
    if (
      $combinedCommand -match "vite(?:\.js)?[^`n]*--port\s+$port(?:\s|$)" -or
      $combinedCommand -match "npm-cli\.js[^`n]*run\s+dev[^`n]*--port\s+$port(?:\s|$)"
    ) {
      return $true
    }
  }

  if (
    $combinedCommand.Contains($frontendDirText) -and
    (
      $combinedCommand.Contains("vite") -or
      $combinedCommand.Contains("esbuild") -or
      $combinedCommand.Contains("npm-cli.js")
    )
  ) {
    return $true
  }

  return $false
}

function Get-QuantXServiceProcessIds {
  $matches = @()
  $processes = @(
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue
  )

  foreach ($processInfo in $processes) {
    if (Test-QuantXServiceProcess -ProcessInfo $processInfo) {
      $matches += [int]$processInfo.ProcessId
    }
  }

  @($matches | Where-Object { $_ -and $_ -ne $PID } | Sort-Object -Unique)
}

function Get-ProcessTreeIds {
  param(
    [int]$RootProcessId,
    [hashtable]$Seen = $null
  )

  if ($null -eq $Seen) {
    $Seen = @{}
  }

  if ($Seen.ContainsKey($RootProcessId)) {
    return @()
  }

  $Seen[$RootProcessId] = $true
  $ids = @($RootProcessId)
  foreach ($childProcessId in Get-ChildProcessIds -ParentProcessId $RootProcessId) {
    $ids += Get-ProcessTreeIds -RootProcessId $childProcessId -Seen $Seen
  }

  @($ids | Sort-Object -Unique)
}

function Stop-ProcessTree {
  param(
    [int]$RootProcessId,
    [switch]$Force
  )

  $process = Get-Process -Id $RootProcessId -ErrorAction SilentlyContinue
  if ($null -eq $process) {
    return
  }

  if ($Force) {
    $previousErrorActionPreference = $ErrorActionPreference
    $output = @()
    $exitCode = 0
    try {
      $ErrorActionPreference = "Continue"
      $output = @(& taskkill.exe /PID $RootProcessId /T /F 2>&1 | ForEach-Object { $_.ToString() })
      $exitCode = $LASTEXITCODE
    } catch {
      $output += $_.Exception.Message
      $exitCode = 1
    } finally {
      $ErrorActionPreference = $previousErrorActionPreference
    }

    $stillAlive = Get-Process -Id $RootProcessId -ErrorAction SilentlyContinue
    if ($exitCode -ne 0 -and $stillAlive) {
      Write-Warn "taskkill did not fully stop process ${RootProcessId}; trying Stop-Process fallback."
      foreach ($line in $output) {
        if ($line) {
          Write-Warn $line
        }
      }

      foreach ($processId in Get-ProcessTreeIds -RootProcessId $RootProcessId) {
        try {
          Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
        } catch {
          Write-Warn "Stop-Process fallback failed for ${processId}: $($_.Exception.Message)"
        }
      }
    }
    return
  }

  foreach ($childProcessId in Get-ChildProcessIds -ParentProcessId $RootProcessId) {
    Stop-ProcessTree -RootProcessId $childProcessId -Force:$Force
  }

  try {
    Stop-Process -Id $RootProcessId -Force:$Force -ErrorAction SilentlyContinue
  } catch {
    Write-Warn "Could not stop process ${RootProcessId}: $($_.Exception.Message)"
  }
}

function Wait-ProcessesExit {
  param(
    [int[]]$ProcessIds,
    [int]$TimeoutSeconds
  )

  $uniqueProcessIds = @($ProcessIds | Where-Object { $_ } | Sort-Object -Unique)
  if ($uniqueProcessIds.Count -eq 0) {
    return $true
  }

  $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
  do {
    $alive = @(
      $uniqueProcessIds |
        Where-Object { Get-Process -Id $_ -ErrorAction SilentlyContinue }
    )

    if ($alive.Count -eq 0) {
      return $true
    }

    Start-Sleep -Milliseconds 500
  } while ((Get-Date) -lt $deadline)

  return $false
}

function Wait-PortFree {
  param(
    [int]$Port,
    [int]$TimeoutSeconds
  )

  $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
  do {
    if ((Get-ListeningPids -Port $Port).Count -eq 0) {
      return $true
    }

    Start-Sleep -Milliseconds 500
  } while ((Get-Date) -lt $deadline)

  return $false
}

function Wait-PortOpen {
  param(
    [int]$Port,
    [int]$TimeoutSeconds
  )

  $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
  do {
    if ((Get-ListeningPids -Port $Port).Count -gt 0) {
      return $true
    }

    Start-Sleep -Milliseconds 1000
  } while ((Get-Date) -lt $deadline)

  return $false
}

function Wait-HttpReady {
  param(
    [string]$Name,
    [string]$Uri,
    [int]$TimeoutSeconds,
    [string]$Method = "GET",
    [string]$Body = "",
    [string]$ContentType = "application/json",
    [int]$RequestTimeoutSeconds = 5
  )

  $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
  do {
    try {
      if ($Method -eq "POST") {
        $response = Invoke-WebRequest `
          -UseBasicParsing `
          -Uri $Uri `
          -Method Post `
          -ContentType $ContentType `
          -Body $Body `
          -TimeoutSec $RequestTimeoutSeconds
      } else {
        $response = Invoke-WebRequest `
          -UseBasicParsing `
          -Uri $Uri `
          -TimeoutSec $RequestTimeoutSeconds
      }

      if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 400) {
        return $true
      }
    } catch {
      Start-Sleep -Seconds 1
      continue
    }

    Start-Sleep -Seconds 1
  } while ((Get-Date) -lt $deadline)

  Write-Warn "$Name did not become ready at $Uri within $TimeoutSeconds seconds."
  return $false
}

function Stop-ProcessRoots {
  param(
    [string]$Reason,
    [int[]]$RootProcessIds
  )

  $roots = @(
    $RootProcessIds |
      Where-Object { $_ -and $_ -ne $PID } |
      Sort-Object -Unique
  )

  if ($roots.Count -eq 0) {
    return
  }

  Write-Warn "${Reason}: $($roots -join ', ')"
  $treeProcessIds = @()
  foreach ($rootProcessId in $roots) {
    $details = Get-ProcessDetails -ProcessIdValue $rootProcessId
    if ($null -ne $details) {
      Write-Info "Stopping PID $rootProcessId ($($details.Name))"
      if ($details.CommandLine) {
        Write-Info "Command line: $(Format-CommandLine -CommandLine $details.CommandLine)"
      }
    } else {
      Write-Info "Stopping PID $rootProcessId"
    }

    $treeProcessIds += Get-ProcessTreeIds -RootProcessId $rootProcessId
    if (-not $DryRun) {
      Stop-ProcessTree -RootProcessId $rootProcessId -Force
    }
  }

  if ($DryRun) {
    Write-Info "DryRun enabled; processes were not stopped."
    return
  }

  $treeProcessIds = @($treeProcessIds | Where-Object { $_ } | Sort-Object -Unique)
  if (-not (Wait-ProcessesExit -ProcessIds $treeProcessIds -TimeoutSeconds $StopTimeoutSeconds)) {
    $alive = @(
      $treeProcessIds |
        Where-Object { Get-Process -Id $_ -ErrorAction SilentlyContinue }
    )

    Write-Warn "Process id(s) still alive after $StopTimeoutSeconds seconds: $($alive -join ', '). Forcing exit."
    foreach ($alivePid in $alive) {
      Stop-ProcessTree -RootProcessId $alivePid -Force
    }

    if (-not (Wait-ProcessesExit -ProcessIds $alive -TimeoutSeconds $StopTimeoutSeconds)) {
      $remaining = @(
        $alive |
          Where-Object { Get-Process -Id $_ -ErrorAction SilentlyContinue }
      )
      throw "Process id(s) did not fully exit: $($remaining -join ', ')"
    }
  }

  Write-Ok "$Reason fully exited."
}

function Stop-PidFileProcess {
  param(
    [string]$Name,
    [string]$PidFile
  )

  if (-not (Test-Path -LiteralPath $PidFile)) {
    Write-Ok "No tracked $Name PID file."
    return
  }

  $pidText = (Get-Content -LiteralPath $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
  $trackedPid = 0
  if (-not [int]::TryParse($pidText, [ref]$trackedPid)) {
    Write-Warn "Invalid $Name PID file; removing: $PidFile"
    if (-not $DryRun) {
      Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
    }
    return
  }

  if (-not (Get-Process -Id $trackedPid -ErrorAction SilentlyContinue)) {
    Write-Ok "Tracked $Name process $trackedPid is already gone."
    if (-not $DryRun) {
      Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
    }
    return
  }

  try {
    Stop-ProcessRoots -Reason "Tracked $Name process tree" -RootProcessIds @($trackedPid)
  } catch {
    Write-Warn "Tracked $Name process $trackedPid could not be fully stopped: $($_.Exception.Message)"
    Write-Warn "Continuing with port checks; run an elevated PowerShell to close that old console window if needed."
  }

  if (-not $DryRun) {
    Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
  }
}

function Stop-DevWindowProcesses {
  param([string]$TitlePrefix)

  $windowPids = @(
    Get-Process -ErrorAction SilentlyContinue |
      Where-Object { $_.Id -ne $PID -and $_.MainWindowTitle -like "$TitlePrefix*" } |
      Select-Object -ExpandProperty Id
  )

  if ($windowPids.Count -eq 0) {
    Write-Ok "No '$TitlePrefix' dev window process found."
    return
  }

  Stop-ProcessRoots -Reason "'$TitlePrefix' dev window process tree" -RootProcessIds $windowPids
}

function Stop-QuantXServiceProcesses {
  $serviceProcessIds = @(Get-QuantXServiceProcessIds)

  if ($serviceProcessIds.Count -eq 0) {
    Write-Ok "No untracked QuantX backend/frontend/Prefect/MCP service process found."
    return
  }

  Stop-ProcessRoots `
    -Reason "Untracked QuantX backend/frontend/Prefect/MCP service process tree" `
    -RootProcessIds $serviceProcessIds
}

function Clear-Port {
  param([int]$Port)

  $listenerPids = @(Get-ListeningPids -Port $Port)
  if ($listenerPids.Count -eq 0) {
    Write-Ok "Port $Port is free."
    return
  }

  Stop-ProcessRoots -Reason "Port $Port listener process tree" -RootProcessIds $listenerPids

  if ($DryRun) {
    return
  }

  if (-not (Wait-PortFree -Port $Port -TimeoutSeconds $StopTimeoutSeconds)) {
    $remainingPids = @(Get-ListeningPids -Port $Port)
    throw "Port $Port is still occupied by process id(s): $($remainingPids -join ', ')"
  }

  Write-Ok "Port $Port has been released."
}

function Start-DevWindow {
  param(
    [string]$Title,
    [string]$WorkingDirectory,
    [string]$Command,
    [string]$PidFile
  )

  $safeTitle = $Title.Replace("'", "''")
  $safeWorkingDirectory = $WorkingDirectory.Replace("'", "''")
  $windowCommand = @"
`$Host.UI.RawUI.WindowTitle = '$safeTitle'
Set-Location -LiteralPath '$safeWorkingDirectory'
$Command
"@

  $encodedCommand = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($windowCommand))
  $windowStyle = if ($Hidden) { "Hidden" } else { "Normal" }

  if ($DryRun) {
    Write-Info "DryRun: would start '$Title' in '$WorkingDirectory' with command: $Command"
    return
  }

  $process = Start-Process powershell.exe `
    -WorkingDirectory $WorkingDirectory `
    -WindowStyle $windowStyle `
    -ArgumentList @(
      "-NoExit",
      "-NoProfile",
      "-ExecutionPolicy",
      "Bypass",
      "-EncodedCommand",
      $encodedCommand
    ) `
    -PassThru

  if ($PidFile) {
    Set-Content -LiteralPath $PidFile -Value $process.Id -NoNewline
  }

  Write-Ok "Started '$Title' launcher process PID $($process.Id)."
}

function Assert-CommandAvailable {
  param([string]$CommandName)

  if (-not (Get-Command $CommandName -ErrorAction SilentlyContinue)) {
    throw "Required command not found in PATH: $CommandName"
  }
}

function Resolve-BackendPython {
  $candidates = @()

  if ($BackendPython) {
    $candidates += $BackendPython
  }

  if ($env:QUANTX_PYTHON_EXE) {
    $candidates += $env:QUANTX_PYTHON_EXE
  }

  if ($env:CONDA_EXE) {
    $condaScriptsDir = Split-Path -Parent $env:CONDA_EXE
    $condaRootDir = Split-Path -Parent $condaScriptsDir
    $candidates += (Join-Path $condaRootDir "envs\$CondaEnvName\python.exe")
  }

  if ($env:USERPROFILE) {
    $candidates += (Join-Path $env:USERPROFILE "miniconda3\envs\$CondaEnvName\python.exe")
    $candidates += (Join-Path $env:USERPROFILE "anaconda3\envs\$CondaEnvName\python.exe")
  }

  if (
    $env:CONDA_PREFIX -and
    (Split-Path -Leaf $env:CONDA_PREFIX) -eq $CondaEnvName
  ) {
    $candidates += (Join-Path $env:CONDA_PREFIX "python.exe")
  }

  foreach ($candidate in ($candidates | Where-Object { $_ } | Select-Object -Unique)) {
    if (Test-Path -LiteralPath $candidate) {
      return (Resolve-Path -LiteralPath $candidate).Path
    }
  }

  throw "Cannot find Python for conda environment '$CondaEnvName'. Pass -BackendPython <path> or set QUANTX_PYTHON_EXE."
}

function Resolve-CondaActivateBat {
  param([string]$ResolvedBackendPythonPath)

  if ($env:CONDA_EXE) {
    $condaScriptsDir = Split-Path -Parent $env:CONDA_EXE
    $candidate = Join-Path $condaScriptsDir "activate.bat"
    if (Test-Path -LiteralPath $candidate) {
      return (Resolve-Path -LiteralPath $candidate).Path
    }
  }

  $envDir = Split-Path -Parent $ResolvedBackendPythonPath
  $envsDir = Split-Path -Parent $envDir
  $condaRootDir = Split-Path -Parent $envsDir
  $candidate = Join-Path $condaRootDir "Scripts\activate.bat"
  if (Test-Path -LiteralPath $candidate) {
    return (Resolve-Path -LiteralPath $candidate).Path
  }

  throw "Cannot find conda activate.bat for environment '$CondaEnvName'."
}

function Write-DevRunners {
  param(
    [string]$CondaActivateBat,
    [string]$ResolvedBackendPythonPath
  )

  $resolvedCondaPrefixPath = Split-Path -Parent $ResolvedBackendPythonPath
  $resolvedCondaScriptsPath = Join-Path $resolvedCondaPrefixPath "Scripts"
  $resolvedCondaExePath = $env:CONDA_EXE
  if (-not $resolvedCondaExePath -or -not (Test-Path -LiteralPath $resolvedCondaExePath)) {
    $condaEnvsDir = Split-Path -Parent $resolvedCondaPrefixPath
    $condaRootDir = Split-Path -Parent $condaEnvsDir
    $condaExeCandidate = Join-Path $condaRootDir "Scripts\conda.exe"
    if (Test-Path -LiteralPath $condaExeCandidate) {
      $resolvedCondaExePath = (Resolve-Path -LiteralPath $condaExeCandidate).Path
    }
  }

  $backendRunnerContent = @(
    "@echo off",
    "set ENV=development",
    "set QUANTX_DEV_SERVICE=backend",
    "set CONDA_DEFAULT_ENV=$CondaEnvName",
    "set CONDA_ENV_NAME=$CondaEnvName",
    "set CONDA_PREFIX=$resolvedCondaPrefixPath",
    "set CONDA_EXE=$resolvedCondaExePath",
    "set QUANTX_PYTHON_EXE=$ResolvedBackendPythonPath",
    "set PATH=$resolvedCondaPrefixPath;$resolvedCondaScriptsPath;%PATH%",
    "cd /d ""$BackendDir""",
    """$ResolvedBackendPythonPath"" main.py"
  )

  $frontendRunnerContent = @(
    "@echo off",
    "set QUANTX_DEV_SERVICE=frontend",
    "cd /d ""$FrontendDir""",
    "npm run dev -- --host 0.0.0.0 --port $FrontendPort --strictPort"
  )

  Set-Content -LiteralPath $BackendRunner -Value $backendRunnerContent -Encoding ASCII
  Set-Content -LiteralPath $FrontendRunner -Value $frontendRunnerContent -Encoding ASCII
}

Write-Host "============================================"
Write-Host "QuantX development startup"
Write-Host "============================================"

Test-RequiredPath -Path (Join-Path $BackendDir "main.py") -Description "Backend entry"
Test-RequiredPath -Path (Join-Path $FrontendDir "package.json") -Description "Frontend package"
Assert-CommandAvailable -CommandName "npm"
$ResolvedBackendPython = Resolve-BackendPython
$ResolvedCondaActivateBat = Resolve-CondaActivateBat -ResolvedBackendPythonPath $ResolvedBackendPython
Write-Info "Backend Python: $ResolvedBackendPython"
Write-Info "Conda activate: $ResolvedCondaActivateBat"

if (-not $DryRun) {
  New-Item -ItemType Directory -Path $StateDir -Force | Out-Null
  Write-DevRunners `
    -CondaActivateBat $ResolvedCondaActivateBat `
    -ResolvedBackendPythonPath $ResolvedBackendPython
}

Write-Info "Step 1: stop previously tracked QuantX dev processes."
Stop-PidFileProcess -Name "backend" -PidFile $BackendPidFile
Stop-PidFileProcess -Name "frontend" -PidFile $FrontendPidFile
Stop-DevWindowProcesses -TitlePrefix "QuantX Backend"
Stop-DevWindowProcesses -TitlePrefix "QuantX Frontend"

Write-Info "Step 2: stop untracked QuantX backend/frontend/Prefect/MCP service processes."
Stop-QuantXServiceProcesses

Write-Info "Step 3: check and release occupied ports."
$portsToRelease = @($BackendPort, $FrontendPort) + $LegacyFrontendPorts
foreach ($port in $portsToRelease | Sort-Object -Unique) {
  Clear-Port -Port $port
}

if ($StopOnly) {
  Write-Host "============================================"
  Write-Ok "QuantX dev processes stopped."
  Write-Host "Backend port:  $BackendPort"
  Write-Host "Frontend port: $FrontendPort"
  if ($LegacyFrontendPorts.Count -gt 0) {
    Write-Host "Legacy frontend ports checked: $($LegacyFrontendPorts -join ', ')"
  }
  Write-Host "============================================"
  exit 0
}

Write-Info "Step 4: start backend and frontend."
Start-DevWindow `
  -Title "QuantX Backend :$BackendPort" `
  -WorkingDirectory $BackendDir `
  -Command "cmd.exe /k `"`"$BackendRunner`"`"" `
  -PidFile $BackendPidFile

Start-DevWindow `
  -Title "QuantX Frontend :$FrontendPort" `
  -WorkingDirectory $FrontendDir `
  -Command "cmd.exe /k `"`"$FrontendRunner`"`"" `
  -PidFile $FrontendPidFile

if (-not $DryRun) {
  Write-Info "Waiting for backend port $BackendPort..."
  if (Wait-PortOpen -Port $BackendPort -TimeoutSeconds $StartTimeoutSeconds) {
    Write-Ok "Backend is listening on http://localhost:$BackendPort"
    if (Wait-HttpReady -Name "Backend health" -Uri "http://127.0.0.1:$BackendPort/health" -TimeoutSeconds 20) {
      Write-Ok "Backend health check is responding."
    }
    $graphqlProbeBody = '{ "query": "query { __typename }" }'
    if (Wait-HttpReady -Name "Backend GraphQL" -Uri "http://127.0.0.1:$BackendPort/graphql" -TimeoutSeconds 20 -Method "POST" -Body $graphqlProbeBody) {
      Write-Ok "Backend GraphQL is responding."
    }
  } else {
    Write-Warn "Backend port $BackendPort did not open within $StartTimeoutSeconds seconds."
  }

  Write-Info "Waiting for frontend port $FrontendPort..."
  if (Wait-PortOpen -Port $FrontendPort -TimeoutSeconds $StartTimeoutSeconds) {
    Write-Ok "Frontend is listening on http://localhost:$FrontendPort"
  } else {
    Write-Warn "Frontend port $FrontendPort did not open within $StartTimeoutSeconds seconds."
  }
}

Write-Host "============================================"
Write-Ok "Startup script finished."
Write-Host "Backend:  http://localhost:$BackendPort"
Write-Host "Frontend: http://localhost:$FrontendPort"
Write-Host "============================================"
