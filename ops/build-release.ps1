[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)]
  [ValidatePattern("^[0-9A-Za-z][0-9A-Za-z._-]{0,63}$")]
  [string]$Version,

  [string]$OutputDirectory = "",

  [string]$PythonExecutable = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Invoke-Checked {
  param(
    [Parameter(Mandatory = $true)]
    [string]$Description,
    [Parameter(Mandatory = $true)]
    [scriptblock]$Action
  )

  & $Action
  if ($LASTEXITCODE -ne 0) {
    throw "$Description failed with exit code $LASTEXITCODE."
  }
}

function Install-ReleaseTool {
  param(
    [Parameter(Mandatory = $true)]
    [string]$Name,
    [Parameter(Mandatory = $true)]
    [object]$Spec,
    [Parameter(Mandatory = $true)]
    [string]$Destination
  )

  $cacheDirectory = Join-Path $repositoryRoot ".runtime\downloads"
  New-Item -ItemType Directory -Path $cacheDirectory -Force | Out-Null
  $cacheName = if ([bool]$Spec.archive) {
    "$Name.zip"
  } else {
    [string]$Spec.executable
  }
  $download = Join-Path $cacheDirectory $cacheName
  $expectedHash = ([string]$Spec.sha256).ToLowerInvariant()
  if (Test-Path -LiteralPath $download -PathType Leaf) {
    $downloadHash = (
      Get-FileHash -LiteralPath $download -Algorithm SHA256
    ).Hash.ToLowerInvariant()
    if ($downloadHash -cne $expectedHash) {
      Remove-Item -LiteralPath $download -Force
    }
  }
  if (-not (Test-Path -LiteralPath $download -PathType Leaf)) {
    $partial = Join-Path $cacheDirectory (
      ".$cacheName.$([guid]::NewGuid().ToString('N')).partial"
    )
    try {
      Invoke-WebRequest -Uri ([string]$Spec.url) -OutFile $partial
      $downloadHash = (
        Get-FileHash -LiteralPath $partial -Algorithm SHA256
      ).Hash.ToLowerInvariant()
      if ($downloadHash -cne $expectedHash) {
        throw "$Name release download checksum mismatch."
      }
      Move-Item -LiteralPath $partial -Destination $download
    } finally {
      if (Test-Path -LiteralPath $partial -PathType Leaf) {
        Remove-Item -LiteralPath $partial -Force
      }
    }
  }
  New-Item -ItemType Directory -Path $Destination -Force | Out-Null
  if ([bool]$Spec.archive) {
    Expand-Archive -LiteralPath $download -DestinationPath $Destination -Force
  } else {
    Copy-Item -LiteralPath $download -Destination (
      Join-Path $Destination ([string]$Spec.executable)
    ) -Force
  }
  $executable = Join-Path $Destination ([string]$Spec.executable)
  if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
    throw "$Name release executable is missing."
  }
  if ($Spec.PSObject.Properties.Name -contains "installedSha256") {
    $installedHash = (
      Get-FileHash -LiteralPath $executable -Algorithm SHA256
    ).Hash.ToLowerInvariant()
    if (
      $installedHash -cne
      ([string]$Spec.installedSha256).ToLowerInvariant()
    ) {
      throw "$Name installed executable checksum mismatch."
    }
  }
}

function Remove-PythonBuildArtifacts {
  param(
    [Parameter(Mandatory = $true)]
    [string]$Root
  )

  $fullRoot = [System.IO.Path]::GetFullPath($Root)
  if (-not $fullRoot.StartsWith(
    $outputRoot + [System.IO.Path]::DirectorySeparatorChar,
    [StringComparison]::OrdinalIgnoreCase
  )) {
    throw "Python artifact cleanup path escaped the release output directory."
  }

  Get-ChildItem -LiteralPath $fullRoot -Recurse -File -Force |
    Where-Object { $_.Extension -in @(".pyc", ".pyo") } |
    ForEach-Object {
      Remove-Item -LiteralPath $_.FullName -Force
    }

  Get-ChildItem -LiteralPath $fullRoot -Recurse -Directory -Force |
    Where-Object { $_.Name -eq "__pycache__" } |
    Sort-Object { $_.FullName.Length } -Descending |
    ForEach-Object {
      Remove-Item -LiteralPath $_.FullName -Recurse -Force
    }
}

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$repositoryRoot = [System.IO.Path]::GetFullPath((Join-Path $scriptRoot ".."))
if (-not [Environment]::GetEnvironmentVariable("UV_CACHE_DIR")) {
  $env:UV_CACHE_DIR = Join-Path $repositoryRoot ".runtime\uv-cache"
}
if (-not $OutputDirectory) {
  $OutputDirectory = Join-Path $repositoryRoot ".runtime\release-artifacts"
}
$outputRoot = [System.IO.Path]::GetFullPath($OutputDirectory)
New-Item -ItemType Directory -Path $outputRoot -Force | Out-Null

$staging = [System.IO.Path]::GetFullPath(
  (Join-Path $outputRoot ".staging-$([guid]::NewGuid().ToString('N'))")
)
if (-not $staging.StartsWith(
  $outputRoot + [System.IO.Path]::DirectorySeparatorChar,
  [StringComparison]::OrdinalIgnoreCase
)) {
  throw "Release staging path escaped the output directory."
}

$webDist = Join-Path $repositoryRoot "apps\web\dist"
if (-not (Test-Path -LiteralPath $webDist -PathType Container)) {
  throw "Web dist is missing. Run npm run build first."
}
$docsDist = Join-Path $repositoryRoot "apps\docs\dist"
if (-not (Test-Path -LiteralPath $docsDist -PathType Container)) {
  throw "Documentation dist is missing. Run npm run build first."
}
foreach ($contract in @(
  "graphql-schema.graphql",
  "graphql-permissions.json",
  "openapi-client.json"
)) {
  if (-not (
    Test-Path `
      -LiteralPath (Join-Path $docsDist "contracts\$contract") `
      -PathType Leaf
  )) {
    throw "Documentation contract is missing: $contract"
  }
}
$node = (Get-Command node -ErrorAction Stop).Source
$nodeVersion = (& $node --version).Trim().TrimStart("v")
if ($LASTEXITCODE -ne 0) {
  throw "Node runtime could not be inspected."
}
try {
  $parsedNodeVersion = [version]$nodeVersion
} catch {
  throw "Node returned an invalid version: $nodeVersion"
}
if ($parsedNodeVersion.Major -ne 20) {
  throw (
    "Production release must be built with Node 20.x; found $nodeVersion."
  )
}

try {
  $wheelDirectory = Join-Path $staging "wheels"
  New-Item -ItemType Directory -Path $wheelDirectory -Force | Out-Null
  Invoke-Checked -Description "Locked Python workspace wheel build" -Action {
    & uv build --quiet --all-packages --wheel --out-dir $wheelDirectory
  }

  $python = if ($PythonExecutable.Trim()) {
    [System.IO.Path]::GetFullPath($PythonExecutable)
  } elseif (
    [Environment]::GetEnvironmentVariable("QUANTX_PYTHON_EXE")
  ) {
    [System.IO.Path]::GetFullPath(
      [Environment]::GetEnvironmentVariable("QUANTX_PYTHON_EXE")
    )
  } else {
    (Get-Command python -ErrorAction Stop).Source
  }
  if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Release Python executable does not exist: $python"
  }
  $pythonVersion = (& $python -c "import platform; print(platform.python_version())")
  if ([version]$pythonVersion -ne [version]"3.13.9") {
    throw "Production release must be built with Python 3.13.9."
  }
  $requirementsDirectory = Join-Path $staging "requirements"
  $serverWheelhouse = Join-Path $staging "wheelhouse\server"
  $qmtWheelhouse = Join-Path $staging "wheelhouse\qmt"
  foreach ($path in @(
    $requirementsDirectory,
    $serverWheelhouse,
    $qmtWheelhouse
  )) {
    New-Item -ItemType Directory -Path $path -Force | Out-Null
  }
  $serverRequirements = Join-Path $requirementsDirectory "server.lock"
  $qmtRequirements = Join-Path $requirementsDirectory "qmt-agent.lock"
  Invoke-Checked -Description "Server dependency export" -Action {
    & uv export --quiet `
      --locked `
      --no-dev `
      --no-editable `
      --no-emit-workspace `
      --no-hashes `
      --package quantx-api `
      --package quantx-ai-runtime `
      --package quantx-engine `
      --package quantx-monitor `
      --package quantx-worker `
      --output-file $serverRequirements
  }
  Invoke-Checked -Description "QMT Agent dependency export" -Action {
    & uv export --quiet `
      --locked `
      --no-dev `
      --no-editable `
      --no-emit-workspace `
      --no-hashes `
      --package quantx-qmt-agent `
      --output-file $qmtRequirements
  }
  Invoke-Checked -Description "Server offline wheelhouse build" -Action {
    & $python -m pip wheel `
      --requirement $serverRequirements `
      --wheel-dir $serverWheelhouse `
      --prefer-binary `
      --disable-pip-version-check `
      --progress-bar off `
      --quiet
  }
  Invoke-Checked -Description "QMT Agent offline wheelhouse build" -Action {
    & $python -m pip wheel `
      --requirement $qmtRequirements `
      --wheel-dir $qmtWheelhouse `
      --prefer-binary `
      --disable-pip-version-check `
      --progress-bar off `
      --quiet
  }

  $webTarget = Join-Path $staging "apps\web\dist"
  New-Item -ItemType Directory -Path $webTarget -Force | Out-Null
  Copy-Item -Path (Join-Path $webDist "*") -Destination $webTarget `
    -Recurse -Force
  $docsTarget = Join-Path $staging "apps\docs"
  New-Item -ItemType Directory -Path $docsTarget -Force | Out-Null
  Copy-Item -LiteralPath $docsDist -Destination $docsTarget -Recurse -Force
  $apiTarget = Join-Path $staging "apps\api"
  $workerTarget = Join-Path $staging "apps\worker"
  New-Item -ItemType Directory -Path $apiTarget -Force | Out-Null
  New-Item -ItemType Directory -Path $workerTarget -Force | Out-Null
  Copy-Item -LiteralPath (Join-Path $repositoryRoot "apps\api\.env.production") `
    -Destination (Join-Path $apiTarget ".env.production") -Force
  Copy-Item -LiteralPath (Join-Path $repositoryRoot "apps\worker\prefect.yaml") `
    -Destination (Join-Path $workerTarget "prefect.yaml") -Force
  Copy-Item -LiteralPath (Join-Path $repositoryRoot "apps\worker\src") `
    -Destination (Join-Path $workerTarget "src") -Recurse -Force

  Copy-Item -LiteralPath (Join-Path $repositoryRoot "ops") `
    -Destination (Join-Path $staging "ops") -Recurse -Force
  New-Item -ItemType Directory `
    -Path (Join-Path $staging "packages\infrastructure") -Force | Out-Null
  Copy-Item `
    -LiteralPath (Join-Path $repositoryRoot "packages\infrastructure\alembic") `
    -Destination (Join-Path $staging "packages\infrastructure\alembic") `
    -Recurse -Force
  foreach ($file in @("alembic.ini", "uv.lock", ".python-version")) {
    Copy-Item -LiteralPath (Join-Path $repositoryRoot $file) `
      -Destination (Join-Path $staging $file) -Force
  }
  $toolLock = Get-Content `
    -LiteralPath (Join-Path $repositoryRoot "ops\tools.lock.json") `
    -Raw |
    ConvertFrom-Json
  Install-ReleaseTool `
    -Name "caddy" `
    -Spec $toolLock.tools.caddy `
    -Destination (Join-Path $staging "tools\caddy")
  Install-ReleaseTool `
    -Name "winsw" `
    -Spec $toolLock.tools.winsw `
    -Destination (Join-Path $staging "tools\winsw")

  Remove-PythonBuildArtifacts -Root $staging

  $manifest = [ordered]@{
    product = "QuantX"
    version = $Version
    builtAt = [datetime]::UtcNow.ToString("o")
    python = $pythonVersion
    node = $nodeVersion
    docsVersion = $Version
    protocol = "1.1"
    databaseRevision = "20260729_0002"
    wheelCount = @(
      Get-ChildItem -LiteralPath $wheelDirectory -Filter "*.whl"
    ).Count
    serverDependencyWheelCount = @(
      Get-ChildItem -LiteralPath $serverWheelhouse -Filter "*.whl"
    ).Count
    qmtDependencyWheelCount = @(
      Get-ChildItem -LiteralPath $qmtWheelhouse -Filter "*.whl"
    ).Count
  }
  $manifest | ConvertTo-Json -Depth 4 |
    Set-Content -LiteralPath (Join-Path $staging "manifest.json") `
      -Encoding utf8

  foreach ($requiredFile in @(
    "apps\web\dist\index.html",
    "apps\docs\dist\index.html",
    "apps\docs\dist\contracts\graphql-schema.graphql",
    "apps\docs\dist\contracts\graphql-permissions.json",
    "apps\docs\dist\contracts\openapi-client.json"
  )) {
    if (-not (
      Test-Path -LiteralPath (Join-Path $staging $requiredFile) -PathType Leaf
    )) {
      throw "Release staging content is missing: $requiredFile"
    }
  }

  $checksums = @(
    Get-ChildItem -LiteralPath $staging -Recurse -File |
      Sort-Object FullName |
      ForEach-Object {
        [ordered]@{
          path = [System.IO.Path]::GetRelativePath($staging, $_.FullName).
            Replace("\", "/")
          sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash
          size = $_.Length
        }
      }
  )
  $checksums | ConvertTo-Json -Depth 4 |
    Set-Content -LiteralPath (Join-Path $staging "checksums.json") `
      -Encoding utf8

  $archive = Join-Path $outputRoot "quantx-$Version-windows-x64.zip"
  if (Test-Path -LiteralPath $archive -PathType Leaf) {
    throw "Release archive already exists: $archive"
  }
  Compress-Archive -Path (Join-Path $staging "*") `
    -DestinationPath $archive -CompressionLevel Optimal
  $archiveHash = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash
  "$archiveHash  $([System.IO.Path]::GetFileName($archive))" |
    Set-Content -LiteralPath "$archive.sha256" -Encoding ascii
  Write-Host "Release created: $archive" -ForegroundColor Green
} finally {
  if (
    (Test-Path -LiteralPath $staging) -and
    $staging.StartsWith(
      $outputRoot + [System.IO.Path]::DirectorySeparatorChar,
      [StringComparison]::OrdinalIgnoreCase
    )
  ) {
    Remove-Item -LiteralPath $staging -Recurse -Force
  }
}
