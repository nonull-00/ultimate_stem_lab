@echo off
setlocal
cd /d "%~dp0"
title Ultimate Stem Lab - Install

echo ========================================
echo Ultimate Stem Lab - Install
echo ========================================
echo.

set "PY_CMD="
where py >nul 2>nul
if not errorlevel 1 set "PY_CMD=py"
if not defined PY_CMD (
  where python >nul 2>nul
  if not errorlevel 1 set "PY_CMD=python"
)

if not defined PY_CMD (
  echo Python was not found on PATH.
  echo Install Python 3 first, then run this file again.
  echo.
  pause
  exit /b 1
)

echo Using Python launcher: %PY_CMD%
echo.
%PY_CMD% .\bootstrap_ultimate_stem_lab.py
if errorlevel 1 (
  echo.
  echo Install failed.
  echo Review the messages above, then try again.
  echo.
  pause
  exit /b 1
)

echo.
echo Install complete.
echo Next step: double-click launch_ultimate_stem_lab.bat
pause
exit /b 0
