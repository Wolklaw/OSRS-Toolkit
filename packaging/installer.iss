#define MyAppName "OSRS Toolkit"
; Passed in by build-release.ps1, which reads it from osrs_toolkit.__version__ so the
; installer can only ever be named after the application it actually contains. There is
; deliberately no fallback: a default here goes stale the moment the app is released again,
; and nothing catches it — the compile succeeds and ships an installer wearing the wrong
; version. Refusing to compile is the cheap way to find out.
#ifndef MyAppVersion
#error MyAppVersion is not defined. Build with build-release.ps1, or pass /DMyAppVersion=<version> to ISCC.
#endif
#define MyAppPublisher "OSRS Toolkit"
#define MyAppExeName "OSRS Toolkit.exe"

[Setup]
AppId={{D8518C0E-7D14-47D9-A9D8-4030E3B25DB6}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=..\release
OutputBaseFilename=OSRS-Toolkit-Setup-{#MyAppVersion}
SetupIconFile=..\assets\osrs_toolkit.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "..\dist\OSRS Toolkit\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
