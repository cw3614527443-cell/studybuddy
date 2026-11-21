"""zjy2 平台（zjy2.icve.com.cn）适配器。

沿用原脚本已验证的结构：
- 章节 .listItem[id]，小节 .items.iChild，资源 .fwenjianjia[id]，进度 .ziProgress；
- 展开靠逐级点击；
- 视频 2x 播放真实观看位置累计；文档翻到末页后刷新记为完成；
- 续播弹窗 .el-message-box 点"继续播放"。
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
        return self.p.evaluate(
            """() => [...document.querySelectorAll('.fwenjianjia')].map(f => ({
                id: f.id,
                name: (f.querySelector('.name')?.innerText || '').trim(),
                prog: (f.querySelector('.ziProgress')?.innerText || '').trim()
            }))"""
        )

    def open_resource(self, name):
        item = self.p.locator(".fwenjianjia").filter(has_text=name).first
        if item.count() == 0:
            return False
        item.locator(".name").first.click(timeout=10000)
        self.br.wait(2200)
        return True

    # ---------- 刷课 ----------
    def brush_all(self, progress_cb=None):
        self.go_index()
        self.expand_all()
        items = self.list_resources()
        # 进度为 100% 视为完成
        todo = [x for x in items if not re.search(r"100", x["prog"])]
        self._log(f"资源 {len(items)} 项，待刷 {len(todo)} 项")
        done_n = 0
        for i, r in enumerate(todo, 1):
            if self._stopped():
                break
            if progress_cb:
                progress_cb(i, len(todo), r["name"])
            self._log(f"[{i}/{len(todo)}] {r['name'][:40]} 进度={r['prog']}")
            try:
                self.open_resource(r["name"])
                self._learn_current()
                done_n += 1
            except Exception as e:
                self._log(f"  异常：{str(e)[:80]}", "warn")
        self.br.goto(ZJY2_INDEX)
        self.br.wait(2000)
        return {"total": len(items), "todo": len(todo), "ok": done_n}

    def _learn_current(self):
        """处理当前打开的资源页：视频 2x 播放/文档翻末页/关续播弹窗。"""
        # 关续播弹窗
        for _ in range(3):
            if self.br.click_text("继续播放", timeout=800):
                self.br.wait(800)
        # 视频
        has_video = self.p.evaluate("() => !!document.querySelector('video')")
        if has_video:
            self.p.evaluate(
                """()=>{const v=document.querySelector('video'); if(!v) return;
                    v.muted=true; v.playbackRate=2; v.play().catch(()=>{});}"""
            )
            t0 = time.time()
            while time.time() - t0 < 1800:
                if self._stopped():
                    return
                self.br.wait(10000)
                s = self.p.evaluate(
                    """()=>{const v=document.querySelector('video');
                        return v?{end:v.ended,cur:v.currentTime,dur:v.duration||0}:null;}"""
                )
                if not s or s["end"] or (s["dur"] and s["cur"] >= s["dur"] - 1):
                    break
            return
        # 文档：翻到末页
        txt = self.br.text()
        m = re.search(r"(\d+)\s*/\s*(\d+)", txt)
        if m:
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
