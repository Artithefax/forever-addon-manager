; Windows-Setup für Leeroyzan (Inno Setup 6)
; Wird automatisch vom GitHub-Workflow gebaut:  ISCC.exe /DAppVersion=1.2.3 installer.iss
; Installiert ohne Adminrechte nach %LOCALAPPDATA%\Programs\Leeroyzan – Python wird nicht benötigt,
; es steckt bereits in Leeroyzan.exe. Einstellungen (%APPDATA%\Leeroyzan) bleiben beim Deinstallieren erhalten.
; Das Setup fragt nach der Sprache (Deutsch oder English) und gibt sie an Leeroyzan weiter.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
; Die AppId muss gleich bleiben (auch in leeroyzan.py: INSTALLER_APP_ID), sonst gilt jedes Setup als neues Programm.
AppId={{8F3C6A52-6B1E-4C59-9C3E-6E2B1C4D9A77}
AppName=Leeroyzan
AppVersion={#AppVersion}
AppVerName=Leeroyzan {#AppVersion}
AppPublisher=Leeroyzan contributors
AppPublisherURL=https://github.com/Artithefax/leeroyzan
AppSupportURL=https://github.com/Artithefax/leeroyzan/issues
AppUpdatesURL=https://github.com/Artithefax/leeroyzan/releases
DefaultDirName={autopf}\Leeroyzan
DefaultGroupName=Leeroyzan
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
OutputDir=dist
OutputBaseFilename=Leeroyzan-Setup
SetupIconFile=leeroyzan.ico
UninstallDisplayIcon={app}\Leeroyzan.exe
UninstallDisplayName=Leeroyzan
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
ShowLanguageDialog=yes

[Languages]
; Der Name (de/en) landet in setup.ini und ist zugleich der Sprachcode in Leeroyzan.
Name: "de"; MessagesFile: "compiler:Languages\German.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "dist\Leeroyzan.exe"; DestDir: "{app}"; Flags: ignoreversion

[INI]
; Leeroyzan startet in der hier gewählten Sprache (später jederzeit unter Einstellungen änderbar).
Filename: "{userappdata}\Leeroyzan\setup.ini"; Section: "Setup"; Key: "Language"; String: "{language}"

[Icons]
Name: "{autoprograms}\Leeroyzan"; Filename: "{app}\Leeroyzan.exe"
Name: "{autodesktop}\Leeroyzan"; Filename: "{app}\Leeroyzan.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Leeroyzan.exe"; Description: "{cm:LaunchProgram,Leeroyzan}"; Flags: nowait postinstall skipifsilent
