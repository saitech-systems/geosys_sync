@echo off
REM Run the qgis-marked plugin tests inside the QGIS python environment.
REM Usage: run_qgis_tests.bat  (from anywhere; paths are absolute)
setlocal
set REPO=%~dp0..\..
set QT_QPA_PLATFORM=offscreen
set PYQGIS=

for %%V in ("QGIS 3.42.2" "QGIS 3.40.2") do (
    if exist "C:\Program Files\%%~V\bin\python-qgis-ltr.bat" (
        set "PYQGIS=C:\Program Files\%%~V\bin\python-qgis-ltr.bat"
        goto :found
    )
    if exist "C:\Program Files\%%~V\bin\python-qgis.bat" (
        set "PYQGIS=C:\Program Files\%%~V\bin\python-qgis.bat"
        goto :found
    )
)
echo No QGIS python found under "C:\Program Files\QGIS *" & exit /b 1
:found
echo Using %PYQGIS%
call "%PYQGIS%" -m pip install --quiet pytest requests-mock
call "%PYQGIS%" -m pytest "%REPO%\plugin\tests" -m qgis -v
endlocal
