@echo off
REM Collect Shazam (and, with a key in .env, YouTube) stats for PERSONAL ANIMOSITY
REM (Apple artist id 1720235535). The artist has two "PERSONAL ANIMOSITY - Topic"
REM channels; passing them skips the 100-unit channel search. The channel flags
REM are ignored when YouTube is off.
REM Extra arguments are passed through, e.g. personal-animosity.bat --json
setlocal
cd /d "%~dp0"

REM Keep the Cyrillic album names readable in the console.
set PYTHONIOENCODING=utf-8

python shazam_stats.py --artist-id 1720235535 ^
    --youtube-topic-channel UCW0XqjvAVf_zimSmZ9MzTaA ^
    --youtube-topic-channel UCExcanjCPOj2O8CKzHas8Fg ^
    %*
exit /b %ERRORLEVEL%
