@echo off
REM Compiles setup.iss with Inno Setup to produce a Windows .exe installer.
REM Requires: Inno Setup 6+ (https://jrsoftware.org/isinfo.php)
REM Usage: package_installer_win.bat

setlocal

set BINARY=dist\windows\VentoyIsoUpdater.exe
set ICON=assets\icon.ico

if not exist "%BINARY%" (
    echo ERROR: %BINARY% not found. Run build.bat first.
    pause & exit /b 1
)

if not exist "%ICON%" (
    echo ERROR: %ICON% not found.
    echo Generate it with: python tools\generate_ico.py
    pause & exit /b 1
)

set INSTALL_OFFERED=
:find_iscc
set ISCC=""
if exist "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" (
    set ISCC="C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
) else if exist "C:\Program Files\Inno Setup 6\ISCC.exe" (
    set ISCC="C:\Program Files\Inno Setup 6\ISCC.exe"
) else if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" (
    set ISCC="%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
) else (
    where iscc >nul 2>&1 && set ISCC=iscc
)
if not %ISCC%=="" goto iscc_found

echo Inno Setup 6 is required to build the installer, and was not found.
if defined INSTALL_OFFERED goto iscc_missing
set INSTALL_OFFERED=1
where winget >nul 2>&1 || goto iscc_missing
choice /c YN /m "Install it now with winget"
if errorlevel 2 goto iscc_missing
winget install --id JRSoftware.InnoSetup -e --accept-package-agreements --accept-source-agreements
goto find_iscc

:iscc_missing
echo ERROR: Inno Setup not found.
echo Download: https://jrsoftware.org/isdl.php
pause & exit /b 1

:iscc_found

REM Defaults to the version in pyproject.toml (single source of truth)
if not defined VERSION for /f "tokens=2 delims== " %%v in ('findstr /b /c:"version" pyproject.toml') do set VERSION=%%~v
REM overridable: set VERSION=1.2.3 ^&^& package_installer_win.bat

echo =^> Compiling the installer...
%ISCC% /DAppVersion=%VERSION% setup.iss

if errorlevel 1 (
    echo ERROR: compilation failed.
    pause & exit /b 1
)

echo.
echo =^> Installer generated: dist\windows\VentoyIsoUpdater-%VERSION%-windows-setup.exe
pause
