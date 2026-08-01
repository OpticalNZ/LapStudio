@echo off
REM ===========================================================================
REM  LapStudio - run the app directly with Python (no build / no exe)
REM  Double-click this file in the project folder that contains
REM  ecu_overlay_app.py and the .ttf fonts.
REM ===========================================================================

setlocal
cd /d "%~dp0"

REM --- Find a working Python launcher ---------------------------------------
set PY=
where py >nul 2>&1 && set PY=py
if not defined PY (
    where python >nul 2>&1 && set PY=python
)
if not defined PY (
    echo.
    echo ERROR: Python was not found on your PATH.
    echo   Install Python from https://www.python.org/downloads/
    echo   and tick "Add Python to PATH" during setup.
    echo.
    pause
    exit /b 1
)

REM --- First run: make sure the dependencies are installed ------------------
REM (Quick check for Pillow; if missing, install everything from requirements.)
%PY% -c "import PIL, numpy, pandas, aggdraw" >nul 2>&1
if errorlevel 1 (
    echo Installing required packages ^(first run only^)...
    if exist "requirements.txt" (
        %PY% -m pip install -r requirements.txt
    ) else (
        %PY% -m pip install pillow numpy pandas aggdraw
    )
    if errorlevel 1 (
        echo.
        echo ERROR: could not install the required packages.
        pause
        exit /b 1
    )
)

echo.
echo Starting LapStudio...
echo.

REM --- Launch the app -------------------------------------------------------
%PY% ecu_overlay_app.py

REM If the app exits with an error, keep the window open so it can be read.
if errorlevel 1 (
    echo.
    echo ============================================
    echo   The program exited with an error ^(above^).
    echo ============================================
    pause
)

endlocal
