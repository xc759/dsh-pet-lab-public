# -*- coding: utf-8 -*-
"""知识增量提炼：把记忆条目压成"话题计数 + 关键词摘要"，**绝不搬原句**。

红线（阶段 B 定稿）：对话内容不出本机、档案里不留聊天原文。所以这里只处理
``ChatMemory`` 已经算好的 ``keywords``（短词），不读 ``text``。由此带来的
约束是：一条没抽出关键词的条目就什么都不写，宁缺毋滥。

产出两样东西：

* ``topics``：``{词: 次数}``，供面板"最近常聊"和注入摘要用；
* ``increments``：``{text, tags, source}``，``text`` 是由关键词拼出的短语，
  不是句子——这样既能"回头看学过什么"，又不会把聊天记录留一份副本。
"""
from __future__ import annotations

import logging

from .schema import MAX_KNOWLEDGE, MAX_TOPICS, new_id, now_iso

logger = logging.getLogger(__name__)

#: 太通用的词不给进话题表，否则"学习""问题"会把排行榜占满
STOPWORDS = frozenset({
    "学习", "问题", "今天", "明天", "昨天", "可以", "什么", "怎么", "为什么",
    "现在", "时候", "这样", "一下", "东西", "事情", "感觉", "知道", "觉得",
    "帮我", "我们", "你们", "他们", "这个", "那个", "然后", "还是", "就是",
    "the", "and", "for", "you", "are", "but", "not", "with",
})

#: 含这些虚词/代词的词组一律丢——上游关键词是 bigram，"换怎""么算"这类切口
#: 看着像词其实不是，进榜只会让"最近常聊"变成乱码。
STOP_CHARS = frozenset("的了是在和与就都也很吗呢吧啊么怎这那你我他她它们有没不把被给让")

#: 单条记忆最多贡献几个词（多的是噪声）
MAX_WORDS_PER_ENTRY = 4


def _clean_word(value) -> str:
    if value is None or not isinstance(value, str):
        return ""
    word = value.strip().strip("#,，。、;；:：()（）[]【】\"'“”").lower()
    if not word or len(word) > 24:
        return ""
    if word in STOPWORDS:
        return ""
    if len(word) == 1 and not word.isascii():
        return ""
    if len(word) <= 4 and any(ch in STOP_CHARS for ch in word):
        return ""
    return word


def entry_keywords(entry) -> list[str]:
    """一条记忆条目 → 去重后的关键词（只读 ``keywords``，不碰 ``text``）。"""
    if not isinstance(entry, dict):
        return []
    if entry.get("archived"):
        return []
    raw = entry.get("keywords")
    if not isinstance(raw, (list, tuple)):
        return []
    words: list[str] = []
    for item in raw:
        word = _clean_word(item)
        if word and word not in words:
            words.append(word)
        if len(words) >= MAX_WORDS_PER_ENTRY:
            break
    return words


def harvest_topics(entries, existing: dict | None = None) -> dict[str, int]:
    """统计话题次数。返回的是**增量**（在 ``existing`` 基础上累加后的结果）。"""
    counts: dict[str, int] = {}
    for name, value in (existing or {}).items():
        key = _clean_word(name)
        try:
            number = int(value)
        except (TypeError, ValueError):
            continue
        if key and number > 0:
            counts[key] = counts.get(key, 0) + number
    for entry in entries or []:
        for word in entry_keywords(entry):
            counts[word] = counts.get(word, 0) + 1
    if len(counts) <= MAX_TOPICS:
        return counts
    # 超限：留计数最高的，同分按词排序保证结果稳定（否则测试与快照会抖）
    top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:MAX_TOPICS]
    return dict(top)


def distill(entries, existing_increments=None, *, moment=None,
            max_new: int = 20) -> list[dict]:
    """记忆条目 → 待追加的知识摘要（已存在的组合不重复生成）。"""
    taken = set()
    for row in existing_increments or []:
        if not isinstance(row, dict):
            continue
        tags = tuple(sorted(str(t) for t in (row.get("tags") or []) if str(t)))
        if tags:
            taken.add(tags)

    stamp = now_iso(moment)
    rows: list[dict] = []
    for entry in entries or []:
        words = entry_keywords(entry)
        if not words:
            continue
        signature = tuple(sorted(words))
        if signature in taken:
            continue
        taken.add(signature)
        rows.append({
            "id": new_id("kb-"),
            "text": " · ".join(words),
            "tags": words,
            "source": "chat",
            "created_at": stamp,
        })
        if len(rows) >= max_new:
            break
    return rows


def merge_increments(existing, rows) -> list[dict]:
    """追加并裁到上限（丢最旧）。"""
    merged = [r for r in (existing or []) if isinstance(r, dict)]
    merged.extend(rows or [])
    if len(merged) > MAX_KNOWLEDGE:
        merged = merged[-MAX_KNOWLEDGE:]
    return merged
