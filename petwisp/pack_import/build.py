# -*- coding: utf-8 -*-
"""把 :class:`PackSpec` 落成运行时可播的形象包（帧序列 + ``character.json``）。

**产物形态**（与素材包发布链完全一致，所以能被既有发布门校验）::

    <out>/<id>/<version>-<variant>-<W>x<H>/
    ├── character.json                # 包索引（petwisp.character_pack 契约）
    └── anim/<类别>/<动作名>/
        ├── f_0001.webp …             # 无损 WebP 帧
        └── meta.json                 # fps/encoder/content_sha/canvas…

**为什么是帧序列而不是 webm**：导入的目的是"一次转换，长期可播"。帧序列没有
ffmpeg 依赖、没有首跑转换等待，且 ``character.json`` 让包在没有源视频时也能
被采纳（``library`` 的无源路径）。反过来，视频来源不在这里转码——
:mod:`petwisp.pack_import.video` 直接产素材树，转码交给运行时自己的
``frameseq_provision``（那条链带白底反解，外部工具复现不了）。

**两处"看起来多余"的设计，都有原因**：

1. 缩放与落位按**整段动作的联合包围盒**算，不逐帧算 —— 逐帧算会让呼吸帧
   忽大忽小（每个格子内容高度不同），播起来抖。
2. 帧率重采样用"采样时刻命中源帧"，不按比例取整 —— 外部格式给的是逐帧毫秒
   （280/110/110/140/140/320 这种），按比例取整会把 110ms 的眨眼帧吞掉。
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from .spec import ActionPlan, ImportError_, PackSpec

#: 动作与帧之间留一点边，避免贴到画布边缘被裁。
EDGE_PAD = 8
#: 缩放上限：外部精灵表常是像素画，硬放大只会糊。
MAX_UPSCALE = 2.0
#: WebP 无损参数（与 frameseq_provision 的编码档同口径）。
WEBP_KWARGS = {"lossless": True, "exact": True, "method": 2}
KEY_EVERY_DEFAULT = 12


_CONST_PATTERN = re.compile(r'^(ENCODER_DESC|UNBLEND_MARK)\s*=\s*"([^"]*)"', re.M)


def _encoder_constants() -> tuple[str, str]:
    """取编码档常量，**必须与运行时同源**（不在这里抄一份副本）。

    优先真导入；``frameseq_provision`` 模块级就 ``import PySide6``，所以在没装 Qt
    的机器（容器/CI/纯命令行机）上会失败——此时退回**读源码文本抽常量**，
    保证值仍然只有一处定义，不会出现"导入层写的档位和运行时对不上、包被发布门拒"。
    """
    try:
        from ..frameseq_provision import ENCODER_DESC, UNBLEND_MARK

        return ENCODER_DESC, UNBLEND_MARK
    except Exception:                       # noqa: BLE001 - 缺 Qt 等任何原因都走兜底
        pass
    source = Path(__file__).resolve().parent.parent / "frameseq_provision.py"
    try:
        text = source.read_text(encoding="utf-8")
    except OSError as exc:
        raise ImportError_(f"读不到运行时编码档常量（{source}）：{exc}") from exc
    found = dict(_CONST_PATTERN.findall(text))
    missing = [key for key in ("ENCODER_DESC", "UNBLEND_MARK") if key not in found]
    if missing:
        raise ImportError_(f"frameseq_provision.py 里找不到 {', '.join(missing)}")
    return found["ENCODER_DESC"], found["UNBLEND_MARK"]


def timeline(durations_ms, fps: float) -> list[int]:
    """按目标帧率重采样：返回输出帧对应的**源帧下标**序列。

    输入是逐帧毫秒（外部格式都是"这一帧停多久"），输出必须是等间隔帧
    （运行时按固定 fps 播）。把源帧铺在时间轴上，用输出帧的采样时刻去命中：
    320ms 的一帧在 24fps 下被采样 7~8 次，110ms 的眨眼帧仍能采到 2~3 次。
    """
    if not durations_ms:
        return []
    frame_ms = 1000.0 / float(fps)
    total = float(sum(durations_ms))
    count = max(1, int(round(total / frame_ms)))
    edges: list[float] = []
    acc = 0.0
    for value in durations_ms:
        acc += float(value)
        edges.append(acc)
    out: list[int] = []
    last = len(edges) - 1
    for index in range(count):
        moment = min((index + 0.5) * frame_ms, edges[-1] - 1e-6)
        lo, hi = 0, last
        while lo < hi:
            mid = (lo + hi) // 2
            if edges[mid] > moment:
                hi = mid
            else:
                lo = mid + 1
        out.append(lo)
    return out


@dataclass(frozen=True)
class Placement:
    """一段动作的统一锚点：**全局缩放** + 联合包围盒（用于水平重心参考）。

    缩放必须整段统一，否则每帧按自身大小缩放到同一高度，呼吸帧会越长越高。
    但**贴底要逐帧做**（各用自身包围盒的底边贴 ``feet_y``）：蹲下、抬手的帧
    内容变矮是动作本身的意思，用联合框贴底反而会让它悬空。
    """

    scale: float
    crop: tuple[int, int, int, int]

    @property
    def center_x(self) -> float:
        return (self.crop[0] + self.crop[2]) / 2.0

    def target_size(self) -> tuple[int, int]:
        x0, y0, x1, y1 = self.crop
        return (max(1, int(round((x1 - x0) * self.scale))),
                max(1, int(round((y1 - y0) * self.scale))))

    def fit(self, cell) -> tuple[int, tuple[int, int]]:
        """按本帧自身包围盒算尺寸与水平偏移（偏移以联合框重心为基准）。"""
        box = cell.getchannel("A").getbbox() or self.crop
        width = max(1, int(round((box[2] - box[0]) * self.scale)))
        height = max(1, int(round((box[3] - box[1]) * self.scale)))
        own_center = (box[0] + box[2]) / 2.0
        offset = int(round((own_center - self.center_x) * self.scale))
        return height, (width, offset)


def _union_bbox(cells) -> tuple[int, int, int, int]:
    boxes = [cell.getchannel("A").getbbox() for cell in cells]
    boxes = [box for box in boxes if box]
    if not boxes:
        raise ImportError_("整段动作都是空白帧")
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


def plan_placement(cells, *, canvas: tuple[int, int], feet_y: int) -> Placement:
    """按整段动作的联合包围盒决定缩放：高度优先、贴脚底基线、水平居中。"""
    crop = _union_bbox(cells)
    box_w = max(1, crop[2] - crop[0])
    box_h = max(1, crop[3] - crop[1])
    scale = min((canvas[0] - EDGE_PAD * 2) / box_w, (feet_y - EDGE_PAD) / box_h, MAX_UPSCALE)
    return Placement(scale=scale, crop=crop)


def render_frame(cell, placement: Placement, *, canvas: tuple[int, int], feet_y: int):
    """把一格内容摆到空画布上：逐帧贴底（``feet_y``）、水平对准动作重心。"""
    from PIL import Image

    box = cell.getchannel("A").getbbox()
    if not box:
        raise ImportError_("遇到空白帧，无法落位")
    height, (width, offset) = placement.fit(cell)
    piece = cell.crop(box)
    if (width, height) != piece.size:
        # 用了 LANCZOS 而不是 NEAREST：像素画放大时 NEAREST 更"锐"，
        # 但这个导入链的输出来自 192×208 的图集、且会经 WebP 往返，
        # 保留 LANCZOS 的平滑更能掩盖一像素级的对齐误差。
        piece = piece.resize((width, height), Image.LANCZOS)
    frame = Image.new("RGBA", canvas, (0, 0, 0, 0))
    left = (canvas[0] - width) // 2 + offset
    top = feet_y - height
    frame.paste(piece, (left, top), piece)
    return frame


def strip_residue(frame):
    """``alpha == 0`` 的像素清 RGB（外部导出常见残影，规格判死但可修）。"""
    from PIL import Image

    alpha = frame.getchannel("A")
    extrema = alpha.getextrema()
    if extrema == (255, 255) or extrema[1] == 0:
        return frame
    keep = alpha.point(lambda value: 255 if value else 0)
    cleaned = Image.composite(frame.convert("RGB"), Image.new("RGB", frame.size, (0, 0, 0)), keep)
    result = cleaned.convert("RGBA")
    result.putalpha(alpha)
    return result


def clip_digest(frames: list[Path]) -> str:
    """与 ``scripts/build_character_pack.py`` 完全同口径的存储身份。"""
    digest = hashlib.sha256()
    for path in frames:
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_clip(image, action: ActionPlan, clip_root: Path, *, cell_wh: tuple[int, int],
                fps: float, canvas: tuple[int, int], feet_y: int,
                source_name: str, source_sha: str) -> dict:
    """写一段动作的帧 + meta，返回 ``character.json`` 用的 clip 条目。"""
    cell_w, cell_h = cell_wh
    cells = [image.crop((col * cell_w, row * cell_h, (col + 1) * cell_w, (row + 1) * cell_h))
             for row, col in action.cells]
    placement = plan_placement(cells, canvas=canvas, feet_y=feet_y)
    order = timeline(action.durations_ms, fps)

    clip_dir = clip_root / action.category / action.name
    clip_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for seq, src_index in enumerate(order, 1):
        frame = strip_residue(render_frame(cells[src_index], placement,
                                           canvas=canvas, feet_y=feet_y))
        path = clip_dir / f"f_{seq:04d}.webp"
        frame.save(path, "WEBP", **WEBP_KWARGS)
        written.append(path)

    encoder, unblend = _encoder_constants()
    content_sha = clip_digest(written)
    meta = {
        "fps": float(fps),
        "source": source_name,
        "frames": len(written),
        "encoder": encoder,
        "unblend": unblend,
        "source_sha256": source_sha,
        "content_sha": content_sha,
        "canvas": [int(canvas[0]), int(canvas[1])],
        "key_every": KEY_EVERY_DEFAULT,
    }
    (clip_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return {
        "name": action.name,
        "category": action.category,
        "dir": f"anim/{action.category}/{action.name}",
        "frames": len(written),
        "key_every": KEY_EVERY_DEFAULT,
        "part": action.part,
        "content_sha": content_sha,
        "bytes": sum(path.stat().st_size for path in written),
    }


def pack_dir_name(spec: PackSpec, *, version: str, variant: str = "base") -> str:
    return f"{version}-{variant}-{spec.canvas[0]}x{spec.canvas[1]}"


def build_pack(spec: PackSpec, out_root: Path | str, *, version: str,
               variant: str = "base", overwrite: bool = False,
               source_name: str = "") -> Path:
    """把 spec 落成形象包，返回包目录（``<id>/<version>-<variant>-<W>x<H>``）。

    目录是**不可变**语义（与素材包一致）：同路径已存在且未 ``overwrite`` 时拒绝，
    避免同一版本号覆盖出"同路径不同内容"的包。
    """
    if spec.image is None or spec.cell is None:
        raise ImportError_("这个来源不含精灵表（视频来源请用 pack_import.video）")
    if not spec.id:
        raise ImportError_("形象包缺 id")
    from .grid import check_geometry, open_image

    image, _ = open_image(spec.image)
    problems = check_geometry(spec, image.size)
    if problems:
        raise ImportError_("；".join(problems))

    out_root = Path(out_root)
    target = out_root / spec.id / pack_dir_name(spec, version=version, variant=variant)
    if target.exists():
        if not overwrite:
            raise ImportError_(
                f"目标已存在：{target}（同版本号不覆盖，请抬版本或加 --overwrite）")
        shutil.rmtree(target)
    anim_root = target / "anim"
    anim_root.mkdir(parents=True, exist_ok=True)

    source_sha = file_sha256(spec.image)
    source_name = source_name or spec.image.name
    clips: list[dict] = []
    for action in spec.actions:
        clips.append(_write_clip(image, action, anim_root, cell_wh=spec.cell,
                                 fps=spec.fps, canvas=spec.canvas, feet_y=spec.feet_y,
                                 source_name=source_name, source_sha=source_sha))

    index = {
        "schema": 1,
        "id": spec.id,
        "name": spec.name or spec.id,
        "canvas": [int(spec.canvas[0]), int(spec.canvas[1])],
        "fps": float(spec.fps),
        "pipeline": "unblend-white-v6",
        "format": "lossless-frame-v1",
        "parts": ["core", "extra"],
        "clips": clips,
        "license": spec.license,
        "description": spec.description,
        "author": spec.author,
        "source": spec.source,
        "importer": "petwisp.pack_import",
    }
    (target / "character.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
    return target


def describe(spec: PackSpec) -> list[str]:
    """给人看的摘要（CLI ``info`` 用）。"""
    lines = [
        f"id: {spec.id or '(未声明)'}",
        f"名称: {spec.name or '(未声明)'}",
        f"来源: {spec.source or '(未声明)'}",
        f"帧率: {spec.fps:g}   画布: {spec.canvas[0]}×{spec.canvas[1]}   脚底基线: {spec.feet_y}",
    ]
    if spec.cell:
        lines.append(
            f"网格: 单格 {spec.cell[0]}×{spec.cell[1]}，{spec.columns} 列 × {spec.rows} 行")
    for action in spec.actions:
        lines.append(
            f"  [{action.part}] {action.category:<7} {action.name:<16} {len(action.cells):>3} 帧"
            + (f"  ({action.note})" if action.note else ""))
    gaps = spec.core_gaps()
    lines.append("core 闭包: " + ("完整" if not gaps else "缺 " + "、".join(gaps)))
    return lines
