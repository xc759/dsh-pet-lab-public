# -*- coding: utf-8 -*-
"""导入层的公共契约：动作类别、几何常量、``petwisp-pack.json`` 描述。

**为什么单独一份规格**：三个适配器（webm / petdex / 精灵表）差异全在"怎么读源"，
读完之后都是同一件事——一张 **帧表**（动作 → 一串格子与每格时长）。把这件事
钉在 :class:`ActionPlan` 上，适配器就只需要产出它，落盘（:mod:`build`）只有一份
实现，校验也只有一份口径。

**与运行时的关系**：这里的常量必须与运行时**同源**，不许各自抄一份——
- :data:`CORE_CATEGORIES` 直接用 :data:`petwisp.character_pack.CORE_REQUIRED_CATEGORIES`；
- :data:`CANVAS` / :data:`FEET_Y` 必须等于运行时素材画布（640×360、脚底 y=330），
  否则导入的包"能装不能看"：角色会被缩放到错误比例。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..character_pack import CORE_REQUIRED_CATEGORIES

#: 运行时素材画布与脚底基线（见 docs/CHARACTER-PACK.md §3）。
CANVAS: tuple[int, int] = (640, 360)
FEET_Y: int = 330
#: 目标帧率。Petdex 给的是**逐帧毫秒**、精灵表描述可能给单帧时长，
#: 统一重采样到这个帧率后再落盘（一个包必须同帧率，发布门会拒混帧率包）。
DEFAULT_FPS: float = 24.0

#: 必须成套出现的日常动作类（缺一类 → ``core`` 闭包不成立 → 不能激活）。
CORE_CATEGORIES: tuple[str, ...] = tuple(CORE_REQUIRED_CATEGORIES)
#: 允许出现在包里的全部动作类。写错的类别直接拒，避免"存了但播不到"。
KNOWN_CATEGORIES: tuple[str, ...] = CORE_CATEGORIES + ("random", "events")

#: 描述文件与索引文件名。
SPEC_NAME = "petwisp-pack.json"
SPEC_SCHEMA = 1


class ImportError_(ValueError):
    """导入失败（源不合规、几何不符、缺必需动作）。与内置 ``ImportError`` 无关。"""


@dataclass(frozen=True)
class ActionPlan:
    """一段待落盘动画。

    ``cells`` 是 ``(row, col)`` 序列（精灵表坐标）；对视频来源是 ``None``，
    因为视频走的是运行时自己的抽帧链（见 :mod:`video`）。
    ``durations_ms`` 逐帧时长，长度必须等于 ``len(cells)``。
    """

    name: str
    category: str
    cells: tuple[tuple[int, int], ...] = ()
    durations_ms: tuple[float, ...] = ()
    part: str = "core"
    note: str = ""

    def __post_init__(self) -> None:
        if self.category not in KNOWN_CATEGORIES:
            raise ImportError_(
                f"未知动作类 {self.category!r}（允许：{', '.join(KNOWN_CATEGORIES)}）")
        if self.part not in ("core", "extra"):
            raise ImportError_(f"分件名必须是 core/extra，收到 {self.part!r}")
        if not self.name or any(ch in self.name for ch in '\\/:*?"<>|'):
            raise ImportError_(f"动作名非法（不能为空或含路径字符）：{self.name!r}")
        if self.cells and len(self.cells) != len(self.durations_ms):
            raise ImportError_(
                f"{self.name}: 帧数({len(self.cells)}) 与时长数({len(self.durations_ms)}) 不一致")
        if not self.cells:
            raise ImportError_(f"{self.name}: 没有帧")


@dataclass(frozen=True)
class PackSpec:
    """一次导入的全部输入：身份信息 + 精灵表几何 + 动作表。"""

    id: str
    name: str
    actions: tuple[ActionPlan, ...]
    image: Path | None = None
    cell: tuple[int, int] | None = None
    columns: int = 0
    rows: int = 0
    fps: float = DEFAULT_FPS
    canvas: tuple[int, int] = CANVAS
    feet_y: int = FEET_Y
    license: str = ""
    description: str = ""
    author: str = ""
    source: str = ""
    extras: dict = field(default_factory=dict)

    def by_category(self) -> dict[str, list[ActionPlan]]:
        out: dict[str, list[ActionPlan]] = {}
        for action in self.actions:
            out.setdefault(action.category, []).append(action)
        return out

    def core_gaps(self) -> list[str]:
        """``core`` 闭包里缺的动作类（空 = 可激活）。"""
        present = {a.category for a in self.actions if a.part == "core"}
        return [c for c in CORE_CATEGORIES if c not in present]

    def total_frames(self) -> int:
        return sum(len(a.cells) for a in self.actions)


def _as_pos_int(value, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ImportError_(f"{field_name} 必须是正整数，收到 {value!r}")
    return value


def spec_from_dict(data, *, base_dir: Path | None = None) -> PackSpec:
    """解析 ``petwisp-pack.json``（通用精灵表来源）。

    字段：``id`` / ``name`` / ``image`` / ``grid{cell_w,cell_h,columns,rows}`` /
    ``fps`` / ``actions[{name,category,row,frames,durations,part}]``。
    """
    if not isinstance(data, dict):
        raise ImportError_("描述文件顶层必须是对象")
    schema = data.get("schema", SPEC_SCHEMA)
    if schema != SPEC_SCHEMA:
        raise ImportError_(f"不支持的描述 schema: {schema!r}（本版支持 {SPEC_SCHEMA}）")

    grid = data.get("grid")
    if not isinstance(grid, dict):
        raise ImportError_("缺 grid 段（cell_w / cell_h / columns / rows）")
    cell_w = _as_pos_int(grid.get("cell_w"), "grid.cell_w")
    cell_h = _as_pos_int(grid.get("cell_h"), "grid.cell_h")
    columns = _as_pos_int(grid.get("columns"), "grid.columns")
    rows = _as_pos_int(grid.get("rows"), "grid.rows")

    raw_actions = data.get("actions")
    if not isinstance(raw_actions, (list, tuple)) or not raw_actions:
        raise ImportError_("actions 必须是非空数组")

    actions: list[ActionPlan] = []
    seen: set[str] = set()
    for i, raw in enumerate(raw_actions):
        if not isinstance(raw, dict):
            raise ImportError_(f"actions[{i}] 必须是对象")
        name = str(raw.get("name") or "").strip()
        category = str(raw.get("category") or "").strip().lower()
        row = raw.get("row")
        if isinstance(row, bool) or not isinstance(row, int) or row < 0 or row >= rows:
            raise ImportError_(f"actions[{i}].row 越界: {row!r}（行数 {rows}）")
        frames = _as_pos_int(raw.get("frames"), f"actions[{i}].frames")
        col0 = raw.get("col0", 0)
        if isinstance(col0, bool) or not isinstance(col0, int) or col0 < 0:
            raise ImportError_(f"actions[{i}].col0 非法: {col0!r}")
        if col0 + frames > columns:
            raise ImportError_(
                f"actions[{i}] 超出图集宽度：col0={col0} + frames={frames} > columns={columns}")
        durations = raw.get("durations")
        if durations is None:
            per = raw.get("frame_ms")
            per = 1000.0 / DEFAULT_FPS if per is None else per
            if isinstance(per, bool) or not isinstance(per, (int, float)) or per <= 0:
                raise ImportError_(f"actions[{i}].frame_ms 必须是正数: {per!r}")
            durations = [float(per)] * frames
        if (not isinstance(durations, (list, tuple)) or len(durations) != frames
                or any(isinstance(d, bool) or not isinstance(d, (int, float)) or d <= 0
                       for d in durations)):
            raise ImportError_(
                f"actions[{i}].durations 必须是 {frames} 个正数（长度不符或含非法值）")
        if not name:
            raise ImportError_(f"actions[{i}].name 不能为空")
        if name in seen:
            raise ImportError_(f"动作名重复: {name}")
        seen.add(name)
        part = "core" if category in CORE_CATEGORIES else str(raw.get("part") or "extra")
        actions.append(ActionPlan(
            name=name,
            category=category,
            cells=tuple((row, col0 + k) for k in range(frames)),
            durations_ms=tuple(float(d) for d in durations),
            part=part,
            note=str(raw.get("note") or ""),
        ))

    canvas = data.get("canvas") or list(CANVAS)
    if (not isinstance(canvas, (list, tuple)) or len(canvas) != 2
            or any(isinstance(v, bool) or not isinstance(v, int) or v <= 0 for v in canvas)):
        raise ImportError_(f"canvas 必须是两个正整数: {canvas!r}")
    fps = data.get("fps", DEFAULT_FPS)
    if isinstance(fps, bool) or not isinstance(fps, (int, float)) or fps <= 0:
        raise ImportError_(f"fps 必须是正数: {fps!r}")

    image = data.get("image")
    image_path = None
    if image:
        image_path = Path(image)
        if not image_path.is_absolute() and base_dir is not None:
            image_path = (Path(base_dir) / image_path).resolve()

    return PackSpec(
        id=str(data.get("id") or "").strip(),
        name=str(data.get("name") or data.get("id") or "").strip(),
        actions=tuple(actions),
        image=image_path,
        cell=(cell_w, cell_h),
        columns=columns,
        rows=rows,
        fps=float(fps),
        canvas=(int(canvas[0]), int(canvas[1])),
        feet_y=int(data.get("feet_y") or FEET_Y),
        license=str(data.get("license") or ""),
        description=str(data.get("description") or ""),
        author=str(data.get("author") or ""),
        source=str(data.get("source") or ""),
        extras={k: v for k, v in data.items()
                if k not in {"schema", "id", "name", "image", "grid", "fps", "actions",
                             "canvas", "feet_y", "license", "description", "author", "source"}},
    )


def load_spec(path: Path | str) -> PackSpec:
    """读一份 ``petwisp-pack.json``（路径可直接给目录）。"""
    import json

    path = Path(path)
    if path.is_dir():
        path = path / SPEC_NAME
    if not path.is_file():
        raise ImportError_(f"描述文件不存在: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ImportError_(f"描述文件不是合法 JSON: {exc}") from exc
    return spec_from_dict(data, base_dir=path.parent)
