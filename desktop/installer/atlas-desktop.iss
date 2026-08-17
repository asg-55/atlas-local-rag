#ifndef DesktopSource
  #error DesktopSource must point to a validated Atlas Desktop payload
#endif
#ifndef DesktopVersion
  #define DesktopVersion "0.1.0"
#endif
#ifndef DesktopOutput
  #define DesktopOutput ".\dist"
#endif

[Setup]
AppId={{1EB7D130-8BF6-4E70-96CF-253E8BFCBB31}
AppName=Atlas Desktop
AppVersion={#DesktopVersion}
AppPublisher=Atlas
DefaultDirName={localappdata}\Programs\Atlas
DefaultGroupName=Atlas
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
SetupArchitecture=x64
OutputDir={#DesktopOutput}
OutputBaseFilename=Atlas-Desktop-{#DesktopVersion}-Windows-x64
Compression=lzma2/normal
SolidCompression=no
DiskSpanning=yes
DiskSliceSize=2000000000
SlicesPerDisk=1
WizardStyle=modern
CloseApplications=force
RestartApplications=no
UsePreviousAppDir=yes
UninstallDisplayName=Atlas Desktop
Uninstallable=yes

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Создать значок на рабочем столе"; GroupDescription: "Дополнительные значки:"; Flags: unchecked

[Files]
Source: "{#DesktopSource}\*"; DestDir: "{app}"; Excludes: "\downloads\*,\validation\*"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Atlas"; Filename: "{app}\runtime\python\pythonw.exe"; Parameters: """{app}\desktop\atlas_launcher.py"" --install-dir ""{app}"""; WorkingDir: "{app}"
Name: "{autodesktop}\Atlas"; Filename: "{app}\runtime\python\pythonw.exe"; Parameters: """{app}\desktop\atlas_launcher.py"" --install-dir ""{app}"""; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\runtime\python\pythonw.exe"; Parameters: """{app}\desktop\atlas_launcher.py"" --install-dir ""{app}"""; WorkingDir: "{app}"; Description: "Запустить Atlas"; Flags: nowait postinstall skipifsilent
