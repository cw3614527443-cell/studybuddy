# StudyBuddy · 智慧职教学习助手

> 带图形界面的 Windows 桌面工具，用于批量处理智慧职教（icve）平台的课程资源进度与学时统计。
> 基于 Python + tkinter + Playwright（CDP 接管本机 Edge），可打包成免安装程序。

## ⚠️ 免责声明

本项目仅供**个人学习、浏览器自动化技术研究**使用。

- 使用本工具产生的任何后果（包括但不限于平台判定异常、成绩与学时无效、账号受限）由使用者自行承担。
- 自动化刷课/刷学时可能违反学校规定与平台用户协议。**请自行判断是否使用**，作者不鼓励也不为此背书。
- 工具不采集、不上传任何账号信息。登录态仅保存在本机 `userdata/` 目录，且已被 `.gitignore` 排除。
- 请勿将本工具用于代刷、售卖等商业或代他人操作场景。

## 功能

| 按钮 | 作用 | 是否真播视频 |
|---|---|---|
| ① 启动浏览器 / 登录 | 启动受控 Edge（独立 profile，登录态持久化，只需扫码一次） | — |
| ② 读取观看时长 | 读取账号的「观看时长 / 学习次数」 | — |
| ③ 刷课（一键过完） | 逐个打开未完成资源：视频拖进度条到结尾、文档翻到末页 | 否，约 8 秒/个 |
| ④ 刷观看时长 | 连续真实播放视频（1x 从头播满整段），累计到目标学时 | 是，必须真播 |
| ⑤ 复核完成情况 | 展开全部模块，统计已完成资源数 | — |
| ❓ 功能说明 | 界面内置的用法解释 | — |

**③ 与 ④ 的目标不同，别混用**：刷课只求目录打勾，可以拖进度条；刷学时是累计首页那个「观看时长」数字，平台只认真实播放时长，拖进度条不计入。

## 支持的两个平台

同一家（智慧职教）的两套界面，看浏览器地址栏选：

| 选项 | 网址 | 页面特征 | 支持 |
|---|---|---|---|
| AI 优课 | `ai.icve.com.cn` | 首页是课程卡片，卡片上写「观看时长 / 学习次数」 | ②③④⑤ |
| zjy2 | `zjy2.icve.com.cn` | 左侧「章节 → 小节 → 资源」树，资源后带百分比 | ③⑤（无学时统计） |

选错的表现是「进入课程失败」或「资源 0 项」，换另一个选项即可。

## 快速开始

### 环境要求

- Windows 10 / 11
- **已安装 Microsoft Edge**（程序复用系统 Edge，不打包浏览器内核）
- Python 3.9+（仅源码运行与打包需要，使用发布版不需要）

### 方式一：安装包（推荐）

下载 `StudyBuddy-Setup-x.y.z.exe`，双击按向导安装。特点：

- **不需要管理员权限**，按用户安装到 `%LOCALAPPDATA%\Programs\StudyBuddy`
- 自动创建开始菜单快捷方式，桌面快捷方式可选
- 自带卸载程序（设置 → 应用，或开始菜单里的「卸载 StudyBuddy」）
- 卸载**不会删除**登录态（存放在 `%LOCALAPPDATA%\StudyBuddy`），重装免扫码

> 程序无数字签名，SmartScreen 会拦一次，点「更多信息 → 仍要运行」即可。

### 方式二：免安装包（绿色版）

下载 `StudyBuddy-x.y.z-win64.zip`，解压后双击 `StudyBuddy.exe`。

**整个文件夹**（含 `_internal/`）必须一起复制，exe 单独拷走会闪退。数据（登录态、截图、日志）就放在 exe 旁边，删目录即彻底清除。

| | 安装版 | 免安装版 |
|---|---|---|
| 管理员权限 | 不需要 | 不需要 |
| 快捷方式 | 自动创建 | 手动 |
| 卸载 | 控制面板卸载 | 删文件夹 |
| 数据位置 | `%LOCALAPPDATA%\StudyBuddy` | exe 同目录 |
| 卸载后登录态 | **保留**，重装免登录 | 随目录删除 |

### 源码运行

```bash
pip install -r requirements.txt
python main.py
```

或双击 `启动.bat`。

首次运行 Playwright 若提示缺少驱动，执行一次：

```bash
python -m playwright install-deps   # 非必需，仅用系统 Edge 时可跳过
```

### 打包

```bash
# 1. 打包主程序，产物在 dist/StudyBuddy/
python -m PyInstaller build.spec --noconfirm --clean

# 2. 打包安装包（需先装 Inno Setup 6：winget install JRSoftware.InnoSetup）
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer\studybuddy.iss
# 产物在 installer_out/StudyBuddy-Setup-x.y.z.exe
```

`installer/studybuddy.iss` 里的 `MyAppVersion` 与 `SrcDir` 需与 `config.py` 的 `APP_VERSION`、PyInstaller 输出目录保持一致。

> 注意：`StudyBuddy.exe` 不能单独拷贝，必须与 `_internal/` 同目录。程序无数字签名，首次运行 Windows SmartScreen 会拦一次，点「更多信息 → 仍要运行」即可。

## 使用流程

1. 点「① 启动浏览器 / 登录」，在弹出的 Edge 窗口里登录（二维码或账号密码）。
2. 点「② 读取观看时长」，看数字是否与手机上一致，确认登录的是自己的号。
3. 点「③ 刷课」把目录里的资源全部打勾。
4. 需要学时再挂「④ 刷观看时长」，填目标小时数与最长挂机分钟数。
5. 点「⑤ 复核完成情况」看统计。任何时候可点「停止当前任务」，已完成项不会回退。

## 工作原理

```
tkinter GUI
   │  EventBus（队列，跨线程）
   ▼
TaskRunner（每任务一个线程）
   │
   ├── EdgeProc：subprocess 拉起 msedge.exe --remote-debugging-port=9222
   │             只负责启动 / 探活 / 关闭，不持有 Playwright 对象
   │
   └── Browser：本线程 connect_over_cdp(9222) → 操作页面 → finally 断开
```

三个关键设计决策，都是踩坑换来的：

1. **浏览器必须是独立进程，不能交给 Playwright 托管。** Playwright 同步 API 有线程亲和性，跨线程复用连接必然 `TargetClosedError`；每个任务自建 CDP 连接、任务结束断开，浏览器与登录态都不受影响。
2. **断开连接只能用 `pw.stop()`，不能用 `browser.close()`。** 对 `connect_over_cdp()` 得到的 Browser，`close()` 的语义是**关闭整个浏览器**。
3. **关闭浏览器要优雅。** 先发 WM_CLOSE 等 6 秒再强杀，否则 cookie 来不及落盘，下次还得重新扫码。

平台适配层位于 `core/platforms/`，全部靠 DOM 选择器驱动，**平台改版即失效**——这是本项目最大的外部依赖风险。

## 目录结构

```
StudyBuddy/
├── main.py                入口（含 --selftest 无头自检）
├── config.py              路径、站点、端口、数据目录解析
├── core/
│   ├── browser.py         Edge 进程管理 + CDP 连接 + 截图 + 弹窗识别
│   ├── events.py          事件总线
│   ├── tasks.py           任务编排（线程池 + 停止控制）
│   └── platforms/
│       ├── ai_icve.py     AI 优课适配器
│       └── zjy2.py        zjy2 适配器
├── ui/app.py              tkinter 界面
├── installer/
│   ├── studybuddy.iss     Inno Setup 安装脚本
│   └── installed.flag     安装版标记（由安装器写入程序目录）
├── assets/                应用图标（icon.ico 7 档尺寸）
├── tools/make_icon.py     图标生成脚本（纯几何绘制，不依赖字体）
├── tests/                 回归测试（重连 / 拖进度条 / 续播弹窗）
├── build.spec             PyInstaller 配置
├── CHANGELOG.md           更新历史
└── 启动.bat / 打包.bat
```

运行时自动生成（已 gitignore）：

- `userdata/` — Edge profile，**含登录态，勿提交、勿外发**
- `shots/` — 每步操作截图，排障用
- `logs/` — 日志（`--selftest` 结果也写在这里）

这三者默认位于程序目录；安装版位于 `%LOCALAPPDATA%\StudyBuddy`。可用环境变量 `SB_DATA_DIR` 整体覆盖，`portable.txt` 强制便携模式。

## 常见问题

**Q：日志出现 `code 4008` / 视频播放失败？**
播放期间不要切走焦点、不要另开标签页读数据。程序已改为播放期间不做页面跳转。

**Q：报 `TargetClosedError`？**
旧版本（≤1.0.0）的线程复用 bug。升级到 1.0.4+ 已修复。

**Q：点了停止，时长为 `None`？**
1.0.6 已修复：收尾读取不再受停止标志影响，读不到时会说明「本次播放 N 分钟，点②可刷新」而非拼出 `None`。

**Q：复核显示 0/0？**
1.0.6 已修复：复核前会先进入课程页。若仍为 0，多半是平台改版导致选择器失效。

**Q：9222 端口被占用？**
同一时间只允许一个受控浏览器实例。可设 `SB_CDP_PORT=9333` 跑隔离实例（自检默认这样做，不会抢你正在用的浏览器）。

**Q：安装时需要管理员权限吗？装到 Program Files 会不会写不进登录态？**
不需要管理员权限，安装版固定在用户目录（`%LOCALAPPDATA%\Programs\StudyBuddy`），登录态另存于 `%LOCALAPPDATA%\StudyBuddy`。即便手动装到 `Program Files`，程序检测到目录不可写也会自动把数据改存到 `%LOCALAPPDATA%`。

**Q：卸载后登录态还在？想彻底清除怎么办？**
这是有意设计——卸载不删登录态，重装直接可用。要彻底清除，删掉 `%LOCALAPPDATA%\StudyBuddy` 整个目录即可。

## 开发

版本号在 `config.py` 的 `APP_VERSION`，每次改动需同步写入 `CHANGELOG.md`。

```bash
python -m py_compile main.py config.py core/*.py core/platforms/*.py ui/*.py
python main.py --selftest          # 无头自检，结果写入数据目录 logs/selftest_result.txt
```

`.vscode/` 内有现成的 F5 调试配置与打包任务。

回归测试需要本机已登录的 Edge profile，通过 `SB_USERDATA` 指定：

```bash
SB_USERDATA="C:/path/to/profile" python tests/test_modal.py
```

## License

[MIT](LICENSE)
