; Inno Setup script: builds dist\DecompressSetup-<version>.exe from dist\Decompress\
; Installs per-user (no admin), adds Start Menu + optional Desktop shortcuts and an uninstaller.
; Build:  iscc /DAppVersion=0.1.0 packaging\installer.iss
#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif

[Setup]
AppId={{6B0F4F7E-3C1A-4C55-9E7B-D2C0D5A1E0A1}
AppName=Decompress
AppVersion={#AppVersion}
AppPublisher=QuietDecibel
DefaultDirName={autopf}\Decompress
DefaultGroupName=Decompress
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\dist
OutputBaseFilename=DecompressSetup-{#AppVersion}
SetupIconFile=decompress.ico
UninstallDisplayIcon={app}\Decompress.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible

[Tasks]
Name: "desktopicon"; Description: "Create a &Desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "..\dist\Decompress\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion
Source: "..\dist\decompress_blender_addon.zip"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist

[Icons]
Name: "{group}\Decompress"; Filename: "{app}\Decompress.exe"; WorkingDir: "{app}"
Name: "{group}\Decompress data folder"; Filename: "{%USERPROFILE}\Decompress"
Name: "{group}\Uninstall Decompress"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Decompress"; Filename: "{app}\Decompress.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\Decompress.exe"; Description: "Launch Decompress now"; Flags: nowait postinstall skipifsilent
