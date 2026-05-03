#!/usr/bin/env python3
"""
Qt 视觉参考 Demo

用途：
- 这不是功能界面，只是“代码级 UI 风格参考”。
- 所有按钮、切换项、菜单箭头都不绑定真实行为。
- 目标是给 agent_qt.py 的后续视觉迭代提供一份可运行、可读的单文件样式样板。

运行：
    python3 design_refs/qt_glass_reference_demo.py
"""

from __future__ import annotations

import sys
from typing import Iterable

from PySide6.QtCore import QPointF, QRectF, Qt, QSize
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpacerItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


PALETTE = {
    "bg0": "#f7f9ff",
    "bg1": "#f3f1ff",
    "bg2": "#eef6ff",
    "panel": (255, 255, 255, 176),
    "panel_soft": (255, 255, 255, 138),
    "panel_line": (255, 255, 255, 210),
    "panel_line_2": (210, 224, 255, 168),
    "text": "#22304f",
    "text_soft": "#7482a3",
    "text_faint": "#9aa7c4",
    "accent": "#7450ff",
    "accent_2": "#8b6dff",
    "accent_3": "#bcafff",
    "danger": "#ff5b58",
    "danger_soft": "#fff4f4",
    "success": "#16a36a",
    "chip": (248, 250, 255, 196),
    "terminal_bg": (251, 252, 255, 200),
    "terminal_panel": (255, 255, 255, 166),
    "terminal_border": (210, 223, 250, 170),
}


def rgba(value: tuple[int, int, int, int]) -> str:
    return f"rgba({value[0]}, {value[1]}, {value[2]}, {value[3]})"


def shadow(
    widget: QWidget,
    *,
    blur: int,
    x: int = 0,
    y: int = 10,
    color: tuple[int, int, int, int] = (134, 153, 196, 55),
) -> None:
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(blur)
    effect.setOffset(x, y)
    effect.setColor(QColor(*color))
    widget.setGraphicsEffect(effect)


class GlowButton(QPushButton):
    def __init__(self, text: str, *, accent: bool = False, danger: bool = False, ghost: bool = False):
        super().__init__(text)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(44)
        self.setMinimumWidth(96)
        self.setFont(QFont("PingFang SC", 11, QFont.Weight.Black))
        base_bg = rgba(PALETTE["panel"])
        border = rgba(PALETTE["panel_line_2"])
        text = PALETTE["text"]
        hover = rgba((255, 255, 255, 210))
        if accent:
            base_bg = (
                "qlineargradient(x1:0,y1:0,x2:1,y2:1,"
                "stop:0 rgba(121,81,255,245), stop:1 rgba(94,63,244,245))"
            )
            border = "rgba(209,196,255,210)"
            text = "#ffffff"
            hover = (
                "qlineargradient(x1:0,y1:0,x2:1,y2:1,"
                "stop:0 rgba(135,95,255,255), stop:1 rgba(106,74,250,255))"
            )
            shadow(self, blur=30, y=8, color=(111, 79, 255, 90))
        elif danger:
            base_bg = rgba((255, 255, 255, 208))
            border = "rgba(255,155,155,180)"
            text = PALETTE["danger"]
            hover = rgba((255, 245, 245, 230))
        elif ghost:
            base_bg = rgba((255, 255, 255, 132))
            border = rgba((214, 224, 248, 185))
            text = PALETTE["text_soft"]
            hover = rgba((255, 255, 255, 176))
        self.setStyleSheet(
            f"""
            QPushButton {{
                background: {base_bg};
                color: {text};
                border: 1px solid {border};
                border-radius: 15px;
                padding: 0 18px;
            }}
            QPushButton:hover {{
                background: {hover};
            }}
            QPushButton:pressed {{
                padding-top: 1px;
            }}
            """
        )


class ToolbarChip(QPushButton):
    def __init__(self, text: str, *, active: bool = False):
        super().__init__(text)
        self.setCursor(Qt.PointingHandCursor)
        self.setFont(QFont("PingFang SC", 11, QFont.Weight.Black))
        self.setFixedHeight(42)
        self.setMinimumWidth(110)
        bg = (
            "qlineargradient(x1:0,y1:0,x2:1,y2:1,"
            "stop:0 rgba(255,255,255,246), stop:1 rgba(245,240,255,250))"
            if active
            else rgba(PALETTE["chip"])
        )
        border = (
            "rgba(202,188,255,220)" if active else rgba((214, 224, 248, 180))
        )
        text = PALETTE["accent"] if active else PALETTE["text"]
        self.setStyleSheet(
            f"""
            QPushButton {{
                background: {bg};
                color: {text};
                border: 1px solid {border};
                border-radius: 16px;
                padding: 0 20px;
            }}
            """
        )
        if active:
            shadow(self, blur=24, y=7, color=(144, 117, 255, 70))


class GlassPanel(QFrame):
    def __init__(self, *, radius: int = 28, padding: int = 18):
        super().__init__()
        self.radius = radius
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(padding, padding, padding, padding)
        self._layout.setSpacing(14)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        self.setStyleSheet("background: transparent;")
        shadow(self, blur=46, y=18, color=(180, 195, 236, 50))

    @property
    def body(self) -> QVBoxLayout:
        return self._layout

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.Antialiasing)
            rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
            path = QPainterPath()
            path.addRoundedRect(rect, self.radius, self.radius)

            fill = QLinearGradient(rect.topLeft(), rect.bottomLeft())
            fill.setColorAt(0.0, QColor(*PALETTE["panel"]))
            fill.setColorAt(1.0, QColor(*PALETTE["panel_soft"]))
            painter.fillPath(path, fill)

            line = QLinearGradient(rect.topLeft(), rect.bottomRight())
            line.setColorAt(0.0, QColor(*PALETTE["panel_line"]))
            line.setColorAt(0.55, QColor(*PALETTE["panel_line_2"]))
            line.setColorAt(1.0, QColor(255, 255, 255, 160))
            painter.setPen(QPen(line, 1.2))
            painter.drawPath(path)
        finally:
            painter.end()


class SidebarSkillCard(GlassPanel):
    def __init__(self, title: str, desc: str, badge: str, *, active: bool = False):
        super().__init__(radius=24, padding=18)
        if active:
            shadow(self, blur=38, y=16, color=(128, 96, 255, 78))
        row = QHBoxLayout()
        row.setSpacing(12)

        icon = QLabel(badge)
        icon.setAlignment(Qt.AlignCenter)
        icon.setFixedSize(46, 46)
        icon.setStyleSheet(
            f"""
            QLabel {{
                background: rgba(255,255,255,0.78);
                color: {PALETTE['accent'] if active else PALETTE['text_soft']};
                border: 1px solid rgba(215,224,247,0.86);
                border-radius: 14px;
                font-size: 18px;
                font-weight: 800;
            }}
            """
        )
        row.addWidget(icon, 0, Qt.AlignTop)

        col = QVBoxLayout()
        col.setSpacing(8)
        title_label = QLabel(title)
        title_label.setWordWrap(True)
        title_label.setStyleSheet(
            f"color: {PALETTE['text']}; font-size: 14px; font-weight: 900; background: transparent;"
        )
        desc_label = QLabel(desc)
        desc_label.setWordWrap(True)
        desc_label.setStyleSheet(
            f"color: {PALETTE['text_soft']}; font-size: 12px; line-height: 1.45; background: transparent;"
        )
        col.addWidget(title_label)
        col.addWidget(desc_label)
        row.addLayout(col, 1)

        if active:
            sparkle = QLabel("✦")
            sparkle.setStyleSheet(
                f"color: {PALETTE['accent']}; font-size: 15px; font-weight: 900; background: transparent;"
            )
            row.addWidget(sparkle, 0, Qt.AlignTop)

        self.body.addLayout(row)


class CodeCard(GlassPanel):
    def __init__(self, title: str, body_lines: Iterable[str], *, compact: bool = False):
        super().__init__(radius=24, padding=18)
        title_label = QLabel(title)
        title_label.setStyleSheet(
            f"color: {PALETTE['text']}; font-size: 13px; font-weight: 900; background: transparent;"
        )
        self.body.addWidget(title_label)

        shell = QFrame()
        shell.setStyleSheet(
            f"""
            QFrame {{
                background: {rgba(PALETTE['terminal_bg'])};
                border: 1px solid {rgba(PALETTE['terminal_border'])};
                border-radius: 20px;
            }}
            """
        )
        shadow(shell, blur=22, y=8, color=(182, 196, 232, 44))
        shell_layout = QVBoxLayout(shell)
        shell_layout.setContentsMargins(18, 16, 18, 16)
        shell_layout.setSpacing(8)

        top = QHBoxLayout()
        top.setSpacing(10)
        prefix = QLabel("[1]  ▓  grep -n ...")
        prefix.setStyleSheet(
            f"color: {PALETTE['text']}; font-size: 13px; font-weight: 700; background: transparent;"
        )
        top.addWidget(prefix)
        top.addStretch()
        copy = QToolButton()
        copy.setText("⧉")
        copy.setCursor(Qt.PointingHandCursor)
        copy.setFixedSize(28, 28)
        copy.setStyleSheet(
            f"""
            QToolButton {{
                background: rgba(255,255,255,0.76);
                color: {PALETTE['text_soft']};
                border: 1px solid rgba(214,224,248,0.9);
                border-radius: 10px;
                font-size: 14px;
                font-weight: 800;
            }}
            """
        )
        top.addWidget(copy)
        shell_layout.addLayout(top)

        for line in body_lines:
            label = QLabel(line)
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            label.setStyleSheet(
                f"color: {PALETTE['text']}; font-size: {12 if compact else 13}px; "
                "font-family: Menlo, Monaco, monospace; background: transparent;"
            )
            shell_layout.addWidget(label)

        self.body.addWidget(shell)


class SegmentBar(GlassPanel):
    def __init__(self):
        super().__init__(radius=24, padding=14)
        row = QHBoxLayout()
        row.setSpacing(14)
        row.addWidget(ToolbarChip("专家模式 · 约45k", active=True))
        row.addWidget(ToolbarChip("技能 ▾"))
        row.addStretch()
        row.addWidget(GlowButton("↑", accent=True))
        self.body.addLayout(row)


class DemoWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Agent 控制台 · 玻璃风格参考 Demo")
        self.resize(1460, 1080)
        self.setMinimumSize(QSize(1320, 900))

        root = QWidget()
        self.setCentralWidget(root)
        shell = QHBoxLayout(root)
        shell.setContentsMargins(24, 22, 24, 20)
        shell.setSpacing(18)

        sidebar = self.build_sidebar()
        shell.addWidget(sidebar, 0)

        workspace = self.build_workspace()
        shell.addWidget(workspace, 1)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.Antialiasing)

            rect = self.rect()
            bg = QLinearGradient(QPointF(0, 0), QPointF(rect.width(), rect.height()))
            bg.setColorAt(0.0, QColor(PALETTE["bg0"]))
            bg.setColorAt(0.42, QColor(PALETTE["bg1"]))
            bg.setColorAt(1.0, QColor(PALETTE["bg2"]))
            painter.fillRect(rect, bg)

            for x, y, w, h, c0, c1, alpha in (
                (-80, rect.height() - 220, 360, 240, QColor(123, 97, 255, 120), QColor(123, 97, 255, 0), 1.0),
                (rect.width() - 340, rect.height() - 240, 360, 240, QColor(139, 188, 255, 98), QColor(139, 188, 255, 0), 1.0),
                (rect.width() - 280, 30, 260, 180, QColor(156, 132, 255, 62), QColor(156, 132, 255, 0), 1.0),
            ):
                grad = QRadialGradient(QPointF(x + w / 2, y + h / 2), max(w, h) / 2)
                grad.setColorAt(0.0, c0)
                fade = QColor(c1)
                fade.setAlphaF(alpha * 0.0)
                grad.setColorAt(1.0, fade)
                painter.setPen(Qt.NoPen)
                painter.setBrush(grad)
                painter.drawEllipse(QRectF(x, y, w, h))

            for sx, sy, r in ((90, rect.height() - 84, 4), (122, rect.height() - 56, 2.5), (145, rect.height() - 96, 2.8)):
                painter.setBrush(QColor(255, 255, 255, 195))
                painter.setPen(Qt.NoPen)
                painter.drawEllipse(QRectF(sx, sy, r, r))
        finally:
            painter.end()

    def build_sidebar(self) -> QWidget:
        panel = GlassPanel(radius=30, padding=18)
        panel.setFixedWidth(320)

        tabs = QHBoxLayout()
        tabs.setSpacing(10)
        tabs.addWidget(ToolbarChip("会话列表"))
        tabs.addWidget(ToolbarChip("项目文件"))
        tabs.addWidget(ToolbarChip("技能列表", active=True))
        panel.body.addLayout(tabs)

        workspace_chip = GlassPanel(radius=18, padding=14)
        workspace_chip.body.setSpacing(0)
        workspace_label = QLabel("📁  /Users/pippo/Desktop/my-project")
        workspace_label.setStyleSheet(
            f"color: {PALETTE['text_soft']}; font-size: 12px; font-weight: 700; background: transparent;"
        )
        workspace_chip.body.addWidget(workspace_label)
        panel.body.addWidget(workspace_chip)

        section = QLabel("技能")
        section.setStyleSheet(f"color: {PALETTE['text']}; font-size: 15px; font-weight: 900; background: transparent;")
        panel.body.addWidget(section)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("background: transparent; border: none;")
        body = QWidget()
        col = QVBoxLayout(body)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(14)
        col.addWidget(
            SidebarSkillCard(
                "creative-writing",
                "用于需要帮助进行创意写作、故事构思、角色塑造、情节设计或文章打磨的场景。",
                "✎",
                active=True,
            )
        )
        col.addWidget(
            SidebarSkillCard(
                "Frontend Design",
                "用于构建 Web 组件、页面或应用原型，强调高质感布局与精致视觉表达。",
                "⌘",
            )
        )
        col.addWidget(
            SidebarSkillCard(
                "Game Engine",
                "适用于创建小游戏、构建引擎、整理玩法数据与交互脚本。",
                "◉",
            )
        )
        col.addWidget(
            SidebarSkillCard(
                "Grill Me",
                "持续追问与压力测试用户需求，帮助收敛方案、拆解决策分支。",
                "♨",
            )
        )
        col.addItem(QSpacerItem(0, 0, QSizePolicy.Minimum, QSizePolicy.Expanding))
        scroll.setWidget(body)
        panel.body.addWidget(scroll, 1)

        add_btn = GlowButton("+  添加技能", ghost=True)
        add_btn.setFixedHeight(46)
        panel.body.addWidget(add_btn)
        return panel

    def build_workspace(self) -> QWidget:
        panel = GlassPanel(radius=32, padding=20)

        header = QHBoxLayout()
        header.setSpacing(12)
        back = GlowButton("‹", ghost=True)
        back.setFixedWidth(46)
        title = QLabel("工作区")
        title.setStyleSheet(
            f"color: {PALETTE['text']}; font-size: 24px; font-weight: 900; background: transparent;"
        )
        path = GlassPanel(radius=18, padding=12)
        path.body.setSpacing(0)
        path.body.addWidget(
            QLabel("📁  /Users/pippo/Desktop/my-project", styleSheet=f"color: {PALETTE['text_soft']}; font-size: 13px; font-weight: 700; background: transparent;")
        )
        header.addWidget(back, 0)
        header.addWidget(title, 0)
        header.addWidget(path, 1)
        header.addStretch()
        header.addWidget(GlowButton("⇪  分享", accent=True))
        header.addWidget(GlowButton("☼", ghost=True))
        header.addWidget(GlowButton("🗑 清空", danger=True))
        panel.body.addLayout(header)

        history_hint = QLabel("已折叠较早 180 条历史。完整历史仍会进入自动化上下文。")
        history_hint.setAlignment(Qt.AlignCenter)
        history_hint.setStyleSheet(
            f"color: {PALETTE['text_faint']}; font-size: 12px; font-weight: 700; background: transparent;"
        )
        panel.body.addWidget(history_hint)

        panel.body.addWidget(
            CodeCard(
                "执行结果",
                [
                    "39:          for (let v = item.max_score; v >= 0; v -= 0.5) {",
                    "40:              const label = v < item.max_score ? `${v} ${item.max_score - v分}` : `${v}（满分）`;",
                    "45:              <span class=\"item-name\">${item.desc}</span>",
                    "46:              <span class=\"item-max\">满分 ${item.max_score} 分</span>",
                    "47:              <select data-key=\"${key}\" data-max=\"${item.max_score}\">${options}</select>",
                ],
            )
        )

        ai_card = GlassPanel(radius=26, padding=20)
        ai_title = QLabel("AI 输出")
        ai_title.setStyleSheet(f"color: {PALETTE['text']}; font-size: 18px; font-weight: 900; background: transparent;")
        ai_card.body.addWidget(ai_title)
        body = QLabel(
            "看日志，app.js 中 item.name 已改为 item.desc，但 undefined 问题还在。检查发现 item.max_score 没问题，"
            "问题出在生成 options 的循环中 item.max_score 是数字，但从日志看到的 undefined 出现在显示条目文本里。"
        )
        body.setWordWrap(True)
        body.setStyleSheet(
            f"color: {PALETTE['text']}; font-size: 14px; line-height: 1.55; background: transparent;"
        )
        ai_card.body.addWidget(body)
        copy_row = QHBoxLayout()
        copy_row.addStretch()
        copy_row.addWidget(GlowButton("✎  复制 AI 输出", ghost=True))
        ai_card.body.addLayout(copy_row)

        collapsed = QFrame()
        collapsed.setStyleSheet(
            f"""
            QFrame {{
                background: rgba(244,247,255,0.72);
                border: 1px solid rgba(210,222,248,0.92);
                border-radius: 16px;
            }}
            """
        )
        inner = QHBoxLayout(collapsed)
        inner.setContentsMargins(16, 12, 16, 12)
        inner.addWidget(QLabel("text undefined", styleSheet=f"color: {PALETTE['text_soft']}; font-size: 13px; background: transparent;"))
        inner.addStretch()
        inner.addWidget(QLabel("▸", styleSheet=f"color: {PALETTE['text_soft']}; font-size: 15px; font-weight: 900; background: transparent;"))
        ai_card.body.addWidget(collapsed)
        panel.body.addWidget(ai_card)

        panel.body.addWidget(SegmentBar())

        terminal = GlassPanel(radius=28, padding=14)
        terminal_header = QHBoxLayout()
        terminal_header.setSpacing(10)
        terminal_header.addWidget(GlowButton("─", ghost=True))
        term_title = QLabel("终端  1 个进程")
        term_title.setStyleSheet(
            f"color: {PALETTE['text']}; font-size: 16px; font-weight: 900; background: transparent;"
        )
        terminal_header.addWidget(term_title)
        terminal_header.addStretch()
        terminal.body.addLayout(terminal_header)

        tabs = QHBoxLayout()
        tabs.setSpacing(8)
        tabs.addWidget(ToolbarChip("▣  my-project", active=True))
        tabs.addWidget(ToolbarChip("+"))
        tabs.addStretch()
        terminal.body.addLayout(tabs)

        term_shell = QFrame()
        term_shell.setStyleSheet(
            f"""
            QFrame {{
                background: {rgba(PALETTE['terminal_bg'])};
                border: 1px solid rgba(209,222,248,0.9);
                border-radius: 18px;
            }}
            """
        )
        term_layout = QVBoxLayout(term_shell)
        term_layout.setContentsMargins(18, 18, 18, 18)
        term_layout.setSpacing(5)
        for line in (
            "# shell: my-project",
            "# cwd: /Users/pippo/Desktop/my-project",
            "# pid: 39545",
            "# terminal_id: 20260503-095102-c2fb89df",
            "$ ",
        ):
            lbl = QLabel(line)
            lbl.setStyleSheet(
                f"color: {PALETTE['text']}; font-size: 13px; font-family: Menlo, Monaco, monospace; background: transparent;"
            )
            term_layout.addWidget(lbl)
        copy_log = QHBoxLayout()
        copy_log.addStretch()
        copy_log.addWidget(GlowButton("复制日志", ghost=True))
        term_layout.addLayout(copy_log)
        terminal.body.addWidget(term_shell)

        panel.body.addWidget(terminal)
        return panel


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setFont(QFont("PingFang SC", 12))
    window = DemoWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
