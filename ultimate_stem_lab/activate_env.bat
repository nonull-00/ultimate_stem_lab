@echo off
set "ROOT=%~dp0"
set "VENV=%ROOT%.venv"
if not exist "%VENV%\Scripts\activate.bat" (
  echo Virtual environment not found: %VENV%
  exit /b 1
)
call "%VENV%\Scripts\activate.bat"
set "PATH=%ROOT%tools\ffmpeg\bin;%PATH%"
echo Environment activated.
echo Python: %VENV%\Scripts\python.exe
