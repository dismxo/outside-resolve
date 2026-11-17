@echo off
REM Remove OutsideResolve for the current user (keeps your config in %APPDATA%).
setlocal
set "APPDIR=%LOCALAPPDATA%\Programs\OutsideResolve"
taskkill /F /IM OutsideResolve.exe >nul 2>&1
reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v OutsideResolve /f >nul 2>&1
del "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Outside Resolve.lnk" >nul 2>&1
del "%USERPROFILE%\Desktop\Outside Resolve.lnk" >nul 2>&1
rmdir /S /Q "%APPDIR%" >nul 2>&1
echo Uninstalled. Your settings in "%APPDATA%\OutsideResolve" were kept.
echo (Delete that folder too if you want a clean wipe.)
pause
