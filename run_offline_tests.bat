@echo off
setlocal
chcp 65001 >nul
title SubFlow Studio - Self Tests

set "ROOT=%~dp0"
set "NO_PAUSE=0"
for %%A in (%*) do (
  if /i "%%~A"=="--no-pause" set "NO_PAUSE=1"
)

if exist "%ROOT%.build_venv\Scripts\python.exe" (
  "%ROOT%.build_venv\Scripts\python.exe" "%ROOT%run_self_tests.py" %*
  set "RC=%ERRORLEVEL%"
  goto :finish
)

where py >nul 2>nul
if not errorlevel 1 (
  py -3.12 "%ROOT%run_self_tests.py" %*
  set "RC=%ERRORLEVEL%"
  goto :finish
)

python "%ROOT%run_self_tests.py" %*
set "RC=%ERRORLEVEL%"

:finish
if "%NO_PAUSE%"=="0" pause
exit /b %RC%
