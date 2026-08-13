@echo off
REM ===========================================================================
REM  LapStudio - this script is kept only so an old shortcut still works.
REM
REM  It used to build the exe itself, with a much shorter PyInstaller command
REM  than the one the app now needs. Running that command today produces a
REM  build that LOOKS fine and is broken in ways you would not notice until a
REM  user hit them:
REM
REM     - no dash_hud_bg.png / dash_hud2_bg.png, so Dash 7 and Dash 9 lose
REM       their backing plates
REM     - no xrk_helper.py, so AiM .drk and .xrk files cannot be opened at all
REM     - no motec_reader or gsensor, so MoTeC .ld files and sensor levelling
REM       are missing
REM     - no AiM DLLs, no Universal C Runtime, no version metadata
REM     - --onefile, which is the shape Windows Defender flags as Wacapew
REM
REM  So it now just calls make_release.bat, which is the one that is kept up to
REM  date and checked by tests/check_release_inputs.py.
REM ===========================================================================

echo.
echo build.bat now hands over to make_release.bat, which bundles everything
echo the app needs. See the notes at the top of this file for why.
echo.

call "%~dp0make_release.bat" %*
