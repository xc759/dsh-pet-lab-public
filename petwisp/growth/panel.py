# -*- coding: utf-8 -*-
"""成长档案面板：右键菜单「成长档案」打开的非模态窗口。

只做三件事，别的一概不塞进来：

1. **看得见进度**——倒计时、今日专注、本周完成度、科目配比、里程碑；
2. **改得了目标**——目标/考试日/每周目标/科目权重在这里改，改完立刻落盘；
3. **不装第二套开关**——催事/复盘/关怀三类主动性开关归属设置页，面板只在
   底部给一句提示，不复制控件（两个地方改同一件事，迟早有一处不同步）。

视觉令牌沿用 ``todo/panel.py`` 那一套（12px 卡片圆角、7px 控件圆角、
13px 正文、强调色 #0a84ff），明暗跟随系统调色板。所有计算都交给
:mod:`petwisp.growth.dashboard`，这里只负责把数字摆上去。
"""
from __future__ import annotations

import logging
from PySide6.QtCore import QDate, Qt, QTimer
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (
    QCheckBox,
    QDateEdit,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from . import dashboard as dash

logger = logging.getLogger(__name__)


def _stylesheet(widget: QWidget) -> str:
    dark = widget.palette().color(QPalette.ColorRole.Window).lightness() < 128
    if dark:
        window, card, border, divider = "#202024", "#2a2a30", "#3a3a42", "#33333a"
        text, hint, hover = "#e0e0e6", "#9a9aa3", "#303036"
    else:
        window, card, border, divider = "#fcfcfd", "#ffffff", "#e2e4e8", "#eceef1"
        text, hint, hover = "#252525", "#777777", "#f4f5f6"
    accent = "#0a84ff"
    return f"""
    QDialog, QWidget#growthBody {{
        background: {window};
        color: {text};
        font-size: 13px;
    }}
    QLabel#growthTitle {{
        font-size: 22px;
        font-weight: 600;
        background: transparent;
    }}
    QLabel#growthCountdown {{
        font-size: 15px;
        font-weight: 600;
        color: {accent};
        background: transparent;
    }}
    QLabel[hint="true"] {{
        color: {hint};
        font-size: 12px;
        background: transparent;
    }}
    QLabel[metric="true"] {{
        font-size: 20px;
        font-weight: 600;
        background: transparent;
    }}
    QLabel[section="true"] {{
        font-size: 13px;
        font-weight: 600;
        background: transparent;
    }}
    QFrame[card="true"] {{
        background: {card};
        border: 1px solid {border};
        border-radius: 12px;
    }}
    QLineEdit, QSpinBox, QDateEdit {{
        background: {window};
        color: {text};
        border: 1px solid {border};
        border-radius: 7px;
        min-height: 30px;
        padding: 0 8px;
    }}
    QLineEdit:focus, QSpinBox:focus, QDateEdit:focus {{
        border: 1px solid {accent};
    }}
    QPushButton {{
        background: {card};
        color: {text};
        border: 1px solid {border};
        border-radius: 7px;
        min-height: 26px;
        padding: 3px 14px;
    }}
    QPushButton:hover {{ background: {hover}; }}
    QPushButton[accent="true"] {{
        background: {accent};
        color: #ffffff;
        border: 1px solid {accent};
    }}
    QCheckBox {{ spacing: 6px; background: transparent; }}
    QCheckBox::indicator {{
        width: 18px;
        height: 18px;
        border: 1px solid {border};
        border-radius: 4px;
        background: {card};
    }}
    QCheckBox::indicator:checked {{
        background: {accent};
        border: 1px solid {accent};
    }}
    QProgressBar {{
        background: {divider};
        border: none;
        border-radius: 4px;
        height: 8px;
        text-align: center;
    }}
    QProgressBar::chunk {{
        background: {accent};
        border-radius: 4px;
    }}
    QScrollArea {{ border: none; background: transparent; }}
    """


def _card(parent: QWidget | None = None) -> QFrame:
    frame = QFrame(parent)
    frame.setProperty("card", True)
    return frame


def _label(text: str, *, hint: bool = False, metric: bool = False,
           section: bool = False) -> QLabel:
    label = QLabel(text)
    if hint:
        label.setProperty("hint", True)
    if metric:
        label.setProperty("metric", True)
    if section:
        label.setProperty("section", True)
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return label


def _bar(value_pct: int) -> QProgressBar:
    bar = QProgressBar()
    bar.setRange(0, 100)
    bar.setValue(max(0, min(100, int(value_pct))))
    bar.setTextVisible(False)
    bar.setFixedHeight(8)
    return bar


class GrowthPanel(QDialog):
    """非模态成长面板。构造参数 ``service`` 为 None 时退化为只读预览。"""

    def __init__(self, service=None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.service = service
        self._data: dict = {}
        self._milestone_rows: list[dict] = []

        self.setWindowTitle("微光 · 成长档案")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.resize(680, 760)
        self.setStyleSheet(_stylesheet(self))
        self._build()
        self.refresh()

    # ------------------------------------------------------------ 构建
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(12)

        head = QHBoxLayout()
        head.setSpacing(8)
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        title_box.addWidget(_label("成长档案", section=True))
        self.countdown_label = _label("", hint=True)
        title_box.addWidget(self.countdown_label)
        head.addLayout(title_box, 1)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.close)
        head.addWidget(close_btn, 0, Qt.AlignmentFlag.AlignTop)
        outer.addLayout(head)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        body.setObjectName("growthBody")
        self.body_layout = QVBoxLayout(body)
        self.body_layout.setContentsMargins(0, 0, 8, 0)
        self.body_layout.setSpacing(12)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        self._build_today_card()
        self._build_goal_card()
        self._build_subject_card()
        self._build_milestone_card()
        self._build_review_card()
        self._build_nudge_card()
        self._build_footer()

    def _build_today_card(self) -> None:
        card = _card()
        grid = QGridLayout(card)
        grid.setContentsMargins(16, 14, 16, 14)
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(6)
        grid.addWidget(_label("今天", section=True), 0, 0, 1, 4)

        self.focus_value = _label("0", metric=True)
        self.distract_value = _label("0", metric=True)
        self.mood_value = _label("0", metric=True)
        for col, (value, caption) in enumerate((
                (self.focus_value, "专注分钟"),
                (self.distract_value, "分心次数"),
                (self.mood_value, "心情指数"))):
            grid.addWidget(value, 1, col * 2)
            grid.addWidget(_label(caption, hint=True), 2, col * 2)

        grid.addWidget(_label("本周进度", hint=True), 3, 0, 1, 4)
        self.week_bar = _bar(0)
        grid.addWidget(self.week_bar, 4, 0, 1, 4)
        self.week_label = _label("", hint=True)
        grid.addWidget(self.week_label, 5, 0, 1, 4)

        grid.addWidget(_label("现在的状态", hint=True), 8, 0, 1, 4)
        self.presence_label = _label("", hint=True)
        grid.addWidget(self.presence_label, 9, 0, 1, 4)

        grid.addWidget(_label("最近在琢磨", hint=True), 6, 0, 1, 4)
        self.topics_label = _label("", hint=True)
        self.topics_label.setWordWrap(True)
        grid.addWidget(self.topics_label, 7, 0, 1, 4)
        self.body_layout.addWidget(card)

    def _build_goal_card(self) -> None:
        card = _card()
        box = QVBoxLayout(card)
        box.setContentsMargins(16, 14, 16, 14)
        box.setSpacing(8)
        box.addWidget(_label("学习目标", section=True))

        self.goal_edit = QLineEdit()
        self.goal_edit.setPlaceholderText("一句话写清这阶段要干成什么（只存本机）")
        box.addWidget(self.goal_edit)

        row = QHBoxLayout()
        row.setSpacing(10)
        exam_box = QVBoxLayout()
        exam_box.setSpacing(4)
        exam_box.addWidget(_label("考试日", hint=True))
        self.exam_edit = QDateEdit()
        self.exam_edit.setCalendarPopup(True)
        self.exam_edit.setDisplayFormat("yyyy-MM-dd")
        self.exam_edit.setSpecialValueText("未设置")
        self.exam_edit.setMinimumDate(QDate(2000, 1, 1))
        self.exam_edit.setMaximumDate(QDate(2100, 12, 31))
        exam_box.addWidget(self.exam_edit)
        row.addLayout(exam_box, 1)

        target_box = QVBoxLayout()
        target_box.setSpacing(4)
        target_box.addWidget(_label("每周目标（分钟）", hint=True))
        self.week_target_spin = QSpinBox()
        self.week_target_spin.setRange(0, 10080)
        self.week_target_spin.setSingleStep(30)
        target_box.addWidget(self.week_target_spin)
        row.addLayout(target_box, 1)
        box.addLayout(row)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.save_btn = QPushButton("保存目标")
        self.save_btn.setProperty("accent", True)
        self.save_btn.clicked.connect(self._on_save_goal)
        actions.addWidget(self.save_btn)
        self.status_label = _label("", hint=True)
        actions.addWidget(self.status_label, 1)
        box.addLayout(actions)
        self.body_layout.addWidget(card)

    def _build_subject_card(self) -> None:
        card = _card()
        box = QVBoxLayout(card)
        box.setContentsMargins(16, 14, 16, 14)
        box.setSpacing(8)
        box.addWidget(_label("科目配比", section=True))
        box.addWidget(_label("权重只影响提醒里「该多看哪科」的口径，不参与任何成绩计算。",
                             hint=True))
        self.subject_host = QWidget()
        self.subject_layout = QVBoxLayout(self.subject_host)
        self.subject_layout.setContentsMargins(0, 0, 0, 0)
        self.subject_layout.setSpacing(6)
        box.addWidget(self.subject_host)

        add_row = QHBoxLayout()
        add_row.setSpacing(8)
        self.subject_name_edit = QLineEdit()
        self.subject_name_edit.setPlaceholderText("科目名，如 数学一")
        add_row.addWidget(self.subject_name_edit, 2)
        self.subject_minutes_spin = QSpinBox()
        self.subject_minutes_spin.setRange(0, 10080)
        self.subject_minutes_spin.setSingleStep(30)
        self.subject_minutes_spin.setPrefix("周 ")
        self.subject_minutes_spin.setSuffix(" 分钟")
        add_row.addWidget(self.subject_minutes_spin, 1)
        self.subject_weight_spin = QSpinBox()
        self.subject_weight_spin.setRange(0, 100)
        self.subject_weight_spin.setSuffix(" 权重")
        add_row.addWidget(self.subject_weight_spin, 1)
        add_subject_btn = QPushButton("加入")
        add_subject_btn.clicked.connect(self._on_add_subject)
        add_row.addWidget(add_subject_btn)
        box.addLayout(add_row)
        self.body_layout.addWidget(card)

    def _build_milestone_card(self) -> None:
        card = _card()
        box = QVBoxLayout(card)
        box.setContentsMargins(16, 14, 16, 14)
        box.setSpacing(8)
        box.addWidget(_label("里程碑", section=True))
        self.milestone_host = QWidget()
        self.milestone_layout = QVBoxLayout(self.milestone_host)
        self.milestone_layout.setContentsMargins(0, 0, 0, 0)
        self.milestone_layout.setSpacing(6)
        box.addWidget(self.milestone_host)

        add_row = QHBoxLayout()
        add_row.setSpacing(8)
        self.milestone_edit = QLineEdit()
        self.milestone_edit.setPlaceholderText("新里程碑，如 408 第一轮过完")
        add_row.addWidget(self.milestone_edit, 2)
        self.milestone_date = QDateEdit()
        self.milestone_date.setCalendarPopup(True)
        self.milestone_date.setDisplayFormat("yyyy-MM-dd")
        self.milestone_date.setDate(QDate.currentDate())
        add_row.addWidget(self.milestone_date, 1)
        add_btn = QPushButton("添加")
        add_btn.clicked.connect(self._on_add_milestone)
        add_row.addWidget(add_btn)
        box.addLayout(add_row)
        self.body_layout.addWidget(card)

    def _build_review_card(self) -> None:
        card = _card()
        box = QVBoxLayout(card)
        box.setContentsMargins(16, 14, 16, 14)
        box.setSpacing(8)
        box.addWidget(_label("错题本", section=True))
        self.review_recap = _label("", hint=True)
        box.addWidget(self.review_recap)
        box.addWidget(_label("自己评，别客气：答不出来就点「重来」，间隔会立刻缩回去。",
                             hint=True))

        self.review_host = QWidget()
        self.review_layout = QVBoxLayout(self.review_host)
        self.review_layout.setContentsMargins(0, 0, 0, 0)
        self.review_layout.setSpacing(8)
        box.addWidget(self.review_host)

        front_row = QHBoxLayout()
        front_row.setSpacing(8)
        self.review_front_edit = QLineEdit()
        self.review_front_edit.setPlaceholderText("题目，如 408：LRU 缺页次数怎么算")
        front_row.addWidget(self.review_front_edit, 2)
        self.review_back_edit = QLineEdit()
        self.review_back_edit.setPlaceholderText("答案要点（可留空）")
        front_row.addWidget(self.review_back_edit, 2)
        add_btn = QPushButton("记一道")
        add_btn.clicked.connect(self._on_add_review)
        front_row.addWidget(add_btn)
        box.addLayout(front_row)
        self.body_layout.addWidget(card)

    def _build_nudge_card(self) -> None:
        """主动提醒开关（阶段B3 补口子）。

        三类提醒的开关原先只在 config.json 里，界面上没有任何入口——用户想
        静音只能手改文件。放在成长面板里而不是设置页：这是"成长档案"自己的
        行为能力，就近管，也不牵动设置页的行 claim 与行数预算。
        """
        card = _card()
        box = QVBoxLayout(card)
        box.setContentsMargins(16, 14, 16, 14)
        box.setSpacing(8)
        box.addWidget(_label("主动提醒", section=True))
        box.addWidget(_label(
            "关掉只影响桌宠开不开口，档案照常记。23:30–07:30 一律不打扰。", hint=True))

        self.nudge_master_check = QCheckBox("允许桌宠主动提醒（总闸）")
        self.nudge_master_check.toggled.connect(self._on_toggle_nudge_master)
        box.addWidget(self.nudge_master_check)

        #: 三类触发的勾选框（kind → QCheckBox）
        self.nudge_kind_checks: dict[str, QCheckBox] = {}
        for kind, title in (
                ("chore", "催事：今天还没记录专注时提一句"),
                ("review", "复盘：周日晚提一句看周复盘"),
                ("care", "状态关怀：当天心情很低时说一句陪着")):
            check = QCheckBox(title)
            check.toggled.connect(
                lambda checked, k=kind: self._on_toggle_nudge_kind(k, checked))
            box.addWidget(check)
            self.nudge_kind_checks[kind] = check

        self.body_layout.addWidget(card)

    def _build_footer(self) -> None:
        self.footer_label = _label(
            "主动提醒的开关就在上面「主动提醒」一栏；档案与错题本只存本机，不联网。",
            hint=True)
        self.body_layout.addWidget(self.footer_label)
        self.body_layout.addStretch(1)

    # ------------------------------------------------------------ 数据
    def _load_archive(self) -> dict:
        service = self.service
        if service is None:
            return {}
        try:
            return service.store.load()
        except Exception:  # noqa: BLE001
            logger.warning("读取成长档案失败，面板按空档案渲染", exc_info=True)
            return {}

    def refresh(self) -> None:
        archive = self._load_archive()
        self._data = dash.build_dashboard(archive)
        data = self._data

        if data.get("exam_date"):
            exam_text = f"考试日 {data['exam_date']}"
        else:
            exam_text = "还没设置考试日"
        self.countdown_label.setText(f"{data['countdown']} · {exam_text}")

        today_stats = data["today_stats"]
        self.focus_value.setText(str(today_stats["focus_minutes"]))
        self.distract_value.setText(str(today_stats["distractions"]))
        self.mood_value.setText(f"{today_stats['mood']:+d}")

        target = data["weekly_target"]
        self.week_bar.setValue(int(round(data["week_ratio"] * 100)))
        if target:
            self.week_label.setText(
                f"本周 {data['week_focus']} / {target} 分钟（{int(round(data['week_ratio'] * 100))}%）"
                f" · 连续 {data['streak_days']} 天 · 精力 {data['energy']}")
        else:
            self.week_label.setText(
                f"本周 {data['week_focus']} 分钟（还没设每周目标）"
                f" · 连续 {data['streak_days']} 天 · 精力 {data['energy']}")

        self.goal_edit.setText(data.get("goal") or "")
        if data.get("exam_date"):
            parsed = QDate.fromString(data["exam_date"], "yyyy-MM-dd")
            if parsed.isValid():
                self.exam_edit.setDate(parsed)
            else:
                self.exam_edit.setDate(QDate.currentDate())
        else:
            self.exam_edit.setDate(self.exam_edit.minimumDate())
        self.week_target_spin.setValue(int(data.get("weekly_target") or 0))

        presence = data.get("presence") or {}
        if presence.get("label"):
            self.presence_label.setText(f"{presence['label']}（{presence.get('reason', '')}）")
        else:
            self.presence_label.setText("")

        topics = data.get("topics") or []
        if topics:
            self.topics_label.setText(" · ".join(f"{name} ×{count}" for name, count in topics))
        else:
            self.topics_label.setText("还没攒够话题（聊过、且抽出关键词之后才会出现）")

        self._render_subjects(data.get("subjects") or [])
        self._render_milestones(data.get("milestones") or [])
        self._render_reviews(data.get("reviews") or {})
        self.refresh_nudge_switches()

    def _render_subjects(self, rows: list[dict]) -> None:
        while self.subject_layout.count():
            item = self.subject_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        if not rows:
            self.subject_layout.addWidget(_label("还没加科目。先加名字，权重可以后补。", hint=True))
            return
        for row in rows:
            line = QWidget()
            grid = QGridLayout(line)
            grid.setContentsMargins(0, 0, 0, 0)
            grid.setHorizontalSpacing(8)
            grid.setVerticalSpacing(2)
            name = _label(row["name"], section=True)
            grid.addWidget(name, 0, 0, 1, 2)
            hint_text = f"周 {row['weekly_minutes']} 分钟"
            if row["weight"]:
                hint_text += f" · 权重 {row['weight']}（{int(round(row['ratio'] * 100))}%）"
            else:
                hint_text += " · 未设权重"
            grid.addWidget(_label(hint_text, hint=True), 1, 0)
            bar = _bar(int(round(row["ratio"] * 100)) if row["weight"] else 0)
            grid.addWidget(bar, 1, 1)
            grid.setColumnStretch(1, 1)
            self.subject_layout.addWidget(line)

    def _render_milestones(self, rows: list[dict]) -> None:
        self._milestone_rows = list(rows)
        while self.milestone_layout.count():
            item = self.milestone_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        if not rows:
            self.milestone_layout.addWidget(
                _label("还没有里程碑。写一个能验收的结果，别写「努力学」。", hint=True))
            return
        for row in rows:
            line = QWidget()
            hbox = QHBoxLayout(line)
            hbox.setContentsMargins(0, 0, 0, 0)
            hbox.setSpacing(8)
            check = QCheckBox(row["title"])
            check.setChecked(row["done"])
            check.clicked.connect(
                lambda _checked=False, mid=row["id"]: self._on_toggle_milestone(mid))
            if row["done"]:
                check.setStyleSheet("color: #8e8e93; text-decoration: line-through;")
            hbox.addWidget(check, 1)

            if row["done"]:
                due_text = "已完成"
            elif row["due"]:
                due_text = f"{row['due']}（{row['days_left']} 天）" if row["days_left"] is not None else row["due"]
                if row["overdue"]:
                    due_text = f"已逾期 · {row['due']}"
            else:
                due_text = "无截止"
            due_label = _label(due_text, hint=True)
            hbox.addWidget(due_label)

            del_btn = QPushButton("删除")
            del_btn.setFixedWidth(64)
            del_btn.clicked.connect(
                lambda _checked=False, mid=row["id"]: self._on_remove_milestone(mid))
            hbox.addWidget(del_btn)
            self.milestone_layout.addWidget(line)

    def _render_reviews(self, data: dict) -> None:
        while self.review_layout.count():
            item = self.review_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        recap = data.get("text") or ""
        if not data.get("fsrs"):
            recap += "（未装 fsrs，按固定阶梯排期）"
        if data.get("done"):
            recap += f" · 累计复习 {data.get('done')} 次"
        self.review_recap.setText(recap)

        rows = data.get("cards") or []
        if not rows:
            self.review_layout.addWidget(
                _label("现在没有到期卡片。做题时记一条，比事后重看一遍强。", hint=True))
            return
        for row in rows:
            box = QVBoxLayout()
            box.setSpacing(4)
            head = QHBoxLayout()
            head.setSpacing(8)
            head.addWidget(_label(row["front"], section=True), 1)
            head.addWidget(_label(row["due_text"], hint=True))
            box.addLayout(head)
            if row.get("back"):
                box.addWidget(_label(row["back"], hint=True))
            if row.get("tags"):
                box.addWidget(_label(" ".join(f"#{tag}" for tag in row["tags"]), hint=True))

            grade_row = QHBoxLayout()
            grade_row.setSpacing(6)
            grade_row.addStretch(1)
            for grade, caption in (("again", "重来"), ("hard", "困难"),
                                   ("good", "一般"), ("easy", "简单")):
                btn = QPushButton(caption)
                btn.setFixedWidth(64)
                btn.clicked.connect(
                    lambda _checked=False, cid=row["id"], g=grade: self._on_grade_review(cid, g))
                grade_row.addWidget(btn)
            box.addLayout(grade_row)
            self.review_layout.addLayout(box)

    # ------------------------------------------------------------ 交互
    def _flash(self, text: str) -> None:
        self.status_label.setText(text)
        QTimer.singleShot(2600, lambda: self.status_label.setText(""))

    def _on_save_goal(self) -> None:
        service = self.service
        if service is None:
            self._flash("预览模式，未接档案")
            return
        exam = self.exam_edit.date()
        exam_text = "" if exam == self.exam_edit.minimumDate() else exam.toString("yyyy-MM-dd")
        archive = service.update_learning(
            goal=self.goal_edit.text(),
            exam_date=exam_text,
            weekly_minutes=int(self.week_target_spin.value()),
        )
        if archive is None:
            self._flash("保存失败，已保持原样")
            return
        self.refresh()
        self._flash("已保存")

    def _on_add_subject(self) -> None:
        name = self.subject_name_edit.text().strip()
        if not name:
            self._flash("先写科目名")
            return
        service = self.service
        if service is None:
            self._flash("预览模式，未接档案")
            return
        rows = [{"name": r["name"], "weekly_minutes": r["weekly_minutes"],
                 "weight": r["weight"]} for r in self._data.get("subjects") or []]
        rows.append({"name": name, "weekly_minutes": int(self.subject_minutes_spin.value()),
                     "weight": int(self.subject_weight_spin.value())})
        archive = service.update_learning(subjects=rows)
        if archive is None:
            self._flash("添加失败")
            return
        self.subject_name_edit.clear()
        self.subject_minutes_spin.setValue(0)
        self.subject_weight_spin.setValue(0)
        self.refresh()
        self._flash(f"已加入「{name}」")

    def _on_add_review(self) -> None:
        front = self.review_front_edit.text().strip()
        if not front:
            self._flash("先写题目")
            return
        service = self.service
        if service is None:
            self._flash("预览模式，未接档案")
            return
        if service.add_review_card(front, self.review_back_edit.text()) is None:
            self._flash("记录失败")
            return
        self.review_front_edit.clear()
        self.review_back_edit.clear()
        self.refresh()
        self._flash("已记入错题本")

    def _on_grade_review(self, card_id: str, grade: str) -> None:
        service = self.service
        if service is None:
            return
        if service.grade_review(card_id, grade) is None:
            self._flash("评价失败")
            return
        self.refresh()

    def _on_add_milestone(self) -> None:
        title = self.milestone_edit.text().strip()
        if not title:
            self._flash("先写里程碑内容")
            return
        service = self.service
        if service is None:
            self._flash("预览模式，未接档案")
            return
        due = self.milestone_date.date().toString("yyyy-MM-dd")
        archive = service.add_milestone(title, due)
        if archive is None:
            self._flash("添加失败")
            return
        self.milestone_edit.clear()
        self.refresh()
        self._flash("已添加")

    # ------------------------------------------------- 主动提醒开关（B3）
    def refresh_nudge_switches(self) -> None:
        """把落盘的开关状态同步到勾选框，并按总闸决定分类开关是否可点。

        只读预览（``service`` 为 None）时全部置灰：没有后端可写，点了就是骗人。
        统一 blockSignals，避免 setChecked 反过来触发写盘。
        """
        service = self.service
        readonly = service is None
        master = True
        kinds = {"chore": True, "review": True, "care": False}
        if not readonly:
            try:
                master = bool(service.nudge_enabled())
                switches = service.proactive_switches()
                kinds = switches.as_dict()
            except Exception:  # noqa: BLE001
                logger.warning("读取成长提醒开关失败，按默认显示", exc_info=True)

        def _set(check: QCheckBox, value: bool, enabled: bool) -> None:
            check.blockSignals(True)
            try:
                check.setChecked(bool(value))
                check.setEnabled(enabled)
            finally:
                check.blockSignals(False)

        _set(self.nudge_master_check, master, not readonly)
        for kind, check in self.nudge_kind_checks.items():
            # 总闸关了就把三类一起置灰：留着能勾会让人以为"关了总闸还在提醒"。
            _set(check, kinds.get(kind, False), (not readonly) and master)

    def _on_toggle_nudge_master(self, checked: bool) -> None:
        service = self.service
        if service is None:
            return
        try:
            service.set_nudge_enabled(bool(checked))
        except Exception:  # noqa: BLE001
            logger.warning("写入成长提醒总闸失败", exc_info=True)
        # 无论成功失败都以落盘值为准回显：失败时勾选会自己弹回去，不假装已保存。
        self.refresh_nudge_switches()

    def _on_toggle_nudge_kind(self, kind: str, checked: bool) -> None:
        service = self.service
        if service is None:
            return
        try:
            service.set_proactive_switch(kind, bool(checked))
        except Exception:  # noqa: BLE001
            logger.warning("写入成长提醒分类开关失败：%s", kind, exc_info=True)
        self.refresh_nudge_switches()

    def _on_toggle_milestone(self, milestone_id: str) -> None:
        service = self.service
        if service is None:
            return
        service.toggle_milestone(milestone_id)
        self.refresh()

    def _on_remove_milestone(self, milestone_id: str) -> None:
        service = self.service
        if service is None:
            return
        service.remove_milestone(milestone_id)
        self.refresh()
