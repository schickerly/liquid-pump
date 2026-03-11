@echo off
setlocal

echo [1/5] Verifying launcher source config...
findstr /c:"BRANCH = \"codex/create-gui-for-pump-control-system\"" updater_launcher.py >nul || (
  echo ERROR: updater_launcher.py is not pinned to codex/create-gui-for-pump-control-system
  exit /b 1
)
findstr /c:"LAUNCHER_VERSION = \"2026.03.11.3\"" updater_launcher.py >nul || (
  echo ERROR: updater_launcher.py does not have expected launcher version 2026.03.11.3
  exit /b 1
)

echo [2/5] Installing build dependency...
py -m pip install pyinstaller || exit /b 1

echo [3/5] Building clean EXE...
py -m PyInstaller --clean --onefile --name PumpLauncher updater_launcher.py || exit /b 1

echo [4/5] Running diagnostic mode from built EXE...
dist\PumpLauncher.exe --diagnose || exit /b 1

echo [5/5] Done. Built EXE: %CD%\dist\PumpLauncher.exe
endlocal
