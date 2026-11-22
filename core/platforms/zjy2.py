"""zjy2 平台（zjy2.icve.com.cn）适配器。

沿用原脚本已验证的结构：
- 章节 .listItem[id]，小节 .items.iChild，资源 .fwenjianjia[id]，进度 .ziProgress；
- 展开靠逐级点击；
- 视频刷课直接把进度拖到末尾，文档翻到末页后刷新；
- 续播弹窗 .el-message-box 点"继续播放"。

版本沿革
--------
v1.2.1 修「日志说刷完了，网页还在放上一个视频」：
- 按 id 精确定位节点（不再 has_text 猜名字，会点中父文件夹）；
- 只操作可见且面积最大的 video（旧播放器只是隐藏，仍在 DOM 里且已 ended）；
- 刷完回读 .ziProgress，没到 100% 不许报完成。

v1.2.2 修「网慢时脚本抢跑，刷的完全无效」：
- **加载闸门**：点开资源后必须等到内容真的就绪（视频拿到时长、文档出现页码/
  预览容器）并且**稳定**下来才动手；页面上还挂着「加载中」遮罩就继续等。
  慢网时按 8 秒一轮自动续期（上限 75 秒），并如实写日志，而不是干等或抢跑。
- 点完只变了个标签、内容还在 loading 的情况，不再判定为「已打开」。
- 刷课动作返回结果：视频没播到结尾、文档没翻到末页都算失败并重试，
  不再「不报错 = 成功」。
- 进度文本支持「已学完 / 已完成」这类没有数字的写法（之前解析成 -1，
  已完成的资源会被当成待刷，84 项全是白刷）。
- 目录展开改为多轮，直到叶子数不再增长（慢网下懒加载目录会漏项）。
"""
import re
import time

from core.kinds import KIND_LABEL, kind_of_name

ZJY2_INDEX = "https://zjy2.icve.com.cn/study/v2/coursePreview/spoccourseIndex"
ZJY2_COURSES = "https://zjy2.icve.com.cn/study/v2/course"

# 内容加载等待：基准 26 秒；只要页面还挂着 loading，最多再续 2 次（每次 8 秒），
# 硬上限 48 秒 —— 慢网够用，又不至于一个坏节点等上几分钟。
LOAD_TIMEOUT = 26.0
LOAD_TIMEOUT_MAX = 48.0

# 内容区状态（一次性把判断依据全取回来，减少 evaluate 往返）
_STATE_JS = r"""
() => {
    const vis = (e) => {
        if (!e) return false;
        const r = e.getBoundingClientRect();
        if (r.width < 24 || r.height < 16) return false;
        const st = getComputedStyle(e);
        return st.display !== 'none' && st.visibility !== 'hidden'
               && st.opacity !== '0' && e.offsetParent !== null;
    };

    // 可见视频：按面积取最大的那个（旧播放器会被隐藏，不算）
    const vids = [...document.querySelectorAll('video')].filter(vis);
    vids.sort((a, b) => (b.clientWidth * b.clientHeight) - (a.clientWidth * a.clientHeight));
    const v = vids[0] || null;

    // 文档页码：旧版是 "12 / 34" 独立短文本；新版 zjy2 写成 "1 / 59 下一页"
    // （页码容器里还塞着翻页按钮），所以必须按**前缀**匹配，不能再要求整行相等。
    // 排除 el-input 的字数计数器（"0/50"），那是评论框，不是页码。
    let pages = null;
    for (const e of document.querySelectorAll('span,div,i,b,em,li,p')) {
        if (!vis(e)) continue;
        const cls = String(e.className || '');
        if (cls.includes('el-input') || cls.includes('el-textarea')) continue;
        const s = (e.innerText || '').trim();
        if (!s || s.length > 24) continue;
        const m = s.match(/^(\d{1,4})\s*[\/]\s*(\d{1,4})(?!\d)/);
        if (!m) continue;
        const cur = +m[1], tot = +m[2];
        if (tot >= 1 && tot <= 3000 && cur >= 1 && cur <= tot) { pages = {cur, tot}; break; }
    }

    // v1.8.0：新版课件阅读器（PPT/PDF 走 .office + el-carousel 幻灯片图片）。
    // 它既没有 iframe 也没有 canvas，页码又是 "1 / 59 下一页" 的复合文本，
    // 之前两样都识别不到 → 明明课件已经显示出来了却报「没出现播放器或文档」。
    const readerEl = document.querySelector('.office');
    const slides = readerEl ? [...readerEl.querySelectorAll('img')].filter(vis) : [];
    const slidesLoaded = slides.filter(i => !/loading\.gif/i.test(i.src || '')).length;

    // 文档预览容器：iframe（可能是跨域 PDF 预览）或 canvas（PDF.js / 课件渲染）
    const frs = [...document.querySelectorAll('iframe')].filter(vis)
        .sort((a, b) => (b.clientWidth * b.clientHeight) - (a.clientWidth * a.clientHeight));
    const fr = frs[0] || null;
    let frameReady = null;
    if (fr) {
        try {
            frameReady = fr.contentDocument ? fr.contentDocument.readyState : null;
        } catch (e) {
            frameReady = 'cross';       // 跨域读不到，按可交互处理
        }
    }
    const canvas = [...document.querySelectorAll('canvas')].filter(vis).length;

    // 加载指示：只有明确是遮罩/spinner 或"加载中"这种独立短句才算，别误伤正文
    const loadEls = [...document.querySelectorAll(
        '[class*=loading],[class*=Loading],[class*=spin],[class*=Spin],[class*=mask]'
    )].filter(e => vis(e) && !e.closest('.listItem'));
    const loadTxt = [...document.querySelectorAll('div,span,p')].filter(e =>
        e.children.length === 0 && vis(e) &&
        /^(正在)?(加载中|加载中\.\.\.|正在加载|正在打开|请稍候|Loading\.\.\.)$/i.test((e.textContent || '').trim())
    );

    const nameEl = document.querySelector('.docname,[class*=docName],[class*=fileName],[class*=resName]');
    const docname = nameEl ? (nameEl.innerText || '').trim() : '';

    const bodyTxt = (document.body.innerText || '').slice(0, 3000);
    return {
        readyState: document.readyState,
        video: v ? {
            ready: v.readyState,
            dur: (isFinite(v.duration) ? v.duration : 0),
            cur: v.currentTime,
            src: ((v.currentSrc || v.src || '').split('?')[0]).slice(-60),
        } : null,
        pages: pages,
        reader: !!readerEl,
        slides: slides.length,
        slidesLoaded: slidesLoaded,
        frame: fr ? (fr.src || fr.getAttribute('src') || '').slice(0, 120) : '',
        frameReady: frameReady,
        canvas: canvas,
        loading: (loadEls.length + loadTxt.length) > 0,
        docname: docname,
        login: /扫码登录|登录已失效|请先登录|重新登录/.test(bodyTxt),
        sig: [
            v ? ((v.currentSrc || v.src || '').split('?')[0]).slice(-60) : '',
            pages ? pages.cur + '/' + pages.tot : '',
            docname,
        ].join('|'),
    };
}
"""


class Zjy2:
    name = "zjy2"

    def __init__(self, br, bus=None):
        self.br = br
        self.bus = bus
        self.p = br.page

    def _log(self, m, level="info"):
        if self.bus:
            self.bus.log(m, level)

    def _stopped(self):
        return self.bus.should_stop() if self.bus else False

    # ---------- 页面 ----------
    def _sync_page(self):
        """资源有可能在新标签页里打开：如果当前页不是课件页，而别的页是，切过去。"""
        try:
            pages = [p for p in self.br.page.context.pages if not p.is_closed()]
            cur = self.br.page
            if "courseware" in (cur.url or ""):
                return
            cand = [p for p in pages if "courseware" in (p.url or "")]
            if cand:
                self.br.page = cand[-1]
                self.p = self.br.page
                self.br.page.bring_to_front()
        except Exception:
            pass

    # ---------- 目录 ----------
    def _tree_loading(self):
        """目录本身还在加载吗（遮罩 / 空骨架）。"""
        try:
            return bool(self.p.evaluate(
                """() => {
                    if (document.readyState !== 'complete') return true;
                    const vis = e => {
                        const r = e.getBoundingClientRect();
                        return r.width > 20 && r.height > 20 && e.offsetParent !== null;
                    };
                    const mask = [...document.querySelectorAll(
                        '[class*=loading],[class*=Loading],[class*=spin]')].filter(vis);
                    return mask.length > 0;
                }"""
            ))
        except Exception:
            return False

    def wait_ready(self, tries=4):
        """等课程目录加载完。

        慢网下分两关：先等节点出现，再等数量稳定（懒加载会一批批追加），
        最后确认没有加载遮罩。任一轮失败就刷新重来。
        """
        for attempt in range(tries):
            try:
                self.p.wait_for_selector(".listItem[id]", state="attached", timeout=20000)
                prev = -1
                stable = 0
                for _ in range(24):
                    n = self.p.locator(".listItem[id]").count()
                    if n > 0 and n == prev:
                        stable += 1
                        if stable >= 2 and not self._tree_loading():
                            return True
                    else:
                        stable = 0
                    prev = n
                    self.br.wait(500)
                if self.p.locator(".listItem[id]").count() > 0:
                    return True
            except Exception:
                pass
            if attempt < tries - 1:
                self._log("  目录还没加载出来，刷新重试…", "warn")
                try:
                    self.br.reload()
                except Exception:
                    pass
                self.br.wait(1500 * (attempt + 1))
        return False

    def go_index(self):
        if "courseware" in self.br.current_url() or "spoccourseIndex" not in self.br.current_url():
            self.br.goto(ZJY2_INDEX)
        return self.wait_ready()

    # ---------- 课程列表（v1.4.4） ----------
    _CARDS_JS = """() => [...document.querySelectorAll('.course_item .course_name, .course_name')]
                    .map(e => (e.textContent || '').trim())
                    .filter(t => t && t.length < 60)"""

    def _page_numbers(self):
        """课程列表的页码（.el-pager li）。一页放 9 门，12 门就会被拆成两页。"""
        try:
            nums = self.p.evaluate(
                """() => [...document.querySelectorAll('.el-pager li')]
                    .map(e => (e.innerText || '').trim())
                    .filter(t => /^\\d+$/.test(t))"""
            )
            return sorted({int(x) for x in nums})
        except Exception:
            return []

    def _goto_list_page(self, no):
        """切到第 no 页课程列表（1 起）。"""
        try:
            self.p.evaluate(
                """(no) => {
                    const li = [...document.querySelectorAll('.el-pager li')]
                        .find(e => (e.innerText || '').trim() === String(no));
                    if (li) li.click();
                }""", no)
        except Exception:
            return False
        for _ in range(12):
            self.br.wait(500)
            try:
                if self.p.locator(".course_item").count() > 0:
                    return True
            except Exception:
                pass
        return False

    def list_course_names(self, timeout=25):
        """从「我的课程」页枚举课程名。卡片是 .course_item，课程名在 .course_name。

        v1.7.1：两个坑一起修。
        1) 以前只等 15 秒且「读不到就立刻问 on_login_page」。zjy2 打开课程页
           要过一次 sso 身份校验（URL 会短暂变成 sso），那一瞬间既没有卡片、
           URL 又像登录页，于是被误判成「登录失效」——账号明明登进去了却让
           用户重新扫码。现在等满超时再判定。
        2) 列表是分页的（一页 9 门）。课程多的账号只看第一页会漏课，
           漏掉的课永远刷不到。现在按页码把每页都读一遍。
        """
        self.br.goto(ZJY2_COURSES)
        self.p = self.br.page
        self.br.wait(1200)
        deadline = time.time() + timeout
        names = []
        while time.time() < deadline:
            names = self.p.evaluate(self._CARDS_JS)
            if names:
                break
            self.br.wait(900)
        if not names:
            # 真的读不到时才区分「没登录」和「页面没渲染完」
            return None if self.br.on_login_page() else []

        pages = self._page_numbers() or [1]
        for no in pages[1:]:
            if self._goto_list_page(no):
                names += self.p.evaluate(self._CARDS_JS)
        # 读完把列表翻回第一页，后面点课程时从第一页开始找
        if len(pages) > 1:
            self._goto_list_page(1)

        seen, out = set(), []
        for n in names:
            if n not in seen:
                seen.add(n)
                out.append(n)
        return out

    def _click_course(self, course_name):
        return self.p.evaluate(
            """(name) => {
                const items = [...document.querySelectorAll('.course_item')];
                const it = items.find(x => ((x.querySelector('.course_name')||{}).textContent||'').trim() === name);
                if (!it) return false;
                const btn = it.querySelector('.course_btn') || it;
                btn.click(); return true;
            }""",
            course_name,
        )

    def enter_course(self, course_name, timeout=18):
        """从「我的课程」页点某门课的「学习」，进入教学内容页。

        v1.7.1：课程可能在第 2 页。以前只在当前页找，翻页的课永远进不去，
        刷课就会报「进入课程失败」。现在逐页找。
        """
        if "/study/v2/course" not in self.br.current_url() or "spoccourseIndex" in self.br.current_url():
            self.br.goto(ZJY2_COURSES)
            self.br.wait(2000)
            self.p = self.br.page
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if self._click_course(course_name):
                    self.br.wait(2500)
                    return "spoccourseIndex" in self.br.current_url()
            except Exception:
                pass
            self.br.wait(800)
        # 当前页没有：按页码翻一圈再找（第 2 页的课以前永远进不去）
        for no in self._page_numbers():
            if not self._goto_list_page(no):
                continue
            try:
                if self._click_course(course_name):
                    self.br.wait(2500)
                    return "spoccourseIndex" in self.br.current_url()
            except Exception:
                pass
        return False

    def probe_resources_fast(self, wait_ms=6000):
        """快速判断当前课程页有无可刷资源（不跑 wait_ready 的 80 秒长等）。

        空课（教学内容整个是空白）永远等不到 .listItem，所以这里只等几秒：
        有章节就展开数叶子，没有章节且没加载遮罩就判无课。

        返回 {brushable, done, total}；认不出来返回 None。
        done/total 用来在课程列表里标「刷完」，不必再单独跑一次复核。
        """
        deadline = time.time() + wait_ms / 1000.0
        while time.time() < deadline:
            try:
                n = self.p.locator(".listItem[id]").count()
                if n > 0:
                    break
                if self._tree_loading():
                    self.br.wait(700)
                    continue
                # 没有章节、也没有加载遮罩：确认是空课
                self.br.wait(800)
                if self.p.locator(".listItem[id]").count() == 0:
                    return {"brushable": False, "done": 0, "total": 0}
            except Exception:
                pass
            self.br.wait(700)
        try:
            self.expand_all(rounds=2)
            items = self.list_resources()
            brushable = [x for x in items if x.get("kind") != "quiz"]
            done = sum(1 for x in brushable if (x.get("pct") or 0) >= 100)
            return {"brushable": len(brushable) > 0, "done": done, "total": len(brushable)}
        except Exception as e:
            self._log(f"检测课程资源时出错：{e}", "warn")
            return None

    def probe_course_info(self, course_name, timeout=16):
        """进入课程并返回 {brushable, done, total}；进不去/读不到返回 None。"""
        if not self.enter_course(course_name, timeout=timeout):
            return None
        res = self.probe_resources_fast()
        if isinstance(res, bool):        # 兼容旧调用/测试桩
            res = {"brushable": res, "done": 0, "total": 0}
        return res

    def probe_course(self, course_name, timeout=16):
        """进入课程、展开目录，判断有无可刷资源（quiz 不算）。返回 True/False/None。"""
        info = self.probe_course_info(course_name, timeout=timeout)
        if info is None:
            return None
        return bool(info.get("brushable"))

    def _leaf_count(self):
        try:
            return self.p.evaluate(
                "() => [...document.querySelectorAll('.fwenjianjia')]"
                ".filter(f => !f.querySelector('.fwenjianjia')).length"
            )
        except Exception:
            return 0

    def expand_all(self, pause=0.35, rounds=3):
        """展开全部章节/小节。

        懒加载目录点一下不会立刻出内容，所以点完要等子节点出现；
        而且一轮下来可能又冒出新的层级，因此多轮直到叶子数不再增长。
        """
        self.wait_ready()
        total = 0
        for _ in range(rounds):
            chapters = self.p.locator(".listItem[id]")
            for i in range(chapters.count()):
                c = chapters.nth(i)
                try:
                    if c.locator(".items.iChild").count() == 0:
                        c.locator(".items").first.click(timeout=6000)
                        self.br.wait(int(pause * 1000))
                except Exception:
                    pass
            subs = self.p.locator(".items.iChild")
            for i in range(subs.count()):
                s = subs.nth(i)
                try:
                    parent = s.locator("xpath=..")
                    if parent.locator(".fwenjianjia").count() == 0:
                        s.click(timeout=6000)
                        self.br.wait(int(pause * 1000))
                except Exception:
                    pass
            self.br.wait(600)
            now = self._leaf_count()
            if now == total:
                break
            total = now
        self.br.wait(400)

    def list_resources(self):
        """枚举资源叶子节点。

        - 只认没有子节点的 .fwenjianjia（父节点点击只会折叠/展开）；
        - 进度文本除了百分比，还要认「已学完 / 已完成」这种没有数字的写法，
          否则已完成项会被当成待刷（v1.2.2 之前的坑）；
        - 没有 .ziProgress 但有完成勾的，也算 100%。

        v1.7.1：任务节点下面挂的视频是 `.fItem[id]`，不是 `.fwenjianjia`。
        以前只扫 `.fwenjianjia`，于是「任务 8.5」这种父节点被当成一个资源
        （进度取的是子项聚合值 95%），点它只会展开、不会播放，最后报
        「没出现播放器或文档」跳过——而真正差 5% 的那个 mp4 永远刷不到。
        现在把 `.fItem[id]` 也当资源，父节点有子项时不收录。
        """
        raw = self.p.evaluate(
            """() => {
                const out = [], seen = new Set();
                const readProg = (f) => {
                    const pr = f.querySelector('.ziProgress');
                    const prog = pr ? (pr.innerText || '').trim() : '';
                    const m = prog.match(/(\\d+)\\s*%/);
                    let pct = -1;
                    if (m) {
                        pct = parseInt(m[1], 10);
                    } else if (/未学完|未完成|未学习|未开始/.test(prog)) {
                        pct = 0;
                    } else if (/已学完|已完成|已学习|学完/.test(prog)) {
                        pct = 100;
                    } else if (!prog && f.querySelector('.checkmark.wc')) {
                        pct = 100;
                    }
                    return {prog, pct};
                };
                const push = (f) => {
                    if (!f.id || seen.has(f.id)) return;
                    const nm = f.querySelector('.name');
                    if (!nm) return;
                    const name = (nm.innerText || '').trim();
                    if (!name) return;
                    const {prog, pct} = readProg(f);
                    seen.add(f.id);
                    out.push({id: f.id, name, prog, pct});
                };
                // 1) 任务节点下真正的资源文件（视频/文档/图片）
                for (const f of document.querySelectorAll('.fItem[id]')) push(f);
                // 2) 独立的叶子任务节点
                for (const f of document.querySelectorAll('.fwenjianjia')) {
                    if (!f.id || seen.has(f.id)) continue;
                    if (f.querySelector('.fwenjianjia')) continue;   // 还有子任务
                    if (f.querySelector('.fItem[id]')) continue;     // 还有子资源
                    push(f);
                }
                return out;
            }"""
        )
        for r in raw:
            r["kind"] = kind_of_name(r["name"])
        return raw

    # ---------- 打开资源 ----------
    def _click_node(self, node_id, mode=0):
        """按 id 点开资源。mode 0=正常点击，1=脚本点击，2=双击。"""
        sel = '[id="%s"]' % node_id
        try:
            if mode == 0:
                self.p.locator(sel + " .name").first.click(timeout=6000)
            elif mode == 1:
                self.p.evaluate(
                    """(id) => {const f=document.getElementById(id); if(!f) return false;
                        const n=f.querySelector('.name')||f; n.click(); return true;}""",
                    node_id,
                )
            else:
                self.p.locator(sel + " .name").first.dblclick(timeout=6000)
            return True
        except Exception:
            return False

    # ---------- 内容区状态 ----------
    def _content_state(self):
        try:
            return self.p.evaluate(_STATE_JS) or {}
        except Exception:
            return {}

    @staticmethod
    def _ready_kind(st):
        """当前内容是否「可以开始刷」。返回 (kind, ready)。"""
        if not st or st.get("readyState") != "complete":
            return "", False
        if st.get("loading"):
            return "", False
        v = st.get("video")
        if v and (v.get("ready") or 0) >= 1 and (v.get("dur") or 0) > 0:
            return "video", True
        if st.get("pages"):
            return "doc", True
        if st.get("frame"):
            if st.get("frameReady") == "loading":
                return "", False
            return "doc", True
        if (st.get("canvas") or 0) > 0:
            return "doc", True
        # 新版阅读器：幻灯片图片已经换掉 loading.gif 就算就绪
        if st.get("reader") and (st.get("slidesLoaded") or 0) > 0:
            return "doc", True
        return "", False

    @staticmethod
    def _describe(st):
        """给日志用的「现在卡在哪」。"""
        if not st:
            return "读不到页面状态"
        if st.get("readyState") != "complete":
            return "页面还没加载完"
        if st.get("loading"):
            return "还在加载中"
        if st.get("video") and not (st["video"].get("dur") or 0):
            return "播放器还没拿到时长"
        if st.get("frame") and st.get("frameReady") == "loading":
            return "预览框还没加载好"
        if st.get("reader") and not (st.get("slidesLoaded") or 0) and not st.get("pages"):
            return "课件阅读器还在加载幻灯片"
        if not (st.get("video") or st.get("pages") or st.get("frame")
                or st.get("canvas") or st.get("reader")):
            return "没出现播放器或文档"
        return "内容还没稳定"

    def _content_shows(self, name):
        """内容区（不含左侧目录树）里是否出现了这个资源名。"""
        try:
            return bool(self.p.evaluate(
                """(name) => {
                    for (const e of document.querySelectorAll('div,span,p,h1,h2,h3,li,a')) {
                        if (!e.id && e.children.length > 2) continue;
                        if (e.offsetParent === null) continue;
                        if (e.closest('.listItem')) continue;          // 排除目录树
                        if ((e.innerText || '').trim() === name) return true;
                    }
                    return false;
                }""",
                name,
            ))
        except Exception:
            return False

    def _content_sig(self):
        """内容指纹：可见视频源 + 页码 + 内容区标题。"""
        return self._content_state().get("sig", "")

    def _wait_opened(self, name, before, timeout=LOAD_TIMEOUT):
        """等资源真的打开：内容变了 + 已就绪 + 稳定 1 秒。

        慢网时只要页面还挂着 loading 遮罩就续期（每次 +8 秒，硬上限 75 秒），
        并且每隔 8 秒往日志写一句，让人知道是在等加载，不是卡死。
        """
        t0 = time.time()
        deadline = t0 + timeout
        hard = t0 + max(timeout, LOAD_TIMEOUT_MAX)
        ext_left = 2
        last_hint = t0
        stable_sig, stable_since = None, 0.0
        st = {}

        while True:
            now = time.time()
            st = self._content_state()
            kind, ready = self._ready_kind(st)
            sig = st.get("sig", "")
            changed = self._content_shows(name) or (bool(before) and sig != (before.get("sig") or ""))

            if ready and changed:
                if sig == stable_sig and now - stable_since >= 1.0:
                    return True, kind, st
                if sig != stable_sig:
                    stable_sig, stable_since = sig, now
            else:
                stable_sig, stable_since = None, 0.0

            # 还在加载 → 给慢网续期（最多两次，避免一个坏节点无限等）
            if (st.get("loading") or st.get("readyState") != "complete") and now >= deadline and ext_left:
                deadline = min(hard, now + min(8.0, max(2.0, timeout * 0.5)))
                ext_left -= 1
            if now >= deadline:
                return False, kind, st

            if not ready and now - last_hint >= 8:
                last_hint = now
                self._log(f"  页面加载较慢（已等 {int(now - t0)} 秒，{self._describe(st)}），继续等…")

            if self._stopped():
                return False, kind, st
            self.br.wait(500)

    def open_resource(self, node, retries=3, wait=None):
        """点开资源并等到内容真的就绪。成功返回 True。

        慢网时每次重试的等待时间逐次加长（26s / 34s / 42s），
        期间绝不开始刷——上一版就是这里抢跑，才出现「刷了半天没变化」。
        """
        self._sync_page()
        rid, name = node["id"], node["name"]
        for attempt in range(retries):
            # 滑块验证挡在前面时，点什么都没反应，先让用户拖完
            if self.captcha_hint() and not self.wait_captcha():
                return False
            before = self._content_state()
            if not self._click_node(rid, mode=attempt):
                self._log("  点击没有命中，重试…", "warn")
                self.br.wait(1000)
                continue
            self.br.wait(700)
            t = wait if wait is not None else (LOAD_TIMEOUT + attempt * 8)
            opened, kind, st = self._wait_opened(name, before, timeout=t)
            if opened:
                self.br.wait(500)      # 让内容再渲染一帧
                return True
            self._log(f"  第 {attempt + 1} 次没等到内容（{self._describe(st)}），换方式重试…", "warn")
            self.br.wait(1200 + attempt * 1200)
        return False

    # ---------- 进度回读 ----------
    def progress_of(self, node_id):
        """读某个资源当前的进度百分比；读不到返回 None。"""
        try:
            txt = self.p.evaluate(
                """(id) => {const f=document.getElementById(id); if(!f) return null;
                    const p=f.querySelector('.ziProgress'); return p?(p.innerText||'').trim():null;}""",
                node_id,
            )
        except Exception:
            return None
        if txt is None:
            return None
        m = re.search(r"(\d+)\s*%", txt)
        if m:
            return int(m.group(1))
        if re.search(r"未学完|未完成|未学习|未开始", txt):
            return 0
        if re.search(r"已学完|已完成|已学习|学完", txt):
            return 100
        return None

    def _progress_text(self, node_id):
        try:
            return self.p.evaluate(
                """(id) => {const f=document.getElementById(id); if(!f) return '';
                    const p=f.querySelector('.ziProgress'); return p?(p.innerText||'').trim():'';}""",
                node_id,
            ) or ""
        except Exception:
            return ""

    def _wait_progress(self, node_id, before, timeout=24):
        """等这个资源的进度真的涨上去（到 100 或比原来高）。

        慢网下平台上报会滞后，所以给 24 秒，并且只在「读得到具体数字」时才判定。
        """
        t0 = time.time()
        while time.time() - t0 < timeout:
            pct = self.progress_of(node_id)
            if pct is not None and (pct >= 100 or (before is not None and before >= 0 and pct > before)):
                return True
            if self._stopped():
                return False
            self.br.wait(1000)
        return False

    # ---------- 安全验证（滑块） ----------
    # 实测：短时间内连续翻几十页会触发平台的滑块验证（页面底部出现
    # 「拖动下方拼图完成验证」）。这不是「加载慢」，等下去永远不会好，
    # 必须让真人在浏览器窗口里拖一次。程序只负责发现并等待，不去绕过它。
    _CAPTCHA_WORDS = ("拖动下方拼图", "请完成验证", "完成安全验证", "滑动验证",
                      "拖动滑块", "安全验证失败")

    def captcha_hint(self):
        """当前页面是否停在人机验证上；是则返回命中的提示词。"""
        try:
            t = self.br.text()[-800:]
        except Exception:
            return ""
        for w in self._CAPTCHA_WORDS:
            if w in t:
                return w
        return ""

    def wait_captcha(self, timeout=180):
        """发现滑块就等用户拖完。返回 True=已通过/本来就没有，False=超时或已停止。"""
        hint = self.captcha_hint()
        if not hint:
            return True
        self._log(f"平台弹出安全验证（「{hint}」）：请在浏览器窗口里拖动滑块完成验证，"
                  f"程序最多等 {timeout} 秒，拖完会自动继续…", "warn")
        try:
            self.br.shot("captcha")
        except Exception:
            pass
        t0 = time.time()
        while time.time() - t0 < timeout:
            if self._stopped():
                return False
            self.br.wait(1500)
            if not self.captcha_hint():
                self._log("验证已通过，继续刷课", "ok")
                self.br.wait(1200)
                return True
        self._log("等待验证超时：滑块超过 3 分钟没拖完，本次先停下。"
                  "拖完验证后再点③即可接着刷", "error")
        return False

    # ---------- 刷课 ----------
    def _dismiss_doc_resume(self):
        """关文档类续播弹窗「上次观看到第X页，是否继续观看？」。

        v1.8.0 实测：一定要点**「取消」**（= 不续播，从第 1 页开始）。
        点「确定」会跳到上次那一页，然后新版阅读器的页码控件就再也读不到了
        （`_doc_pages()` 一直返回 None），后续翻页逻辑直接失效 —— 表现就是
        「课件明明在翻，进度一点没涨」。

        只看按钮所在浮层的文案，普通公告里的「取消」不会被误点。
        """
        for label in ("取消", "确定"):
            try:
                hit = bool(self.p.evaluate(
                    """(label) => {
                        const vis = e => {
                            const r = e.getBoundingClientRect();
                            return r.width > 1 && r.height > 1 &&
                                   getComputedStyle(e).visibility !== 'hidden';
                        };
                        for (const b of document.querySelectorAll(
                                'button,[role=button],a,[class*=btn],[class*=button]')) {
                            if (!vis(b) || (b.textContent || '').trim() !== label) continue;
                            let p = b, i = 0, ok = false;
                            while (p && i++ < 10) {
                                if (/上次观看到|上次阅读到|上次浏览到/.test(p.innerText || '')) {
                                    ok = true; break;
                                }
                                p = p.parentElement;
                            }
                            if (ok) { b.click(); return true; }
                        }
                        return false;
                    }""",
                    label,
                ))
            except Exception:
                hit = False
            if hit:
                self.br.wait(900)
                return True
        return False

    def _dismiss_resume(self):
        """兜底关续播弹窗（正常由页面内看门狗处理，这里防它没跑到）。"""
        for label in ("继续播放", "重新开始"):
            for _ in range(2):
                if self.br.click_text(label, timeout=700):
                    self.br.wait(700)
                else:
                    break
        if self._dismiss_doc_resume():
            self.br.wait(700)

    def _learn_current(self, name=""):
        """处理当前打开的资源：视频拖到末尾 / 文档翻到末页。

        返回 {kind, ok, note}：**没真的刷到才算失败**，由调用方重试。
        """
        self._dismiss_resume()
        # 续播弹窗点「确定」会跳到上次那一页，页码控件会短暂重绘读不到；
        # 这时若直接把 pages 当空的，就会走成「单页文档」分支、一页都不翻（实测过的坑）。
        self.br.wait(900)
        st = self._content_state()
        kind, ready = self._ready_kind(st)
        for _ in range(4):
            if ready:
                break
            self.br.wait(700)
            st = self._content_state()
            kind, ready = self._ready_kind(st)
        if not ready:
            return {"kind": kind, "ok": False, "note": self._describe(st)}

        if kind == "video":
            if self._drag_to_end():
                return {"kind": "video", "ok": True, "note": ""}
            return {"kind": "video", "ok": False, "note": "播放器没能播到结尾"}

        return self._read_doc(st)

    def _wait_slide_loaded(self, timeout_ms=2200):
        """等当前这一页幻灯片的图真的换上来（新版阅读器先显示 loading.gif）。

        不确认就点「下一页」，平台会认为这页没看，进度少记几页 ——
        实测一份 48 页的 PPT 只能记到 95%。
        """
        t0 = time.time()
        while (time.time() - t0) * 1000 < timeout_ms:
            try:
                ok = self.p.evaluate(
                    """() => {
                        const vis = i => i && i.offsetParent !== null &&
                                         i.getBoundingClientRect().width > 200;
                        const imgs = [...document.querySelectorAll('.office img')].filter(vis);
                        if (!imgs.length) return true;
                        return imgs.every(i => !/loading\\.gif/i.test(i.src || ''));
                    }"""
                )
            except Exception:
                return
            if ok:
                return
            self.br.wait(250)

    def _read_doc(self, st):
        """文档类：翻到末页（单页则滚动停留），最后刷新让平台同步进度。"""
        pages = st.get("pages") or {}
        if not pages.get("tot"):
            # 页码可能还在重绘：补读几次，别把「48 页的 PPT」当单页跳过
            for _ in range(5):
                self.br.wait(600)
                again = self._doc_pages()
                if again:
                    pages = again
                    break
        cur, tot = pages.get("cur"), pages.get("tot")

        moved = 0
        if tot and tot > 1:
            for _ in range(tot + 10):
                if self._stopped():
                    return {"kind": "doc", "ok": False, "note": "已停止"}
                if self.captcha_hint() and not self.wait_captcha():
                    return {"kind": "doc", "ok": False, "note": "卡在安全验证（滑块）"}
                if not self._click_next_page():
                    break
                moved += 1
                # v1.8.0 实测：翻页太快平台**不计进度**。
                # 同一份 48 页 PPT：650ms/页翻完只记到 10%，1400ms/页能记到 22%（11 页）。
                # 平台按「每页停留≥1 秒」结算，抢跑等于白翻，还会谎报成功。
                self.br.wait(1400)
                self._wait_slide_loaded()      # 等这一页的图真的换上来再继续
                now = self._doc_pages()
                if now and now["cur"] >= now["tot"]:
                    break
            # 停在末页多待一会儿：平台要这段停留时间才把最后几页记进去
            self.br.wait(2200)
            # v1.4.3：弹窗盖住预览时「下一页」根本点不动，以前照样返回 ok，
            # 进度一直 0% 却继续往下刷。这里必须判定为没刷到，让调用方重试。
            now = self._doc_pages()
            if moved == 0 and now and now["cur"] < now["tot"]:
                return {"kind": "doc", "ok": False,
                        "note": f"停在第 {now['cur']}/{now['tot']} 页，翻页按钮点不动"
                                "（可能被弹窗挡住）"}
        else:
            # 单页文档 / 没有页码的预览：往下滚一遍并停留一会儿
            self._scroll_content(5)
            self.br.wait(2200)

        self._flush_progress()
        return {"kind": "doc", "ok": True, "note": ""}

    def _doc_pages(self):
        """读文档页码。兼容旧版 "12 / 34" 与新版 "1 / 59 下一页"。"""
        try:
            return self.p.evaluate(
                """() => {
                    const vis = e => e && e.offsetParent !== null;
                    for (const e of document.querySelectorAll('span,div,i,b,em,li,p')) {
                        if (!vis(e)) continue;
                        const cls = String(e.className || '');
                        if (cls.includes('el-input') || cls.includes('el-textarea')) continue;
                        const s = (e.innerText || '').trim();
                        if (!s || s.length > 24) continue;
                        const m = s.match(/^(\\d{1,4})\\s*[\\/]\\s*(\\d{1,4})(?!\\d)/);
                        if (!m) continue;
                        const cur = +m[1], tot = +m[2];
                        if (tot >= 1 && tot <= 3000 && cur >= 1 && cur <= tot) return {cur, tot};
                    }
                    return null;
                }"""
            )
        except Exception:
            return None

    def _click_next_page(self):
        try:
            return bool(self.p.evaluate(
                """() => {
                    const vis = e => e && e.offsetParent !== null && e.getBoundingClientRect().width > 0;
                    // 旧版是纯文本「下一页」；新版是 <button><span> 下一页 </span></button>
                    const cands = [...document.querySelectorAll('button,a,span,div,li,i')].filter(e =>
                        e.children.length === 0 && vis(e) &&
                        /^(下一页|下一頁|Next)$/.test((e.textContent || '').trim()) &&
                        !e.disabled && !/is-disabled/.test(String(e.className || '')));
                    if (!cands.length) return false;
                    (cands[0].closest('button') || cands[0]).click();
                    return true;
                }"""
            ))
        except Exception:
            return False

    def _scroll_content(self, times=4, step=700):
        """在内容区滚轮往下翻（跨域 PDF 预览也能滚）。"""
        try:
            vp = self.p.viewport_size or {"width": 1280, "height": 800}
            x, y = vp["width"] * 0.62, vp["height"] * 0.55
            self.p.mouse.move(x, y)
            for _ in range(times):
                if self._stopped():
                    return
                self.p.mouse.wheel(0, step)
                self.br.wait(400)
        except Exception:
            pass

    def _expand_light(self):
        """刷新后把目录展开一层，让 `.ziProgress` 能被读到。

        v1.8.0：新版页面的目录是**懒加载**的 —— 刷新之后只剩章节行，
        资源和它的进度条要再点一次章节和「课件」子项才会请求回来。
        不展开就读不到进度，进度回读永远是 None，全部项都被拖到结尾统一复核。
        """
        prev = -1
        for _ in range(3):
            try:
                self.p.evaluate(
                    """() => {
                        for (const el of document.querySelectorAll('.listItem[id]')) {
                            const it = el.querySelector(':scope > .items');
                            if (it && !el.querySelector('.items.iChild')) it.click();
                        }
                    }"""
                )
            except Exception:
                pass
            self.br.wait(700)
            try:
                self.p.evaluate(
                    """() => {
                        for (const el of document.querySelectorAll('.items.iChild')) {
                            const wrap = el.parentElement;
                            if (wrap && !wrap.querySelector('.fwenjianjia')) el.click();
                        }
                    }"""
                )
            except Exception:
                pass
            self.br.wait(900)
            try:
                n = self.p.evaluate(
                    "() => document.querySelectorAll('.fwenjianjia[id]').length")
            except Exception:
                return
            if n and n == prev:      # 数量不再增长 = 都点开了
                break
            prev = n

    def _flush_progress(self):
        """刷新一次，让平台把刚看的进度上报/回显。刷新失败不影响结果。

        v1.8.0 两个修正：
        · **先回目录页再刷新**。刷完文档后 URL 还停在
          `spoccourseIndex/courseware?...` 阅读器路由上，直接 reload 只会把阅读器
          再加载一遍 —— 页面上根本没有 `.listItem`，进度回读永远是 None。
        · 刷新后把目录展开一层：新版页面目录是懒加载的，不点开进度条节点不存在。
        """
        try:
            self.go_index()          # 从阅读器路由回到目录
        except Exception:
            pass
        try:
            self.br.reload()
        except Exception:
            return
        t0 = time.time()
        while time.time() - t0 < 15:
            try:
                if self.p.locator(".listItem[id]").count() > 0:
                    break
            except Exception:
                pass
            self.br.wait(600)
        self._expand_light()

    def _drag_to_end(self):
        """分两步拖进度条：先到一半，再到结尾前 0.5 秒，等它自然播完。

        慢网时 seek 会卡在缓冲，这里不再 25 秒就放弃：只要还在缓冲
        （readyState < 3 且进度没动）就继续等，最多 60 秒。
        """
        dur = 0.0
        for _ in range(6):
            s = self._video_state()
            if not s:
                return False
            dur = float(s.get("dur") or 0)
            if dur >= 1:
                break
            self._play_video()
            self.br.wait(1800)
        if dur < 1:
            return False

        for pos in (dur * 0.5, max(0.0, dur - 0.5)):
            if self._stopped():
                return False
            self._seek(pos)
            self.br.wait(800)
            self.br.click_text("继续播放", timeout=600)

        t0 = time.time()
        last_cur, last_move = -1.0, time.time()
        while time.time() - t0 < 60:
            if self._stopped():
                return False
            self.br.wait(1000)
            self.br.guard_flush()
            s = self._video_state()
            if not s:
                return False
            if s["end"] or (s["dur"] and s["cur"] >= s["dur"] - 1):
                return True
            if abs(s["cur"] - last_cur) > 0.3:
                last_cur, last_move = s["cur"], time.time()
            elif time.time() - last_move > 20 and (s.get("ready") or 0) < 3:
                # 长时间没动且没缓冲好：重新 seek 一次，别直接判失败
                self._seek(max(0.0, dur - 0.5))
                last_move = time.time()
            if s["paused"]:
                self._play_video()
        return False

    def _video_state(self):
        try:
            return self.p.evaluate(
                """() => {
                    const vs = [...document.querySelectorAll('video')].filter(v => v.offsetParent !== null);
                    if (!vs.length) return null;
                    vs.sort((a, b) => (b.clientWidth * b.clientHeight) - (a.clientWidth * a.clientHeight));
                    const v = vs[0];
                    return {dur: v.duration || 0, cur: v.currentTime, paused: v.paused,
                            end: v.ended, ready: v.readyState};
                }"""
            )
        except Exception:
            return None

    def _play_video(self):
        try:
            self.p.evaluate(
                """() => {
                    const vs = [...document.querySelectorAll('video')].filter(v => v.offsetParent !== null);
                    if (!vs.length) return;
                    vs.sort((a, b) => (b.clientWidth * b.clientHeight) - (a.clientWidth * a.clientHeight));
                    const v = vs[0];
                    v.muted = true; v.playbackRate = 1; v.play().catch(() => {});
                }"""
            )
        except Exception:
            pass

    def _seek(self, t):
        try:
            self.p.evaluate(
                """(t) => {
                    const vs = [...document.querySelectorAll('video')].filter(v => v.offsetParent !== null);
                    if (!vs.length) return;
                    vs.sort((a, b) => (b.clientWidth * b.clientHeight) - (a.clientWidth * a.clientHeight));
                    const v = vs[0];
                    v.muted = true; v.playbackRate = 1;
                    try { v.currentTime = t; } catch (e) {}
                    v.play().catch(() => {});
                }""",
                t,
            )
        except Exception:
            pass

    def verify(self):
        """复核：展开目录，按 .ziProgress 统计真实完成数。"""
        self.go_index()
        self.expand_all()
        items = self.list_resources()
        done = sum(1 for x in items if x["pct"] >= 100)
        return {"total": len(items), "done": done, "items": items}

    def brush_all(self, progress_cb=None):
        if not self.go_index():
            self._log("课程目录迟迟没有加载出来（可能网速很慢或登录已失效），"
                      "本次先不刷，请确认页面能正常打开后重试", "error")
            return {"total": 0, "todo": 0, "ok": 0, "ran": 0, "done_after": 0,
                    "total_after": 0, "failed": [], "error": "目录未加载"}

        self.expand_all()
        items = self.list_resources()

        # 三分类：能刷的（有进度条的视频/文档）/ 题目类（要自己答）/ 无进度标记的其它
        quizzes = [x for x in items if x["kind"] == "quiz"]
        marked = [x for x in items if x["pct"] >= 0 and x["kind"] != "quiz"]
        no_prog = len(items) - len(marked) - len(quizzes)
        todo = [x for x in marked if x["pct"] < 100]

        summary = (f"资源 {len(items)} 项：可刷 {len(marked)} 项"
                   f"（已完成 {len(marked) - len(todo)} 项），待刷 {len(todo)} 项")
        if quizzes:
            summary += f"，测验/作业 {len(quizzes)} 项（需自己作答）"
        if no_prog:
            summary += f"，{no_prog} 项无进度标记已跳过"
        self._log(summary)
        if quizzes:
            for q in quizzes[:8]:
                self._log(f"  · 需自己答：{q['name'][:46]}" +
                          ("（已完成）" if q["pct"] >= 100 else ""))
            if len(quizzes) > 8:
                self._log(f"  · 另有 {len(quizzes) - 8} 项题目类资源，见网页目录")

        if not todo:
            self._log("可刷的资源已全部完成，无需刷课")
            return {"total": len(items), "todo": 0, "ok": 0, "ran": 0,
                    "done_after": len(marked), "total_after": len(marked),
                    "failed": [], "quiz": [x["name"] for x in quizzes]}

        ok, ran, failed = 0, 0, []
        fail_streak = 0          # 连续「进度没涨」的项数
        aborted = False          # 是否因系统性问题提前中止
        for i, r in enumerate(todo, 1):
            if self._stopped():
                break
            if self.captcha_hint() and not self.wait_captcha():
                aborted = True
                self._log("因安全验证未通过提前中止，剩下的项目留着下次刷", "error")
                break
            if progress_cb:
                progress_cb(i, len(todo), r["name"])
            self._log(f"[{i}/{len(todo)}] {r['name'][:40]}（当前 {r['prog'] or '无进度'}，"
                      f"{KIND_LABEL.get(r['kind'], '资源')}）")
            self.br.guard_flush()

            confirmed, executed = False, False
            for attempt in (1, 2, 3):
                if self._stopped():
                    break
                if not self.open_resource(r, retries=2):
                    if attempt < 3:
                        self._log("  页面还没打开，等一会儿重来（网速慢时属正常）", "warn")
                        self.br.wait(2000 * attempt)
                        continue
                    self._log("  多次等待仍未加载出来，跳过这一项", "warn")
                    break

                res = self._learn_current(r["name"])
                if not res["ok"]:
                    if attempt < 3:
                        self._log(f"  没确认刷到（{res['note']}），重试", "warn")
                        self.br.wait(1500 * attempt)
                        continue
                    break
                if not executed:
                    executed = True
                    ran += 1

                # 尽量回读真实进度；当前页读不到就留到结尾统一复核，不谎报
                pct = self.progress_of(r["id"])
                if pct is None:
                    confirmed = None
                elif pct >= 100 or (r["pct"] >= 0 and pct > r["pct"]):
                    confirmed = True
                elif self._wait_progress(r["id"], r["pct"], timeout=6):
                    confirmed = True
                else:
                    confirmed = False

                if confirmed is True:
                    break
                if confirmed is False and attempt < 3:
                    self._log(f"  进度没涨（仍 {pct}%），再试一次", "warn")
                    self.br.wait(2500)
                    continue
                break

            if confirmed is True:
                ok += 1
                fail_streak = 0
                self._log("  ✓ 已完成")
            elif confirmed is None and executed:
                fail_streak = 0
                self._log("  ✓ 已刷完（当前页读不到进度，结尾统一复核）")
            else:
                failed.append(r["name"])
                fail_streak += 1
                self._log("  × 未确认完成，稍后可点「⑤ 复核」再查", "warn")
            self.br.shot(f"brush_{i:03d}")
            self.br.guard_flush()

            if fail_streak >= 2:
                aborted = True
                self._log("连续 2 项进度都没涨：多半是弹窗没关掉、登录失效或平台没记录。"
                          "已停止刷后面的资源，避免整轮白刷；处理完提示后重试", "error")
                self.br.shot("brush_aborted")
                break

        # 收尾：回目录复核真实完成数
        self.go_index()
        self.expand_all()
        after = self.list_resources()
        done_after = sum(1 for x in after if x["pct"] >= 100)
        self._log(f"刷课结束：本次执行 {ran}/{len(todo)} 项，当场确认 {ok} 项，"
                  f"目录标记完成 {done_after}/{len(after)}"
                  + ("（已提前中止）" if aborted else ""))
        if quizzes:
            self._log(f"提醒：还有 {len(quizzes)} 项测验/作业需要你在网页里自己作答，本工具不代答")
        return {"total": len(items), "todo": len(todo), "ok": ok, "ran": ran,
                "done_after": done_after, "total_after": len(after), "failed": failed,
                "aborted": aborted, "quiz": [x["name"] for x in quizzes]}
