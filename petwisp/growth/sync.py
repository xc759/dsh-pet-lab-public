# -*- coding: utf-8 -*-
"""周快照外发：把脱敏快照同步到一个**独立私有仓**。

三条硬约束（写在这里，改代码前先读一遍）：

1. **只发快照，不发档案**。档案里有成长记录、错题、情绪；快照是 ``snapshot.py``
   生成的摘要，聊天原文与 API Key 已在生成时就剔掉了。这里只搬 ``*.json`` 快照。
2. **只在给定仓目录里动 ``snapshots/`` 子目录**。绝不 ``reset`` / ``checkout`` /
   ``pull`` / ``clean``——用户那个仓可能还放着别的东西，桌宠没有权力动它们。
3. **默认关闭**。没配 ``growth_snapshot_repo`` 就什么都不做；任何失败只返回状态，
   不抛异常（桌宠不能因为网断了就崩）。

``git`` 调用全部走 ``runner`` 注入，测试里换成假的，不依赖本机装没装 git。
"""
from __future__ import annotations

import logging
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from . import snapshot as snapshot_mod

logger = logging.getLogger(__name__)

#: 仓内放快照的子目录名（与本地 ``growth/snapshots`` 同名，便于对照）
SNAPSHOT_SUBDIR = "snapshots"

#: git 单条命令超时：push 走网络，给足 60s，超时就当失败
GIT_TIMEOUT = 60

#: 提交信息前缀：仓库里一眼能看出是桌宠写的
COMMIT_PREFIX = "pet: weekly growth snapshot"


def _run_git(args: list[str], cwd: Path) -> tuple[int, str]:
    """跑一条 git 命令，返回 ``(返回码, 输出)``；异常一律折成返回码 -1。"""
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=GIT_TIMEOUT,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        return proc.returncode, out.strip()
    except FileNotFoundError:
        return -1, "未找到 git 可执行文件"
    except subprocess.TimeoutExpired:
        return -1, "git 超时"
    except Exception:  # noqa: BLE001 - 旁路动作，任何异常都不能往上冒
        logger.debug("git 调用失败: %s", args, exc_info=True)
        return -1, "git 调用异常"


def is_repo(repo_dir: Path) -> bool:
    """``.git`` 在就算（不用 ``git rev-parse``，省一次进程）。"""
    return (Path(repo_dir) / ".git").exists()


def copy_snapshots(config_dir, repo_dir) -> list[Path]:
    """把本地快照逐个复制进 ``<repo>/snapshots/``，返回**内容有变**的那些文件。

    同名同内容不重写（避免 git 看见无意义改动）；本地快照变少时**不删**远端旧文件。
    """
    source_dir = snapshot_mod.growth_dir(config_dir) / snapshot_mod.SNAPSHOT_DIRNAME
    target_dir = Path(repo_dir) / SNAPSHOT_SUBDIR
    changed: list[Path] = []
    if not Path(source_dir).exists():
        return changed
    target_dir.mkdir(parents=True, exist_ok=True)
    for src in sorted(Path(source_dir).glob("*.json"), key=lambda p: p.name):
        dst = target_dir / src.name
        try:
            if dst.exists() and dst.read_bytes() == src.read_bytes():
                continue
            shutil.copyfile(src, dst)
            changed.append(dst)
        except OSError:
            logger.warning("快照复制失败: %s", src, exc_info=True)
    return changed


def push(repo_dir, period: str, *, runner=_run_git) -> dict:
    """add + commit + push 一轮；返回 ``{"status": ..., "detail": ...}``。

    ``status`` 取值：``pushed`` / ``nothing`` / ``committed`` / ``failed``
    （``committed`` = 本地提交成功但推送失败，下次会再试）。
    """
    repo = Path(repo_dir)
    target = repo / SNAPSHOT_SUBDIR
    code, out = runner(["add", "--", str(target)], repo)
    if code != 0:
        return {"status": "failed", "stage": "add", "detail": out}

    code, out = runner(["status", "--porcelain", "--", str(target)], repo)
    if code != 0:
        return {"status": "failed", "stage": "status", "detail": out}
    if not out.strip():
        return {"status": "nothing", "detail": "无改动"}

    code, out = runner(["commit", "-m", f"{COMMIT_PREFIX} {period}"], repo)
    if code != 0:
        return {"status": "failed", "stage": "commit", "detail": out}

    code, out = runner(["push"], repo)
    if code != 0:
        return {"status": "committed", "stage": "push", "detail": out}
    return {"status": "pushed", "detail": out}


def sync(config_dir, repo_dir, period: str | None = None, *, runner=_run_git) -> dict:
    """完整一次同步：复制快照 → 提交 → 推送。

    ``repo_dir`` 为空、不存在、不是 git 仓 → ``skipped``（不报错，只说明原因）。
    """
    if not repo_dir:
        return {"status": "skipped", "detail": "未配置 growth_snapshot_repo"}
    repo = Path(repo_dir)
    if not is_repo(repo):
        return {"status": "skipped", "detail": f"{repo} 不是 git 仓库"}
    changed = copy_snapshots(config_dir, repo)
    result = push(repo, period or _now_period(), runner=runner)
    result["copied"] = [p.name for p in changed]
    return result


def _now_period() -> str:
    return snapshot_mod.snapshot_period(datetime.now().astimezone())
