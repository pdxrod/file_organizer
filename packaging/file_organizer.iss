; file_organizer (organize) — per-user Windows installer.
; Compiled by packaging/build_windows.ps1; {{VERSION}} is stamped there.
; SourceDir is the repo root (the .iss lives one level below, in build\).

#define MyAppName "file_organizer (organize)"
#define MyAppVersion "{{VERSION}}"
#define MyAppPublisher "file_organizer"
#define MyAppExeName "organize.exe"

[Setup]
AppId={{8E4B2A7C-31F6-4C5E-9D2B-0A1E7C4F6B9D}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\file_organizer
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=FileOrganizerSetup
SourceDir=..
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\organize.exe
UninstallDisplayName={#MyAppName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
; The PyInstaller onedir build: organize.exe + _internal\, plus the
; config template and the phone-pull helper staged by build_windows.ps1.
Source: "dist\organize\*"; DestDir: "{app}"; \
    Flags: ignoreversion recursesubdirs createallsubdirs

[Registry]
; Put {app} on the user's PATH (HKCU — no admin rights needed).
; A new console window picks it up; explorer-based apps after a re-login.
Root: HKCU; Subkey: "Environment"; ValueType: expandsz; ValueName: "Path"; \
    ValueData: "{olddata};{app}"; Check: NeedsAddPath('{app}'); \
    Flags: preservestringtype

[Run]
Filename: "{cmd}"; \
    Parameters: "/k echo file_organizer installed! Open a NEW PowerShell/CMD window and type: organize"; \
    Description: "&Open a console window"; \
    Flags: postinstall nowait skipifsilent

[Code]
function NeedsAddPath(Param: string): boolean;
var
  OrigPath: string;
begin
  if not RegQueryStringValue(HKEY_CURRENT_USER, 'Environment', 'Path', OrigPath) then
  begin
    Result := True;
    exit;
  end;
  Result := Pos(';' + UpperCase(Param) + ';', ';' + UpperCase(OrigPath) + ';') = 0;
end;
