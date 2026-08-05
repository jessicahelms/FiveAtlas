; Inno Setup script for the FiveAtlas.
; Compiled by build_release.ps1 when Inno Setup is installed:
;     winget install JRSoftware.InnoSetup
; Or by hand:  ISCC.exe /DMyAppVersion=0.1.0 installer.iss

#ifndef MyAppVersion
  #define MyAppVersion "0.1.0"
#endif
; build_release.ps1 passes these so the build can live off C: (which is full).
#ifndef StageDir
  #define StageDir "release\FiveAtlas"
#endif
#ifndef OutDir
  #define OutDir "release"
#endif

#define MyAppName "FiveAtlas"
#define MyAppExeName "FiveAtlas.exe"

[Setup]
AppId={{7C4E1F0A-9B33-4C21-9A5E-2F6D5C8E41A7}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
DefaultDirName={autopf}\FiveAtlas
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir={#OutDir}
OutputBaseFilename=FiveAtlas-{#MyAppVersion}-setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
ArchitecturesAllowed=x64compatible
; No admin rights needed -- installs per-user, so anyone can run it on a
; managed lab machine without IT involvement.
PrivilegesRequired=lowest
UninstallDisplayIcon={app}\{#MyAppExeName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Files]
Source: "{#StageDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Start {#MyAppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; The install folder only holds the program. Edits live in %LOCALAPPDATA%\FiveAtlas
; and are deliberately LEFT BEHIND on uninstall -- they are the user's work.
Type: filesandordirs; Name: "{app}\_internal"
