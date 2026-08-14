& "$PSScriptRoot\Invoke-PostgreSqlTool.ps1" -Tool createdb @args
if (-not $?) { exit 1 }
exit $LASTEXITCODE
