"""AI 优课（ai.icve.com.cn）平台适配器。

基于实测验证的逻辑：
- .collapse-panel 模块，click i.jiantou 触发懒加载展开；
- div.node 为资源节点，p.title 为名称，i[class*=checkmark] 中 wc=完成、jd=未完成；
- **刷课**（v1.0.5 起）：打开视频后直接把进度条拖到末尾即可记为完成，不必真实观看；
- **刷时长**：必须从头 1x 真实播放满整段，才计入观看学时；
- 续播弹窗「检测到上次播放至X分X秒，是否继续？」：刷课点「继续播放」后直接拖结尾，
  刷时长点「重新开始」保证从 0 计时。统一由 dismiss_modal() 处理；
- 文档：翻到末页后刷新记为完成；分页计数器必须在"下一页"所在 .page 容器内读取。
"""
import os
import re
import time

from config import AI_ICVE_HOME

# 页面上可能同时存在多个 video（上一个资源留下的播放器只是隐藏，并没有被移除）。
# 一旦 querySelector('video') 抓到那个旧的，就会出现「日志说刷完了、网页还在放上一个视频」，
# 所以统一只操作**可见且面积最大**的那一个。
_PICK_VIDEO = """const pick = () => {
    const vs = [...document.querySelectorAll('video')].filter(v => v.offsetParent !== null);
    if (!vs.length) return null;
    vs.sort((a, b) => (b.clientWidth * b.clientHeight) - (a.clientWidth * a.clientHeight));
    return vs[0];
};
"""


class AiIcve:
    name = "AI 优课"

    def __init__(self, br, bus=None):
        self.br = br
        self.bus = bus
        self.p = br.page

    # ---------- 基础 ----------
    def _log(self, m, level="info"):
        if self.bus:
            self.bus.log(m, level)

    def _stopped(self):
        return self.bus.should_stop() if self.bus else False

    def read_watch(self, timeout=20, stop_grace=15, ignore_stop=False):
        """读取首页观看时长(小时)与学习次数。会导航到首页，故仅在未播放时调用。

        - 页面是 SPA，刚 load 完数字区块可能还没渲染，固定等待容易读到 None → 轮询。
        - v1.0.2：用户点「停止」后不再立刻放弃，而是再给 stop_grace 秒把数字读出来。
        - v1.0.6：**任务收尾的读取不应受「停止」影响**。之前点停止后立刻跳首页、只等
          6 秒，页面还没渲染完就放弃 → 日志出现「5.16 → None 时」。现在收尾调用传
          ignore_stop=True：完全无视停止标志，先等 25 秒，还读不到就刷新重试 12 秒。
        """
        self.br.goto(AI_ICVE_HOME)
        for attempt in (1, 2):
            # 首次别死等太久：10 秒没出来基本就是本次导航落错了路由，刷新比干等有效
            budget = min(timeout, 10) if attempt == 1 else timeout
            deadline = time.time() + budget
            stop_deadline = None
            while time.time() < deadline:
                t = self.br.text()
                m = re.search(r"观看时长\s*([\d.]+)\s*时", t)
                if m:
                    h = float(m.group(1))
                    n = re.search(r"学习次数\s*(\d+)\s*次", t)
                    return h, (int(n.group(1)) if n else None)
                if self._stopped() and not ignore_stop:
                    if stop_deadline is None:
                        stop_deadline = time.time() + stop_grace
                        self._log(f"已请求停止，但仍在 {stop_grace} 秒内尝试读取最终时长…")
                    elif time.time() > stop_deadline:
                        break
                self.br.wait(1000)
            if attempt == 2:
                break
            self._log("首页数字区块未渲染，刷新一次重试…", "warn")
            try:
                self.br.reload()
            except Exception:
                pass
            self.br.wait(2000)
        self._log("读取观看时长失败：页面始终没有出现「观看时长」数字，"
                  "可能掉登录或平台改版。已播放的学时平台已经记录，重开浏览器点②即可刷新", "warn")
        return None, None

    def read_watch_inplace(self):
        """不导航、在当前页读时长（仅当当前页是首页时有效）。"""
        t = self.br.text()
        m = re.search(r"观看时长\s*([\d.]+)\s*时", t)
        n = re.search(r"学习次数\s*(\d+)\s*次", t)
        return (float(m.group(1)) if m else None, int(n.group(1)) if n else None)

    def logged_in(self):
        t = self.br.text()
        return ("登录" not in t[:200]) or ("我的课程" in t) or ("学习次数" in t)

    def enter_course(self, course_name="大学生心理健康", timeout=20):
        """从首页课程卡片进入课程教学内容。

        v1.0.6：首页卡片是异步渲染的，之前只等 2.5 秒就找一次，网络慢一点就报
        「未找到课程」，连带复核也失败。改成轮询 20 秒，期间必要时重新打开首页。
        """
        deadline = time.time() + timeout
        ok = False
        while True:
            ok = self.p.evaluate(
                """(name) => {
                    const e=[...document.querySelectorAll('.course-name')].find(x=>(x.textContent||'').trim()===name);
                    if(!e) return false; e.click(); return true;
                }""",
                course_name,
            )
            if ok:
                break
            if time.time() > deadline:
                self._log(f"未找到课程「{course_name}」，请确认课程名与首页卡片完全一致"
                          "（差一个字都会找不到）", "warn")
                return False
            if "my-excellent-home" not in self.br.current_url():
                self.br.goto(AI_ICVE_HOME)
            self.br.wait(1500)
        self.br.wait(3500)
        self.br.click_text("教学内容", timeout=6000)
        self.br.wait(3000)
        return "kcnr" in self.br.current_url()

    # ---------- 目录 ----------
    def expand_all(self, passes=5):
        """展开所有折叠模块。

        v1.0.6：模块是**惰性加载**的 —— 点开一个模块才去请求它的子节点，而子模块里
        可能还有折叠项。只点一轮的话，第二轮才出现的子模块不会被展开，
        导致 `list_resources()` 少算资源（实测只数到 45 项，实际 125 项）。
        改为反复点，直到某一轮点不动、且节点数不再增长为止。
        """
        total = 0
        prev_cnt = -1
        for _ in range(passes):
            n = self.p.evaluate(
                """() => {
                    let c=0;
                    for(const el of document.querySelectorAll('i.jiantou,[class*=jiantou]')){
                        const panel=el.closest('.collapse-panel');
                        const pc=panel && panel.querySelector(':scope > .panel-content');
                        if(pc && pc.style.display==='none'){ el.click(); c++; }
                    }
                    return c;
                }"""
            )
            total += n
            self.br.wait(1500)
            cnt = self.p.evaluate("() => document.querySelectorAll('div.node').length")
            if n == 0 and cnt == prev_cnt:
                break
            prev_cnt = cnt
        return total

    def list_resources(self):
        """枚举资源节点：返回 [{id,name,type,done}]。"""
        return self.p.evaluate(
            """() => [...document.querySelectorAll('div.node')].filter(n=>n.id && n.querySelector('p.title'))
                .map(n=>{
                    const name=(n.querySelector('p.title').textContent||'').trim();
                    let type='other';
                    if(/\\.(mp4|mov)$/i.test(name)) type='video';
                    else if(/\\.(pdf|doc|docx|ppt|pptx)$/i.test(name)) type='doc';
                    const mark=n.querySelector('i[class*=checkmark]');
                    const cls=mark?String(mark.className):'';
                    const done=cls.includes('wc');
                    return {id:n.id,name,type,done};
                })"""
        )

    # ---------- 刷课 ----------
    def video_state(self):
        try:
            return self.p.evaluate(
                "() => {" + _PICK_VIDEO + """
                    const v = pick();
                    return v ? {dur:v.duration||0,cur:v.currentTime,paused:v.paused,
                    ready:v.readyState,end:v.ended,err:v.error?v.error.code:null} : null;}"""
            )
        except Exception:
            return None

    def node_done(self, node_id):
        """读目录里这个节点的完成标记（wc=已完成，jd=未完成）。读不到返回 None。"""
        try:
            cls = self.p.evaluate(
                """(id) => {const n=document.getElementById(id); if(!n) return null;
                    const m=n.querySelector('i[class*=checkmark]'); return m?String(m.className):null;}""",
                node_id,
            )
        except Exception:
            return None
        if cls is None:
            return None
        return "wc" in cls

    def _wait_done(self, node_id, timeout=12):
        """刷完回读目录标记 —— 这是唯一可信的「真的完成了」证据。"""
        t0 = time.time()
        while time.time() - t0 < timeout:
            d = self.node_done(node_id)
            if d:
                return True
            if self._stopped():
                return False
            self.br.wait(1500)
        return False

    def wait_video(self, timeout=20):
        """等播放器出现并拿到 metadata。"""
        t0 = time.time()
        while time.time() - t0 < timeout:
            s = self.video_state()
            if s and s["ready"] >= 1:
                return s
            if self._stopped():
                return None
            self.br.wait(600)
        return self.video_state()

    def open_node(self, node):
        """点击目录里的资源节点。"""
        try:
            return self.p.evaluate(
                """(id)=>{const n=document.getElementById(id); if(!n) return false;
                    const t=n.querySelector('p.title'); if(!t) return false;
                    t.click(); return true;}""",
                node["id"],
            )
        except Exception:
            return False

    def dismiss_modal(self, prefer_resume=True, rounds=4):
        """处理挡路的弹窗（主要是「检测到上次播放…是否继续？」）。

        只点「继续播放 / 重新开始」这两个按钮——它们只会出现在续播询问框里，
        不会误点到页面上的其他元素。prefer_resume=True 时优先「继续播放」
        （省掉从头加载的时间），刷时长场景传 False 走「重新开始」。
        返回点击次数。
        """
        n = 0
        for _ in range(rounds):
            txt = self.br.visible_modal_text() or ""
            if not txt:
                break
            hit = any(k in txt for k in ("是否继续", "继续播放", "重新开始", "检测到上次播放"))
            if not hit:
                break
            label = "继续播放" if prefer_resume else "重新开始"
            if self.br.click_text(label, timeout=1500):
                self._log(f"检测到续播弹窗 → 点「{label}」")
                self.br.wait(1200)
                n += 1
                continue
            label2 = "重新开始" if prefer_resume else "继续播放"
            if self.br.click_text(label2, timeout=1200):
                self._log(f"检测到续播弹窗 → 改为点「{label2}」")
                self.br.wait(1200)
                n += 1
                continue
            break
        return n

    def play_video(self, node, from_start=True):
        """打开视频并真实播放（刷时长用）。from_start=True 时从头播。"""
        # 打开前先清掉可能残留的弹窗，避免点击被遮罩吞掉
        self.dismiss_modal(prefer_resume=False, rounds=2)
        if not self.open_node(node):
            return None
        self.br.wait(2500)
        self.dismiss_modal(prefer_resume=not from_start)
        self.br.wait(1200)
        st = self.p.evaluate(
            "() => {" + _PICK_VIDEO + """
                const v = pick(); if (!v) return null;
                if (v.currentTime > 3) { try { v.currentTime = 0; } catch(e){} }
                v.muted = true; v.volume = 0; v.playbackRate = 1; v.play().catch(()=>{});
                return {dur:v.duration||0,cur:v.currentTime,paused:v.paused,ready:v.readyState};}"""
        )
        return st

    def watch_video_to_end(self, node, timeout_sec=1800, stop_flag=False):
        """把一个视频从头播到结尾。返回是否播完。"""
        st = self.play_video(node)
        if not st:
            return False
        t0 = time.time()
        last_report = t0
        while time.time() - t0 < timeout_sec:
            if self._stopped():
                return False
            self.br.wait(15000)
            s = self.p.evaluate(
                "() => {" + _PICK_VIDEO + """
                    const v = pick();
                    return v ? {end:v.ended,cur:v.currentTime,paused:v.paused,dur:v.duration||0,
                    ready:v.readyState,err:v.error?v.error.code:null,
                    fail:document.body.innerText.includes('视频播放失败')} : null;}"""
            )
            if not s:
                return False
            if s.get("fail") or s.get("err"):
                self._log(f"  播放失败(code={s.get('err')})，刷新恢复", "warn")
                self.br.reload()
                self.br.wait(4000)
                self.expand_all()
                return False
            if s["end"] or (s["dur"] and s["cur"] >= s["dur"] - 1):
                return True
            if s["paused"] and s["ready"] >= 2:
                self.p.evaluate(
                    "() => {" + _PICK_VIDEO + """
                        const v = pick();
                        if (v) { v.muted = true; v.playbackRate = 1; v.play().catch(()=>{}); }}"""
                )
            if time.time() - last_report >= 120:
                last_report = time.time()
                self._log(f"  播放中 {s['cur']:.0f}/{s['dur']:.0f}s")
        return False

    def skip_video(self, node, settle=4.0):
        """刷课：打开视频后直接把进度条拖到末尾，不实际观看。

        v1.2.1：拖完不算完 —— 必须回读目录里这个节点的完成标记（wc）。
        旧实现只认「video.ended」，一旦抓到上一个资源遗留的隐藏播放器（它早就 ended），
        就会立刻返回成功，于是出现「日志一路刷完、网页还在放上一个视频」。
        """
        self.dismiss_modal(rounds=2)
        if not self.open_node(node):
            self._log("  节点已失效（目录可能重排），跳过", "warn")
            return False
        self.br.wait(1500)
        self.dismiss_modal()          # 打开之后才弹的续播询问
        st = self.wait_video(20)
        if not st:
            self._log("  未出现播放器，跳过", "warn")
            return False

        node_id = node.get("id")
        for attempt in range(2):
            if self._stopped():
                return False
            if not self._drag_to_end(st, settle):
                self._log("  拖动进度条没成功", "warn")
                return False
            # 目录标记是唯一可信证据：等它变 wc
            if node_id and self._wait_done(node_id, timeout=12):
                return True
            if attempt == 0:
                self._log("  进度拖到末尾但平台没记完成，重新打开再试一次", "warn")
                self.dismiss_modal(rounds=2)
                if not self.open_node(node):
                    return False
                self.br.wait(2000)
                self.dismiss_modal()
                st = self.wait_video(12) or st
        return bool(node_id and self.node_done(node_id))

    def _drag_to_end(self, st, settle=4.0):
        """把当前可见播放器的进度分两步拖到结尾，等它真的 ended。"""
        dur = 0.0
        for _ in range(3):
            if self._stopped():
                return False
            s = self.video_state() or st
            dur = float(s.get("dur") or 0)
            if dur >= 1:
                break
            # 有的播放器要先 play 一次才有 duration
            self.p.evaluate(
                "() => {" + _PICK_VIDEO + """
                    const v = pick(); if (v) { v.muted = true; v.play().catch(()=>{}); }}"""
            )
            self.br.wait(2000)
        if dur < 1:
            return False

        # 第一步：拖到一半（让平台看到进度变化）
        self.p.evaluate(
            "() => {" + _PICK_VIDEO + """
                const v = pick(); if (!v) return;
                v.muted = true; v.playbackRate = 1;
                try { v.currentTime = v.duration * 0.5; } catch(e){}
                v.play().catch(()=>{});}"""
        )
        self.br.wait(800)
        self.dismiss_modal(rounds=1)
        # 第二步：拖到结尾
        self.p.evaluate(
            "() => {" + _PICK_VIDEO + """
                const v = pick(); if (!v) return;
                try { v.currentTime = Math.max(0, (v.duration || 0) - 0.5); } catch(e){}
                v.play().catch(()=>{});}"""
        )
        t0 = time.time()
        while time.time() - t0 < 25:
            if self._stopped():
                return False
            self.br.wait(1000)
            s2 = self.video_state()
            if not s2:
                return False
            if s2["end"] or (s2["dur"] and s2["cur"] >= s2["dur"] - 1):
                self.br.wait(int(settle * 1000))   # 等平台把「完成」上报上去
                return True
            if s2["paused"]:
                self.p.evaluate(
                    "() => {" + _PICK_VIDEO + """
                        const v = pick(); if (v) v.play().catch(()=>{}); }"""
                )
        return False

    def brush_doc(self, node):
        """文档：点开，翻到末页，刷新记为完成。"""
        self.dismiss_modal(rounds=2)
        if not self.open_node(node):
            return False
        self.br.wait(3000)
        # 尽可能点"下一页"直到没了
        for _ in range(200):
            if self._stopped():
                return False
            moved = self.p.evaluate(
                """()=>{
                    const btn=[...document.querySelectorAll('span,a,button,div')]
                        .find(e=>e.children.length===0 && (e.textContent||'').trim()==='下一页' && e.offsetParent!==null);
                    if(!btn) return false; btn.click(); return true;
                }"""
            )
            if not moved:
                break
            self.br.wait(600)
        self.br.wait(1500)
        return True

    def brush_all(self, only_undone=True, progress_cb=None):
        """刷完所有资源（视频+文档）。返回统计。

        v1.0.5：
        - 视频改为「拖进度条到末尾」，不再真实播放，速度快很多；
        - 全程自动处理「检测到上次播放…是否继续？」弹窗；
        - 待刷为 0 时明确说明「已全部完成」，不再输出让人误会的「处理 0 项」。
        """
        self.expand_all()
        items = self.list_resources()
        undone = [x for x in items if not x["done"]]
        todo = list(items) if not only_undone else undone
        already = len(items) - len(undone)
        if not todo:
            self._log(f"资源共 {len(items)} 项，已全部完成（{already}/{len(items)}），无需刷课")
            return {"total": len(items), "todo": 0, "ok": 0,
                    "done_after": already, "total_after": len(items)}
        self._log(f"资源共 {len(items)} 项（已完成 {already} 项），本次待刷 {len(todo)} 项")

        ok_n = 0
        for i, node in enumerate(todo, 1):
            if self._stopped():
                break
            if progress_cb:
                progress_cb(i, len(todo), node["name"])
            self._log(f"[{i}/{len(todo)}] {node['name'][:40]} ({node['type']})")
            if node["type"] == "video":
                done = self.skip_video(node)
            elif node["type"] == "doc":
                done = self.brush_doc(node)
            else:
                done = False
            if done:
                ok_n += 1
                self._log(f"  ✓ 已完成")
            else:
                self._log(f"  × 未确认完成，稍后可点「⑤ 复核完成情况」检查", "warn")
            self.br.shot(f"brush_{i:03d}")

        # 收尾复核：重新读一遍目录里的完成标记
        self.expand_all()
        after = self.list_resources()
        done_after = sum(1 for x in after if x["done"])
        self._log(f"刷课结束：本次处理 {ok_n}/{len(todo)} 项，目录标记完成 {done_after}/{len(after)}")
        return {"total": len(items), "todo": len(todo), "ok": ok_n,
                "done_after": done_after, "total_after": len(after)}

    # ---------- 刷时长 ----------
    def brush_watch_time(self, target_hours=5.0, max_minutes=300, progress_cb=None):
        """连续真实播放视频，把观看时长刷到目标小时。"""
        h0, c0 = self.read_watch()
        self._log(f"起始 观看时长={h0}时 学习次数={c0}")
        if h0 is not None and h0 >= target_hours:
            return {"start": h0, "end": h0, "reached": True}
        if not self.enter_course():
            return {"start": h0, "end": h0, "reached": False, "error": "进入课程失败"}
        self.expand_all()
        vids = [x for x in self.list_resources() if x["type"] == "video"]
        if not vids:
            return {"start": h0, "end": h0, "reached": False, "error": "未找到视频"}
        self._log(f"视频 {len(vids)} 个，开始连续真实播放")
        t0 = time.time()
        vi = 0
        # 先起播第一个
        self.play_video(vids[0])
        while time.time() - t0 < max_minutes * 60:
            if self._stopped():
                break
            self.br.wait(20000)
            modal = self.br.visible_modal_text()
            if modal:
                self.br.shot("watch_modal")
                self._log(f"  检测到弹窗：{modal[:80]}", "warn")
                self.dismiss_modal(prefer_resume=False, rounds=2)
            s = self.p.evaluate(
                "() => {" + _PICK_VIDEO + """
                    const v = pick();
                    return v ? {end:v.ended,cur:v.currentTime,paused:v.paused,dur:v.duration||0,
                    ready:v.readyState,err:v.error?v.error.code:null,
                    fail:document.body.innerText.includes('视频播放失败')} : null;}"""
            )
            if not s or (s.get("dur", 0) == 0 and s.get("ready", 0) == 0):
                vi += 1
                self.play_video(vids[vi % len(vids)])
                continue
            if s.get("fail"):
                self.br.shot("watch_fail")
                self.br.reload()
                self.br.wait(4000)
                self.expand_all()
                vi += 1
                self.play_video(vids[vi % len(vids)])
                continue
            if s["end"] or (s["dur"] and s["cur"] >= s["dur"] - 1):
                vi += 1
                self.play_video(vids[vi % len(vids)])
                self.br.shot(f"watch_next_{vi:03d}")
                continue
            if s["paused"] and s["ready"] >= 2:
                self.p.evaluate(
                    "() => {" + _PICK_VIDEO + """
                        const v = pick();
                        if (v) { v.muted = true; v.playbackRate = 1; v.play().catch(()=>{}); }}"""
                )
            if progress_cb:
                # 用已播时长粗估进度
                elapsed = (time.time() - t0) / 60.0
                if h0 is not None:
                    est = h0 + elapsed / 60.0
                    pct = min(99, int((est - h0) / max(0.01, target_hours - h0) * 100))
                    progress_cb(pct, f"已播放 {elapsed:.0f} 分钟，预计 {est:.2f} 时")
        # 收尾：即使已经点过「停止」，也必须把最终数字读出来（ignore_stop=True）。
        # 这是 v1.0.6 的核心修复 —— 之前停止后直接放弃，界面显示「5.16 → None 时」。
        self.dismiss_modal(rounds=1)
        elapsed_min = (time.time() - t0) / 60.0
        self._log(f"本次真实播放约 {elapsed_min:.1f} 分钟，读取最终观看时长…")
        h1, c1 = self.read_watch(timeout=25, ignore_stop=True)
        return {"start": h0, "end": h1, "minutes": elapsed_min,
                "final_read_failed": h1 is None,
                "reached": (h1 is not None and h1 >= target_hours)}

    # ---------- 复核 ----------
    def verify(self):
        """展开全部并统计完成情况。"""
        self.expand_all()
        items = self.list_resources()
        done = sum(1 for x in items if x["done"])
        return {"total": len(items), "done": done, "items": items}
