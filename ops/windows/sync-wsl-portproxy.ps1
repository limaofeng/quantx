[CmdletBinding()]
param(
  [Parameter(Position = 0)]
  [ValidateSet("install", "sync", "status")]
  [string]$Command = "status",

  [ValidatePattern("^(?:\d{1,3}\.){3}\d{1,3}$")]
  [string]$ListenAddress = "0.0.0.0",

  [ValidateRange(1, 1440)]
  [int]$RefreshMinutes = 5,

  [string]$Distribution = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$TaskName = "QuantX-WSL-PortProxy"
$ManagedPorts = @(30081, 30420, 30179, 32432)
$PortProxyRegistryPath = (
  "HKLM:\SYSTEM\CurrentControlSet\Services\PortProxy\v4tov4\tcp"
)
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path

function Test-IsAdministrator {
  $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
  $principal = [System.Security.Principal.WindowsPrincipal]::new($identity)
  return $principal.IsInRole(
    [System.Security.Principal.WindowsBuiltInRole]::Administrator
  )
}

function Assert-Administrator {
  if (-not (Test-IsAdministrator)) {
    throw "Command '$Command' must run from an elevated PowerShell session."
  }
}

function Get-WslEth0Address {
  $wsl = (Get-Command wsl.exe -ErrorAction Stop).Source
  $arguments = @()
  if ($Distribution.Trim()) {
    $arguments += @("--distribution", $Distribution.Trim())
  }
  $arguments += @(
    "--exec", "ip", "-4", "-j", "address", "show", "dev", "eth0"
  )

  $output = @(& $wsl @arguments 2>&1)
  $exitCode = $LASTEXITCODE
  if ($exitCode -ne 0) {
    throw "Unable to inspect WSL eth0 (wsl.exe exit code $exitCode)."
  }

  $text = ($output -join [Environment]::NewLine).Trim()
  $jsonStart = $text.IndexOf("[")
  if ($jsonStart -lt 0) {
    throw "WSL did not return JSON address data for eth0."
  }
  $interfaces = $text.Substring($jsonStart) | ConvertFrom-Json
  $address = @(
    $interfaces |
      ForEach-Object { $_.addr_info } |
      Where-Object {
        $_.family -eq "inet" -and
        $_.scope -eq "global" -and
        $_.local -is [string]
      } |
      Select-Object -First 1
  )
  if (-not $address) {
    throw "WSL eth0 has no global IPv4 address."
  }

  $parsed = [System.Net.IPAddress]::None
  if (-not [System.Net.IPAddress]::TryParse($address[0].local, [ref]$parsed)) {
    throw "WSL returned an invalid eth0 IPv4 address."
  }
  return $parsed.ToString()
}

function Get-PortProxyTarget {
  param(
    [Parameter(Mandatory = $true)]
    [int]$Port
  )

  if (-not (Test-Path -LiteralPath $PortProxyRegistryPath)) {
    return $null
  }
  $name = "{0}/{1}" -f $ListenAddress, $Port
  try {
    return Get-ItemPropertyValue `
      -LiteralPath $PortProxyRegistryPath `
      -Name $name `
      -ErrorAction Stop
  } catch [System.Management.Automation.PSArgumentException] {
    return $null
  }
}

function Set-ManagedPortProxies {
  param(
    [Parameter(Mandatory = $true)]
    [string]$ConnectAddress
  )

  Assert-Administrator
  foreach ($port in $ManagedPorts) {
    $desiredTarget = "{0}/{1}" -f $ConnectAddress, $port
    $currentTarget = Get-PortProxyTarget -Port $port
    if ($currentTarget -eq $desiredTarget) {
      Write-Host (
        "Port proxy {0}:{1} already targets {2}." -f
        $ListenAddress,
        $port,
        $desiredTarget
      )
      continue
    }

    $verb = if ($null -eq $currentTarget) { "add" } else { "set" }
    $netshOutput = @(
      & netsh.exe interface portproxy $verb v4tov4 `
        listenaddress=$ListenAddress `
        listenport=$port `
        connectaddress=$ConnectAddress `
        connectport=$port `
        protocol=tcp 2>&1
    )
    if ($LASTEXITCODE -ne 0) {
      throw (
        "Failed to {0} port proxy {1}:{2}: {3}" -f
        $verb,
        $ListenAddress,
        $port,
        ($netshOutput -join " ")
      )
    }
    Write-Host (
      "Updated port proxy {0}:{1} -> {2}:{1}." -f
      $ListenAddress,
      $port,
      $ConnectAddress
    ) -ForegroundColor Green
  }
}

function Invoke-Sync {
  $address = Get-WslEth0Address
  Set-ManagedPortProxies -ConnectAddress $address
  Write-Host "Managed WSL target: $address" -ForegroundColor Green
}

function Register-SyncTask {
  Assert-Administrator
  $powershell = Join-Path $PSHOME "powershell.exe"
  if (-not (Test-Path -LiteralPath $powershell -PathType Leaf)) {
    $powershell = (Get-Command powershell.exe -ErrorAction Stop).Source
  }

  $arguments = (
    '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{0}" sync ' +
    '-ListenAddress "{1}"'
  ) -f $PSCommandPath, $ListenAddress
  if ($Distribution.Trim()) {
    $arguments += ' -Distribution "{0}"' -f $Distribution.Trim()
  }

  $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
  $action = New-ScheduledTaskAction `
    -Execute $powershell `
    -Argument $arguments `
    -WorkingDirectory $Root
  $logonTrigger = New-ScheduledTaskTrigger -AtLogOn -User $identity
  $refreshTrigger = New-ScheduledTaskTrigger `
    -Once `
    -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes $RefreshMinutes)
  $principal = New-ScheduledTaskPrincipal `
    -UserId $identity `
    -LogonType Interactive `
    -RunLevel Highest
  $settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 2) `
    -MultipleInstances IgnoreNew
  $definition = New-ScheduledTask `
    -Action $action `
    -Principal $principal `
    -Settings $settings `
    -Trigger @($logonTrigger, $refreshTrigger)
  Register-ScheduledTask `
    -TaskName $TaskName `
    -InputObject $definition `
    -Force | Out-Null

  Write-Host (
    "Scheduled task '$TaskName' refreshes every $RefreshMinutes minutes."
  ) -ForegroundColor Green
}

function Show-Status {
  $address = Get-WslEth0Address
  Write-Host "Current WSL eth0 address: $address"
  foreach ($port in $ManagedPorts) {
    $target = Get-PortProxyTarget -Port $port
    if ($null -eq $target) {
      $target = "MISSING"
    }
    [pscustomobject]@{
      Listen = "{0}:{1}" -f $ListenAddress, $port
      Target = $target
      MatchesWsl = $target -eq ("{0}/{1}" -f $address, $port)
    }
  }
  Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue |
    Select-Object TaskName, State
}

switch ($Command) {
  "install" {
    Invoke-Sync
    Register-SyncTask
  }
  "sync" {
    Invoke-Sync
  }
  "status" {
    Show-Status
  }
}
