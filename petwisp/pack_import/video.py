# -*- coding: utf-8 -*-
"""视频来源适配：``.webm`` / ``.gif`` 素材树 → PetWisp 原生素材树（不转码）。

**为什么这里不转帧序列**：运行时自己的 ``frameseq_provision`` 转码链带
"白底反解"（把白底合成的视频反解成直通 alpha）和缓存世代身份，外部工具
很难复现同一档位；而**转错档位的包会被发布门直接拒**（``ENCODER_DESC`` 不符）。
所以导入层只做它确定能做好的一半：**布局规范化 + 文件校验 + 索引生成**，
把帧序列留给运行时那条已经验证过的链首次运行时生成。

产物::

    <out>/<id>/
    ├── character.json        # 只有 videos/ 时也可省，但写上更利于"一个包一个索引"
    ├── manifest.json         # 素材清单（docs/CHARACTER-PACK.md 的传统布局）
    └── videos/<类别>/<名字>.webm

类别从**相对路径首段**取（``walk/x.webm`` → ``walk``）；文件名/路径都看不出类别
时要求显式 ``--as <类别>``——猜错类别的后果是"存了但永远播不到"，比报错更糟。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from .spec import CORE_CATEGORIES, KNOWN_CATEGORIES, ImportError_

VIDEO_SUFFIXES = (".webm", ".gif")
_EBML_MAGIC = b"\x1a\x45\xdf\xa3"
_GIF_MAGIC = b"GIF8"

#: 文件名关键词 → 类别（只做**明确的**映射，模糊的宁可不猜）。
_KEYWORDS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("idle", "stand", "breath", "rest", "待机", "呼吸"), "idle"),
    (("walk", "move", "run", "步", "走", "移动"), "move"),
    (("turn", "look", "gaze", "转头", "看"), "turn"),
    (("click", "tap", "poke", "wave", "点", "摸", "戳"), "click"),
    (("drag", "lift", "pick", "拖", "拎"), "drag"),
    (("random", "idle_extra", "misc"), "random"),
)


def guess_category(name: str) -> str | None:
    """从文件名猜类别（猜不到返回 ``None``，不抛）。"""
    lowered = name.lower()
    for keys, category in _KEYWORDS:
        if any(key in lowered for key in keys):
            return category
    return None


def probe_videos(root: Path | str) -> list[tuple[Path, str | None, str]]:
    """扫目录，返回 ``[(路径, 猜到的类别, 动作名)]``（按路径排序，结果稳定）。"""
    root = Path(root)
    if not root.is_dir():
        raise ImportError_(f"不是目录: {root}")
    found: list[tuple[Path, str | None, str]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in VIDEO_SUFFIXES:
            continue
        rel = path.relative_to(root)
        head = rel.parts[0].lower() if len(rel.parts) > 1 else ""
        category = head if head in KNOWN_CATEGORIES else guess_category(rel.stem)
        found.append((path, category, rel.stem))
    return found


def check_video(path: Path) -> list[str]:
    """校验容器头（坏文件/改了扩展名的伪视频在导入时就挡住）。"""
    with path.open("rb") as handle:
        head = handle.read(16)
    if not head:
        return ["空文件"]
    if path.suffix.lower() == ".webm":
        return [] if head.startswith(_EBML_MAGIC) else ["不是 WebM/EBML 容器（扩展名与实际不符？）"]
    if path.suffix.lower() == ".gif":
        return [] if head.startswith(_GIF_MAGIC) else ["不是 GIF（扩展名与实际不符？）"]
    return [f"不支持的格式 {path.suffix}"]


def import_videos(root: Path | str, out_root: Path | str, *, pack_id: str,
                  name: str = "", part: str = "base", as_category: str = "",
                  fps: float = 24.0, canvas: tuple[int, int] = (640, 360),
                  overwrite: bool = False, copy: bool = True) -> Path:
    """把视频素材树规整成 ``<out>/<id>/videos/<类别>/<名字>.*`` 并写索引。

    返回包目录（``<out>/<id>``）。缺 ``core`` 类别时**不阻断**（用户可能分多次
    导入不同动作），但会在返回的 manifest 里记下缺口，CLI 会打出来。
    """
    if not pack_id:
        raise ImportError_("形象包缺 id")
    entries = probe_videos(root)
    if not entries:
        raise ImportError_(f"目录里没有 .webm/.gif：{root}")

    unresolved = [path.name for path, category, _ in entries if not category]
    if unresolved:
        hint = f"（--as {as_category} 可强制指定）" if as_category else "（或用 --as 指定）"
        raise ImportError_(
            "这些文件推不出动作类别，请按 <类别>/ 分子目录" + hint + "："
            + "、".join(unresolved[:8]))

    target = Path(out_root) / pack_id
    videos_root = target / "videos"
    if target.exists() and not overwrite:
        # 允许"往已有包里补动作"：只要不覆盖同名文件就继续。
        pass
    videos_root.mkdir(parents=True, exist_ok=True)

    clips: list[dict] = []
    for path, category, stem in entries:
        problems = check_video(path)
        if problems:
            raise ImportError_(f"{path.name}: {'；'.join(problems)}")
        dest_dir = videos_root / category
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{stem}{path.suffix.lower()}"
        if dest.exists():
            if not overwrite:
                raise ImportError_(f"目标已存在：{dest}（加 --overwrite 覆盖）")
            dest.unlink()
        if copy:
            shutil.copy2(path, dest)
        else:
            shutil.move(str(path), str(dest))
        clips.append({
            "name": stem,
            "category": category,
            "dir": f"videos/{category}",
            "file": dest.name,
            "frames": 0,          # 0 = 未生成帧序列，运行时首跑会转换
            "part": "core" if category in CORE_CATEGORIES else part,
            "bytes": dest.stat().st_size,
        })

    manifest = {
        "schema": 1,
        "id": pack_id,
        "name": name or pack_id,
        "canvas": [int(canvas[0]), int(canvas[1])],
        "fps": float(fps),
        "format": "video-source",
        "clips": clips,
        "importer": "petwisp.pack_import.video",
    }
    (target / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")

    present = {clip["category"] for clip in clips}
    gaps = [c for c in CORE_CATEGORIES if c not in present]
    if not (target / "character.json").exists() and not gaps:
        index = {
            "schema": 1,
            "id": pack_id,
            "name": name or pack_id,
            "canvas": [int(canvas[0]), int(canvas[1])],
            "fps": float(fps),
            "pipeline": "unblend-white-v6",
            "format": "video-source",
            "parts": ["core", "extra"],
            "clips": clips,
            "importer": "petwisp.pack_import.video",
        }
        (target / "character.json").write_text(
            json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
    return target
