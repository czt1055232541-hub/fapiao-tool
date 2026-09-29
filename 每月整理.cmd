@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

rem Pure ASCII only: cmd.exe reads this file with the console code page.
rem Chinese output comes from Python, which forces UTF-8 on stdout.
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
if not defined FAPIAO_PYTHON set "FAPIAO_PYTHON=python"

rem --nopause is for the scheduled task: it must NOT be forwarded to Python.
rem cmd's shift does not touch %*, so strip it by substitution instead.
set "PAUSE_FLAG=1"
if "%~1"=="--nopause" set "PAUSE_FLAG=0"
set "ARGS=%*"
set "ARGS=%ARGS:--nopause=%"

echo ============================================
echo   Monthly invoice collection (fapiao)
echo   Window: 21st of last month - 20th of this month
echo ============================================
echo.

"%FAPIAO_PYTHON%" "scripts\run_monthly.py" %ARGS%
set "RC=%ERRORLEVEL%"

echo.
if not "%RC%"=="0" (
  echo [FAILED] exit code %RC%
) else (
  echo [DONE]
)

echo.
if "%PAUSE_FLAG%"=="1" pause
exit /b %RC%
