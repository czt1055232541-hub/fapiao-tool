@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
if not defined FAPIAO_PYTHON set "FAPIAO_PYTHON=python"

set "PAUSE_FLAG=1"
if "%~1"=="--nopause" (
  set "PAUSE_FLAG=0"
  shift
)

echo ============================================
echo   Monthly invoice collection (fapiao)
echo   Window: 21st of last month - 20th of this month
echo ============================================
echo.

"%FAPIAO_PYTHON%" "scripts\run_monthly.py" %*
set "RC=%ERRORLEVEL%"

echo.
if not "%RC%"=="0" (
  echo [FAILED] exit code %RC%
  echo See log under data\logs\ for details.
) else (
  echo [DONE]
)

echo.
if "%PAUSE_FLAG%"=="1" pause
exit /b %RC%
