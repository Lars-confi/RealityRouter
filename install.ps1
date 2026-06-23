# Reality Router Native Windows Installation Script
# This script automates the setup of Reality Router for PowerShell users.

$ErrorActionPreference = "Stop"

# --- Configuration ---
$REPO_URL = "https://github.com/Lars-confi/RealityRouter"
$TARGET_DIR = Join-Path $env:USERPROFILE ".reality_router"

Write-Host "========================================" -ForegroundColor Blue
Write-Host "   Reality Router Windows Installer     " -ForegroundColor Blue
Write-Host "========================================" -ForegroundColor Blue

# --- Pre-flight Checks ---
Write-Host "Step 1: Checking dependencies..." -ForegroundColor Cyan

# 1. Check for Git
if (!(Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Host "Error: Git is not installed. Please install Git for Windows (https://git-scm.com/) and try again." -ForegroundColor Red
    exit 1
}

# 2. Check for Python
$pythonCmd = "python"
try {
    & $pythonCmd --version | Out-Null
} catch {
    try {
        $pythonCmd = "python3"
        & $pythonCmd --version | Out-Null
    } catch {
        Write-Host "Error: Python 3 is not installed or not in PATH. Please install Python from python.org or the Windows Store." -ForegroundColor Red
        exit 1
    }
}
Write-Host "✅ Found Git and Python ($pythonCmd)" -ForegroundColor Green

# --- Installation ---
Write-Host "`nStep 2: Cloning repository..." -ForegroundColor Cyan

if (Test-Path $TARGET_DIR) {
    if (Test-Path (Join-Path $TARGET_DIR ".git")) {
        Write-Host "Existing installation found. Updating..." -ForegroundColor Yellow
        Push-Location $TARGET_DIR
        git pull
        Pop-Location
    } else {
        Write-Host "Target directory exists but is not a git repo. Initializing..." -ForegroundColor Yellow
        git clone $REPO_URL $TARGET_DIR
    }
} else {
    Write-Host "Cloning to $TARGET_DIR..." -ForegroundColor Green
    git clone $REPO_URL $TARGET_DIR
}

# --- Virtual Environment Setup ---
Write-Host "`nStep 3: Setting up Virtual Environment..." -ForegroundColor Cyan
Push-Location $TARGET_DIR

if (-not (Test-Path "venv")) {
    Write-Host "Creating venv..." -ForegroundColor Green
    & $pythonCmd -m venv venv
} else {
    Write-Host "Re-using existing venv." -ForegroundColor Green
}

Write-Host "Activating venv and installing dependencies..." -ForegroundColor Green
# Use the full path to the venv python to ensure correct package installation
$venvPython = Join-Path $TARGET_DIR "venv\Scripts\python.exe"
$requirements = Join-Path $TARGET_DIR "reality-router\requirements.txt"

& $venvPython -m pip install --upgrade pip --quiet
& $venvPython -m pip install -r $requirements --quiet

# --- PowerShell Function Setup ---
Write-Host "`nStep 4: Configuring PowerShell shortcut..." -ForegroundColor Cyan

$functionName = "reality-router"
$functionCode = @"

# Reality Router Shortcut
function $functionName {
    Push-Location "$TARGET_DIR"
    if (Test-Path ".\venv\Scripts\Activate.ps1") {
        . ".\venv\Scripts\Activate.ps1"
        python start_router.py
        deactivate
    } else {
        Write-Host "Error: Virtual environment not found in $TARGET_DIR" -ForegroundColor Red
    }
    Pop-Location
}
"@

# Check if profile exists, create if not
if (!(Test-Path $PROFILE)) {
    New-Item -Type File -Path $PROFILE -Force | Out-Null
}

$profileContent = Get-Content $PROFILE -Raw
if ($profileContent -notlike "*function $functionName*") {
    Write-Host "Adding '$functionName' command to your PowerShell profile..." -ForegroundColor Green
    Add-Content $PROFILE $functionCode
    Write-Host "✅ Shortcut added!" -ForegroundColor Green
} else {
    Write-Host "Shortcut already exists in your profile." -ForegroundColor Yellow
}

# --- Completion ---
Write-Host "`n" + ("=" * 40) -ForegroundColor Blue
Write-Host "✅ Installation Complete!" -ForegroundColor Green
Write-Host ("=" * 40) -ForegroundColor Blue
Write-Host "`nTo start the Reality Router:"
Write-Host "1. Restart your PowerShell window"
Write-Host "2. Type: " -NoNewline; Write-Host "reality-router" -ForegroundColor Cyan
Write-Host "`nLocation: $TARGET_DIR"

Pop-Location
exit 0
