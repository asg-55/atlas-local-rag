param(
    [Parameter(Mandatory = $true)]
    [string]$BasePayload,
    [string]$Destination = "desktop\staging\Atlas"
)

$ErrorActionPreference = "Stop"
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
