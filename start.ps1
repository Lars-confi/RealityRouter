# Reality Router Native Windows Startup Script
# This script handles environment preparation and launches the setup wizard.

$ErrorActionPreference = "Stop"

# Define App Home
if ($env:REALITY_ROUTER_HOME -eq $null) {
    $REALITY_ROUTER_HOME = Join-Path $env:USERPROFILE ".reality_router"
} else {
    $REALITY_ROUTER_HOME = $env:REALITY_ROUTER_HOME
}

Write-Host "========================================" -ForegroundColor Blue
Write-Host "     Reality Router Initialization      " -ForegroundColor Blue
Write-Host "========================================" -ForegroundColor Blue

# Ensure directories exist
if (-not (Test-Path $REALITY_ROUTER_HOME)) {
    New-Item -ItemType Directory -Force -Path $REALITY_ROUTER_HOME | Out-Null
}
New-Item -ItemType Directory -Force -Path (Join-Path $REALITY_ROUTER_HOME "config") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $REALITY_ROUTER_HOME "logs") | Out-Null

# Optional: Migrate existing files if they exist in current directory
$filesToMigrate = @(".env", "disabled_models.json", "user_models.json", "reality_router.db")
foreach ($file in $filesToMigrate) {
    if (Test-Path $file) {
        $target = Join-Path $REALITY_ROUTER_HOME $file
        if (-not (Test-Path $target)) {
            Write-Host "Migrating $file to $REALITY_ROUTER_HOME..." -ForegroundColor Yellow
            Copy-Item $file $target
        }
        Remove-Item $file
    }
}

# 1. Check for Python 3
try {
    python --version | Out-Null
} catch {
    Write-Host "Error: Python is not installed or not in PATH. Please install Python to continue." -ForegroundColor Red
    exit 1
}

# 2. Check/Create Virtual Environment
if (-not (Test-Path "venv")) {
    Write-Host "Creating Python virtual environment..." -ForegroundColor Green
    python -m venv venv
} else {
    Write-Host "Virtual environment found." -ForegroundColor Green
}

# 3. Activate Virtual Environment
Write-Host "Activating virtual environment..." -ForegroundColor Green
if (Test-Path ".\venv\Scripts\Activate.ps1") {
    . ".\venv\Scripts\Activate.ps1"
} else {
    Write-Host "Warning: Could not find activation script. Attempting to continue anyway..." -ForegroundColor Yellow
}

# 4. Install Dependencies
Write-Host "Checking and installing dependencies..." -ForegroundColor Green
Set-Location "reality-router"
python -m pip install --upgrade pip --quiet
python -m pip install -r requirements.txt --quiet
Set-Location ".."

# 5. Launch the Python TUI
Write-Host "Launching Reality Router Setup Wizard..." -ForegroundColor Green
$env:LOG_DIR = Join-Path $REALITY_ROUTER_HOME "logs"
# Use absolute path for safety
$scriptPath = Join-Path $PSScriptRoot "start_router.py"
python $scriptPath

# Deactivate is handled by the shell session closing or manual call
