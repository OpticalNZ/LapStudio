@echo off
REM ===========================================================================
REM  LapStudio - send the source code up to GitHub.
REM
REM  Double-click this. It shows you what is about to go, waits for you to
REM  agree, then pushes.
REM
REM  This sends the SOURCE only. The zip that users download is a separate
REM  thing: build it with make_release.bat and attach it to a Release on the
REM  GitHub website. See RELEASING.md.
REM ===========================================================================

setlocal
cd /d "%~dp0"

REM  Find git. GitHub Desktop ships its own copy but does not put it on the
REM  PATH, so "git is not installed" is often wrong - it is installed, just
REM  somewhere Windows will not look.
set GIT=
where git >nul 2>&1
if not errorlevel 1 set GIT=git

if not defined GIT (
    for %%P in (
        "%ProgramFiles%\Git\cmd\git.exe"
        "%ProgramFiles(x86)%\Git\cmd\git.exe"
        "%LOCALAPPDATA%\Programs\Git\cmd\git.exe"
        "%LOCALAPPDATA%\GitHubDesktop\app-3.4.13\resources\app\git\cmd\git.exe"
    ) do if exist %%P set GIT=%%P
)

if not defined GIT (
    REM  GitHub Desktop's folder has the version in its name, so look for any.
    for /d %%D in ("%LOCALAPPDATA%\GitHubDesktop\app-*") do (
        if exist "%%D\resources\app\git\cmd\git.exe" set GIT="%%D\resources\app\git\cmd\git.exe"
    )
)

if not defined GIT (
    echo.
    echo   Git was not found on this computer.
    echo.
    echo   EASIEST FIX - install GitHub Desktop:
    echo       https://desktop.github.com
    echo   It signs in through your browser, so there is no token to create,
    echo   and it can push with a button. Then use it instead of this file:
    echo   File, Add local repository, choose this folder, Push origin.
    echo.
    echo   OR install command-line Git:
    echo       https://git-scm.com/download/win
    echo   Accept the defaults, then run this file again.
    echo.
    pause
    exit /b 1
)

echo.
echo ============================================
echo   LapStudio - push source to GitHub
echo ============================================
echo.
echo Repository:
%GIT% remote get-url origin
echo.

REM  Anything edited but not committed would NOT be pushed, which is the most
REM  confusing way for this to go wrong - it looks like it worked and the change
REM  is still only on this machine.
%GIT% diff-index --quiet HEAD --
if errorlevel 1 (
    echo ^>^> You have changes that are saved on disk but not committed.
    echo ^>^> These will NOT be pushed:
    echo.
    %GIT% status --short
    echo.
    echo    To include them, commit them first:
    echo        git add -A
    echo        git commit -m "what you changed"
    echo.
)

echo Commits waiting to go up:
echo.
%GIT% log --oneline origin/main..HEAD
echo.

%GIT% log --oneline origin/main..HEAD | findstr /r "." >nul
if errorlevel 1 (
    echo Nothing to push - GitHub already has everything committed here.
    echo.
    pause
    exit /b 0
)

echo ============================================
echo.
choice /c YN /m "Push these to GitHub"
if errorlevel 2 (
    echo.
    echo Cancelled. Nothing was sent.
    echo.
    pause
    exit /b 0
)

echo.
%GIT% push origin main
if errorlevel 1 (
    echo.
    echo ============================================
    echo   PUSH FAILED
    echo ============================================
    echo.
    echo   Asked for a username and password?
    echo     GitHub stopped accepting account passwords. Use a Personal Access
    echo     Token as the password: github.com, Settings, Developer settings,
    echo     Personal access tokens. Give it "repo" access.
    echo     Easier alternative: install GitHub Desktop and push from there.
    echo.
    echo   Said "rejected" or "behind"?
    echo     The copy on GitHub has changes this one does not. Run:
    echo         git pull --rebase origin main
    echo     then try again.
    echo.
    pause
    exit /b 1
)

echo.
echo ============================================
echo   DONE - the source is on GitHub
echo ============================================
echo.
echo   https://github.com/OpticalNZ/LapStudio
echo.
echo   This did NOT publish a download for users. For that:
echo     1. make_release.bat 1.3.0
echo     2. test the zip from dist\
echo     3. attach it to a new Release on the website
echo   RELEASING.md has the detail.
echo.
pause
