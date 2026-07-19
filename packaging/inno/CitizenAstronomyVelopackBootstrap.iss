; One-time migration wrapper for legacy Inno-installed Citizen Astronomy builds.
; The legacy updater downloads this file using schema-v1 metadata and launches it
; with Inno-style silent switches. The embedded Velopack Setup becomes the new
; managed installation before the legacy install is removed.

#ifndef AppVersion
  #error AppVersion must be supplied by the publisher
#endif
#ifndef VelopackSetupPath
  #error VelopackSetupPath must be supplied by the publisher
#endif
#ifndef OutputDir
  #define OutputDir "..\dist"
#endif
#ifndef OutputBaseFilename
  #define OutputBaseFilename "CitizenAstronomyAlphaReview-" + AppVersion + "-Setup"
#endif

#define BootstrapAppId "{{B6F1E7B4-0F94-4F76-AC58-929F11F31489}"
#define LegacyAppName "Citizen Astronomy (CAst) Alpha Review"
#define MainExeName "CitizenAstronomyAlphaReview.exe"
#define VelopackSetupName ExtractFileName(VelopackSetupPath)

[Setup]
AppId={#BootstrapAppId}
AppName=Citizen Astronomy Velopack Migration
AppVersion={#AppVersion}
AppPublisher=Ogetay
DefaultDirName={tmp}\CitizenAstronomyVelopackMigration
CreateAppDir=no
CreateUninstallRegKey=no
Uninstallable=no
OutputDir={#OutputDir}
OutputBaseFilename={#OutputBaseFilename}
SetupIconFile=..\citizen_astronomy_installer.ico
Compression=lzma2/ultra64
SolidCompression=yes
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=force
CloseApplicationsFilter={#MainExeName}
RestartApplications=no
DisableWelcomePage=yes
DisableDirPage=yes
DisableProgramGroupPage=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "{#VelopackSetupPath}"; Flags: dontcopy

[Code]
var
  VelopackInstalled: Boolean;

function RunAndWait(const FileName, Parameters, WorkingDirectory: String;
  var ResultCode: Integer): Boolean;
begin
  Result := Exec(
    FileName,
    Parameters,
    WorkingDirectory,
    SW_HIDE,
    ewWaitUntilTerminated,
    ResultCode
  );
end;

procedure InstallVelopack;
var
  SetupPath: String;
  VelopackRoot: String;
  VelopackExe: String;
  LegacyRoot: String;
  LegacyUninstaller: String;
  StartMenuShortcut: String;
  DesktopShortcut: String;
  HadLegacyDesktopShortcut: Boolean;
  ResultCode: Integer;
begin
  ExtractTemporaryFile('{#VelopackSetupName}');
  SetupPath := ExpandConstant('{tmp}\{#VelopackSetupName}');
  VelopackRoot := ExpandConstant('{localappdata}\CitizenAstronomy.CAst');
  VelopackExe := AddBackslash(VelopackRoot) + '{#MainExeName}';
  LegacyRoot := ExpandConstant(
    '{localappdata}\Programs\Citizen Astronomy (CAst) Alpha Review'
  );
  LegacyUninstaller := AddBackslash(LegacyRoot) + 'unins000.exe';
  DesktopShortcut := ExpandConstant('{userdesktop}\{#LegacyAppName}.lnk');
  HadLegacyDesktopShortcut := FileExists(DesktopShortcut);

  if (not RunAndWait(SetupPath, '--silent', ExpandConstant('{tmp}'), ResultCode)) or
     (ResultCode <> 0) or (not FileExists(VelopackExe)) then
  begin
    RaiseException(
      'Velopack Setup did not complete successfully. The existing Citizen ' +
      'Astronomy installation was left unchanged.'
    );
  end;
  VelopackInstalled := True;

  if FileExists(LegacyUninstaller) then
  begin
    RunAndWait(
      LegacyUninstaller,
      '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART',
      LegacyRoot,
      ResultCode
    );
  end;

  { The old uninstaller may remove shortcuts with the same display name. }
  StartMenuShortcut := ExpandConstant(
    '{userprograms}\Citizen Astronomy (CAst).lnk'
  );
  CreateShellLink(
    StartMenuShortcut,
    'Citizen Astronomy (CAst)',
    VelopackExe,
    '',
    VelopackRoot,
    VelopackExe,
    0,
    SW_SHOWNORMAL
  );
  if HadLegacyDesktopShortcut then
  begin
    CreateShellLink(
      ExpandConstant('{userdesktop}\Citizen Astronomy (CAst).lnk'),
      'Citizen Astronomy (CAst)',
      VelopackExe,
      '',
      VelopackRoot,
      VelopackExe,
      0,
      SW_SHOWNORMAL
    );
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  VelopackExe: String;
  ResultCode: Integer;
begin
  if CurStep = ssPostInstall then
  begin
    InstallVelopack;
    if VelopackInstalled then
    begin
      VelopackExe := ExpandConstant(
        '{localappdata}\CitizenAstronomy.CAst\{#MainExeName}'
      );
      Exec(
        VelopackExe,
        '',
        ExtractFileDir(VelopackExe),
        SW_SHOWNORMAL,
        ewNoWait,
        ResultCode
      );
    end;
  end;
end;
