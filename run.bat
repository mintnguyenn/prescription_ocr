@echo off
cd /d "%~dp0"

call .venv\Scripts\activate.bat
if errorlevel 1 (
    echo Failed to activate the virtual environment.
    pause
    exit /b 1
)

python -m src.main
set "exit_code=%ERRORLEVEL%"

echo.
echo Process finished with exit code %exit_code%.
pause
exit /b %exit_code%
