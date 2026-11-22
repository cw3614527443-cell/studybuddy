"""client.py —— 与常驻浏览器驱动（agent.mjs）通信的客户端。

所有脚本（catalog / video / quiz / run）都通过这里发命令，不直接操作浏览器。
协议：写 io/cmd.json  ->  agent.mjs 轮询执行  ->  写 io/result.json（带同一个 id）。

环境变量 BROWSER_ROOT 指定会话目录，不同账号互不干扰。
"""

import json
import os
import time

# 默认会话目录 = 本脚本所在 suite/ 的上一级（browser/）。
# 解压到任何位置都能用；多账号时用 BROWSER_ROOT 或 start.cmd 指定子目录。
DEFAULT_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

# StudyBuddy 集成：独立运行时没人设置，行为与原来完全一致。
# 软件内调用时注入 EventBus.should_stop，让长任务（播放/采题/答题）能响应「停止」。
_STOP_CHECK = None


def set_stop_check(fn):
    global _STOP_CHECK
    _STOP_CHECK = fn


def stopped():
    if _STOP_CHECK is None:
        return False
    try:
        return bool(_STOP_CHECK())
    except Exception:
        return False


class Browser:
    def __init__(self, root=None, page_index=0):
        self.root = root or os.environ.get('BROWSER_ROOT') or DEFAULT_ROOT
        self.io = os.path.join(self.root, 'io')
        self.shots = os.path.join(self.root, 'shots')
        os.makedirs(self.io, exist_ok=True)
        os.makedirs(self.shots, exist_ok=True)
        self.page_index = page_index
        self._seq = 0

    # ---------- 底层通道 ----------
    def send(self, cmd, timeout=60):
        self._seq += 1
        rid = 'c%d-%d' % (int(time.time() * 1000), self._seq)
        payload = dict(cmd)
        payload['id'] = rid
        if self.page_index is not None and 'pageIndex' not in payload:
            payload['pageIndex'] = self.page_index
        with open(os.path.join(self.io, 'cmd.json'), 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False)
        deadline = time.time() + timeout
        while time.time() < deadline:
            if stopped():
                return {'ok': False, 'error': 'STOPPED', 'id': rid}
            time.sleep(0.25)
            try:
                with open(os.path.join(self.io, 'result.json'), encoding='utf-8') as f:
                    r = json.load(f)
            except Exception:
                continue
            if r.get('id') == rid:
                return r
        return {'ok': False, 'error': 'TIMEOUT %ss' % timeout, 'id': rid}

    def ok(self, cmd, timeout=60):
        r = self.send(cmd, timeout)
        if not r.get('ok'):
            raise RuntimeError('%s failed: %s' % (cmd.get('type'), str(r.get('error'))[:400]))
        return r.get('data')

    # ---------- 页面操作 ----------
    def status(self):
        return self.ok({'type': 'status'})

    def titles(self):
        return self.ok({'type': 'titles'})

    def goto(self, url, settle=2000):
        return self.ok({'type': 'goto', 'url': url, 'settle': settle}, timeout=90)

    def reload(self, settle=2500):
        return self.ok({'type': 'reload', 'settle': settle}, timeout=90)

    def shot(self, name):
        path = name if os.path.isabs(name) else os.path.join(self.shots, name)
        return self.ok({'type': 'shot', 'path': path})['file']

    def js(self, code, frame=None, timeout=60):
        # 统一约定：code 是「函数体」，用 return 返回结果。
        # 有 frame 时驱动用 new Function(code) 执行；无 frame 时走 CDP，
        # 需要自己包一层函数，否则 return 会报 Illegal return statement。
        if not frame:
            code = '(function(){\n%s\n})()' % code
        cmd = {'type': 'eval', 'code': code}
        if frame:
            cmd['frameUrl'] = frame
        return self.ok(cmd, timeout=timeout)

    def text(self, frame=None):
        cmd = {'type': 'text'}
        if frame:
            cmd['frameUrl'] = frame
        return self.ok(cmd)

    def html(self, frame=None):
        cmd = {'type': 'html'}
        if frame:
            cmd['frameUrl'] = frame
        return self.ok(cmd)

    def frames(self):
        return [x['url'] for x in self.ok({'type': 'frames'})]

    def fill(self, selector, value):
        """填输入框（走 Playwright 的 fill，会派发 input 事件，受控输入也能识别）。"""
        return self.ok({'type': 'fill', 'selector': selector, 'value': value}, timeout=45)

    def click(self, selector=None, text=None, frame=None, settle=1500, timeout=15000):
        cmd = {'type': 'click', 'settle': settle, 'timeout': timeout}
        if selector:
            cmd['selector'] = selector
        if text:
            cmd['text'] = text
            cmd['exact'] = False
        if frame:
            cmd['frameUrl'] = frame
        return self.ok(cmd, timeout=settle / 1000.0 + timeout / 1000.0 + 45)

    def wait(self, ms):
        return self.ok({'type': 'wait', 'ms': ms})

    def focus_emu(self, enabled=True):
        """CDP 焦点模拟：页面永远认为自己是 focused（防「失焦即暂停」）。"""
        try:
            return self.ok({'type': 'focusemu', 'enabled': enabled})
        except Exception:
            return None

    def win_state(self, state='normal'):
        """窗口状态：minimized / normal / maximized（用于实测失焦场景）。"""
        try:
            return self.ok({'type': 'winstate', 'state': state})
        except Exception:
            return None

    def mouse_move(self, x=700, y=500):
        """真实鼠标移动（不点击），喂 mousemove 事件给页面的活动检测。"""
        try:
            return self.ok({'type': 'mouse', 'x': int(x), 'y': int(y)})
        except Exception:
            return None

    # ---------- 常用复合动作 ----------
    def frame_of(self, needle):
        for u in self.frames():
            if needle in u:
                return u
        return None

    def has_frame(self, needle):
        return self.frame_of(needle) is not None

    def find_page_with_url(self, needle, auto_switch=True):
        for p in self.titles():
            if needle in p['url']:
                if auto_switch:
                    self.page_index = p['i']
                return p['i']
        return None

    def locate_course(self):
        """把 page_index 切到「课程页」（课程目录页或单章节学习页）。"""
        for needle in ('studentstudy', 'mycourse/stu'):
            i = self.find_page_with_url(needle)
            if i is not None:
                return i
        return self.find_page_with_frame('mycourse/studentcourse')

    def find_page_with_frame(self, needle, auto_switch=True):
        """在所有标签页里找含指定框架的那一页，找到就把 page_index 切过去。"""
        for p in self.titles():
            i = p['i']
            try:
                r = self.send({'type': 'frames', 'pageIndex': i}, timeout=30)
            except Exception:
                continue
            urls = [x['url'] for x in (r.get('data') or [])] if r.get('ok') else []
            if any(needle in u for u in urls):
                if auto_switch:
                    self.page_index = i
                return i
        return None


def fmt(sec):
    try:
        sec = float(sec)
    except Exception:
        return '??'
    return '%d:%02d' % (int(sec) // 60, int(sec) % 60)
