@echo off
REM ===========================================================================
REM  LapStudio - build the exe and package a release zip in one step.
REM
REM  What it does:
REM    1. builds dist\LapStudio.exe with PyInstaller (same options as build.bat)
REM    2. checks whether ffmpeg.exe is a STATIC build, because only a static one
REM       works when bundled - a shared build needs ~250 MB of av*.dll beside it
REM    3. zips the result into  dist\LapStudio <version>.zip
REM
REM  Usage:   make_release.bat 1.2.0
REM           (if you leave the version off it uses the date)
REM ===========================================================================

setlocal EnableDelayedExpansion
cd /d "%~dp0"

set VER=%~1
if "%VER%"=="" (
    for /f %%d in ('powershell -NoProfile -Command "Get-Date -Format yyyy.MM.dd"') do set VER=%%d
)

echo.
echo ============================================
echo   LapStudio release %VER%
echo ============================================
echo.

REM --- 1. Is ffmpeg.exe static or shared? ------------------------------------
set FFMPEG_ARG=
set FFMPEG_NOTE=none
if exist "ffmpeg.exe" (
    powershell -NoProfile -Command ^
      "$b=[IO.File]::ReadAllBytes('ffmpeg.exe');" ^
      "$s=[Text.Encoding]::ASCII.GetString($b);" ^
      "if($s -match 'avcodec-\d+\.dll'){exit 1}else{exit 0}"
    if errorlevel 1 (
        echo   ffmpeg.exe is a SHARED build - it needs its av*.dll files.
        echo.
        echo   Bundling it into the exe will NOT work: the bundled copy cannot
        echo   find the DLLs and video export will fail on other machines.
        echo.
        echo   Either:
        echo     - download a STATIC ffmpeg build ^(one self-contained .exe^)
        echo       from https://www.gyan.dev/ffmpeg/builds/ ^(choose "essentials"^)
        echo       and replace ffmpeg.exe with it, or
        echo     - press a key to continue and ship WITHOUT ffmpeg, telling users
        echo       to install it themselves.
        echo.
        pause
        set FFMPEG_NOTE=omitted ^(shared build^)
    ) else (
        echo   ffmpeg.exe is a STATIC build - bundling it into the exe.
        set FFMPEG_ARG=--add-data "ffmpeg.exe;."
        set FFMPEG_NOTE=bundled
    )
) else (
    echo   No ffmpeg.exe here - the exe will look for ffmpeg on the PATH.
)

REM --- 2. Build --------------------------------------------------------------
python -m pip show pyinstaller >nul 2>&1
if errorlevel 1 python -m pip install pyinstaller

echo.
echo Building dist\LapStudio.exe ...
echo.
python -m PyInstaller ^
  --onefile ^
  --windowed ^
  --noconfirm ^
  --name LapStudio ^
  --add-data "BigShoulders-Bold.ttf;." ^
  --add-data "Poppins-Bold.ttf;." ^
  %FFMPEG_ARG% ^
  --collect-all aggdraw ^
  ecu_overlay_app.py

if errorlevel 1 (
    echo.
    echo BUILD FAILED - see above.
    pause
    exit /b 1
)

REM --- 3. Package ------------------------------------------------------------
set OUT=dist\LapStudio %VER%.zip
if exist "%OUT%" del "%OUT%"

echo.
echo Packaging "%OUT%" ...

set FILES='dist\LapStudio.exe','SETUP.md'
if "%FFMPEG_NOTE%"=="omitted (shared build)" set FILES=!FILES!,'ffmpeg.exe'

powershell -NoProfile -Command ^
  "Compress-Archive -Path %FILES% -DestinationPath '%OUT%' -Force"

if errorlevel 1 (
    echo.
    echo PACKAGING FAILED.
    pause
    exit /b 1
)

echo.
echo ============================================
echo   DONE
echo   %OUT%
echo   ffmpeg: %FFMPEG_NOTE%
echo ============================================
echo.
echo Test it before sending: copy the zip to a folder with nothing else in it,
echo unzip, run LapStudio.exe, load a log and export a short clip.
echo.
pause
endlocal
