# Build the Windows installer (PyInstaller package + Inno Setup).
#
# Usage (from project root):
#   powershell -ExecutionPolicy Bypass -File scripts\build_installer.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\build_installer.ps1 -SkipPackage
#
# Requires: uv, and Inno Setup 6 (ISCC.exe on PATH or in the default install dir).

[CmdletBinding()]
param(
    [switch]$SkipPackage,
    [switch]$NoClean
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

function Find-Iscc {
    $cmd = Get-Command "ISCC.exe" -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }

    $candidates = @(
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "${env:ProgramFiles}\Inno Setup 6\ISCC.exe",
        "${env:LOCALAPPDATA}\Programs\Inno Setup 6\ISCC.exe"
    )
    foreach ($path in $candidates) {
        if (Test-Path $path) { return $path }
    }
    return $null
}

$iscc = Find-Iscc
if (-not $iscc) {
    Write-Error @"
Inno Setup 6 compiler (ISCC.exe) was not found.
Install it from https://jrsoftware.org/isinfo.php
or add ISCC.exe to your PATH.
"@
}

$distRoot = Join-Path $Root "dist\eBrailleChecker"
$distExe = Join-Path $distRoot "eBrailleChecker.exe"
if (-not $SkipPackage) {
    Write-Host "==> Packaging app with PyInstaller…" -ForegroundColor Cyan
    Write-Host "    (bundles Temurin JRE, eBraille Checker, and EPUBCheck)" -ForegroundColor DarkGray
    $pkgArgs = @("run", "python", "scripts/package.py")
    if (-not $NoClean) { $pkgArgs += "--clean" }
    & uv @pkgArgs
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
elseif (-not (Test-Path $distExe)) {
    Write-Error "Packaged app not found at $distExe. Run without -SkipPackage first."
}

# Ensure the installer will ship the Java runtime and both checker jars.
$requiredDirs = @(
    @{ Path = (Join-Path $distRoot "runtime");   Label = "bundled Temurin JRE (runtime/)" },
    @{ Path = (Join-Path $distRoot "checker");   Label = "bundled eBraille Checker (checker/)" },
    @{ Path = (Join-Path $distRoot "epubcheck"); Label = "bundled EPUBCheck (epubcheck/)" }
)
foreach ($req in $requiredDirs) {
    if (-not (Test-Path $req.Path -PathType Container)) {
        Write-Error @"
Missing $($req.Label) under dist\eBrailleChecker\.
Re-run packaging without --no-bundle-java / --no-bundle-checker / --no-bundle-epubcheck:
  uv run python scripts/package.py --clean
"@
    }
}
Write-Host "==> Bundled components present: runtime/, checker/, epubcheck/" -ForegroundColor DarkGray

Write-Host "==> Compiling Inno Setup installer…" -ForegroundColor Cyan
$iss = Join-Path $Root "installer\eBrailleChecker.iss"
$outputDir = Join-Path $Root "installer\Output"
# Compile to %TEMP% first — antivirus often locks Setup.exe while ISCC
# updates its icon resources in a watched project Output folder.
$tempOut = Join-Path $env:TEMP ("ebraille-iss-out-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force -Path $tempOut | Out-Null
try {
    & $iscc "/O$tempOut" $iss
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    New-Item -ItemType Directory -Force -Path $outputDir | Out-Null
    Copy-Item (Join-Path $tempOut "*") $outputDir -Force
}
finally {
    Remove-Item $tempOut -Recurse -Force -ErrorAction SilentlyContinue
}

$setup = Get-ChildItem $outputDir -Filter "eBrailleCheckerGUI-*-setup.exe" |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1

Write-Host ""
Write-Host "Installer build complete." -ForegroundColor Green
if ($setup) {
    Write-Host "Output: $($setup.FullName)"
}
else {
    Write-Host "Output directory: $outputDir"
}
