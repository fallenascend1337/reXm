; reXm Windows installer. Build after PyInstaller:  ISCC packaging\reXm.iss
; Installs per-user (no admin prompt); profiles and log in %APPDATA%\reXm are kept on uninstall.

#define AppVersion "1.0.2"

[Setup]
AppId={{6E7C2B1A-4D8F-4E3B-9A51-7F2C0D9E5B13}
AppName=reXm
AppVersion={#AppVersion}
AppVerName=reXm {#AppVersion}
AppPublisher=kurushimu
AppPublisherURL=mailto:fallen4500x@outlook.com
AppComments=Control app for Sony WH/WF-1000XM headphones
DefaultDirName={localappdata}\Programs\reXm
DefaultGroupName=reXm
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\dist\installer
OutputBaseFilename=reXm-Setup-{#AppVersion}
SetupIconFile=..\rexm.ico
UninstallDisplayIcon={app}\reXm.exe
UninstallDisplayName=reXm
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
CloseApplications=force
RestartApplications=no

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"
Name: "startup"; Description: "Start reXm with &Windows (in the tray)"; GroupDescription: "Startup:"; Flags: unchecked

[Files]
Source: "..\dist\reXm\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\LICENSE"; DestDir: "{app}"; DestName: "LICENSE.txt"; Flags: ignoreversion
Source: "..\THIRD_PARTY_NOTICES.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\reXm"; Filename: "{app}\reXm.exe"; Comment: "Sony XM headset control"
Name: "{autodesktop}\reXm"; Filename: "{app}\reXm.exe"; Tasks: desktopicon
; same name the app's own "Start with Windows" switch uses, so the two stay in sync
Name: "{userstartup}\reXm"; Filename: "{app}\reXm.exe"; Parameters: "--background"; Tasks: startup

[Run]
Filename: "{app}\reXm.exe"; Description: "Launch reXm"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{sys}\taskkill.exe"; Parameters: "/f /im reXm.exe"; Flags: runhidden; RunOnceId: "KillReXm"

[UninstallDelete]
; created by the in-app "Start with Windows" switch rather than by setup
Type: files; Name: "{userstartup}\reXm.lnk"

