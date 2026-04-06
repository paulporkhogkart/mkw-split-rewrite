; Custom NSIS hooks for mkw-tracker installer.
; Included by Tauri's generated NSIS script via bundle.windows.nsis.installerHooks.

; After uninstall: delete the Python tracker's app-data folder (%APPDATA%\mkw-tracker)
; when the user has ticked "Delete app data", mirroring what Tauri does for its own data.
!macro NSIS_HOOK_POSTUNINSTALL
  ${If} $DeleteAppDataCheckboxState = 1
  ${AndIf} $UpdateMode <> 1
    RmDir /r "$APPDATA\mkw-tracker"
  ${EndIf}
!macroend
