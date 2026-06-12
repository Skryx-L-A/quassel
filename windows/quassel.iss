; Inno-Setup-Skript für den Quassel-Windows-Installer.
; Bauen: Inno Setup über "iscc quassel.iss" (nach dem PyInstaller-Build).
#define MyAppName "Quassel"
#define MyAppVersion "2.1.0"
#define MyAppPublisher "Skryx-L-A"
#define MyAppURL "https://github.com/Skryx-L-A/quassel"
#define MyAppExeName "Quassel.exe"

[Setup]
AppId={{B6F1B6B0-VOX-TYPE-0001-SKRYXLA000001}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
DefaultDirName={autopf}\Quassel
DefaultGroupName=Quassel
DisableProgramGroupPage=yes
OutputBaseFilename=Quassel-Setup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
SetupIconFile=..\assets\quassel.ico

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "german"; MessagesFile: "compiler:Languages\German.isl"

[CustomMessages]
english.AutostartTask=Start Quassel when I log in
german.AutostartTask=Quassel beim Anmelden starten
english.LaunchApp=Launch Quassel
german.LaunchApp=Quassel starten
english.SetupStatus=Downloading speech engine and model (GPU auto-detect)...
german.SetupStatus=Lade Sprach-Engine und Modell herunter (GPU-Erkennung)...
english.RemoveData=Also delete the downloaded speech engine and model (about 2 GB)?
german.RemoveData=Auch die heruntergeladene Sprach-Engine und das Modell löschen (ca. 2 GB)?

[Tasks]
Name: "autostart"; Description: "{cm:AutostartTask}"; GroupDescription: "Autostart:"

[Files]
Source: "dist\Quassel\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\Quassel"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\Quassel"; Filename: "{app}\{#MyAppExeName}"

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; \
  ValueType: string; ValueName: "Quassel"; ValueData: """{app}\{#MyAppExeName}"""; \
  Tasks: autostart; Flags: uninsdeletevalue

[Run]
Filename: "{app}\{#MyAppExeName}"; Parameters: "--setup"; \
  StatusMsg: "{cm:SetupStatus}"; \
  Flags: waituntilterminated
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchApp}"; \
  Flags: nowait postinstall skipifsilent

[Code]
// Quassel samt whisper-server beenden, sonst sind beim Update/Deinstallieren
// Dateien gesperrt (der Server lebt als eigener Prozess weiter).
procedure KillProcesses;
var
  R: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'),
       '/F /T /IM Quassel.exe /IM whisper-server.exe',
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
  // Engine + Modell liegen unter %LOCALAPPDATA%\Quassel (~2 GB) — auf
  // Wunsch mitlöschen, bei stiller Deinstallation unangetastet lassen.
  if (CurUninstallStep = usPostUninstall) and not UninstallSilent then
    if MsgBox(CustomMessage('RemoveData'), mbConfirmation, MB_YESNO) = IDYES then
      DelTree(ExpandConstant('{localappdata}\Quassel'), True, True, True);
end;
