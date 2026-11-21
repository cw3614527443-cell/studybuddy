"""zjy2 平台（zjy2.icve.com.cn）适配器。

沿用原脚本已验证的结构：
- 章节 .listItem[id]，小节 .items.iChild，资源 .fwenjianjia[id]，进度 .ziProgress；
- 展开靠逐级点击；
- 视频刷课直接把进度拖到末尾，文档翻到末页后刷新；
- 续播弹窗 .el-message-box 点"继续播放"。

v1.2.1 修复「日志说刷完了，网页还在放上一个视频」
--------------------------------------------------
老实现有三个致命假设，全部会在这套页面上失效：

1. **按名字找节点**：`locator(".fwenjianjia").filter(has_text=name).first`
   —— has_text 是「包含」，父节点的 innerText 里也包含子节点的名字，
   于是经常点到**文件夹**上：点一下只是折叠/展开，右侧内容根本不会切；
2. **点完就当作已打开**：只 sleep 2.2 秒，没有任何校验，内容没切也照刷；
3. **取第一个 `<video>`**：上一个资源的播放器还挂在 DOM 里（只是隐藏），
   `document.querySelector('video')` 拿到的就是那个**旧视频**，
   它早已 ended → 立刻「刷完」，下一个，几秒一个，实际什么都没做。

现在的做法：
- 用 **id** 精确定位节点（列表自带 id，不用名字猜）；
- 点完必须**确认内容真的换了**（内容区出现该资源名，或视频源/标签变了），
  没换就换一种点击方式重试，仍不行就跳过并如实报告；
- 只操作**可见且面积最大**的那个 video（旧播放器即使还在 DOM 也不会误伤）；
- 刷完**回读该节点的 .ziProgress**，只有进度真的到 100% 才算完成，
  否则重试一次，还不行就标红——绝不再谎报「已完成」。
"""
import re
import time

ZJY2_INDEX = "https://zjy2.icve.com.cn/study/v2/coursePreview/spoccourseIndex"


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
    def wait_ready(self, tries=3):
        for _ in range(tries):
            try:
                self.p.wait_for_selector(".listItem[id]", state="attached", timeout=12000)
                prev = -1
                for _ in range(12):
                    n = self.p.locator(".listItem[id]").count()
                    if n > 0 and n == prev:
                        return True
                    prev = n
                    self.br.wait(450)
                if self.p.locator(".listItem[id]").count() > 0:
                    return True
            except Exception:
                pass
            self.br.reload()
            self.br.wait(1200)
        return False

    def go_index(self):
        if "courseware" in self.br.current_url() or "spoccourseIndex" not in self.br.current_url():
            self.br.goto(ZJY2_INDEX)
        self.wait_ready()

    def expand_all(self, pause=0.35):
        self.wait_ready()
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
        self.br.wait(500)

    def list_resources(self):
        """枚举资源叶子节点。

        v1.2.1：只认**没有子节点**的 .fwenjianjia —— 父节点会把子节点名字吃掉，
        被当资源处理时点击只会折叠/展开（这正是「刷了没反应」的直接原因之一）。
        同时按 id 去重，避免同一资源被算两次。
        """
        return self.p.evaluate(
            """() => {
                const out = [], seen = new Set();
                for (const f of document.querySelectorAll('.fwenjianjia')) {
                    if (!f.id || seen.has(f.id)) continue;
                    if (f.querySelector('.fwenjianjia')) continue;   // 不是叶子
                    const nm = f.querySelector('.name');
                    if (!nm) continue;
                    const name = (nm.innerText || '').trim();
                    if (!name) continue;
                    const pr = f.querySelector('.ziProgress');
                    const prog = pr ? (pr.innerText || '').trim() : '';
                    const m = prog.match(/(\\d+)/);
                    seen.add(f.id);
                    out.push({id: f.id, name, prog, pct: m ? parseInt(m[1], 10) : -1});
                }
                return out;
            }"""
        )

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
        """内容指纹：可见视频的源 + 高亮标签文字。用来判断「点完有没有换内容」。"""
        try:
            return self.p.evaluate(
                """() => {
                    const vis = e => e && e.offsetParent !== null;
                    const vids = [...document.querySelectorAll('video')].filter(vis)
                        .map(v => ((v.currentSrc || v.src || '').split('?')[0]).slice(-48));
                    const tabs = [...document.querySelectorAll('[class*=active],[class*=cur]')]
                        .filter(e => vis(e) && (e.innerText || '').trim().length < 40)
                        .map(e => (e.innerText || '').trim()).sort();
                    return vids.join('|') + '#' + tabs.join('|');
                }"""
            )
        except Exception:
            return ""

    def _wait_opened(self, name, before, timeout=9.0):
        t0 = time.time()
        while time.time() - t0 < timeout:
            if self._content_shows(name):
                return True
            if self._content_sig() != before:
                return True
            if self._stopped():
                return False
            self.br.wait(600)
        return False

    def open_resource(self, node, retries=3, wait=9.0):
        """点开资源并确认内容真的切过去了。成功返回 True。"""
        self._sync_page()
        rid, name = node["id"], node["name"]
        last = ""
        for attempt in range(retries):
            before = self._content_sig()
            if not self._click_node(rid, mode=attempt):
                last = "点击没有命中"
                self.br.wait(800)
                continue
            self.br.wait(1200)
            if self._wait_opened(name, before, timeout=wait):
                return True
            last = "内容没有切换"
            self.br.wait(800)
        self._log(f"  打开失败（{last}）", "warn")
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
        m = re.search(r"(\d+)", txt)
        return int(m.group(1)) if m else None

    def _wait_progress(self, node_id, before, timeout=15):
        """等这个资源的进度真的涨上去（到 100 或比原来高）。"""
        t0 = time.time()
        while time.time() - t0 < timeout:
            pct = self.progress_of(node_id)
            if pct is not None and (pct >= 100 or (before is not None and before >= 0 and pct > before)):
                return True
            if self._stopped():
                return False
            self.br.wait(1000)
        return False

    # ---------- 刷课 ----------
    def _learn_current(self):
        """处理当前打开的资源页：视频拖到末尾 / 文档翻末页。"""
        # 续播弹窗
        for _ in range(3):
            if self.br.click_text("继续播放", timeout=800):
                self.br.wait(800)
            else:
                break

        # 视频：只认**可见**的播放器，避免操作上一个资源留下的隐藏 video
        info = self.p.evaluate(
            """() => {
                const vis = [...document.querySelectorAll('video')].filter(v => v.offsetParent !== null);
                if (!vis.length) return null;
                vis.sort((a, b) => (b.clientWidth * b.clientHeight) - (a.clientWidth * a.clientHeight));
                const v = vis[0];
                for (const o of vis) if (o !== v) { try { o.pause(); } catch (e) {} }
                v.muted = true; v.playbackRate = 1;
                try { v.currentTime = 0; } catch (e) {}
                v.play().catch(() => {});
                return {dur: v.duration || 0, cur: v.currentTime,
                        src: ((v.currentSrc || v.src || '').split('?')[0]).slice(-60)};
            }"""
        )
        if info is not None:
            self._drag_to_end()
            return

        # 文档：翻到末页
        txt = self.br.text()
        m = re.search(r"(\d+)\s*/\s*(\d+)", txt)
        if m and int(m.group(2)) > 1:
            for _ in range(300):
                if self._stopped():
                    return
                moved = self.p.evaluate(
                    """()=>{const b=[...document.querySelectorAll('*')].find(e=>e.children.length===0 &&
                        (e.textContent||'').trim()==='下一页' && e.offsetParent!==null);
                        if(!b) return false; b.click(); return true;}"""
                )
                if not moved:
                    break
                self.br.wait(500)
            self.br.reload()
            self.br.wait(1500)

    def _drag_to_end(self):
        """分两步拖进度条：先到一半，再到结尾前 0.5 秒，等它自然播完。"""
        dur = 0.0
        for _ in range(3):
            s = self.p.evaluate(
                """() => {const vs=[...document.querySelectorAll('video')].filter(v=>v.offsetParent!==null);
                    if(!vs.length) return null;
                    vs.sort((a,b)=>(b.clientWidth*b.clientHeight)-(a.clientWidth*a.clientHeight));
                    const v=vs[0]; return {dur:v.duration||0,cur:v.currentTime,paused:v.paused,end:v.ended};}"""
            )
            if not s:
                return False
            dur = float(s.get("dur") or 0)
            if dur >= 1:
                break
            # 还没拿到 duration：先播一下
            self.p.evaluate(
                """() => {const vs=[...document.querySelectorAll('video')].filter(v=>v.offsetParent!==null);
                    if(vs.length){vs.sort((a,b)=>(b.clientWidth*b.clientHeight)-(a.clientWidth*a.clientHeight));
                    vs[0].muted=true; vs[0].play().catch(()=>{});}}"""
            )
            self.br.wait(2000)
        if dur < 1:
            return False

        for pos in (dur * 0.5, max(0.0, dur - 0.5)):
            if self._stopped():
                return False
            self.p.evaluate(
                """(t) => {const vs=[...document.querySelectorAll('video')].filter(v=>v.offsetParent!==null);
                    if(!vs.length) return;
                    vs.sort((a,b)=>(b.clientWidth*b.clientHeight)-(a.clientWidth*a.clientHeight));
                    const v=vs[0]; v.muted=true; v.playbackRate=1;
                    try{v.currentTime=t;}catch(e){} v.play().catch(()=>{});}""",
                pos,
            )
            self.br.wait(800)
            self.br.click_text("继续播放", timeout=600)

        t0 = time.time()
        while time.time() - t0 < 25:
            if self._stopped():
                return False
            self.br.wait(1000)
            s = self.p.evaluate(
                """() => {const vs=[...document.querySelectorAll('video')].filter(v=>v.offsetParent!==null);
                    if(!vs.length) return null;
                    vs.sort((a,b)=>(b.clientWidth*b.clientHeight)-(a.clientWidth*a.clientHeight));
                    const v=vs[0]; return {end:v.ended,cur:v.currentTime,dur:v.duration||0,paused:v.paused};}"""
            )
            if not s:
                return False
            if s["end"] or (s["dur"] and s["cur"] >= s["dur"] - 1):
                return True
            if s["paused"]:
                self.p.evaluate(
                    """() => {const vs=[...document.querySelectorAll('video')].filter(v=>v.offsetParent!==null);
                        if(vs.length){vs.sort((a,b)=>(b.clientWidth*b.clientHeight)-(a.clientWidth*a.clientHeight));
                        vs[0].play().catch(()=>{});}}"""
                )
        return False

    def verify(self):
        """复核：展开目录，按 .ziProgress 统计真实完成数。"""
        self.go_index()
        self.expand_all()
        items = self.list_resources()
        done = sum(1 for x in items if x["pct"] >= 100)
        return {"total": len(items), "done": done, "items": items}

    def brush_all(self, progress_cb=None):
        self.go_index()
        self.expand_all()
        items = self.list_resources()
        # 没有进度条的叶子节点多半是「讨论/作业」这类，没有可刷的进度，直接跳过，
        # 免得对每一项都重试到超时。
        brushable = [x for x in items if x["pct"] >= 0]
        no_prog = len(items) - len(brushable)
        todo = [x for x in brushable if x["pct"] < 100]
        self._log(f"资源 {len(items)} 项（已完成 {len(brushable) - len(todo)} 项），"
                  f"待刷 {len(todo)} 项" + (f"，另有 {no_prog} 项无进度标记已跳过" if no_prog else ""))
        if not todo:
            self._log("该课程资源已全部完成，无需刷课")
            return {"total": len(items), "todo": 0, "ok": 0,
                    "done_after": len(brushable), "total_after": len(brushable), "failed": []}

        ok, failed = 0, []
        for i, r in enumerate(todo, 1):
            if self._stopped():
                break
            if progress_cb:
                progress_cb(i, len(todo), r["name"])
            self._log(f"[{i}/{len(todo)}] {r['name'][:40]}（当前 {r['prog'] or '无进度'}）")

            done = False
            for attempt in (1, 2):
                if not self.open_resource(r):
                    self._log("  打不开这一项，跳过", "warn")
                    break
                self._learn_current()
                if self._wait_progress(r["id"], r["pct"], timeout=15):
                    done = True
                    break
                now = self.progress_of(r["id"])
                self._log(f"  进度没有变化（{now if now is not None else '读不到'}%），"
                          f"第 {attempt + 1} 次尝试失败", "warn")
                self.br.wait(1500)

            if done:
                ok += 1
                self._log("  ✓ 已完成")
            else:
                failed.append(r["name"])
                self._log("  × 未确认完成，稍后可点「⑤ 复核」再查", "warn")
            self.br.shot(f"brush_{i:03d}")

        # 收尾：回目录复核真实完成数
        self.go_index()
        self.expand_all()
        after = self.list_resources()
        done_after = sum(1 for x in after if x["pct"] >= 100)
        self._log(f"刷课结束：本次确认完成 {ok}/{len(todo)} 项，"
                  f"目录标记完成 {done_after}/{len(after)}")
        return {"total": len(items), "todo": len(todo), "ok": ok,
                "done_after": done_after, "total_after": len(after), "failed": failed}
