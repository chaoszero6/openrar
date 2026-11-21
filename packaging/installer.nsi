; NSIS 3 script: builds dist\OpenRAR-Setup-<version>.exe from dist\OpenRAR\
;   makensis packaging\installer.nsi            (Windows: NSIS 3 from nsis.sourceforge.io)
;   makensis -DVERSION=1.2.0 packaging/installer.nsi   (Linux: apt install nsis)
; Per-user install (no administrator rights), like the per-user Explorer integration.
Unicode true
Target amd64-unicode
SetCompressor /SOLID lzma

!ifndef VERSION
  !define VERSION "1.0.1"
!endif
!define APP "OpenRAR"
!define UNINST_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP}"
!define ROOT ".."

Name "${APP} ${VERSION}"
OutFile "${ROOT}\dist\${APP}-Setup-${VERSION}.exe"
InstallDir "$LOCALAPPDATA\Programs\${APP}"
InstallDirRegKey HKCU "Software\${APP}" "InstallDir"
RequestExecutionLevel user
ManifestDPIAware true
BrandingText "${APP} ${VERSION}"

VIProductVersion "${VERSION}.0"
VIAddVersionKey "ProductName" "${APP}"
VIAddVersionKey "FileDescription" "${APP} Setup"
VIAddVersionKey "FileVersion" "${VERSION}"
VIAddVersionKey "ProductVersion" "${VERSION}"
VIAddVersionKey "LegalCopyright" "${APP} contributors"

!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "WinMessages.nsh"

!define MUI_ICON "${ROOT}\openrar\resources\icon.ico"
!define MUI_UNICON "${ROOT}\openrar\resources\icon.ico"
!define MUI_ABORTWARNING
!define MUI_COMPONENTSPAGE_SMALLDESC
!define MUI_FINISHPAGE_RUN "$INSTDIR\OpenRAR.exe"
!define MUI_FINISHPAGE_RUN_TEXT "Start ${APP}"

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_COMPONENTS
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"

Section "${APP} (required)" SecMain
  SectionIn RO
  ; close a running copy so its files can be replaced
  nsExec::Exec 'taskkill /IM OpenRAR.exe /F'
  SetOutPath "$INSTDIR"
  RMDir /r "$INSTDIR\_internal"
  File "${ROOT}\dist\OpenRAR\OpenRAR.exe"
  File "${ROOT}\dist\OpenRAR\orar.exe"
  File /r "${ROOT}\dist\OpenRAR\_internal"
  WriteUninstaller "$INSTDIR\Uninstall.exe"

  WriteRegStr HKCU "Software\${APP}" "InstallDir" "$INSTDIR"
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayName" "${APP}"
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "${UNINST_KEY}" "Publisher" "${APP}"
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayIcon" "$INSTDIR\OpenRAR.exe"
  WriteRegStr HKCU "${UNINST_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UNINST_KEY}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegStr HKCU "${UNINST_KEY}" "QuietUninstallString" '"$INSTDIR\Uninstall.exe" /S'
  WriteRegDWORD HKCU "${UNINST_KEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINST_KEY}" "NoRepair" 1
  WriteRegDWORD HKCU "${UNINST_KEY}" "EstimatedSize" 125000

  CreateDirectory "$SMPROGRAMS\${APP}"
  CreateShortcut "$SMPROGRAMS\${APP}\${APP}.lnk" "$INSTDIR\OpenRAR.exe"
  CreateShortcut "$SMPROGRAMS\${APP}\Uninstall ${APP}.lnk" "$INSTDIR\Uninstall.exe"
SectionEnd

Section "Explorer context menu and file associations" SecShell
  ExecWait '"$INSTDIR\OpenRAR.exe" --register-shell'
SectionEnd

Section /o "Desktop shortcut" SecDesktop
  CreateShortcut "$DESKTOP\${APP}.lnk" "$INSTDIR\OpenRAR.exe"
SectionEnd

Section /o "Add orar.exe (command line) to PATH" SecPath
  ReadRegStr $0 HKCU "Environment" "Path"
  Push "$0"
  Push "$INSTDIR"
  Call PathContains
  Pop $1
  ${If} $1 == 0
    ${If} $0 == ""
      WriteRegExpandStr HKCU "Environment" "Path" "$INSTDIR"
    ${Else}
      WriteRegExpandStr HKCU "Environment" "Path" "$0;$INSTDIR"
    ${EndIf}
    WriteRegDWORD HKCU "Software\${APP}" "AddedToPath" 1
    SendMessage ${HWND_BROADCAST} ${WM_SETTINGCHANGE} 0 "STR:Environment" /TIMEOUT=2000
  ${EndIf}
SectionEnd

!insertmacro MUI_FUNCTION_DESCRIPTION_BEGIN
  !insertmacro MUI_DESCRIPTION_TEXT ${SecMain} "The OpenRAR application and the orar.exe command-line tool."
  !insertmacro MUI_DESCRIPTION_TEXT ${SecShell} "Add OpenRAR to the right-click menu and open archives with OpenRAR. Can be changed later in Options > Settings > Integration."
  !insertmacro MUI_DESCRIPTION_TEXT ${SecDesktop} "Put an OpenRAR shortcut on the desktop."
  !insertmacro MUI_DESCRIPTION_TEXT ${SecPath} "Lets you run orar from any command prompt."
!insertmacro MUI_FUNCTION_DESCRIPTION_END

; ---------------------------------------------------------------- uninstall
Section "Uninstall"
  nsExec::Exec 'taskkill /IM OpenRAR.exe /F'
  ExecWait '"$INSTDIR\OpenRAR.exe" --unregister-shell'

  ReadRegDWORD $0 HKCU "Software\${APP}" "AddedToPath"
  ${If} $0 == 1
    ReadRegStr $1 HKCU "Environment" "Path"
    Push "$1"
    Push "$INSTDIR"
    Call un.PathRemove
    Pop $1
    WriteRegExpandStr HKCU "Environment" "Path" "$1"
    SendMessage ${HWND_BROADCAST} ${WM_SETTINGCHANGE} 0 "STR:Environment" /TIMEOUT=2000
  ${EndIf}

  ; only remove what the installer put there (the folder may be user-chosen)
  Delete "$INSTDIR\OpenRAR.exe"
  Delete "$INSTDIR\orar.exe"
  RMDir /r "$INSTDIR\_internal"
  Delete "$INSTDIR\Uninstall.exe"
  RMDir "$INSTDIR"
  Delete "$SMPROGRAMS\${APP}\${APP}.lnk"
  Delete "$SMPROGRAMS\${APP}\Uninstall ${APP}.lnk"
  RMDir "$SMPROGRAMS\${APP}"
  Delete "$DESKTOP\${APP}.lnk"
  DeleteRegKey HKCU "${UNINST_KEY}"
  DeleteRegKey HKCU "Software\${APP}"
SectionEnd

; ---------------------------------------------------------------- PATH helpers
; Path entries are compared case-insensitively as whole ';'-separated items.
!macro PATH_FUNCS PFX
Function ${PFX}PathSplit        ; in: $R0 = path list, $R2 = start index; out: $R3 = item, $R2 = next index (-1 = end)
  StrCpy $R3 ""
  StrLen $R4 $R0
  ${Do}
    ${If} $R2 >= $R4
      StrCpy $R2 -1
      ${Break}
    ${EndIf}
    StrCpy $R5 $R0 1 $R2
    IntOp $R2 $R2 + 1
    ${If} $R5 == ";"
      ${Break}
    ${EndIf}
    StrCpy $R3 "$R3$R5"
  ${Loop}
FunctionEnd
!macroend
!insertmacro PATH_FUNCS ""
!insertmacro PATH_FUNCS "un."

Function PathContains           ; stack: path list, dir -> 1/0
  Exch $R1
  Exch
  Exch $R0
  Push $R2
  Push $R3
  Push $R4
  Push $R5
  StrCpy $R2 0
  StrCpy $R6 0
  ${Do}
    ${If} $R2 == -1
      ${Break}
    ${EndIf}
    Call PathSplit
    ${If} $R3 == $R1            ; NSIS string == is case-insensitive
    ${OrIf} $R3 == "$R1\"
      StrCpy $R6 1
      ${Break}
    ${EndIf}
  ${Loop}
  Pop $R5
  Pop $R4
  Pop $R3
  Pop $R2
  Pop $R0
  Pop $R1
  Push $R6
FunctionEnd

Function un.PathRemove          ; stack: path list, dir -> path list without dir
  Exch $R1
  Exch
  Exch $R0
  Push $R2
  Push $R3
  Push $R4
  Push $R5
  StrCpy $R2 0
  StrCpy $R6 ""
  ${Do}
    ${If} $R2 == -1
      ${Break}
    ${EndIf}
    Call un.PathSplit
    ${If} $R3 == ""
    ${OrIf} $R3 == $R1
    ${OrIf} $R3 == "$R1\"
      ${Continue}
    ${EndIf}
    ${If} $R6 == ""
      StrCpy $R6 $R3
    ${Else}
      StrCpy $R6 "$R6;$R3"
    ${EndIf}
  ${Loop}
  Pop $R5
  Pop $R4
  Pop $R3
  Pop $R2
  Pop $R0
  Pop $R1
  Push $R6
FunctionEnd
