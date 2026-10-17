@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Launcher

:menu
cls
echo ================================
echo   Select application to run
echo ================================
echo.
echo   1 - SileroRead
echo   2 - Pimsleur app
echo   3 - Shadowing
echo   0 - Exit
echo.
set /p choice=Your choice: 

if "%choice%"=="1" goto run_app
if "%choice%"=="2" goto run_pimsleur
if "%choice%"=="3" goto run_shadowing
if "%choice%"=="0" exit /b

echo.
echo Invalid choice, try again.
timeout /t 2 >nul
goto menu

:run_app
set "SCRIPT=app.py"
goto run

:run_pimsleur
set "SCRIPT=pimsleur_app.py"
goto run

:run_shadowing
set "SCRIPT=Shadowing.py"
goto run

:run
cls
echo Running %SCRIPT% ...
echo.
if not exist "venv\Scripts\activate.bat" (
    echo [ERROR] venv\Scripts\activate.bat not found
    echo Make sure this .bat file is next to the venv folder.
    echo.
    pause
    goto menu
)
call venv\Scripts\activate.bat
python %SCRIPT%
echo.
echo --- Script finished (exit code: %errorlevel%) ---
pause
goto menu