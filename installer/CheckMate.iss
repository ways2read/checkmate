; Inno Setup script for CheckMate (Windows x64)
;
; Prerequisites:
;   1. Install Inno Setup 6: https://jrsoftware.org/isinfo.php
;   2. Build the app first (bundles Temurin JRE, eBraille Checker, EPUBCheck, veraPDF):
;        uv sync --extra dev
;        uv run python scripts/package.py --clean
;      Or one-shot: powershell -ExecutionPolicy Bypass -File scripts\build_installer.ps1
;        (packages, compiles, then Authenticode-signs like Fido unless
;         CHECKMATE_SKIP_INSTALLER_SIGN=1 or -SkipSign)
;   3. Compile this script (ISCC or Inno Setup Compiler GUI):
;        iscc installer\CheckMate.iss
;
; The [Files] section ships the full dist\CheckMate\ tree, including:
;   runtime\   (JRE), checker\ (eBraille Checker), epubcheck\ (W3C EPUBCheck),
;   verapdf\   (veraPDF CLI), ace\ (Ace by DAISY with Node + Chromium)
;
; Output: installer\Output\CheckMate-<version>-setup.exe

#define MyAppName "CheckMate"
#define MyAppFullName "CheckMate"
#define MyAppVersion "0.7.38"
#define MyAppPublisher "ways2read"
#define MyAppURL "https://github.com/ways2read/checkmate"
#define MyAppExeName "CheckMate.exe"
; Keep in sync with application data folder name (checkmate/paths.py)
#define MyAppDataName "CheckMate"
; Stable identity across upgrades — do not change once released
#define MyAppId "{{7F3A9B2E-1C4D-4E8F-A6B5-9D2E8C1F4A70}"

[Setup]
AppId={#MyAppId}
AppName={#MyAppFullName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppFullName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
AppUpdatesURL={#MyAppURL}/releases
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
LicenseFile=..\LICENSE
InfoBeforeFile=welcome.txt
OutputDir=Output
OutputBaseFilename=CheckMate-{#MyAppVersion}-setup
SetupIconFile=CheckMate.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppFullName}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
WizardSizePercent=120
ShowLanguageDialog=no
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Python 3.13 / current wxPython builds require Windows 10+
MinVersion=10.0
ChangesAssociations=yes
; Per-user install by default; users can elevate for Program Files
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
; Close a running copy during upgrade
CloseApplications=yes
RestartApplications=no
DisableProgramGroupPage=yes
UsedUserAreasWarning=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "fileassoc"; Description: "Add .ebrl/.epub/.pdf/.html/.svg/.css Open with… and ""Validate with CheckMate"" context menu"; GroupDescription: "File associations:"; Flags: checkedonce

[Files]
; Entire PyInstaller onedir tree (exe + _internal + runtime + checker + epubcheck + verapdf)
Source: "..\dist\CheckMate\*"; DestDir: "{app}"; \
  Flags: ignoreversion recursesubdirs createallsubdirs
; Explicit icon for Start Menu / desktop shortcuts (more reliable than exe embed)
Source: "CheckMate.ico"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; \
  IconFilename: "{app}\CheckMate.ico"; \
  Comment: "Check eBraille, EPUB, PDF, HTML, and DAISY publications"
Name: "{group}\{cm:UninstallProgram,{#MyAppFullName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; \
  IconFilename: "{app}\CheckMate.ico"; \
  Comment: "Check eBraille, EPUB, PDF, HTML, and DAISY publications"; Tasks: desktopicon

[Registry]
; Do not set Software\Classes\.ebrl / .epub / .pdf (default) — that would steal double-click.
; Clear .ebrl default only if an older installer of this app claimed it.
Root: HKA; Subkey: "Software\Classes\.ebrl"; ValueType: string; ValueName: ""; \
  Flags: deletevalue; Tasks: fileassoc
; Remove legacy ProgIDs / shell verbs from pre-CheckMate installs
Root: HKA; Subkey: "Software\Classes\SystemFileAssociations\.ebrl\shell\eBrailleCheck"; \
  Flags: deletekey; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\SystemFileAssociations\.ebrl\shell\eBrailleValidate"; \
  Flags: deletekey; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\SystemFileAssociations\.epub\shell\eBrailleValidate"; \
  Flags: deletekey; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\SystemFileAssociations\.pdf\shell\eBrailleValidate"; \
  Flags: deletekey; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\eBrailleChecker.ebrl"; Flags: deletekey; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\eBrailleChecker.epub"; Flags: deletekey; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\eBrailleChecker.pdf"; Flags: deletekey; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\.ebrl\OpenWithProgids"; ValueType: none; \
  ValueName: "eBrailleChecker.ebrl"; Flags: deletevalue; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\.epub\OpenWithProgids"; ValueType: none; \
  ValueName: "eBrailleChecker.epub"; Flags: deletevalue; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\.pdf\OpenWithProgids"; ValueType: none; \
  ValueName: "eBrailleChecker.pdf"; Flags: deletevalue; Tasks: fileassoc
; Remove leftover SystemFileAssociations verbs from older CheckMate installs
; (HKCU stubs can linger after a per-user install; Validate now uses *\shell).
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.ebrl\shell\CheckMateValidate"; Flags: deletekey
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.epub\shell\CheckMateValidate"; Flags: deletekey
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.pdf\shell\CheckMateValidate"; Flags: deletekey
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.html\shell\CheckMateValidate"; Flags: deletekey
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.htm\shell\CheckMateValidate"; Flags: deletekey
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.xhtml\shell\CheckMateValidate"; Flags: deletekey
Root: HKLM; Subkey: "Software\Classes\SystemFileAssociations\.ebrl\shell\CheckMateValidate"; \
  Flags: deletekey; Check: IsAdminInstallMode
Root: HKLM; Subkey: "Software\Classes\SystemFileAssociations\.epub\shell\CheckMateValidate"; \
  Flags: deletekey; Check: IsAdminInstallMode
Root: HKLM; Subkey: "Software\Classes\SystemFileAssociations\.pdf\shell\CheckMateValidate"; \
  Flags: deletekey; Check: IsAdminInstallMode
Root: HKLM; Subkey: "Software\Classes\SystemFileAssociations\.html\shell\CheckMateValidate"; \
  Flags: deletekey; Check: IsAdminInstallMode
Root: HKLM; Subkey: "Software\Classes\SystemFileAssociations\.htm\shell\CheckMateValidate"; \
  Flags: deletekey; Check: IsAdminInstallMode
Root: HKLM; Subkey: "Software\Classes\SystemFileAssociations\.xhtml\shell\CheckMateValidate"; \
  Flags: deletekey; Check: IsAdminInstallMode
; --- .ebrl: Open with… (ProgID + OpenWithProgids) ---
Root: HKA; Subkey: "Software\Classes\CheckMate.ebrl"; \
  ValueType: string; ValueName: ""; ValueData: "eBraille Publication"; \
  Flags: uninsdeletekey; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\CheckMate.ebrl"; \
  ValueType: string; ValueName: "FriendlyTypeName"; ValueData: "eBraille Publication"; \
  Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\CheckMate.ebrl\DefaultIcon"; \
  ValueType: string; ValueName: ""; ValueData: "{app}\CheckMate.ico,0"; \
  Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\CheckMate.ebrl\shell\open"; \
  ValueType: string; ValueName: "FriendlyAppName"; ValueData: "{#MyAppFullName}"; \
  Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\CheckMate.ebrl\shell\open\command"; \
  ValueType: string; ValueName: ""; \
  ValueData: """{app}\{#MyAppExeName}"" ""%1"""; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\.ebrl\OpenWithProgids"; \
  ValueType: string; ValueName: "CheckMate.ebrl"; ValueData: ""; \
  Flags: uninsdeletevalue; Tasks: fileassoc
; --- .epub: Open with… (do NOT clear Classes\.epub default — other apps own it) ---
Root: HKA; Subkey: "Software\Classes\CheckMate.epub"; \
  ValueType: string; ValueName: ""; ValueData: "EPUB Publication"; \
  Flags: uninsdeletekey; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\CheckMate.epub"; \
  ValueType: string; ValueName: "FriendlyTypeName"; ValueData: "EPUB Publication"; \
  Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\CheckMate.epub\DefaultIcon"; \
  ValueType: string; ValueName: ""; ValueData: "{app}\CheckMate.ico,0"; \
  Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\CheckMate.epub\shell\open"; \
  ValueType: string; ValueName: "FriendlyAppName"; ValueData: "{#MyAppFullName}"; \
  Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\CheckMate.epub\shell\open\command"; \
  ValueType: string; ValueName: ""; \
  ValueData: """{app}\{#MyAppExeName}"" ""%1"""; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\.epub\OpenWithProgids"; \
  ValueType: string; ValueName: "CheckMate.epub"; ValueData: ""; \
  Flags: uninsdeletevalue; Tasks: fileassoc
; --- .pdf: Open with… (do NOT clear Classes\.pdf default — other apps own it) ---
Root: HKA; Subkey: "Software\Classes\CheckMate.pdf"; \
  ValueType: string; ValueName: ""; ValueData: "PDF Document"; \
  Flags: uninsdeletekey; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\CheckMate.pdf"; \
  ValueType: string; ValueName: "FriendlyTypeName"; ValueData: "PDF Document"; \
  Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\CheckMate.pdf\DefaultIcon"; \
  ValueType: string; ValueName: ""; ValueData: "{app}\CheckMate.ico,0"; \
  Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\CheckMate.pdf\shell\open"; \
  ValueType: string; ValueName: "FriendlyAppName"; ValueData: "{#MyAppFullName}"; \
  Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\CheckMate.pdf\shell\open\command"; \
  ValueType: string; ValueName: ""; \
  ValueData: """{app}\{#MyAppExeName}"" ""%1"""; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\.pdf\OpenWithProgids"; \
  ValueType: string; ValueName: "CheckMate.pdf"; ValueData: ""; \
  Flags: uninsdeletevalue; Tasks: fileassoc
; --- .html / .htm / .xhtml: Open with… (do NOT clear defaults — browsers own them) ---
Root: HKA; Subkey: "Software\Classes\CheckMate.html"; \
  ValueType: string; ValueName: ""; ValueData: "HTML Document"; \
  Flags: uninsdeletekey; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\CheckMate.html"; \
  ValueType: string; ValueName: "FriendlyTypeName"; ValueData: "HTML Document"; \
  Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\CheckMate.html\DefaultIcon"; \
  ValueType: string; ValueName: ""; ValueData: "{app}\CheckMate.ico,0"; \
  Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\CheckMate.html\shell\open"; \
  ValueType: string; ValueName: "FriendlyAppName"; ValueData: "{#MyAppFullName}"; \
  Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\CheckMate.html\shell\open\command"; \
  ValueType: string; ValueName: ""; \
  ValueData: """{app}\{#MyAppExeName}"" ""%1"""; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\.html\OpenWithProgids"; \
  ValueType: string; ValueName: "CheckMate.html"; ValueData: ""; \
  Flags: uninsdeletevalue; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\.htm\OpenWithProgids"; \
  ValueType: string; ValueName: "CheckMate.html"; ValueData: ""; \
  Flags: uninsdeletevalue; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\.xhtml\OpenWithProgids"; \
  ValueType: string; ValueName: "CheckMate.html"; ValueData: ""; \
  Flags: uninsdeletevalue; Tasks: fileassoc
; --- .svg / .css: Open with… (do NOT clear defaults) ---
Root: HKA; Subkey: "Software\Classes\CheckMate.svg"; \
  ValueType: string; ValueName: ""; ValueData: "SVG Document"; \
  Flags: uninsdeletekey; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\CheckMate.svg"; \
  ValueType: string; ValueName: "FriendlyTypeName"; ValueData: "SVG Document"; \
  Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\CheckMate.svg\DefaultIcon"; \
  ValueType: string; ValueName: ""; ValueData: "{app}\CheckMate.ico,0"; \
  Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\CheckMate.svg\shell\open"; \
  ValueType: string; ValueName: "FriendlyAppName"; ValueData: "{#MyAppFullName}"; \
  Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\CheckMate.svg\shell\open\command"; \
  ValueType: string; ValueName: ""; \
  ValueData: """{app}\{#MyAppExeName}"" ""%1"""; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\.svg\OpenWithProgids"; \
  ValueType: string; ValueName: "CheckMate.svg"; ValueData: ""; \
  Flags: uninsdeletevalue; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\CheckMate.css"; \
  ValueType: string; ValueName: ""; ValueData: "CSS Stylesheet"; \
  Flags: uninsdeletekey; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\CheckMate.css"; \
  ValueType: string; ValueName: "FriendlyTypeName"; ValueData: "CSS Stylesheet"; \
  Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\CheckMate.css\DefaultIcon"; \
  ValueType: string; ValueName: ""; ValueData: "{app}\CheckMate.ico,0"; \
  Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\CheckMate.css\shell\open"; \
  ValueType: string; ValueName: "FriendlyAppName"; ValueData: "{#MyAppFullName}"; \
  Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\CheckMate.css\shell\open\command"; \
  ValueType: string; ValueName: ""; \
  ValueData: """{app}\{#MyAppExeName}"" ""%1"""; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\.css\OpenWithProgids"; \
  ValueType: string; ValueName: "CheckMate.css"; ValueData: ""; \
  Flags: uninsdeletevalue; Tasks: fileassoc
; --- Validate with CheckMate ---
; *\shell so the verb is not tied to the current default handler (Edge/Adobe
; UserChoice on .pdf/.epub/.html). AppliesTo must be AQS with OR: a semicolon
; list (".html;.pdf;…") does not match .html, so the shortcut never appeared.
Root: HKA; Subkey: "Software\Classes\*\shell\CheckMateValidate"; \
  ValueType: string; ValueName: ""; ValueData: "Validate with CheckMate"; \
  Flags: uninsdeletekey; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\*\shell\CheckMateValidate"; \
  ValueType: string; ValueName: "Icon"; ValueData: "{app}\CheckMate.ico,0"; \
  Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\*\shell\CheckMateValidate"; \
  ValueType: string; ValueName: "AppliesTo"; \
  ValueData: "System.FileExtension:=.ebrl OR System.FileExtension:=.epub OR System.FileExtension:=.pdf OR System.FileExtension:=.html OR System.FileExtension:=.htm OR System.FileExtension:=.xhtml OR System.FileExtension:=.svg OR System.FileExtension:=.css"; \
  Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\*\shell\CheckMateValidate\command"; \
  ValueType: string; ValueName: ""; \
  ValueData: """{app}\{#MyAppExeName}"" ""%1"""; Tasks: fileassoc
; Also list under Applications\…\SupportedTypes for Open with… discovery
Root: HKA; Subkey: "Software\Classes\Applications\{#MyAppExeName}\SupportedTypes"; \
  ValueType: string; ValueName: ".ebrl"; ValueData: ""; \
  Flags: uninsdeletevalue; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\Applications\{#MyAppExeName}\SupportedTypes"; \
  ValueType: string; ValueName: ".epub"; ValueData: ""; \
  Flags: uninsdeletevalue; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\Applications\{#MyAppExeName}\SupportedTypes"; \
  ValueType: string; ValueName: ".pdf"; ValueData: ""; \
  Flags: uninsdeletevalue; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\Applications\{#MyAppExeName}\SupportedTypes"; \
  ValueType: string; ValueName: ".html"; ValueData: ""; \
  Flags: uninsdeletevalue; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\Applications\{#MyAppExeName}\SupportedTypes"; \
  ValueType: string; ValueName: ".htm"; ValueData: ""; \
  Flags: uninsdeletevalue; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\Applications\{#MyAppExeName}\SupportedTypes"; \
  ValueType: string; ValueName: ".xhtml"; ValueData: ""; \
  Flags: uninsdeletevalue; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\Applications\{#MyAppExeName}\SupportedTypes"; \
  ValueType: string; ValueName: ".svg"; ValueData: ""; \
  Flags: uninsdeletevalue; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\Applications\{#MyAppExeName}\SupportedTypes"; \
  ValueType: string; ValueName: ".css"; ValueData: ""; \
  Flags: uninsdeletevalue; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\Applications\{#MyAppExeName}"; \
  ValueType: string; ValueName: "FriendlyAppName"; ValueData: "{#MyAppFullName}"; \
  Flags: uninsdeletekeyifempty; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\Applications\{#MyAppExeName}\shell\open\command"; \
  ValueType: string; ValueName: ""; \
  ValueData: """{app}\{#MyAppExeName}"" ""%1"""; \
  Flags: uninsdeletekey; Tasks: fileassoc

[Run]
; Warm the bundled JRE once so Defender's first-run scan happens during
; install instead of the user's first check (the app warms the jars and Ace
; itself on first launch — see checkmate/warmup.py).
Filename: "{app}\runtime\bin\java.exe"; Parameters: "-version"; \
  Flags: runhidden nowait skipifdoesntexist
Filename: "{app}\{#MyAppExeName}"; \
  Description: "{cm:LaunchProgram,{#MyAppName}}"; \
  Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Remove empty leftover dirs under {app} if any; keep user app data by default
Type: filesandordirs; Name: "{app}\_internal"
Type: filesandordirs; Name: "{app}\runtime"
Type: filesandordirs; Name: "{app}\checker"
Type: filesandordirs; Name: "{app}\epubcheck"
Type: filesandordirs; Name: "{app}\verapdf"
Type: filesandordirs; Name: "{app}\ace"

[Code]
function GetAppDataDir: String;
begin
  Result := ExpandConstant('{localappdata}\{#MyAppDataName}');
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  AppDataDir: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    AppDataDir := GetAppDataDir;
    if DirExists(AppDataDir) then
    begin
      if MsgBox(
           'Also remove saved settings and any downloaded checker/EPUBCheck/veraPDF updates?' + #13#10 + #13#10 +
           AppDataDir,
           mbConfirmation, MB_YESNO) = IDYES then
      begin
        DelTree(AppDataDir, True, True, True);
      end;
    end;
  end;
end;
