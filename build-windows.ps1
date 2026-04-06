# RpaClaw Windows Desktop Build Script
# Requires: Node.js 20+, Python 3.13, PowerShell 5.1+

param(
    [switch]$SkipFrontend,
    [switch]$SkipPython,
    [switch]$SkipElectron
)

$ErrorActionPreference = "Stop"

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "RpaClaw Windows Desktop Build" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

$RootDir = $PSScriptRoot
$BuildDir = Join-Path $RootDir "build"
$PythonDir = Join-Path $BuildDir "python"
$PythonUrl = "https://www.python.org/ftp/python/3.13.0/python-3.13.0-embed-amd64.zip"
$PythonZip = Join-Path $BuildDir "python-embed.zip"
$GetPipUrl = "https://bootstrap.pypa.io/get-pip.py"
$GetPipPath = Join-Path $BuildDir "get-pip.py"

# Create build directory
if (-not (Test-Path $BuildDir)) {
    New-Item -ItemType Directory -Path $BuildDir | Out-Null
}

# Step 1: Build Frontend
if (-not $SkipFrontend) {
    Write-Host "[1/3] Building Frontend..." -ForegroundColor Green
    Push-Location (Join-Path $RootDir "RpaClaw\frontend")

    Write-Host "  Installing dependencies..." -ForegroundColor Yellow
    npm install --silent

    Write-Host "  Building production bundle..." -ForegroundColor Yellow
    npm run build

    Pop-Location
    Write-Host "  Frontend build complete!" -ForegroundColor Green
    Write-Host ""
} else {
    Write-Host "[1/3] Skipping Frontend build" -ForegroundColor Gray
    Write-Host ""
}

# Step 2: Prepare Python Environment
if (-not $SkipPython) {
    Write-Host "[2/3] Preparing Python Environment..." -ForegroundColor Green

    # Download Python embeddable package
    if (-not (Test-Path $PythonZip)) {
        Write-Host "  Downloading Python 3.13 embeddable..." -ForegroundColor Yellow
        Invoke-WebRequest -Uri $PythonUrl -OutFile $PythonZip
    }

    # Extract Python
    if (Test-Path $PythonDir) {
        Write-Host "  Removing old Python directory..." -ForegroundColor Yellow
        Remove-Item -Recurse -Force $PythonDir
    }
    Write-Host "  Extracting Python..." -ForegroundColor Yellow
    Expand-Archive -Path $PythonZip -DestinationPath $PythonDir -Force

    # Enable site-packages by uncommenting import site in python313._pth
    $PthFile = Join-Path $PythonDir "python313._pth"
    if (Test-Path $PthFile) {
        $content = Get-Content $PthFile
        $content = $content -replace "#import site", "import site"
        Set-Content -Path $PthFile -Value $content
    }

    # Download get-pip.py
    if (-not (Test-Path $GetPipPath)) {
        Write-Host "  Downloading get-pip.py..." -ForegroundColor Yellow
        Invoke-WebRequest -Uri $GetPipUrl -OutFile $GetPipPath
    }

    # Install pip
    Write-Host "  Installing pip..." -ForegroundColor Yellow
    $PythonExe = Join-Path $PythonDir "python.exe"
    & $PythonExe $GetPipPath --no-warn-script-location

    # Install backend dependencies
    Write-Host "  Installing backend dependencies..." -ForegroundColor Yellow
    & $PythonExe -m pip install -r (Join-Path $RootDir "RpaClaw\backend\requirements.txt") --no-warn-script-location

    # Install task-service dependencies
    Write-Host "  Installing task-service dependencies..." -ForegroundColor Yellow
    & $PythonExe -m pip install -r (Join-Path $RootDir "RpaClaw\task-service\requirements.txt") --no-warn-script-location

    # Install Playwright browsers
    Write-Host "  Installing Playwright Chromium browser..." -ForegroundColor Yellow
    Write-Host "  (This may take several minutes...)" -ForegroundColor Yellow
    $PlaywrightBrowsersPath = Join-Path $PythonDir "Lib\site-packages\playwright\driver\package\.local-browsers"
    $env:PLAYWRIGHT_BROWSERS_PATH = $PlaywrightBrowsersPath
    & $PythonExe -m playwright install chromium
    Remove-Item Env:\PLAYWRIGHT_BROWSERS_PATH

    Write-Host "  Python environment ready!" -ForegroundColor Green
    Write-Host ""
} else {
    Write-Host "[2/3] Skipping Python environment preparation" -ForegroundColor Gray
    Write-Host ""
}

# Step 3: Build Electron Application
if (-not $SkipElectron) {
    Write-Host "[3/3] Building Electron Application..." -ForegroundColor Green
    Push-Location (Join-Path $RootDir "electron-app")

    Write-Host "  Installing dependencies..." -ForegroundColor Yellow
    npm install --silent

    Write-Host "  Compiling TypeScript..." -ForegroundColor Yellow
    npm run build

    Write-Host "  Packaging with Electron Builder..." -ForegroundColor Yellow
    Write-Host "  (This may take several minutes...)" -ForegroundColor Yellow
    npm run dist

    Pop-Location
    Write-Host "  Electron build complete!" -ForegroundColor Green
    Write-Host ""
} else {
    Write-Host "[3/3] Skipping Electron build" -ForegroundColor Gray
    Write-Host ""
}

# Summary
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Build Complete!" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

$InstallerPath = Join-Path $RootDir "electron-app\release\RpaClaw Setup 1.0.0.exe"
if (Test-Path $InstallerPath) {
    Write-Host "Installer created at:" -ForegroundColor Green
    Write-Host "  $InstallerPath" -ForegroundColor White
    Write-Host ""
    Write-Host "File size: $([math]::Round((Get-Item $InstallerPath).Length / 1MB, 2)) MB" -ForegroundColor Gray
} else {
    Write-Host "Warning: Installer not found at expected location" -ForegroundColor Yellow
    Write-Host "Check electron-app/release/ directory" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Next steps:" -ForegroundColor Cyan
Write-Host "  1. Test the installer on a clean Windows VM" -ForegroundColor White
Write-Host "  2. Verify first-run wizard works correctly" -ForegroundColor White
Write-Host "  3. Test all features (chat, RPA, tasks, skills)" -ForegroundColor White
Write-Host ""
