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
#define MyAppUrl "https://github.com/Wolklaw/OSRS-Toolkit"

[Setup]
AppId={{D8518C0E-7D14-47D9-A9D8-4030E3B25DB6}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
; Windows shows these three as links in Apps & features. Without them the entry is a name
; and a size, which is what an application nobody can look up looks like.
AppPublisherURL={#MyAppUrl}
AppSupportURL={#MyAppUrl}/issues
AppUpdatesURL={#MyAppUrl}/releases/latest
AppCopyright=Copyright (C) 2026 Wolklaw. Licensed under the GNU GPL v3.
; Stamps the setup executable itself with a version resource. An unsigned installer that
; also records no version at all is a step worse than an unsigned one that does.
VersionInfoVersion={#MyAppVersion}
VersionInfoCompany={#MyAppPublisher}
VersionInfoProductName={#MyAppName}
VersionInfoDescription={#MyAppName} Setup
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
UninstallDisplayName={#MyAppName} {#MyAppVersion}
OutputDir=..\release
OutputBaseFilename=OSRS-Toolkit-Setup-{#MyAppVersion}
SetupIconFile=..\assets\osrs_toolkit.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
LicenseFile=..\LICENSE
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; The modern style hides the welcome page by default, on the reasoning that it says
; nothing. It says something now.
DisableWelcomePage=no
; Inno picks the closest size to the display's scaling; see tools/make_installer_art.py.
WizardImageFile=wizard\sidebar-164x314.bmp,wizard\sidebar-192x386.bmp,wizard\sidebar-246x459.bmp,wizard\sidebar-328x628.bmp,wizard\sidebar-410x797.bmp
WizardSmallImageFile=wizard\badge-55x55.bmp,wizard\badge-64x68.bmp,wizard\badge-92x97.bmp,wizard\badge-110x116.bmp,wizard\badge-138x140.bmp,wizard\badge-164x161.bmp
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
PrivilegesRequired=lowest
; "commandline" is what lets an in-app update install itself back into the same place the
; running copy came from — see updater.silent_install_arguments, which passes /CURRENTUSER
; or /ALLUSERS to match. Without it an update of a machine-wide install would quietly
; retarget itself at the user's own folder and leave two copies installed.
PrivilegesRequiredOverridesAllowed=dialog commandline
; Two setups unpacking into the same folder at once is worth refusing outright.
SetupMutex={#MyAppName}-Setup-Mutex
; Restart Manager finds a running copy and closes it rather than failing on a locked file.
; Relaunching afterwards is left to the [Run] entries below, which know whether this was an
; in-app update or somebody double-clicking the installer.
CloseApplications=yes
RestartApplications=no

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
; Someone who ran the installer themselves gets the usual tick-box.
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
; An in-app update closed the application to replace it, so it is this installer's job to
; put it back. Guarded by an explicit flag rather than by "was this silent", so that a
; scripted silent install for a deployment does not start the app on someone's desktop.
Filename: "{app}\{#MyAppExeName}"; Flags: nowait; Check: RelaunchRequested

[Code]
function RelaunchRequested: Boolean;
begin
  Result := ExpandConstant('{param:RELAUNCH|0}') = '1';
end;
