"""按资源名字判断类型：视频 / 文档 / 测验作业 / 其它。

两个平台（AI 优课、zjy2）的目录节点只有名字和进度，没有可靠的类型字段，
所以统一按名字判定，规则集中在这里，改一处两个平台都跟着变。

判定顺序很重要：
1. **先看扩展名** —— 「第二节 心理咨询 测验.mp4」是视频，不是测验；
2. 再看关键词 —— 「单元测验 / 作业 / 讨论 / 考试」这类**不能自动刷**，
   工具要如实跳过并告诉用户自己去答，而不是假装刷完。
"""
import re

VIDEO_EXT = re.compile(r"\.(mp4|mov|avi|flv|m3u8|wmv|mkv|rmvb|ts)$", re.I)
DOC_EXT = re.compile(r"\.(pdf|docx?|pptx?|xlsx?|txt|rtf|wps|et|dps|csv)$", re.I)

# 题目类：需要人（或大模型）作答，拖进度条/翻页都不算完成
QUIZ_WORDS = re.compile(
    r"测验|测试|考试|作业|练习|习题|题库|答题|做题|问卷|投票|调研|自测|"
    r"单元测|期中|期末|考核|互评"
)

KIND_LABEL = {
    "video": "视频",
    "doc": "文档",
    "quiz": "测验/作业",
    "other": "其它",
}


def kind_of_name(name):
    """返回 video / doc / quiz / other。"""
    n = (name or "").strip()
    if not n:
        return "other"
    if VIDEO_EXT.search(n):
        return "video"
    if DOC_EXT.search(n):
        return "doc"
    if QUIZ_WORDS.search(n):
        return "quiz"
    return "other"


def is_brushable(kind):
    """能不能靠「拖进度条 / 翻页」刷完。测验作业不行——必须自己答。"""
    return kind in ("video", "doc")
