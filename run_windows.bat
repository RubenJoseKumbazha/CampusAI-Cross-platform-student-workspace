@echo off
setlocal
cd /d "%~dp0"
if not exist ".env" copy ".env.example" ".env" >nul
if not exist ".venv\Scripts\python.exe" (
  py -3.12 -m venv .venv 2>nul
  if errorlevel 1 py -3.11 -m venv .venv
  if errorlevel 1 py -m venv .venv
)
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo Dependency installation failed. Please install Python 3.11 or 3.12 and try again.
  pause
  exit /b 1
)
echo.
echo CampusAI is starting. Your browser will open automatically when the server is ready.
start "CampusAI Server" /min ".venv\Scripts\python.exe" -m uvicorn main:app --host 127.0.0.1 --port 8000
powershell -NoProfile -ExecutionPolicy Bypass -Command "$url='http://127.0.0.1:8000'; for($i=0;$i -lt 40;$i++){try{$r=Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 1; if($r.StatusCode -eq 200){Start-Process $url; exit 0}}catch{}; Start-Sleep -Milliseconds 500}; Write-Host 'CampusAI server did not become ready. Check the CampusAI Server window.'"
