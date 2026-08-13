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

where git >nul 2>&1
if errorlevel 1 (
    echo.
    echo Git is not installed, or not on the PATH.
    echo Get it from https://git-scm.com/download/win and run this again.
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
git remote get-url origin
echo.

REM  Anything edited but not committed would NOT be pushed, which is the most
REM  confusing way for this to go wrong - it looks like it worked and the change
REM  is still only on this machine.
git diff-index --quiet HEAD --
if errorlevel 1 (
    echo ^>^> You have changes that are saved on disk but not committed.
    echo ^>^> These will NOT be pushed:
    echo.
    git status --short
    echo.
    echo    To include them, commit them first:
    echo        git add -A
    echo        git commit -m "what you changed"
    echo.
)

echo Commits waiting to go up:
echo.
git log --oneline origin/main..HEAD
echo.

git log --oneline origin/main..HEAD | findstr /r "." >nul
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
git push origin main
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
