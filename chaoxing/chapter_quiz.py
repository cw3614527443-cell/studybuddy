"""chapter_quiz.py —— 章节内嵌「章节测验」三段式工具（7.1/8.1/9.1/10.1 这类）。

跟 exam.py 的区别：
  exam.py 处理的是**独立章节**（目录里单独一行的 "Quiz"，如 11.6）；
  本脚本处理的是**藏在章节里**的测验——7.1 这种章节点开后有「1 视频 / 2 章节测验」标签，
  切到「章节测验」后页面里可能同时挂着**多个** doHomeWorkNew iframe（每个是一个独立测验），
  exam.py 只会采第一个，会漏掉其余的。

  用 workId 精确定位每个测验（URL 里的 workId=xxxxxxx），题目编号写成 "7.1#56141956"，
  答案集里也用同样的编号，避免同一章节多个测验互相冲突。

流程跟 exam.py 一致：export -> 外部 AI 作答 -> check -> answer。

用法：
    python chapter_quiz.py export --nos=7.1,8.1,9.1,10.1
    python chapter_quiz.py show
    python chapter_quiz.py check answers.json
    python chapter_quiz.py answer answers.json --submit
"""

import json
import os
import re
import sys
import time
from datetime import datetime

from client import Browser, stopped
import catalog as catalog_mod
import exam as E
from quiz import open_quiz

# 章节里可能挂多个 doHomeWorkNew，用 workId 精确匹配
WORK_RE = re.compile(r'workId=(\d+)')


def list_quiz_frames(b):
    """返回当前页所有测验 iframe 的 workId（按 URL 出现顺序去重）。"""
    ids = []
    for url in b.frames():
        if 'doHomeWorkNew' not in url:
            continue
        m = WORK_RE.search(url)
        if m and m.group(1) not in ids:
            ids.append(m.group(1))
    return ids


def frame_status(b, work_id):
    """读某个测验的状态：题数、已完成/待完成、题型。"""
    js = r'''
    return (function(){
      var qs=document.querySelectorAll('.singleQuesId');
      var body=(document.body.innerText||'');
      var types={};
      for(var i=0;i<qs.length;i++){
        var t=qs[i].querySelector('.newZy_TItle');
        var s=t?(t.innerText||'').replace(/\s+/g,''):'';
        var k=/判断/.test(s)?'judge':/多选/.test(s)?'multi':/填空/.test(s)?'blank':'single';
        types[k]=(types[k]||0)+1;
      }
      return JSON.stringify({
        qcount: qs.length, types: types,
        pending: /待完成/.test(body), done: /已完成|已提交/.test(body),
        head: body.replace(/\s+/g,' ').slice(0,120)
      });
    })()
    '''
    try:
        return json.loads(b.js(js, frame=work_id))
    except Exception as e:
        return {'error': str(e)[:150]}


def collect_frame(b, work_id, tries=3, gap=2):
    """采某个 workId 对应测验的题目。"""
    last = None
    for _ in range(tries):
        try:
            raw = b.js(E.COLLECT_JS, frame=work_id)
            data = json.loads(raw)
            if data.get('questions'):
                return data
            last = data
        except Exception as e:
            last = {'error': str(e)[:200]}
        time.sleep(gap)
    return last or {'questions': []}


def export_nos(b, nos, outdir):
    """导出指定章节里的所有内嵌测验。"""
    cat = catalog_mod.parse(browser=b, save=True)
    byno = {x['no']: x for x in cat['items']}
    papers = []
    for no in nos:
        if stopped():
            print('已请求停止，导出提前结束', flush=True)
            break
        it = byno.get(no)
        if not it:
            print('%s 目录里没有' % no)
            continue
        if not open_quiz(b, it):
            print('%s 打不开章节测验' % no)
            continue
        time.sleep(3)
        wids = list_quiz_frames(b)
        print('[%s] 发现 %d 个章节测验: %s' % (no, len(wids), ','.join(wids)), flush=True)
        for wid in wids:
            st = frame_status(b, wid)
            print('   %s -> %d 题 %s %s' % (wid, st.get('qcount', 0),
                  st.get('types', {}), '待完成' if st.get('pending') else ''), flush=True)
            data = collect_frame(b, wid)
            qs = data.get('questions', [])
            if not qs:
                print('     采集失败，跳过')
                continue
            papers.append({'no': '%s#%s' % (no, wid), 'title': '章节测验(%s)' % wid,
                           'questions': qs})
    if not papers:
        print('没有采到任何题目')
        return None
    payload = {
        'format': 'chaoxing-exam-v1',
        'course': '大学英语口语',
        'exported_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'count': len(papers),
        'papers': papers,
        'fails': [],
    }
    paths = E._write_bundle(payload, outdir)
    total = sum(len(p['questions']) for p in papers)
    print('\n导出完成：%d 套 / %d 题' % (len(papers), total))
    return paths


def answer_file(b, path, dry=True, only=None, force=False):
    """导入答案并逐套填答提交。编号用 7.1#56141956 这种。"""
    answers = E.load_answers(path)
    results = []
    # 按章节分组：同一章节只 open_quiz 一次，然后逐个 workId 处理
    by_chapter = {}
    for (no, q), ans in answers.items():
        chapter = no.split('#')[0]
        by_chapter.setdefault(chapter, {})[no] = ans
    cat = catalog_mod.parse(browser=b, save=True)
    byno = {x['no']: x for x in cat['items']}

    for chapter, qmap in by_chapter.items():
        if stopped():
            print('已请求停止，填答提前结束', flush=True)
            break
        if only and chapter not in only and not any(n in only for n in qmap):
            continue
        it = byno.get(chapter)
        if not it:
            print('%s 目录里没有' % chapter)
            continue
        if not open_quiz(b, it):
            print('%s 打不开章节测验' % chapter)
            continue
        time.sleep(3)
        wids = list_quiz_frames(b)
        for no, _ in qmap.items():
            if only and no not in only:
                continue
            wid = no.split('#')[1] if '#' in no else None
            if wid and wid not in wids:
                print('%s workId 不在当前页，跳过' % no)
                continue
            target_frame = wid or 'doHomeWorkNew'
            print('\n=== %s ===' % no, flush=True)
            data = collect_frame(b, target_frame)
            qs = data.get('questions', [])
            if not qs:
                print('  采不到题目')
                results.append({'no': no, 'status': 'openfail'})
                continue
            paper = {'no': no, 'title': '章节测验', 'questions': qs}
            plan, missing = E.build_plan(paper, answers)
            if missing and not force:
                print('  缺答案 %s，跳过' % missing)
                results.append({'no': no, 'status': 'missing', 'missing': missing})
                continue
            print('  计划填答 %d/%d 题' % (len(plan), len(qs)))
            try:
                filled = json.loads(b.js('return (%s)(%s);' % (E.FILL_FN, json.dumps(plan)),
                                         frame=target_frame))
            except Exception as e:
                print('  填答异常 %s' % str(e)[:150])
                results.append({'no': no, 'status': 'fillerror'})
                continue
            bad = [x for x in filled if not x.get('ok') and x.get('type') != 'blank']
            for x in filled:
                print('    q%-2d %-6s 想要%s 实际%s %s' % (
                    x['q'], x.get('type'), ''.join(x.get('want') or []),
                    ''.join(str(h) for h in (x.get('hit') or [])), '' if x.get('ok') else '✗'))
            time.sleep(1)
            b.shot('ans-cq-%s-filled.png' % no.replace('#', '-').replace('.', '-'))
            if dry:
                print('  --dry 未提交')
                results.append({'no': no, 'status': 'dry'})
                continue
            if bad and not force:
                print('  ✗ %d 题没点中，拒绝提交' % len(bad))
                results.append({'no': no, 'status': 'unfilled', 'bad': bad})
                continue
            # 提交按钮用 workId 精确定位
            sub_js = ('''return (function(){var d=document.querySelector('.btnSubmit');'''
                      '''if(d){d.click();return 'ok';}return 'no';})()''')
            try:
                b.js(sub_js, frame=target_frame)
                time.sleep(2)
                # 确认弹窗在主文档
                b.js("return (function(){var p=document.getElementById('popok');"
                     "if(p){p.click();return 'ok';}return 'no';})()")
            except Exception as e:
                print('  提交异常 %s' % str(e)[:120])
            time.sleep(3)
            b.shot('ans-cq-%s-submitted.png' % no.replace('#', '-').replace('.', '-'))
            txt = E.grade_text(b)
            sc = E.parse_score(txt)
            print('  已提交，分数：%s' % sc)
            results.append({'no': no, 'status': 'submitted', 'score': sc,
                            'at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')})
            time.sleep(2)

    rp = os.path.join(b.io, 'chapter-quiz-results.json')
    old = {}
    if os.path.exists(rp):
        try:
            old = json.load(open(rp, encoding='utf-8'))
        except Exception:
            pass
    for r in results:
        if r.get('status') == 'submitted':
            old[r['no']] = r
    with open(rp, 'w', encoding='utf-8') as f:
        json.dump(old, f, ensure_ascii=False, indent=1)
    print('\n=== 汇总 ===')
    for r in results:
        print('  %-16s %-10s %s' % (r['no'], r['status'], r.get('score', '')))
    return results


def main():
    if '-h' in sys.argv[1:] or '--help' in sys.argv[1:]:
        print(__doc__)
        return
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    flags = [a for a in sys.argv[1:] if a.startswith('--')]
    from client import DEFAULT_ROOT
    root0 = os.environ.get('BROWSER_ROOT') or DEFAULT_ROOT
    outdir0 = os.path.join(root0, 'io', 'exports')

    if args and args[0] == 'check':
        if len(args) < 2:
            print('用法：python chapter_quiz.py check answers.json')
            return
        E.check_answers(args[1], outdir0)
        return
    if args and args[0] == 'show':
        lj = os.path.join(outdir0, 'chapter-latest.json')
        if not os.path.exists(lj):
            print('还没有导出记录')
            return
        info = json.load(open(lj, encoding='utf-8'))
        print('最近一次导出：%s' % info.get('at'))
        print('  %d 套 / %d 题' % (info.get('count', 0), info.get('total_q', 0)))
        print('  题目 ->', info.get('json'))
        print('  给 AI ->', info.get('md'))
        return

    b = Browser()
    if b.locate_course() is None:
        raise RuntimeError('请先打开课程学习页')

    if args and args[0] == 'export':
        nos = []
        for f in flags:
            if f.startswith('--nos='):
                nos = [x for x in f.split('=', 1)[1].split(',') if x]
        if not nos:
            print('用法：python chapter_quiz.py export --nos=7.1,8.1')
            return
        paths = export_nos(b, nos, os.path.join(b.io, 'exports'))
        if paths:
            jpath, mpath, tpath = paths
            total = sum(len(p['questions']) for p in json.load(open(jpath, encoding='utf-8'))['papers'])
            with open(os.path.join(b.io, 'exports', 'chapter-latest.json'), 'w', encoding='utf-8') as f:
                json.dump({'json': jpath, 'md': mpath, 'template': tpath,
                           'at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                           'count': json.load(open(jpath, encoding='utf-8'))['count'],
                           'total_q': total}, f, ensure_ascii=False, indent=1)
            print('  给 AI 的文件 ->', mpath)
        return

    if args and args[0] == 'answer':
        if len(args) < 2:
            print('用法：python chapter_quiz.py answer answers.json [--submit]')
            return
        only = None
        for f in flags:
            if f.startswith('--only='):
                only = {x for x in f.split('=', 1)[1].split(',') if x}
        answer_file(b, args[1], dry='--submit' not in flags,
                    only=only, force='--force' in flags)
        return

    print(__doc__)


if __name__ == '__main__':
    main()
