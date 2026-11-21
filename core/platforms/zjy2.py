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

    // 文档页码：只认 "12 / 34" 这种独立的短文本，避免把日期、学年当页码
    let pages = null;
    for (const e of document.querySelectorAll('span,div,i,b,em,li,p')) {
        if (e.children.length !== 0 || !vis(e)) continue;
        const s = (e.textContent || '').trim();
        const m = s.match(/^(\d{1,4})\s*[\/]\s*(\d{1,4})$/);
        if (!m) continue;
        const cur = +m[1], tot = +m[2];
        if (tot >= 1 && tot <= 3000 && cur >= 1 && cur <= tot) { pages = {cur, tot}; break; }
    }

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
        """
        raw = self.p.evaluate(
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
                    seen.add(f.id);
                    out.push({id: f.id, name, prog, pct});
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
        if not (st.get("video") or st.get("pages") or st.get("frame") or st.get("canvas")):
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

    # ---------- 刷课 ----------
    def _dismiss_doc_resume(self):
        """关文档类续播弹窗「上次观看到第X页，是否继续观看？」→ 点「确定」。

        只看按钮所在浮层的文案，普通公告里的「确定」不会被误点。
        """
        try:
            return bool(self.p.evaluate(
                """() => {
                    const vis = e => {
                        const r = e.getBoundingClientRect();
                        return r.width > 1 && r.height > 1 &&
                               getComputedStyle(e).visibility !== 'hidden';
                    };
                    for (const b of document.querySelectorAll(
                            'button,[role=button],a,[class*=btn],[class*=button]')) {
                        if (!vis(b) || (b.textContent || '').trim() !== '确定') continue;
                        let p = b, i = 0, hit = false;
                        while (p && i++ < 10) {
                            if (/上次观看到|上次阅读到|上次浏览到/.test(p.innerText || '')) {
                                hit = true; break;
                            }
                            p = p.parentElement;
                        }
                        if (hit) { b.click(); return true; }
                    }
                    return false;
                }"""
            ))
        except Exception:
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
        st = self._content_state()
        kind, ready = self._ready_kind(st)
        if not ready:
            return {"kind": kind, "ok": False, "note": self._describe(st)}

        if kind == "video":
            if self._drag_to_end():
                return {"kind": "video", "ok": True, "note": ""}
            return {"kind": "video", "ok": False, "note": "播放器没能播到结尾"}

        return self._read_doc(st)

    def _read_doc(self, st):
        """文档类：翻到末页（单页则滚动停留），最后刷新让平台同步进度。"""
        pages = st.get("pages") or {}
        cur, tot = pages.get("cur"), pages.get("tot")

        moved = 0
        if tot and tot > 1:
            for _ in range(tot + 10):
                if self._stopped():
                    return {"kind": "doc", "ok": False, "note": "已停止"}
                if not self._click_next_page():
                    break
                moved += 1
                self.br.wait(650)
                now = self._doc_pages()
                if now and now["cur"] >= now["tot"]:
                    break
            self.br.wait(800)
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
        try:
            return self.p.evaluate(
                """() => {
                    const vis = e => e && e.offsetParent !== null;
                    for (const e of document.querySelectorAll('span,div,i,b,em,li,p')) {
                        if (e.children.length || !vis(e)) continue;
                        const m = (e.textContent || '').trim().match(/^(\\d{1,4})\\s*[\\/]\\s*(\\d{1,4})$/);
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
                    const cands = [...document.querySelectorAll('*')].filter(e =>
                        e.children.length === 0 && vis(e) &&
                        /^(下一页|下一頁|Next)$/.test((e.textContent || '').trim()));
                    if (!cands.length) return false;
                    cands[0].click();
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

    def _flush_progress(self):
        """刷新一次，让平台把刚看的进度上报/回显。刷新失败不影响结果。"""
        try:
            self.br.reload()
        except Exception:
            return
        # 等它重新渲染出来，但不强求（刷新只是为了同步进度）
        t0 = time.time()
        while time.time() - t0 < 12:
            st = self._content_state()
            kind, ready = self._ready_kind(st)
            if ready:
                break
            self.br.wait(700)

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
