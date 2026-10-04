@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
call scripts\resolve-python.cmd
if errorlevel 1 goto finish
"%FPILOT_COMMAND%" %FPILOT_ARGS% -X utf8 -m pip install --only-binary=:all: -r requirements-runtime.txt
if errorlevel 1 goto finish
"%FPILOT_COMMAND%" %FPILOT_ARGS% -X utf8 launch.py --check
:finish
pause
