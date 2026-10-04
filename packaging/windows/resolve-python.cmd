@echo off
if defined FPILOT_PYTHON (
  set "FPILOT_COMMAND=%FPILOT_PYTHON%"
  set "FPILOT_ARGS="
  exit /b 0
)
py -3.11 -c "import sys; assert sys.maxsize > 2**32" >nul 2>&1
if not errorlevel 1 (
  set "FPILOT_COMMAND=py"
  set "FPILOT_ARGS=-3.11"
  exit /b 0
)
python -c "import sys; assert sys.version_info[:2] == (3,11) and sys.maxsize > 2**32" >nul 2>&1
if not errorlevel 1 (
  set "FPILOT_COMMAND=python"
  set "FPILOT_ARGS="
  exit /b 0
)
echo Please install Python 3.11 x64, or set FPILOT_PYTHON to an existing python.exe.
exit /b 2
