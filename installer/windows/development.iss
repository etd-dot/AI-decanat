#ifndef PayloadRoot
  #define PayloadRoot GetEnv('DECANAT_PAYLOAD_ROOT')
#endif
#ifndef OutputRoot
  #define OutputRoot GetEnv('DECANAT_INSTALLER_ROOT')
#endif
#if PayloadRoot == ''
  #error Missing verified payload root
#endif

[Setup]
AppId={{C97AAD9A-44C2-4459-A8A7-BA6DF598FB93}
AppName=ИИ деканата — демонстрационная версия
AppVersion=0.1.0
AppPublisher=Проект ИИ-деканата
DefaultDirName={localappdata}\Programs\AI-decanat-Development
DefaultGroupName=ИИ деканата — демонстрация
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.22000
AppMutex=Local\AI-decanat-development
OutputDir={#OutputRoot}
OutputBaseFilename=AI-decanat-development-setup
Compression=none
SolidCompression=no
DiskSpanning=yes
DiskSliceSize=1500000000
WizardStyle=modern
InfoBeforeFile={#PayloadRoot}\DEVELOPMENT-PREVIEW.txt
UninstallDisplayName=ИИ деканата — демонстрационная версия
SetupLogging=yes

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Files]
Source: "{#PayloadRoot}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\ИИ деканата — демонстрация"; Filename: "{app}\Decanat.exe"; WorkingDir: "{app}"
Name: "{group}\Удалить демонстрационную версию"; Filename: "{uninstallexe}"

[Run]
Filename: "{app}\Decanat.exe"; Description: "Открыть пробную версию"; Flags: nowait postinstall skipifsilent

; User configuration, knowledge and journal live outside {app}; uninstall does
; not recursively delete the user's data or their independent Codex account.
