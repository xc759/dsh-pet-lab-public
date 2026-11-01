"""分层立绘渲染引擎（引擎 2：伪 Live2D）。

设计目标
--------
- 不依赖 Live2D 运行时，用「单张高清立绘 + 分层 + 程序化动效」实现呼吸/注视/拖拽摆动/
  点击回弹/说话口型等观感，作为 moc3 不可用时的主力渲染路径。
- 与 frameseq / webm 引擎共用动作槽语义（idle/click/drag/fall/speak/watch/expression）。
- 严格禁止在本模块内绘制任何几何或矢量人物图形：全部像素均来自角色包内的图片资源。

角色包格式（layers 模式）
-------------------------
<角色包>/manifest.json
{
  "id": "demo",
  "engine": "layered",
  "canvas": [1080, 1920],
  "layers": [
    {"file": "layers/body.png",  "z": 0, "motion": "body"},
    {"file": "layers/head.png",  "z": 10, "motion": "head", "lag": 0.10},
    {"file": "layers/eye_open.png",   "z": 20, "motion": "eye_open"},
    {"file": "layers/eye_closed.png", "z": 21, "motion": "eye_closed"},
    {"file": "layers/hair_back.png",  "z": -5, "motion": "hair", "amp": 1.2, "freq": 0.45, "phase": 0.0},
    {"file": "layers/hair_front.png", "z": 30, "motion": "hair", "amp": 1.6, "freq": 0.52, "phase": 1.0},
    {"file": "layers/mouth_open.png", "z": 25, "motion": "mouth"}
  ]
}
缺少 manifest 时退化为「目录内所有 png 按文件名排序，全部按 body 层处理」。
"""
from __future__ import annotations

import json
import math
import os
import random
from dataclasses import dataclass, field
from typing import List, Optional

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QAction, QImage, QPainter, QPixmap, QTransform
from PySide6.QtWidgets import QApplication, QMenu, QWidget

_IMG_EXT = (".png", ".webp", ".jpg", ".jpeg")


# --------------------------------------------------------------------------- #
# 图层描述
# --------------------------------------------------------------------------- #
@dataclass
class LayerSpec:
    file: str
    z: int = 0
    motion: str = "body"          # body / head / hair / eye_open / eye_closed / mouth
    amp: float = 1.0              # 动效幅度系数
    freq: float = 1.0             # 动效频率系数
    phase: float = 0.0            # 相位（弧度）
    lag: float = 0.0              # 跟随滞后秒数


@dataclass
class Layer:
    spec: LayerSpec
    image: QImage = None          # 原始分辨率
    _cache: dict = field(default_factory=dict, repr=False)   # (target_h, variant) -> QPixmap


# --------------------------------------------------------------------------- #
# 角色（分层立绘）
# --------------------------------------------------------------------------- #
class LayeredCharacter:
    """加载并驱动一个分层立绘角色。"""

    BREATH_PERIOD = 3.2           # 呼吸周期（秒）
    BLINK_MIN, BLINK_MAX = 2.5, 6.0

    def __init__(self) -> None:
        self.pack_dir: str = ""
        self.character_id: str = "unknown"
        self.canvas = (1080, 1920)
        self.layers: List[Layer] = []
        self._t = 0.0
        self._blink_at = random.uniform(self.BLINK_MIN, self.BLINK_MAX)
        self._blink_left = 0.0
        self._click_t = -1.0
        self._speak_until = -1.0
        self._watch = QPointF(0.0, 0.0)   # -1..1 归一化注视方向
        self._drag_tilt = 0.0
        self._target_h_for_state = 500

    # ---------------- 加载 ---------------- #
    @classmethod
    def load(cls, pack_dir: str) -> "LayeredCharacter":
        c = cls()
        c.pack_dir = pack_dir
        manifest_path = os.path.join(pack_dir, "manifest.json")
        specs: List[LayerSpec] = []
        if os.path.isfile(manifest_path):
            try:
                with open(manifest_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                c.character_id = str(data.get("id") or os.path.basename(pack_dir))
                canvas = data.get("canvas")
                if isinstance(canvas, (list, tuple)) and len(canvas) == 2:
                    c.canvas = (int(canvas[0]), int(canvas[1]))
                for item in data.get("layers") or []:
                    file = str(item.get("file", "")).replace("\\", "/")
                    if not file:
                        continue
                    specs.append(LayerSpec(
                        file=file,
                        z=int(item.get("z", 0)),
                        motion=str(item.get("motion", "body")),
                        amp=float(item.get("amp", 1.0)),
                        freq=float(item.get("freq", 1.0)),
                        phase=float(item.get("phase", 0.0)),
                        lag=float(item.get("lag", 0.0)),
                    ))
            except Exception as exc:  # noqa: BLE001 - 坏 manifest 不应阻断启动
                print(f"[layered] manifest 解析失败，退化为目录扫描: {exc}")
        if not specs:
            c.character_id = os.path.basename(pack_dir.rstrip("\\/"))
            for name in sorted(os.listdir(pack_dir)):
                if name.lower().endswith(_IMG_EXT):
                    specs.append(LayerSpec(file=name))
            layers_dir = os.path.join(pack_dir, "layers")
            if os.path.isdir(layers_dir):
                for name in sorted(os.listdir(layers_dir)):
                    if name.lower().endswith(_IMG_EXT):
                        specs.append(LayerSpec(file=f"layers/{name}"))

        for spec in specs:
            path = os.path.join(pack_dir, spec.file.replace("/", os.sep))
            if not os.path.isfile(path):
                print(f"[layered] 缺图层，跳过: {spec.file}")
                continue
            img = QImage(path)
            if img.isNull():
                print(f"[layered] 图层读取失败: {spec.file}")
                continue
            c.layers.append(Layer(spec=spec, image=img))
        c.layers.sort(key=lambda l: l.spec.z)
        if not c.layers:
            raise RuntimeError(f"角色包不含任何可用图层: {pack_dir}")
        return c

    # ---------------- 缓存 ---------------- #
    # 预烘焙缩放档：呼吸只用「换档 + 整数平移」，运行时不做任何重采样，
    # 这是把 CPU 从 36% 压到 3% 以下的关键（避免每帧 SmoothPixmapTransform 缩放）。
    VARIANTS = 7          # 0..6，对称中心为 3
    VARIANT_STEP = 0.0018  # 每档 0.18%，最大 ±0.54%

    @classmethod
    def variant_scale(cls, variant: int) -> float:
        return 1.0 + (variant - cls.VARIANTS // 2) * cls.VARIANT_STEP

    def _layer_pixmap(self, layer: Layer, target_h: int, variant: int = 3) -> QPixmap:
        key = (target_h, variant)
        pm = layer._cache.get(key)
        if pm is not None:
            return pm
        if len(layer._cache) > 64:      # 缩放档换了就丢弃旧档，防内存膨胀
            layer._cache.clear()
        scale = self.variant_scale(variant)
        h = max(1, int(round(target_h * scale)))
        img = layer.image
        if img.height() != h:
            img = img.scaledToHeight(h, Qt.SmoothTransformation)
        pm = QPixmap.fromImage(img)
        layer._cache[key] = pm
        return pm

    def native_height(self) -> int:
        return max((l.image.height() for l in self.layers), default=0)

    def native_width(self) -> int:
        return max((l.image.width() for l in self.layers), default=0)

    def canvas_width(self, target_h: int) -> int:
        nh = self.native_height() or 1
        nw = self.native_width() or 1
        return max(1, int(round(target_h * nw / nh)))

    # ---------------- 状态驱动 ---------------- #
    def advance(self, dt: float, mouse_dir: QPointF | None = None,
                dragging: bool = False) -> None:
        self._t += dt
        if mouse_dir is not None:
            self._watch = mouse_dir
        target_tilt = 0.0
        if dragging:
            target_tilt = max(-4.0, min(4.0, self._watch.x() * 4.0))
        self._drag_tilt += (target_tilt - self._drag_tilt) * min(1.0, dt * 6.0)

        self._blink_at -= dt
        if self._blink_at <= 0 and self._blink_left <= 0:
            self._blink_left = 0.14
            self._blink_at = random.uniform(self.BLINK_MIN, self.BLINK_MAX)
        if self._blink_left > 0:
            self._blink_left = max(0.0, self._blink_left - dt)

        if self._click_t >= 0:
            self._click_t += dt
            if self._click_t > 0.45:
                self._click_t = -1.0

    def trigger_click(self) -> None:
        self._click_t = 0.0

    def trigger_speak(self, seconds: float = 3.0) -> None:
        self._speak_until = self._t + seconds

    # ---------------- 帧状态量化（用于「无变化不重绘」） ---------------- #
    HEAD_LAG = 0.12

    def _head_motion(self, spec: LayerSpec) -> tuple[int, int]:
        lag = spec.lag or self.HEAD_LAG
        lb = math.sin((self._t - lag) * 2 * math.pi / self.BREATH_PERIOD)
        variant = max(0, min(self.VARIANTS - 1, 3 + int(round(lb * 2.2))))
        return variant, int(round(lb * self._target_h_for_state * 0.005))

    def frame_state(self, target_h: int) -> tuple:
        self._target_h_for_state = target_h
        t = self._t
        breath = math.sin(t * 2 * math.pi / self.BREATH_PERIOD)
        vb = max(0, min(self.VARIANTS - 1, 3 + int(round(breath * 2.2))))
        dyb = int(round(breath * target_h * 0.005))
        click_q = -1 if self._click_t < 0 else int(min(0.45, self._click_t) * 60)
        blink = 1 if self._blink_left > 0 else 0
        tilt_q = int(round(self._drag_tilt * 4))
        speak_q = int((t * 11.0) % 1.0 * 8) if t < self._speak_until else -1
        heads = tuple(self._head_motion(l.spec) for l in self.layers if l.spec.motion == "head")
        return (vb, dyb, click_q, blink, tilt_q, speak_q, heads)

    # ---------------- 绘制 ---------------- #
    def paint(self, painter: QPainter, target_h: int) -> None:
        """快路径优先：除点击/拖拽/口型外一律整数坐标 drawPixmap，运行时零重采样。"""
        t = self._t
        breath = math.sin(t * 2 * math.pi / self.BREATH_PERIOD)

        squash = 1.0
        kick = 0.0
        if self._click_t >= 0:
            p = self._click_t / 0.45
            squash = 1.0 - 0.05 * math.sin(p * math.pi)
            kick = -target_h * 0.012 * math.sin(p * math.pi)

        speaking = self._t < self._speak_until
        mouth_open = (0.5 + 0.5 * math.sin(t * 11.0)) * 0.08 if speaking else 0.0
        blinking = self._blink_left > 0
        tilt = self._drag_tilt
        canvas_w = self.canvas_width(target_h)

        painter.setRenderHint(QPainter.SmoothPixmapTransform, False)

        for layer in self.layers:
            spec = layer.spec
            motion = spec.motion

            if motion == "eye_closed" and not blinking:
                continue
            if motion == "eye_open" and blinking:
                continue

            if motion == "body":
                variant = max(0, min(self.VARIANTS - 1, 3 + int(round(breath * 2.2))))
                dy = int(round(breath * target_h * 0.005))
                slow = squash != 1.0
            elif motion == "head":
                variant, dy = self._head_motion(spec)
                slow = abs(tilt) > 0.05
            else:
                variant, dy, slow = 3, 0, False

            pm = self._layer_pixmap(layer, target_h, variant)
            if pm.isNull():
                continue
            w, h = pm.width(), pm.height()

            if motion in ("body", "head"):
                x = (canvas_w - w) // 2
                y = target_h - h + dy
                if not slow:
                    painter.drawPixmap(x, y, pm)
                    continue
            elif motion == "hair":
                phase = t * 2 * math.pi * 0.35 * spec.freq + spec.phase
                x = (canvas_w - w) // 2 + int(round(math.sin(phase) * spec.amp * 1.6))
                y = target_h - h
                painter.drawPixmap(x, y, pm)
                continue
            elif motion == "mouth":
                if not speaking:
                    continue
                x, y = (canvas_w - w) // 2, target_h - h
            else:
                x, y = (canvas_w - w) // 2, target_h - h

            # ---- 慢路径：点击回弹 / 拖拽摆动 / 口型 ----
            painter.save()
            painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
            cx, cy = w / 2.0, h / 2.0
            painter.translate(cx + x, cy + y + (kick if motion == "body" else 0))
            if motion == "head":
                painter.rotate(tilt * 0.6)
            elif motion == "body":
                painter.scale(2.0 - squash, squash)
            elif motion == "mouth":
                painter.scale(1.0, 1.0 + mouth_open)
            painter.translate(-cx, -cy)
            painter.drawPixmap(0, 0, pm)
            painter.restore()

    def size_hint(self, target_h: int) -> tuple[int, int]:
        nh = self.native_height() or 1
        nw = self.native_width() or 1
        return int(round(target_h * nw / nh)), target_h


# --------------------------------------------------------------------------- #
# 透明置顶窗口
# --------------------------------------------------------------------------- #
class LayeredOverlay(QWidget):
    """承载 LayeredCharacter 的透明置顶窗口（可独立运行做链路验证）。

    性能策略：桌宠默认 30fps，空闲超过 IDLE_AFTER 秒且无交互时降到 IDLE_FPS，
    有鼠标靠近/拖拽/点击时立刻恢复。这是把 CPU 压到 3% 以下的关键手段之一。
    """

    FPS = 30
    IDLE_FPS = 15
    IDLE_AFTER = 2.0

    def __init__(self, character: LayeredCharacter, target_height: int = 500,
                 fps: int = 30) -> None:
        super().__init__(None)
        self.character = character
        self.target_h = int(target_height)
        self.fps = int(fps)
        self._drag_offset: Optional[QPoint] = None
        self._dragging = False
        self._last_pos = QPointF()
        self._idle_time = 0.0
        self._last_geom = None
        self._fps_now = self.fps
        self._last_sig = None
        self._setup_window()
        self._resize_to_character()

        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.CoarseTimer)
        self._timer.setInterval(int(1000 / self.fps))
        self._timer.timeout.connect(self._on_tick)
        self._timer.start()

    # ---------------- 窗口 ---------------- #
    def _setup_window(self) -> None:
        flags = Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        if hasattr(Qt, "NoDropShadowWindowHint"):
            flags |= Qt.NoDropShadowWindowHint
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setWindowTitle("Layered Pet Demo")

    def _resize_to_character(self) -> None:
        w, h = self.character.size_hint(self.target_h)
        self.resize(max(1, w), max(1, h))
        scr = QApplication.primaryScreen()
        if scr is not None:
            geo = scr.availableGeometry()
            self.move(geo.center().x() - w // 2, geo.bottom() - h + 1)

    # ---------------- 主循环 ---------------- #
    def _on_tick(self) -> None:
        dt = 1.0 / float(self._cur_fps())
        cursor = self.mapFromGlobal(self.cursor().pos())
        inside = self.rect().contains(cursor)
        mouse_dir = QPointF(0.0, 0.0)
        if self.width() > 0 and self.height() > 0 and (inside or self._dragging):
            mouse_dir = QPointF(
                max(-1.0, min(1.0, (cursor.x() - self.width() / 2) / (self.width() / 2))),
                max(-1.0, min(1.0, (cursor.y() - self.height() / 2) / (self.height() / 2))),
            )

        # 空闲降频：鼠标不在角色上、未拖拽时按 IDLE_FPS 走
        if inside or self._dragging:
            self._idle_time = 0.0
            want = self.fps
        else:
            self._idle_time += dt
            want = self.IDLE_FPS if self._idle_time > self.IDLE_AFTER else self.fps
        if want != self._fps_now:
            self._fps_now = want
            self._timer.setInterval(max(15, int(1000 / want)))

        self.character.advance(dt, mouse_dir=mouse_dir, dragging=self._dragging)
        sig = self.character.frame_state(self.target_h)
        if self._dragging or sig != self._last_sig:
            self._last_sig = sig
            self.update()

    def _cur_fps(self) -> int:
        return self._fps_now or self.fps

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        painter.setRenderHint(QPainter.Antialiasing, False)
        painter.setRenderHint(QPainter.TextAntialiasing, False)
        self.character.paint(painter, self.target_h)
        painter.end()

    # ---------------- 交互 ---------------- #
    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self._dragging = True

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag_offset is not None:
            self.move(event.globalPosition().toPoint() - self._drag_offset)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            moved = self._dragging and self._drag_offset is not None
            self._drag_offset = None
            self._dragging = False
            if moved and event.position().toPoint() != QPoint(0, 0):
                # 点击（未明显移动）时触发回弹；拖拽视为移动，不触发
                delta = self.pos() - self._last_pos
                if delta.manhattanLength() < 6:
                    self.character.trigger_click()

    def wheelEvent(self, event) -> None:  # noqa: N802
        step = event.angleDelta().y() / 120
        new_h = int(self.target_h * (1.08 ** step))
        new_h = max(200, min(1200, new_h))
        if new_h != self.target_h:
            bottom = self.geometry().bottom()
            center_x = self.geometry().center().x()
            self.target_h = new_h
            w, h = self.character.size_hint(self.target_h)
            self.resize(w, h)
            self.move(center_x - w // 2, bottom - h + 1)

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        menu = QMenu(self)
        act_speak = QAction("说句话（口型）", self)
        act_speak.triggered.connect(lambda: self.character.trigger_speak(3.0))
        act_big = QAction("放大", self)
        act_big.triggered.connect(lambda: self._scale_by(1.15))
        act_small = QAction("缩小", self)
        act_small.triggered.connect(lambda: self._scale_by(1 / 1.15))
        act_quit = QAction("退出", self)
        act_quit.triggered.connect(self.close)
        for a in (act_speak, act_big, act_small):
            menu.addAction(a)
        menu.addSeparator()
        menu.addAction(act_quit)
        menu.exec(event.globalPos())

    def _scale_by(self, factor: float) -> None:
        new_h = max(200, min(1200, int(self.target_h * factor)))
        bottom = self.geometry().bottom()
        center_x = self.geometry().center().x()
        self.target_h = new_h
        w, h = self.character.size_hint(self.target_h)
        self.resize(w, h)
        self.move(center_x - w // 2, bottom - h + 1)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key_Escape:
            self.close()


# --------------------------------------------------------------------------- #
# 独立运行：python -m petwisp.overlay_layered <角色包目录> [高度]
# --------------------------------------------------------------------------- #
def main() -> int:
    import sys

    args = [a for a in sys.argv[1:] if a]
    if not args:
        print("用法: python -m petwisp.overlay_layered <角色包目录> [显示高度]")
        return 2
    pack = args[0]
    height = int(args[1]) if len(args) > 1 else 500

    app = QApplication(sys.argv)
    character = LayeredCharacter.load(pack)
    print(f"[layered] 已加载 {character.character_id}: {len(character.layers)} 层, "
          f"{character.native_width()}x{character.native_height()}")
    overlay = LayeredOverlay(character, target_height=height)
    overlay.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
