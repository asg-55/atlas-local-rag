param(
    [string]$Destination = "model_cache\desktop",
    [switch]$KeepPip
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$manifestPath = Join-Path $PSScriptRoot "components.json"
$requirementsPath = Join-Path $PSScriptRoot "requirements-windows.in"
$lockPath = Join-Path $PSScriptRoot "requirements-windows.lock.json"
$destinationRoot = [IO.Path]::GetFullPath((Join-Path $projectRoot $Destination))
$downloadsDir = Join-Path $destinationRoot "downloads"
$pythonDir = Join-Path $destinationRoot "runtime\python"
$sitePackages = Join-Path $pythonDir "Lib\site-packages"
$appDir = Join-Path $destinationRoot "app"
$validationDir = Join-Path $destinationRoot "validation"
$bundleDesktopDir = Join-Path $destinationRoot "desktop"
Add-Type -AssemblyName System.IO.Compression.FileSystem

function Assert-InsideDestination([string]$Path) {
    $resolved = [IO.Path]::GetFullPath($Path)
    $prefix = $destinationRoot.TrimEnd('\') + '\'
    if ($resolved -ne $destinationRoot -and -not $resolved.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Путь выходит за Desktop staging: $resolved"
    }
    return $resolved
}

function Test-Artifact($Path, $Component) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $false }
    $item = Get-Item -LiteralPath $Path
    if ($item.Length -ne [int64]$Component.size) { return $false }
    $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToLowerInvariant()
    return $hash -eq ([string]$Component.sha256).ToLowerInvariant()
}

function Expand-VerifiedArchive([string]$Archive, [string]$Target) {
    New-Item -ItemType Directory -Force -Path $Target | Out-Null
    $targetRoot = [IO.Path]::GetFullPath($Target).TrimEnd('\') + '\'
    $package = [IO.Compression.ZipFile]::OpenRead($Archive)
    try {
        foreach ($entry in $package.Entries) {
            $entryPath = [IO.Path]::GetFullPath((Join-Path $Target $entry.FullName))
            if (-not $entryPath.StartsWith($targetRoot, [StringComparison]::OrdinalIgnoreCase)) {
                throw "Небезопасный путь в архиве: $($entry.FullName)"
            }
        }
    }
    finally {
        $package.Dispose()
    }
    [IO.Compression.ZipFile]::ExtractToDirectory($Archive, $Target, $true)
}

function Install-ManifestComponent($Component) {
    New-Item -ItemType Directory -Force -Path $downloadsDir | Out-Null
    $artifact = Assert-InsideDestination (Join-Path $downloadsDir $Component.filename)
    if (-not (Test-Artifact $artifact $Component)) {
        $partial = "$artifact.part"
        if (Test-Path -LiteralPath $partial) { Remove-Item -LiteralPath $partial -Force }
        Write-Host "Загрузка $($Component.id)..."
        Invoke-WebRequest -Uri $Component.url -OutFile $partial
        if (-not (Test-Artifact $partial $Component)) {
            Remove-Item -LiteralPath $partial -Force
            throw "Размер или SHA-256 не совпал для $($Component.id)"
        }
        Move-Item -LiteralPath $partial -Destination $artifact -Force
    }
    $target = Assert-InsideDestination (Join-Path $destinationRoot $Component.destination)
    Expand-VerifiedArchive $artifact $target
    Write-Host "Подготовлен $($Component.id): $target"
}

$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
$components = @{}
foreach ($component in $manifest.components) { $components[$component.id] = $component }
$safePythonDir = Assert-InsideDestination $pythonDir
if (Test-Path -LiteralPath $safePythonDir) {
    Remove-Item -LiteralPath $safePythonDir -Recurse -Force
}
foreach ($required in @("python_embed", "pip_bootstrap")) {
    if (-not $components.ContainsKey($required)) { throw "В manifest нет $required" }
    Install-ManifestComponent $components[$required]
}

$pthFile = Join-Path $pythonDir "python311._pth"
@(
    "python311.zip"
    "."
    "Lib"
    "Lib\site-packages"
    "..\..\app"
    "import site"
) | Set-Content -LiteralPath $pthFile -Encoding ascii

$pythonExe = Join-Path $pythonDir "python.exe"
if (-not (Test-Path -LiteralPath $pythonExe -PathType Leaf)) {
    throw "python.exe отсутствует после распаковки"
}

$env:PYTHONNOUSERSITE = "1"
$env:PYTHONDONTWRITEBYTECODE = "1"
$env:PIP_DISABLE_PIP_VERSION_CHECK = "1"
$env:PIP_NO_CACHE_DIR = "1"

& $pythonExe -c "import sys; assert sys.version_info[:2] == (3, 11); print(sys.version)"
if ($LASTEXITCODE -ne 0) { throw "Встроенный Python не запустился" }
& $pythonExe -m pip --version
if ($LASTEXITCODE -ne 0) { throw "pip bootstrap не запустился" }

New-Item -ItemType Directory -Force -Path $validationDir | Out-Null
$installReport = Join-Path $validationDir "python-install-report.json"
if (-not (Test-Path -LiteralPath $lockPath -PathType Leaf)) {
    throw "Отсутствует requirements-windows.lock.json"
}
$lock = Get-Content -LiteralPath $lockPath -Raw | ConvertFrom-Json
if ($lock.schema_version -ne 1 -or -not $lock.only_binary) {
    throw "Некорректный Windows wheel lock"
}
$wheelhouse = Assert-InsideDestination (Join-Path $downloadsDir "windows-wheels")
New-Item -ItemType Directory -Force -Path $wheelhouse | Out-Null
foreach ($package in $lock.packages) {
    $target = Join-Path $wheelhouse $package.filename
    if (Test-Path -LiteralPath $target -PathType Leaf) {
        $actualHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $target).Hash.ToLowerInvariant()
        if ($actualHash -eq ([string]$package.sha256).ToLowerInvariant()) { continue }
        Remove-Item -LiteralPath $target -Force
    }
    $partial = "$target.part"
    if (Test-Path -LiteralPath $partial) { Remove-Item -LiteralPath $partial -Force }
    Write-Host "Wheel $($package.name)==$($package.version)..."
    & curl.exe -L --fail --silent --show-error --retry 5 --retry-all-errors `
        --connect-timeout 30 --output $partial $package.url
    if ($LASTEXITCODE -ne 0) { throw "Не удалось скачать wheel $($package.name)" }
    $actualHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $partial).Hash.ToLowerInvariant()
    if ($actualHash -ne ([string]$package.sha256).ToLowerInvariant()) {
        Remove-Item -LiteralPath $partial -Force
        throw "SHA-256 не совпал для wheel $($package.name)"
    }
    Move-Item -LiteralPath $partial -Destination $target -Force
}

Write-Host "Офлайн-установка закреплённых Windows wheels..."
& $pythonExe -m pip install `
    --only-binary=:all: `
    --no-index `
    --find-links $wheelhouse `
    --no-cache-dir `
    --upgrade `
    --target $sitePackages `
    --report $installReport `
    -r $requirementsPath
if ($LASTEXITCODE -ne 0) { throw "Установка Windows wheels завершилась ошибкой" }

$safeAppDir = Assert-InsideDestination $appDir
if (Test-Path -LiteralPath $safeAppDir) {
    Remove-Item -LiteralPath $safeAppDir -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $safeAppDir | Out-Null
Copy-Item -LiteralPath (Join-Path $projectRoot "app.py") -Destination $appDir -Force
Copy-Item -LiteralPath (Join-Path $projectRoot "rag_assistant") -Destination $appDir -Recurse -Force
Copy-Item -LiteralPath (Join-Path $projectRoot ".streamlit") -Destination $appDir -Recurse -Force
$safeBundleDesktopDir = Assert-InsideDestination $bundleDesktopDir
if (Test-Path -LiteralPath $safeBundleDesktopDir) {
    Remove-Item -LiteralPath $safeBundleDesktopDir -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $safeBundleDesktopDir | Out-Null
foreach ($name in @("__init__.py", "atlas_launcher.py", "components.json", "README.md")) {
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot $name) -Destination $safeBundleDesktopDir -Force
}

$validationScript = Join-Path $PSScriptRoot "validate_backend.py"
$validationJson = Join-Path $validationDir "python-runtime.json"
$validationOutput = & $pythonExe $validationScript
if ($LASTEXITCODE -ne 0) {
    $validationOutput | Set-Content -LiteralPath $validationJson -Encoding utf8
    throw "Проверка переносимого Python runtime завершилась ошибкой"
}
$validationOutput | Set-Content -LiteralPath $validationJson -Encoding utf8

if (-not $KeepPip) {
    foreach ($path in @(
        (Join-Path $sitePackages "pip"),
        (Join-Path $sitePackages "pip-26.2.1.dist-info")
    )) {
        $safePath = Assert-InsideDestination $path
        if (Test-Path -LiteralPath $safePath) { Remove-Item -LiteralPath $safePath -Recurse -Force }
    }
}

$files = Get-ChildItem -LiteralPath $pythonDir -Recurse -File
$summary = [ordered]@{
    ready = $true
    python = "3.11.9"
    files = $files.Count
    bytes = ($files | Measure-Object -Property Length -Sum).Sum
    pip_in_runtime = [bool]$KeepPip
    requirements = (Split-Path $requirementsPath -Leaf)
}
$summary | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $validationDir "python-footprint.json") -Encoding utf8
Write-Host "Atlas Desktop Python runtime готов: $pythonDir"
Write-Host ($summary | ConvertTo-Json -Compress)
