; 拾影 Windows 安装程序（Inno Setup 6）
; 由 scripts/release/build_exe.py --installer 驱动：
;   ISCC /DAPP_VERSION=x.y.z /DDIST_DIR=build\pyinstaller\dist scripts\release\installer\shiying.iss
; 产物：releases\拾影_v{x.y.z}_win64_setup_{日期}.exe

#define AppName "拾影"
#define AppNameEn "Shiying"
#ifndef AppVersion
#define AppVersion "0.0.0"
#endif
#ifndef DistDir
#define DistDir "..\..\build\pyinstaller\dist\拾影"
#endif
#ifndef OutputBase
#define OutputBase "拾影_v" + AppVersion + "_win64_setup"
#endif

[Setup]
AppId={{B4E7F2C0-9A31-4D6E-8F5A-C1D2E3F4A5B6}
AppName={#AppName} ({#AppNameEn})
AppVersion={#AppVersion}
AppVerName={#AppName} v{#AppVersion}
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
UninstallDisplayName={#AppName} {#AppNameEn}
UninstallDisplayIcon={app}\拾影.exe
AppPublisher={#AppName} 本地影像档案
; 便携语义的产物目录（dist/拾影）整体安装，无需额外文件变换
SetupIconFile=..\..\..\app\static\brand.ico
OutputDir=..\..\..\releases
OutputBaseFilename={#OutputBase}
Compression=lzma2/max
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
; 按用户安装：免 UAC，且运行期 data/downloads 写入安装目录不会被 UAC 虚拟化拦截，
; 与便携包「数据随程序目录」的语义保持一致。
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=commandline dialog
; 升级/卸载时若拾影正在运行，提示用户关闭（含 --serve 后端，同名 exe）
CloseApplications=yes
CloseApplicationsFilter=*.exe
RestartApplications=no
WizardStyle=modern

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Files]
Source: "{#DistDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\拾影.exe"; WorkingDir: "{app}"
Name: "{group}\停止 {#AppName}"; Filename: "{app}\停止拾影.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\拾影.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务："

[Run]
Filename: "{app}\拾影.exe"; Description: "立即启动 {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 运行期生成的缓存与窗口数据随卸载清理；任务记录(data/state)与下载(downloads)保留，
; 避免误删用户内容。彻底清理请手动删除安装目录残余的 data 与 downloads 文件夹。
Type: filesandordirs; Name: "{app}\cache"
Type: filesandordirs; Name: "{app}\data\webview"
Type: filesandordirs; Name: "{app}\data\runtime"

[Messages]
FinishedLabel=安装完成。%n%n注意：直接关闭窗口不会停止后台下载，完全退出请使用设置中的「完全退出」或「停止 拾影」。%n%n任务记录与下载文件保存在安装目录内，卸载时不会删除。
