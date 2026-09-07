[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)]
  [string]$SourceDirectory,
  [Parameter(Mandatory = $true)]
  [string]$OutputDirectory,
  [Parameter(Mandatory = $true)]
  [string]$Python,
  [string]$LightGBMVersion = "4.7.0",
  [string]$BoostRoot = "",
  [string]$BoostLibraryDir = "",
  [string]$OpenCLIncludeDir = "",
  [string]$OpenCLLibrary = ""
)

$ErrorActionPreference = "Stop"

function Get-ToolVersion([string]$Tool, [string[]]$Arguments) {
  try {
    $text = (& $Tool @Arguments 2>&1 | Out-String).Trim()
    return $text.Split([Environment]::NewLine)[0]
  } catch {
    return "unavailable"
  }
}

function Assert-NoReparsePath([string]$Path) {
  $full = [IO.Path]::GetFullPath($Path)
  $root = [IO.Path]::GetPathRoot($full)
  if (-not $root) {
    throw "Path has no filesystem root: $Path"
  }
  $current = $root
  $tail = $full.Substring($root.Length)
  foreach ($part in ($tail -split '[\\/]' | Where-Object { $_ })) {
    $current = [IO.Path]::Combine($current, $part)
    if (Test-Path -LiteralPath $current) {
      $item = Get-Item -LiteralPath $current -Force -ErrorAction Stop
      if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Junction or symlink is not allowed in path: $current"
      }
    }
  }
}

if (-not (Test-Path -LiteralPath $SourceDirectory -PathType Container)) {
  throw "LightGBM source directory does not exist"
}
if ($LightGBMVersion -ne "4.7.0") {
  throw "This qualification build is locked to LightGBM 4.7.0"
}
Assert-NoReparsePath $SourceDirectory
Assert-NoReparsePath $OutputDirectory
$source = (Resolve-Path -LiteralPath $SourceDirectory).Path
$output = [IO.Path]::GetFullPath($OutputDirectory)
New-Item -ItemType Directory -Force -Path $output | Out-Null
Assert-NoReparsePath $output

# This is an explicit one-time dependency preparation command.  It never
# installs into the QMT Python environment and is not referenced by
# ops/quantx.ps1 up.  GPU is enabled only through the official pip build
# backend CMake config-settings below; environment switches are intentionally
# not used because they are easy to lose in a reused shell.

$cmake = Get-ToolVersion "cmake" @("--version")
$cl = Get-ToolVersion "cl" @()
$pythonVersion = Get-ToolVersion $Python @("--version")
$pythonAbi = (& $Python -c "import sysconfig; print(sysconfig.get_config_var('SOABI') or '')").Trim()

Push-Location $source
try {
  $cmakeDefinitions = @(
    "--config-settings=cmake.define.USE_GPU=ON"
  )
  if ($BoostRoot) {
    $cmakeDefinitions += "--config-settings=cmake.define.BOOST_ROOT=$BoostRoot"
  }
  if ($BoostLibraryDir) {
    $cmakeDefinitions += "--config-settings=cmake.define.BOOST_LIBRARYDIR=$BoostLibraryDir"
  }
  if ($OpenCLIncludeDir) {
    $cmakeDefinitions += "--config-settings=cmake.define.OpenCL_INCLUDE_DIR=$OpenCLIncludeDir"
  }
  if ($OpenCLLibrary) {
    $cmakeDefinitions += "--config-settings=cmake.define.OpenCL_LIBRARY=$OpenCLLibrary"
  }
  & $Python -m pip wheel . --no-deps --wheel-dir $output @cmakeDefinitions
  if ($LASTEXITCODE -ne 0) {
    throw "LightGBM OpenCL wheel build failed"
  }
} finally {
  Pop-Location
}

$wheels = @(Get-ChildItem -LiteralPath $output -Filter "lightgbm-$LightGBMVersion-*.whl")
if ($wheels.Count -ne 1) {
  throw "Build output must contain exactly one target wheel; use a clean output directory."
}
$wheel = $wheels[0]
if ($null -eq $wheel) {
  throw "Expected LightGBM $LightGBMVersion wheel was not produced"
}
if (($wheel.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
  throw "Produced wheel must be a regular file"
}
$wheelVersion = (& $Python -c "import zipfile,sys; p=sys.argv[1]; m=[n for n in zipfile.ZipFile(p).namelist() if n.endswith('/METADATA')][0]; print(next(line.split(':',1)[1].strip() for line in zipfile.ZipFile(p).read(m).decode().splitlines() if line.startswith('Version:')))" $wheel.FullName).Trim()
if ($wheelVersion -ne $LightGBMVersion) {
  throw "Produced wheel metadata version $wheelVersion does not match $LightGBMVersion"
}
$sha = (Get-FileHash -LiteralPath $wheel.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
$sourceRevision = (& git -C $source rev-parse HEAD 2>$null).Trim()
if (-not $sourceRevision) {
  $sourceRevision = (& git -C $source describe --tags --always 2>$null).Trim()
}
$evidence = [ordered]@{
  schema_version = 1
  lightgbm_version = $LightGBMVersion
  wheel = $wheel.Name
  wheel_sha256 = $sha
  use_gpu = $true
  cmake_definitions = $cmakeDefinitions
  platform = "Windows"
  cmake = $cmake
  visual_studio_cl = $cl
  boost_root_configured = [bool]$BoostRoot
  boost_librarydir_configured = [bool]$BoostLibraryDir
  opencl_include_dir_configured = [bool]$OpenCLIncludeDir
  opencl_library_configured = [bool]$OpenCLLibrary
  source_revision = $sourceRevision
  wheel_metadata_version = $wheelVersion
  python = $pythonVersion
  python_abi = $pythonAbi
  built_at_utc = (Get-Date).ToUniversalTime().ToString("o")
}
$evidencePath = Join-Path $output "lightgbm-opencl-build-evidence.json"
Assert-NoReparsePath $evidencePath
$evidence | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $evidencePath -Encoding UTF8
Write-Host "Built wheel: $($wheel.FullName)"
Write-Host "SHA256: $sha"
Write-Host "Evidence: $evidencePath"
