@echo off
REM Build the fast-starting folder version into dist\OutsideResolve\ (install.bat uses this).
cd /d "%~dp0"
setlocal
echo Installing build dependencies...
py -m pip install -r requirements.txt "pyinstaller>=6.3.0" || goto :err
echo.
echo Building OutsideResolve ...
py -m PyInstaller --noconfirm OutsideResolve.spec || goto :err
echo.
echo Done:  "%~dp0dist\OutsideResolve\OutsideResolve.exe"
goto :done
:err
echo.
echo Build FAILED. Make sure Python is installed and on PATH (try: py --version).
:done
pause
