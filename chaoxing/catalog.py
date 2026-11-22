"""catalog.py —— 解析课程目录，输出任务点清单与完成状态。

两种页面结构都支持：
  1) 课程目录页：目录在 iframe（mycourse/studentcourse）里，
     已完成 = .catalog_state.icon_yiwanc
     未完成 = .catalog_jindu .bntHoverTips「N个待完成任务点」+ 隐藏域 .knowledgeJobCount
  2) 单章节学习页：目录在主文档里（#cur<id> .posCatalog_name），
     已完成 = .icon_Completed
     未完成 = .prevHoverTips「N个待完成任务点」+ 隐藏域 .jobUnfinishCount

用法：
    python catalog.py            # 解析并写 io/catalog.json
    python catalog.py --show     # 顺手打印待完成清单
"""

import json
import os
import re
import sys
from datetime import datetime

from client import Browser

CATALOG_FRAME = 'mycourse/studentcourse'

EXTRACT_JS = r"""
return (function(){
  var out=[];
  var nodes=document.querySelectorAll('[id^="cur"]');
  for(var i=0;i<nodes.length;i++){
    var el=nodes[i];
    var id=(el.id||'').replace('cur','');
    var sbar=el.querySelector('.catalog_sbar, .posCatalog_sbar');
    if(!sbar) continue;                        // 没有编号的行跳过（章节标题行）
    var no=(sbar.textContent||'').trim();
    var nameEl=el.querySelector('.posCatalog_name') || el.querySelector('.clicktitle');
    var title='';
    if(nameEl && nameEl.getAttribute('title')) title=nameEl.getAttribute('title');
    if(!title && nameEl) title=(nameEl.textContent||'').replace(no,'').trim();
    if(!title) title=(el.getAttribute('title')||'').trim();

    var done = !!(el.querySelector('.icon_Completed') || el.querySelector('.catalog_state.icon_yiwanc'));
    var tipEl = el.querySelector('.prevHoverTips') || el.querySelector('.bntHoverTips');
    var tip = tipEl ? (tipEl.textContent||'').trim() : '';
    var jc = el.querySelector('.jobUnfinishCount') || el.querySelector('.knowledgeJobCount');
    var m = tip.match(/(\d+)/);
    var pend = (!done && m) ? parseInt(m[1]) : 0;
    if(!done && !pend && jc && parseInt(jc.value||'0')>0) pend = parseInt(jc.value);

    out.push({id:id, no:no, title:title, done:done, pending:pend,
              jobCount: jc ? parseInt(jc.value||'0') : 0,
              state: done ? 'done' : 'pending'});
  }
  var head=document.querySelector('.chapter_head');
  var headTxt=head?(head.innerText||'').replace(/\s+/g,' ').trim():'';
  if(!headTxt){
    var p=document.querySelector('.posCatalog_title, .chapterNumber, .catalog_jindu');
    if(p) headTxt=(p.textContent||'').replace(/\s+/g,' ').trim();
  }
  return JSON.stringify({items:out, head:headTxt});
})()
"""


def _target(b):
    """找到装有目录的页面/框架：优先课程目录页 iframe，其次单章节学习页主文档。"""
    b.locate_course()
    frame = b.frame_of(CATALOG_FRAME)
    if frame:
        return frame
    # 主文档里已经有目录节点 → 当前就在单章节学习页，直接读主文档
    try:
        if b.js('return document.querySelectorAll(\'[id^="cur"]\').length > 0'):
            return None
    except Exception:
        pass
    # 新版课程空间默认停在「任务」标签，章节树要点一下左侧「章节」才加载。
    # 不点这一步的后果是 items 为空：刷课「目标 0 个」，明明没刷却说已全部完成。
    try:
        b.open_chapter()
    except Exception:
        pass
    return b.frame_of(CATALOG_FRAME)


def parse(browser=None, save=True):
    b = browser or Browser()
    frame = _target(b)
    raw = b.js(EXTRACT_JS, frame=frame) if frame else b.js(EXTRACT_JS)
    data = json.loads(raw)
    items = data['items']

    progress = None
    cid = ''
    try:
        cid = b.course_id() or ''
    except Exception:
        cid = ''
    m = re.search(r'(\d+)\s*/\s*(\d+)', data.get('head') or '')
    if m:
        progress = {'done': int(m.group(1)), 'total': int(m.group(2)),
                    'text': data['head'], 'cid': cid}
        with open(os.path.join(b.io, 'progress.json'), 'w', encoding='utf-8') as f:
            json.dump(progress, f, ensure_ascii=False)
    else:
        # 单章节页没有「已完成任务点: x/y」表头，沿用上次进度（progress.json 只在有表头时更新）。
        # 必须校验 cid：否则换课程后会读到上一门课的 134/134，
        # 于是「数据恢复 0/31」被显示成「课程进度：134/134 没有待完成条目」。
        pj = os.path.join(b.io, 'progress.json')
        if os.path.exists(pj):
            try:
                with open(pj, encoding='utf-8') as f:
                    progress = json.load(f)
                if progress.get('cid') and cid and progress['cid'] != cid:
                    progress = None
            except Exception:
                progress = None

    pending = [x for x in items if x['state'] == 'pending']
    result = {
        'at': datetime.now().isoformat(timespec='seconds'),
        'page': b.page_index,
        'progress': progress,
        'items': items,
        'pending': pending,
        'pending_points': sum(x['pending'] for x in pending),
    }
    if save:
        path = os.path.join(b.io, 'catalog.json')
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=1)
        result['saved'] = path
    return result


def state_of(result, cid):
    for x in result['items']:
        if x['id'] == cid:
            return x['state'], x
    return None, None


def show(result):
    p = result.get('progress') or {}
    if p.get('total'):
        print('已完成任务点: %s/%s   待完成条目 %d 个 / 任务点 %d 个'
              % (p.get('done'), p.get('total'), len(result['pending']), result['pending_points']))
    else:
        print('（当前在单章节学习页，读不到「已完成任务点 x/y」表头；'
              '待完成条目 %d 个 / 任务点 %d 个）' % (len(result['pending']), result['pending_points']))
    for x in result['pending']:
        print('  %-6s %-50s %s' % (x['no'], x['title'][:48],
                                   ('%d 个待完成' % x['pending']) if x['pending'] else '无任务点'))


if __name__ == '__main__':
    r = parse()
    show(r)
    print('catalog ->', r.get('saved'))
