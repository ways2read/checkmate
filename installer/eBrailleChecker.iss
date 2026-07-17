; Inno Setup script for eBraille Checker GUI (Windows x64)
;
; Prerequisites:
;   1. Install Inno Setup 6: https://jrsoftware.org/isinfo.php
;   2. Build the app first:
;        uv sync --extra dev
;        uv run python scripts/package.py --clean
;   3. Compile this script (ISCC or Inno Setup Compiler GUI):
;        iscc installer\eBrailleChecker.iss
;
; Output: installer\Output\eBrailleCheckerGUI-<version>-setup.exe

#define MyAppName "eBraille Checker"
#define MyAppFullName "eBraille Checker GUI"
#define MyAppVersion "0.1.0"
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
Name: "fileassoc"; Description: "Add .ebrl Open with… and ""Validate with eBraille Checker"" context menu"; GroupDescription: "File associations:"; Flags: checkedonce

[Files]
; Entire PyInstaller onedir tree (exe + _internal + runtime + checker)
Source: "..\dist\eBrailleChecker\*"; DestDir: "{app}"; \
  Flags: ignoreversion recursesubdirs createallsubdirs
; Explicit icon for Start Menu / desktop shortcuts (more reliable than exe embed)
Source: "eBrailleChecker.ico"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; \
  IconFilename: "{app}\eBrailleChecker.ico"; \
  Comment: "Check eBraille publications"
Name: "{group}\{cm:UninstallProgram,{#MyAppFullName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; \
  IconFilename: "{app}\eBrailleChecker.ico"; \
  Comment: "Check eBraille publications"; Tasks: desktopicon

[Registry]
; Do not set Software\Classes\.ebrl (default) — that would steal double-click.
; Clear it if an older installer claimed the default handler.
Root: HKA; Subkey: "Software\Classes\.ebrl"; ValueType: string; ValueName: ""; \
  Flags: deletevalue; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\SystemFileAssociations\.ebrl\shell\eBrailleCheck"; \
  Flags: deletekey; Tasks: fileassoc
; Open with… — ProgID + OpenWithProgids (not the default association)
Root: HKA; Subkey: "Software\Classes\eBrailleChecker.ebrl"; \
  ValueType: string; ValueName: ""; ValueData: "eBraille Publication"; \
  Flags: uninsdeletekey; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\eBrailleChecker.ebrl\DefaultIcon"; \
  ValueType: string; ValueName: ""; ValueData: "{app}\eBrailleChecker.ico,0"; \
  Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\eBrailleChecker.ebrl\shell\open\command"; \
  ValueType: string; ValueName: ""; \
  ValueData: """{app}\{#MyAppExeName}"" ""%1"""; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\.ebrl\OpenWithProgids"; \
  ValueType: string; ValueName: "eBrailleChecker.ebrl"; ValueData: ""; \
  Flags: uninsdeletevalue; Tasks: fileassoc
; Dedicated context menu verb
Root: HKA; Subkey: "Software\Classes\SystemFileAssociations\.ebrl\shell\eBrailleValidate"; \
  ValueType: string; ValueName: ""; ValueData: "Validate with eBraille Checker"; \
  Flags: uninsdeletekey; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\SystemFileAssociations\.ebrl\shell\eBrailleValidate\command"; \
  ValueType: string; ValueName: ""; \
  ValueData: """{app}\{#MyAppExeName}"" ""%1"""; Tasks: fileassoc

[Run]
Filename: "{app}\{#MyAppExeName}"; \
  Description: "{cm:LaunchProgram,{#MyAppName}}"; \
  Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Remove empty leftover dirs under {app} if any; keep user app data by default
Type: filesandordirs; Name: "{app}\_internal"
Type: filesandordirs; Name: "{app}\runtime"
Type: filesandordirs; Name: "{app}\checker"

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
           'Also remove saved settings and any downloaded checker updates?' + #13#10 + #13#10 +
           AppDataDir,
           mbConfirmation, MB_YESNO) = IDYES then
      begin
        DelTree(AppDataDir, True, True, True);
      end;
    end;
  end;
end;
