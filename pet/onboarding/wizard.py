# -*- coding: utf-8 -*-
"""首次启动向导：选角色 → 填考试日 → 配模型（三步，可全跳）。

为什么是这三步：这三件事是"换台机器后不做就没法用"的全部——角色决定长什么样，
考试日决定成长档案的倒计时，模型 Key 决定能不能开口说话。其余设置（缩放、热键、
自启动…）都有合理默认值，不该在第一次见面时拦住用户。

实现约定：
* 三个值各自 try 包一层：任何一步写失败都不拦后面两步（用户至少能带走一部分）；
* 角色走 ``shell.switch_character``（内部会 config.set + save + 重建窗口），
  没有 shell 时退化成只写配置（测试与无壳场景）；
* 模型 Key 走 ``pet.chat`` 的 SecretStore，**延迟 import**：no-chat 变体里没有
  ``pet.chat``，向导在那条链路上直接不出现第三步；
* 关窗/跳过也写完成标记：否则每次启动都会再弹一次，变成骚扰。
"""
from __future__ import annotations

import importlib.util
import logging
from datetime import date

from PySide6.QtCore import QDate, Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox, QDateEdit, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QPushButton, QStackedWidget, QVBoxLayout, QWidget,
)

from . import state as onboarding_state

_STEP_TITLES = ("选择角色", "考试倒计时", "接入模型")

_QSS = """
#obCard { background: #ffffff; border-radius: 12px; }
#obTitle { font-size: 18px; font-weight: 600; color: #1b1f23; }
#obHint { color: #5b6570; font-size: 12px; }
#obStep { color: #8a939d; font-size: 12px; }
#obHeading { font-size: 15px; font-weight: 600; color: #1b1f23; }
#obBody { color: #444d57; font-size: 12px; }
#obCharList { border: 1px solid #dfe4ea; border-radius: 8px; padding: 4px; }
#obCharList::item { padding: 8px 6px; border-radius: 6px; }
#obCharList::item:selected { background: #e8f1ff; color: #1b1f23; }
#obInput { border: 1px solid #dfe4ea; border-radius: 8px; padding: 6px 8px; }
"""


def chat_available() -> bool:
    """打包变体是否带 AI 聊天（no-chat 变体 excludes=['pet.chat']）。

    与 ``pet/__main__.py`` 的 ``_chat_available`` 同口径：只做 find_spec 探测，
    不 import —— 首次启动向导没有理由为一次探测把聊天模块拉进内存。
    """
    try:
        return importlib.util.find_spec("pet.chat") is not None
    except (ImportError, ValueError):
        return False


def _character_ids(config) -> list[str]:
    """可切换角色 id（内置 + 用户目录里装的角色包）；探测失败返回空列表。"""
    try:
        from .. import catalog

        ids = [str(cid) for cid in catalog.list_available_characters() if str(cid).strip()]
    except Exception:
        logging.exception("读取角色列表失败，跳过角色步骤")
        return []
    # 当前角色放最前：用户升级后重跑向导时，第一项就是自己正在用的那只。
    current = str(config.get("character", "") or "")
    if current in ids:
        ids.remove(current)
        ids.insert(0, current)
    return ids


class OnboardingWizard(QDialog):
    """三步向导本体（非模态，不挡桌宠）。"""

    def __init__(self, config, shell=None, parent=None) -> None:
        super().__init__(parent)
        self.config = config
        self.shell = shell
        self._character_ids = _character_ids(config)
        self._show_model_step = chat_available()
        self.setWindowTitle("大肥鱼 · 首次设置")
        self.setModal(False)
        self.setMinimumWidth(560)
        self.setStyleSheet(_QSS)
        self._build()
        self._sync_step_ui()

    # ------------------------------------------------------------ 构建
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 12)
        outer.setSpacing(12)

        card = QFrame(self)
        card.setObjectName("obCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(20, 18, 20, 18)
        card_layout.setSpacing(10)

        title = QLabel("第一次见面，先对三件事", card)
        title.setObjectName("obTitle")
        card_layout.addWidget(title)

        hint = QLabel(
            "这些设置随时能在右键菜单 →「设置」里改；现在跳过也不会坏，只是桌宠不会"
            "知道你考什么、也没法开口聊天。",
            card,
        )
        hint.setObjectName("obHint")
        hint.setWordWrap(True)
        card_layout.addWidget(hint)

        self.step_label = QLabel("", card)
        self.step_label.setObjectName("obStep")
        card_layout.addWidget(self.step_label)

        self.stack = QStackedWidget(card)
        self.stack.addWidget(self._build_character_page())
        self.stack.addWidget(self._build_exam_page())
        self.stack.addWidget(self._build_model_page())
        self.stack.addWidget(self._build_done_page())
        card_layout.addWidget(self.stack, 1)
        outer.addWidget(card, 1)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.skip_button = QPushButton("跳过", self)
        self.skip_button.clicked.connect(self._on_skip)
        buttons.addWidget(self.skip_button)
        buttons.addStretch(1)
        self.back_button = QPushButton("上一步", self)
        self.back_button.clicked.connect(self._on_back)
        buttons.addWidget(self.back_button)
        self.next_button = QPushButton("下一步", self)
        self.next_button.setDefault(True)
        self.next_button.clicked.connect(self._on_next)
        buttons.addWidget(self.next_button)
        outer.addLayout(buttons)

    def _page(self, heading: str, body: str) -> tuple[QWidget, QVBoxLayout]:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 6, 0, 0)
        layout.setSpacing(8)
        head = QLabel(heading, page)
        head.setObjectName("obHeading")
        layout.addWidget(head)
        if body:
            text = QLabel(body, page)
            text.setObjectName("obBody")
            text.setWordWrap(True)
            layout.addWidget(text)
        return page, layout

    def _build_character_page(self) -> QWidget:
        page, layout = self._page(
            "想让它长什么样？",
            "内置角色 + 你装过并下好素材的角色都会出现在这里；以后右键菜单也能换。",
        )
        self.character_list = QListWidget(page)
        self.character_list.setObjectName("obCharList")
        self.character_list.setMinimumHeight(150)
        for cid in self._character_ids:
            try:
                name = self.config.character_display_name(cid)
            except Exception:
                name = cid
            label = f"{name}" if name == cid else f"{name}（{cid}）"
            item = QListWidgetItem(label, self.character_list)
            item.setData(Qt.ItemDataRole.UserRole, cid)
        if self.character_list.count():
            current = str(self.config.get("character", "") or "")
            for row in range(self.character_list.count()):
                item = self.character_list.item(row)
                if item.data(Qt.ItemDataRole.UserRole) == current:
                    self.character_list.setCurrentRow(row)
                    break
            else:
                self.character_list.setCurrentRow(0)
        else:
            empty = QLabel("没找到可用角色素材，这一步先跳过。", page)
            empty.setObjectName("obBody")
            layout.addWidget(empty)
        layout.addWidget(self.character_list, 1)
        return page

    def _build_exam_page(self) -> QWidget:
        page, layout = self._page(
            "有没有在等的考试？",
            "填了就会在桌面显示倒计时，成长档案也按它算进度；不填（或以后填）都不影响使用。",
        )
        self.exam_enabled = QCheckBox("我有明确的考试日期", page)
        layout.addWidget(self.exam_enabled)

        self.exam_edit = QDateEdit(page)
        self.exam_edit.setObjectName("obInput")
        self.exam_edit.setCalendarPopup(True)
        self.exam_edit.setDisplayFormat("yyyy-MM-dd")
        # 默认给"明年今天"：既不是空值（用户一勾就有值），也不假装知道他的考期。
        self.exam_edit.setDate(QDate.currentDate().addYears(1))
        self.exam_edit.setEnabled(False)
        layout.addWidget(self.exam_edit)

        self.exam_enabled.toggled.connect(self.exam_edit.setEnabled)
        layout.addStretch(1)
        return page

    def _build_model_page(self) -> QWidget:
        page, layout = self._page(
            "要不要让它能开口？",
            "填 DeepSeek 的 API Key 后就能直接对话（Key 存进系统凭据管理器，不写进配置文件）；"
            "没有也可以先跳过，右键菜单 →「设置」→「AI」里随时补。",
        )
        self.api_key_edit = QLineEdit(page)
        self.api_key_edit.setObjectName("obInput")
        self.api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_edit.setPlaceholderText("sk-...（可留空）")
        layout.addWidget(self.api_key_edit)

        self.model_hint = QLabel("", page)
        self.model_hint.setObjectName("obBody")
        self.model_hint.setWordWrap(True)
        layout.addWidget(self.model_hint)
        self._refresh_model_hint()
        layout.addStretch(1)
        return page

    def _build_done_page(self) -> QWidget:
        page, layout = self._page(
            "好了，可以玩了",
            "左键拖动、双击说话、右键菜单里还有设置、成长档案和换角色。",
        )
        self.done_summary = QLabel("", page)
        self.done_summary.setObjectName("obBody")
        self.done_summary.setWordWrap(True)
        layout.addWidget(self.done_summary)
        layout.addStretch(1)
        return page

    def _refresh_model_hint(self) -> None:
        try:
            settings = self.config.chat_settings()
            provider = settings.active_provider_settings()
            text = f"当前模型接口：{provider.name} / {provider.model}（{provider.base_url}）"
        except Exception:
            text = "当前使用默认模型接口（DeepSeek）。"
        self.model_hint.setText(text)

    # ------------------------------------------------------------ 步骤控制
    def _step_indexes(self) -> list[int]:
        """可见步骤序列：没有角色/没有 chat 时对应页跳过，完成页恒在末位。"""
        steps = []
        if self._character_ids:
            steps.append(0)
        steps.append(1)
        if self._show_model_step:
            steps.append(2)
        steps.append(3)
        return steps

    def _position(self) -> int:
        order = self._step_indexes()
        current = self.stack.currentIndex()
        return order.index(current) if current in order else 0

    def _sync_step_ui(self) -> None:
        order = self._step_indexes()
        index = min(self._position(), len(order) - 1)
        self.stack.setCurrentIndex(order[index])
        last = index >= len(order) - 1
        self.step_label.setText(f"第 {index + 1} / {len(order)} 步")
        self.back_button.setEnabled(index > 0)
        self.next_button.setText("完成" if last else "下一步")
        self.skip_button.setText("跳过全部" if not last else "先这样")

    def _on_next(self) -> None:
        order = self._step_indexes()
        if self._position() >= len(order) - 1:
            self.finish()
            return
        self.stack.setCurrentIndex(order[self._position() + 1])
        self._sync_step_ui()
        if self.stack.currentIndex() == 3:
            self.done_summary.setText(self._summary_text())

    def _on_back(self) -> None:
        order = self._step_indexes()
        position = self._position()
        if position <= 0:
            return
        self.stack.setCurrentIndex(order[position - 1])
        self._sync_step_ui()

    def _on_skip(self) -> None:
        """跳过 = 立刻收工并写完成标记（已填的步骤照样落盘，不白填）。"""
        self.finish()

    def _summary_text(self) -> str:
        lines = []
        cid = self.selected_character()
        if cid:
            try:
                lines.append(f"角色：{self.config.character_display_name(cid)}")
            except Exception:
                lines.append(f"角色：{cid}")
        exam = self.selected_exam_date()
        lines.append(f"考试日：{exam}" if exam else "考试日：未填（成长档案里随时补）")
        key = self.api_key_edit.text().strip() if self._show_model_step else ""
        lines.append("模型：已接入" if key else "模型：未填 Key（设置 → AI 里随时补）")
        return "\n".join(lines)

    # ------------------------------------------------------------ 取值 / 落盘
    def selected_character(self) -> str:
        item = self.character_list.currentItem()
        if item is None:
            return ""
        return str(item.data(Qt.ItemDataRole.UserRole) or "")

    def selected_exam_date(self) -> str:
        if not self.exam_enabled.isChecked():
            return ""
        value = self.exam_edit.date().toString("yyyy-MM-dd")
        try:
            date.fromisoformat(value)
        except ValueError:
            return ""
        return value

    def apply(self) -> dict:
        """把三步的答案写进配置/档案/凭据；返回每一步的结果（测试据此断言）。"""
        result = {
            "character": self._apply_character(),
            "exam_date": self._apply_exam_date(),
            "model": self._apply_model_key(),
        }
        result["first_run"] = onboarding_state.mark_first_run_done(self.config)
        return result

    def _apply_character(self) -> str:
        cid = self.selected_character()
        if not cid:
            return ""
        try:
            current = str(self.config.get("character", "") or "")
            switch = getattr(self.shell, "switch_character", None)
            if cid != current and callable(switch):
                switch(cid)
            else:
                self.config.set("character", cid)
                self.config.save()
            return cid
        except Exception:
            logging.exception("首次向导写入角色失败")
            return ""

    def _apply_exam_date(self) -> str:
        exam = self.selected_exam_date()
        if not exam:
            return ""
        service = getattr(self.shell, "growth_service", None)
        try:
            if service is None:
                # 无壳场景（测试/纯核心启动）：临时服务只用来落一次档案，构造不落盘。
                from ..growth import GrowthService

                service = GrowthService(self.config)
                service.start()
            # 返回 None = 日期不合法被服务端清洗掉（这里已预校验过，兜底而已）。
            if service.update_learning(exam_date=exam) is not None:
                return exam
        except Exception:
            logging.exception("首次向导写入考试日失败")
        return ""

    def _apply_model_key(self) -> str:
        key = self.api_key_edit.text().strip() if self._show_model_step else ""
        if not key:
            return ""
        try:
            from ..chat.models import SecretStore

            settings = self.config.chat_settings()
            provider = settings.active_config
            ref = str(getattr(provider, "api_key_ref", "") or "").strip()
            if not ref:
                ref = f"provider/{provider.provider_id}"
                provider.api_key_ref = ref
            store = SecretStore()
            if not store.set(ref, key):
                # 钥匙串不可用时不假装成功：Key 只在本次进程内存里（SecretStore 兜底），
                # 重启即失效，所以这一步记为未完成，让用户在设置页重填。
                logging.warning("首次向导：Key 写不进系统钥匙串，本次未落盘")
                return ""
            self.config.set_chat_settings(settings)
            self.config.save()
            return provider.provider_id
        except Exception:
            logging.exception("首次向导写入模型 Key 失败")
            return ""

    # ------------------------------------------------------------ 收口
    def finish(self) -> None:
        """落盘 + 关窗。异常不外抛：向导失败不该带走整个进程。"""
        try:
            self.apply()
        except Exception:
            logging.exception("首次向导落盘失败")
        finally:
            self.accept()

    def closeEvent(self, event) -> None:  # noqa: N802 —— Qt 命名
        """直接关窗也算"跳过"：写完成标记，别让下次启动再弹一遍。"""
        try:
            onboarding_state.mark_first_run_done(self.config)
        except Exception:
            logging.exception("关闭首次向导时写完成标记失败")
        super().closeEvent(event)


def maybe_start_first_run(
    shell, config, *, delay_ms: int = 1200, needed: bool | None = None,
) -> "OnboardingWizard | None":
    """启动路径上的唯一入口：该弹就延迟弹，不该弹立刻返回 None。

    延迟的理由：向导要在桌宠窗口画完第一帧之后才出现，否则会和首帧解码抢
    GUI 线程，用户看到的是"启动卡了一下再蹦出个窗"。延迟由调用方的事件循环
    执行（QTimer），本函数不阻塞。

    ``needed``：调用方（``app.main``）在 Config 构造后、任何 save 之前算好的
    判定结果。**必须由调用方先算**——``controller.start()`` 会写位置到 config.json，
    之后目录里就有配置文件了，再判"是否首次"会一律判成老用户。
    """
    if needed is None:
        try:
            needed = onboarding_state.needs_first_run(config)
        except Exception:
            logging.exception("首次启动判定失败，本次不弹向导")
            return None
    if not needed:
        return None

    wizard = None

    def _show() -> None:
        nonlocal wizard
        try:
            wizard = OnboardingWizard(config, shell=shell)
            # 顶层窗没有 QObject 父级，Python wrapper 必须被别处持有，否则 GC 之后
            # 窗口会凭 C++ 对象"僵"在屏幕上（点按钮无响应）。
            if shell is not None:
                try:
                    setattr(shell, "onboarding_wizard", wizard)
                except Exception:
                    pass
            wizard.show()
            wizard.raise_()
            wizard.activateWindow()
        except Exception:
            logging.exception("首次向导打开失败，按已跳过处理")
            onboarding_state.mark_first_run_done(config)

    QTimer.singleShot(max(0, int(delay_ms)), _show)
    return wizard
