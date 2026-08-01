@echo off
REM ===========================================================================
REM  LapStudio - build a standalone Windows .exe with PyInstaller
REM  Just double-click this file (or run it from a command prompt) in the
REM  project folder that contains ecu_overlay_app.py and the .ttf fonts.
REM ===========================================================================

setlocal

echo.
echo ============================================
echo   LapStudio - building Windows executable
echo ============================================
echo.

REM --- Make sure PyInstaller is installed -----------------------------------
python -m pip show pyinstaller >nul 2>&1
if errorlevel 1 (
    echo PyInstaller not found - installing it now...
    python -m pip install pyinstaller
    if errorlevel 1 (
        echo.
        echo ERROR: could not install PyInstaller. Is Python on your PATH?
        pause
        exit /b 1
    )
)

REM --- Optional: include ffmpeg.exe in the bundle if it sits next to this bat -
set FFMPEG_ARG=
if exist "ffmpeg.exe" (
    echo Found ffmpeg.exe - it will be bundled into the exe.
    set FFMPEG_ARG=--add-data "ffmpeg.exe;."
) else (
    echo No ffmpeg.exe found next to this script.
    echo   ^(That's fine if ffmpeg is already on your system PATH.^)
)

echo.
echo Building LapStudio.exe ... this can take a minute or two.
echo.

REM --- The build command ----------------------------------------------------
REM   --onefile     one single .exe
REM   --windowed    no console window behind the GUI
REM   --name        output is named LapStudio.exe
REM   --add-data    bundle the fonts at the bundle root (where the code looks)
REM   --collect-all aggdraw  force-include aggdraw's binary
python -m PyInstaller ^
  --onefile ^
  --windowed ^
  --name LapStudio ^
  --add-data "BigShoulders-Bold.ttf;." ^
  --add-data "Poppins-Bold.ttf;." ^
  %FFMPEG_ARG% ^
  --collect-all aggdraw ^
  ecu_overlay_app.py

if errorlevel 1 (
    echo.
    echo ============================================
    echo   BUILD FAILED - see the messages above.
    echo ============================================
    echo If you see "module not found", add a line like:
    echo     --hidden-import ^<module^> ^
    echo to the command above and run again.
    pause
    exit /b 1
)

echo.
echo ============================================
echo   BUILD COMPLETE
echo   Your program is at:  dist\LapStudio.exe
echo ============================================
echo.
echo Tip: if the app won't start, rebuild WITHOUT --windowed
echo      so a console stays open and shows the error.
echo.
pause
endlocal
