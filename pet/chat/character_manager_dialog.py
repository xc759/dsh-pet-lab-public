# -*- coding: utf-8 -*-
"""角色管理对话框（v5.1 M3）。

用途：给每个形象单独写人设、重命名、导入新角色包、停用不用的角色。
聊天历史由上游 SessionStore 按角色分目录（sessions/<character_id>/），
本对话框只做提示，不参与读写。

人设优先级（上游 chat/prompt.py 的 effective_system_prompt）：
    role_prompt（场景扮演） > 角色 manifest.chat.system_prompt > AI 设置里的全局 System Prompt
「统一人格」开关在 AI 设置页，开启后忽略角色人设，全部走全局 System Prompt。
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from .. import character_manager as cm


class CharacterManagerDialog(QDialog):
    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("角色管理")
        self.resize(760, 520)
        self._current: str | None = None

        root = QVBoxLayout(self)
        tip = QLabel(
            "每个形象有独立人设与独立聊天历史。人设留空时使用 AI 设置里的全局 System Prompt。"
            "聊天历史按角色分开存放，切角色不会串话。"
        )
        tip.setWordWrap(True)
        root.addWidget(tip)

        body = QHBoxLayout()
        left = QVBoxLayout()
        left.addWidget(QLabel("形象列表"))
        self.list = QListWidget(self)
        self.list.setMinimumWidth(230)
        self.list.currentRowChanged.connect(self._on_select)
        left.addWidget(self.list, 1)
        row = QHBoxLayout()
        self.import_btn = QPushButton("导入角色包…", self)
        self.import_btn.clicked.connect(self._import_pack)
        self.park_btn = QPushButton("停用角色", self)
        self.park_btn.clicked.connect(self._park)
        self.rename_btn = QPushButton("重命名", self)
        self.rename_btn.clicked.connect(self._rename)
        row.addWidget(self.import_btn)
        row.addWidget(self.park_btn)
        row.addWidget(self.rename_btn)
        left.addLayout(row)
        body.addLayout(left)

        right = QVBoxLayout()
        self.head = QLabel("未选择形象")
        right.addWidget(self.head)
        self.editor = QPlainTextEdit(self)
        self.editor.setPlaceholderText(
            "写这个形象的人设：身份、语气、说话习惯、对用户的称呼……\n"
            "例：你是住在桌面上的御姐管家「凛」，说话简短克制，偶尔毒舌，但会把用户的事放在第一位。"
        )
        right.addWidget(self.editor, 1)
        save_row = QHBoxLayout()
        save_row.addStretch(1)
        self.save_btn = QPushButton("保存人设", self)
        self.save_btn.clicked.connect(self._save)
        save_row.addWidget(self.save_btn)
        right.addLayout(save_row)
        body.addLayout(right, 1)
        root.addLayout(body, 1)

        bottom = QHBoxLayout()
        bottom.addStretch(1)
        close_btn = QPushButton("关闭", self)
        close_btn.clicked.connect(self.accept)
        bottom.addWidget(close_btn)
        root.addLayout(bottom)

        self._reload()

    # ------------------------------------------------------------ 列表
    def _reload(self, keep: str | None = None) -> None:
        keep = keep or self._current
        current_config = self.config
        self.list.blockSignals(True)
        self.list.clear()
        for cid in cm.list_characters():
            alias = ""
            try:
                alias = current_config.character_alias(cid) or ""
            except Exception:
                alias = ""
            name = current_config.character_display_name(cid) if hasattr(current_config, "character_display_name") else cid
            has_persona = bool(cm.get_persona(cid).strip())
            mark = "＊" if has_persona else "　"
            item = QListWidgetItem(f"{mark}{name}（{cid}）")
            item.setData(Qt.ItemDataRole.UserRole, cid)
            item.setToolTip("＊ = 已有专属人设" + (f"\n别名：{alias}" if alias else ""))
            self.list.addItem(item)
            if cid == keep:
                self.list.setCurrentItem(item)
        self.list.blockSignals(False)
        if self.list.count() and self.list.currentRow() < 0:
            self.list.setCurrentRow(0)
        else:
            self._on_select(self.list.currentRow())

    def _on_select(self, row: int) -> None:
        if row < 0 or row >= self.list.count():
            self._current = None
            self.head.setText("未选择形象")
            self.editor.setPlainText("")
            return
        item = self.list.item(row)
        cid = item.data(Qt.ItemDataRole.UserRole)
        self._current = cid
        try:
            name = self.config.character_display_name(cid)
        except Exception:
            name = cid
        builtin = "内置形象" if cm.is_builtin(cid) else "用户形象"
        self.head.setText(f"当前编辑：{name}（{cid}，{builtin}）")
        self.editor.setPlainText(cm.get_persona(cid))
        self.save_btn.setEnabled(True)

    # ------------------------------------------------------------ 动作
    def _save(self) -> None:
        if not self._current:
            return
        text = self.editor.toPlainText()
        if not cm.set_persona(self._current, text):
            QMessageBox.warning(self, "保存失败", "没找到该角色的 manifest.json，无法写入人设。")
            return
        self._reload(keep=self._current)
        QMessageBox.information(self, "已保存", "人设已写入角色包，下一轮对话立即生效。")

    def _import_pack(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择角色包目录（含 manifest.json 与 videos/）")
        if not path:
            return
        ok, msg = cm.import_pack(Path(path))
        if not ok:
            QMessageBox.warning(self, "导入失败", msg)
            return
        self._reload(keep=msg)
        QMessageBox.information(self, "已导入", f"角色 {msg} 已复制到用户角色目录，可在列表里写人设。")

    def _park(self) -> None:
        if not self._current:
            return
        cid = self._current
        if QMessageBox.question(
            self,
            "停用角色",
            f"将把角色 {cid} 的整个角色包移动到用户目录下的 .parked 文件夹（不是物理删除，可手工恢复）。\n"
            "该角色的聊天历史会保留在原处，重新启用后可继续使用。\n\n确定停用？",
        ) != QMessageBox.StandardButton.Yes:
            return
        ok, msg = cm.park_character(cid)
        if not ok:
            QMessageBox.warning(self, "无法停用", msg)
            return
        self._current = None
        self._reload()
        QMessageBox.information(self, "已停用", msg)

    def _rename(self) -> None:
        if not self._current:
            return
        cid = self._current
        try:
            old = self.config.character_display_name(cid)
        except Exception:
            old = cid
        text, ok = QInputDialog.getText(self, "重命名", "显示名称：", text=old)
        if not ok:
            return
        text = (text or "").strip()
        if not text:
            return
        try:
            self.config.set_character_alias(cid, text)
        except Exception as exc:  # pragma: no cover - 极端情况
            QMessageBox.warning(self, "重命名失败", str(exc))
            return
        self._reload(keep=cid)
