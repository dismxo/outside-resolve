@echo off
REM Build a single-file PORTABLE OutsideResolve.exe (copy it anywhere, no install).
cd /d "%~dp0"
setlocal
echo Installing build dependencies...
py -m pip install -r requirements.txt "pyinstaller>=6.3.0" || goto :err
echo.
echo Building portable OutsideResolve.exe ...
py -m PyInstaller --noconfirm --onefile --windowed --name OutsideResolve --icon "assets\icon.ico" main.py || goto :err
echo.
echo Done. Portable app:  "%~dp0dist\OutsideResolve.exe"
echo Copy that one file anywhere (desktop, USB stick) and double-click it.
goto :done
:err
echo.
echo Build FAILED. Make sure Python is installed and on PATH (try: py --version).
:done
pause
