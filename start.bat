@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    python -m venv .venv
    if errorlevel 1 goto error
)

".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto error

".venv\Scripts\python.exe" main.py %*
if errorlevel 1 goto error

exit /b 0

:error
echo.
echo Uruchomienie nie powiodlo sie. Sprawdz komunikat powyzej.
exit /b 1