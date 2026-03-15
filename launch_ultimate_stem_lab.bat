@echo off
setlocal
cd /d "%~dp0"
title Ultimate Stem Lab - Launch

set "VENV_PY=.\ultimate_stem_lab\.venv\Scripts\python.exe"
if not exist "%VENV_PY%" (
  echo Ultimate Stem Lab is not installed yet.
  echo Run install_ultimate_stem_lab.bat first.
  echo.
  pause
  exit /b 1
)

echo ========================================
echo Ultimate Stem Lab - Launch
echo ========================================
echo Starting local launcher...
echo Browser URL: http://127.0.0.1:8765
echo Leave this window open while the launcher is running.
echo Press Ctrl+C in this window if you want to stop the launcher.
echo.
"%VENV_PY%" .\stem_lab_launcher.py
set "ERR=%ERRORLEVEL%"
if not "%ERR%"=="0" (
  echo.
  echo Launcher exited with code %ERR%.
  pause
)
exit /b %ERR%
