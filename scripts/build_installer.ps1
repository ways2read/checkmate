# Build the Windows installer (PyInstaller package + Inno Setup + Authenticode).
#
# Usage (from project root):
#   powershell -ExecutionPolicy Bypass -File scripts\build_installer.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\build_installer.ps1 -SkipPackage
#   powershell -ExecutionPolicy Bypass -File scripts\build_installer.ps1 -SkipSign
#
# Requires: uv, and Inno Setup 6 (ISCC.exe on PATH or in the default install dir).
#
# Authenticode (same technique as Fido): after Inno compile, one signtool
# invocation so the GlobalSign USB-token PIN is asked once. Copies to
# installer\Output stay signed.
#   CHECKMATE_SKIP_INSTALLER_SIGN=1  — do not run signtool
#   CHECKMATE_SIGNTOOL               — full path to signtool.exe
#                                      (default: Windows Kits App Certification Kit)
#   CHECKMATE_SIGN_TIMESTAMP_URL     — RFC 3161 TSA (default: GlobalSign r6 advanced)

[CmdletBinding()]
param(
    [switch]$SkipPackage,
    [switch]$NoClean,
    [switch]$SkipSign
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

function Invoke-InstallerSign {
    param([Parameter(Mandatory = $true)][string]$SetupPath)

    if ($SkipSign -or $env:CHECKMATE_SKIP_INSTALLER_SIGN -eq "1") {
        $reason = if ($SkipSign) { "-SkipSign" } else { "CHECKMATE_SKIP_INSTALLER_SIGN=1" }
        Write-Host "Skipping installer sign ($reason): $SetupPath" -ForegroundColor Yellow
        return
    }

    $signtool = $env:CHECKMATE_SIGNTOOL
    if (-not $signtool) {
        $signtool = Join-Path ${env:ProgramFiles(x86)} "Windows Kits\10\App Certification Kit\signtool.exe"
    }
    $timestampUrl = $env:CHECKMATE_SIGN_TIMESTAMP_URL
    if (-not $timestampUrl) {
        $timestampUrl = "http://timestamp.globalsign.com/tsa/r6advanced1"
    }

    if (-not (Test-Path $signtool)) {
        Write-Error @"
signtool not found at "$signtool"
Set CHECKMATE_SIGNTOOL to the full path, or set CHECKMATE_SKIP_INSTALLER_SIGN=1 / -SkipSign to skip signing.
"@
    }

    Write-Host "==> Signing installer (Authenticode)…" -ForegroundColor Cyan
    Write-Host "    $SetupPath"
    & $signtool sign /a /tr $timestampUrl /td SHA256 /fd SHA256 $SetupPath
    if ($LASTEXITCODE -ne 0) {
        Write-Error "signtool failed (exit $LASTEXITCODE)"
    }
}

$iscc = Find-Iscc
if (-not $iscc) {
    Write-Error @"
Inno Setup 6 compiler (ISCC.exe) was not found.
Install it from https://jrsoftware.org/isinfo.php
or add ISCC.exe to your PATH.
"@
}

$distRoot = Join-Path $Root "dist\CheckMate"
$distExe = Join-Path $distRoot "CheckMate.exe"
if (-not $SkipPackage) {
    Write-Host "==> Packaging app with PyInstaller…" -ForegroundColor Cyan
    Write-Host "    (bundles Temurin JRE, eBraille Checker, EPUBCheck, veraPDF, and Ace)" -ForegroundColor DarkGray
    $pkgArgs = @("run", "python", "scripts/package.py")
    if (-not $NoClean) { $pkgArgs += "--clean" }
    & uv @pkgArgs
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
elseif (-not (Test-Path $distExe)) {
    Write-Error "Packaged app not found at $distExe. Run without -SkipPackage first."
}

# Ensure the installer will ship the Java runtime and checker tools.
$requiredDirs = @(
    @{ Path = (Join-Path $distRoot "runtime");   Label = "bundled Temurin JRE (runtime/)" },
    @{ Path = (Join-Path $distRoot "checker");   Label = "bundled eBraille Checker (checker/)" },
    @{ Path = (Join-Path $distRoot "epubcheck"); Label = "bundled EPUBCheck (epubcheck/)" },
    @{ Path = (Join-Path $distRoot "verapdf");   Label = "bundled veraPDF (verapdf/)" },
    @{ Path = (Join-Path $distRoot "ace");       Label = "bundled Ace by DAISY (ace/)" }
)
foreach ($req in $requiredDirs) {
    if (-not (Test-Path $req.Path -PathType Container)) {
        Write-Error @"
Missing $($req.Label) under dist\CheckMate\.
Re-run packaging without --no-bundle-* flags:
  uv run python scripts/package.py --clean
"@
    }
}
Write-Host "==> Bundled components present: runtime/, checker/, epubcheck/, verapdf/, ace/" -ForegroundColor DarkGray

Write-Host "==> Compiling Inno Setup installer…" -ForegroundColor Cyan
$iss = Join-Path $Root "installer\CheckMate.iss"
$outputDir = Join-Path $Root "installer\Output"
# Compile to %TEMP% first — antivirus often locks Setup.exe while ISCC
# updates its icon resources in a watched project Output folder.
$tempOut = Join-Path $env:TEMP ("checkmate-iss-out-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force -Path $tempOut | Out-Null
try {
    & $iscc "/O$tempOut" $iss
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    $tempSetup = Get-ChildItem $tempOut -Filter "CheckMate-*-setup.exe" |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
    if (-not $tempSetup) {
        Write-Error "Inno Setup did not produce CheckMate-*-setup.exe in $tempOut"
    }

    # Sign before copying so installer\Output stays signed (Fido pattern).
    Invoke-InstallerSign -SetupPath $tempSetup.FullName

    New-Item -ItemType Directory -Force -Path $outputDir | Out-Null
    Copy-Item (Join-Path $tempOut "*") $outputDir -Force
}
finally {
    Remove-Item $tempOut -Recurse -Force -ErrorAction SilentlyContinue
}

$setup = Get-ChildItem $outputDir -Filter "CheckMate-*-setup.exe" |
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
