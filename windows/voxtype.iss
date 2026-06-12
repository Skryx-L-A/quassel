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

[CustomMessages]
english.AutostartTask=Start VoxType when I log in
german.AutostartTask=VoxType beim Anmelden starten
english.LaunchApp=Launch VoxType
german.LaunchApp=VoxType starten
english.SetupStatus=Downloading speech engine and model (GPU auto-detect)...
german.SetupStatus=Lade Sprach-Engine und Modell herunter (GPU-Erkennung)...
english.RemoveData=Also delete the downloaded speech engine and model (about 2 GB)?
german.RemoveData=Auch die heruntergeladene Sprach-Engine und das Modell löschen (ca. 2 GB)?

[Tasks]
Name: "autostart"; Description: "{cm:AutostartTask}"; GroupDescription: "Autostart:"

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
Filename: "{app}\{#MyAppExeName}"; Parameters: "--setup"; \
  StatusMsg: "{cm:SetupStatus}"; \
  Flags: waituntilterminated
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchApp}"; \
  Flags: nowait postinstall skipifsilent

[Code]
// VoxType samt whisper-server beenden, sonst sind beim Update/Deinstallieren
// Dateien gesperrt (der Server lebt als eigener Prozess weiter).
procedure KillProcesses;
var
  R: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'),
       '/F /T /IM VoxType.exe /IM whisper-server.exe',
       '', SW_HIDE, ewWaitUntilTerminated, R);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  KillProcesses;
  Result := '';
end;

function InitializeUninstall(): Boolean;
begin
  KillProcesses;
  Result := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  // Engine + Modell liegen unter %LOCALAPPDATA%\VoxType (~2 GB) — auf
  // Wunsch mitlöschen, bei stiller Deinstallation unangetastet lassen.
  if (CurUninstallStep = usPostUninstall) and not UninstallSilent then
    if MsgBox(CustomMessage('RemoveData'), mbConfirmation, MB_YESNO) = IDYES then
      DelTree(ExpandConstant('{localappdata}\VoxType'), True, True, True);
end;
