# -*- coding: utf-8 -*-
"""通用精灵表校验：一张网格图 + ``petwisp-pack.json`` 描述。

与 :mod:`petdex` 的区别只在**描述从哪来**：Petdex 把行→状态写死在渲染端，
通用精灵表由创作者自己在 ``petwisp-pack.json`` 里声明（:func:`spec.spec_from_dict`）。
所以本模块只做两件事：把描述与实际图片对上、把"能装不能看"的坑挡在落盘之前。
"""

from __future__ import annotations

from pathlib import Path

from .spec import ImportError_, PackSpec, load_spec

SPEC_NAME = "petwisp-pack.json"


def find_spec_file(root: Path | str) -> Path | None:
    """在目录里找描述文件（找不到返回 ``None``，由调用方决定是否给模板）。"""
    root = Path(root)
    candidate = root / SPEC_NAME
    return candidate if candidate.is_file() else None


def open_image(path: Path | str, *, allow_residue: bool = False):
    """打开精灵表并做几何/透明残影前置检查，返回 ``(image, problems)``。"""
    from PIL import Image

    path = Path(path)
    if not path.is_file():
        raise ImportError_(f"精灵表不存在: {path}")
    try:
        image = Image.open(path)
        image.load()
    except OSError as exc:
        raise ImportError_(f"精灵表读不开: {exc}") from exc
    if image.mode not in ("RGBA", "LA", "P"):
        raise ImportError_(f"精灵表必须带 alpha 通道，当前模式 {image.mode}")
    return image.convert("RGBA"), ()


def check_geometry(spec: PackSpec, size: tuple[int, int]) -> list[str]:
    """图片尺寸是否正好铺满 ``columns × rows`` 个格子。"""
    if spec.cell is None:
        return ["描述缺 grid 段"]
    expect_w = spec.columns * spec.cell[0]
    expect_h = spec.rows * spec.cell[1]
    if size != (expect_w, expect_h):
        return [f"精灵表尺寸 {size[0]}×{size[1]} 与网格不符（应 {expect_w}×{expect_h}）"]
    return []


def check_cells(spec: PackSpec, image) -> list[str]:
    """逐格检查：声明的格子必须非空，越界引用直接判死。"""
    problems: list[str] = []
    if spec.cell is None:
        return ["描述缺 grid 段"]
    cell_w, cell_h = spec.cell
    for action in spec.actions:
        for index, (row, col) in enumerate(action.cells):
            if not (0 <= row < spec.rows and 0 <= col < spec.columns):
                problems.append(f"{action.name} 第 {index} 帧越界：({row},{col})")
                continue
            box = (col * cell_w, row * cell_h, (col + 1) * cell_w, (row + 1) * cell_h)
            if image.crop(box).getchannel("A").getbbox() is None:
                problems.append(f"{action.name} 第 {index} 帧为空（{row},{col}）")
    return problems


def check_pack(root: Path | str, *, strict: bool = True) -> tuple[PackSpec, list[str]]:
    """读描述 + 校验图片，返回 ``(spec, problems)``。

    ``strict=True`` 时有任何问题即抛；否则原样返回问题清单（供 CLI 打印）。
    """
    root = Path(root)
    spec = load_spec(root)
    image, _ = open_image(spec.image) if spec.image else (None, ())
    if image is None:
        raise ImportError_("描述缺 image 字段（精灵表路径）")
    problems = check_geometry(spec, image.size) + check_cells(spec, image)
    if problems and strict:
        head = "；".join(problems[:6])
        more = f"（另有 {len(problems) - 6} 条）" if len(problems) > 6 else ""
        raise ImportError_(f"精灵表不合规：{head}{more}")
    return spec, problems


def template(*, image: str = "spritesheet.webp", cell: tuple[int, int] = (192, 208),
             columns: int = 8, rows: int = 11) -> dict:
    """给创作者起步用的描述模板（``shell new`` 会写盘）。"""
    return {
        "schema": 1,
        "id": "my-pet",
        "name": "我的形象",
        "image": image,
        "grid": {"cell_w": cell[0], "cell_h": cell[1], "columns": columns, "rows": rows},
        "fps": 24,
        "license": "CC0-1.0",
        "actions": [
            {"name": "idle_loop", "category": "idle", "row": 0, "frames": 4,
             "frame_ms": 120},
            {"name": "move_walk", "category": "move", "row": 1, "frames": 4,
             "frame_ms": 120},
            {"name": "turn_peek", "category": "turn", "row": 2, "frames": 2,
             "frame_ms": 160},
            {"name": "click_happy", "category": "click", "row": 3, "frames": 4,
             "frame_ms": 120},
            {"name": "drag_wobble", "category": "drag", "row": 4, "frames": 2,
             "frame_ms": 140},
        ],
    }
