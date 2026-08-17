param(
    [string]$Destination = "model_cache\desktop",
    [switch]$SkipModelValidation
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$destinationRoot = [IO.Path]::GetFullPath((Join-Path $projectRoot $Destination))
$manifestPath = Join-Path $PSScriptRoot "components.json"
$modelManifestPath = Join-Path $PSScriptRoot "model-packs.json"
$downloadsDir = Join-Path $destinationRoot "downloads"
$validationDir = Join-Path $destinationRoot "validation"
$pythonExe = Join-Path $destinationRoot "runtime\python\python.exe"
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
    if ((Get-Item -LiteralPath $Path).Length -ne [int64]$Component.size) { return $false }
    $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToLowerInvariant()
    return $hash -eq ([string]$Component.sha256).ToLowerInvariant()
}

function Get-VerifiedArtifact($Component) {
    New-Item -ItemType Directory -Force -Path $downloadsDir | Out-Null
    $artifact = Assert-InsideDestination (Join-Path $downloadsDir $Component.filename)
    if (Test-Artifact $artifact $Component) { return $artifact }
    $partial = "$artifact.part"
    Write-Host "Загрузка $($Component.id)..."
    & curl.exe -L --fail --silent --show-error --retry 5 --retry-all-errors `
        --continue-at - --connect-timeout 30 --output $partial $Component.url
    if ($LASTEXITCODE -ne 0 -or -not (Test-Artifact $partial $Component)) {
        if (Test-Path -LiteralPath $partial) { Remove-Item -LiteralPath $partial -Force }
        throw "Размер или SHA-256 не совпал для $($Component.id)"
    }
    Move-Item -LiteralPath $partial -Destination $artifact -Force
    return $artifact
}

function Expand-VerifiedArchive($Component) {
    $artifact = Get-VerifiedArtifact $Component
    $target = Assert-InsideDestination (Join-Path $destinationRoot $Component.destination)
    New-Item -ItemType Directory -Force -Path $target | Out-Null
    $targetRoot = [IO.Path]::GetFullPath($target).TrimEnd('\') + '\'
    $package = [IO.Compression.ZipFile]::OpenRead($artifact)
    try {
        foreach ($entry in $package.Entries) {
            $entryPath = [IO.Path]::GetFullPath((Join-Path $target $entry.FullName))
            if (-not $entryPath.StartsWith($targetRoot, [StringComparison]::OrdinalIgnoreCase)) {
                throw "Небезопасный путь в архиве: $($entry.FullName)"
            }
        }
    }
    finally { $package.Dispose() }
    [IO.Compression.ZipFile]::ExtractToDirectory($artifact, $target, $true)
    $extracted = Join-Path $target $Component.extracted_file
    $actualMd5 = (Get-FileHash -Algorithm MD5 -LiteralPath $extracted).Hash.ToLowerInvariant()
    if ($actualMd5 -ne ([string]$Component.extracted_md5).ToLowerInvariant()) {
        throw "MD5 распакованной модели не совпал для $($Component.id)"
    }
}

if (-not (Test-Path -LiteralPath $pythonExe -PathType Leaf)) {
    throw "Сначала выполните desktop/build_python_runtime.ps1"
}
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
$components = @{}
foreach ($component in $manifest.components) { $components[$component.id] = $component }

foreach ($id in @("easyocr_detector", "easyocr_cyrillic")) {
    if (-not $components.ContainsKey($id)) { throw "В manifest нет $id" }
    Expand-VerifiedArchive $components[$id]
}

$libreOffice = $components["libreoffice_windows"]
if (-not $libreOffice) { throw "В manifest нет libreoffice_windows" }
$libreOfficeMsi = Get-VerifiedArtifact $libreOffice
$libreOfficeTarget = Assert-InsideDestination (Join-Path $destinationRoot $libreOffice.destination)
$soffice = Join-Path $libreOfficeTarget "program\soffice.com"
if (-not (Test-Path -LiteralPath $soffice -PathType Leaf)) {
    if (Test-Path -LiteralPath $libreOfficeTarget) {
        Remove-Item -LiteralPath $libreOfficeTarget -Recurse -Force
    }
    New-Item -ItemType Directory -Force -Path $libreOfficeTarget | Out-Null
    Write-Host "Административная распаковка LibreOffice..."
    & msiexec.exe /a $libreOfficeMsi /qn "TARGETDIR=$libreOfficeTarget"
    if ($LASTEXITCODE -notin @(0, 3010)) { throw "msiexec завершился с кодом $LASTEXITCODE" }
}
if (-not (Test-Path -LiteralPath $soffice -PathType Leaf)) {
    throw "LibreOffice soffice.com не найден после распаковки"
}

New-Item -ItemType Directory -Force -Path $validationDir | Out-Null
$env:PYTHONNOUSERSITE = "1"
$env:PYTHONDONTWRITEBYTECODE = "1"
$env:HF_HUB_DISABLE_SYMLINKS_WARNING = "1"
$modelPackOutput = Join-Path $validationDir "offline-model-files.json"
& $pythonExe (Join-Path $PSScriptRoot "prepare_offline_models.py") `
    --manifest $modelManifestPath `
    --destination (Join-Path $destinationRoot "models\huggingface") `
    --cache-dir (Join-Path $downloadsDir "huggingface-cache") `
    --output $modelPackOutput
if ($LASTEXITCODE -ne 0) { throw "Подготовка Hugging Face model packs завершилась ошибкой" }

$validationArgs = @(
    (Join-Path $PSScriptRoot "validate_offline_assets.py"),
    "--root", $destinationRoot
)
if ($SkipModelValidation) { $validationArgs += "--skip-model-load" }
$validationOutput = & $pythonExe @validationArgs
$validationOutput | Set-Content -LiteralPath (Join-Path $validationDir "offline-assets.json") -Encoding utf8
if ($LASTEXITCODE -ne 0) { throw "Офлайн-компоненты не прошли проверку" }

$files = Get-ChildItem -LiteralPath (Join-Path $destinationRoot "models") -Recurse -File
$summary = [ordered]@{
    ready = $true
    model_files = $files.Count
    model_bytes = ($files | Measure-Object -Property Length -Sum).Sum
    libreoffice = $soffice
    ffmpeg_cli_required = $false
}
$summary | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $validationDir "offline-assets-footprint.json") -Encoding utf8
Write-Host ($summary | ConvertTo-Json -Compress)
