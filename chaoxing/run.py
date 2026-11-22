"""run.py —— 统一入口。

    python run.py login            # 打开登录页并截图，等你扫码；扫完自动检测并落盘
    python run.py login-pwd 账号 密码   # 账号密码登录（滑块需人工拖一次）
    python run.py courses           # 只扫描课程列表（StudyBuddy「扫描课程」用）
    python run.py open [关键词]     # 进个人空间 -> 找课程 -> 打开课程学习页，写 io/session.json
    python run.py catalog          # 解析章节目录，打印待完成清单
    python run.py video 7.1,8.1    # 视频任务点（可多个）
    python run.py video --pending  # 所有待完成条目（无视频的自动跳过）
    python run.py quiz --list      # 列出待完成测验
    python run.py quiz 11.6        # 测验采集/填答/提交（手工给答案）
    python run.py exam export --pending          # 导出题目+阅读材料（给 AI 作答）
    python run.py exam answer answers.json       # 导入答案集自动填答（加 --submit 才提交）
    python run.py status           # 当前标签页与框架
"""

import json
import os
import re
import sys
import time

from client import Browser, stopped

SPACE = 'https://i.chaoxing.com/'


def cmd_status(b):
    for p in b.titles():
        print('[%d] %s  %s' % (p['i'], p.get('title', '')[:30], p['url'][:110]))
    print('当前 pageIndex =', b.page_index)
    for f in b.frames():
        print('   frame:', f[:140])


def cmd_login(b, wait_s=180):
    b.goto('https://v8.chaoxing.com/', settle=6000)
    # 默认标签会在扫码 / 账密之间自动切换，点一次扫码并给它渲染时间。
    # 二维码加载失败也没关系：用户可以直接在弹出的窗口里用账号密码登录，
    # 成功判定看的是 URL 是否离开 v8.chaoxing.com，两种方式都兼容。
    try:
        b.click(text='扫码登录', settle=3000)
        b.wait(2500)
    except Exception:
        pass
    shot = b.shot('login-qr.png')
    print('登录页截图:', shot)
    print('请在弹出的浏览器窗口里扫码，或用手机号 / 账号密码登录；最多等 %d 秒…' % wait_s)
    t0 = time.time()
    while time.time() - t0 < wait_s:
        if stopped():
            print('已请求停止，不再等待扫码', flush=True)
            return {'ok': False, 'stopped': True}
        try:
            url = b.js('return location.href;', timeout=15) or ''
        except Exception:
            url = ''
        if 'v8.chaoxing.com' not in url:
            print('已跳转:', url)
            break
        time.sleep(3)
    b.goto(SPACE, settle=4000)
    txt = b.text()[:200].replace('\n', ' ')
    print('登录后页面:', txt[:120])
    try:
        now = b.js('return location.href;', timeout=15) or ''
    except Exception:
        now = ''
    logged = 'v8.chaoxing.com' not in now
    if not logged:
        print('仍在登录页，后面扫描课程会失败；请重新点①并登录', flush=True)
    return {'ok': True, 'logged_in': logged}


def cmd_login_password(b, user, password, wait_s=180):
    """账号密码登录（StudyBuddy「账号密码登录」用）。

    平台在提交账号密码后会弹滑块拼图，脚本无法也不应绕过：
    这里自动填表、勾选、提交，出现滑块时打印提示并继续等用户拖完。

    返回 {'ok': bool, 'logged_in': bool, 'reason': str}
    """
    try:
        cur = b.js('return location.href;', timeout=15) or ''
    except Exception:
        cur = ''
    if cur and 'v8.chaoxing.com' not in cur:
        print('当前已是登录状态，无需再登录')
        return {'ok': True, 'logged_in': True, 'reason': 'already'}

    b.goto('https://v8.chaoxing.com/', settle=6000)

    # 1) 确保停在「账号登录」标签（页面默认可能停在扫码页，且会异步切换）
    def has_form():
        try:
            return bool(b.js("return !!document.querySelector('#uunnmm');", timeout=15))
        except Exception:
            return False

    for _ in range(3):
        if has_form():
            break
        try:
            b.click(text='账号登录', settle=1500)
        except Exception:
            pass
        b.wait(1200)
    if not has_form():
        print('没找到账号登录表单，页面可能改版', flush=True)
        return {'ok': False, 'logged_in': False, 'reason': '找不到账号登录表单'}

    # 2) 填账号密码（受控输入：fill 会派发 input 事件，超星的按钮才会解禁）
    b.fill('#uunnmm', user)
    b.fill('#pwd', password)
    b.wait(600)
    try:
        got = b.js("return (document.querySelector('#uunnmm')||{}).value || '';", timeout=15) or ''
    except Exception:
        got = user
    if got != user:
        print('账号没填进去，重试一次…', flush=True)
        b.click(selector='#uunnmm', settle=500)
        b.fill('#uunnmm', user)
        b.wait(400)

    # 3) 提交
    print('已填入账号密码，点登录…', flush=True)
    try:
        b.click(selector='#login', settle=2500, timeout=15000)
    except Exception as e:
        return {'ok': False, 'logged_in': False, 'reason': '点不动登录按钮：%s' % e}

    # 4) 等结果；出现滑块就提示一次，然后继续等人工拖完
    t0 = time.time()
    told = False
    while time.time() - t0 < wait_s:
        if stopped():
            print('已请求停止，不再等待登录', flush=True)
            return {'ok': False, 'logged_in': False, 'reason': 'stopped'}
        # 别只取 innerText 前 400 字：滑块文案在页面末尾，会被截掉。
        # 直接在页面里算好标记，既准又快。
        probe = (
            'return JSON.stringify({'
            'url: location.href,'
            "captcha: !!document.querySelector('.u-captcha, .cx_slider_tip, [class*=verify-slide]'),"
            "err: (document.body.innerText.match(/账号或密码(错误|不正确)|密码错误/)||[''])[0]"
            '});')
        try:
            info = json.loads(b.js(probe, timeout=15) or '{}')
        except Exception:
            info = {}
        url = info.get('url', '')
        if url and 'v8.chaoxing.com' not in url:
            print('登录成功:', url, flush=True)
            return {'ok': True, 'logged_in': True, 'reason': 'ok'}
        if not told and info.get('captcha'):
            told = True
            print('平台要求滑块验证：请在浏览器窗口里向右拖动滑块，拖完程序会自动继续…', flush=True)
        if info.get('err') and not info.get('captcha'):
            print('平台提示账号或密码不正确', flush=True)
            return {'ok': False, 'logged_in': False, 'reason': '账号或密码错误'}
        time.sleep(1.5)

    print('等待登录超时', flush=True)
    return {'ok': False, 'logged_in': False, 'reason': '等待登录超时（%d 秒）' % wait_s}


def list_courses(b):
    """只扫描个人空间的课程卡片，不打开任何课程（StudyBuddy 扫描课程用）。"""
    b.goto(SPACE, settle=5000)
    f = b.frame_of('visit/interaction')
    if not f:
        raise RuntimeError('个人空间框架没加载出来（可能没登录）')
    html = b.html(f)
    # 每门课一个 <div class="course ..." id="c_<courseid>"> 卡片块
    marks = list(re.finditer(r'<div class="course[^"]*"[^>]*id="c_(\d+)"', html))
    courses = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else min(len(html), m.start() + 12000)
        block = html[m.start():end]
        cid = m.group(1)
        nm = re.findall(r'course-name[^>]*title="([^"]+)"', block)
        name = nm[0].strip() if nm else ''
        pr = re.findall(r'任务点进度[:：]\s*(\d+)\s*/\s*(\d+)', block)
        done, total = (int(pr[-1][0]), int(pr[-1][1])) if pr else (None, None)
        href = re.search(r'href="([^"]*stucoursemiddle[^"]*)"', block)
        link = href.group(1).replace('&amp;', '&') if href else (
            'https://mooc1.chaoxing.com/visit/stucoursemiddle?courseid=%s&clazzid=&cpi=&ismooc2=1&v=2' % cid)
        courses.append({'name': name, 'url': link, 'done': done, 'total': total, 'cid': cid})
    if not courses:
        raise RuntimeError('没解析到课程链接')
    # 去重（同一课程可能出现多个链接）
    seen = set()
    uniq = []
    for c in courses:
        key = c['url'].split('&v=')[0]
        if key in seen:
            continue
        seen.add(key)
        uniq.append(c)
    return uniq


def cmd_open(b, keyword=''):
    courses = list_courses(b)
    if keyword:
        hit = [c for c in courses if keyword == c['name']]
        if not hit:
            hit = [c for c in courses if keyword in c['name']]
        if hit:
            courses = hit
        else:
            raise RuntimeError('没找到课程「%s」，请重新扫描课程列表再选' % keyword)
    else:
        courses.sort(key=lambda c: (c['total'] or 0) - (c['done'] or 0), reverse=True)
    c = courses[0]
    print('可选课程:')
    for x in courses:
        print('  - %s  %s/%s' % (x['name'], x['done'], x['total']))
    print('选中:', c['name'], ' 进度 %s/%s' % (c['done'], c['total']))
    cid_m = re.search(r'courseid=(\d+)', c['url'])
    cid = cid_m.group(1) if cid_m else ''
    b.goto(c['url'], settle=5000)
    # 课程页可能在新标签打开：优先找同 courseid 的课程标签
    idx = None
    for p in b.titles():
        if cid and ('courseid=' + cid) in p['url'] and 'mycourse/stu' in p['url']:
            idx = p['i']
            break
    if idx is None:
        b.wait(3000)
        idx = b.find_page_with_frame('mycourse/studentcourse')
    if idx is None:
        raise RuntimeError('打开后没有找到目录框架，请检查是否被拦到登录页')
    b.page_index = idx
    course_url = b.titles()[idx]['url']
    sess = {'courseName': c['name'], 'courseUrl': course_url, 'courseLink': c['url'],
            'at': time.strftime('%Y-%m-%d %H:%M:%S')}
    with open(os.path.join(b.io, 'session.json'), 'w', encoding='utf-8') as f2:
        json.dump(sess, f2, ensure_ascii=False, indent=1)
    print('会话已保存 ->', os.path.join(b.io, 'session.json'))
    b.shot('course-page.png')
    return sess


def main():
    if '-h' in sys.argv[1:] or '--help' in sys.argv[1:]:
        print(__doc__)
        return
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'status'
    rest = sys.argv[2:]
    b = Browser()

    if cmd == 'status':
        cmd_status(b)
    elif cmd == 'login':
        cmd_login(b)
    elif cmd == 'login-pwd':
        if len(rest) < 2:
            print('用法: python run.py login-pwd <账号> <密码>')
            return
        r = cmd_login_password(b, rest[0], rest[1])
        print(r)
    elif cmd == 'courses':
        for x in list_courses(b):
            print('%s\t%s/%s\t%s' % (x['name'], x['done'], x['total'], x['url']))
    elif cmd == 'open':
        cmd_open(b, rest[0] if rest else '')
    elif cmd == 'catalog':
        import catalog
        r = catalog.parse(browser=b)
        catalog.show(r)
    elif cmd == 'video':
        import video
        sys.argv = ['video.py'] + rest
        video.main()
    elif cmd == 'quiz':
        import quiz
        sys.argv = ['quiz.py'] + rest
        quiz.main()
    elif cmd == 'exam':
        import exam
        sys.argv = ['exam.py'] + rest
        exam.main()
    else:
        print(__doc__)


if __name__ == '__main__':
    main()
