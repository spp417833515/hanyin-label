; 汉印标签打印 安装包 (Inno Setup 6)
; 当前用户安装, 不需要管理员权限; 用户数据在 %LOCALAPPDATA%\HanyinLabel\data, 卸载时保留

#define AppName "汉印标签打印"
#define AppVersion "1.0.0"
#define AppExe "HanyinLabel.exe"

[Setup]
AppId={{8F3C2A51-6B7E-4D2A-9C1F-3E5B7A9D2C41}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
DefaultDirName={localappdata}\Programs\HanyinLabel
PrivilegesRequired=lowest
DisableProgramGroupPage=yes
DisableDirPage=auto
OutputDir=output
OutputBaseFilename=汉印标签打印-安装-{#AppVersion}
SetupIconFile=..\web\favicon.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=no

[Languages]
Name: "chs"; MessagesFile: "ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加选项："
Name: "autostart"; Description: "开机自动启动（在右下角托盘运行，局域网 API 随时可用）"; GroupDescription: "附加选项："

[Files]
Source: "dist\HanyinLabel\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autoprograms}\卸载 {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "HanyinLabelPrint"; ValueData: """{app}\{#AppExe}"" --tray"; Tasks: autostart

[Run]
Filename: "{app}\{#AppExe}"; Description: "立即启动 {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{sys}\taskkill.exe"; Parameters: "/F /IM {#AppExe}"; Flags: runhidden; RunOnceId: "StopApp"
Filename: "{sys}\reg.exe"; Parameters: "delete HKCU\Software\Microsoft\Windows\CurrentVersion\Run /v HanyinLabelPrint /f"; Flags: runhidden; RunOnceId: "RemoveAutostart"

[Code]
// 安装 / 升级前先关掉正在运行的程序 (托盘里那个), 否则文件被占用
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Code: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM {#AppExe}', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Sleep(800);
  Result := '';
end;
