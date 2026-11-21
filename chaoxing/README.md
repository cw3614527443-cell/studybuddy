> **StudyBuddy 集成说明**：本目录是从 `chaoxing-suite-v1.4` 原样收进来的已验证脚本套件，
> 下面的「命令行用法」仍然成立。在 StudyBuddy 里不用自己开命令行、也不用装 Node / npm：
> 选平台「学习通」→ 点 ① 扫码登录 → ②/③/⑤/⑥ 即在软件内调用本套件。
> 软件会复用自带的 Node 与 playwright-core，并把脚本输出转发到软件日志；
> 浏览器 profile 放在 `<数据目录>/userdata/chaoxing/`。以下原文保留供查阅与排障。

# 学习通刷课脚本套件（chaoxing-suite）

一套用**有头浏览器**（窗口真实显示在桌面）驱动学习通的脚本，覆盖：登录、进课程、解析目录、看视频任务点、做章节测验。

> 版本：v1.4（2026-11-21 去除本机硬编码路径、补齐 `--help`，全链路验收通过）  
> 适用范围：超星学习通 mooc2 课程页 / 单章节学习页  
> 运行环境：Windows + Node 22 + Python 3.13 + playwright-core（见「环境依赖」）

---

## 一、设计原则

1. **有头运行**：`headless: false`，浏览器窗口必须真实可见。学习通对「页面不可离开/最小化」有硬性要求，无头模式既容易被判异常，也不方便人工监督。
2. **人机分工**：登录扫码、答案决策由人负责；翻页、播放、填答、提交由脚本负责。
3. **每步可回溯**：关键动作前后都截图，落在 `shots/`；目录状态、播放进度、填答结果都落 JSON 到 `io/`。
4. **不查外部题库**：答案只来自课程自身内容（扩展阅读原文、常识判断）或人工提供的答案表。
5. **已完成即跳过**：目录里显示绿勾（已完成）的条目一律不重复刷。

---

## 二、目录结构

```
browser/
├─ suite/                 ← 本套件（可整体打包）
│  ├─ agent.mjs           浏览器常驻驱动（Node）
│  ├─ client.py           通信客户端 + 通用操作
│  ├─ catalog.py          解析章节目录 / 任务点状态
│  ├─ video.py            视频任务点：1 倍速播到结尾 + 回写复核 + 收尾盘点
│  ├─ quiz.py             章节测验：采题 / 填答 / 提交 / 读分（手工给答案）
│  ├─ exam.py             章节测验三段式：导出题目→外部 AI 作答→导入答案自动填答
│  ├─ chapter_quiz.py     章节内嵌测验：同章多个 workId 逐个导出/填答/提交
│  ├─ run.py              统一命令行入口
│  ├─ start.cmd           Windows 启动驱动
│  └─ README.md           本文件
├─ a2/  a3/ ...           ← 每个账号一个独立会话目录（profile/io/shots）
└─ node_modules/          playwright-core
```

**会话目录**（如 `a3/`）存放：

| 子目录 | 内容 |
|---|---|
| `profile/` | 浏览器配置与登录态（cookie 在 `profile/Default/Network/Cookies`），换账号必须换目录，否则会串登录态 |
| `io/` | 命令通道 `cmd.json`/`result.json`、`catalog.json`、`session.json`、各章节采集结果 |
| `shots/` | 各步骤截图 |

---

## 三、各文件作用

### 1. `agent.mjs` —— 浏览器常驻驱动

- 用 `chromium.launchPersistentContext(profile, { headless:false })` 打开一个**真实窗口**（1500×940，位置 60,40）。
- 启动后自动打开 `https://v8.chaoxing.com/` 并截一张 `01-login.png`。
- 之后进入**命令轮询循环**：读 `io/cmd.json`，执行，写 `io/result.json`（带同一个 `id`）。
- 支持的指令：`status / titles / goto / reload / wait / shot / eval / text / html / frames / click / fill / scroll / bring / select / newtab / close / focusemu / mouse / winstate`。
- 会话目录由环境变量 `BROWSER_ROOT` 决定；不设时默认是**本脚本所在 `suite/` 的上一级**（即 `browser/`），换机器解压后无需改代码。多账号用 `start.cmd a3` 这类方式指定子目录。
- 浏览器内核自动查找：优先环境变量 `CHROME_PATH`，其次 `%LOCALAPPDATA%\ms-playwright\` 下版本号最高的 chromium，最后系统 Chrome。

> 注意：`eval` 的代码约定是**函数体**（用 `return` 返回值）。有 `frameUrl` 时驱动内部用 `new Function(code)` 执行；无 `frameUrl` 时走 CDP，由 `client.js()` 自动包一层 `(function(){...})()`。

### 2. `client.py` —— 通信客户端

其他脚本都通过它发指令，不直接碰浏览器。

| 方法 | 作用 |
|---|---|
| `send / ok` | 发一条指令并等待结果（超时保护） |
| `js(code, frame=None)` | 在指定框架或主文档执行 JS（函数体写法） |
| `text / html` | 取框架或主文档文本 / HTML |
| `frames()` | 列出当前页所有框架 URL |
| `shot(name)` | 截图到 `shots/` |
| `click(selector/text/frame)` | 点击元素或按文字点击 |
| `locate_course()` | **自动把 page_index 切到课程页**（`studentstudy` 或 `mycourse/stu`） |
| `find_page_with_frame / find_page_with_url` | 在多标签里定位目标页 |
| `fmt(sec)` | 秒转 `m:ss` |

### 3. `catalog.py` —— 目录解析

解析课程目录，输出 `io/catalog.json`：

```json
{"progress": {"done": 73, "total": 134},
 "items": [{"id":"705057922","no":"17.1","title":"...","done":false,"pending":1}],
 "pending": [...], "pending_points": 61}
```

**兼容两种页面结构**（这是踩过的坑）：

| 页面 | 目录位置 | 已完成标记 | 未完成标记 |
|---|---|---|---|
| 课程目录页 | iframe `mycourse/studentcourse` | `.catalog_state.icon_yiwanc` | `.catalog_jindu .bntHoverTips`「N个待完成任务点」 |
| 单章节学习页 | 主文档 `#cur<id>` | `.icon_Completed` | `.prevHoverTips`「N个待完成任务点」 |

单章节学习页没有「已完成任务点 x/y」表头，此时沿用上一次解析到的进度值。

### 4. `video.py` —— 视频任务点（无人值守版）

流程：`截图 → 点开章节 → 探测 <video>（并踢一次 play 触发加载）→ 1.0 倍播放 → 守到视频结尾 → 等 12s 平台回写 → reload 最多 3 次复核目录 → 判定 done`。

#### 最重要的一条：判完成只看「视频绿标」，不看右边目录

页面结构（实测，别再靠文字猜）：

```html
<div class="ans-attach-ct videoContainer ans-job-finished">      <!-- 已完成 -->
  <div class="ans-job-icon ans-job-video" aria-label="任务点已完成">
```

- **唯一依据**：`.ans-attach-ct.videoContainer.ans-job-finished`（绿标）。
  它既不在 `innerText` 里，也不是普通文字节点，是 `aria-label` + 容器类名，所以文字匹配一律无效。
- **右侧目录橙色 ≠ 视频没刷完**。橙色只说明「本章节还有任务点没做完」，基本都是章节测验没答。
  实测 7.1 / 8.1 / 9.1 / 10.1：视频上方已绿，右侧仍是橙色，因为 2 个任务点里的测验没做。
  用目录判完成会导致反复重刷已经看过的视频（这版就是为了修这个）。
- 打开章节先读绿标，有就跳过、不播放；播放途中一旦出现绿标也立刻收工（平台按累计时长记账，有时没播满就记够了）。

播放侧仍然守到结尾（`ratio=0.999` 或 `ended`）：观看时长按累计记账、服务端回写有 10s 级延迟，
实测 17.3 播到 96% 时服务端只记了 76s，补齐到结尾并等 12s 绿标才出现。

#### 反「失焦即暂停」（学习通的检测特效）

平台会在**鼠标移出窗口 / 浏览器失去焦点 / 窗口被遮挡**时自动暂停视频。三层一起解决：

| 层 | 做法 | 位置 |
|---|---|---|
| Chrome 启动参数 | `--disable-background-timer-throttling`、`--disable-backgrounding-occluded-windows`、`--disable-renderer-backgrounding`、`--disable-features=CalculateNativeWinOcclusion` | `agent.mjs` |
| CDP 焦点模拟 | `Emulation.setFocusEmulationEnabled(true)`，页面永远认为自己是 focused | `agent.mjs` 的 `focusemu` |
| 页面内补丁 | 篡改 `document.hasFocus / hidden / visibilityState`；捕获阶段吞掉 `blur`、`visibilitychange`、`mouseleave`；每 2.5s 发 `mousemove` 心跳；视频被暂停 1.5s 内自动拉回 | `video.py` 的 `GUARD_JS` / `install_guard()` |
| 真实输入 | 每 60s 用 CDP 真实移动鼠标（只移动不点击）+ `bringToFront` | `video.py` 的 `keepalive()` |

补丁只在当前文档有效，所以**每次打开章节后都要重装**（`run_one` 里已自动调用）。

实测：装好补丁后把窗口最小化 40 秒，视频照常前进了 40.9 秒，且平台正常记账、任务点正常变绿。

#### 关键实现

- `_WALK`：跨 iframe 递归收集所有 `<video>`，深度上限 6 + window 去重——不加限制会掉进 `about:blank` 死循环（踩过）。
- `wait_videos()`：**点播放之前 `duration` 一直是 `NaN`**，必须先 `play()` 踢一脚才会去拉流。所以探测到时长为 0 就点一次播放再继续轮询（这是本轮新修的坑，否则永远读到 `0:00`）。
- `probe()`：返回同页**全部**播放器的 `{d, c, p, e, r}`，同一章节挂多段视频时按顺序逐个播完。
- `watch()`：每 5s 读一次进度；`paused` 立即恢复；倍速被改回去立刻压回 1.0；每 30s 打一行进度，方便确认没卡。
- 停滞自愈：连续 30s 没进度 → `bring` 切前台 + 关掉「继续观看/知道了/确定」弹窗 + 重新 play；连续 120s 判卡死，放弃并在汇总里标 `timeout`。
- `video_marks()`：跨 iframe 扫描 `.ans-attach-ct.videoContainer`，返回 `videos / finished`，是完成判定的唯一来源。
- `audit()`：只读巡检，逐个开章节读绿标，把「目录橙色但视频其实已刷完」的章节挑出来（结果写 `io/audit.json`）。
- 断点续跑：`io/video-progress.json`（每次运行覆盖）与 `io/video-done.json`（只有目录确认 `done` 才写），重跑自动跳过已完成。

#### 用法

```bash
python video.py --list                    # 只列待完成且有任务点的条目，几秒钟
python video.py --audit                   # 巡检：哪些章节视频其实已经刷完了（只读，约 7s/章）
python video.py --check=7.1,8.1           # 只校验指定章节的绿标
python video.py 17.1                      # 单个章节
python video.py 17.1,17.6,17.7            # 多个
python video.py --pending --skip-quiz     # 批量：跳过标题像答题的条目（推荐）
python video.py --pending --skip-quiz --limit=10   # 只跑前 10 个，跑完续跑
python video.py 17.3 --dry                # 只探测时长/是否可播，探测完自动暂停
python video.py --pending --max=60 --retry=2       # 单条最多守 60 分钟，失败重试 2 次
```

| 参数 | 说明 |
|---|---|
| `--list` | 按目录盘点，不播放 |
| `--audit` | 逐个开章节读绿标，输出真实待刷清单（`io/audit.json`） |
| `--check=编号` | 只校验指定章节绿标 |
| `--shuffle` | 随机顺序跑（压测各类视频；配 `--seed=N` 可复现） |
| `--only-todo` | 只跑 `--audit` 判定「视频未完成」的条目 |
| `--pending` | 跑目录里所有待完成条目 |
| `--skip-quiz` | 按标题跳过 Quiz/测验/作业（批量刷视频时用；标题含 `Test` 的 6.2 会被误伤，但它本来已完成） |
| `--limit=N` | 本轮最多跑 N 个 |
| `--dry` | 探测模式，不真的守着播 |
| `--ratio=0.999` | 应急调低完成阈值，正常不要动 |
| `--max=分钟` / `--retry=N` | 单条超时上限 / 失败重试次数 |

#### 产出

- `io/video-progress.json`：每条 `status` ∈ `done / unconfirmed / timeout / novideo / openfail / skip-done / dry`。
  **`unconfirmed` = 视频播完了但目录没变绿**，会保留在待办里下次重播，不会假完成。
- `io/video-done.json`：已确认完成的断点表。
- `io/remaining.json`：跑完自动盘点，把剩下的分成「疑似还有视频」和「需要人工答题」两栏——**答题脚本解决不了的部分，这里会直接列给你**。

#### 退出码

`0` 全部达标 / `2` 有 timeout、openfail、unconfirmed / `3` 驱动没起来。

### 5. `quiz.py` —— 章节测验

流程：`点开章节 → 切「章节测验」标签 → 采集题目 → 按答案表填答 → 提交 → 读分`。

- 答题页在 iframe（URL 含 `doHomeWorkNew`）内，题目节点 `.singleQuesId`，选项 `li[aria-label]`。
- 判断题两个选项（对/错），单选题四个选项；`--answers=1,3,2,4,1` 里数字是**选项序号（从 1 开始）**。
- 提交：点 `.btnSubmit` → 弹窗「确认提交？」→ 点 `#popok` 确认。
- **平台限制**：批阅页只显示自己的答案和每题得分，不显示正确答案；`/work/retest` 重做接口返回「作答状态或作答次数已变化」，成绩基本不可改。所以提交前必须确认答案。

用法：
```bash
python quiz.py --list                                   # 列出带 Quiz 的待完成章节
python quiz.py 11.6                                     # 只采集题目，存 io/quiz-11-6.json
python quiz.py 11.6 --answers=1,1,2,3,1 --dry           # 填答但不提交（安全演练）
python quiz.py 11.6 --answers=1,1,2,3,1                 # 填答并提交，读回分数
```

### 6. `exam.py` —— 测验三段式（导出 → 外部 AI 作答 → 导入自动填答）

这是**推荐的答题方式**。`quiz.py` 要求人肉把答案写成 `--answers=1,3,2,4,1`，题一多就不现实；
`exam.py` 把「取题」和「答题」拆开，中间的作答交给任意 AI。

```
① export   脚本抓题（含阅读理解原文）→ 一个固定格式的文件
② 作答      你把文件丢给任意 AI（另一个聊天窗、别的模型都行）→ AI 回一份答案集
③ check    离线校验答案覆盖率，不碰浏览器（缺题会整卷跳过）
④ answer   脚本导入答案集 → 自动逐题填答 → 校验 → 提交 → 读分
```

#### ① 导出

```bash
python exam.py export --pending          # 全部待完成测验
python exam.py export --no=11.6,11.7     # 指定章节
python exam.py export --pending --limit=3   # 先导 3 套看看
```

一次导出会生成 3 个文件到 `io/exports/`：

| 文件 | 给谁 | 内容 |
|---|---|---|
| `exam-<时间>.md` | **给 AI / 给人看** | 全部题目 + 阅读材料 + 作答要求 + 输出格式 |
| `exam-<时间>.json` | 脚本内部 | 同内容的机器格式（`format: chaoxing-exam-v1`） |
| `answers-template-<时间>.json` | 可选 | 答案骨架，`answer` 留空，AI 也直接填这个 |

`latest.json` 记录最近一次导出的三个路径。

#### ② 固定格式（这段是约定，改要同步改脚本）

**导出的题目格式** `chaoxing-exam-v1`：

```json
{
  "format": "chaoxing-exam-v1",
  "course": "大学英语口语",
  "count": 1,
  "papers": [
    {
      "no": "11.6",
      "title": "Quiz",
      "questions": [
        {"q": 1, "type": "single",
         "passage": "阅读材料原文，没有则为空串",
         "stem": "题干文本",
         "options": [{"label": "A", "text": "选项内容"}]}
      ]
    }
  ]
}
```

`type` 取值：`single` 单选 / `multi` 多选 / `judge` 判断 / `blank` 填空 / `short` 简答。

**AI 要返回的答案格式** `chaoxing-answers-v1`：

```json
{
  "format": "chaoxing-answers-v1",
  "answers": [
    {"no": "11.6", "q": 1, "answer": "B"},
    {"no": "11.6", "q": 2, "answer": ["A", "C"]},
    {"no": "11.6", "q": 3, "answer": "对"},
    {"no": "11.6", "q": 4, "answer": ["第一个空", "第二个空"]}
  ]
}
```

规则：

| 题型 | `answer` 写法 | 兼容写法 |
|---|---|---|
| single | `"B"` | `"b"`、`"B."`、`"B、xxx"` 都会归一化 |
| multi | `["A","C"]` | `"A,C"` / `"AC"` 也认 |
| judge | `"对"` / `"错"` | `T/F`、`true/false`、`√/×`、`是/否` 都认 |
| blank | 单个文本或数组（多空按顺序） | 数组长度不足只填空着的那些 |
| short | 文本 | — |

`no` 是章节编号（`11.6`），`q` 是题号（从 1 开始）。**这两个必须和导出文件对得上**，
所以导出的 md 里每题都标了 `### 第 N 题`，AI 照抄即可。

#### ③ 校验答案覆盖率（离线，不需要浏览器）

拿到 AI 回的答案文件后，**先跑这一步**，确认没有漏题再导入：

```bash
python exam.py check answers.json
```

它会对照最近一次导出（`io/exports/latest.json`）报告：题目总数、已作答数、答案为空 / 答案里根本没有的题号、以及多余条目。
全覆盖才会打印 `✓ 全覆盖，可以导入`；有缺口时导入会**整卷跳过**那套题（除非加 `--force`），避免漏答一题交上去拿 0 分。
AI 有时会把答案包在 ```json 代码块里，这个文件解析也认。

遇到「阅读理解题但课文没抓到」时，可以 dump 那一章的原始 HTML 来诊断：

```bash
python exam.py inspect 11.6     # 写 io/exports/../inspect-11-6.html + 截图，并打印题目结构
```

#### ④ 导入并填答

```bash
python exam.py answer answers.json            # 只填答 + 截图，不提交（默认）
python exam.py answer answers.json --submit   # 填答并提交，读回分数
python exam.py answer answers.json --only=11.6 --submit
python exam.py answer answers.json --force    # 有题缺答案也强行提交（默认拒绝）
```

安全设计（都是实测踩出来的）：

- **默认不提交**。填完只截图 `shots/ans-<章节>-1-filled.png`，你先看一眼再 `--submit`。
- **缺答案就拒绝提交**：少一题就整卷跳过，避免「漏答一题交上去拿 0 分」。
- **没点中就不提交**：填答后回读每题 `input` 值和选中项，有题没填上会列出来并中止。
- 提交前后各一张截图，分数写 `io/quiz-results.json`。

#### 采集实现的关键点

- 题目容器 `.singleQuesId`（在 iframe `doHomeWorkNew` 内）。
- **阅读材料不在题干里**：它是题目容器**前面的一段兄弟节点**，所以采集时从题目往前扫
  `previousElementSibling`，扫到上一题为止，拼起来作为 `passage`。
  另外存一份原始文本块兜底，避免材料被漏掉。
- 题型判定不靠文字猜：
  `input[type=checkbox]` 存在 → 多选；两个选项是「对/错」→ 判断；
  有 `li[aria-label]` → 单选；有文本输入框 → 填空。
- 选项字母优先从 `li` 文本的 `A、xxx` 前缀取，取不到用 `aria-label`，再不行按顺序 `A/B/C/D`。

### 7. `chapter_quiz.py` —— 章节内嵌测验（一个章节里多个测验）

`exam.py` 处理的是目录里单独一行的 "Quiz"（如 11.6）。但 7.1/8.1/9.1/10.1 这类章节，
点开后有「1 视频 / 2 章节测验」标签，切到章节测验后页面里**同时挂着两个** `doHomeWorkNew`
iframe（一个 4 道判断题、一个 5 道单选），`exam.py` 只会采第一个、漏掉第二个。

本脚本按 URL 里的 `workId` 精确定位每个测验，题目编号写成 `7.1#56141956`，答案集用同样编号：

```bash
python chapter_quiz.py export --nos=7.1,8.1,9.1,10.1   # 导出全部内嵌测验
python chapter_quiz.py check answers.json               # 离线校验（复用 exam.check_answers）
python chapter_quiz.py answer answers.json --submit     # 填答并提交
```

答案格式与 `exam.py` 相同，只是 `no` 带 workId 后缀：

```json
{"format": "chaoxing-answers-v1",
 "answers": [{"no": "7.1#56141956", "q": 1, "answer": "错"},
             {"no": "7.1#56141955", "q": 1, "answer": "A"}]}
```

判断题「对/错」会自动映射成 A/B，已实测。

### 8. `run.py` —— 统一入口

```bash
python run.py login              # 打开登录页并截图二维码，等待扫码
python run.py open [课程关键词]   # 个人空间 -> 找课程 -> 打开学习页，写 io/session.json
python run.py catalog            # 解析目录并打印待完成清单
python run.py video 17.1         # 转发给 video.py
python run.py quiz --list        # 转发给 quiz.py
python run.py exam export --pending        # 转发给 exam.py（导出题目）
python run.py exam answer answers.json --submit   # 导入答案集并提交
python run.py status             # 当前标签页与框架
```

`open` 的课程解析按卡片块 `<div class="course" id="c_<courseid>">` 提取，能正确配对「课程名 ↔ 链接 ↔ 进度」。不传关键词时，默认选**剩余任务点最多**的那门课。

### 9. `start.cmd` —— 启动驱动

```cmd
start.cmd a3        :: 用 browser\a3 作为会话目录启动有头浏览器
```

---

## 四、一次完整的跑法

```bash
cd .workbuddy\browser\suite
set BROWSER_ROOT=%CD%\..\a3     :: 换成自己的会话目录；只用默认 browser/ 时可省略这行

:: 1) 起浏览器（另开窗口，别关）
start.cmd a3

:: 2) 出二维码，扫码登录
python run.py login

:: 3) 进课程
python run.py open 大学英语口语

:: 4) 看还剩什么
python run.py catalog

:: 5) 刷视频（1 倍速、必须播到结尾，耗时≈视频总时长；窗口别最小化）
python video.py --pending --skip-quiz
:: 中途想分段跑：python video.py --pending --skip-quiz --limit=8

:: 6) 做测验（三段式）
python exam.py export --pending            :: 导出全部题目 + 阅读材料
::    把 io/exports/exam-*.md 丢给任意 AI，让它按文件里的格式回答案
python exam.py answer answers.json         :: 先只填答（不提交），看截图确认
python exam.py answer answers.json --submit :: 确认没问题再提交
```

---

## 五、环境依赖

| 项目 | 说明 |
|---|---|
| Node.js | 22.x |
| playwright-core | 装在 `browser/node_modules`，若缺失：`npm install playwright-core --prefix .workbuddy/browser` |
| 浏览器内核 | 自动查找：`CHROME_PATH` → `%LOCALAPPDATA%\ms-playwright\` 中版本最高的 chromium → 系统 Chrome |
| Python | 3.13，仅用标准库（无需 pip 安装） |

---

## 六、已知限制与注意事项

1. **视频只能 1 倍速真实播放**，平台设 2 倍会被重置并暂停；并且**必须播到结尾**任务点才变绿（96% 也不算）。所以刷课耗时约等于视频总时长，窗口不能最小化，也不能被别的窗口挡住太久。
2. **答题不能纯脚本算出来**：题库在平台内，提交后只回显自己的答案与得分、不给正确答案，且重做接口会拒绝。所以用 `exam.py` 的三段式：脚本负责**取题**和**填答**，答案由外部 AI（或你本人）按固定格式给。这是目前唯一可行且稳定的方式。
3. **登录态靠 profile 落盘**。驱动被强杀（Ctrl+C、任务管理器结束）时 cookie 可能还没刷盘，会丢登录态、需要重扫码。要停就等界面上的 `cmd.json` 处理完再停，或先确认 `profile/Default/Network/Cookies` 有内容。
4. **一只浏览器只能跑一个脚本**。多个脚本同时跑会争用 `io/cmd.json`，导致指令互相覆盖、超时。要并行就开多个会话目录（各自的驱动进程）。
5. **测验提交不可逆**，提交前务必确认答案；`quiz.py --dry` 只填不交。
6. **目录状态是快照**，任何「是否已完成」的判断都在 reload 之后做。
7. 部分 Culture Talk 章节的测验考视频细节（年份、书名、天数），课程没有上传字幕（`richvideo/subtitle` 返回空数组），只能靠常识或人工提供答案。

---

## 七、版本记录

| 日期 | 版本 | 内容 |
|---|---|---|
| 2026-11-12 | v0.1 | 账号一：目录解析 + 24 份章节测验批量提交（当时脚本散在 `browser/` 根目录） |
| 2026-11-20 | v0.2 | 账号二：视频任务点验证（单视频守到 95% 变绿）、倍速封禁与 iframe 递归死循环排查 |
| 2026-11-21 | v1.0 | 收敛为 `suite/`：统一 client、双页面结构兼容的目录解析、跳过已完成、提交前复核，实测通过 |
| 2026-11-21 | v1.4 | 去除 `client.py` / `agent.mjs` 中的本机绝对路径（换目录即可运行）；浏览器内核自动探测并支持 `CHROME_PATH`；五个入口补 `--help`；`render` 刷新索引时间；全链路验收通过（见下节） |
| 2026-11-21 | v1.3 | 新增 `chapter_quiz.py`：处理章节内嵌的多个测验（按 workId 区分），7.1/8.1/9.1/10.1 共 8 套 36 题实测通过 |
| 2026-11-21 | v1.2 | 新增 `exam.py` 三段式答题：导出题目+阅读材料（`chaoxing-exam-v1`）→ 外部 AI 按 `chaoxing-answers-v1` 回答案 → 离线 `check` 校验覆盖率、`inspect` 诊断 DOM、导入自动填答、缺答/没点中拒绝提交、`--submit` 才真提交 |
| 2026-11-21 | v1.1 | 视频脚本定稿：改为「播到结尾」判定（实测 96% 无效）、修掉时长一直 `NaN` 的加载坑、同页多视频、停滞自愈、`unconfirmed` 不作假完成、`--list/--dry/--skip-quiz/--limit`、收尾出 `remaining.json`。17.3、17.6 实测转绿 |

---

## 八、验收测试记录（v1.4，2026-11-21）

| 项目 | 结果 |
|---|---|
| 语法 | 7 个 `.py` 编译通过，`agent.mjs` 通过 `node --check` |
| 帮助 | `run/video/quiz/exam/chapter_quiz` 五个入口 `--help` 均正常，不再误触发浏览器 |
| 驱动启动 | 用独立临时会话全新启动，`status / titles / shot` 正常返回，已清理，未动生产会话的登录态 |
| 浏览器通道 | `status / titles / frames / shot / focusemu / mouse` 实测通过，截图已复核 |
| 目录解析 | `run.py catalog`：134 个任务点，识别出 6 个待办条目（4 个扩展阅读 + 2 个无任务点） |
| 视频脚本 | `video.py --list` 列出 4 条；`video.py 7.3 --dry` 正确识别为文档、判 `novideo` 且未播放 |
| 三段式答题 | `exam.py render` 重建 21 套 / 105 题；`check` 报 105/105 全覆盖；章节答案集 36/36 全覆盖 |
| 平台状态 | 本轮**没有提交任何测验、没有改变任何任务点状态**，只做只读探测与离线校验 |

剩余 7.3 / 8.3 / 9.3 / 10.3 扩展阅读内嵌测验、22.1 阅读、23.1 问卷属于「需要人做决定」的部分，脚本已能准确识别并列出，不属于运行故障。
