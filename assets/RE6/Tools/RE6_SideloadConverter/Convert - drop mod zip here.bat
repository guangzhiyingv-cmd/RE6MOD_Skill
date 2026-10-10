@echo off
rem RE6 Sideload Converter - drag one or more mod .zip files onto this file
setlocal
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
chcp 65001 >nul
"%~dp0runtime\python.exe" -I -B "%~dp0app\arc_mod_to_loose.py" %*
echo.
pause
