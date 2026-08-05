@echo off
REM ============================================================
REM  FiveAtlas - start the app (Windows)
REM  Opens http://localhost:8000 in your browser.
REM ============================================================
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo The app isn't set up yet. Please run  setup.bat  first.
  pause
  exit /b 1
)

echo Starting FiveAtlas ...
echo Open http://localhost:8000  (opening it for you now)
echo Keep this window open while you use the app; close it to stop.
echo.

start "" "http://localhost:8000"
cd backend
"..\.venv\Scripts\python.exe" -m uvicorn app:app --host 127.0.0.1 --port 8000
