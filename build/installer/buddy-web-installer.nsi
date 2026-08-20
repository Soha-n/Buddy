; Buddy web installer.
;
; A small stub that downloads the real payload at install time, so the file a
; user downloads is a couple of megabytes rather than a couple of hundred.
;
; Three decisions worth knowing about:
;
; Machine-wide install into Program Files, which is where Windows expects an
; application to live and what users recognise. It requires elevation, so the
; installer asks for admin rights up front.
;
; Two consequences worth knowing. Program Files is not writable by a normal
; user, so nothing the app writes may live there - the database, logs and the
; search index all go to %LOCALAPPDATA%\Buddy, per user. And an in-place
; self-update would need elevation too, so updates must run the installer
; rather than patching the directory.
;
; INetC rather than NSISdl. NSISdl speaks plain HTTP only; GitHub is HTTPS with
; redirects, so it fails at the first hop.
;
; Hashing and extraction go through PowerShell rather than the Crypto and
; nsisunz plugins. nsisunz has no Unicode build - in a Unicode installer it
; fails at runtime rather than at compile time - and both plugins would be
; extra binaries to vendor. Get-FileHash and Expand-Archive ship with every
; supported Windows version, so this drops two dependencies for free.
;
; Pinned URLs and hashes, injected at build time from manifest.json. The
; installer never resolves "latest", so a release published later cannot change
; what an installer already in a user's hands will fetch.

Unicode true
ManifestDPIAware true

!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "FileFunc.nsh"
!include "TextFunc.nsh"
!include "WinVer.nsh"
!include "x64.nsh"
!include "nsDialogs.nsh"

; --- Injected by build-installer.ps1 from manifest.json ---------------------
!ifndef APP_VERSION
  !define APP_VERSION "1.0.0"
!endif
!ifndef APP_URL
  !define APP_URL "https://github.com/Soha-n/Buddy/releases/download/v1.0.0/buddy-app-1.0.0.zip"
!endif
!ifndef APP_SHA256
  !define APP_SHA256 ""
!endif
!ifndef APP_SIZE_MB
  !define APP_SIZE_MB "150"
!endif

!define APP_NAME "Buddy"
!define APP_PUBLISHER "Buddy"
!define APP_EXE "Buddy.exe"
!define OLLAMA_URL "https://ollama.com/download/OllamaSetup.exe"
!define WEBVIEW2_URL "https://go.microsoft.com/fwlink/p/?LinkId=2124703"
!define UNINST_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}"

Name "${APP_NAME}"
OutFile "..\release\Buddy-Setup-${APP_VERSION}.exe"
InstallDir "$PROGRAMFILES64\${APP_NAME}"
; Prompts for elevation before anything runs; writing to Program Files and
; HKLM both require it.
RequestExecutionLevel admin
ShowInstDetails show
SetCompressor /SOLID lzma

Var InstallOllama
Var NeedsWebView2
Var OllamaCheckbox
; Set at install time when Buddy installed Ollama; read by the uninstaller.
Var BuddyOwnsOllama

!define MUI_ABORTWARNING
!define MUI_ICON "..\..\desktop\src-tauri\icons\icon.ico"
!define MUI_UNICON "..\..\desktop\src-tauri\icons\icon.ico"

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
Page custom ComponentsPageShow ComponentsPageLeave
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_RUN "$INSTDIR\${APP_EXE}"
!define MUI_FINISHPAGE_RUN_TEXT "Start ${APP_NAME}"
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "English"

; --------------------------------------------------------------------------- ;
; Dependency detection
; --------------------------------------------------------------------------- ;

Function .onInit
  ${IfNot} ${AtLeastWin10}
    MessageBox MB_ICONSTOP "${APP_NAME} requires Windows 10 or later."
    Abort
  ${EndIf}
  ${IfNot} ${RunningX64}
    MessageBox MB_ICONSTOP "${APP_NAME} requires 64-bit Windows."
    Abort
  ${EndIf}

  ; WebView2 is present on Windows 11 and most Windows 10; the runtime key is
  ; the reliable signal, and it can be per-machine or per-user.
  StrCpy $NeedsWebView2 "1"
  ReadRegStr $0 HKLM "SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" "pv"
  ${If} $0 != ""
    StrCpy $NeedsWebView2 "0"
  ${EndIf}
  ReadRegStr $0 HKCU "Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" "pv"
  ${If} $0 != ""
    StrCpy $NeedsWebView2 "0"
  ${EndIf}

  ; Ollama serves the models. Offer it only when it is actually missing.
  ; Running elevated, HKCU is the administrator's hive rather than the user's,
  ; and Ollama installs per-user - so several places have to be checked before
  ; concluding it is missing.
  StrCpy $InstallOllama "1"
  ReadRegStr $0 HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Ollama" "DisplayName"
  ${If} $0 == ""
    ReadRegStr $0 HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\Ollama" "DisplayName"
  ${EndIf}
  ${If} $0 != ""
    StrCpy $InstallOllama "0"
  ${EndIf}
  IfFileExists "$LOCALAPPDATA\Programs\Ollama\ollama.exe" 0 +2
    StrCpy $InstallOllama "0"
  ; On PATH is the most reliable signal: it means ollama is usable right now.
  nsExec::ExecToStack 'cmd /c where ollama'
  Pop $1
  ${If} $1 == "0"
    StrCpy $InstallOllama "0"
  ${EndIf}
FunctionEnd

Function ComponentsPageShow
  ; Nothing to choose when Ollama is already installed.
  ${If} $InstallOllama == "0"
    Abort
  ${EndIf}
  nsDialogs::Create 1018
  Pop $0
  ${NSD_CreateLabel} 0 0 100% 44u "Buddy runs AI models on your computer using Ollama, which is not installed yet.$\r$\n$\r$\nIt is about 700 MB and is downloaded from ollama.com."
  Pop $0
  ${NSD_CreateCheckbox} 0 52u 100% 12u "Download and install Ollama (recommended)"
  Pop $OllamaCheckbox
  ${NSD_Check} $OllamaCheckbox
  nsDialogs::Show
FunctionEnd

Function ComponentsPageLeave
  ${NSD_GetState} $OllamaCheckbox $0
  ${If} $0 == ${BST_UNCHECKED}
    StrCpy $InstallOllama "0"
  ${EndIf}
FunctionEnd

; --------------------------------------------------------------------------- ;
; Download helper
;
; Verifies SHA-256 before use. A truncated or tampered download then fails
; loudly here rather than installing an app that breaks later in ways nobody
; can diagnose.
; --------------------------------------------------------------------------- ;

!macro DownloadVerified Url Target Expected Label
  DetailPrint "Downloading ${Label}..."
  inetc::get /CAPTION "${Label}" /RESUME "" "${Url}" "${Target}" /END
  Pop $0
  ${If} $0 != "OK"
    ; Retry once: transient failures are common, and GitHub rate-limits.
    DetailPrint "Download failed ($0). Retrying..."
    Sleep 3000
    inetc::get /CAPTION "${Label}" /RESUME "" "${Url}" "${Target}" /END
    Pop $0
    ${If} $0 != "OK"
      MessageBox MB_ICONSTOP "Could not download ${Label}.$\r$\n$\r$\nReason: $0$\r$\n$\r$\nCheck your internet connection and run the installer again."
      Abort
    ${EndIf}
  ${EndIf}

  ${If} "${Expected}" != ""
    DetailPrint "Verifying ${Label}..."
    ; -NoProfile so a user's PowerShell profile cannot interfere with output.
    ; Both sides lowercased explicitly: the manifest stores lowercase and
    ; Get-FileHash returns uppercase. PowerShell's -eq is case-insensitive for
    ; strings so this works either way, but relying on that is a trap for
    ; whoever changes the comparison later.
    nsExec::ExecToStack 'powershell -NoProfile -NonInteractive -Command "if ((Get-FileHash -Algorithm SHA256 -LiteralPath \"${Target}\").Hash.ToLower() -ceq \"${Expected}\".ToLower()) { \"MATCH\" } else { \"MISMATCH\" }"'
    Pop $0
    Pop $1
    ${If} $0 != "0"
      MessageBox MB_ICONSTOP "Could not verify ${Label} (PowerShell returned $0)."
      Abort
    ${EndIf}
    ; Compare inside PowerShell: ExecToStack returns output with a trailing
    ; newline, and trimming it in NSIS needs more machinery than the check
    ; itself is worth. "MATCH"/"MISMATCH" comes back clean either way.
    ${If} $1 != "MATCH$\r$\n"
    ${AndIf} $1 != "MATCH$\n"
    ${AndIf} $1 != "MATCH"
      Delete "${Target}"
      MessageBox MB_ICONSTOP "${Label} failed its integrity check and was discarded.$\r$\n$\r$\nThe download may have been corrupted. Please run the installer again."
      Abort
    ${EndIf}
    DetailPrint "${Label} verified."
  ${EndIf}
!macroend

; --------------------------------------------------------------------------- ;
; Install
; --------------------------------------------------------------------------- ;

Section "Buddy" SecMain
  SetOutPath "$INSTDIR"

  ; WebView2 first: it is what renders the UI, so installing it after the app
  ; would leave a broken window on the first launch.
  ${If} $NeedsWebView2 == "1"
    DetailPrint "Installing WebView2 runtime..."
    inetc::get /CAPTION "WebView2 runtime" "${WEBVIEW2_URL}" "$PLUGINSDIR\webview2.exe" /END
    Pop $0
    ${If} $0 == "OK"
      ExecWait '"$PLUGINSDIR\webview2.exe" /silent /install' $1
      DetailPrint "WebView2 installer finished ($1)"
    ${Else}
      DetailPrint "WebView2 download failed ($0); continuing"
    ${EndIf}
  ${EndIf}

  !insertmacro DownloadVerified "${APP_URL}" "$PLUGINSDIR\buddy-app.zip" "${APP_SHA256}" "Buddy (${APP_SIZE_MB} MB)"

  DetailPrint "Installing..."
  ; -Force so a reinstall over an existing directory overwrites rather than
  ; failing halfway through.
  nsExec::ExecToLog 'powershell -NoProfile -NonInteractive -Command "Expand-Archive -LiteralPath \"$PLUGINSDIR\buddy-app.zip\" -DestinationPath \"$INSTDIR\" -Force"'
  Pop $0
  ${If} $0 != "0"
    MessageBox MB_ICONSTOP "Could not extract Buddy (error $0)."
    Abort
  ${EndIf}
  ; Extraction reporting success while producing nothing would otherwise leave
  ; a shortcut pointing at a missing executable.
  IfFileExists "$INSTDIR\${APP_EXE}" +3 0
    MessageBox MB_ICONSTOP "Buddy did not install correctly: ${APP_EXE} is missing."
    Abort

  ${If} $InstallOllama == "1"
    DetailPrint "Downloading Ollama (about 700 MB)..."
    ; Straight from ollama.com: rehosting their binary would make us
    ; responsible for shipping their security updates.
    inetc::get /CAPTION "Ollama" /RESUME "" "${OLLAMA_URL}" "$PLUGINSDIR\OllamaSetup.exe" /END
    Pop $0
    ${If} $0 == "OK"
      DetailPrint "Installing Ollama..."
      ; /S, not /VERYSILENT. OllamaSetup.exe is an NSIS installer, which does
      ; not recognise Inno Setup's switch and silently ignored it - so it ran
      ; its full UI and then launched its tray app over the top of ours, in the
      ; middle of our install. /S is the NSIS silent switch it does honour.
      ExecWait '"$PLUGINSDIR\OllamaSetup.exe" /S' $1
      DetailPrint "Ollama installer finished ($1)"
      ; Ollama starts itself after installing. Nothing needs it until the user
      ; picks a model, and leaving it running mid-install puts a tray icon and
      ; a window in front of the person still using our wizard.
      nsExec::ExecToLog 'taskkill /F /IM "ollama app.exe"'
      nsExec::ExecToLog 'taskkill /F /IM ollama.exe'
      ; Remember that Buddy is what put Ollama on this machine, so the
      ; uninstaller can remove it without touching an installation that was
      ; already here. Written under our own key, which the uninstaller deletes.
      WriteRegDWORD HKLM "${UNINST_KEY}" "BuddyInstalledOllama" 1
    ${Else}
      DetailPrint "Ollama download failed ($0)"
      MessageBox MB_ICONEXCLAMATION "Ollama could not be downloaded. Buddy will still install, but you will need to install Ollama from ollama.com before you can chat."
    ${EndIf}
  ${EndIf}

  ; All-users locations: the install is machine-wide, so every user
  ; should see it.
  SetShellVarContext all
  CreateDirectory "$SMPROGRAMS\${APP_NAME}"
  CreateShortcut "$SMPROGRAMS\${APP_NAME}\${APP_NAME}.lnk" "$INSTDIR\${APP_EXE}"
  CreateShortcut "$SMPROGRAMS\${APP_NAME}\Uninstall ${APP_NAME}.lnk" "$INSTDIR\Uninstall.exe"
  CreateShortcut "$DESKTOP\${APP_NAME}.lnk" "$INSTDIR\${APP_EXE}"

  WriteUninstaller "$INSTDIR\Uninstall.exe"

  ; HKLM, matching the machine-wide install, so the entry is visible to every
  ; user rather than only the one who installed.
  WriteRegStr HKLM "${UNINST_KEY}" "DisplayName" "${APP_NAME}"
  WriteRegStr HKLM "${UNINST_KEY}" "DisplayVersion" "${APP_VERSION}"
  WriteRegStr HKLM "${UNINST_KEY}" "Publisher" "${APP_PUBLISHER}"
  WriteRegStr HKLM "${UNINST_KEY}" "DisplayIcon" "$INSTDIR\${APP_EXE}"
  WriteRegStr HKLM "${UNINST_KEY}" "UninstallString" "$\"$INSTDIR\Uninstall.exe$\""
  ; What winget and scripted removals invoke; without it they fall back to
  ; the interactive string and hang waiting for a click.
  WriteRegStr HKLM "${UNINST_KEY}" "QuietUninstallString" "$\"$INSTDIR\Uninstall.exe$\" /S"
  WriteRegStr HKLM "${UNINST_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegDWORD HKLM "${UNINST_KEY}" "NoModify" 1
  WriteRegDWORD HKLM "${UNINST_KEY}" "NoRepair" 1

  ${GetSize} "$INSTDIR" "/S=0K" $0 $1 $2
  IntFmt $0 "0x%08X" $0
  WriteRegDWORD HKLM "${UNINST_KEY}" "EstimatedSize" "$0"
SectionEnd

; --------------------------------------------------------------------------- ;
; Uninstall
; --------------------------------------------------------------------------- ;

Section "Uninstall"
  ; The app spawns a backend; removing files under a running one would leave a
  ; half-deleted install.
  DetailPrint "Stopping ${APP_NAME}..."
  nsExec::ExecToLog 'taskkill /F /IM ${APP_EXE}'
  nsExec::ExecToLog 'taskkill /F /IM buddy-backend.exe'
  nsExec::ExecToLog 'taskkill /F /IM buddy-runner.exe'
  Sleep 1500

  ; Same context the installer used, or these resolve to the wrong user.
  SetShellVarContext all
  Delete "$SMPROGRAMS\${APP_NAME}\${APP_NAME}.lnk"
  Delete "$SMPROGRAMS\${APP_NAME}\Uninstall ${APP_NAME}.lnk"
  RMDir "$SMPROGRAMS\${APP_NAME}"
  Delete "$DESKTOP\${APP_NAME}.lnk"

  ; Read before the key is deleted: this is the only record of whether Buddy
  ; is what put Ollama on this machine, and the next line destroys it.
  ReadRegDWORD $BuddyOwnsOllama HKLM "${UNINST_KEY}" "BuddyInstalledOllama"

  RMDir /r "$INSTDIR"
  DeleteRegKey HKLM "${UNINST_KEY}"

  ; --- Buddy's own data ---------------------------------------------------- ;
  ;
  ; Conversations, settings and the search index live per user under
  ; %LOCALAPPDATA%\Buddy. This uninstaller runs elevated, so $LOCALAPPDATA is
  ; the *administrator's* profile rather than the person who used the app - and
  ; on a shared machine there may be one copy per user. Removing only the
  ; elevated profile's copy would leave the real data behind on exactly the
  ; machines where it matters most.
  ;
  ; So every profile is walked. The helper scripts are extracted to $PLUGINSDIR
  ; rather than run from $INSTDIR, which no longer exists by this point, and
  ; NSIS deletes that directory itself when the uninstaller exits.
  ;
  ; They are separate .ps1 files because quoting a script of this length inside
  ; an NSIS single-quoted string means escaping quotes through two layers, and
  ; a mistake there fails silently at uninstall time - the worst place to find
  ; one.
  DetailPrint "Removing ${APP_NAME} data..."
  File "/oname=$PLUGINSDIR\purge-data.ps1" "purge-data.ps1"
  nsExec::ExecToLog 'powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$PLUGINSDIR\purge-data.ps1"'
  Pop $0

  ; --- Ollama -------------------------------------------------------------- ;
  ;
  ; Only when Buddy installed it. A pre-existing Ollama may be serving other
  ; applications, and its models are tens of gigabytes the user did not ask us
  ; to discard - so an installation we did not create is left alone.
  ${If} $BuddyOwnsOllama == 1
    DetailPrint "Removing Ollama (installed by ${APP_NAME})..."
    nsExec::ExecToLog 'taskkill /F /IM "ollama app.exe"'
    nsExec::ExecToLog 'taskkill /F /IM ollama.exe'
    Sleep 1000
    File "/oname=$PLUGINSDIR\purge-ollama.ps1" "purge-ollama.ps1"
    nsExec::ExecToLog 'powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$PLUGINSDIR\purge-ollama.ps1"'
    Pop $0
  ${Else}
    DetailPrint "Leaving Ollama installed (it was already on this machine)."
  ${EndIf}
SectionEnd
