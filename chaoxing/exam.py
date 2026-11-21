"""exam.py —— 章节测验「导出题目 -> 外部 AI 作答 -> 导入答案自动填答」三段式工具。

为什么这样设计（实测结论）：
  学习通测验的正确答案在平台内部，脚本拿不到——提交后只回显「自己选的 + 每题得分」，
  重做接口 /work/retest 直接返回「作答状态或作答次数已变化」。纯脚本不可能自己算出答案。
  但「题目 + 阅读理解课文」是**可以完整导出**的。所以把流程拆成三段：

    1) export  ｜ 脚本抓题（含阅读材料），导出固定格式的文件（JSON + Markdown）
    2) 作答     ｜ 用户把 Markdown/JSON 丢给任意 AI，AI 按固定格式回一份答案集
    3) answer  ｜ 脚本导入答案集，自动逐题填答、校验、提交、读分

固定格式（改动要同步 README）：

  【导出】exam-export.json
  {
    "format": "chaoxing-exam-v1",
    "course": "...", "exported_at": "...", "count": 21,
    "papers": [
      {"no": "11.6", "title": "Quiz", "questions": [
        {"q": 1, "type": "single|multi|judge|blank|short",
         "passage": "阅读材料原文（没有则空串）",
         "stem": "题干",
         "options": [{"label": "A", "text": "..."}]}
      ]}
    ]
  }

  【答案】answers.json
  {
    "format": "chaoxing-answers-v1",
    "answers": [
      {"no": "11.6", "q": 1, "answer": "B"},
      {"no": "11.6", "q": 2, "answer": ["A", "C"]},
      {"no": "11.6", "q": 3, "answer": "对"},
      {"no": "11.6", "q": 4, "answer": "填入文本"}
    ]
  }

用法：
    python exam.py export --pending              # 导出全部待完成测验
    python exam.py export --no=11.6,11.7         # 只导出指定章节
    python exam.py export --pending --limit=3    # 先导 3 套试试
    python exam.py show                          # 看已导出的题目统计
    python exam.py check answers.json            # 离线校验答案覆盖率（不碰浏览器）

    python exam.py answer answers.json --dry     # 只填答、截图，不提交
    python exam.py answer answers.json --submit  # 填答并提交，读回分数
    python exam.py answer answers.json --only=11.6 --submit
"""

import json
import os
import re
import sys
import time
from datetime import datetime

from client import Browser, stopped
import catalog as catalog_mod
from quiz import QUIZ_FRAME, open_quiz, grade_text, parse_score

QUIZ_HINTS = ('quiz', '测验', '作业', '考试', '练习', '测试', 'exercise', 'test')
FORMAT_EXPORT = 'chaoxing-exam-v1'
FORMAT_ANSWERS = 'chaoxing-answers-v1'


def is_quiz(title):
    t = (title or '').lower()
    return any(k in t for k in QUIZ_HINTS)


# ------------------------------------------------------------------ 采集
# 关键点（都踩过）：
#   1. 题目容器是 .singleQuesId，答题页在 iframe（URL 含 doHomeWorkNew）里；
#   2. 阅读理解的课文**不在题干里**，是题目容器前面的一段兄弟节点，
#      所以要往前扫 previousElementSibling，扫到上一题为止；
#   3. 不能用 innerText 判断题型，多选看 input[type=checkbox]、判断看两选项是否为对/错。
COLLECT_JS = r"""
return (function(){
  function clean(s){ return (s||'').replace(/[\u00a0\u3000]/g,' ').replace(/\s+/g,' ').trim(); }
  var qs=[].slice.call(document.querySelectorAll('.singleQuesId'));
  var out=[];
  for(var i=0;i<qs.length;i++){
    var q=qs[i];

    /* ---- 阅读材料：往前扫兄弟，遇到上一题就停 ---- */
    var stop = i>0 ? qs[i-1] : null;
    var chunks=[], sib=q.previousElementSibling, guard=0;
    while(sib && sib!==stop && guard<30){
      guard++;
      var t=clean(sib.innerText);
      if(t) chunks.unshift(t);
      sib=sib.previousElementSibling;
    }
    var passage=chunks.join('\n').trim();
    if(passage.length<60 || passage.length>12000) passage='';

    /* 兜底：题型标记是「阅读理解」类，但前面没扫到材料，
       说明材料被包在某个分组容器里。向上找 3 层，取「比题干多出一大段」的容器文本。
       只对阅读类题启用——否则会把整个测验页当成材料（踩过）。 */
    var typeTag0=q.querySelector('.newZy_TItle');
    var typeTxt0=typeTag0?clean(typeTag0.innerText):'';
    var readingLike=/阅读|reading|passage|cloze|完形/i.test(typeTxt0);
    if(readingLike && !passage){
      var par=q.parentElement, lvl=0;
      var stem0='';
      var se0=q.querySelector('.Zy_TItle .fontLabel')||q.querySelector('.Zy_TItle');
      stem0=se0?clean(se0.innerText):'';
      while(par && lvl<3){
        var pt=clean(par.innerText);
        if(pt.length>stem0.length+200 && pt.length<12000){
          passage=pt.slice(0,8000);
          break;
        }
        par=par.parentElement; lvl++;
      }
    }

    /* ---- 题干：务必取 .fontLabel（题干正文），
           不能取 .TiMu（那是祖先节点，innerText 会把选项一起吞进来，踩过） ---- */
    var typeTag=q.querySelector('.newZy_TItle');
    var typeTxt=typeTag?clean(typeTag.innerText):'';
    var stemEl=q.querySelector('.Zy_TItle .fontLabel') || q.querySelector('.Zy_TItle');
    var stem=stemEl?clean(stemEl.innerText):'';
    stem=stem.replace(/^【[^】]*】\s*/,'').trim();     // 去掉「【单选题】」前缀
    if(!stem) stem=clean(q.innerText).slice(0,300);

    /* ---- 选项：字母取 .num_option 的 data，文本取 a.after ---- */
    var lis=[].slice.call(q.querySelectorAll('li[aria-label]'));
    if(!lis.length) lis=[].slice.call(q.querySelectorAll('.Zy_ulTop li'));
    var opts=[], roles=[];
    for(var j=0;j<lis.length;j++){
      var li=lis[j];
      var aria=li.getAttribute('aria-label')||'';
      var role=li.getAttribute('role')||'';
      if(role) roles.push(role);
      var numEl=li.querySelector('.num_option');
      var lab=numEl?(numEl.getAttribute('data')||clean(numEl.innerText)):'';
      lab=(lab||'').toUpperCase();
      if(!/^[A-Z]$/.test(lab)){
        var m=aria.match(/^([A-Za-z])[\s、.．)）:：]/);
        lab=m?m[1].toUpperCase():String.fromCharCode(65+j);
      }
      var aEl=li.querySelector('a.after') || li.querySelector('a');
      var txt=aEl?clean(aEl.innerText):clean(li.innerText);
      if(!txt) txt=clean(aria).replace(/选择$/,'').trim();
      opts.push({label:lab, text:txt});
    }
    /* 判断题：选项是「对/错」时 label 用词本身 */
    var judgeLike=opts.length===2 && opts.every(function(o){
      return /^(对|错|正确|错误|是|否|true|false|√|×)$/i.test(o.text+' '+o.label);
    });
    if(judgeLike){
      for(var k=0;k<opts.length;k++){
        opts[k].label=/对|正确|是|true|√/i.test(opts[k].text) ? '对' : '错';
      }
    }

    /* ---- 题型：先看「【多选题】」标记，再看 DOM ---- */
    var hasCk=q.querySelector('input[type=checkbox]') ||
              roles.indexOf('checkbox')>=0 || /多选/.test(typeTxt);
    var blanks=[].slice.call(q.querySelectorAll('input[type=text], textarea'))
                 .filter(function(e){return !e.disabled && e.type!=='hidden';});
    var type='single';
    if(judgeLike || /判断/.test(typeTxt)) type='judge';
    else if(hasCk) type='multi';
    else if(/填空/.test(typeTxt)) type='blank';
    else if(/简答|问答|论述/.test(typeTxt)) type='short';
    else if(opts.length) type='single';
    else if(blanks.length) type='blank';

    var inp=q.querySelector('input[id^="answer"]');
    out.push({q:i+1, type:type, typeTag:typeTxt, passage:passage, stem:stem,
              options:opts, blanks:blanks.length,
              qid:q.getAttribute('data')||'', answerId: inp?inp.id:null});
  }
  return JSON.stringify({
    questions: out,
    paging: !!document.querySelector('.Zy_Pager, .paging, .pageNum, a.pagerNext'),
    title: (document.title||'').slice(0,120)
  });
})()
"""


def collect(b, tries=3, gap=2):
    """采当前测验页的全部题目。"""
    last = None
    for k in range(tries):
        try:
            raw = b.js(COLLECT_JS, frame=QUIZ_FRAME)
            data = json.loads(raw)
            if data.get('questions'):
                return data
            last = data
        except Exception as e:
            last = {'error': str(e)[:200]}
        time.sleep(gap)
    return last or {'questions': []}


def open_and_collect(b, item, settle=3):
    if not open_quiz(b, item):
        return None
    time.sleep(settle)
    for _ in range(10):
        if b.has_frame(QUIZ_FRAME):
            break
        time.sleep(1.5)
    data = collect(b)
    return data if data.get('questions') else None


# ------------------------------------------------------------------ 导出
def _md_paper(p):
    L = []
    L.append('## %s — %s' % (p['no'], p.get('title') or '测验'))
    L.append('')
    for q in p['questions']:
        tn = {'single': '单选题', 'multi': '多选题', 'judge': '判断题',
              'blank': '填空题', 'short': '简答题'}.get(q['type'], q['type'])
        L.append('### 第 %d 题（%s）' % (q['q'], tn))
        L.append('')
        if q.get('passage'):
            L.append('**阅读材料：**')
            L.append('')
            for line in q['passage'].split('\n'):
                L.append('> ' + line)
            L.append('')
        L.append('**题目：** %s' % q['stem'])
        L.append('')
        if q['options']:
            for o in q['options']:
                L.append('- %s. %s' % (o['label'], o['text']))
            L.append('')
    return '\n'.join(L)


def export(b, targets, outdir):
    os.makedirs(outdir, exist_ok=True)
    papers = []
    fails = []
    for i, it in enumerate(targets, 1):
        if stopped():
            print('已请求停止，导出提前结束', flush=True)
            break
        no = it['no']
        print('[%d/%d] %s %s' % (i, len(targets), no, it['title'][:40]), flush=True)
        data = open_and_collect(b, it)
        if not data:
            print('   采集失败（没进到答题页或页面无题目）', flush=True)
            b.shot('exam-fail-%s.png' % no.replace('.', '-'))
            fails.append({'no': no, 'title': it['title']})
            continue
        qs = data['questions']
        paper = {'no': no, 'id': it['id'], 'title': it['title'],
                 'paging': bool(data.get('paging')), 'questions': qs}
        papers.append(paper)
        types = {}
        for q in qs:
            types[q['type']] = types.get(q['type'], 0) + 1
        pas = sum(1 for q in qs if q.get('passage'))
        print('   %d 题 %s  含阅读材料 %d 题%s'
              % (len(qs), types, pas, '  [分页]' if data.get('paging') else ''), flush=True)
        time.sleep(1)

    stamp = datetime.now().strftime('%Y%m%d-%H%M')
    payload = {'format': FORMAT_EXPORT,
               'course': '大学英语口语',
               'exported_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
               'count': len(papers),
               'papers': papers}
    jpath = os.path.join(outdir, 'exam-%s.json' % stamp)
    with open(jpath, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)

    return _write_bundle(payload, outdir)


def _write_bundle(payload, outdir):
    """把一份 payload 渲染成 3 个交付文件（题目 JSON / 给 AI 的 md / 答案模板）。"""
    os.makedirs(outdir, exist_ok=True)
    papers = payload['papers']
    stamp = datetime.now().strftime('%Y%m%d-%H%M')
    total_q = sum(len(p['questions']) for p in papers)
    jpath = os.path.join(outdir, 'exam-%s.json' % stamp)
    with open(jpath, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)

    # 整份文件就是给 AI 的指令：从第一行到文末直接复制即可
    L = [
        '# 学习通《大学英语口语》章节测验 — AI 作答任务',
        '',
        '> 把这份文件**从第一行到文末整个**复制给任意 AI（ChatGPT / Claude / 元宝 / DeepSeek 都行）。',
        '> 文末有输出格式要求，AI 回的那份 JSON 存成文件后，脚本会自动导入填答。',
        '',
        '---',
        '',
        '## 你的任务',
        '',
        '你是一个英语课程答题助手。下面是导出的章节测验，共 **%d 套、%d 道题**。'
        % (len(papers), total_q),
        '请认真读每一道题及其选项，逐题给出你认为正确的答案。',
        '',
        '## 输出要求（脚本会自动校验，必须全部满足）',
        '',
        '1. **整个回复只能是一个 JSON 代码块**。不要写开场白、解释、推理过程、总结，代码块外不要有任何文字。',
        '2. `format` 字段固定填写 `"chaoxing-answers-v1"`。',
        '3. `answers` 数组里**每道题恰好一条**，不能遗漏，也不能出现题目里没有的编号。',
        '4. `no` 照抄每套题标题里的章节编号（如 `11.6`），`q` 照抄「第 N 题」里的题号，二者必须一一对应。',
        '5. `answer` 的写法按题型：',
        '   - 单选题（single）：字母字符串，如 `"B"`；',
        '   - 多选题（multi）：字母数组，如 `["A", "C"]`；',
        '   - 判断题（judge）：`"对"` 或 `"错"`；',
        '   - 填空题（blank）：文本，多个空按顺序给数组，如 `["first", "second"]`；',
        '   - 简答题（short）：文本。',
        '',
        '## 输出格式（结构照抄，只替换 answer 的值）',
        '',
        '```json',
        '{',
        '  "format": "chaoxing-answers-v1",',
        '  "answers": [',
        '    {"no": "11.6", "q": 1, "answer": "B"},',
        '    {"no": "11.6", "q": 2, "answer": "A"},',
        '    {"no": "11.6", "q": 3, "answer": ["A", "C"]},',
        '    {"no": "11.6", "q": 4, "answer": "对"},',
        '    {"no": "11.6", "q": 5, "answer": "填空文本"}',
        '  ]',
        '}',
        '```',
        '',
        '---',
        '',
        '## 题目正文（题号与选项字母已标好）',
        '',
    ]
    for p in papers:
        L.append(_md_paper(p))
        L.append('---')
        L.append('')
    L += ['## 再提醒一次', '',
          '你的**整个回复**只能是一个 JSON 代码块（如上面格式所示），不要输出任何其他内容。']
    mpath = os.path.join(outdir, 'exam-%s.md' % stamp)
    with open(mpath, 'w', encoding='utf-8') as f:
        f.write('\n'.join(L))

    # 答案模板：把结构都填好，AI 只需替换 answer
    tpl = {'format': FORMAT_ANSWERS, 'answers': []}
    for p in papers:
        for q in p['questions']:
            tpl['answers'].append({'no': p['no'], 'q': q['q'], 'answer': ''})
    tpath = os.path.join(outdir, 'answers-template-%s.json' % stamp)
    with open(tpath, 'w', encoding='utf-8') as f:
        json.dump(tpl, f, ensure_ascii=False, indent=1)

    fails = payload.get('fails', [])
    with open(os.path.join(outdir, 'latest.json'), 'w', encoding='utf-8') as f:
        json.dump({'json': jpath, 'md': mpath, 'template': tpath,
                   'at': payload.get('exported_at', datetime.now().strftime('%Y-%m-%d %H:%M:%S')),
                   'count': len(papers), 'total_q': total_q, 'fails': fails},
                  f, ensure_ascii=False, indent=1)

    print('\n生成完成：%d 套 / %d 题' % (len(papers), total_q))
    print('  题目(JSON)  ->', jpath)
    print('  给 AI 的文件 ->', mpath)
    print('  答案模板     ->', tpath)
    if fails:
        print('  失败 %d 套：%s' % (len(fails), ','.join(x['no'] for x in fails)))
    return jpath, mpath, tpath


# ------------------------------------------------------------------ 答案解析
JUDGE_TRUE = ('对', '正确', '是', 'true', 't', '√', 'yes', 'y', 'a')
JUDGE_FALSE = ('错', '错误', '否', 'false', 'f', '×', 'x', 'no', 'n', 'b')


def norm_letters(ans):
    """把各种写法的答案统一成字母列表。"""
    if ans is None:
        return []
    if isinstance(ans, (list, tuple)):
        out = []
        for a in ans:
            out += norm_letters(a)
        return out
    s = str(ans).strip().upper()
    s = s.replace('，', ',').replace('、', ',').replace('；', ',').replace(';', ',')
    s = s.replace('.', '').replace('。', '').replace(' ', '')
    if ',' in s:
        parts = [x for x in s.split(',') if x]
    elif re.fullmatch(r'[A-H]{2,}', s):
        parts = list(s)          # "AC" -> ["A","C"]
    else:
        parts = [s] if s else []
    out = []
    for p in parts:
        p = re.sub(r'[^A-H]', '', p)
        if p:
            out.append(p)
    return out


def norm_judge(ans):
    s = str(ans).strip().lower()
    if s in JUDGE_TRUE:
        return '对'
    if s in JUDGE_FALSE:
        return '错'
    if '对' in s or '正确' in s or 'true' in s:
        return '对'
    if '错' in s or 'false' in s:
        return '错'
    return None


def load_answers(path):
    """读答案集，返回 {(no,q): raw_answer}。兼容外层带 ```json 的文本。"""
    txt = open(path, encoding='utf-8').read()
    try:
        data = json.loads(txt)
    except Exception:
        m = re.search(r'\{[\s\S]*\}', txt)
        if not m:
            raise RuntimeError('答案文件不是合法 JSON，也找不到 JSON 块')
        data = json.loads(m.group(0))
    rows = data.get('answers') if isinstance(data, dict) else None
    if not rows:
        raise RuntimeError('答案文件缺少 answers 数组')
    out = {}
    for r in rows:
        no = str(r.get('no') or r.get('paper') or '').strip()
        q = r.get('q', r.get('no_q', r.get('index')))
        if not no or q is None:
            continue
        try:
            q = int(q)
        except Exception:
            continue
        out[(no, q)] = r.get('answer', r.get('ans', ''))
    return out


# ------------------------------------------------------------------ 填答
FILL_FN = r"""function(plan){
  var qs=document.querySelectorAll('.singleQuesId');
  var out=[];
  function norm(s){ return (s||'').replace(/[\u00a0\u3000]/g,' ').replace(/\s+/g,' ').trim(); }
  for(var i=0;i<plan.length;i++){
    var p=plan[i];
    var q=qs[p.n-1];
    if(!q){ out.push({q:p.n, err:'no-question'}); continue; }
    var lis=[].slice.call(q.querySelectorAll('li[aria-label]'));
    if(!lis.length) lis=[].slice.call(q.querySelectorAll('.answerList li, .Zy_ulTop li'));
    var hit=[], missed=[];

    if(p.type==='blank'){
      var ins=[].slice.call(q.querySelectorAll('input[type=text], textarea'))
                .filter(function(e){return !e.disabled && e.type!=='hidden';});
      var texts=p.texts||[];
      for(var b=0;b<texts.length && b<ins.length;b++){
        var el=ins[b];
        el.focus();
        el.value=texts[b];
        el.dispatchEvent(new Event('input',{bubbles:true}));
        el.dispatchEvent(new Event('change',{bubbles:true}));
        el.blur();
      }
      out.push({q:p.n, type:'blank', filled:Math.min(texts.length,ins.length), inputs:ins.length,
                vals:ins.map(function(e){return e.value;})});
      continue;
    }

    var wants=(p.labels||[]).map(function(x){return String(x).toUpperCase();});
    for(var j=0;j<lis.length;j++){
      var li=lis[j];
      var aria=(li.getAttribute('aria-label')||'');
      var t=norm(li.innerText);
      var m=t.match(/^([A-Za-z])[\s、.．)）:：]/);
      var lab=(m?m[1]:'').toUpperCase();
      if(!lab && /^[A-Za-z]$/.test(aria)) lab=aria.toUpperCase();
      if(!lab) lab=String.fromCharCode(65+j);

      var match=false;
      if(p.type==='judge'){
        if(wants.indexOf('对')>=0 && /对|正确|true|√/i.test(aria+' '+t)) match=true;
        if(wants.indexOf('错')>=0 && /错|错误|false|×/i.test(aria+' '+t)) match=true;
      }else{
        if(wants.indexOf(lab)>=0) match=true;
      }
      if(match){
        li.click();
        hit.push(lab);
      }
    }
    var want=wants.length;
    if(hit.length<want) missed=[];
    out.push({q:p.n, type:p.type, want:wants, hit:hit,
              ok: hit.length>=want});
  }
  return JSON.stringify(out);
}"""

VERIFY_JS = r"""
return (function(){
  var qs=document.querySelectorAll('.singleQuesId');
  var out=[];
  for(var i=0;i<qs.length;i++){
    var q=qs[i];
    var inp=q.querySelector('input[id^="answer"]');
    var lis=[].slice.call(q.querySelectorAll('li[aria-label]'));
    var sel=[];
    for(var j=0;j<lis.length;j++){
      var cls=lis[j].className||'';
      var ck=lis[j].querySelector('input:checked');
      if(/check_answer|cur|selected/.test(cls) || ck) sel.push(j+1);
    }
    out.push({q:i+1, value: inp?inp.value:null, picked:sel,
              inputs: [].slice.call(q.querySelectorAll('input[type=text]'))
                        .filter(function(e){return e.type!=='hidden';})
                        .map(function(e){return e.value;})});
  }
  return JSON.stringify(out);
})()
"""


def build_plan(paper, answers):
    """把答案映射成填答计划；返回 (plan, missing, unknown)。"""
    plan, missing = [], []
    for q in paper['questions']:
        key = (paper['no'], q['q'])
        if key not in answers:
            missing.append(q['q'])
            continue
        raw = answers[key]
        t = q['type']
        item = {'n': q['q'], 'type': t}
        if t in ('single', 'multi'):
            letters = norm_letters(raw)
            valid = {o['label'].upper() for o in q['options']}
            letters = [x for x in letters if x in valid] or letters
            if not letters:
                missing.append(q['q'])
                continue
            item['labels'] = letters
        elif t == 'judge':
            j = norm_judge(raw)
            if not j:
                missing.append(q['q'])
                continue
            item['labels'] = [j]
        else:
            if isinstance(raw, (list, tuple)):
                item['texts'] = [str(x) for x in raw]
            else:
                item['texts'] = [str(raw)]
            item['type'] = 'blank'
        plan.append(item)
    return plan, missing


def submit(b):
    b.js("return (function(){var x=document.querySelector('.btnSubmit');if(x){x.click();return 'ok';}"
         "return 'no';})()", frame=QUIZ_FRAME)
    time.sleep(2)
    # 确认弹窗：popok 在主文档
    for _ in range(3):
        try:
            r = b.js("return (function(){var p=document.getElementById('popok');if(p){p.click();return 'ok';}return 'no';})()")
            if r == 'ok':
                break
        except Exception:
            pass
        time.sleep(1.5)
    time.sleep(6)


def answer_file(b, path, dry=True, only=None, force=False):
    answers = load_answers(path)
    cat = catalog_mod.parse(browser=b, save=True)
    byno = {x['no']: x for x in cat['items']}
    nos = sorted({k[0] for k in answers}, key=lambda s: [int(x) for x in re.findall(r'\d+', s)] or [0])
    if only:
        nos = [n for n in nos if n in only]
    results = []
    for no in nos:
        if stopped():
            print('已请求停止，填答提前结束', flush=True)
            break
        it = byno.get(no)
        if not it:
            print('%s 目录里没有，跳过' % no)
            continue
        if it['state'] == 'done':
            print('%s 已完成，跳过' % no)
            continue
        print('\n=== %s %s' % (no, it['title'][:40]), flush=True)
        data = open_and_collect(b, it)
        if not data:
            print('  打不开答题页')
            results.append({'no': no, 'status': 'openfail'})
            continue
        paper = {'no': no, 'title': it['title'], 'questions': data['questions']}
        plan, missing = build_plan(paper, answers)
        if missing:
            msg = '%s 缺答案的题号：%s' % (no, missing)
            if not force:
                print('  ' + msg + ' —— 跳过（--force 可强行只答有答案的题）')
                results.append({'no': no, 'status': 'missing', 'missing': missing})
                continue
            print('  ' + msg + '（--force，继续）')
        print('  计划填答 %d/%d 题' % (len(plan), len(paper['questions'])))
        try:
            filled = json.loads(b.js('return (%s)(%s);' % (FILL_FN, json.dumps(plan)), frame=QUIZ_FRAME))
        except Exception as e:
            print('  填答异常：%s' % str(e)[:160])
            results.append({'no': no, 'status': 'fillerror'})
            continue
        bad = [x for x in filled if not x.get('ok') and x.get('type') != 'blank']
        for x in filled:
            if x.get('type') == 'blank':
                print('    q%-2d 填空 %s/%s  %s' % (x['q'], x.get('filled'), x.get('inputs'), x.get('vals')))
            else:
                print('    q%-2d %-6s 想要%s 实际%s %s'
                      % (x['q'], x.get('type'), ''.join(x.get('want') or []),
                         ''.join(str(h) for h in (x.get('hit') or [])), '' if x.get('ok') else '✗'))
        time.sleep(1)
        b.shot('ans-%s-1-filled.png' % no.replace('.', '-'))
        try:
            vf = json.loads(b.js(VERIFY_JS, frame=QUIZ_FRAME))
            empty = [x['q'] for x in vf if not x.get('value') and not x.get('picked')
                     and not any(x.get('inputs') or [])]
            if empty:
                print('  ⚠ 校验：这些题看起来没填上 %s' % empty)
        except Exception:
            empty = []

        if dry:
            print('  --dry：已填答，未提交。截图 -> ans-%s-1-filled.png' % no.replace('.', '-'))
            results.append({'no': no, 'status': 'dry', 'bad': [x['q'] for x in bad], 'empty': empty})
            continue
        if bad and not force:
            print('  ✗ 有 %d 题没点中，拒绝提交（%s）' % (len(bad), [x['q'] for x in bad]))
            results.append({'no': no, 'status': 'unfilled', 'bad': [x['q'] for x in bad]})
            continue

        submit(b)
        b.shot('ans-%s-2-submitted.png' % no.replace('.', '-'))
        txt = grade_text(b)
        sc = parse_score(txt)
        print('  已提交，分数：%s' % sc)
        results.append({'no': no, 'status': 'submitted', 'score': sc,
                        'at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')})
        time.sleep(2)

    rp = os.path.join(b.io, 'quiz-results.json')
    old = {}
    if os.path.exists(rp):
        try:
            old = json.load(open(rp, encoding='utf-8'))
        except Exception:
            old = {}
    for r in results:
        if r.get('status') == 'submitted':
            old[r['no']] = r
    with open(rp, 'w', encoding='utf-8') as f:
        json.dump(old, f, ensure_ascii=False, indent=1)

    print('\n=== 汇总 ===')
    for r in results:
        print('  %-6s %-10s %s' % (r['no'], r['status'], r.get('score', '')))
    return results


def check_answers(path, outdir):
    """离线校验答案集覆盖度（不碰浏览器）。"""
    lj = os.path.join(outdir, 'latest.json')
    if not os.path.exists(lj):
        print('还没有导出记录，先跑 exam.py export')
        return
    info = json.load(open(lj, encoding='utf-8'))
    exam = json.load(open(info['json'], encoding='utf-8'))
    ans = load_answers(path)
    total = hit = 0
    missing, empty = [], []
    for pap in exam['papers']:
        for q in pap['questions']:
            total += 1
            k = (pap['no'], q['q'])
            v = ans.get(k)
            if v is None:
                missing.append('%s q%d' % k)
            elif isinstance(v, (list, tuple)):
                if len([x for x in v if str(x).strip()]):
                    hit += 1
                else:
                    empty.append('%s q%d' % k)
            elif str(v).strip():
                hit += 1
            else:
                empty.append('%s q%d' % k)
    print('题目总数 %d，已作答 %d，空缺 %d，缺失 %d'
          % (total, hit, len(empty), len(missing)))
    if empty:
        print('  答案为空的：%s' % ', '.join(empty[:30]))
    if missing:
        print('  答案里没有的：%s' % ', '.join(missing[:30]))
    extra = [k for k in ans if k not in {(p2['no'], q['q'])
             for p2 in exam['papers'] for q in p2['questions']}]
    if extra:
        print('  多余条目（题目里找不到，会被忽略）：%s'
              % ', '.join('%s q%d' % k for k in extra[:10]))
    if not missing and not empty:
        print('  ✓ 全覆盖，可以导入')
    else:
        print('  ✗ 还有缺口，导入时该套会被整卷跳过（除非 --force）')


# ------------------------------------------------------------------ 入口
def main():
    if '-h' in sys.argv[1:] or '--help' in sys.argv[1:]:
        print(__doc__)
        return
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    flags = [a for a in sys.argv[1:] if a.startswith('--')]

    # 这两个命令纯离线，不需要浏览器（驱动没起也能用）
    from client import DEFAULT_ROOT
    root0 = os.environ.get('BROWSER_ROOT') or DEFAULT_ROOT
    outdir0 = os.path.join(root0, 'io', 'exports')
    if args and args[0] == 'check':
        if len(args) < 2:
            print('用法：python exam.py check answers.json')
            return
        check_answers(args[1], outdir0)
        return
    if args and args[0] == 'render':
        # 从已有题目 JSON 重新生成「带提示词的文件」，不碰浏览器
        src = args[1] if len(args) > 1 else None
        if not src:
            lj = os.path.join(outdir0, 'latest.json')
            src = json.load(open(lj, encoding='utf-8'))['json'] if os.path.exists(lj) else None
        if not src or not os.path.exists(src):
            print('用法：python exam.py render [题目.json]   （默认用最近一次导出）')
            return
        payload = json.load(open(src, encoding='utf-8'))
        # 重新渲染算一次新的导出，索引时间应刷新为当前时间（而不是源文件的旧时间）
        payload['exported_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        _write_bundle(payload, outdir0)
        return
    if args and args[0] == 'show':
        lj = os.path.join(outdir0, 'latest.json')
        if not os.path.exists(lj):
            print('还没有导出记录')
            return
        info = json.load(open(lj, encoding='utf-8'))
        print('最近一次导出：%s' % info['at'])
        print('  %d 套 / %d 题' % (info['count'], info['total_q']))
        print('  题目 ->', info['json'])
        print('  给 AI ->', info['md'])
        if info.get('fails'):
            print('  失败 ->', ','.join(x['no'] for x in info['fails']))
        return

    b = Browser()
    if b.locate_course() is None:
        raise RuntimeError('请先打开课程学习页（python run.py open）')
    outdir = os.path.join(b.io, 'exports')

    if args and args[0] == 'inspect':
        no = args[1] if len(args) > 1 else ''
        cat = catalog_mod.parse(browser=b, save=True)
        it = next((x for x in cat['items'] if x['no'] == no), None)
        if not it:
            print('目录里没有 %s' % no)
            return
        if not open_quiz(b, it):
            print('打不开答题页')
            return
        time.sleep(4)
        h = b.html(frame=QUIZ_FRAME)
        path = os.path.join(b.io, 'inspect-%s.html' % no.replace('.', '-'))
        open(path, 'w', encoding='utf-8').write(h)
        print('HTML ->', path, len(h), 'bytes')
        prob = b.js(r"""
return (function(){
  function c(s){return (s||'').replace(/\\s+/g,' ').trim();}
  var qs=document.querySelectorAll('.singleQuesId'); var out=[];
  for(var i=0;i<qs.length;i++){
    var q=qs[i];
    var tt=q.querySelector('.newZy_TItle');
    var par=q.parentElement;
    out.push({q:i+1, typeTag:tt?c(tt.innerText):'',
              cls:q.className, parentCls:(par?par.className:'').slice(0,60),
              parentLen:par?c(par.innerText).length:0,
              prevSib:q.previousElementSibling?(q.previousElementSibling.className||q.previousElementSibling.tagName):'-'});
  }
  return JSON.stringify(out,null,1);
})()
""", frame=QUIZ_FRAME)
        print(prob)
        b.shot('inspect-%s.png' % no.replace('.', '-'))
        return

    if args and args[0] == 'export':
        nos = []
        for fl in flags:
            if fl.startswith('--no='):
                nos = [x for x in fl.split('=', 1)[1].split(',') if x]
        limit = 0
        for fl in flags:
            if fl.startswith('--limit='):
                limit = int(fl.split('=', 1)[1])
        cat = catalog_mod.parse(browser=b, save=True)
        if nos:
            targets = [x for x in cat['items'] if x['no'] in nos]
        else:
            targets = [x for x in cat['pending'] if is_quiz(x['title']) and x['pending'] > 0]
        if limit:
            targets = targets[:limit]
        if not targets:
            print('没有待导出的测验')
            return
        print('待导出 %d 套测验' % len(targets))
        export(b, targets, outdir)
        return

    if args and args[0] == 'answer':
        if len(args) < 2:
            print('用法：python exam.py answer answers.json [--dry|--submit] [--only=11.6] [--force]')
            return
        only = None
        for fl in flags:
            if fl.startswith('--only='):
                only = {x for x in fl.split('=', 1)[1].split(',') if x}
        dry = '--submit' not in flags
        answer_file(b, args[1], dry=dry, only=only, force='--force' in flags)
        return

    print(__doc__)


if __name__ == '__main__':
    main()
