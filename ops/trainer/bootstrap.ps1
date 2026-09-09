[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)]
  [string]$CondaExecutable
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not [System.IO.Path]::IsPathRooted($CondaExecutable) -or
    -not (Test-Path -LiteralPath $CondaExecutable -PathType Leaf) -or
    [System.IO.Path]::GetFileName($CondaExecutable) -ne "conda.exe") {
  throw "Trainer bootstrap requires an explicit Conda executable."
}
$condaFile = Get-Item -LiteralPath $CondaExecutable
if ($condaFile.Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
  throw "Trainer bootstrap does not accept a linked Conda executable."
}
$condaRoot = Split-Path -Parent (Split-Path -Parent $condaFile.FullName)
if (-not (Test-Path -LiteralPath (Join-Path $condaRoot "conda-meta") -PathType Container)) {
  throw "Trainer bootstrap requires a standard Conda installation."
}
$prefix = Join-Path $condaRoot "envs\quantx-train"
foreach ($candidate in @($condaRoot, (Join-Path $condaRoot "envs"), $prefix)) {
  if ((Test-Path -LiteralPath $candidate) -and
      ((Get-Item -LiteralPath $candidate).Attributes -band [System.IO.FileAttributes]::ReparsePoint)) {
    throw "Trainer Conda directories must not be links or junctions."
  }
}
$python = Join-Path $prefix "python.exe"
$previousPythonPath = $env:PYTHONPATH
$previousPythonHome = $env:PYTHONHOME
$previousUserSite = $env:PYTHONNOUSERSITE
try {
  $env:PYTHONPATH = $null
  $env:PYTHONHOME = $null
  $env:PYTHONNOUSERSITE = "1"
  if (-not (Test-Path -LiteralPath $prefix)) {
    & $condaFile.FullName create --prefix $prefix --no-default-packages `
      --override-channels --channel conda-forge "python=3.13" pip --yes
    if ($LASTEXITCODE -ne 0) {
      throw "Trainer Conda creation failed; retain setup logs and inspect the partial prefix."
    }
  }
  if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Trainer prefix is incomplete; bootstrap will not overwrite it."
  }
  & $python -I -c @"
import json, pathlib, sys
p = pathlib.Path(sys.prefix).resolve()
assert p.name == 'quantx-train' and (p / 'conda-meta').is_dir(), 'Wrong Conda identity'
assert sys.version_info[:2] == (3, 13), 'Trainer requires Python 3.13'
print(json.dumps({'status': 'CONDA_READY', 'environment': p.name, 'python': sys.version.split()[0]}))
"@
  if ($LASTEXITCODE -ne 0) {
    throw "Existing Trainer interpreter failed identity validation; no environment was updated."
  }
} finally {
  $env:PYTHONPATH = $previousPythonPath
  $env:PYTHONHOME = $previousPythonHome
  $env:PYTHONNOUSERSITE = $previousUserSite
}
