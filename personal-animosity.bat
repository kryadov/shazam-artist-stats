@echo off
REM Collect Shazam stats for PERSONAL ANIMOSITY (Apple artist id 1720235535).
REM Extra arguments are passed through, e.g. personal-animosity.bat --json
setlocal
cd /d "%~dp0"

REM Keep the Cyrillic album names readable in the console.
set PYTHONIOENCODING=utf-8

python shazam_stats.py --artist-id 1720235535 %*
exit /b %ERRORLEVEL%
