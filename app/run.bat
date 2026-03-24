@echo off
chcp 65001 >nul
setlocal

set "APP_DIR=%~dp0"
set "ROOT_DIR=%~dp0.."
set "GIS_DIR=%ROOT_DIR%\gis"
set "QGIS_DIR=%GIS_DIR%\apps\qgis"
set "PY_DIR=%GIS_DIR%\apps\Python312"
set "QT_DIR=%GIS_DIR%\apps\Qt5"

if not exist "%QGIS_DIR%\bin\qgis_core.dll" (
    echo ERROR: QGIS not found at %QGIS_DIR%
    pause
    exit /b 1
)

set "PATH=%QGIS_DIR%\bin;%GIS_DIR%\bin;%PY_DIR%;%PY_DIR%\Scripts;%QT_DIR%\bin;%PATH%"
set "PYTHONPATH=%QGIS_DIR%\python;%QGIS_DIR%\python\plugins;%PY_DIR%\Lib\site-packages"
set "QGIS_PREFIX_PATH=%QGIS_DIR%"
set "GDAL_DATA=%GIS_DIR%\apps\gdal\share\gdal"
set "PROJ_LIB=%GIS_DIR%\share\proj"
set "GDAL_DRIVER_PATH=%GIS_DIR%\apps\gdal\bin\gdalplugins"
set "QT_PLUGIN_PATH=%QGIS_DIR%\qtplugins;%QT_DIR%\plugins"
set "PYTHONHOME=%PY_DIR%"

cd /d "%APP_DIR%"
"%PY_DIR%\python.exe" main.py

echo.
pause
