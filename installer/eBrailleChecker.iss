; Inno Setup script for eBraille Checker GUI (Windows x64)
;
; Prerequisites:
;   1. Install Inno Setup 6: https://jrsoftware.org/isinfo.php
;   2. Build the app first (bundles Temurin JRE, eBraille Checker, EPUBCheck, veraPDF):
;        uv sync --extra dev
;        uv run python scripts/package.py --clean
;      Or one-shot: powershell -ExecutionPolicy Bypass -File scripts\build_installer.ps1
;   3. Compile this script (ISCC or Inno Setup Compiler GUI):
;        iscc installer\eBrailleChecker.iss
;
; The [Files] section ships the full dist\eBrailleChecker\ tree, including:
;   runtime\   (JRE), checker\ (eBraille Checker), epubcheck\ (W3C EPUBCheck),
;   verapdf\   (veraPDF CLI)
;
; Output: installer\Output\eBrailleCheckerGUI-<version>-setup.exe

#define MyAppName "eBraille Checker"
#define MyAppFullName "eBraille Checker GUI"
#define MyAppVersion "0.3.0"
#define MyAppPublisher "ways2read"
#define MyAppURL "https://github.com/ways2read/ebraille-checker-gui"
#define MyAppExeName "eBrailleChecker.exe"
; Keep in sync with application data folder name (app/paths.py)
#define MyAppDataName "eBrailleCheckerGUI"
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
OutputBaseFilename=eBrailleCheckerGUI-{#MyAppVersion}-setup
SetupIconFile=eBrailleChecker.ico
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
Name: "fileassoc"; Description: "Add .ebrl/.epub/.pdf Open with… and ""Validate with eBraille Checker"" context menu"; GroupDescription: "File associations:"; Flags: checkedonce

[Files]
; Entire PyInstaller onedir tree (exe + _internal + runtime + checker + epubcheck + verapdf)
Source: "..\dist\eBrailleChecker\*"; DestDir: "{app}"; \
  Flags: ignoreversion recursesubdirs createallsubdirs
; Explicit icon for Start Menu / desktop shortcuts (more reliable than exe embed)
Source: "eBrailleChecker.ico"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; \
  IconFilename: "{app}\eBrailleChecker.ico"; \
  Comment: "Check eBraille, EPUB, and PDF publications"
Name: "{group}\{cm:UninstallProgram,{#MyAppFullName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; \
  IconFilename: "{app}\eBrailleChecker.ico"; \
  Comment: "Check eBraille, EPUB, and PDF publications"; Tasks: desktopicon

[Registry]
; Do not set Software\Classes\.ebrl / .epub / .pdf (default) — that would steal double-click.
; Clear .ebrl default only if an older installer of this app claimed it.
Root: HKA; Subkey: "Software\Classes\.ebrl"; ValueType: string; ValueName: ""; \
  Flags: deletevalue; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\SystemFileAssociations\.ebrl\shell\eBrailleCheck"; \
  Flags: deletekey; Tasks: fileassoc
; --- .ebrl: Open with… (ProgID + OpenWithProgids) ---
Root: HKA; Subkey: "Software\Classes\eBrailleChecker.ebrl"; \
  ValueType: string; ValueName: ""; ValueData: "eBraille Publication"; \
  Flags: uninsdeletekey; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\eBrailleChecker.ebrl"; \
  ValueType: string; ValueName: "FriendlyTypeName"; ValueData: "eBraille Publication"; \
  Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\eBrailleChecker.ebrl\DefaultIcon"; \
  ValueType: string; ValueName: ""; ValueData: "{app}\eBrailleChecker.ico,0"; \
  Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\eBrailleChecker.ebrl\shell\open"; \
  ValueType: string; ValueName: "FriendlyAppName"; ValueData: "{#MyAppFullName}"; \
  Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\eBrailleChecker.ebrl\shell\open\command"; \
  ValueType: string; ValueName: ""; \
  ValueData: """{app}\{#MyAppExeName}"" ""%1"""; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\.ebrl\OpenWithProgids"; \
  ValueType: string; ValueName: "eBrailleChecker.ebrl"; ValueData: ""; \
  Flags: uninsdeletevalue; Tasks: fileassoc
; --- .ebrl: context menu "Validate with eBraille Checker" ---
Root: HKA; Subkey: "Software\Classes\SystemFileAssociations\.ebrl\shell\eBrailleValidate"; \
  ValueType: string; ValueName: ""; ValueData: "Validate with eBraille Checker"; \
  Flags: uninsdeletekey; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\SystemFileAssociations\.ebrl\shell\eBrailleValidate\command"; \
  ValueType: string; ValueName: ""; \
  ValueData: """{app}\{#MyAppExeName}"" ""%1"""; Tasks: fileassoc
; --- .epub: Open with… (do NOT clear Classes\.epub default — other apps own it) ---
Root: HKA; Subkey: "Software\Classes\eBrailleChecker.epub"; \
  ValueType: string; ValueName: ""; ValueData: "EPUB Publication"; \
  Flags: uninsdeletekey; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\eBrailleChecker.epub"; \
  ValueType: string; ValueName: "FriendlyTypeName"; ValueData: "EPUB Publication"; \
  Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\eBrailleChecker.epub\DefaultIcon"; \
  ValueType: string; ValueName: ""; ValueData: "{app}\eBrailleChecker.ico,0"; \
  Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\eBrailleChecker.epub\shell\open"; \
  ValueType: string; ValueName: "FriendlyAppName"; ValueData: "{#MyAppFullName}"; \
  Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\eBrailleChecker.epub\shell\open\command"; \
  ValueType: string; ValueName: ""; \
  ValueData: """{app}\{#MyAppExeName}"" ""%1"""; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\.epub\OpenWithProgids"; \
  ValueType: string; ValueName: "eBrailleChecker.epub"; ValueData: ""; \
  Flags: uninsdeletevalue; Tasks: fileassoc
; --- .epub: context menu (same app; routes to stock EPUBCheck) ---
Root: HKA; Subkey: "Software\Classes\SystemFileAssociations\.epub\shell\eBrailleValidate"; \
  ValueType: string; ValueName: ""; ValueData: "Validate with eBraille Checker"; \
  Flags: uninsdeletekey; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\SystemFileAssociations\.epub\shell\eBrailleValidate\command"; \
  ValueType: string; ValueName: ""; \
  ValueData: """{app}\{#MyAppExeName}"" ""%1"""; Tasks: fileassoc
; --- .pdf: Open with… (do NOT clear Classes\.pdf default — other apps own it) ---
Root: HKA; Subkey: "Software\Classes\eBrailleChecker.pdf"; \
  ValueType: string; ValueName: ""; ValueData: "PDF Document"; \
  Flags: uninsdeletekey; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\eBrailleChecker.pdf"; \
  ValueType: string; ValueName: "FriendlyTypeName"; ValueData: "PDF Document"; \
  Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\eBrailleChecker.pdf\DefaultIcon"; \
  ValueType: string; ValueName: ""; ValueData: "{app}\eBrailleChecker.ico,0"; \
  Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\eBrailleChecker.pdf\shell\open"; \
  ValueType: string; ValueName: "FriendlyAppName"; ValueData: "{#MyAppFullName}"; \
  Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\eBrailleChecker.pdf\shell\open\command"; \
  ValueType: string; ValueName: ""; \
  ValueData: """{app}\{#MyAppExeName}"" ""%1"""; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\.pdf\OpenWithProgids"; \
  ValueType: string; ValueName: "eBrailleChecker.pdf"; ValueData: ""; \
  Flags: uninsdeletevalue; Tasks: fileassoc
; --- .pdf: context menu (same app; routes to veraPDF PDF/UA) ---
Root: HKA; Subkey: "Software\Classes\SystemFileAssociations\.pdf\shell\eBrailleValidate"; \
  ValueType: string; ValueName: ""; ValueData: "Validate with eBraille Checker"; \
  Flags: uninsdeletekey; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\SystemFileAssociations\.pdf\shell\eBrailleValidate\command"; \
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
Root: HKA; Subkey: "Software\Classes\Applications\{#MyAppExeName}"; \
  ValueType: string; ValueName: "FriendlyAppName"; ValueData: "{#MyAppFullName}"; \
  Flags: uninsdeletekeyifempty; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\Applications\{#MyAppExeName}\shell\open\command"; \
  ValueType: string; ValueName: ""; \
  ValueData: """{app}\{#MyAppExeName}"" ""%1"""; \
  Flags: uninsdeletekey; Tasks: fileassoc

[Run]
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
