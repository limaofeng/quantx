param(
  [Parameter(Mandatory = $true)]
  [ValidateSet("pg_dump", "pg_restore", "createdb", "dropdb")]
  [string]$Tool,

  [Parameter(ValueFromRemainingArguments = $true)]
  [string[]]$ToolArguments
)

$translated = [System.Collections.Generic.List[string]]::new()
foreach ($argumentValue in $ToolArguments) {
  $argument = [string]$argumentValue
  $pathMatch = [regex]::Match(
    $argument,
    "^(?<drive>[A-Za-z]):\\(?<rest>.*)$"
  )
  if (-not $pathMatch.Success) {
    $translated.Add($argument)
    continue
  }
  $drive = $pathMatch.Groups["drive"].Value.ToLowerInvariant()
  $rest = $pathMatch.Groups["rest"].Value -replace "\\", "/"
  $translated.Add("/mnt/$drive/$rest")
}

$previousWslEnv = $env:WSLENV
$previousConnectTimeout = $env:PGCONNECT_TIMEOUT
try {
  $env:PGCONNECT_TIMEOUT = "10"
  $wslVariables = @(
    "PGPASSWORD",
    "PGCONNECT_TIMEOUT",
    @($previousWslEnv -split ":" | Where-Object { $_ })
  ) | Select-Object -Unique
  $env:WSLENV = $wslVariables -join ":"
  & wsl.exe $Tool @translated
} finally {
  $env:WSLENV = $previousWslEnv
  $env:PGCONNECT_TIMEOUT = $previousConnectTimeout
}
