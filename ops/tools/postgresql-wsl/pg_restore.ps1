& "$PSScriptRoot\Invoke-PostgreSqlTool.ps1" -Tool pg_restore @args
if (-not $?) { exit 1 }
exit $LASTEXITCODE
