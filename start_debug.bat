@echo off
setlocal
chcp 65001 >nul
title SubFlow Studio - Startup Diagnostics

set "ROOT=%~dp0"
if exist "%ROOT%.build_venv\Scripts\python.exe" (
  "%ROOT%.build_venv\Scripts\python.exe" "%ROOT%diagnose_startup.py" --launch %*
  set "RC=%ERRORLEVEL%"
  goto :finish
)

where py >nul 2>nul
if not errorlevel 1 (
  py -3.12 "%ROOT%diagnose_startup.py" --launch %*
  set "RC=%ERRORLEVEL%"
  goto :finish
)

python "%ROOT%diagnose_startup.py" --launch %*
set "RC=%ERRORLEVEL%"

:finish
pause
exit /b %RC%
