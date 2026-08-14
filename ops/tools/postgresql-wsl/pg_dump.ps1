& "$PSScriptRoot\Invoke-PostgreSqlTool.ps1" -Tool pg_dump @args
if (-not $?) { exit 1 }
exit $LASTEXITCODE
