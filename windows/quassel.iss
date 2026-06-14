; Inno-Setup-Skript für den Quassel-Windows-Installer.
; Bauen: Inno Setup über "iscc quassel.iss" (nach dem PyInstaller-Build).
#define MyAppName "Quassel"
#define MyAppVersion "2.4.0"
#define MyAppPublisher "Skryx-L-A"
#define MyAppURL "https://github.com/Skryx-L-A/quassel"
#define MyAppExeName "Quassel.exe"

[Setup]
; Eigene Produkt-GUID fuer Quassel (das fruehere Beta-Produkt hatte eine
; andere) — so installiert Quassel sauber nach ...\Programs\Quassel.
AppId={{5F9B9F78-80B3-4100-868E-53C6DD317F24}
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

[Messages]
; SmartScreen-Hinweis direkt auf der Willkommensseite (die exe ist noch
; nicht signiert) — zweisprachig, je nach gewählter Setup-Sprache.
english.WelcomeLabel2=This will install Quassel on your computer.%n%nQuassel is open source and not yet code-signed, so Windows SmartScreen may have warned before this installer started ("Windows protected your PC" - "More info" then "Run anyway"). That warning is expected.%n%nIt is recommended that you close all other applications before continuing.
german.WelcomeLabel2=Quassel wird auf Ihrem Computer installiert.%n%nQuassel ist Open Source und noch nicht signiert - Windows SmartScreen warnt daher eventuell, bevor dieser Installer startet ("Der Computer wurde durch Windows geschützt" - auf "Weitere Informationen" und dann "Trotzdem ausführen" klicken). Diese Warnung ist normal.%n%nEs wird empfohlen, vor dem Fortfahren alle anderen Anwendungen zu schließen.

[CustomMessages]
english.AutostartTask=Start Quassel when I log in
german.AutostartTask=Quassel beim Anmelden starten
english.LaunchApp=Launch Quassel
german.LaunchApp=Quassel starten
english.SetupStatus=Downloading the matching speech engine and one model (GPU auto-detect)...
german.SetupStatus=Lade die passende Sprach-Engine und ein Modell herunter (GPU-Erkennung)...
english.SetupStatusAll=Downloading all speech engines and all models (about 4.3 GB)...
german.SetupStatusAll=Lade alle Sprach-Engines und alle Modelle herunter (ca. 4,3 GB)...
english.RemoveData=Also delete the downloaded speech engine and models?
german.RemoveData=Auch die heruntergeladenen Sprach-Engines und Modelle löschen?
english.FullOfflineGroup=Offline use:
german.FullOfflineGroup=Offline-Nutzung:
english.FullOfflineTask=Download everything now for full offline use (all engines + all 5 models, about 4.3 GB)
german.FullOfflineTask=Jetzt alles für volle Offline-Nutzung herunterladen (alle Engines + alle 5 Modelle, ca. 4,3 GB)

[Tasks]
Name: "autostart"; Description: "{cm:AutostartTask}"; GroupDescription: "Autostart:"
; Standard AUS: der Erststart laedt sonst nur die passende Engine + EIN Modell.
Name: "fulloffline"; Description: "{cm:FullOfflineTask}"; GroupDescription: "{cm:FullOfflineGroup}"; Flags: unchecked

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
; Standard: schlank — nur die zur Hardware passende Engine + EIN Modell.
Filename: "{app}\{#MyAppExeName}"; Parameters: "--setup"; \
  StatusMsg: "{cm:SetupStatus}"; \
  Flags: waituntilterminated; Tasks: not fulloffline
; Mit Haken: alles fuer volle Offline-Nutzung (alle Engines + alle 5 Modelle).
Filename: "{app}\{#MyAppExeName}"; Parameters: "--setup --all"; \
  StatusMsg: "{cm:SetupStatusAll}"; \
  Flags: waituntilterminated; Tasks: fulloffline
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

function InitializeSetup(): Boolean;
var
  unins: String;
  rc: Integer;
begin
  // Migration: ein zuvor installiertes fruheres Beta-Produkt (frueherer
  // Produktname, andere AppId unten) still entfernen, damit kein verwaister
  // Ordner/Eintrag zurueckbleibt. Der Datenordner bleibt unangetastet.
  if RegQueryStringValue(HKCU,
      'Software\Microsoft\Windows\CurrentVersion\Uninstall\{B6F1B6B0-VOX-TYPE-0001-SKRYXLA000001}_is1',
      'UninstallString', unins) then
  begin
    unins := RemoveQuotes(unins);
    if unins <> '' then
      Exec(unins, '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART', '', SW_HIDE,
           ewWaitUntilTerminated, rc);
  end;
  Result := True;
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
