@echo off
setlocal
cd /d "%~dp0"

echo Installing build dependencies...
py -m pip install pyinstaller pyserial keyboard hidapi || exit /b 1

echo Building PumpFillGui.exe...
py -m PyInstaller --clean PumpFillGui.spec || exit /b 1

echo Done: %CD%\dist\PumpFillGui.exe
endlocal
