param(
    [Parameter(Mandatory = $true)]
    [string]$SourceDirectory,
    [string]$OutputDirectory = "desktop\dist",
    [string]$Version = "0.1.0",
    [string]$CompilerPath,
    [switch]$ValidateOnly
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$sourceRoot = (Resolve-Path -LiteralPath $SourceDirectory).Path
$outputRoot = [IO.Path]::GetFullPath((Join-Path $projectRoot $OutputDirectory))
$installerScript = Join-Path $PSScriptRoot "installer\atlas-desktop.iss"

if ($Version -notmatch '^\d+\.\d+\.\d+([.-][0-9A-Za-z.-]+)?$') {
    throw "Некорректная версия Desktop: $Version"
}

$requiredFiles = @(
    "app\app.py",
    "desktop\atlas_launcher.py",
    "runtime\python\python.exe",
    "runtime\python\pythonw.exe",
    "runtime\llama\cpu\llama-server.exe",
    "models\chat.gguf"
)
foreach ($relativePath in $requiredFiles) {
    $path = Join-Path $sourceRoot $relativePath
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Установочный payload неполон: $relativePath"
    }
}

$pythonExe = Join-Path $sourceRoot "runtime\python\python.exe"
$launcher = Join-Path $sourceRoot "desktop\atlas_launcher.py"
& $pythonExe $launcher --check --install-dir $sourceRoot
if ($LASTEXITCODE -ne 0) {
    throw "Desktop launcher отклонил установочный payload"
}

Write-Host "Payload Atlas Desktop прошёл проверку: $sourceRoot"
Write-Host "Каталоги downloads и validation не включаются в установочный комплект."
if ($ValidateOnly) {
    return
}

if (-not $CompilerPath) {
    $command = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($command) {
        $CompilerPath = $command.Source
    }
    else {
        foreach ($candidate in @(
            "$env:LOCALAPPDATA\Programs\Inno Setup 7\ISCC.exe",
            "$env:ProgramFiles\Inno Setup 7\ISCC.exe",
            "${env:ProgramFiles(x86)}\Inno Setup 7\ISCC.exe"
        )) {
            if (Test-Path -LiteralPath $candidate -PathType Leaf) {
                $CompilerPath = $candidate
                break
            }
        }
    }
}
if (-not $CompilerPath -or -not (Test-Path -LiteralPath $CompilerPath -PathType Leaf)) {
    throw "ISCC.exe (Inno Setup 7) не найден. Установите compiler или передайте -CompilerPath."
}

New-Item -ItemType Directory -Force -Path $outputRoot | Out-Null
& $CompilerPath `
    "/DDesktopSource=$sourceRoot" `
    "/DDesktopOutput=$outputRoot" `
    "/DDesktopVersion=$Version" `
    $installerScript
if ($LASTEXITCODE -ne 0) {
    throw "Inno Setup не смог собрать установочный комплект Atlas"
}

$baseName = "Atlas-Desktop-$Version-Windows-x64"
$artifacts = Get-ChildItem -LiteralPath $outputRoot -File |
    Where-Object { $_.Name -eq "$baseName.exe" -or $_.Name -like "$baseName-*.bin" } |
    Sort-Object Name
if (-not $artifacts) {
    throw "Inno Setup не создал ожидаемые файлы установочного комплекта"
}
$checksums = foreach ($artifact in $artifacts) {
    $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $artifact.FullName).Hash.ToLowerInvariant()
    "$hash  $($artifact.Name)"
}
$checksums | Set-Content -LiteralPath (Join-Path $outputRoot "SHA256SUMS.txt") -Encoding ascii
Write-Host "Установочный комплект и SHA-256 созданы: $outputRoot"
