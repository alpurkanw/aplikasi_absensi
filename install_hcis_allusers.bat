@echo off
setlocal

REM ====================================================
REM Install HCIS Desktop App to D:\HCIS and add Start Menu shortcut for all users
REM ====================================================

set APP_ROOT=D:\HCIS
set APP_EXE=%APP_ROOT%\hcis_desktop_x64.exe
set APP_CONFIG=%APP_ROOT%\config_client.json
set STARTMENU=C:\ProgramData\Microsoft\Windows\Start Menu\Programs\D-HCIS Payroll

if not exist "%APP_ROOT%" (
    mkdir "%APP_ROOT%" >nul 2>&1
)

REM Copy the application files into the target folder.
copy /Y "D:\my doc\HCIS\build_windows\x64\hcis_desktop_x64.exe" "%APP_EXE%" >nul 2>&1
if exist "D:\my doc\HCIS\config_client.json" (
    copy /Y "D:\my doc\HCIS\config_client.json" "%APP_CONFIG%" >nul 2>&1
)

if not exist "%APP_EXE%" (
    echo File aplikasi tidak ditemukan: %APP_EXE%
    echo Pastikan build x64 sudah ada di D:\my doc\HCIS\build_windows\x64.
    pause
    exit /b 1
)

mkdir "%STARTMENU%" 2>nul

powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%STARTMENU%\\D-HCIS Payroll.lnk'); $s.TargetPath='%APP_EXE%'; $s.WorkingDirectory='%APP_ROOT%'; $s.IconLocation='%APP_EXE%,0'; $s.Save()"

echo.
echo HCIS berhasil dipasang.
echo Folder aplikasi: %APP_ROOT%
echo Shortcut: %STARTMENU%\D-HCIS Payroll.lnk
echo.
pause
