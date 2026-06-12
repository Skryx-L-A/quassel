; Inno-Setup-Skript für den VoxType-Windows-Installer.
; Bauen: Inno Setup über "iscc voxtype.iss" (nach dem PyInstaller-Build).
#define MyAppName "VoxType"
#define MyAppVersion "2.1.0"
#define MyAppPublisher "Skryx-L-A"
#define MyAppURL "https://github.com/Skryx-L-A/voxtype"
#define MyAppExeName "VoxType.exe"

[Setup]
AppId={{B6F1B6B0-VOX-TYPE-0001-SKRYXLA000001}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
DefaultDirName={autopf}\VoxType
DefaultGroupName=VoxType
DisableProgramGroupPage=yes
OutputBaseFilename=VoxType-Setup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
SetupIconFile=..\assets\voxtype.ico

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "german"; MessagesFile: "compiler:Languages\German.isl"

[Tasks]
Name: "autostart"; Description: "Start VoxType when I log in"; GroupDescription: "Autostart:"

[Files]
Source: "dist\VoxType\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\VoxType"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\VoxType"; Filename: "{app}\{#MyAppExeName}"

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; \
  ValueType: string; ValueName: "VoxType"; ValueData: """{app}\{#MyAppExeName}"""; \
  Tasks: autostart; Flags: uninsdeletevalue

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch VoxType"; \
  Flags: nowait postinstall skipifsilent
