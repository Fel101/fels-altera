; Altera's per-user installer. Build with scripts/build_installer.py.
#ifndef AppVersion
  #error Build this with scripts/build_installer.py.
#endif

#define ReleaseDir "{app}\releases\" + AppVersion
#define AppName "Fel's Altera"
#define Repository "Fel101/fels-altera"

[Setup]
AppId={{304FF512-403E-4C6D-ABBD-198232D3E6E1}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppName} project
AppPublisherURL=https://github.com/{#Repository}
AppUpdatesURL=https://github.com/{#Repository}/releases
DefaultDirName={localappdata}\FelsAltera
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
SetupMutex=Local\AlteraManagedSetup
AppMutex=Local\AlteraManagedRuntime
CloseApplications=no
OutputDir={#Output}
OutputBaseFilename=FelsAltera-Setup-{#AppVersion}
SetupIconFile={#Staging}\altera.ico
UninstallDisplayIcon={app}\altera.ico
UninstallDisplayName={#AppName}
WizardStyle=modern
Compression=lzma2/fast
SolidCompression=yes
ExtraDiskSpaceRequired=500000000
SetupLogging=yes

[Tasks]
Name: "models"; Description: "Download the memory and microphone models (about 230 MB)"; Flags: unchecked
Name: "kokoro"; Description: "Install the optional local Kokoro voice (1–2 GB of packages, plus voice models)"

[Files]
Source: "{#Staging}\app\*"; DestDir: "{#ReleaseDir}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#Staging}\python\*"; DestDir: "{app}\python"; Flags: onlyifdoesntexist recursesubdirs createallsubdirs
Source: "{#Staging}\wheels\*.whl"; DestDir: "{tmp}\wheels"; Flags: ignoreversion
Source: "{#Staging}\app\scripts\windows_install.py"; DestDir: "{app}"; DestName: "launch.pyw"; Flags: ignoreversion
Source: "{#Staging}\altera.ico"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{userprograms}\{#AppName}"; Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\launch.pyw"""; WorkingDir: "{app}"; IconFilename: "{app}\altera.ico"

[Run]
Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\launch.pyw"" --from-setup"; Description: "Open {#AppName} in your browser"; Flags: postinstall nowait skipifsilent; Check: LaunchAfterSetup

[Code]
var
  TasksOpened: Boolean;
  SetupWorked: Boolean;

function OpenAfterSetup(): Boolean;
begin
  Result := ExpandConstant('{param:ALTERAUPDATE|0}') <> '1';
end;

function LaunchAfterSetup(): Boolean;
begin
  Result := SetupWorked and OpenAfterSetup();
end;

function VoiceLabInstalled(): Boolean;
var
  Key: String;
begin
  Key := 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{6F1D2A8B-5C3E-4B7A-9D21-3E8F0C4A7B15}_is1';
  Result := RegKeyExists(HKCU, Key) or RegKeyExists(HKCU32, Key);
end;

procedure CurPageChanged(Page: Integer);
var
  I: Integer;
begin
  if (Page = wpSelectTasks) and not TasksOpened then
  begin
    TasksOpened := True;
    for I := 0 to WizardForm.TasksList.Items.Count - 1 do
      if (not OpenAfterSetup()) or (VoiceLabInstalled() and (Pos('Kokoro', WizardForm.TasksList.Items[I]) > 0)) then
        WizardForm.TasksList.Checked[I] := False;
  end;
end;

function RunSetupHelper(Options: String): String;
var
  Folder, ErrorFile: String;
  Code: Integer;
  Raw: AnsiString;
begin
  Result := '';
  Folder := ExpandConstant('{app}');
  ErrorFile := Folder + '\setup-error.txt';
  DeleteFile(ErrorFile);
  if not Exec(Folder + '\python\python.exe', '-I -B "' + Folder + '\launch.pyw" ' + Options,
      Folder, SW_HIDE, ewWaitUntilTerminated, Code) or (Code <> 0) then
  begin
    if LoadStringFromFile(ErrorFile, Raw) then
      Result := String(Raw)
    else
      Result := 'Altera could not finish Setup. Try again and check setup.log in its installation folder.';
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Folder: String;
begin
  Result := '';
  Folder := ExpandConstant('{app}');
  if FileExists(Folder + '\altera.py') or DirExists(Folder + '\.git') then
    Result := 'Choose another folder. Setup uses its own installation folder.'
  else if FileExists(Folder + '\installed.json') then
    Result := RunSetupHelper('--plan {#AppVersion}');
end;

procedure CurStepChanged(Step: TSetupStep);
var
  Options, Problem: String;
begin
  if Step <> ssPostInstall then
    Exit;
  Options := '--finish {#AppVersion} --wheels "' + ExpandConstant('{tmp}\wheels') + '"';
  if WizardIsTaskSelected('models') and (not WizardSilent() or OpenAfterSetup()) then
    Options := Options + ' --models';
  if WizardIsTaskSelected('kokoro') and (not WizardSilent() or OpenAfterSetup()) then
    Options := Options + ' --voice';
  WizardForm.StatusLabel.Caption := 'Setting up Altera. Setup needs to finish this step before closing.';
  WizardForm.ProgressGauge.Style := npbstMarquee;
  Problem := RunSetupHelper(Options);
  WizardForm.ProgressGauge.Style := npbstNormal;
  SetupWorked := Problem = '';
  if not SetupWorked then
    SuppressibleMsgBox(Problem, mbError, MB_OK, IDOK);
end;

function GetCustomSetupExitCode(): Integer;
begin
  if SetupWorked then
    Result := 0
  else
    Result := 1;
end;
