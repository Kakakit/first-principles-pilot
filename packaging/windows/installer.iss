#ifndef RuntimeDir
  #error RuntimeDir must point to the clean self-contained FPilot directory
#endif
#ifndef ReleaseVersion
  #define ReleaseVersion "1.1.0-beta.1"
#endif
#ifndef OutputDir
  #define OutputDir "."
#endif

[Setup]
AppId={{D4B8AA4B-5985-4B82-AF65-0EF945E89E25}
AppName=First Principles Pilot
AppVersion={#ReleaseVersion}
AppVerName=First Principles Pilot {#ReleaseVersion}
AppPublisher=First Principles Pilot
DefaultDirName={localappdata}\Programs\FirstPrinciplesPilot
DefaultGroupName=First Principles Pilot
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
OutputBaseFilename=FPilot-{#ReleaseVersion}-Windows-x64-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
DisableProgramGroupPage=yes
CloseApplications=no
RestartApplications=no
UninstallDisplayName=First Principles Pilot
SetupMutex=FirstPrinciplesPilotInstaller

[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; Flags: checkedonce

[Files]
Source: "{#RuntimeDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\First Principles Pilot"; Filename: "{app}\python\pythonw.exe"; Parameters: "-X utf8 ""{app}\scripts\installed_launcher.py"""; WorkingDir: "{app}"
Name: "{group}\停止工作台"; Filename: "{app}\python\pythonw.exe"; Parameters: "-X utf8 ""{app}\scripts\installed_launcher.py"" --stop"; WorkingDir: "{app}"
Name: "{group}\导出启动诊断"; Filename: "{app}\python\pythonw.exe"; Parameters: "-X utf8 ""{app}\scripts\installed_launcher.py"" --diagnostics"; WorkingDir: "{app}"
Name: "{autodesktop}\First Principles Pilot"; Filename: "{app}\python\pythonw.exe"; Parameters: "-X utf8 ""{app}\scripts\installed_launcher.py"""; WorkingDir: "{app}"; Tasks: desktopicon
Name: "{group}\卸载 First Principles Pilot"; Filename: "{uninstallexe}"

[Run]
Filename: "{app}\python\pythonw.exe"; Parameters: "-X utf8 ""{app}\scripts\installed_launcher.py"""; Description: "打开 First Principles Pilot"; Flags: nowait postinstall skipifsilent

[Code]
function ApplicationClosed(): Boolean;
var PythonPath: String; ExitCode: Integer;
begin
  PythonPath := ExpandConstant('{app}\python\python.exe');
  Result := True;
  if FileExists(PythonPath) then
    Result := Exec(PythonPath, '-X utf8 "' + ExpandConstant('{app}\scripts\installed_launcher.py') + '" --can-update',
      ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, ExitCode) and (ExitCode = 0);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  if not ApplicationClosed() then
    Result := '工作台仍在运行。请先使用“停止工作台”或控制页的“退出控制中心”，等待提交回执完成后再更新。';
end;

function InitializeUninstall(): Boolean;
begin
  Result := ApplicationClosed();
  if not Result then
    MsgBox('请先停止工作台并退出控制中心，再卸载。项目数据会保留。', mbInformation, MB_OK);
end;
