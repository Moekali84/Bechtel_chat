# PBIChat Backend Startup Script
# Run this from backend folder: .\start-backend.ps1

param(
    [switch]$SkipHealthCheck
)

Write-Host "🚀 Starting PBIChat Backend..." -ForegroundColor Green

# Check Python
try {
    $pythonVersion = & py --version 2>$null
    Write-Host "✅ Python found: $pythonVersion" -ForegroundColor Green
} catch {
    Write-Host "❌ Python not found. Install Python 3.11+ and add to PATH." -ForegroundColor Red
    exit 1
}

# Set paths
$venvPath = "C:\temp\pbiviz-venv"
$backendDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$requirementsPath = Join-Path $backendDir "requirements.txt"

# Create venv if missing
if (!(Test-Path $venvPath)) {
    Write-Host "📦 Creating virtual environment..." -ForegroundColor Yellow
    & py -m venv $venvPath
    if ($LASTEXITCODE -ne 0) {
        Write-Host "❌ Failed to create venv" -ForegroundColor Red
        exit 1
    }
}

# Upgrade pip
Write-Host "⬆️ Upgrading pip..." -ForegroundColor Yellow
& "$venvPath\Scripts\python.exe" -m pip install --upgrade pip --quiet

# Install requirements
Write-Host "📥 Installing dependencies..." -ForegroundColor Yellow
& "$venvPath\Scripts\python.exe" -m pip install -r $requirementsPath --quiet
if ($LASTEXITCODE -ne 0) {
    Write-Host "❌ Failed to install requirements" -ForegroundColor Red
    exit 1
}

# Start server
Write-Host "▶️ Starting uvicorn server..." -ForegroundColor Green
$serverProcess = Start-Process -FilePath "$venvPath\Scripts\python.exe" `
    -ArgumentList "-m uvicorn main:app --host 0.0.0.0 --port 8000 --reload" `
    -WorkingDirectory $backendDir `
    -NoNewWindow `
    -PassThru

# Wait a moment for startup
Start-Sleep -Seconds 3

# Health check
if (!$SkipHealthCheck) {
    Write-Host "🔍 Checking health..." -ForegroundColor Yellow
    try {
        $response = Invoke-WebRequest -Uri "http://localhost:8000/health" -UseBasicParsing -TimeoutSec 10
        if ($response.StatusCode -eq 200) {
            $content = $response.Content | ConvertFrom-Json
            Write-Host "✅ Backend healthy!" -ForegroundColor Green
            Write-Host "   Status: $($content.status)" -ForegroundColor Cyan
            Write-Host "   Databricks: $($content.databricks_connected)" -ForegroundColor Cyan
            Write-Host "   LLM: $($content.llm_configured)" -ForegroundColor Cyan
        } else {
            Write-Host "❌ Health check failed: $($response.StatusCode)" -ForegroundColor Red
        }
    } catch {
        Write-Host "❌ Health check failed: $($_.Exception.Message)" -ForegroundColor Red
    }
}

Write-Host "🎯 Backend running at http://localhost:8000" -ForegroundColor Green
Write-Host "   Press Ctrl+C to stop" -ForegroundColor Gray

# Wait for server process
Wait-Process -Id $serverProcess.Id