@echo off
chcp 65001 >nul
setlocal

set "APP_DIR=F:\SWARM\app"
set "NGROOT=E:\Distributive Soft\NextGIS"
set "NGBAT=%NGROOT%\ng.bat"

if not exist "%NGBAT%" (
    echo ERROR: not found "%NGBAT%"
    pause
    exit /b 1
)

call "%NGBAT%"
if errorlevel 1 (
    echo ERROR: ng.bat failed
    pause
    exit /b 1
)

cd /d "%APP_DIR%"
python main.py

echo.
pause
