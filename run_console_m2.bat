@echo off
cd /d "%~dp0"
rem Second box (RTX 2080 Ti). Reads clips over SMB, mattes to a LOCAL scratch, ships each
rem finished clip to the rig, deletes local. C:\kartoff_scratch is created automatically.
set KARTOFF_CLIPS_DIR=\\PAUL-AM5-DT\kartoff\captures_sdr\en_uk\clips
set KARTOFF_PROCESS_OUT=C:\kartoff_scratch\asset_chips
set KARTOFF_CLAIMS_DIR=\\PAUL-AM5-DT\kartoff\asset_chips\claims
set KARTOFF_SHIP_DIR=\\PAUL-AM5-DT\kartoff\asset_chips
python tools\sweep_console\app.py
