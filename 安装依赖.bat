@echo off
cd /d "%~dp0"
where uv >nul 2>nul
if errorlevel 1 (
  echo Please install uv from https://docs.astral.sh/uv/getting-started/installation/
  pause
  exit /b 1
)
uv venv .venv --python 3.12
if errorlevel 1 exit /b 1
uv pip install --python .venv\Scripts\python.exe -r requirements.lock.txt
if errorlevel 1 exit /b 1
echo Ready. Open startup VBS to launch.
pause
