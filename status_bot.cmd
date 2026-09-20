@echo off
chcp 65001 >nul
pushd "%~dp0"
if not exist ".venv\Scripts\python.exe" goto missing
".venv\Scripts\python.exe" "scripts\bot_control.py" status
set "hd2ExitCode=%ERRORLEVEL%"
goto done
:missing
echo Project Python not found. Install .venv using README.md first.
set "hd2ExitCode=2"
:done
popd
if /I not "%~1"=="--no-pause" pause
exit /b %hd2ExitCode%
