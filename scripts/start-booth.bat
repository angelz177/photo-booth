@echo off
rem Starts the photo booth service with the Canon EDSDK adapter and opens the
rem booth screen full-screen in Microsoft Edge. Run from the project folder.
cd /d "%~dp0\.."
if exist .venv\Scripts\activate.bat call .venv\Scripts\activate.bat
start "" /min cmd /c "python -m booth --camera edsdk --edsdk-dir EDSDK"
timeout /t 4 /nobreak >nul
start "" msedge --kiosk http://localhost:8000/ --edge-kiosk-type=fullscreen --no-first-run
