@echo off
setlocal enabledelayedexpansion
REM ==========================================================================
REM  Restart-looping launcher for the asset matte batch (process_all.py).
REM
REM  process_all exits 75/76 when the birefnet CUDA context dies mid-run -- a TDR
REM  / GPU driver reset (e.g. desktop/browser GPU contention stalling a kernel
REM  past TdrDelay). A fresh PROCESS gets a fresh CUDA context, so we wait for the
REM  driver to settle and re-run; the manifest makes it resume where it stopped.
REM    75 = context lost AFTER matting >=1 clip this run (progress) -> reset counter
REM    76 = context lost with NO progress this run (GPU maybe wedged) -> count it
REM  Any other code ends the loop: 0 = finished / stop-file / limit, or a real crash.
REM
REM  Pass process_all args through, e.g.:
REM    run_matte.bat --clips D:\kartoff\clips --out D:\kartoff\asset_chips
REM
REM  ALSO raise TdrDelay so normal desktop use doesn't trip the reset in the first
REM  place (elevated prompt, then reboot):
REM    reg add "HKLM\SYSTEM\CurrentControlSet\Control\GraphicsDrivers" /v TdrDelay /t REG_DWORD /d 10 /f
REM ==========================================================================
cd /d "%~dp0"
set "PY=temp\asset-venv-matte\Scripts\python.exe"
set /a WEDGED=0

:loop
"%PY%" tools\asset_matte\process_all.py %*
set "RC=!ERRORLEVEL!"
if "!RC!"=="75" (
  set /a WEDGED=0
  echo.
  echo [run_matte] CUDA context lost after making progress -- waiting 20s, then resuming...
  timeout /t 20 /nobreak >nul
  goto loop
)
if "!RC!"=="76" (
  set /a WEDGED+=1
  if !WEDGED! GEQ 5 (
    echo.
    echo [run_matte] CUDA context lost 5x with NO progress -- the GPU driver is likely wedged.
    echo [run_matte] Reboot the machine, then re-run this launcher.
    exit /b 76
  )
  echo.
  echo [run_matte] CUDA context lost, no progress ^(!WEDGED!/5^) -- waiting 20s for the driver...
  timeout /t 20 /nobreak >nul
  goto loop
)
echo [run_matte] process_all exited !RC! -- done.
exit /b !RC!
