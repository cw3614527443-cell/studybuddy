"""doc.py —— 刷「文档类」任务点（PPT / PDF / Word 在线阅读）。

为什么需要它
------------
学习通的任务点不只有视频。老师在章节里挂 PPT/PDF 时，平台会用
`ananas/modules/pdf` 预览器打开，真正的内容在 `pan-yz.chaoxing.com/screen`
这个 iframe 里，分页节点是 `li[id="anchorN"]`，一页一个 img。

这类任务点按「翻页阅读」计完成：逐页翻到最后一页，平台的待完成数才清零。
旧套件只找 `<video>`，遇到文档就打印「没有 video 元素，交给人工」跳过。
真机测试（数据恢复 0/31）里 18 个待完成条目有 13 个是文档，
等于这门课基本没刷 —— 这就是「刷不了」的根因。

实测结论（2026-11-22，pan-yz 阅读器）
-------------------------------------
- 文档 iframe：`https://pan-yz.chaoxing.com/screen/v2/file_<obj>?ext=...`
- 页数：`document.querySelectorAll('li[id^="anchor"]').length`（该课 65 页）
- 逐页 `scrollIntoView({block:'start'})` + 每页停留 ~1.4s，翻到底后等 20~30s，
  目录里 `1.1` 从 pending 变 done，待完成由 1 清零。
"""

import json
import time

try:
    from client import stopped
except Exception:                     # 单独跑本文件时没有 client 也能用
    def stopped():
        return False

# 阅读器可能出现在这几个地址（pan-yz 是内容层，screen/file 是中间层）
DOC_FRAME_HINTS = ('pan-yz.chaoxing.com/screen', 'mooc-ans/screen/file', 'screen/v2')

INFO_JS = r"""
  var lis = document.querySelectorAll('li[id^="anchor"]');
  return JSON.stringify({pages: lis.length,
                         scrollH: document.documentElement.scrollHeight,
                         viewH: window.innerHeight});
"""


def find_doc_frame(b, timeout=20):
    """等文档预览器加载出来，返回它的 frame url。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        urls = []
        try:
            urls = b.frames() or []
        except Exception:
            urls = []
        for u in urls:
            if any(h in u for h in DOC_FRAME_HINTS):
                return u
        time.sleep(1.2)
    return None


def page_count(b, frame):
    try:
        return int(json.loads(b.js(INFO_JS, frame=frame)).get('pages') or 0)
    except Exception:
        return 0


def has_doc(b, timeout=12):
    return find_doc_frame(b, timeout=timeout) is not None


def read_through(b, frame=None, dwell=1.4, log=print, tag=''):
    """逐页翻到最后一页。返回 {'ok', 'pages', 'reason'}。"""
    frame = frame or find_doc_frame(b)
    if not frame:
        return {'ok': False, 'pages': 0, 'reason': 'nodoc'}
    n = page_count(b, frame)
    if not n:
        return {'ok': False, 'pages': 0, 'reason': 'nopage', 'frame': frame}
    label = '[%s] ' % tag if tag else ''
    log('%s文档任务点：共 %d 页，逐页翻到底…' % (label, n))
    for i in range(1, n + 1):
        if stopped():
            log('%s已请求停止，停在第 %d 页' % (label, i))
            return {'ok': False, 'pages': n, 'done_pages': i - 1, 'reason': 'stopped'}
        try:
            b.js('var el=document.getElementById("anchor%d");'
                 'if(el) el.scrollIntoView({block:"start"}); return 1;' % i, frame=frame)
        except Exception:
            pass
        if i % 10 == 0:
            log('%s已翻到 %d/%d 页' % (label, i, n))
        time.sleep(dwell)
    # 兜底：有的文档单页比视口高，最后一页可能没完整进过视口
    try:
        b.js('window.scrollTo(0, document.documentElement.scrollHeight); return 1;', frame=frame)
    except Exception:
        pass
    time.sleep(1.5)
    return {'ok': True, 'pages': n, 'done_pages': n, 'frame': frame}


def brush(b, log=print, dwell=1.4, tag='', verify=None, wait=25):
    """文档任务点完整流程：等阅读器 → 翻到底 → 等平台回写 → 复核。

    verify 是一个可调用对象（video.verify_done 的偏函数），返回 (state, item)。
    """
    frame = find_doc_frame(b, timeout=20)
    if not frame:
        return {'kind': 'other'}
    res = read_through(b, frame=frame, dwell=dwell, log=log, tag=tag)
    if not res.get('ok'):
        return {'kind': 'doc', 'status': res.get('reason') or 'fail',
                'pages': res.get('pages', 0), 'done_pages': res.get('done_pages', 0)}
    log('[%s] 已翻到最后一页（%d 页），等平台回写…' % (tag, res['pages']))
    time.sleep(wait)
    if verify is None:
        # 没给复核函数：翻完就算「已送交平台」，由整门课末尾的统一复核判定。
        # 这样每个文档省掉一次 reload+解析（15~30 秒）。
        return {'kind': 'doc', 'status': 'flushed', 'pages': res['pages']}
    state = None
    try:
        state, _item = verify()
    except Exception:
        state = None
    if state == 'done':
        return {'kind': 'doc', 'status': 'done', 'pages': res['pages']}
    return {'kind': 'doc', 'status': 'unconfirmed', 'pages': res['pages']}
