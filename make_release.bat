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
REM  Usage:   make_release.bat 1.2.0            -> folder build (recommended)
REM           make_release.bat 1.2.0 onefile    -> single .exe
REM           (if you leave the version off it uses the date)
REM
REM  Why a folder by default: a --onefile exe unpacks ~250 MB to %TEMP% on every
REM  launch and runs code from there, which is the behaviour Windows Defender
REM  flags as Win32/Wacapew.C!ml. A folder build does none of that, is far less
REM  likely to be flagged, and starts much faster.
REM ===========================================================================

setlocal EnableDelayedExpansion
cd /d "%~dp0"

REM  Every stop-and-wait goes through %HOLD% so the run can be logged to a file:
REM      make_release.bat 1.2.0 > build.log 2>&1
REM  with LAPSTUDIO_NOPAUSE=1 set, which is the only way to capture a failure you
REM  cannot screenshot. A bare `pause` waits on a prompt you cannot see.
set HOLD=pause
if defined LAPSTUDIO_NOPAUSE set HOLD=rem

set VER=%~1
if "%VER%"=="" (
    for /f %%d in ('powershell -NoProfile -Command "Get-Date -Format yyyy.MM.dd"') do set VER=%%d
)

set MODE=--onedir
set MODENAME=folder
if /i "%~2"=="onefile" (
    set MODE=--onefile
    set MODENAME=single exe
    echo   NOTE: onefile builds are the ones Defender flags. Use the folder build
    echo         unless you have a specific reason not to.
)

echo.
echo ============================================
echo   LapStudio release %VER%
echo ============================================
echo.

REM --- 1. ffmpeg: bundle it, with its DLLs if it is a shared build -----------
REM  A shared ffmpeg.exe is only ~545 KB but needs seven av*/sw* DLLs. Those are
REM  bundled explicitly here rather than relying on PyInstaller's dependency scan
REM  to notice them. (The v1.1.0 release worked because the scan did catch them;
REM  do not depend on that.) A static ffmpeg needs none of this and makes a
REM  smaller exe - see the note at the end of this file.
set FFMPEG_ARG=
set FFMPEG_NOTE=none - users must install ffmpeg themselves
if exist "ffmpeg.exe" (
    powershell -NoProfile -Command ^
      "$s=[Text.Encoding]::ASCII.GetString([IO.File]::ReadAllBytes('ffmpeg.exe'));" ^
      "if($s -match 'avcodec-\d+\.dll'){exit 1}else{exit 0}"
    if errorlevel 1 (
        echo   ffmpeg.exe is a SHARED build - looking for its DLLs...
        set MISSING=
        for %%D in (avcodec-62.dll avformat-62.dll avfilter-11.dll avutil-60.dll ^
                    swscale-9.dll swresample-6.dll avdevice-62.dll) do (
            if exist "%%D" (
                set FFMPEG_ARG=!FFMPEG_ARG! --add-binary "%%D;."
            ) else (
                set MISSING=!MISSING! %%D
            )
        )
        if "!MISSING!"=="" (
            set FFMPEG_ARG=!FFMPEG_ARG! --add-data "ffmpeg.exe;."
            set FFMPEG_NOTE=bundled ^(shared + 7 DLLs, adds about 86 MB^)
            echo   all seven found - ffmpeg will be bundled.
        ) else (
            echo.
            echo   MISSING:!MISSING!
            echo.
            echo   Without these the bundled ffmpeg cannot start and video export
            echo   will fail on any machine that has no ffmpeg of its own.
            echo   Put them beside ffmpeg.exe, or swap in a STATIC ffmpeg build.
            echo.
            %HOLD%
            set FFMPEG_NOTE=OMITTED - DLLs missing
        )
    ) else (
        echo   ffmpeg.exe is a STATIC build - bundling it.
        set FFMPEG_ARG=--add-data "ffmpeg.exe;."
        set FFMPEG_NOTE=bundled ^(static^)
    )
) else (
    echo   No ffmpeg.exe here - the exe will look for ffmpeg on the PATH.
)

REM --- 1b. AiM reader: the DLL, its dependencies and the VC++ 2008 runtime ---
REM  Without these, .drk / .xrk files cannot be opened. CSV and VBO do not need
REM  them. The reader runs in a second process; when frozen the exe re-runs
REM  itself with --xrk-helper (see the top of ecu_overlay_app.py), so the helper
REM  module must be in the bundle too.
set AIM_ARG=
set AIM_NOTE=not bundled - AiM .drk/.xrk files will not open
set AIM_MISSING=
for %%D in (MatLabXRK-2022-64-ReleaseU.dll libxml2-2.dll libiconv-2.dll ^
            libz.dll pthreadVC2_x64.dll msvcr90.dll) do (
    if exist "%%D" (
        set AIM_ARG=!AIM_ARG! --add-binary "%%D;."
    ) else (
        set AIM_MISSING=!AIM_MISSING! %%D
    )
)
if "!AIM_MISSING!"=="" (
    echo   AiM reader: all six files found - bundling.
    set AIM_NOTE=bundled
) else (
    echo.
    echo   AiM reader files missing:!AIM_MISSING!
    echo   Copy them from your RaceStudio 3 folder if you want .drk support in
    echo   this release. Building without them - CSV and VBO still work.
    echo.
    set AIM_ARG=
    %HOLD%
)

REM --- 1c. Universal C Runtime, for Windows 8.1 and 7 ------------------------
REM  Windows 10 and 11 include the UCRT (api-ms-win-crt-*.dll, ucrtbase.dll).
REM  Windows 8.1 and 7 do NOT unless update KB2999226 was installed, and without
REM  it the app dies at launch with "api-ms-win-crt-stdio-l1-1-0.dll is missing".
REM  Python, ffmpeg and Pillow all link against it.
REM
REM  Microsoft permits shipping these app-local, from the Windows SDK's Redist
REM  folder. If the SDK is installed here they are bundled; if not, the release
REM  still works on Windows 10/11 and SETUP.md tells older machines what to do.
set UCRT_ARG=
set UCRT_NOTE=not bundled - Windows 8.1/7 users need KB2999226
set UCRT_DIR=
set "KITS=%ProgramFiles(x86)%\Windows Kits\10\Redist"
if not exist "%KITS%" set "KITS=%ProgramFiles%\Windows Kits\10\Redist"
for /d %%K in ("%KITS%\*") do (
    if exist "%%~K\ucrt\DLLs\x64\ucrtbase.dll" set "UCRT_DIR=%%~K\ucrt\DLLs\x64"
)
if not defined UCRT_DIR if exist "%KITS%\ucrt\DLLs\x64\ucrtbase.dll" set "UCRT_DIR=%KITS%\ucrt\DLLs\x64"
if defined UCRT_DIR (
    echo   UCRT found: !UCRT_DIR!
    set UCRT_ARG=--add-binary "!UCRT_DIR!\*.dll;."
    set UCRT_NOTE=bundled ^(runs on Windows 8.1 and 7 too^)
) else (
    echo   UCRT redistributable not found - the build will need Windows 10 or 11,
    echo   or KB2999226 on older machines. Install the Windows SDK to bundle it.
)

REM --- 1d. Which Windows will this build run on? -----------------------------
REM  Decided by the Python you build with, not by PyInstaller. Per PEP 11 a
REM  Python release supports a Windows version only while Microsoft does:
REM     Python 3.13+  ->  Windows 10 and newer
REM     Python 3.12   ->  Windows 8.1 and newer
REM     Python 3.8    ->  Windows 7 and newer
REM  Building with 3.14 and running on Windows 8 gives
REM  "Failed to load Python DLL python314.dll".
for /f "tokens=2" %%v in ('python -V 2^>^&1') do set PYVER=%%v
for /f "tokens=1,2 delims=." %%a in ("!PYVER!") do (
    set PYMAJ=%%a
    set PYMIN=%%b
)
set MINWIN=Windows 10 or newer
if !PYMAJ! EQU 3 if !PYMIN! LEQ 12 set MINWIN=Windows 8.1 or newer
if !PYMAJ! EQU 3 if !PYMIN! LEQ 8  set MINWIN=Windows 7 or newer
echo   Python !PYVER!  -^>  this build will require !MINWIN!

REM --- 2. Build --------------------------------------------------------------
python -m pip show pyinstaller >nul 2>&1
if errorlevel 1 python -m pip install pyinstaller

echo.
echo Building dist\LapStudio.exe ...
echo.
REM  Assets: the two display fonts, and the backing images the HUD dashes draw
REM  on (Dash 7 and Dash 9 load these at render time - without them those two
REM  dashes lose their backing plate).
REM  Modules: every renderer, both log readers, and the AiM helper. Most are
REM  found by the import scan, but they are pinned so a refactor cannot quietly
REM  drop one from the bundle.
REM  aim_reader is an optional import that does not exist; excluding it keeps the
REM  build log clean.
REM  --noupx: UPX-compressed binaries are heavily flagged by antivirus, and
REM  PyInstaller will use UPX automatically if it finds it on the PATH.
REM  --version-file: gives the exe a publisher and product name instead of no
REM  metadata at all, which anonymous binaries are penalised for.
python -m PyInstaller ^
  %MODE% ^
  --windowed ^
  --noconfirm ^
  --noupx ^
  --version-file version_info.txt ^
  --name LapStudio ^
  --add-data "BigShoulders-Bold.ttf;." ^
  --add-data "Poppins-Bold.ttf;." ^
  --add-data "dash_hud_bg.png;." ^
  --add-data "dash_hud2_bg.png;." ^
  --add-data "xrk_helper.py;." ^
  --hidden-import xrk_helper ^
  --hidden-import xrk_reader ^
  --hidden-import vbo_reader ^
  --hidden-import motec_reader ^
  --hidden-import gsensor ^
  --hidden-import renderer_pil ^
  --hidden-import renderer_multistyle ^
  --hidden-import lapdata_render ^
  --hidden-import gtrace_render ^
  --hidden-import trackmap_render ^
  --hidden-import dash8_render ^
  --hidden-import textgrid ^
  --exclude-module aim_reader ^
  --exclude-module matplotlib ^
  %FFMPEG_ARG% ^
  %AIM_ARG% ^
  %UCRT_ARG% ^
  --collect-all aggdraw ^
  ecu_overlay_app.py

if errorlevel 1 (
    echo.
    echo BUILD FAILED - see above.
    %HOLD%
    exit /b 1
)

REM --- 2b. Code signing ------------------------------------------------------
REM  Signing is the only permanent cure for the antivirus false positives, and
REM  once a certificate exists every release should be signed. It is skippable
REM  only because you cannot sign without one - set LAPSTUDIO_REQUIRE_SIGNING=1
REM  and an unsigned build becomes a hard failure instead of a warning.
REM
REM  Pick ONE of these (subject or thumbprint for a token / cloud certificate,
REM  which is what new OV certificates use - since June 2023 their private keys
REM  must live on FIPS 140-2 hardware, so a .pfx is no longer issued):
REM     LAPSTUDIO_CERT_SUBJECT   e.g. "OpticalNZ"        cert in the Windows store
REM     LAPSTUDIO_CERT_SHA1      certificate thumbprint, no spaces
REM     LAPSTUDIO_CERT + LAPSTUDIO_CERTPW                path to a .pfx and its password
REM
REM  A self-signed certificate is NOT worth doing: SmartScreen and Defender
REM  ignore it and the user still sees "unknown publisher".
set SIGN_NOTE=UNSIGNED - expect antivirus warnings on first run
set SIGNED_OK=
set SIGN_ARGS=
if defined LAPSTUDIO_CERT_SUBJECT set SIGN_ARGS=/n "%LAPSTUDIO_CERT_SUBJECT%"
if defined LAPSTUDIO_CERT_SHA1    set SIGN_ARGS=/sha1 %LAPSTUDIO_CERT_SHA1%
if defined LAPSTUDIO_CERT         set SIGN_ARGS=/f "%LAPSTUDIO_CERT%" /p "%LAPSTUDIO_CERTPW%"

if /i "%MODENAME%"=="folder" (
    set TOSIGN=dist\LapStudio\LapStudio.exe
) else (
    set TOSIGN=dist\LapStudio.exe
)

if defined SIGN_ARGS (
    where signtool >nul 2>&1
    if errorlevel 1 (
        echo   signtool not found - install the Windows SDK.
    ) else (
        echo.
        echo Signing !TOSIGN! ...
        REM  /tr timestamps the signature so it stays valid after the certificate
        REM  expires; without it the software "expires" when the cert does.
        signtool sign %SIGN_ARGS% ^
                 /tr http://timestamp.digicert.com /td sha256 /fd sha256 ^
                 /d "LapStudio" "!TOSIGN!"
        if errorlevel 1 (
            echo   SIGNING FAILED.
        ) else (
            signtool verify /pa /q "!TOSIGN!"
            if errorlevel 1 (
                echo   signed, but verification failed - check the chain.
            ) else (
                set SIGN_NOTE=signed and verified
                set SIGNED_OK=1
            )
        )
    )
) else (
    echo   No signing certificate configured - see the notes in this file.
)

if defined LAPSTUDIO_REQUIRE_SIGNING (
    if not defined SIGNED_OK (
        echo.
        echo ============================================
        echo   ABORTING: LAPSTUDIO_REQUIRE_SIGNING is set but the build is
        echo   not signed. Refusing to package an unsigned release.
        echo ============================================
        %HOLD%
        exit /b 1
    )
)

REM --- 3. Package ------------------------------------------------------------
set OUT=dist\LapStudio %VER%.zip
if exist "%OUT%" del "%OUT%"

echo.
echo Packaging "%OUT%" ...

REM  A folder build produces dist\LapStudio\ ; a onefile build a single exe.
REM  Either way the setup notes travel with it. A loose shared ffmpeg.exe is
REM  useless without its DLLs, so it is never shipped on its own.
if /i "%MODENAME%"=="folder" (
    copy /y "SETUP.md" "dist\LapStudio\SETUP.md" >nul
    powershell -NoProfile -Command ^
      "Compress-Archive -Path 'dist\LapStudio' -DestinationPath '%OUT%' -Force"
) else (
    powershell -NoProfile -Command ^
      "Compress-Archive -Path 'dist\LapStudio.exe','SETUP.md' -DestinationPath '%OUT%' -Force"
)

if errorlevel 1 (
    echo.
    echo PACKAGING FAILED.
    %HOLD%
    exit /b 1
)

echo.
echo ============================================
echo   DONE
echo   %OUT%
echo   build:      %MODENAME%
echo   signing:    !SIGN_NOTE!
echo   UCRT:       %UCRT_NOTE%
echo   runs on:    %MINWIN%  ^(built with Python %PYVER%^)
echo   ffmpeg:     %FFMPEG_NOTE%
echo   AiM reader: %AIM_NOTE%
echo ============================================
echo.
if not defined LAPSTUDIO_REQUIRE_SIGNING (
    if not defined SIGNED_OK (
        echo   ^>^> This release is UNSIGNED. Windows will warn whoever runs it.
        echo   ^>^> Signing is the only permanent fix; see section 2b in this file.
        echo.
    )
)
echo Test it before sending: copy the zip to a folder with nothing else in it,
echo unzip, run LapStudio.exe, then
echo    - load a CSV and export a short clip   ^(checks ffmpeg^)
echo    - load a .drk and export a short clip  ^(checks the AiM reader^)
echo Ideally on a machine that has never had ffmpeg or RaceStudio installed.
echo.
echo If Windows Defender flags the result as Win32/Wacapew.C^!ml, that is a false
echo positive: the ^!ml suffix means a machine-learning guess, not a signature
echo match. Report it once at
echo   https://www.microsoft.com/en-us/wdsi/filesubmission
echo choosing "Software developer" - they usually clear it within a few days.
echo A code-signing certificate is the only permanent fix.
echo.
echo Size note: bundling the SHARED ffmpeg adds about 86 MB to the exe, because
echo its seven DLLs total 236 MB before compression. A STATIC ffmpeg build is a
echo single file and produces a noticeably smaller release. Get one from
echo   https://www.gyan.dev/ffmpeg/builds/   ^(the "essentials" build^)
echo and replace ffmpeg.exe with it; this script will then bundle just that.
echo.
%HOLD%
endlocal
