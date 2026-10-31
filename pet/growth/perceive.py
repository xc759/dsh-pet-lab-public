"""对话文本的轻量感知：关键词命中 + 计数，不调用任何模型、不出本机。

阶段 B 的红线决定了这里只能做「规则」而不是「推理」：

* **零网络、零模型**：用户说过的话一个字都不该为了"懂他"而被送到云端。
* **只出标签与计数**：档案里存的是 ``{"数学": 12, "408": 8}`` 和情绪计数，
  不是原句。原句归上游 ``ChatMemory`` 管，两边不重复囤积。
* **判不准就不说**：命中不到明确词就返回空，绝不"猜到"用户焦虑或偷懒。
  宁可少记录，也不要让桌宠拿错判去劝人。
"""
from __future__ import annotations

from .schema import MAX_TOPICS

#: 学习相关词（命中任一即算"在聊学习"）
STUDY_WORDS = (
    "考研", "408", "数学", "英语", "政治", "专业课", "真题", "刷题", "背单词",
    "复习", "错题", "算法", "代码", "论文", "上课", "作业", "实验", "复试",
    "初试", "线代", "高数", "概率", "数据结构", "操作系统", "计网", "计算机网络",
    "组成原理", "雅思", "托福", "期末", "考试", "自习", "笔记", "网课", "做题", "题目",
)

#: 科目归并：命中任一别名即归到左侧标准名，用于"这周聊得最多的是哪科"
SUBJECT_ALIASES = {
    "数学": ("数学", "高数", "线代", "概率", "微积分", "数一", "数二", "数三"),
    "408": ("408", "数据结构", "操作系统", "计网", "计算机网络", "组成原理", "机组"),
    "英语": ("英语", "单词", "阅读理解", "作文", "翻译", "雅思", "托福", "四六级"),
    "政治": ("政治", "马原", "毛中特", "肖四", "肖八", "史纲"),
}

#: 情绪词 → 状态。**必须是明确表述**，模糊词（"还行""一般"）一律不收。
EMOTION_WORDS = {
    "tired": ("累", "好困", "困死", "熬夜", "撑不住", "睡不醒", "没精神",
              "眼睛酸", "头疼", "扛不住", "犯困"),
    "distracted": ("摸鱼", "走神", "分心", "刷手机", "刷视频", "看不进去",
                   "静不下心", "拖延", "开了小差"),
    "low": ("焦虑", "崩溃", "想放弃", "来不及", "考不上", "难过", "压力大",
            "丧", "委屈", "自闭", "学不动", "不想学", "烦死", "心态崩"),
    "energized": ("有干劲", "状态好", "效率高", "学进去了", "开窍", "拿下",
                  "有成就感", "超爽", "稳了"),
}

#: 情绪对能量与当日心情的影响（心情取值 -5..5）
EMOTION_IMPACT = {
    "tired": (-6, -1),
    "distracted": (-4, -1),
    "low": (-8, -2),
    "energized": (5, 2),
}

#: 命中学习词但无明确情绪时的当日心情加成
STUDY_MOOD_BONUS = 1


def classify(text: str) -> dict:
    """把一段用户输入打成标签。

    返回 ``{"study": bool, "subject": str, "emotion": str, "topics": list[str]}``；
    全部为空/False 表示"这段没信息可记"。
    """
    raw = str(text or "")
    if not raw.strip():
        return {"study": False, "subject": "", "emotion": "", "topics": []}

    lowered = raw.lower()

    study_hits = [word for word in STUDY_WORDS if word in raw or word in lowered]
    subject = ""
    for name, aliases in SUBJECT_ALIASES.items():
        if any(alias in raw or alias in lowered for alias in aliases):
            subject = name
            break

    emotion = ""
    for state, words in EMOTION_WORDS.items():
        if any(word in raw for word in words):
            emotion = state
            break

    topics: list[str] = []
    if subject:
        topics.append(subject)
    for word in study_hits[:2]:
        if word not in topics:
            topics.append(word)

    return {
        "study": bool(study_hits),
        "subject": subject,
        "emotion": emotion,
        "topics": topics[:MAX_TOPICS],
    }
