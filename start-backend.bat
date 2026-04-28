@echo off
REM PBIChat Backend Startup Batch Script
REM Run from backend folder: start-backend.bat

echo 🚀 Starting PBIChat Backend...

REM Check Python
py --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ❌ Python not found. Install Python 3.11+ and add to PATH.
    pause
    exit /b 1
)

echo ✅ Python found

REM Set paths
set VENV_PATH=C:\temp\pbiviz-venv
set BACKEND_DIR=%~dp0
set REQUIREMENTS=%BACKEND_DIR%requirements.txt

REM Create venv if missing
if not exist "%VENV_PATH%" (
    echo 📦 Creating virtual environment...
    py -m venv "%VENV_PATH%"
    if %errorlevel% neq 0 (
        echo ❌ Failed to create venv
        pause
        exit /b 1
    )
)

REM Upgrade pip
echo ⬆️ Upgrading pip...
"%VENV_PATH%\Scripts\python.exe" -m pip install --upgrade pip --quiet

REM Install requirements
echo 📥 Installing dependencies...
"%VENV_PATH%\Scripts\python.exe" -m pip install -r "%REQUIREMENTS%" --quiet
if %errorlevel% neq 0 (
    echo ❌ Failed to install requirements
    pause
    exit /b 1
)

REM Start server
echo ▶️ Starting uvicorn server...
echo 🎯 Backend will run at http://localhost:8000
echo    Press Ctrl+C to stop
"%VENV_PATH%\Scripts\python.exe" -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload