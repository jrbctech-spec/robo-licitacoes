; =====================================================================
; Script Inno Setup – Instalador do Robô de Licitações (JRBC Tech)
; Gera o arquivo único: Instalador_Robo_Licitacoes.exe
;
; Como compilar:
; 1. Execute no terminal: python build.py
; 2. Abra este arquivo no Inno Setup Compiler e clique em "Compile" (Ctrl+F9)
; =====================================================================

#define MyAppName "Robô de Licitações"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "JRBC Tech"
#define MyAppURL "https://jrbc.tech"
#define MyAppExeName "robo_licitacoes.exe"

[Setup]
; GUID exclusivo da aplicação
AppId={{D37E84C1-4B9F-4917-817B-9CEF478952B1}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\Robo de Licitacoes
DefaultGroupName=Robo de Licitacoes
DisableProgramGroupPage=yes
; Pasta onde o instalador final será salvo
OutputDir=dist_installer
OutputBaseFilename=Instalador_Robo_Licitacoes
SetupIconFile=assets\icon.ico
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64compatible
DisableDirPage=no

[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
; Copia todos os arquivos da pasta gerada pelo PyInstaller (dist/robo_licitacoes)
Source: "dist\robo_licitacoes\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Arquivos adicionais de configuração
Source: ".env.example"; DestDir: "{app}"; Flags: ignoreversion
Source: "schema.sql"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\assets\icon.ico"
Name: "{group}\Desinstalar {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\assets\icon.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
