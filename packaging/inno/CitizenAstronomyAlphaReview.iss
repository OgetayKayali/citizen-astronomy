; Citizen Astronomy private alpha-review installer.
; Wraps the tested one-folder PyInstaller bundle only.

#define AppName "Citizen Astronomy (CAst) Alpha Review"
#define AppExeName "CitizenAstronomyAlphaReview.exe"
#define AppPublisher "Ogetay"
#define AppURL "https://ogetay.com/citizen-astronomy-cast"
#ifndef AppVersion
  #define AppVersion "0.1.1-alpha.1"
#endif
#ifndef OutputBaseFilename
  #define OutputBaseFilename "CitizenAstronomyAlphaReview-" + AppVersion + "-Setup"
#endif
; Paths are relative to packaging/inno/.
#define BundleRoot "..\..\_tmp_alpha_review_dist\CitizenAstronomyAlphaReview"
#define OutputDir "..\dist"

[Setup]
AppId={{A4D6F2B1-7C93-4E2A-9B61-3F8E5D0C1A72}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
DefaultDirName={localappdata}\Programs\Citizen Astronomy (CAst) Alpha Review
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir={#OutputDir}
OutputBaseFilename={#OutputBaseFilename}
SetupIconFile=..\citizen_astronomy_installer.ico
UninstallDisplayIcon={app}\{#AppExeName}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64
CloseApplications=force
CloseApplicationsFilter={#AppExeName}
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "{#BundleRoot}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\README_ALPHA_REVIEW.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\ALPHA_REVIEW_NOTICE.txt"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\README - Alpha Review"; Filename: "{app}\README_ALPHA_REVIEW.txt"
Name: "{group}\Alpha Review Notice"; Filename: "{app}\ALPHA_REVIEW_NOTICE.txt"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent; Check: IsInteractiveInstall
Filename: "{app}\{#AppExeName}"; Flags: nowait runasoriginaluser; Check: IsUpdateInstall

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Code]
function IsUpdateInstall: Boolean;
var
  Index: Integer;
begin
  Result := False;
  for Index := 1 to ParamCount do
  begin
    if CompareText(ParamStr(Index), '/UPDATE=1') = 0 then
    begin
      Result := True;
      Exit;
    end;
  end;
end;

function IsInteractiveInstall: Boolean;
begin
  Result := not IsUpdateInstall;
end;
