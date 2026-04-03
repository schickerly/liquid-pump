@echo off
setlocal
REM Git pull (read-only token ok), then start fill GUI — use with desktop shortcut on field PC.
cd /d "%~dp0.."

where git >nul 2>&1
if errorlevel 1 (
  echo Git not found on PATH; starting GUI without pull.
  goto run_gui
)

git rev-parse --is-inside-work-tree >nul 2>&1
if errorlevel 1 (
  echo Not a git clone; starting GUI without pull.
  goto run_gui
)

echo Updating repo (git pull --ff-only)...
git pull --ff-only
if errorlevel 1 (
  echo Git pull failed; starting GUI anyway with current files.
)

:run_gui
REM Always run pump_fill_gui.py from the repo so git pull updates apply. For offline use with no Python,
REM run dist\PumpFillGui.exe directly (or rebuild after pulling).
py "%~dp0..\pump_fill_gui.py"
if errorlevel 1 python "%~dp0..\pump_fill_gui.py"

endlocal
