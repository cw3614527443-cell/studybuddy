; StudyBuddy 安装包脚本（Inno Setup 6）
; 编译：ISCC.exe installer\studybuddy.iss
; 前置：先执行 python -m PyInstaller build.spec --distpath dist_v110 --workpath build_v110
;
; 设计取舍：
; - 按用户安装（PrivilegesRequired=lowest）到 %LOCALAPPDATA%\Programs\StudyBuddy，
;   不需要管理员权限，学校/公用电脑也能装，卸载走 HKCU 不污染系统。
; - 用户数据（登录态）不放安装目录，放 %LOCALAPPDATA%\StudyBuddy，
;   由程序读取 installed.flag 判定；这样卸载不会误删登录态，重装免扫码。

#define MyAppName "StudyBuddy 学习助手"
#define MyAppVersion "1.7.2"
#define MyAppPublisher "cw3614527443-cell"
#define MyAppURL "https://github.com/cw3614527443-cell/studybuddy"
#define MyAppExeName "StudyBuddy.exe"
#define SrcDir "..\dist_v172\StudyBuddy"

[Setup]
AppId={{9B2F4C71-6E3A-4D58-9C0B-7A1E53B8D204}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
AppUpdatesURL={#MyAppURL}/releases
DefaultDirName={localappdata}\Programs\StudyBuddy
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\installer_out
OutputBaseFilename=StudyBuddy-Setup-{#MyAppVersion}
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
MinVersion=10.0
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
RestartApplications=no
LicenseFile=..\LICENSE
AllowNoIcons=yes
DisableWelcomePage=no

[Messages]
; Inno 6 不自带简体中文语言包（官方只有 Unofficial 版，下载源不稳定），
; 这里直接覆盖 Default.isl 的可见文案，做到向导全中文。
SetupAppTitle=安装程序
SetupWindowTitle=安装 %1
ExitSetupTitle=退出安装
ExitSetupMessage=安装尚未完成。现在退出将不会安装该程序。%n%n确定退出？
WelcomeLabel1=欢迎使用 [name] 安装向导
WelcomeLabel2=即将在你的电脑上安装 [name]。%n%n建议先关闭其他正在运行的程序，尤其是浏览器。
ClickNext=点「下一步」继续，或点「取消」退出安装。
WizardLicense=许可协议
LicenseLabel=请阅读以下重要信息。
LicenseLabel3=请阅读下面的许可协议（MIT）。继续安装前需要接受该协议。
LicenseAccepted=我接受此协议(&A)
LicenseNotAccepted=我不接受此协议(&D)
WizardSelectDir=选择安装位置
SelectDirDesc=[name] 要安装到哪里？
SelectDirLabel3=安装程序将把 [name] 装到下面的文件夹。
SelectDirBrowseLabel=点「下一步」继续；如需更换位置，点「浏览」。
WizardSelectTasks=选择附加任务
SelectTasksDesc=需要执行哪些附加任务？
SelectTasksLabel2=选择安装时附带的附加任务，然后点「下一步」。
WizardReady=准备安装
ReadyLabel1=安装程序已准备好，即将开始安装 [name]。
ReadyLabel2a=点「安装」开始；如需重新检查设置，点「上一步」。
ReadyMemoDir=安装位置：
ReadyMemoTasks=附加任务：
WizardPreparing=准备安装
PreparingDesc=安装程序正在准备安装 [name]…
WizardInstalling=正在安装
InstallingLabel=正在安装 [name]，请稍候…
ExtractingLabel=正在解压文件…
StatusExtractFiles=正在解压文件…
StatusCreateIcons=正在创建快捷方式…
StatusRunProgram=正在完成安装…
FinishedHeadingLabel=[name] 安装完成
FinishedLabel=[name] 已成功安装。%n%n首次启动请点「① 启动浏览器 / 登录」完成登录。%n%n登录态保存在 {localappdata}\StudyBuddy，卸载时不会删除，重装后无需重新登录。
ClickFinish=点「完成」退出安装程序。
DirExistsTitle=文件夹已存在
DirExists=文件夹：%n%n%1%n%n已存在。仍要安装到该文件夹吗？
DirDoesntExistTitle=文件夹不存在
DirDoesntExist=文件夹：%n%n%1%n%n不存在。要创建它吗？
DiskSpaceWarningTitle=磁盘空间不足
DiskSpaceWarning=安装至少需要 %1 KB 可用空间，但所选磁盘只剩 %2 KB。%n%n仍要继续吗？
ButtonNext=下一步(&N) >
ButtonBack=< 上一步(&B)
ButtonInstall=安装(&I)
ButtonCancel=取消
ButtonFinish=完成(&F)
ButtonBrowse=浏览(&B)…
ButtonWizardBrowse=浏览(&R)…
ButtonYes=是(&Y)
ButtonNo=否(&N)
ConfirmUninstall=确定要完全卸载 %1 吗？%n%n登录态与日志保存在 {localappdata}\StudyBuddy，不会被删除。
UninstallStatusLabel=正在卸载 %1，请稍候…
UninstalledAll=%1 已从你的电脑上移除。
UninstalledMost=%1 卸载完成。%n%n部分文件未能移除，可手动删除。

[CustomMessages]
CreateDesktopIcon=创建桌面快捷方式(&D)
AdditionalIcons=附加快捷方式：
UninstallProgram=卸载 StudyBuddy
LaunchProgram=立即运行 StudyBuddy

[Languages]
Name: "chs"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#SrcDir}\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SrcDir}\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "installed.flag"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\CHANGELOG.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
; 显式写 {userprograms}/{userdesktop}：即使安装器被提升权限运行，也不会往
; C:\ProgramData 写快捷方式（那需要管理员权限，普通用户会失败）
Name: "{userprograms}\{#MyAppName}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{userprograms}\{#MyAppName}\{cm:UninstallProgram}"; Filename: "{uninstallexe}"
Name: "{userdesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}\_internal"
Type: files; Name: "{app}\installed.flag"
; 卸载后清掉空的开始菜单文件夹（Inno 只在「本次创建的文件夹」为空时自动删，重装场景会残留空目录）
Type: dirifempty; Name: "{userprograms}\{#MyAppName}"
