@echo off
setlocal
REM Run from repo clone: updates with git (read-only token ok), then starts GUI.
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
if exist "dist\PumpMotorTestGui.exe" (
  start "" "%~dp0..\dist\PumpMotorTestGui.exe"
) else (
  py "%~dp0..\pump_motor_test_gui.py"
  if errorlevel 1 python "%~dp0..\pump_motor_test_gui.py"
)

endlocal
