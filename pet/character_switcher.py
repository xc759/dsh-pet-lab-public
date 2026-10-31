# -*- coding: utf-8 -*-
"""多形象一键切换：角色索引、首帧缩略图、循环切换与定时轮番。

本模块只做三件上游没有的事，其余一律复用既有入口：

1. **角色索引**：把内置目录（``assets/characters``、``assets/characters_gif``）与
   外部角色目录（``catalog.external_character_dirs()``）合并成一份"角色 id → 目录"，
   供缩略图与轮番使用。列表本身仍以 ``catalog.list_available_characters()`` 为准，
   本模块不新增第二套发现规则。
2. **首帧缩略图**：复用 ``animation_thumbnail.decode_representative_frame``（已带
   内存/磁盘缓存与解码限流），不重复实现解码。
3. **切换动线**：``CharacterStrip``（右键菜单里的缩略图行）、``cycle_character``
   （热键与轮番共用的"下一个"）、``CharacterRotation``（定时轮番）。

纪律：切换**只**走 ``shell.switch_character(cid, sprite)`` —— 换库、持久化、
聊天面同步、缩略图缓存作废都在那一条路径里收口，这里不碰素材与配置写盘。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Iterable, Optional

from PySide6.QtCore import QObject, QSize, Qt, QTimer
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout, QMenu, QToolButton, QWidget, QWidgetAction,
)

from . import catalog
from .animation_thumbnail import decode_representative_frame

logger = logging.getLogger(__name__)

#: 轮番配置键（落在主配置 config.json；缺省关闭 = 行为与加这个功能之前一致）。
#: 用两个标量键而不是一个 dict：Config 对未知 dict 型键会回落默认值，标量才能读回。
ROTATION_ENABLED_KEY = "character_rotation_enabled"
ROTATION_INTERVAL_KEY = "character_rotation_interval_min"
ROTATION_DEFAULT_INTERVAL = 30

#: 缩略图边长（逻辑像素）：菜单里一行 3~5 个角色也不显拥挤
_THUMB_SIDE = 64

#: 角色目录下可当"代表帧"的动画扩展名
_VIDEO_SUFFIXES = (".webm", ".gif")


# ---------------------------------------------------------------- 角色索引
def character_roots() -> dict[str, Path]:
    """角色 id → 角色包目录（内置优先，外部目录只补新 id）。"""
    roots: dict[str, Path] = {}
    bases: list[Path] = [catalog.characters_dir(), catalog.characters_gif_dir()]
    try:
        bases.extend(catalog.external_character_dirs())
    except Exception:
        logger.debug("枚举外部角色目录失败", exc_info=True)
    for base in bases:
        try:
            if not base.is_dir():
                continue
            children = sorted(base.iterdir())
        except OSError:
            continue
        for child in children:
            if child.is_dir() and not child.name.startswith(".") and child.name not in roots:
                roots[child.name] = child
    return roots


def available_characters() -> list[str]:
    """可切换角色（以 catalog 为准，并补上只在磁盘上存在的目录）。"""
    ids: list[str] = []
    try:
        ids.extend(catalog.list_available_characters())
    except Exception:
        logger.debug("catalog 角色枚举失败", exc_info=True)
    for cid in character_roots():
        if cid not in ids:
            ids.append(cid)
    return ids


def _first_video(character_dir: Path) -> Optional[Path]:
    """角色包里最适合当封面的一段动画：先 idle，再 turn/random，最后任意。"""
    videos = character_dir / "videos"
    preferred = ["idle", "turn", "idle_turn", "random", "move", "click", "drag", "events"]
    for name in preferred:
        sub = videos / name
        if not sub.is_dir():
            continue
        try:
            files = sorted(sub.iterdir())
        except OSError:
            continue
        for f in files:
            if f.is_file() and f.suffix.lower() in _VIDEO_SUFFIXES:
                return f
    try:
        for f in sorted(videos.rglob("*")):
            if f.is_file() and f.suffix.lower() in _VIDEO_SUFFIXES:
                return f
    except OSError:
        pass
    return None


def character_thumbnail(character_id: str, side: int = _THUMB_SIDE) -> QPixmap:
    """角色首帧缩略图；解不出来时返回空 pixmap（调用方按占位处理）。"""
    root = character_roots().get(character_id)
    if root is None:
        return QPixmap()
    video = _first_video(root)
    if video is None:
        return QPixmap()
    image = decode_representative_frame(video)
    if image is None or image.isNull():
        return QPixmap()
    pixmap = QPixmap.fromImage(image)
    return pixmap.scaled(side, side, Qt.AspectRatioMode.KeepAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)


def display_name(cfg, character_id: str) -> str:
    """角色显示名：用户别名优先，其次 catalog 内置名，最后 id。"""
    alias = ""
    alias_fn = getattr(cfg, "character_alias", None)
    if callable(alias_fn):
        try:
            alias = str(alias_fn(character_id) or "")
        except Exception:
            alias = ""
    if alias:
        return alias
    try:
        return str(catalog.character_display_name(character_id) or character_id)
    except Exception:
        return character_id


# ---------------------------------------------------------------- 当前角色
def current_character(shell, sprite=None) -> str:
    """读"这一只"当前角色：overlay 走 per-sprite 配置，legacy 走窗口配置。

    注意：**不要**优先读 ``sprite._profile_character``——它由建库时读档写入，
    主配置与档案不一致时会给出脏值（启动按主配置显示 demo、属性却是档案里的
    frost），切换后会错一格。进程内推进另见 :data:`_LAST_SWITCHED`。
    """
    candidates: list[Callable[[], object]] = []
    sprite_cfg = getattr(shell, "_sprite_config", None)
    if sprite is not None and callable(sprite_cfg):
        candidates.append(lambda: sprite_cfg(sprite))
    candidates.append(lambda: getattr(shell, "config", None))
    instance = getattr(shell, "instance", None)
    if instance is not None:
        candidates.append(lambda: getattr(instance, "config", None))
    for getter in candidates:
        try:
            cfg = getter()
        except Exception:
            continue
        if cfg is None or not hasattr(cfg, "get"):
            continue
        try:
            value = cfg.get("character", "")
        except Exception:
            value = ""
        if value:
            return str(value)
    return catalog.DEFAULT_CHARACTER


#: 进程内"上一次成功切到谁"的记忆（key: id(sprite) 或 id(shell)）。
#: 配置落盘后回读有延迟，热键紧接轮番时会读到旧角色 → 连续两次切到同一只；
#: 这份记忆让连续快速切换按真实顺序推进。
_LAST_SWITCHED: dict[int, str] = {}


def mark_current(shell, character_id: str, sprite=None) -> None:
    """外部（如缩略图点击）切完角色后登记，避免下一次热键错一格。"""
    if character_id:
        _LAST_SWITCHED[id(sprite) if sprite is not None else id(shell)] = str(character_id)


def cycle_character(shell, sprite=None, step: int = 1) -> Optional[str]:
    """切到上/下一个角色，返回新角色 id（无可切或失败返回 None）。"""
    ids = available_characters()
    if len(ids) < 2:
        return None
    key = id(sprite) if sprite is not None else id(shell)
    remembered = _LAST_SWITCHED.get(key)
    current = remembered if remembered in ids else current_character(shell, sprite)
    try:
        index = ids.index(current)
    except ValueError:
        index = 0
    target = ids[(index + step) % len(ids)]
    switch = getattr(shell, "switch_character", None)
    if not callable(switch):
        logger.warning("cycle_character: shell 没有 switch_character")
        return None
    try:
        if _switch_accepts_sprite(switch):
            switch(target, sprite)
        else:
            switch(target)
    except TypeError:
        switch(target)
    except Exception:
        logger.exception("cycle_character: 切换到 %s 失败", target)
        return None
    _LAST_SWITCHED[key] = target
    return target


def _switch_accepts_sprite(switch: Callable) -> bool:
    try:
        import inspect

        params = inspect.signature(switch).parameters
    except (TypeError, ValueError):
        return False
    return "sprite" in params


# ---------------------------------------------------------------- 缩略图行
def _button_qss(active: bool) -> str:
    border = "2px solid #6ea8fe" if active else "1px solid rgba(255,255,255,0.12)"
    bg = "rgba(110,168,254,0.18)" if active else "transparent"
    return (
        "QToolButton{border:%s;border-radius:8px;padding:4px;background:%s;"
        "color:%s;}" 
        "QToolButton:hover{background:rgba(255,255,255,0.10);}"
        % (border, bg, "#ffffff" if active else "rgba(255,255,255,0.85)")
    )


class CharacterStrip(QWidget):
    """菜单里的角色缩略图行：一格一只，当前那只高亮。

    ``on_pick(character_id)`` 由调用方注入——**只**负责"点了谁"，切不切、怎么切
    由调用方决定（菜单侧接 ``pet.request_switch_character``）。
    """

    def __init__(self, character_ids: Iterable[str], current: str,
                 name_of: Callable[[str], str],
                 on_pick: Callable[[str], None],
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(6)
        self._buttons: list[QToolButton] = []
        for cid in character_ids:
            button = QToolButton(self)
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            button.setIconSize(QSize(_THUMB_SIDE, _THUMB_SIDE))
            button.setFixedSize(QSize(_THUMB_SIDE + 24, _THUMB_SIDE + 30))
            button.setAutoRaise(True)
            thumb = character_thumbnail(cid)
            if not thumb.isNull():
                button.setIcon(QIcon(thumb))
            button.setText(name_of(cid))
            button.setToolTip(name_of(cid))
            button.setStyleSheet(_button_qss(cid == current))
            button.clicked.connect(
                lambda _checked=False, cid=cid: on_pick(cid))
            layout.addWidget(button)
            self._buttons.append(button)
        layout.addStretch(1)


def attach_strip(submenu: QMenu, *, current: str,
                 name_of: Callable[[str], str],
                 on_pick: Callable[[str], None],
                 close_after: Optional[QMenu] = None) -> None:
    """把缩略图行插到子菜单最上方（文字列表原样保留在下方）。"""

    def pick(character_id: str) -> None:
        on_pick(character_id)
        # QWidgetAction 里的按钮不会自动关菜单，手动收口
        try:
            target = close_after or submenu
            target.close()
        except Exception:
            logger.debug("关闭角色菜单失败", exc_info=True)

    strip = CharacterStrip(available_characters(), current, name_of, pick)
    action = QWidgetAction(submenu)
    action.setDefaultWidget(strip)
    actions = submenu.actions()
    if actions:
        submenu.insertAction(actions[0], action)
    else:
        submenu.addAction(action)
    submenu.addSeparator()


# ---------------------------------------------------------------- 定时轮番
class CharacterRotation(QObject):
    """按间隔轮番切角色（"三件套"里的定时那一条）。

    配置：``character_rotation = {"enabled": bool, "interval_min": int}``。
    计时器挂在 shell 上，随窗口重建存活；``apply_config`` 幂等，改完设置调一次即可。
    """

    def __init__(self, shell, sprite=None) -> None:
        super().__init__(shell if isinstance(shell, QObject) else None)
        self._shell = shell
        self._sprite = sprite
        self._timer = QTimer(self)
        self._timer.setSingleShot(False)
        self._timer.timeout.connect(self._tick)
        self.interval_min = 0
        self.enabled = False

    # -- 配置 ------------------------------------------------------
    @staticmethod
    def read_config(cfg) -> dict:
        """读 ``{"enabled": bool, "interval_min": int}``；脏值一律回落默认。"""
        enabled = False
        interval = ROTATION_DEFAULT_INTERVAL
        if cfg is not None:
            try:
                enabled = bool(cfg.get(ROTATION_ENABLED_KEY, False))
            except Exception:
                enabled = False
            try:
                interval = int(cfg.get(ROTATION_INTERVAL_KEY, ROTATION_DEFAULT_INTERVAL))
            except (TypeError, ValueError):
                interval = ROTATION_DEFAULT_INTERVAL
        return {"enabled": enabled, "interval_min": max(1, interval)}

    def apply_config(self, cfg=None) -> None:
        if cfg is None:
            cfg = getattr(self._shell, "config", None)
        conf = self.read_config(cfg)
        self.enabled = bool(conf["enabled"])
        self.interval_min = int(conf["interval_min"])
        if self.enabled and len(available_characters()) > 1:
            self._timer.start(max(1, self.interval_min) * 60_000)
            logger.info("角色轮番已开启：每 %d 分钟", self.interval_min)
        else:
            self._timer.stop()
            if self.enabled:
                logger.info("角色轮番已开启但可切换角色不足 2 个，暂不启动")

    # -- 计时 ------------------------------------------------------
    def _tick(self) -> None:
        if not self.enabled:
            self._timer.stop()
            return
        target = cycle_character(self._shell, self._sprite)
        if target is None:
            # 只有一只角色或切换失败：停表，免得每分钟空转
            logger.debug("角色轮番：无可切换角色，暂停")
            self._timer.stop()
        else:
            logger.info("角色轮番切换到：%s", target)

    def stop(self) -> None:
        self._timer.stop()
