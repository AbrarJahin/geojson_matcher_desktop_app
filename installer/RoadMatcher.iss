#define MyAppName "Road Matcher"
#define MyAppVersion "1.3.0"
#define MyAppPublisher "Road Matcher Research"
#define MyAppExeName "RoadMatcher.exe"
#define MyAppUserModelID "RoadMatcher.Research.Desktop"

[Setup]
AppId={{A7D9E3E3-47D4-42E3-AF4B-6335A476B97E}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\RoadMatcher
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=..\installer_output
OutputBaseFilename=RoadMatcher-Setup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
SetupIconFile=..\app\resources\road_matcher.ico
UninstallDisplayIcon={app}\{#MyAppExeName}

[Files]
Source: "..\dist\RoadMatcher.exe"; DestDir: "{app}"; Flags: ignoreversion

[InstallDelete]
; Remove the dependency tree left by pre-1.3 one-folder installations.
Type: filesandordirs; Name: "{app}\_internal"
Type: files; Name: "{app}\*.pyd"
Type: files; Name: "{app}\python*.dll"

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\{#MyAppExeName}"; AppUserModelID: "{#MyAppUserModelID}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\{#MyAppExeName}"; AppUserModelID: "{#MyAppUserModelID}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"; Flags: unchecked

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
