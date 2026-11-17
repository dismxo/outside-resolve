@echo off
REM Install OutsideResolve for the current user (no admin needed).
REM   install.bat /quiet   doesn't wait for a key press at the end
cd /d "%~dp0"
setlocal EnableExtensions
set "APPDIR=%LOCALAPPDATA%\Programs\OutsideResolve"
set "EXE=%APPDIR%\OutsideResolve.exe"
set "SMDIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs"

if not exist "dist\OutsideResolve\OutsideResolve.exe" (
  echo Building the app first ^(one-time^)...
  py -m pip install -r requirements.txt "pyinstaller>=6.3.0" || goto :err
  py -m PyInstaller --noconfirm OutsideResolve.spec || goto :err
)

echo Installing to "%APPDIR%" ...
taskkill /F /IM OutsideResolve.exe >nul 2>&1
ping -n 2 127.0.0.1 >nul
if exist "%APPDIR%" rmdir /S /Q "%APPDIR%"
xcopy /E /I /Y /Q "dist\OutsideResolve" "%APPDIR%" >nul || goto :err

echo Creating shortcuts...
powershell -NoProfile -Command "$w=New-Object -ComObject WScript.Shell; $s=$w.CreateShortcut('%SMDIR%\Outside Resolve.lnk'); $s.TargetPath='%EXE%'; $s.WorkingDirectory='%APPDIR%'; $s.IconLocation='%EXE%'; $s.Save()"
powershell -NoProfile -Command "$w=New-Object -ComObject WScript.Shell; $d=[Environment]::GetFolderPath('Desktop'); $s=$w.CreateShortcut($d+'\Outside Resolve.lnk'); $s.TargetPath='%EXE%'; $s.WorkingDirectory='%APPDIR%'; $s.IconLocation='%EXE%'; $s.Save()"

echo Enabling start with Windows ^(in the tray^)...
reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v OutsideResolve /t REG_SZ /d "\"%EXE%\" --minimized" /f >nul

echo.
echo Installed. Starting Outside Resolve...
start "" "%EXE%"
goto :done
:err
echo.
echo Install FAILED. Make sure Python is installed for the build step ^(py --version^).
:done
if /i not "%~1"=="/quiet" pause
