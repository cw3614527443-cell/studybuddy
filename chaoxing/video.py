"""video.py —— 视频任务点：打开章节 -> 1.0 倍真实播放 -> 守到目标进度 -> 复核任务点是否变绿。

平台硬规则（实测，不要试图绕过）：
  * 任务点只在**播到视频结尾**（ended）时才被平台判完成——
    实测播到 96% 后刷新，目录仍然橙色，服务端只记了 70%；播到结尾再刷新立刻变绿。
    所以本脚本守到 ended（或 currentTime >= duration-1s），不做「卡 95% 就撤」；
  * 未完成前进度条不可拖拽，只能真实播放；
  * 播放时页面不可离开或最小化，窗口必须在前台；
  * 倍速被硬封：设 2 倍会被重置为 1 并暂停，所以脚本每秒都强制 playbackRate=1。

本版相对上一版的改动（无人值守相关）：
  1. 同页多视频：一个章节里挂多个 <video> 时按顺序逐个播完，不再只看第一个；
  2. 停滞自愈：进度 30s 不动就 bring 到前台 + 重新 play + 关掉「继续观看/知道了」弹窗；
  3. 完成复核：播完后刷新目录最多确认 3 次（平台写进度有延迟，一次常常读到旧快照）；
  4. 断点续跑：已完成条目重跑自动跳过，进度写 io/video-progress.json / video-done.json；
     断点只在两种情况下写：目录确认 done，或章节剩余任务点数确实减少（视频已记账）；
  5. 收尾报告：跑完自动列出「仍然待完成 = 需要人工答题」的条目，写 io/remaining.json。

用法：
    python video.py --list               # 只列待完成且有视频的条目（不播放，几秒钟）
    python video.py --check=7.1,8.1      # 只打开看视频绿标是否已完成，不播放
    python video.py --audit              # 巡检全部待完成章节：哪些视频其实已经刷完了
    python video.py --pending            # 跑全部待完成条目（自动跳过已完成/无视频/纯测验）
    python video.py 17.1                 # 单个
    python video.py 17.1,17.2,18.1       # 多个
    python video.py --pending --skip-quiz   # 批量刷视频，跳过标题像答题的条目（推荐）
    python video.py --pending --limit=10 --skip-quiz   # 只跑前 10 个，跑完再续
    python video.py --pending --max=60 --retry=2
    python video.py 17.1 --dry           # 只探测时长与是否可播，不真的守着播

退出码：0 全部达标；2 有超时/失败；3 驱动不可用。
"""

import json
import os
import random
import sys
import time

import catalog as catalog_mod
import doc as doc_mod
from client import Browser, fmt, stopped

POLL = 5          # 轮询间隔（秒）
END_GRACE = 12    # 播完后等平台回写学习时长（实测有 10s 级延迟，太早刷新会读到旧状态）
STALL_WARN = 6    # 连续 N 次没进度 = 30s，触发自愈
STALL_HARD = 24   # 连续 N 次 = 120s，判定卡死，重开章节


# ---------------------------------------------------------------- JS 片段
# 统一的遍历器：跨 iframe 递归收集所有 <video>，顺序固定（同一个 walk 顺序），
# 所以「第 i 个视频」在多次调用之间是稳定的。
_WALK = r"""
  var seen=[], out=[];
  function walk(w,d){
    if(d>6) return;
    for(var k=0;k<seen.length;k++){ if(seen[k]===w) return; }
    seen.push(w);
    try{ var vs=w.document.querySelectorAll('video');
         for(var i=0;i<vs.length;i++) out.push(vs[i]); }catch(e){}
    try{ var fs=w.document.querySelectorAll('iframe');
         for(var j=0;j<fs.length;j++){
           try{ var cw=fs[j].contentWindow; if(cw) walk(cw,d+1); }catch(e){}
         } }catch(e){}
  }
  walk(window,0);
"""

COLLECT_JS = "return (function(){" + _WALK + r"""
  var res=[];
  for(var n=0;n<out.length;n++){
    var v=out[n];
    res.push({i:n, d:String(v.duration), c:String(v.currentTime),
              p:!!v.paused, e:!!v.ended, r:String(v.playbackRate),
              src:String(v.currentSrc||v.src||'').slice(0,100)});
  }
  return JSON.stringify(res);
})()
"""

PLAY_JS = "return (function(){" + _WALK + r"""
  var i=%d;
  if(!out.length) return 'NOV';
  if(i<0) i=0;
  if(i>=out.length) i=out.length-1;
  var v=out[i];
  try{ v.playbackRate=1.0; }catch(e){}
  try{ if(v.muted!==true){ v.muted=true; } }catch(e){}
  try{ var pr=v.play(); if(pr&&pr.catch) pr.catch(function(){}); }catch(e){}
  return JSON.stringify({c:String(v.currentTime), d:String(v.duration), p:!!v.paused});
})()
"""

PAUSE_JS = "return (function(){" + _WALK + r"""
  var i=%d;
  if(!out.length) return 'NOV';
  if(i<0) i=0;
  if(i>=out.length) i=out.length-1;
  try{ out[i].pause(); out[i].playbackRate=1.0; }catch(e){}
  return JSON.stringify({c:String(out[i].currentTime), d:String(out[i].duration)});
})()
"""

# 关掉超星常见遮挡弹窗（只在停滞时调用，避免误点）
DISMISS_JS = r"""
return (function(){
  var words={'继续观看':1,'继续学习':1,'我知道了':1,'知道了':1,'确定':1,'确认':1,'我知道了。':1};
  var els=document.querySelectorAll('a,button,div,span,input');
  var clicked=0, names=[];
  for(var i=0;i<els.length && clicked<3;i++){
    var e=els[i];
    var t=(e.textContent||'').replace(/\s+/g,'').trim();
    if(!t || t.length>6) continue;
    if(!words[t]) continue;
    var r=e.getBoundingClientRect();
    if(r.width<10 || r.height<10) continue;
    if(r.bottom<0 || r.top>window.innerHeight) continue;
    var st=window.getComputedStyle(e);
    if(st.visibility==='hidden' || st.display==='none') continue;
    try{ e.click(); clicked++; names.push(t); }catch(err){}
  }
  return JSON.stringify({clicked:clicked, names:names});
})()
"""

OPEN_JS = r"""
return (function(){
  var out={clicked:false, mode:''};
  var a=document.querySelector('#cur%s .clicktitle') || document.querySelector('#cur%s .posCatalog_name');
  if(a){ a.click(); out.clicked=true; out.mode='dom'; return JSON.stringify(out); }
  var el=document.getElementById('cur%s');
  if(el){ el.click(); out.clicked=true; out.mode='item'; return JSON.stringify(out); }
  return JSON.stringify(out);
})()
"""


# ---------------------------------------------------------------- 小工具
def num(x):
    try:
        v = float(x)
        if v != v or v in (float('inf'), float('-inf')):
            return 0.0
        return v
    except Exception:
        return 0.0


def load_session(b):
    p = os.path.join(b.io, 'session.json')
    if not os.path.exists(p):
        raise RuntimeError('缺少 io/session.json（用 run.py open 生成，或手工写入 courseUrl）')
    with open(p, encoding='utf-8') as f:
        return json.load(f)


def load_json(path, default):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, obj):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)



# ---------------------------------------------------------------- 反「失焦暂停」
GUARD_JS = r"""
return (function(){
  var done=[];
  function alive(w,tag){
    try{
      if(w.__vguard) return;
      w.__vguard=1;
      // 1) 永远认为自己是 focused / visible
      try{ w.document.hasFocus=function(){return true;}; }catch(e){}
      try{
        Object.defineProperty(w.document,'hidden',{configurable:true,get:function(){return false;}});
        Object.defineProperty(w.document,'visibilityState',{configurable:true,get:function(){return 'visible';}});
      }catch(e){}
      // 2) 拦截 blur / visibilitychange：平台收不到「你切走了」这件事
      var kill=function(t){
        try{ w.addEventListener(t,function(e){ try{e.stopImmediatePropagation();}catch(x){} },true); }catch(e){}
      };
      kill('blur'); kill('visibilitychange');
      // 3) 鼠标移出窗口：拦 document 上的 mouseleave
      try{
        w.document.addEventListener('mouseleave',function(e){ try{e.stopImmediatePropagation();}catch(x){} },true);
        w.addEventListener('mouseout',function(e){
          var t=e.target;
          if(t===w.document||t===w.document.documentElement||t===w.document.body){
            try{e.stopImmediatePropagation();}catch(x){}
          }
        },true);
      }catch(e){}
      // 4) 心跳：每 2.5s 发一次 mousemove，满足「有活动」判定
      try{
        if(!w.__vbeat){
          w.__vbeat=w.setInterval(function(){
            try{
              var x=200+(Date.now()%400), y=180+(Date.now()%260);
              w.document.dispatchEvent(new w.MouseEvent('mousemove',{bubbles:true,clientX:x,clientY:y}));
            }catch(e){}
          },2500);
        }
      }catch(e){}
      // 5) 兜底：视频被暂停 1.5s 内拉回来
      try{
        w.document.addEventListener('pause',function(e){
          var v=e.target;
          if(!v||v.tagName!=='VIDEO'||v.ended) return;
          w.setTimeout(function(){
            try{ if(v.paused&&!v.ended){ v.playbackRate=1; var pr=v.play(); if(pr&&pr.catch) pr.catch(function(){}); } }catch(x){}
          },1500);
        },true);
      }catch(e){}
      done.push(tag);
    }catch(e){}
  }
  function walk(w,d,tag){
    if(d>6) return;
    alive(w,tag);
    try{
      var fs=w.document.querySelectorAll('iframe');
      for(var i=0;i<fs.length;i++){
        try{ var cw=fs[i].contentWindow; if(cw) walk(cw,d+1,tag+'>'+i); }catch(e){}
      }
    }catch(e){}
  }
  walk(window,0,'root');
  return JSON.stringify(done);
})()
"""


def install_guard(b):
    """装上「反失焦暂停」补丁：CDP 焦点模拟 + 各 iframe 内的属性篡改与心跳。"""
    ok_emu = b.focus_emu(True)
    try:
        frames = b.js(GUARD_JS)
    except Exception as e:
        frames = 'ERR %s' % str(e)[:80]
    return ok_emu, frames


_KEEP = [0]


def keepalive(b):
    """真实鼠标在窗口内移动 + 置前台，破「鼠标移出窗口」检测。"""
    _KEEP[0] += 1
    points = [(1240, 880), (1180, 850), (1300, 900), (1120, 870)]
    x, y = points[_KEEP[0] % len(points)]
    try:
        b.mouse_move(x, y)
    except Exception:
        pass


# ---------------------------------------------------------------- 页面动作
def probe(b):
    """返回当前页所有视频的状态列表；没有视频返回 []。"""
    raw = b.js(COLLECT_JS)
    if not isinstance(raw, str) or raw == 'NOV':
        return []
    try:
        return json.loads(raw)
    except Exception:
        return []


def vstate(b, idx=0):
    """兼容旧接口：返回第 idx 个视频的状态 dict。"""
    vids = probe(b)
    if not vids:
        return None
    return vids[min(idx, len(vids) - 1)]


def play(b, idx=0):
    return b.js(PLAY_JS % idx)


def pause(b, idx=0):
    try:
        return b.js(PAUSE_JS % idx)
    except Exception:
        return None


def bring_front(b):
    try:
        b.ok({'type': 'bring'})
        return True
    except Exception:
        return False


def dismiss_popups(b):
    try:
        raw = b.js(DISMISS_JS)
        d = json.loads(raw)
        if d.get('clicked'):
            print('      关掉弹窗: %s' % ','.join(d.get('names') or []), flush=True)
        return d.get('clicked', 0)
    except Exception:
        return 0


def wait_videos(b, max_s=45, kick=True):
    """打开章节后等播放器就绪。

    关键：超星视频在点播放之前 duration 一直是 NaN，必须踢一脚 play 才会去拉流。
    所以探测到 video 元素但时长为 0 时，先点一次播放，再继续轮询。
    """
    t0 = time.time()
    kicked = False
    while time.time() - t0 < max_s:
        vids = probe(b)
        if vids and num(vids[0]['d']) > 0:
            return vids
        if vids and kick and not kicked:
            dismiss_popups(b)
            play(b, 0)
            kicked = True
            time.sleep(3)
            continue
        time.sleep(2)
    return probe(b)


def video_marks(b, verbose=False):
    """读当前任务点标签页里每个任务点的完成标记（视频 / PPT / PDF 都算）。

    实测结论（很重要，别再靠文字判断）：
      「✓ 任务点已完成」不在 innerText 里，它是 `aria-label` + 容器类：
        <div class="ans-attach-ct videoContainer ans-job-finished">
          <div class="ans-job-icon ans-job-video" aria-label="任务点已完成">
      所以判定必须看 `.ans-attach-ct` 的 `ans-job-finished` / `.ans-job-icon`，
      文字匹配、body.innerText 一律无效（踩过）。
      右侧目录橙色 ≠ 没刷完，橙色只代表本章节还有别的任务点没做。

    v1.7.2：以前只数 `.videoContainer`，PPT/PDF 任务点没有这个 class，
    于是「PPT 明明显示任务点已完成」也算不出来，又被逐页翻了一遍 —— 白刷。
    现在按全部 `.ans-attach-ct` 统计：total / finished 是所有任务点，
    videos / videoFinished 才是视频子集。
    """
    js = r"""
    return (function(){
      var seen=[], acc={total:0, finished:0, videos:0, videoFinished:0, marks:[]};
      function scan(w,d){
        if(d>5) return;
        for(var k=0;k<seen.length;k++){ if(seen[k]===w) return; }
        seen.push(w);
        try{
          var doc=w.document;
          var vs=doc.querySelectorAll('.ans-attach-ct');
          for(var i=0;i<vs.length;i++){
            var el=vs[i];
            var ic=el.querySelector('.ans-job-icon');
            var fin=el.classList.contains('ans-job-finished') ||
                    !!(ic && (ic.getAttribute('aria-label')||'').indexOf('已完成') >= 0);
            var isVideo=el.classList.contains('videoContainer');
            // 既没有 finished 标记、也没有 job 图标的，是普通附件，不算任务点
            if(!fin && !ic && !el.classList.contains('ans-job-unfinished')) continue;
            acc.total++;
            if(fin) acc.finished++;
            if(isVideo){ acc.videos++; if(fin) acc.videoFinished++; }
            acc.marks.push({fin:fin ? 1 : 0, video:isVideo ? 1 : 0,
                            label:(ic && ic.getAttribute('aria-label')) || ''});
          }
        }catch(e){}
        try{
          var fs=w.document.querySelectorAll('iframe');
          for(var j=0;j<fs.length;j++){
            try{ var cw=fs[j].contentWindow; if(cw) scan(cw,d+1); }catch(e){}
          }
        }catch(e){}
      }
      scan(window,0);
      return JSON.stringify(acc);
    })()
    """
    try:
        import json as _json
        r = b.js(js)
        acc = _json.loads(r) if isinstance(r, str) else {}
        if verbose:
            print('    [marks] videos=%s finished=%s %s'
                  % (acc.get('videos'), acc.get('finished'), acc.get('marks')))
        return acc
    except Exception as e:
        if verbose:
            print('    [marks] err %s' % str(e)[:100])
        return {}


def page_label_done(b, verbose=False):
    """当前标签页里的任务点是否全部显示「任务点已完成」（绿标）。

    v1.7.2：以前只数视频，PPT 任务点永远算出 0 个视频 → 判定「未完成」，
    于是已经刷完的 PPT 又被逐页翻一遍（每页 1.4 秒，56 页就是 80 秒）。
    现在按全部任务点统计。
    """
    acc = video_marks(b, verbose=verbose)
    total = acc.get('total', 0)
    fin = acc.get('finished', 0)
    return total > 0 and fin >= total


def wait_label_done(b, tries=3, gap=10):
    """播完后等平台打绿标（有 10s 级延迟）。返回是否看到绿标。"""
    for k in range(tries):
        if k:
            time.sleep(gap)
        if page_label_done(b):
            return True
    return False


# ---------------------------------------------------------------- 章节内多任务点

TABS_JS = r"""
return (function(){
  var out=[];
  var lis=document.querySelectorAll('.prev_ul > li');
  for(var i=0;i<lis.length;i++){
    var e=lis[i];
    out.push({i:i,
              title:(e.getAttribute('title')||'').trim(),
              active:e.classList.contains('active')});
  }
  return JSON.stringify(out);
})()
"""


def tabs(b):
    """读章节顶部的任务点标签（如「1 PPT / 2 视频」）。

    一个章节常常挂多个任务点：PPT + 视频 + 文档。旧逻辑只处理打开章节时
    默认选中的第一个（通常是 PPT），同节的视频从来没被点开过 ——
    目录里的「N 个待完成任务点」就一直清不掉。
    """
    try:
        return json.loads(b.js(TABS_JS)) or []
    except Exception:
        return []


def switch_tab(b, i, settle=3.5):
    """切到第 i 个任务点标签，等它把内容加载出来。"""
    try:
        r = b.js('var l=document.querySelectorAll(".prev_ul > li");'
                 'if(l[%d]){l[%d].click(); return 1;} return 0;' % (i, i))
    except Exception:
        return False
    time.sleep(settle)
    return str(r).strip() in ('1', 'true')


LIMIT_JS = r"""
return (function(){
  var seen=[], hit=false;
  function scan(w,d){
    if(d>5 || hit) return;
    for(var k=0;k<seen.length;k++){ if(seen[k]===w) return; }
    seen.push(w);
    try{
      var doc=w.document;
      var all=doc.querySelectorAll('div,p,span,em,li');
      for(var i=0;i<all.length;i++){
        var t=(all[i].textContent||'').trim();
        if(t.length>90 || t.indexOf('任务点完成数已达上限')<0) continue;
        try{
          var st=w.getComputedStyle(all[i]);
          if(st.display==='none' || st.visibility==='hidden') continue;
          var r=all[i].getBoundingClientRect();
          if(r.width<1 || r.height<1) continue;
        }catch(e){}
        hit=true; return;
      }
    }catch(e){}
    try{
      var fs=w.document.querySelectorAll('iframe');
      for(var j=0;j<fs.length;j++){
        try{ if(fs[j].contentWindow) scan(fs[j].contentWindow,d+1); }catch(e){}
      }
    }catch(e){}
  }
  scan(window,0);
  return hit ? '1' : '0';
})()
"""


def job_limit_hit(b):
    """平台是否在提示「今日视频任务点完成数已达上限」。

    这是硬限制：今天继续刷，视频也不会被计成完成。以前不认这个提示，
    程序会在「播放→不算完成→重试」里空转到超时，看着就是「怎么这么慢」。
    """
    try:
        return str(b.js(LIMIT_JS)).strip() in ('1', 'true')
    except Exception:
        return False


def wait_marks(b, timeout=16):
    """等任务点标记渲染出来（knowledge iframe 要 8~10 秒才挂上 ans-attach-ct）。

    v1.7.2：以前打开章节只等 3 秒就判定，iframe 还没加载完 → total=0 →
    「任务点已完成」被判成「未完成」，然后把早就刷完的 PPT 又逐页翻一遍。
    """
    deadline = time.time() + timeout
    acc = {}
    while time.time() < deadline:
        acc = video_marks(b)
        if acc.get('total'):
            return acc
        time.sleep(1.5)
    return acc


def open_chapter(b, cid, course_url):
    """在课程页点开指定章节；点不到就刷新课程页再试。"""
    code = OPEN_JS % (cid, cid, cid)
    frame = b.frame_of('mycourse/studentcourse')
    if frame:
        try:
            if json.loads(b.js(code, frame=frame)).get('clicked'):
                return True
        except Exception:
            pass
    # 单章节学习页：目录在主文档
    try:
        if json.loads(b.js(code)).get('clicked'):
            return True
    except Exception:
        pass
    # 兜底：回课程页重来
    b.goto(course_url, settle=4000)
    time.sleep(1.5)
    frame = b.frame_of('mycourse/studentcourse')
    if frame:
        try:
            return bool(json.loads(b.js(code, frame=frame)).get('clicked'))
        except Exception:
            return False
    return False


def verify_done(b, cid, tries=3, gap=8):
    """确认章节任务点是否已完成。目录面板是渲染快照，必须刷新后重读才准。"""
    state, item = None, None
    for k in range(tries):
        if k:
            time.sleep(gap)
        try:
            b.reload(settle=5000)
            r = catalog_mod.parse(browser=b, save=True)
            state, item = catalog_mod.state_of(r, cid)
        except Exception:
            state = None
        if state == 'done':
            return state, item
        if state is None:
            continue
    return state, item


# ---------------------------------------------------------------- 播放守卫
def watch(b, idx=0, ratio=0.95, max_sec=None, tag=''):
    """守住第 idx 个视频播放到 duration*ratio。返回 (ok, duration, current, 用时)。"""
    t0 = time.time()
    play(b, idx)
    time.sleep(2)

    best = -1.0
    stale = 0
    last_log = 0.0
    last_check = 0.0
    last_keep = 0.0
    dur = 0.0

    while True:
        if stopped():
            print('  [%s] 已请求停止，暂停本视频' % tag, flush=True)
            try:
                pause(b, idx)
            except Exception:
                pass
            return False, dur, max(best, 0.0), time.time() - t0
        el = time.time() - t0
        limit = max_sec or (dur * 1.8 + 600 if dur else 5400)
        if el > limit:
            print('  [%s] 超时 %s / %s' % (tag, fmt(best), fmt(dur)), flush=True)
            return False, dur, max(best, 0.0), el

        vids = probe(b)
        if not vids:
            stale += 1
            time.sleep(POLL)
            continue

        i = min(idx, len(vids) - 1)
        v = vids[i]
        d = num(v['d'])
        c = num(v['c'])
        if d > 0:
            dur = d

        # 倍速被平台改回去就立刻压回 1.0（实测秒回 1 并暂停）
        if v.get('r') not in ('1', '1.0', '1.00'):
            play(b, i)

        if c > best + 0.5:
            best = c
            stale = 0
        else:
            stale += 1

        if (d > 0 and c >= d * ratio) or v.get('e'):
            print('  [%s] %s / %s = %.1f%%  播放到结尾  用时 %.0fs'
                  % (tag, fmt(c), fmt(d), (c / d * 100 if d else 0), time.time() - t0), flush=True)
            return True, d, c, time.time() - t0

        # 平台已经打上「任务点已完成」就不用等播满了（实测有时播到一半就记满了）
        if time.time() - last_check >= 10:
            last_check = time.time()
            if page_label_done(b):
                print('  [%s] 已出现「任务点已完成」，提前收工 (%.0f%%)'
                      % (tag, (c / d * 100) if d else 0), flush=True)
                return True, d, c, time.time() - t0

        if time.time() - last_log >= 30:
            last_log = time.time()
            pct = (c / d * 100) if d else 0
            print('  [%s] %s / %s (%.0f%%) 播放中' % (tag, fmt(c), fmt(d), pct), flush=True)

        if time.time() - last_keep >= 60:
            last_keep = time.time()
            keepalive(b)

        if v.get('p'):
            play(b, i)
            print('  [%s] 被暂停 -> 恢复 (%.0f%%)' % (tag, (c / d * 100) if d else 0), flush=True)

        if stale and stale % STALL_WARN == 0:
            print('  [%s] 进度 %ds 没动，自愈中' % (tag, stale * POLL), flush=True)
            bring_front(b)
            dismiss_popups(b)
            play(b, i)

        if stale >= STALL_HARD:
            print('  [%s] 卡死，放弃本视频' % tag, flush=True)
            return False, dur, best, time.time() - t0

        time.sleep(POLL)


def audit(b, targets, session, only=None):
    """只读巡检：逐个打开章节，读「视频任务点」绿标，不播放。"""
    done_path = os.path.join(b.io, 'video-done.json')
    done_log = load_json(done_path, {})
    byno = {x['no']: x for x in targets}
    seq = only or [x['no'] for x in targets]
    rows = []
    for no in seq:
        it = byno.get(no)
        if not it:
            print('  %-6s 目录里没有这个编号' % no)
            continue
        if not open_chapter(b, it['id'], session['courseUrl']):
            print('  %-6s 打不开章节' % no)
            rows.append({'no': no, 'status': 'openfail'})
            continue
        time.sleep(4)
        acc = video_marks(b)
        v = acc.get('videos', 0)
        f = acc.get('finished', 0)
        if v == 0:
            tag, status = '无视频任务点', 'novideo'
        elif f >= v:
            tag, status = '视频已完成', 'done'
            done_log[it['id']] = {'no': no, 'status': 'done', 'label': 'done',
                                  'at': time.strftime('%Y-%m-%d %H:%M:%S')}
        else:
            tag, status = '待刷 %d/%d' % (f, v), 'todo'
        print('  %-6s %-40s %-12s 目录:%s' % (no, it['title'][:38], tag, it['state']), flush=True)
        rows.append({'no': no, 'id': it['id'], 'title': it['title'],
                     'videos': v, 'finished': f, 'status': status,
                     'catalog': it['state']})
    save_json(done_path, done_log)
    save_json(os.path.join(b.io, 'audit.json'),
              {'at': time.strftime('%Y-%m-%d %H:%M:%S'), 'rows': rows})
    todo = [r for r in rows if r.get('status') == 'todo']
    print('\n巡检 %d 个：视频已完成 %d，待刷 %d，无视频 %d'
          % (len(rows), sum(1 for r in rows if r.get('status') == 'done'),
             len(todo), sum(1 for r in rows if r.get('status') == 'novideo')))
    if todo:
        print('待刷：' + ','.join(r['no'] for r in todo))
    print('明细 -> io/audit.json')


# ---------------------------------------------------------------- 单条目
GOOD_TAB = {'done', 'skip-done', 'video-done', 'doc-done', 'doc-flushed'}


def run_one(b, item, session, ratio, max_sec, retry=1, dry=False):
    """刷一个章节：逐个任务点标签处理（PPT / 视频 / 文档都会走到）。"""
    tag = item['no']
    cid = item['id']
    shot_base = 'v-%s' % tag.replace('.', '-')
    print('[%s] %s（id=%s）' % (tag, item['title'][:44], cid), flush=True)
    b.shot(shot_base + '-0-pre.png')

    if not open_chapter(b, cid, session['courseUrl']):
        print('  [%s] 打不开章节，跳过' % tag, flush=True)
        return {'no': tag, 'id': cid, 'status': 'openfail'}

    time.sleep(2)
    bring_front(b)
    install_guard(b)      # 每次导航后都要重装（补丁只在当前文档有效）
    wait_marks(b, 12)     # 等 iframe 把任务点标记挂上，别过早判成「未完成」

    groups = tabs(b)
    if len(groups) <= 1:
        r = _run_tab(b, item, session, ratio, max_sec, retry, dry, shot_base)
        r['tab'] = (groups[0].get('title') if groups else '') or ''
        return r

    print('  [%s] 本节 %d 个任务点：%s' % (
        tag, len(groups),
        ' / '.join('%d %s' % (g['i'] + 1, g.get('title') or '任务点')
                   for g in groups)), flush=True)

    parts = []
    for g in groups:
        name = g.get('title') or ('任务点%d' % (g['i'] + 1))
        if g['i']:
            if not switch_tab(b, g['i']):
                print('  [%s] %s 切不过去，跳过' % (tag, name), flush=True)
                parts.append({'tab': name, 'status': 'tabfail'})
                continue
            install_guard(b)
            wait_marks(b, 9)
        if dry:
            parts.append({'tab': name, 'status': 'dry'})
            continue
        if page_label_done(b):
            print('  [%s] %s：已是「任务点已完成」，跳过' % (tag, name), flush=True)
            b.shot(shot_base + '-skip.png')
            parts.append({'tab': name, 'status': 'skip-done'})
            continue
        r = _run_tab(b, item, session, ratio, max_sec, retry, dry, shot_base)
        r['tab'] = name
        parts.append(r)
        if r.get('status') == 'limit':
            break

    bad = [p for p in parts if p.get('status') not in GOOD_TAB]
    if not bad:
        status = 'done'
    elif any(p.get('status') == 'limit' for p in parts):
        status = 'limit'
    else:
        status = bad[0].get('status') or 'unconfirmed'
    return {'no': tag, 'id': cid, 'status': status, 'parts': parts,
            'pending_before': item.get('pending', 0)}


def _run_tab(b, item, session, ratio, max_sec, retry, dry, shot_base, tab=''):
    """处理当前已选中的那个任务点（视频 / 文档 / 其它）。"""
    tag = item['no']
    cid = item['id']
    before_pending = item.get('pending', 0)   # 本章节刷之前剩几个任务点

    # 文档类任务点（PPT/PDF 在线阅读）：先花 7 秒探一下阅读器在不在。
    # 以前是「先等 45 秒视频再看文档」，PPT 页面永远等不到 video 元素，
    # 等于每个文档任务点白等 38 秒；一门课十几个文档就是七八分钟。
    if doc_mod.has_doc(b, timeout=7):
        # 阅读器出来了，说明 iframe 也加载完了，此时再确认一次「是否已完成」：
        # 刚打开章节那一瞬间标记常常还没挂上，只看一次会把已完成的 PPT 又翻一遍。
        if page_label_done(b):
            print('  [%s] %s：已是「任务点已完成」，跳过不刷' % (tag, tab or '文档'), flush=True)
            b.shot(shot_base + '-skip.png')
            return {'no': tag, 'id': cid, 'status': 'skip-done', 'pending_before': before_pending}
        # 逐页翻到底再等回写。这里不做逐个 reload 复核：reload + 重新解析
        # 目录要 15~30 秒，一门课十几个文档就是十几分钟。整门课跑完统一复核。
        dr = doc_mod.brush(b, log=print, tag=tag, verify=None, wait=10)
        if dr.get('kind') == 'doc':
            b.shot(shot_base + '-doc.png')
            st = dr.get('status')
            status = {'done': 'doc-done'}.get(st, 'doc-flushed')
            print('  [%s] 文档任务点 %d 页 => %s' % (tag, dr.get('pages'), status), flush=True)
            return {'no': tag, 'id': cid, 'status': status,
                    'pending_before': before_pending,
                    'pages': dr.get('pages'),
                    'doc_status': dr.get('status')}

    vids = wait_videos(b, 45)
    b.shot(shot_base + '-1-page.png')
    if not vids:
        if job_limit_hit(b):
            print('  [%s] 平台提示「今日视频任务点完成数已达上限」，今天再刷也不计完成'
                  % tag, flush=True)
            return {'no': tag, 'id': cid, 'status': 'limit'}
        print('  [%s] 既没有 video 也没有文档（测验/讨论），交给人工' % tag, flush=True)
        return {'no': tag, 'id': cid, 'status': 'novideo'}

    print('  [%s] 发现 %d 个播放器：%s' % (
        tag, len(vids), ' , '.join('%s' % fmt(num(v['d'])) for v in vids)), flush=True)

    if dry:
        for i in range(len(vids)):
            pause(b, i)
        return {'no': tag, 'id': cid, 'status': 'dry',
                'videos': [round(num(v['d']), 1) for v in vids]}

    attempt = 0
    results = []
    while attempt <= retry:
        vids = wait_videos(b, 20) or vids
        all_ok = True
        for i, v in enumerate(vids):
            d = num(v['d'])
            c = num(v['c'])
            if d > 0 and c >= d * ratio:
                print('  [%s] 第 %d 段已在 %.0f%%，跳过' % (tag, i + 1, c / d * 100), flush=True)
                continue
            ok, dd, cc, used = watch(b, i, ratio=ratio, max_sec=max_sec, tag='%s.%d' % (tag, i + 1))
            results.append({'i': i + 1, 'ok': ok, 'dur': round(dd, 1), 'cur': round(cc, 1),
                            'used': round(used)})
            if not ok:
                all_ok = False
                break
        if all_ok:
            break
        attempt += 1
        if attempt <= retry:
            print('  [%s] 第 %d 次重试（重开章节）' % (tag, attempt), flush=True)
            open_chapter(b, cid, session['courseUrl'])
            time.sleep(4)

    b.shot(shot_base + '-2-end.png')
    time.sleep(END_GRACE)                    # 平台回写学习时长有 10s 级延迟
    green = wait_label_done(b, tries=3, gap=10)
    ok = all(r.get('ok') for r in results) if results else False
    if green:
        status = 'done'
    elif job_limit_hit(b):
        # 播完了却没打绿标，页面还提示「今日视频任务点完成数已达上限」：
        # 今天再重试也不会算完成，直接标记出来，别空转。
        status = 'limit'
    elif ok:
        status = 'unconfirmed'   # 播到结尾了但绿标没出现，下次重跑会再播一遍
    else:
        status = 'timeout'
    print('  [%s] 视频上方绿标: %s  => %s' % (tag, '有' if green else '无', status), flush=True)
    return {'no': tag, 'id': cid,
            'status': status,
            'label_done': green,
            'pending_before': before_pending,
            'videos': results,
            'total_used': sum(r.get('used', 0) for r in results)}


# ---------------------------------------------------------------- 收尾
QUIZ_HINTS = ('测验', '作业', '讨论', '考试', '练习', '测试', 'quiz', 'homework', 'test')


def is_quiz(title):
    t = (title or '').lower()
    return any(k in t for k in QUIZ_HINTS)


def report_remaining(b):
    """跑完盘点：把仍未完成的条目分「视频没刷到」和「需要人工答题」两类。"""
    try:
        cat = catalog_mod.parse(browser=b, save=True)
    except Exception as e:
        print('盘点失败：%s' % str(e)[:120])
        return
    pend = [x for x in cat['items'] if x['state'] == 'pending' and x['pending'] > 0]
    p = cat.get('progress') or {}
    print('\n=== 剩余待完成 ===')
    if p.get('total'):
        print('课程进度：%s/%s 任务点' % (p.get('done'), p.get('total')))
    if not pend:
        print('  没有待完成条目了。')
        return
    done_log = load_json(os.path.join(b.io, 'video-done.json'), {})
    quiz, other, leftover = [], [], []
    for x in pend:
        if x['id'] in done_log:
            leftover.append(x)
        elif is_quiz(x['title']):
            quiz.append(x)
        else:
            other.append(x)
    if other:
        print('  疑似还有视频/文档（可重跑 video.py）：')
        for x in other:
            print('    %-6s %-44s %d 个待完成' % (x['no'], x['title'][:42], x['pending']))
    if leftover:
        print('  已刷过但仍有剩余任务点（多半是章节测验，或这份文档没认到完成）：')
        for x in leftover:
            print('    %-6s %-44s 剩 %d 个' % (x['no'], x['title'][:42], x['pending']))
    if quiz:
        print('  需要人工答题（脚本只负责列出来）：')
        for x in quiz:
            print('    %-6s %-44s %d 个待完成' % (x['no'], x['title'][:42], x['pending']))
    save_json(os.path.join(b.io, 'remaining.json'), {
        'at': time.strftime('%Y-%m-%d %H:%M:%S'),
        'progress': p, 'quiz': quiz, 'other': other, 'leftover': leftover,
    })
    print('  清单已写 io/remaining.json')


# ---------------------------------------------------------------- 入口
def main():
    if '-h' in sys.argv[1:] or '--help' in sys.argv[1:]:
        print(__doc__)
        return
    argv = sys.argv[1:]
    args = [a for a in argv if not a.startswith('--')]
    opts = [a for a in argv if a.startswith('--')]

    ratio = 0.999          # 必须播到结尾；--ratio 只用于应急调低
    max_min = 0.0          # 0 = 按视频时长自适应
    retry = 1
    dry = '--dry' in opts
    skip_quiz = '--skip-quiz' in opts
    shuffle = '--shuffle' in opts
    only_todo = '--only-todo' in opts
    seed = 0
    for o in opts:
        if o.startswith('--seed='):
            seed = int(o.split('=', 1)[1])
    check_nos = []
    for o in opts:
        if o.startswith('--check='):
            check_nos = [x for x in o.split('=', 1)[1].split(',') if x]
    limit = 0
    for o in opts:
        if o.startswith('--limit='):
            limit = int(o.split('=', 1)[1])
    for o in opts:
        if o.startswith('--ratio='):
            ratio = float(o.split('=', 1)[1])
        elif o.startswith('--max='):
            max_min = float(o.split('=', 1)[1])
        elif o.startswith('--retry='):
            retry = int(o.split('=', 1)[1])
    max_sec = max_min * 60 if max_min else None

    b = Browser()
    try:
        b.status()
    except Exception:
        print('驱动不可用：先 start.cmd 起浏览器')
        sys.exit(3)

    if b.locate_course() is None and b.find_page_with_frame('mycourse/studentcourse') is None:
        raise RuntimeError('请先打开课程学习页（run.py open）')

    session = load_session(b)
    cat = catalog_mod.parse(browser=b, save=True)
    byno = {}
    for x in cat['items']:
        byno.setdefault(x['no'], x)
    done = {x['id'] for x in cat['items'] if x['state'] == 'done'}

    # 判断「这个条目还有没有活要干」
    targets = []
    if '--list' in opts or '--pending' in opts:
        targets = [x for x in cat['items'] if x['state'] == 'pending' and x['pending'] > 0]
        if skip_quiz:
            skipped = [x for x in targets if is_quiz(x['title'])]
            targets = [x for x in targets if not is_quiz(x['title'])]
            if skipped and '--list' not in opts:
                print('按标题跳过 %d 个疑似答题条目：%s'
                      % (len(skipped), ','.join(x['no'] for x in skipped)), flush=True)
        if '--list' in opts:
            print('待完成且有任务点的条目 %d 个：' % len(targets))
            for x in targets:
                mark = '答题' if is_quiz(x['title']) else '视频?'
                print('  %-6s %-44s %d 个  [%s]' % (x['no'], x['title'][:42], x['pending'], mark))
            return
    else:
        for no in (args[0].split(',') if args else []):
            it = byno.get(no)
            if not it:
                print('跳过 %s：目录里没有这个编号' % no)
                continue
            targets.append(it)

    # 只跑巡检（--audit）确认「视频未完成」的条目
    if only_todo:
        aj = load_json(os.path.join(b.io, 'audit.json'), {})
        todo = {r['no'] for r in aj.get('rows', []) if r.get('status') == 'todo'}
        if todo:
            before = len(targets)
            targets = [x for x in targets if x['no'] in todo]
            print('按巡检结果筛选：%d -> %d 个（只跑视频未完成的）' % (before, len(targets)), flush=True)
        else:
            print('没有 io/audit.json 或没有待刷条目，先跑 --audit', flush=True)
            return

    # 随机顺序：验证脚本对各类视频的适应性（可配 --seed 复现）
    if shuffle:
        random.Random(seed or None).shuffle(targets)
        print('顺序已随机（seed=%s）：%s'
              % (seed or 'random', ','.join(x['no'] for x in targets)), flush=True)

    if limit and len(targets) > limit:
        print('本轮只跑前 %d 个（共 %d 个待刷）' % (limit, len(targets)), flush=True)
        targets = targets[:limit]
    print('目标 %d 个' % len(targets), flush=True)
    results = []

    # --check / --audit：只开章节读视频任务点绿标，不播放
    if check_nos or '--audit' in opts:
        audit(b, targets, session, only=check_nos or None)
        return
    prog_path = os.path.join(b.io, 'video-progress.json')
    done_path = os.path.join(b.io, 'video-done.json')
    done_log = load_json(done_path, {})

    for i, it in enumerate(targets, 1):
        if stopped():
            print('已请求停止，本轮提前结束', flush=True)
            break
        if it['id'] in done or done_log.get(it['id'], {}).get('status') == 'done':
            print('[%d/%d] %s 已完成，跳过' % (i, len(targets), it['no']), flush=True)
            results.append({'no': it['no'], 'id': it['id'], 'status': 'skip-done'})
            continue
        print('[%d/%d]' % (i, len(targets)), end=' ', flush=True)
        r = run_one(b, it, session, ratio, max_sec, retry=retry, dry=dry)
        results.append(r)
        save_json(prog_path, results)
        if r.get('status') in ('done', 'skip-done', 'video-done'):
            done_log[it['id']] = {'no': it['no'], 'status': r.get('status'),
                                  'pending_left': r.get('pending_after'),
                                  'at': time.strftime('%Y-%m-%d %H:%M:%S')}
            save_json(done_path, done_log)

    print('\n=== 汇总 ===')
    for r in results:
        print('  %-6s %-12s %s' % (r['no'], r['status'], r.get('catalog_state') or ''))

    bad = [r for r in results if r['status'] in ('timeout', 'openfail', 'unconfirmed')]
    if not dry:
        report_remaining(b)
    sys.exit(2 if bad else 0)


if __name__ == '__main__':
    main()
