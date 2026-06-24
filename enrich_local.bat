@echo off
REM Double-click to enrich up to 100 leads locally.
REM For more, run from a terminal:  enrich_local.bat 250   (or)   enrich_local.bat all
python enrich_local.py %*
echo.
pause
