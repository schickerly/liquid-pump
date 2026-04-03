@echo off
setlocal
cd /d "%~dp0"

echo Installing build dependency...
py -m pip install pyinstaller pyserial keyboard hidapi || exit /b 1

echo Building PumpMotorTestGui.exe...
py -m PyInstaller --clean PumpMotorTestGui.spec || exit /b 1

echo Done: %CD%\dist\PumpMotorTestGui.exe
endlocal
