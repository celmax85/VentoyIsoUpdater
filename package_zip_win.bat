@echo off
REM Creates a portable Windows ZIP — extract and run VentoyIsoUpdater.exe, no install needed.
REM Usage: package_zip_win.bat

setlocal

set APP_NAME=VentoyIsoUpdater
REM Defaults to the version in pyproject.toml (single source of truth)
if not defined VERSION for /f "tokens=2 delims== " %%v in ('findstr /b /c:"version" pyproject.toml') do set VERSION=%%~v
REM overridable: set VERSION=1.2.3 ^&^& package_zip_win.bat
set BINARY=dist\windows\%APP_NAME%.exe
set OUTPUT=dist\windows\%APP_NAME%-%VERSION%-windows-portable.zip
set WORK_DIR=build\portable_%APP_NAME%

if not exist "%BINARY%" (
    echo ERROR: %BINARY% not found. Run build.bat first.
    pause & exit /b 1
)

echo =^> Preparing the portable folder...

if exist "%WORK_DIR%" rmdir /s /q "%WORK_DIR%"
mkdir "%WORK_DIR%"

copy "%BINARY%" "%WORK_DIR%\%APP_NAME%.exe" >nul

(
    echo @echo off
    echo start "" "%~dp0%APP_NAME%.exe"
) > "%WORK_DIR%\Launch %APP_NAME%.bat"

(
    echo VentoyIsoUpdater %VERSION% — Windows portable version
    echo ==================================================
    echo.
    echo Run VentoyIsoUpdater.exe directly.
    echo No installation required.
    echo.
    echo Your preferences are saved in:
    echo   %%APPDATA%%\ventoyisoupdater\
    echo.
    echo https://github.com/celmax85
) > "%WORK_DIR%\README.txt"

echo =^> Creating the ZIP...
powershell -NoProfile -Command ^
    "Compress-Archive -Path '%WORK_DIR%\*' -DestinationPath '%OUTPUT%' -Force"

if errorlevel 1 (
    echo ERROR: ZIP creation failed.
    pause & exit /b 1
)

rmdir /s /q "%WORK_DIR%"

echo.
echo =^> Portable ZIP generated: %OUTPUT%
pause
