@echo off
setlocal

REM ====================================================
REM D-HCIS Payroll Start Menu shortcut installer for all users
REM ====================================================

set APP_EXE=D:\HCIS\hcis_desktop_x64.exe
set APP_DIR=D:\HCIS
set STARTMENU=C:\ProgramData\Microsoft\Windows\Start Menu\Programs\D-HCIS Payroll

if not exist "%APP_DIR%" (
    echo Folder aplikasi tidak ditemukan: %APP_DIR%
    echo Pastikan file exe dan config_client.json sudah ada di D:\HCIS
    pause
    exit /b 1
)

if not exist "%APP_EXE%" (
    echo File exe tidak ditemukan: %APP_EXE%
    echo Pastikan nama file exe sesuai dengan yang ada di D:\HCIS
    pause
    exit /b 1
)

mkdir "%STARTMENU%" 2>nul

powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%STARTMENU%\\D-HCIS Payroll.lnk'); $s.TargetPath='%APP_EXE%'; $s.WorkingDirectory='%APP_DIR%'; $s.IconLocation='%APP_EXE%,0'; $s.Save()"

echo.
echo Shortcut berhasil dibuat untuk semua user.
echo Nama shortcut: D-HCIS Payroll
echo Lokasi shortcut: %STARTMENU%

echo.
pause
