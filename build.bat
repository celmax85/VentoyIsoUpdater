@echo off
REM Builds a standalone Windows executable with PyInstaller
REM Usage: build.bat

setlocal

if exist ".venv\Scripts\pip.exe" (
    set PIP=.venv\Scripts\pip.exe
    set PYINSTALLER=.venv\Scripts\pyinstaller.exe
) else (
    set PIP=pip
    set PYINSTALLER=pyinstaller
)

echo =^> Installing dependencies...
%PIP% install -q -r requirements.txt pyinstaller
if errorlevel 1 (
    echo ERROR: dependency installation failed.
    pause & exit /b 1
)

REM Generates icon.ico if missing
if not exist "assets\icon.ico" (
    echo =^> Generating assets\icon.ico...
    python tools\generate_ico.py
)

REM Generates the --hidden-import flags for every module in sources/
set HIDDEN=
for %%f in (sources\*.py) do (
    if not "%%~nf"=="__init__" (
        set HIDDEN=%HIDDEN% --hidden-import sources.%%~nf
    )
)

if not exist "dist\windows" mkdir "dist\windows"

echo =^> Building the Windows executable...
%PYINSTALLER% ^
    --onefile ^
    --windowed ^
    --name "VentoyIsoUpdater" ^
    --icon "assets\icon.ico" ^
    --add-data "data\distros.json;data" ^
    --add-data "assets;assets" ^
    --hidden-import customtkinter ^
    --hidden-import PIL ^
    --hidden-import PIL._tkinter_finder ^
    --hidden-import packaging ^
    --hidden-import packaging.version ^
    --collect-all customtkinter ^
    --distpath "dist\windows" ^
    --workpath "build" ^
    %HIDDEN% ^
    main.py

if errorlevel 1 (
    echo ERROR: build failed.
    pause & exit /b 1
)

echo.
echo =^> Executable generated: dist\windows\VentoyIsoUpdater.exe
echo.
echo     Available packages:
echo     package_zip_win.bat          -^> dist\windows\*-portable.zip
echo     package_installer_win.bat    -^> dist\windows\*-setup.exe
pause
