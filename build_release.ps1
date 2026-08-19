<#
    Build the shippable FiveAtlas.

    Produces, in <OutRoot>\release\ :
      FiveAtlas/                  the app folder (double-click FiveAtlas.exe)
      FiveAtlas-<ver>-win64.zip   that folder, zipped -- this is what you send
      FiveAtlas-<ver>-setup.exe   installer, only if Inno Setup is installed

    Everything the build writes goes to OutRoot (~1 GB of scratch), which defaults
    to %LOCALAPPDATA%. That is a LOCAL disk -- building on the S: share works but
    is far slower -- and it is outside the repo, so the build trees never show up
    in git status. Nothing lands next to the source tree.

    Usage:
        powershell -ExecutionPolicy Bypass -File build_release.ps1
        powershell -ExecutionPolicy Bypass -File build_release.ps1 -Version 0.2.0
        powershell -ExecutionPolicy Bypass -File build_release.ps1 -OutRoot D:\builds
#>
param(
    [string]$Version = "",
    [string]$OutRoot = "$env:LOCALAPPDATA\FiveAtlas_build",
    [switch]$SkipFrontend
)

$ErrorActionPreference = "Stop"

# No -Version given: use the git tag this tree is at, the same way the macOS
# CI labels its build from the tag. It was a fixed "0.1.0" here, which is how
# the Windows zip of the very same commit as the Mac's 0.5.1 went out labelled
# 0.1.0 -- and stamped 0.1.0 into the provenance of every file it edited.
if (-not $Version) {
    try {
        $Version = (& git -C $PSScriptRoot describe --tags --dirty --always 2>$null)
    } catch { $Version = $null }
    if ($Version) { $Version = ($Version -replace '^v', '').Trim() }
    if (-not $Version) { $Version = "0.0.0-dev" }
}
$App = $PSScriptRoot
# Prefer the repo-local venv (what setup.bat creates, and what requirements.txt
# governs) over the one in the parent directory. It used to look ONLY in the
# parent, which on this machine resolves to a shared Python 3.9 environment that
# no requirements file constrains -- so the pins that keep the build working,
# numcodecs<0.16 in particular, did not apply to the Windows build at all.
$Venv = $null
foreach ($cand in @((Join-Path $App ".venv\Scripts\python.exe"),
                    (Join-Path (Split-Path $App -Parent) ".venv\Scripts\python.exe"))) {
    if (Test-Path $cand) { $Venv = $cand; break }
}
if (-not $Venv) {
    throw "python venv not found in $App\.venv or $(Split-Path $App -Parent)\.venv - run setup.bat first"
}

# Fail loudly on the dependency combination that silently broke the macOS build:
# zarr 2.x imports numcodecs.blosc.cbuffer_sizes, which numcodecs 0.16 removed, so
# `import zarr` raises and the packaged app dies at startup while the developer's
# machine is fine. Cheap to check, and the failure is otherwise very confusing.
$depCheck = & $Venv -c @"
import sys
try:
    import zarr, numcodecs
    from numcodecs.blosc import cbuffer_sizes  # noqa: F401
    print('OK ' + zarr.__version__ + ' ' + numcodecs.__version__)
except Exception as e:
    print('BAD ' + type(e).__name__ + ': ' + str(e))
"@
if ($depCheck -notmatch '^OK') {
    throw "the build environment is broken: $depCheck`nFix with: & '$Venv' -m pip install -r '$App\requirements.txt'"
}
Write-Host "   deps         -> zarr/numcodecs $($depCheck -replace '^OK ','')" -ForegroundColor DarkGray

$Work = Join-Path $OutRoot "work"
$DistDir = Join-Path $OutRoot "dist"
$Scratch = Join-Path $OutRoot "temp"
New-Item -ItemType Directory -Force -Path $OutRoot, $Work, $DistDir, $Scratch | Out-Null

Write-Host "== FiveAtlas $Version ==" -ForegroundColor Cyan
Write-Host "   build output -> $OutRoot" -ForegroundColor DarkGray
$drive = Get-PSDrive -Name (Split-Path $OutRoot -Qualifier).TrimEnd(':') -ErrorAction SilentlyContinue
if ($drive) { Write-Host ("   free there   -> {0:N1} GB" -f ($drive.Free / 1GB)) -ForegroundColor DarkGray }

# 1. the UI ------------------------------------------------------------------
if (-not $SkipFrontend) {
    Write-Host "[1/4] building frontend..." -ForegroundColor Yellow
    Push-Location (Join-Path $App "frontend")
    try {
        & npm.cmd run build
        if ($LASTEXITCODE -ne 0) { throw "npm run build failed" }
    } finally { Pop-Location }
} else {
    Write-Host "[1/4] skipping frontend build" -ForegroundColor DarkGray
}
if (-not (Test-Path (Join-Path $App "frontend\dist\index.html"))) {
    throw "frontend/dist/index.html missing - the UI did not build"
}

# 2. the exe -----------------------------------------------------------------
Write-Host "[2/4] running PyInstaller (a few minutes)..." -ForegroundColor Yellow
# A running copy (or a shell sitting inside dist\FiveAtlas) locks the output
# folder and PyInstaller dies with WinError 32 -- clear it first.
Get-Process FiveAtlas -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Push-Location $App
$oldTemp, $oldTmp = $env:TEMP, $env:TMP
try {
    # PyInstaller's scratch also has to stay off C:
    $env:TEMP = $Scratch; $env:TMP = $Scratch
    # The spec reads this, so the version baked into the build is the one asked
    # for here rather than a literal in the spec that silently goes stale.
    $env:FIVEATLAS_VERSION = $Version
    & $Venv -m PyInstaller --noconfirm --clean `
        --workpath $Work --distpath $DistDir FiveAtlas.spec
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
} finally {
    $env:TEMP = $oldTemp; $env:TMP = $oldTmp
    Pop-Location
}

$Built = Join-Path $DistDir "FiveAtlas"
if (-not (Test-Path (Join-Path $Built "FiveAtlas.exe"))) { throw "FiveAtlas.exe was not produced" }

$Release = Join-Path $OutRoot "release"
New-Item -ItemType Directory -Force -Path $Release | Out-Null
$Staged = Join-Path $Release "FiveAtlas"
if (Test-Path $Staged) { Remove-Item -Recurse -Force $Staged }
Copy-Item -Recurse $Built $Staged

# a plain-English note next to the exe
@"
FiveAtlas $Version

To run:  double-click FiveAtlas.exe
A console window opens with the address, and your browser opens on it.
Keep the console window open while you work; closing it stops the app.

IMPORTANT: copy this folder to your own computer's hard drive first.
Running it straight off a network drive (S:, or any \\server share) makes
startup take minutes instead of seconds -- the app is ~1,100 files and every
one of them has to come over the network. From a local disk it starts in
about 2 seconds.

The first time Windows may say "Windows protected your PC" because the app
is not code-signed. Click "More info" then "Run anyway".

To load your data: click the folder button at the top, or "Open folder..."
in the sidebar, and pick the folder that holds your experiment.xenium file.

Your original files are never modified. Edits are saved under:
   %LOCALAPPDATA%\FiveAtlas
Use Export in the sidebar to write GeoJSON out where you want it.
"@ | Set-Content -Encoding utf8 (Join-Path $Staged "README.txt")

# 3. zip ---------------------------------------------------------------------
Write-Host "[3/4] zipping..." -ForegroundColor Yellow
$Zip = Join-Path $Release "FiveAtlas-$Version-win64.zip"
if (Test-Path $Zip) { Remove-Item -Force $Zip }
Compress-Archive -Path $Staged -DestinationPath $Zip

# 4. installer (optional) ----------------------------------------------------
Write-Host "[4/4] installer..." -ForegroundColor Yellow
$Iscc = $null
foreach ($c in @("ISCC.exe",
                 "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
                 "C:\Program Files\Inno Setup 6\ISCC.exe")) {
    $found = if ($c -eq "ISCC.exe") { (Get-Command $c -ErrorAction SilentlyContinue).Source } `
             elseif (Test-Path $c) { $c } else { $null }
    if ($found) { $Iscc = $found; break }
}
if ($Iscc) {
    # the .iss lives with the source but must read/write on OutRoot
    & $Iscc "/DMyAppVersion=$Version" "/DStageDir=$Staged" "/DOutDir=$Release" `
            (Join-Path $App "installer.iss")
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }
} else {
    Write-Host "      Inno Setup not found - zip only." -ForegroundColor DarkGray
    Write-Host "      To also build the installer:  winget install JRSoftware.InnoSetup" -ForegroundColor DarkGray
}

Write-Host ""
Write-Host "Done. In $Release :" -ForegroundColor Green
Get-ChildItem $Release | ForEach-Object {
    $size = if ($_.PSIsContainer) {
        "{0:N0} MB (folder)" -f ((Get-ChildItem $_.FullName -Recurse -File | Measure-Object Length -Sum).Sum / 1MB)
    } else { "{0:N0} MB" -f ($_.Length / 1MB) }
    "  {0,-42} {1}" -f $_.Name, $size
}
