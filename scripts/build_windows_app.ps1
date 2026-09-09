#!/usr/bin/env pwsh
# Windows-only: build Amane/ with PyInstaller (onedir) + Native AOT tray/supervisor.
# Not for macOS/Linux. Expects: Windows, .venv with pyinstaller, web/dist built, .NET 8 SDK.
# Prefer: just windows-app (sync + frontend build + this script).
#
# Env:
#   AMANE_WINDOWS_APP_OUT   output dir (default: dist/Amane)
#   AMANE_WINDOWS_APP_WORK  work dir (default: dist/windows-app-work)
$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $true

if ($env:OS -ne "Windows_NT") {
    Write-Error "scripts/build_windows_app.ps1 is Windows-only"
    exit 1
}

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $Root

$Out = if ($env:AMANE_WINDOWS_APP_OUT) { $env:AMANE_WINDOWS_APP_OUT } else { Join-Path $Root "dist\Amane" }
$Work = if ($env:AMANE_WINDOWS_APP_WORK) { $env:AMANE_WINDOWS_APP_WORK } else { Join-Path $Root "dist\windows-app-work" }

$PyInstaller = Join-Path $Root ".venv\Scripts\pyinstaller.exe"
if (-not (Test-Path $PyInstaller)) {
    Write-Error "pyinstaller missing; run: just sync"
    exit 1
}

if (-not (Test-Path (Join-Path $Root "web\dist\index.html"))) {
    Write-Error "web/dist missing; run: just build"
    exit 1
}

if (-not (Test-Path (Join-Path $Root "assets\app.ico"))) {
    Write-Error "assets/app.ico missing; run: just icons"
    exit 1
}

if (-not (Get-Command dotnet -ErrorAction SilentlyContinue)) {
    Write-Error "dotnet SDK missing (need .NET 8+ for Native AOT)"
    exit 1
}

Remove-Item -Recurse -Force $Work, $Out -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $Work, $Out | Out-Null

& $PyInstaller `
    --noconfirm --clean --onedir --console `
    --name "Amane.Server" `
    --paths (Join-Path $Root "src") `
    --add-data "$(Join-Path $Root 'web\dist');web/dist" `
    --add-data "$(Join-Path $Root 'src\amane\db\migrations');amane/db/migrations" `
    --add-data "$(Join-Path $Root 'src\amane\media\watermarks');amane/media/watermarks" `
    --add-data "$(Join-Path $Root 'alembic.ini');." `
    --collect-submodules amane `
    --exclude-module patchright `
    --exclude-module IPython `
    --collect-all pydantic_ai `
    --collect-all pydantic_graph `
    --collect-all genai_prices `
    --hidden-import socksio `
    --copy-metadata genai_prices `
    --copy-metadata pydantic_ai_slim `
    --copy-metadata amane `
    --distpath (Join-Path $Work "dist") `
    --workpath (Join-Path $Work "build") `
    --specpath $Work `
    (Join-Path $Root "scripts\pyinstaller_entry.py")
if ($LASTEXITCODE -ne 0) {
    Write-Error "pyinstaller failed ($LASTEXITCODE)"
    exit $LASTEXITCODE
}

$Onedir = Join-Path $Work "dist\Amane.Server"
if (-not (Test-Path $Onedir)) {
    Write-Error "PyInstaller onedir missing: $Onedir"
    exit 1
}

$Version = (& (Join-Path $Root ".venv\Scripts\python.exe") -c "from amane.version import get_version; print(get_version())").Trim()
if ($LASTEXITCODE -ne 0 -or -not $Version) {
    Write-Error "failed to read amane.version"
    exit 1
}

$ShellOut = Join-Path $Work "shell"
dotnet publish (Join-Path $Root "winapp\Amane.csproj") `
    -c Release `
    -r win-x64 `
    --self-contained `
    -o $ShellOut `
    /p:Version="$Version"
if ($LASTEXITCODE -ne 0) {
    Write-Error "dotnet publish failed"
    exit 1
}

Get-ChildItem $ShellOut -File | Where-Object {
    $_.Extension -notin ".pdb", ".lib", ".exp", ".xml", ".json"
} | ForEach-Object {
    Copy-Item $_.FullName -Destination $Out
}

Copy-Item -Recurse $Onedir (Join-Path $Out "onedir")
New-Item -ItemType Directory -Force -Path (Join-Path $Out "web") | Out-Null
Copy-Item -Recurse (Join-Path $Root "web\dist") (Join-Path $Out "web\dist")

$Exe = Join-Path $Out "Amane.exe"
$Server = Join-Path $Out "onedir\Amane.Server.exe"
$Web = Join-Path $Out "web\dist\index.html"
foreach ($path in @($Exe, $Server, $Web)) {
    if (-not (Test-Path $path)) {
        Write-Error "missing $path"
        exit 1
    }
}

Write-Output "EXE=$Exe"
Write-Output "SERVER=$Server"
Write-Output "APP=$Out"
Get-ChildItem $Out | Format-Table Name, Length
