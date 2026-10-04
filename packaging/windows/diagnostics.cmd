@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
call scripts\resolve-python.cmd
if errorlevel 1 goto finish
"%FPILOT_COMMAND%" %FPILOT_ARGS% -X utf8 scripts\collect_diagnostics.py
:finish
pause
