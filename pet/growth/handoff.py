# -*- coding: utf-8 -*-
"""半自动转交外部 AI 助手：超纲识别 → 桌宠提议 → 一键打包上下文并唤起。

设计红线：

* **不自动跳转**。桌宠只在气泡里提议，用户点了才真的走；用户不点就当没发生。
* **只在剪贴板里过一手**。打包内容是用户"自己贴给自己"的文本，写剪贴板即止，
  绝不落盘、绝不进成长档案。
* **唤起失败也算半成功**。深链被系统拒绝时剪贴板还在，文案会提示手动粘贴。
* **零内置品牌**。本模块不绑定任何具体产品：目标由 ``handoff_scheme``
  （配置键）或环境变量 ``DSH_HANDOFF_SCHEME`` 提供；未配置即"纯剪贴板模式"，
  只把整理好的上下文放进剪贴板，不唤起任何程序。

模块自身不 import Qt：剪贴板与唤起都从外部注入，方便单测。
"""
from __future__ import annotations

import logging
import os
import re
import sys
from datetime import datetime

LOG = logging.getLogger("dsh-pet-standalone")

#: 深链 scheme 的默认值：**空**。空 = 纯剪贴板模式（不唤起任何外部程序）。
DEFAULT_SCHEME = ""

#: 未配置时气泡按钮/文案里的中性称呼。
DEFAULT_LABEL = "AI 助手"

#: 环境变量名（便于不写配置文件的临时覆盖）。
SCHEME_ENV = "DSH_HANDOFF_SCHEME"
LABEL_ENV = "DSH_HANDOFF_LABEL"


def handoff_scheme(cfg=None) -> str:
    """当前生效的深链，优先级：环境变量 > 配置键 ``handoff_scheme`` > 空。

    空字符串表示"只想把上下文放进剪贴板"，这是公开版的默认行为。
    """
    raw = str(os.environ.get(SCHEME_ENV) or "").strip()
    if not raw and cfg is not None:
        try:
            raw = str(cfg.get("handoff_scheme", "") or "").strip()
        except Exception:  # noqa: BLE001 - 配置坏掉按未配置处理
            raw = ""
    # 只接受 scheme 形式，避免把任意命令行当成可执行目标
    if raw and not re.match(r"^[A-Za-z][A-Za-z0-9+.\-]*://", raw):
        LOG.warning("handoff_scheme 不是合法深链，已忽略：%s", raw)
        return ""
    return raw


def handoff_label(cfg=None) -> str:
    """外部助手的中性称呼（显示在按钮与气泡文案里）。"""
    raw = str(os.environ.get(LABEL_ENV) or "").strip()
    if not raw and cfg is not None:
        try:
            raw = str(cfg.get("handoff_agent_label", "") or "").strip()
        except Exception:  # noqa: BLE001
            raw = ""
    return raw or DEFAULT_LABEL

#: 命中任一词即判为"这活儿桌宠干不了"——需要动手、需要外部工具、需要联网。
STRONG_CUES = (
    "写代码", "改代码", "写个脚本", "跑一下", "运行一下", "帮我跑", "debug", "报错",
    "打开网页", "搜一下", "搜一搜", "查一下资料", "联网", "下载", "安装", "卸载",
    "下单", "买一个", "订票", "发邮件", "发消息给", "整理文件", "重装",
    "做个 PPT", "做个PPT", "写个报告", "写篇文章", "写份", "翻译一下",
    "画个图", "生成图片", "爬", "接口", "调 API", "调API", "表格", "Excel", "excel",
    "文件", "文档", "PDF", "pdf", "截图", "录屏",
)

#: 泛问句：单独命中不算超纲，但配上长度/叠加时会把"随口一问"推向转交。
WEAK_CUES = (
    "怎么做", "怎么弄", "为什么", "解释一下", "原理", "推导", "证明",
    "对比", "总结一下", "规划一下", "帮我看看", "帮我分析", "值不值",
)

#: 口语化的短问句（"今天学什么"）即使命中弱线索也该留在桌宠里。
CASUAL_MAX_CHARS = 12
#: 超过这个字数基本不是随口一问。
LONG_TEXT_CHARS = 60


def score(text: str) -> int:
    """给一段用户输入打"该不该转交"的分。

    * 强线索：每个 +3，命中即大概率超纲；
    * 弱线索：每个 +1（泛问句本身桌宠也能聊两句）；
    * 长文本：+2（说得这么细，多半是要办事）；
    * 极短口语问句：直接压到 0，避免"怎么啦"这种也被转走。
    """
    raw = str(text or "").strip()
    if not raw:
        return 0
    hits_strong = sum(1 for cue in STRONG_CUES if cue in raw)
    hits_weak = sum(1 for cue in WEAK_CUES if cue in raw)
    total = hits_strong * 3 + hits_weak
    if len(raw) >= LONG_TEXT_CHARS:
        total += 2
    if len(raw) <= CASUAL_MAX_CHARS and not hits_strong:
        # 短口语问句最多算"弱"：不叠加长度分，也别因为它短就强行清零——
        # 否则阈值调低时"解释一下X"这类真问题永远转不出去。
        total = min(total, 1)
    return total


def is_complex(text: str, threshold: int = 3) -> tuple[bool, int]:
    """是否该提议转交给外部助手。返回 ``(是否转交, 分数)``。"""
    got = score(text)
    return got >= threshold, got


def _exam_days(profile: dict, now: datetime) -> int | None:
    learning = (profile or {}).get("learning")
    learning = learning if isinstance(learning, dict) else {}
    raw = str(learning.get("exam_date") or "").strip()
    if not raw:
        return None
    try:
        due = datetime.fromisoformat(raw.replace("/", "-")).date()
    except ValueError:
        return None
    return (due - now.date()).days


def build_payload(text: str, archive: dict | None = None, now: datetime | None = None) -> str:
    """把"这句请求 + 我的档案摘要"拼成一段可直接粘给任意 AI 助手的上下文。"""
    now = now or datetime.now()
    body = str(text or "").strip()
    lines = ["【来自桌宠的转交】", "", "我的请求：", body or "（空）", ""]

    data = archive if isinstance(archive, dict) else {}
    profile = data.get("profile") if isinstance(data.get("profile"), dict) else {}
    learning = profile.get("learning") if isinstance(profile.get("learning"), dict) else {}
    lines_ctx: list[str] = []
    goal = str(learning.get("goal") or "").strip()
    if goal:
        lines_ctx.append(f"- 目标：{goal}")
    days = _exam_days(profile, now)
    if days is not None:
        tail = f"，还剩 {days} 天" if days >= 0 else f"，已过去 {-days} 天"
        lines_ctx.append(f"- 考试日：{learning.get('exam_date')}{tail}")
    subjects = [str(s) for s in (learning.get("subjects") or []) if str(s).strip()]
    if subjects:
        lines_ctx.append("- 备考科目：" + "、".join(subjects[:6]))

    progress = data.get("progress") if isinstance(data.get("progress"), dict) else {}
    focus = progress.get("focus_minutes") if isinstance(progress, dict) else None
    if isinstance(focus, int) and focus > 0:
        lines_ctx.append(f"- 今日专注：{focus} 分钟")
    today = now.date().isoformat()
    topics = data.get("topics") if isinstance(data.get("topics"), dict) else {}
    recent = topics.get(today) if isinstance(topics.get(today), dict) else {}
    names = [str(k) for k, v in recent.items() if isinstance(v, int) and v > 0]
    if names:
        lines_ctx.append("- 今天聊到：" + "、".join(names[:6]))

    if lines_ctx:
        lines.append("我的近况（桌宠档案摘要）：")
        lines.extend(lines_ctx)
        lines.append("")
    lines.append("（以上由桌宠替你整理，你可以直接接着提要求。）")
    return "\n".join(lines)


def proposal_text(text: str, max_chars: int = 24, label: str | None = None) -> str:
    """桌宠提议文案。带上请求开头，用户才知道"转的是哪一句"。"""
    raw = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(raw) > max_chars:
        raw = raw[:max_chars] + "…"
    who = str(label or DEFAULT_LABEL)
    return f"「{raw}」这事我接不住，要我打包转给{who}吗？" if raw else f"这事我接不住，要我转给{who}吗？"


def button_label(label: str | None = None) -> str:
    """气泡按钮文案（调用方不再硬编码具体产品名）。"""
    return f"转给 {str(label or DEFAULT_LABEL)}"


# ------------------------------------------------------------------ 副作用层
def copy_to_clipboard(text: str) -> bool:
    """写系统剪贴板：优先 Qt（已在跑 QApplication），回退 clip.exe。"""
    payload = str(text or "")
    if not payload:
        return False
    try:
        from PySide6.QtGui import QGuiApplication

        if QGuiApplication.instance() is not None:
            QGuiApplication.clipboard().setText(payload)
            return True
    except Exception:  # noqa: BLE001 - Qt 不在/没实例都继续走回退
        LOG.debug("Qt 剪贴板不可用，回退 clip.exe", exc_info=True)
    if sys.platform == "win32":
        try:
            import subprocess

            proc = subprocess.run(
                ["clip"], input=payload.encode("utf-16-le"),
                capture_output=True, timeout=5,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return proc.returncode == 0
        except Exception:  # noqa: BLE001
            LOG.warning("写剪贴板失败", exc_info=True)
    return False


def launch_external(url: str = "") -> bool:
    """按深链唤起外部程序。未配置 scheme 时**什么都不做**并返回 False。"""
    target = str(url or "").strip()
    if not target:
        # 纯剪贴板模式：不是错误，调用方据此把文案降级为"自己粘贴"
        return False
    try:
        if sys.platform == "win32":
            os.startfile(target)  # noqa: S606 - scheme 已校验为 xxx:// 形式
        else:
            import subprocess

            subprocess.Popen(["xdg-open", target])  # noqa: S603,S607
        return True
    except Exception:  # noqa: BLE001 - 唤起失败不该打断对话
        LOG.warning("唤起外部助手失败：%s", target, exc_info=True)
        return False


def handoff(
    text: str,
    archive: dict | None = None,
    *,
    now: datetime | None = None,
    clipboard=None,
    launcher=None,
    allow_launch: bool = True,
) -> dict:
    """执行转交：打包 → 剪贴板 → 唤起。任何一步失败都不抛异常。

    返回 ``{"ok", "payload", "copied", "launched", "reason"}``：
    ``ok`` 只代表"剪贴板拿到了上下文"——唤起失败仍算 ok，
    因为用户可以手动粘贴，这时 ``reason`` 会说明该自己粘。
    """
    payload = build_payload(text, archive, now)
    copy_fn = clipboard or copy_to_clipboard
    copied = False
    try:
        copied = bool(copy_fn(payload))
    except Exception:  # noqa: BLE001
        LOG.warning("转交时写剪贴板异常", exc_info=True)
        copied = False

    launched = False
    if copied and allow_launch:
        launch_fn = launcher or launch_external
        try:
            launched = bool(launch_fn())
        except Exception:  # noqa: BLE001
            LOG.warning("转交时唤起外部助手异常", exc_info=True)
            launched = False

    if not copied:
        reason = "剪贴板被占用，没能打包"
    elif launched:
        reason = "已复制并唤起外部助手，直接粘贴发送"
    else:
        reason = "已复制到剪贴板，切过去粘贴即可"
    return {
        "ok": copied,
        "payload": payload,
        "copied": copied,
        "launched": launched,
        "reason": reason,
    }
