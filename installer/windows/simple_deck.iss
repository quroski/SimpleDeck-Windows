; ============================================================================
;  Simple Deck - Inno Setup script
;  Buduje instalator .exe dla Windows (10/11 x64)
;  ----------------------------------------------------------------------------
;  Wymaga zbudowania PyInstallerem wcześniej (patrz build.ps1).
;
;  Kompilacja z linii komend:
;     ISCC.exe simple_deck.iss
;
;  W VS Code Inno Setup:  F6 (Compile)
; ============================================================================

#define MyAppName           "Simple Deck"
#ifndef MyAppVersion
  #define MyAppVersion      "1.0.3a"
#endif
#define MyAppPublisher      "GREJEM INDUSTRIES"
#define MyAppURL            "https://github.com/grejem-industries/grejem-os"
#define MyAppExeName        "Simple-Deck.exe"
#define MyAppDescription    "Simple Deck - Stream Deck Controller"

[Setup]
; AppId - GUID generowany RAZ, nie zmieniać między wersjami (stabilny identyfikator
; do wykrywania istniejącej instalacji przy upgrade). D11 fix: wygenerowany stabilny.
AppId={{F9CDE1D2-96E9-4A44-AE36-FBCB7012CB1C}

AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
AppContact={#MyAppPublisher}

; Default install path: Program Files\Simple Deck
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppPublisher}

; D5 fix: OutputDir zgodne z build.ps1 (szuka w installer\windows\output\, nie installer\output\)
OutputDir=output
OutputBaseFilename=Simple-Deck-Setup-{#MyAppVersion}

; Kompresja - LZMA2 Ultra daje najlepszy ratio (PySide6 jest spory)
Compression=lzma2/ultra64
SolidCompression=yes
LZMAUseSeparateProcess=yes

; Estetyka - nowoczesny wizard z gradientami
WizardStyle=modern
WizardSizePercent=120,120
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64compatible
ArchitecturesAllowed=x64compatible

; Ikony
SetupIconFile=..\..\installer\icons\simple_deck.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}

; Pozostałe
DisableProgramGroupPage=yes
DisableDirPage=no
DisableReadyPage=no
LicenseFile=LICENSE.txt
InfoBeforeFile=BEFORE.txt
InfoAfterFile=AFTER.txt
CloseApplications=force
RestartApplications=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "polish";  MessagesFile: "compiler:Languages\Polish.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "startupicon"; Description: "Uruchom przy &starcie systemu"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
; QuickLaunch usunięty (Windows 7+ nie ma paska Szybkiego uruchamiania)

; === Pliki aplikacji - skopiuj wszystko co PyInstaller wypluł ===
[Files]
; D9 fix: usunięto 'uninsneveruninstall' - kolidowało z [UninstallDelete] {app}
; które i tak kasuje cały katalog. Pozostawienie obu flag było mylące.
Source: "dist\Simple-Deck\*"; DestDir: "{app}"; \
    Flags: recursesubdirs createallsubdirs ignoreversion

; Ikony dla Add/Remove Programs (reszta bundle'owana przez PyInstaller)
Source: "..\..\installer\icons\simple_deck_256.png"; DestDir: "{app}\icons"; Flags: ignoreversion

; === Skróty ===
[Icons]
; Start Menu (folder wydawcy)
Name: "{group}\{#MyAppName}";         Filename: "{app}\{#MyAppExeName}"; \
    IconFilename: "{app}\{#MyAppExeName}"; \
    Comment: "{#MyAppDescription}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"

; Pulpit (opcjonalnie)
Name: "{commondesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; \
    Tasks: desktopicon; \
    IconFilename: "{app}\{#MyAppExeName}"

; Autostart (opcjonalnie)
Name: "{commonstartup}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; \
    Tasks: startupicon; \
    IconFilename: "{app}\{#MyAppExeName}"; \
    WorkingDir: "{app}"

; === Rejestr Windows - informacje o aplikacji ===
[Registry]
; Add/Remove Programs entry (dopełnienie Inno Setup)
Root: HKLM; Subkey: "SOFTWARE\GREJEM INDUSTRIES\Simple Deck"; \
    ValueType: string; ValueName: "InstallPath"; ValueData: "{app}"; \
    Flags: uninsdeletekey
Root: HKLM; Subkey: "SOFTWARE\GREJEM INDUSTRIES\Simple Deck"; \
    ValueType: string; ValueName: "Version"; ValueData: "{#MyAppVersion}"; \
    Flags: uninsdeletekey
; Dodatkowo: po odinstalowaniu usuń pusty klucz nadrzędny "GREJEM INDUSTRIES"
Root: HKLM; Subkey: "SOFTWARE\GREJEM INDUSTRIES"; \
    Flags: uninsdeletekeyifempty noerror

; USB HID nie wymaga sterownika - Windows ma go wbudowany (hid.dll / hidclass.sys)

; === Akcje po instalacji ===
[Run]
; Uruchom aplikację po instalacji (opcjonalnie, jeśli nie cichy)
Filename: "{app}\{#MyAppExeName}"; \
    Description: "{cm:LaunchProgram,{#MyAppName}}"; \
    Flags: nowait postinstall skipifsilent unchecked

; === Akcje przy deinstalacji ===
[UninstallDelete]
; Skasuj katalog aplikacji całkowicie (jeśli są resztki)
Type: filesandordirs; Name: "{app}"

; === Code section - niestandardowa logika ===
[Code]
function InitializeSetup(): Boolean;
begin
    Result := True;
end;

function NeedRestart(): Boolean;
begin
    Result := False;
end;
