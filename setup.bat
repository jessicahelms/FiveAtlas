@echo off
REM ============================================================
REM  FiveAtlas - one-time setup (Windows)
REM  Creates a local Python environment and installs everything.
REM ============================================================
setlocal
cd /d "%~dp0"

echo.
echo === FiveAtlas setup ===
echo.

REM find Python (prefer the py launcher)
where py >nul 2>&1
if %errorlevel%==0 (set "PY=py") else (set "PY=python")

%PY% --version >nul 2>&1
if not %errorlevel%==0 (
  echo ERROR: Python was not found.
  echo Install Python 3.10-3.12 from https://www.python.org/downloads/
  echo and tick "Add python.exe to PATH" during install, then re-run setup.bat
  pause
  exit /b 1
)

echo Creating local Python environment (.venv) ...
%PY% -m venv .venv
if not exist ".venv\Scripts\python.exe" (
  echo ERROR: failed to create the virtual environment.
  pause
  exit /b 1
)

echo Installing dependencies (this can take a few minutes) ...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if not %errorlevel%==0 (
  echo ERROR: dependency install failed. See the messages above.
  pause
  exit /b 1
)

echo.
echo === Setup complete! ===
echo Double-click  run.bat  to start the app.
echo.
pause
