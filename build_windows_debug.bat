@echo off
setlocal
chcp 65001 >nul
set "SUBFLOW_DEBUG_BUILD=1"
echo Building diagnostic console version. This is NOT the quiet release build.
"%~dp0build_windows.bat" --debug %*
exit /b %ERRORLEVEL%
