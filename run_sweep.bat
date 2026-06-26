@echo off
REM ==========================================================================
REM  Asset clip sweep — one launcher.
REM
REM  nxbt MUST run in WSL2 (Linux Bluetooth) and ffmpeg+detection MUST run on
REM  Windows (the capture card), so they are separate processes — this just
REM  opens both for you and drops you straight into the pilot.  Double-click,
REM  or run `run_sweep.bat`.
REM
REM  If the Agent window asks for a WSL sudo password, type it there (nxbt needs
REM  root for Bluetooth).  `pip install keyring` makes it silent (reads the one
REM  nxauto already stored).
REM ==========================================================================
cd /d "%~dp0"

echo [1/3] Starting controller agent in WSL2 (auto-connects the Switch)...
start "MKW Agent"   cmd /k python tools\autotemplate\start_agent.py
echo       waiting for the agent + Switch to come up...
timeout /t 10 /nobreak >nul

echo [2/3] Starting the tracker (owns the capture card, detection on the 1080p tee)...
start "MKW Tracker" cmd /k python -m mkw_tracker --clip-capture --ws-port 8766
echo       waiting for the tracker...
timeout /t 6 /nobreak >nul

echo.
echo [3/3] PILOT — drive to character-select (Mario top-left), then press Enter.
echo.
python tools\autotemplate\sweep_runner.py --pilot

echo.
echo Sweep window done.  The Agent and Tracker windows stay open — close them when finished.
pause
