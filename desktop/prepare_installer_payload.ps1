param(
    [Parameter(Mandatory = $true)]
    [string]$BasePayload,
    [string]$Destination = "desktop\staging\Atlas",
    [string]$RceditPath,
    [string]$Version = "0.1.0"
)

$ErrorActionPreference = "Stop"
if ($Version -notmatch '^\d+\.\d+\.\d+$') {
    throw "Для ресурсов EXE нужна версия вида X.Y.Z: $Version"
}
$resourceVersion = "$Version.0"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$baseRoot = (Resolve-Path -LiteralPath (Join-Path $projectRoot $BasePayload)).Path
$destinationRoot = [IO.Path]::GetFullPath((Join-Path $projectRoot $Destination))
$allowedRoots = @(
    [IO.Path]::GetFullPath((Join-Path $projectRoot "desktop\staging")),
    [IO.Path]::GetFullPath((Join-Path $projectRoot "tmp"))
)
$insideAllowedRoot = $false
foreach ($allowedRoot in $allowedRoots) {
    $prefix = $allowedRoot.TrimEnd('\') + '\'
    if ($destinationRoot.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        $insideAllowedRoot = $true
        break
    }
}
if (-not $insideAllowedRoot) {
    throw "Destination разрешён только внутри desktop\staging или tmp: $destinationRoot"
}
if (Test-Path -LiteralPath $destinationRoot) {
    if (Get-ChildItem -LiteralPath $destinationRoot -Force | Select-Object -First 1) {
        throw "Destination должен отсутствовать или быть пустым: $destinationRoot"
    }
}
else {
    New-Item -ItemType Directory -Force -Path $destinationRoot | Out-Null
}

$status = git -C $projectRoot status --porcelain
if ($LASTEXITCODE -ne 0 -or $status) {
    throw "Перед комплектацией Desktop рабочее дерево Git должно быть чистым"
}
$sourceCommit = (git -C $projectRoot rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $sourceCommit -notmatch '^[0-9a-f]{40}$') {
    throw "Не удалось определить исходный commit"
}

foreach ($directory in @("runtime", "models")) {
    $source = Join-Path $baseRoot $directory
    if (-not (Test-Path -LiteralPath $source -PathType Container)) {
        throw "Base payload неполон: $directory"
    }
    Write-Host "Копирование $directory..."
    Copy-Item -LiteralPath $source -Destination $destinationRoot -Recurse
}

$appRoot = Join-Path $destinationRoot "app"
New-Item -ItemType Directory -Force -Path $appRoot | Out-Null
Copy-Item -LiteralPath (Join-Path $projectRoot "app.py") -Destination $appRoot
Copy-Item -LiteralPath (Join-Path $projectRoot "rag_assistant") -Destination $appRoot -Recurse
Copy-Item -LiteralPath (Join-Path $projectRoot ".streamlit") -Destination $appRoot -Recurse

$desktopRoot = Join-Path $destinationRoot "desktop"
New-Item -ItemType Directory -Force -Path $desktopRoot | Out-Null
foreach ($name in @("__init__.py", "atlas_launcher.py", "components.json", "model-packs.json", "README.md")) {
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot $name) -Destination $desktopRoot
}
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "assets") -Destination $desktopRoot -Recurse

$tools = Get-Content -LiteralPath (Join-Path $PSScriptRoot "installer-tools.json") -Raw | ConvertFrom-Json
$rcedit = $tools.rcedit
if (-not $RceditPath) {
    $toolCache = Join-Path $projectRoot "tmp\desktop-build-tools"
    New-Item -ItemType Directory -Force -Path $toolCache | Out-Null
    $RceditPath = Join-Path $toolCache "rcedit-x64-$($rcedit.version).exe"
}
if (-not [IO.Path]::IsPathRooted($RceditPath)) {
    $RceditPath = Join-Path $projectRoot $RceditPath
}
$RceditPath = [IO.Path]::GetFullPath($RceditPath)
function Test-Rcedit([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $false }
    $item = Get-Item -LiteralPath $Path
    $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToLowerInvariant()
    return $item.Length -eq [int64]$rcedit.size -and $hash -eq ([string]$rcedit.sha256).ToLowerInvariant()
}
if (-not (Test-Rcedit $RceditPath)) {
    $partial = "$RceditPath.part"
    Invoke-WebRequest -Uri $rcedit.url -OutFile $partial
    if (-not (Test-Rcedit $partial)) {
        Remove-Item -LiteralPath $partial -Force
        throw "Размер или SHA-256 rcedit не совпал"
    }
    Move-Item -LiteralPath $partial -Destination $RceditPath -Force
}
$atlasExe = Join-Path $destinationRoot "runtime\python\Atlas.exe"
Copy-Item -LiteralPath (Join-Path $destinationRoot "runtime\python\pythonw.exe") -Destination $atlasExe
$atlasIcon = Join-Path $desktopRoot "assets\atlas.ico"
& $RceditPath $atlasExe `
    --set-icon $atlasIcon `
    --set-file-version $resourceVersion `
    --set-product-version $resourceVersion `
    --set-version-string "FileDescription" "Atlas Desktop" `
    --set-version-string "ProductName" "Atlas Desktop" `
    --set-version-string "InternalName" "Atlas" `
    --set-version-string "OriginalFilename" "Atlas.exe" `
    --set-version-string "CompanyName" "Atlas"
if ($LASTEXITCODE -ne 0) { throw "Не удалось добавить бренд Atlas в исполняемый файл" }

$buildMetadata = [ordered]@{
    schema_version = 1
    source_commit = $sourceCommit
    desktop_edition = "prototype-1"
}
$buildMetadata | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $destinationRoot "desktop-build.json") -Encoding utf8

$pythonExe = Join-Path $destinationRoot "runtime\python\python.exe"
$launcher = Join-Path $destinationRoot "desktop\atlas_launcher.py"
& $pythonExe $launcher --check --install-dir $destinationRoot
if ($LASTEXITCODE -ne 0) { throw "Собранный Desktop payload не прошёл launcher --check" }

& $pythonExe (Join-Path $PSScriptRoot "payload_manifest.py") `
    --create `
    --root $destinationRoot `
    --manifest (Join-Path $destinationRoot "payload-manifest.json") `
    --source-commit $sourceCommit
if ($LASTEXITCODE -ne 0) { throw "Не удалось создать payload manifest" }

Write-Host "Чистый Atlas Desktop payload готов: $destinationRoot"
