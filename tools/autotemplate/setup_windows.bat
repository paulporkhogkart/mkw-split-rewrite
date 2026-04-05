@echo off
echo Creating Windows venv for input_server.py...
python -m venv "%~dp0windows-venv"
echo.
echo Done. No extra packages needed (uses built-in XInput via ctypes).
echo To run input_server.py:
echo   windows-venv\Scripts\python.exe input_server.py
pause
