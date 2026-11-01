# -*- coding: utf-8 -*-
"""Petdex 包适配：``pet.json`` + 横向网格 ``spritesheet`` → :class:`PackSpec`。

**外部规格来源**（照抄，不猜）：Petdex / Codex 桌宠图集是固定几何的网格图，
``pet.json`` 只声明身份与图集版本，**行 → 状态**的对应关系写死在渲染端。

======  ======================  ====  ==========================
行       状态                    帧数  逐帧时长（毫秒）
======  ======================  ====  ==========================
0        idle                    6    280,110,110,140,140,320
1        running-right           8    120 ×7, 末帧 220
2        running-left            8    120 ×7, 末帧 220
3        waving                  4    140 ×3, 末帧 280
4        jumping                 5    140 ×4, 末帧 280
5        failed                  8    140 ×7, 末帧 240
6        waiting                 6    150 ×5, 末帧 260
7        running                 6    120 ×5, 末帧 220
8        review                  6    150 ×5, 末帧 280
9–10     16 个注视方向（仅 v2）  16    每帧 120
======  ======================  ====  ==========================

几何：v2 = 1536×2288 / 8 列 × 11 行 / 单格 192×208；v1 = 1536×1872 / 8 列 × 9 行，
单格同为 192×208。**尺寸必须精确**（渲染端按固定行列算 ``background-position``），
所以这里逐条硬校验，不符直接拒收，不做"差不多就缩放"。

语义映射到 PetWisp 的五个日常动作（``core``）是有损的，逐条说明理由：

=====================  ==========  =====================================
Petdex 状态            这里归到     理由
=====================  ==========  =====================================
idle                   idle        语义一致（平静呼吸/眨眼）
running-right/left     move        方向性步态，正好对上左右移动两段
waving                 click       鼠标点击的回应动作，挥手是最接近的
jumping                drag        被拎起来时的纵向弹跳，最接近的现成动作
16 注视方向            turn        转头/东张西望，直接用方向序列
其余（failed 等）      random      「随机小动作」池，语义宽松
=====================  ==========  =====================================

**没有 turn 的 v1 包**：退回 ``idle`` 帧会让"转向"看上去静止，属于伪造动作，
所以默认判为缺口（:meth:`PackSpec.core_gaps` 会报出来）；确实需要时由用户显式
打开 ``allow_missing_turn``，此时才用 running 的左右两段拼接做近似。
"""

from __future__ import annotations

import json
from pathlib import Path

from .spec import ActionPlan, ImportError_, PackSpec

PET_JSON = "pet.json"

#: 图集版本 → (画布宽, 画布高, 列数, 行数, 单格宽, 单格高)。
ATLAS_LAYOUTS: dict[int, tuple[int, int, int, int, int, int]] = {
    1: (1536, 1872, 8, 9, 192, 208),
    2: (1536, 2288, 8, 11, 192, 208),
}

#: 行 → (Petdex 状态名, 帧数, 逐帧时长)。
_ROW_SPECS: tuple[tuple[str, int, tuple[int, ...]], ...] = (
    ("idle", 6, (280, 110, 110, 140, 140, 320)),
    ("running-right", 8, (120,) * 7 + (220,)),
    ("running-left", 8, (120,) * 7 + (220,)),
    ("waving", 4, (140, 140, 140, 280)),
    ("jumping", 5, (140, 140, 140, 140, 280)),
    ("failed", 8, (140,) * 7 + (240,)),
    ("waiting", 6, (150,) * 5 + (260,)),
    ("running", 6, (120,) * 5 + (220,)),
    ("review", 6, (150,) * 5 + (280,)),
)

#: Petdex 状态 → (PetWisp 动作名, 动作类)。映射理由见模块文档表格。
_STATE_TO_ACTION: dict[str, tuple[str, str]] = {
    "idle": ("idle_loop", "idle"),
    "running-right": ("move_right", "move"),
    "running-left": ("move_left", "move"),
    "waving": ("click_wave", "click"),
    "jumping": ("drag_hop", "drag"),
    "failed": ("idle_fail", "random"),
    "waiting": ("idle_wait", "random"),
    "running": ("idle_busy", "random"),
    "review": ("idle_review", "random"),
}

#: 16 个注视方向（v2 的 9、10 行）拼成一段转向动画，每帧时长。
GAZE_FRAME_MS = 120


def read_pet_json(pet_dir: Path | str) -> dict:
    """读 ``pet.json``；缺字段按规格判死（不猜默认值）。"""
    pet_dir = Path(pet_dir)
    path = pet_dir / PET_JSON
    if not path.is_file():
        raise ImportError_(f"不是 Petdex 包：缺 {PET_JSON}（{pet_dir}）")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ImportError_(f"{PET_JSON} 不是合法 JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ImportError_(f"{PET_JSON} 顶层必须是对象")
    for key in ("id", "displayName", "spritesheetPath"):
        if not str(data.get(key) or "").strip():
            raise ImportError_(f"{PET_JSON} 缺必填字段 {key}")
    version = data.get("spriteVersionNumber")
    if isinstance(version, bool) or not isinstance(version, int):
        raise ImportError_(
            "spriteVersionNumber 缺失或不是数字（字符串 \"2\" 也不收）："
            f"{version!r}——旧版图集请先升级")
    if version not in ATLAS_LAYOUTS:
        raise ImportError_(f"不支持的图集版本 {version}（支持 {sorted(ATLAS_LAYOUTS)}）")
    return data


def _open_atlas(path: Path):
    from PIL import Image

    try:
        image = Image.open(path)
        image.load()
    except OSError as exc:
        raise ImportError_(f"图集读不开: {exc}") from exc
    if image.mode not in ("RGBA", "LA", "P"):
        raise ImportError_(f"图集必须带 alpha 通道，当前模式 {image.mode}")
    return image.convert("RGBA")


def check_atlas(image, layout: tuple[int, int, int, int, int, int], *,
                allow_residue: bool = False) -> list[str]:
    """几何 + 透明残影 + 空帧检查，返回问题列表（空 = 通过）。

    "透明残影"指 ``alpha == 0`` 但 RGB 非零的像素——外部工具导出常见，
    叠在任何背景上都会渗出色边，所以按规格判死。``allow_residue`` 留给
    "我知道有残影、落盘时统一清零"的场景（:mod:`build` 会做）。
    """
    width, height, columns, rows, cell_w, cell_h = layout
    problems: list[str] = []
    if image.size != (width, height):
        problems.append(f"画布尺寸不符：{image.size[0]}×{image.size[1]}，要求 {width}×{height}")
        return problems

    expected_rows = len(_ROW_SPECS) + (2 if rows > len(_ROW_SPECS) else 0)
    if rows != expected_rows:
        problems.append(f"行数不符：{rows}，要求 {expected_rows}")

    for row, (state, frames, _) in enumerate(_ROW_SPECS):
        for col in range(columns):
            box = (col * cell_w, row * cell_h, (col + 1) * cell_w, (row + 1) * cell_h)
            cell = image.crop(box)
            used = col < frames
            empty = cell.getchannel("A").getbbox() is None
            if used and empty:
                problems.append(f"第 {row} 行（{state}）第 {col} 列为空，但该列应有帧")
            elif not used and not empty:
                problems.append(f"第 {row} 行（{state}）第 {col} 列应为空格，却有内容")
            if used and not allow_residue and _has_residue(cell):
                problems.append(f"第 {row} 行（{state}）第 {col} 列透明区残留 RGB")

    if rows > len(_ROW_SPECS):
        for row in (len(_ROW_SPECS), len(_ROW_SPECS) + 1):
            for col in range(columns):
                box = (col * cell_w, row * cell_h, (col + 1) * cell_w, (row + 1) * cell_h)
                if image.crop(box).getchannel("A").getbbox() is None:
                    problems.append(f"第 {row} 行第 {col} 列注视方向帧为空")
    return problems


def _has_residue(cell) -> bool:
    """``alpha == 0`` 的像素里是否残留非零 RGB。"""
    alpha = cell.getchannel("A")
    if alpha.getbbox() is not None and alpha.getextrema() == (255, 255):
        return False          # 全不透明：没有透明区，谈不上残影
    rgb = cell.convert("RGB")
    a = alpha.tobytes()
    px = rgb.tobytes()
    for i, value in enumerate(a):
        if value == 0 and (px[i * 3] or px[i * 3 + 1] or px[i * 3 + 2]):
            return True
    return False


def spec_from_petdex(pet_dir: Path | str, *, allow_missing_turn: bool = False,
                     allow_residue: bool = False, check: bool = True) -> PackSpec:
    """把 Petdex 包读成 :class:`PackSpec`（只读源，不写盘）。"""
    pet_dir = Path(pet_dir)
    meta = read_pet_json(pet_dir)
    version = int(meta["spriteVersionNumber"])
    layout = ATLAS_LAYOUTS[version]
    image_path = pet_dir / str(meta["spritesheetPath"])
    if not image_path.is_file():
        raise ImportError_(f"图集文件不存在: {image_path}")

    image = _open_atlas(image_path)
    problems = check_atlas(image, layout, allow_residue=allow_residue)
    if problems and check:
        head = "；".join(problems[:6])
        more = f"（另有 {len(problems) - 6} 条）" if len(problems) > 6 else ""
        raise ImportError_(f"图集不合规：{head}{more}")

    rows = layout[3]
    actions: list[ActionPlan] = []
    for row, (state, frames, durations) in enumerate(_ROW_SPECS):
        name, category = _STATE_TO_ACTION[state]
        actions.append(ActionPlan(
            name=name,
            category=category,
            cells=tuple((row, col) for col in range(frames)),
            durations_ms=tuple(float(d) for d in durations),
            part="core" if category in ("idle", "move", "click", "drag", "turn") else "extra",
            note=f"Petdex {state}",
        ))

    if version >= 2:
        actions.append(ActionPlan(
            name="turn_gaze",
            category="turn",
            cells=tuple((row, col) for row in (9, 10) for col in range(8)),
            durations_ms=tuple(float(GAZE_FRAME_MS) for _ in range(16)),
            part="core",
            note="Petdex 16 方向注视",
        ))
    elif allow_missing_turn:
        # 显式授权才做的近似：左右移动各取 4 帧交替，构成"原地张望"。
        actions.append(ActionPlan(
            name="turn_look",
            category="turn",
            cells=((1, 0), (1, 1), (2, 0), (2, 1)),
            durations_ms=(160.0, 160.0, 160.0, 240.0),
            part="core",
            note="v1 无注视行，用左右移动帧近似（用户显式授权）",
        ))

    cell_w, cell_h = layout[4], layout[5]
    return PackSpec(
        id=str(meta["id"]).strip(),
        name=str(meta.get("displayName") or meta["id"]).strip(),
        actions=tuple(actions),
        image=image_path,
        cell=(cell_w, cell_h),
        columns=layout[2],
        rows=rows,
        fps=float(meta.get("fps") or 24.0),
        license=str(meta.get("license") or ""),
        description=str(meta.get("description") or ""),
        author=str(meta.get("author") or ""),
        source=f"petdex:{meta['id']}",
        extras={"sprite_version": version},
    )
