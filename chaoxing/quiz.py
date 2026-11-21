"""quiz.py —— 章节测验：进测验页 -> 采集题目 -> 填答 -> 提交 -> 读分。

学习通章节测验的关键事实（实测）：
  * 答题页在 iframe（URL 含 doHomeWorkNew）里，题目节点是 .singleQuesId；
  * 选项是 li[aria-label]，判断题为「对选择/错选择」，单选题按顺序排列；
  * 提交按钮 .btnSubmit，会先弹「确认提交？」，确认按钮 id=popok；
  * 提交后页面切到批阅页，只显示自己的答案与每题得分，不显示正确答案；
  * 重做接口 /work/retest 会返回「作答状态或作答次数已变化」，成绩基本不可改。

用法：
    python quiz.py --list                 # 列出待完成的测验章节
    python quiz.py 11.6 --collect         # 只采题（不填不交），存 io/quiz-11-6.json
    python quiz.py 11.6 --answers=1,3,2,4,1 --dry   # 填答但先不提交
    python quiz.py 11.6 --answers=1,3,2,4,1        # 填答并提交，读回分数
"""

import json
import os
import re
import sys
import time

from client import Browser
import catalog as catalog_mod

QUIZ_FRAME = 'doHomeWorkNew'

COLLECT_JS = r"""
return (function(){
  var qs=document.querySelectorAll('.singleQuesId');
  var out=[];
  for(var i=0;i<qs.length;i++){
    var q=qs[i];
    var tEl=q.querySelector('.Zy_TItle, .mark_item .clearfix, .TiMu');
    var stem=tEl?(tEl.innerText||'').replace(/\s+/g,' ').trim():'';
    if(!stem) stem=(q.innerText||'').replace(/\s+/g,' ').trim().slice(0,200);
    var lis=q.querySelectorAll('li[aria-label]');
    var opts=[];
    for(var j=0;j<lis.length;j++){
      opts.push({idx:j, aria:lis[j].getAttribute('aria-label'),
                 text:(lis[j].innerText||'').replace(/\s+/g,' ').trim().slice(0,120)});
    }
    var inp=q.querySelector('input[id^="answer"]');
    out.push({n:i+1, stem:stem, opts:opts,
              answerId: inp?inp.id:null, value: inp?inp.value:null,
              type: (opts.length===2 && /对|错/.test(opts.map(function(o){return o.aria}).join('')))?'judge':'choice'});
  }
  return JSON.stringify(out);
})()
"""

FILL_FN = r"""function(picks){
  var qs=document.querySelectorAll('.singleQuesId');
  var out=[];
  for(var i=0;i<qs.length && i<picks.length;i++){
    var p=picks[i];
    if(!p){ out.push({n:i+1, skipped:true}); continue; }
    var lis=qs[i].querySelectorAll('li[aria-label]');
    var idx=p-1;
    var clicked=false;
    if(lis[idx]){ lis[idx].click(); clicked=true; }
    var inp=qs[i].querySelector('input[id^="answer"]');
    out.push({n:i+1, pick:p, clicked:clicked, val:inp?inp.value:null});
  }
  return JSON.stringify(out);
}"""


def open_quiz(b, item):
    """点开章节并切到「章节测验」标签。"""
    cid = item['id']
    code = ("return (function(){var a=document.querySelector('#cur%s .posCatalog_name')||"
            "document.querySelector('#cur%s .clicktitle');if(a){a.click();return 'ok';}return 'no';})()") % (cid, cid)
    clicked = None
    frame = b.frame_of('mycourse/studentcourse')
    try:
        clicked = b.js(code, frame=frame) if frame else None
    except Exception:
        clicked = None
    if clicked != 'ok':
        try:
            clicked = b.js(code)
        except Exception:
            clicked = None
    if clicked != 'ok':
        return False
    time.sleep(3)
    # 切标签：不同页面结构下可能是 li.spanText 或 a/span
    tab_js = ("return (function(){var els=[].slice.call(document.querySelectorAll('.spanText, a, li, span'));"
              "for(var i=0;i<els.length;i++){var t=(els[i].innerText||'').trim();"
              "if(t==='章节测验'&&els[i].getBoundingClientRect().width>0){(els[i].closest('li')||els[i]).click();return 'ok';}}"
              "return 'no';})()")
    try:
        b.js(tab_js)
    except Exception:
        pass
    # 等答题 iframe 出现
    for _ in range(12):
        if b.has_frame(QUIZ_FRAME):
            return True
        time.sleep(1.5)
    return False


def collect(b):
    raw = b.js(COLLECT_JS, frame=QUIZ_FRAME)
    return json.loads(raw)


def fill(b, picks):
    raw = b.js('return (%s)(%s);' % (FILL_FN, json.dumps(picks)), frame=QUIZ_FRAME)
    return json.loads(raw)


def grade_text(b):
    """读批阅页文本（含每题得分）。"""
    for needle in ('selectWorkQuestionYiPiYue', 'selectWorkQuestionNew', QUIZ_FRAME):
        if b.has_frame(needle):
            try:
                t = b.text(needle)
                if '分' in t:
                    return t
            except Exception:
                pass
    try:
        return b.text()
    except Exception:
        return ''


def parse_score(txt):
    m = re.search(r'(\d+(?:\.\d+)?)\s*分', txt or '')
    return float(m.group(1)) if m else None


def main():
    if '-h' in sys.argv[1:] or '--help' in sys.argv[1:]:
        print(__doc__)
        return
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    flags = [a for a in sys.argv[1:] if a.startswith('--')]
    b = Browser()
    if b.locate_course() is None:
        raise RuntimeError('请先打开课程学习页（run.py open）')
    cat = catalog_mod.parse(browser=b, save=True)

    if '--list' in flags:
        rows = [x for x in cat['pending'] if 'quiz' in x['title'].lower()]
        print('待完成测验 %d 个:' % len(rows))
        for x in rows:
            print('  %-6s %-24s id=%s' % (x['no'], x['title'], x['id']))
        return

    if not args:
        print(__doc__)
        return

    no = args[0]
    item = None
    for x in cat['items']:
        if x['no'] == no:
            item = x
            break
    if not item:
        raise RuntimeError('目录里没有 %s' % no)
    if item['state'] == 'done':
        print('%s 已完成，跳过' % no)
        return

    print('打开 %s %s' % (no, item['title']))
    b.shot('q-%s-0-pre.png' % no.replace('.', '-'))
    if not open_quiz(b, item):
        print('没能打开章节测验，请检查页面')
        return
    time.sleep(2)
    b.shot('q-%s-1-quiz.png' % no.replace('.', '-'))

    qs = collect(b)
    out_path = os.path.join(b.io, 'quiz-%s.json' % no.replace('.', '-'))
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(qs, f, ensure_ascii=False, indent=1)
    print('采到 %d 题 -> %s' % (len(qs), out_path))
    for q in qs:
        print('  %d. [%s] %s' % (q['n'], q['type'], q['stem'][:90]))
        for o in q['opts']:
            print('       %d) %s' % (o['idx'] + 1, o['text'][:70]))

    ans = None
    for fl in flags:
        if fl.startswith('--answers='):
            ans = [int(x) for x in fl.split('=', 1)[1].split(',') if x.strip()]
    if not ans:
        print('未提供 --answers，仅采集。')
        return
    if len(ans) < len(qs):
        print('答案数量(%d) 少于题目数(%d)，中止。' % (len(ans), len(qs)))
        return

    filled = fill(b, ans)
    print('填答结果:', json.dumps(filled, ensure_ascii=False))
    b.shot('q-%s-2-filled.png' % no.replace('.', '-'))
    if '--dry' in flags:
        print('--dry：已填答但未提交。')
        return

    # 提交
    b.js("return (function(){var b=document.querySelector('.btnSubmit');if(b){b.click();return 'ok';}return 'no';})()",
         frame=QUIZ_FRAME)
    time.sleep(2)
    b.js("return (function(){var p=document.getElementById('popok');if(p){p.click();return 'ok';}return 'no';})()")
    time.sleep(6)
    b.shot('q-%s-3-submitted.png' % no.replace('.', '-'))
    txt = grade_text(b)
    print('分数:', parse_score(txt))
    print(txt[:600].replace('\n', ' | '))


if __name__ == '__main__':
    main()
