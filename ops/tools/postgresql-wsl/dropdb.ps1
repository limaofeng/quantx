& "$PSScriptRoot\Invoke-PostgreSqlTool.ps1" -Tool dropdb @args
if (-not $?) { exit 1 }
exit $LASTEXITCODE
