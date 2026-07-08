@echo off
cd /d "%~dp0"
rem Rig (RTX 5080). Joins the shared claim queue; output stays local on D: (which is the share).
set KARTOFF_CLAIMS_DIR=D:\kartoff\asset_chips\claims
python tools\sweep_console\app.py
