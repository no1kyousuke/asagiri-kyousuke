@echo off
setlocal
chcp 65001 >nul
title SubFlow Studio - Windows Release Build

set "ROOT=%~dp0"
set "NO_PAUSE=0"
for %%A in (%*) do (
  if /i "%%~A"=="--no-pause" set "NO_PAUSE=1"
)

if not exist "%ROOT%build_release.py" (
  echo [ERROR] build_release.py is missing.
  echo Please extract the complete ZIP with "Extract All" first.
  if "%NO_PAUSE%"=="0" pause
  exit /b 2
)

where py >nul 2>nul
if not errorlevel 1 (
  py -3.12 "%ROOT%build_release.py" %*
  set "RC=%ERRORLEVEL%"
  goto :finish
)

if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" (
  "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" "%ROOT%build_release.py" %*
  set "RC=%ERRORLEVEL%"
  goto :finish
)

where python >nul 2>nul
if not errorlevel 1 (
  python "%ROOT%build_release.py" %*
  set "RC=%ERRORLEVEL%"
  goto :finish
)

echo [ERROR] Python 3.12 x64 was not found.
echo Install Python 3.12 x64 with the Python Launcher, then retry.
set "RC=3"

:finish
if not "%RC%"=="0" (
  echo.
  echo [FAILED] Build failed with exit code %RC%.
)
if "%NO_PAUSE%"=="0" pause
exit /b %RC%
