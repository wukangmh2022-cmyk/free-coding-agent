#!/usr/bin/env python3
"""Probe Qt streaming-render jank with large AgentQT-like text.

This is a standalone diagnostic tool. It does not import agent_qt.py and does
not change application state. The default source is the real long session
export used while investigating UI stalls.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)


DEFAULT_SOURCE = Path(
    "/Users/pippo/.agent_qt/projects/my-project-41f28478cd85dcdc/exports/"
    "agent-qt-session-20260505-104844-20260505-113001.txt"
)

COMPLETION_LINE_RE = re.compile(r"^\s*AGENT_DONE\s*$")
LOW_VALUE_CONTEXT_START = "<<<AGENT_QT_LOW_VALUE_CONTEXT_START"
LOW_VALUE_CONTEXT_END = "<<<AGENT_QT_LOW_VALUE_CONTEXT_END>>>"
AGENT_QT_HIDDEN_BLOCK_RE = re.compile(r"<agent_qt_hidden\b[^>]*>.*?</agent_qt_hidden>", re.I | re.S)


def strip_agent_qt_hidden_blocks(text: str) -> str:
    return AGENT_QT_HIDDEN_BLOCK_RE.sub("", str(text or ""))


def mask_low_value_context_markers_for_display(text: str) -> str:
    lines: list[str] = []
    current_kind = "low_value"
    for line in strip_agent_qt_hidden_blocks(text).splitlines(keepends=True):
        body = line[:-1] if line.endswith("\n") else line
        newline = "\n" if line.endswith("\n") else ""
        stripped = body.strip()
        leading = len(body) - len(body.lstrip())
        trailing = len(body) - len(body.rstrip())
        if stripped.startswith(LOW_VALUE_CONTEXT_START):
            match = re.search(r"\bkind=([A-Za-z0-9_-]+)", stripped)
            current_kind = match.group(1) if match else "low_value"
            lines.append((" " * leading) + f"<<< {current_kind} >>>" + (" " * trailing) + newline)
        elif stripped == LOW_VALUE_CONTEXT_END:
            lines.append((" " * leading) + f"<<< /{current_kind or 'low_value'} >>>" + (" " * trailing) + newline)
            current_kind = "low_value"
        else:
            lines.append(line)
    return "".join(lines)


def split_markdown_fenced_blocks(text: str) -> list[dict[str, str]]:
    """Copy the app's fenced-block scanner closely enough for perf probing."""

    parts: list[dict[str, str]] = []
    lines = (text or "").splitlines(keepends=True)
    buffer: list[str] = []
    code_buffer: list[str] = []
    in_code = False
    code_lang = ""
    fence_char = ""
    fence_len = 0
    markdown_outer_fence = False
    nested_markdown_fence_depth = 1

    def flush_markdown() -> None:
        nonlocal buffer
        if buffer:
            parts.append({"type": "markdown", "text": "".join(buffer)})
            buffer = []

    def flush_code() -> None:
        nonlocal code_buffer, code_lang, fence_char, fence_len, markdown_outer_fence, nested_markdown_fence_depth
        parts.append({"type": "code", "lang": code_lang.strip(), "text": "".join(code_buffer).rstrip("\n")})
        code_buffer = []
        code_lang = ""
        fence_char = ""
        fence_len = 0
        markdown_outer_fence = False
        nested_markdown_fence_depth = 1

    def opening_fence(line: str):
        return re.match(r"^\s{0,3}([`~]{3,})([^\r\n]*)\s*$", line.rstrip("\n\r"))

    def is_closing_fence(line: str) -> bool:
        if not fence_char or fence_len <= 0:
            return False
        pattern = rf"^\s{{0,3}}{re.escape(fence_char)}{{{fence_len},}}\s*$"
        return re.match(pattern, line.rstrip("\n\r")) is not None

    def matching_fence_candidate(line: str):
        match = opening_fence(line)
        if not match:
            return None
        fence = match.group(1)
        if not fence_char or fence[0] != fence_char or len(fence) < fence_len:
            return None
        return match

    def has_future_matching_fence(start_index: int) -> bool:
        lookahead = start_index
        while lookahead < len(lines):
            if matching_fence_candidate(lines[lookahead]):
                return True
            lookahead += 1
        return False

    index = 0
    while index < len(lines):
        line = lines[index]
        if in_code:
            if markdown_outer_fence:
                fence_match = matching_fence_candidate(line)
                if fence_match:
                    if nested_markdown_fence_depth <= 1 and not has_future_matching_fence(index + 1):
                        flush_code()
                        in_code = False
                        index += 1
                        continue
                    if nested_markdown_fence_depth <= 1:
                        nested_markdown_fence_depth = 2
                    else:
                        nested_markdown_fence_depth -= 1
                    code_buffer.append(line)
                    index += 1
                    continue
            if is_closing_fence(line):
                flush_code()
                in_code = False
                index += 1
                continue
            if COMPLETION_LINE_RE.match(line):
                flush_code()
                in_code = False
                buffer.append(line)
                index += 1
                continue
            code_buffer.append(line)
            index += 1
            continue

        match = opening_fence(line)
        if match:
            flush_markdown()
            in_code = True
            fence = match.group(1)
            fence_char = fence[0]
            fence_len = len(fence)
            code_lang = (match.group(2) or "").strip().split(maxsplit=1)[0] if (match.group(2) or "").strip() else ""
            markdown_outer_fence = code_lang.lower() in {"markdown", "md"}
            nested_markdown_fence_depth = 1
            index += 1
            continue
        buffer.append(line)
        index += 1

    if in_code:
        flush_code()
    elif index >= len(lines):
        flush_markdown()
    return [part for part in parts if part.get("text", "").strip()]


def markdown_with_pipe_tables_to_html(markdown_text: str) -> str:
    """Lightweight stand-in for app markdown/html conversion cost."""

    text = str(markdown_text or "").lstrip("\r\n")
    # Common app-side sanitation/rendering substitutions. This is intentionally
    # regex-heavy to exercise the same class of CPU work as live rendering.
    text = re.sub(r"&", "&amp;", text)
    text = re.sub(r"<", "&lt;", text)
    text = re.sub(r">", "&gt;", text)
    text = re.sub(r"`([^`\n]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*([^*\n]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?m)^\s*[-*]\s+(.+)$", r"<li>\1</li>", text)
    text = re.sub(r"(?m)^(#{1,6})\s+(.+)$", lambda m: f"<h{len(m.group(1))}>{m.group(2)}</h{len(m.group(1))}>", text)
    if "|" in text:
        # Simulate pipe-table detection by walking all lines.
        rows = [line for line in text.splitlines() if line.count("|") >= 2]
        if rows:
            text += "\n" + "".join(f"<!-- table-row {len(row)} -->" for row in rows)
    return text.replace("\n", "<br>")


def estimate_wrapped_text_height(text: str, metrics, width: int, max_visual_lines: int | None = None) -> int:
    """Approximate the app's wrapped-height scan cost."""

    if not text:
        return metrics.lineSpacing() + 10
    average_char_width = max(1, metrics.averageCharWidth())
    chars_per_line = max(1, width // average_char_width)
    visual_lines = 0
    for line in text.splitlines() or [""]:
        visual_lines += max(1, (len(line) + chars_per_line - 1) // chars_per_line)
        if max_visual_lines is not None and visual_lines >= max_visual_lines:
            visual_lines = max_visual_lines
            break
    return visual_lines * metrics.lineSpacing() + 26


def load_payload(path: Path, amplify: int, max_chars: int) -> str:
    if path.exists():
        text = path.read_text(errors="replace")
    else:
        text = (
            "AgentQT stream-render fallback payload.\n\n"
            "```bash\n"
            "ls -d /Users/pippo/Desktop/my-project/test_* "
            "'/Users/pippo/Desktop/my-project/test space path' 2>&1\n"
            "```\n\n"
            "Execution log:\n"
            + "\n".join(f"[{i}] line {i:04d}: simulated provider output and execution result" for i in range(5000))
        )
    text = text * max(1, amplify)
    if max_chars > 0:
        text = text[:max_chars]
    return text


def parse_export_sections(text: str) -> list[tuple[str, str]]:
    pattern = re.compile(r"^===== (用户需求|AI 输出|执行结果) =====\s*$", re.M)
    matches = list(pattern.finditer(text))
    sections: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        role_name = match.group(1)
        role = {"用户需求": "user", "AI 输出": "ai", "执行结果": "result"}[role_name]
        body = text[start:end].strip()
        if body:
            sections.append((role, body))
    return sections


class StreamRenderProbe(QWidget):
    def __init__(self, payload: str, sections: list[tuple[str, str]], args: argparse.Namespace):
        super().__init__()
        self.args = args
        self.payload = payload
        self.sections = sections or [("result", payload)]
        self.pos = 0
        self.total_streamed_chars = 0
        self.section_index = 0
        self.section_pos = 0
        self.last_frame_at = time.perf_counter()
        self.frame_count = 0
        self.lag_count = 0
        self.max_lag_ms = 0.0
        self.flush_count = 0
        self.total_flush_ms = 0.0
        self.max_flush_ms = 0.0
        self.current_text = ""
        self.streaming_card: QFrame | None = None
        self.streaming_editor: QPlainTextEdit | QTextBrowser | QLabel | None = None
        self.log_path = Path(args.log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_file = self.log_path.open("w", encoding="utf-8")

        self.setWindowTitle("AgentQT streaming render probe")
        self.resize(1180, 820)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        controls = QHBoxLayout()
        controls.setSpacing(8)
        root.addLayout(controls)

        self.mode_combo = QComboBox()
        self.mode_combo.addItems(
            [
                "baseline: full setPlainText + height scan",
                "append: insert delta + height scan",
                "append: insert delta + fixed height",
                "markdown: full setMarkdown",
            ]
        )
        controls.addWidget(QLabel("Mode"))
        controls.addWidget(self.mode_combo, 1)

        self.chunk_spin = QSpinBox()
        self.chunk_spin.setRange(1, 32768)
        self.chunk_spin.setValue(args.chunk_chars)
        self.chunk_spin.setSingleStep(12)
        controls.addWidget(QLabel("Chunk chars"))
        controls.addWidget(self.chunk_spin)

        self.interval_spin = QSpinBox()
        self.interval_spin.setRange(0, 1000)
        self.interval_spin.setValue(args.interval_ms)
        self.interval_spin.setSingleStep(5)
        controls.addWidget(QLabel("Interval ms"))
        controls.addWidget(self.interval_spin)

        self.preload_spin = QSpinBox()
        self.preload_spin.setRange(0, 500)
        self.preload_spin.setValue(args.preload_cards)
        self.preload_spin.setSingleStep(4)
        controls.addWidget(QLabel("Preload cards"))
        controls.addWidget(self.preload_spin)

        self.outer_scroll_checkbox = QCheckBox("App-like outer scroll")
        self.outer_scroll_checkbox.setChecked(True)
        controls.addWidget(self.outer_scroll_checkbox)

        self.loop_checkbox = QCheckBox("Loop add cards")
        self.loop_checkbox.setChecked(not args.no_loop)
        controls.addWidget(self.loop_checkbox)

        self.follow_new_card_checkbox = QCheckBox("Follow new card")
        self.follow_new_card_checkbox.setChecked(not args.no_follow)
        controls.addWidget(self.follow_new_card_checkbox)

        self.agent_pipeline_checkbox = QCheckBox("AgentQT MD/regex pipeline")
        self.agent_pipeline_checkbox.setChecked(not args.no_pipeline)
        controls.addWidget(self.agent_pipeline_checkbox)

        self.start_btn = QPushButton("Start")
        self.pause_btn = QPushButton("Pause")
        self.reset_btn = QPushButton("Reset")
        controls.addWidget(self.start_btn)
        controls.addWidget(self.pause_btn)
        controls.addWidget(self.reset_btn)

        self.stats_label = QLabel()
        self.stats_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        root.addWidget(self.stats_label)

        self.progress = QProgressBar()
        self.progress.setRange(0, max(1, len(self.payload)))
        root.addWidget(self.progress)

        self.host = QFrame()
        self.host_layout = QVBoxLayout(self.host)
        self.host_layout.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self.host, 1)
        self.editor: QPlainTextEdit | QTextBrowser | None = None
        self.outer_scroll: QScrollArea | None = None
        self.chat_body: QWidget | None = None
        self.chat_layout: QVBoxLayout | None = None
        self.rebuild_editor()

        self.stream_timer = QTimer(self)
        self.stream_timer.timeout.connect(self.stream_once)
        self.frame_timer = QTimer(self)
        self.frame_timer.timeout.connect(self.on_frame_tick)
        self.frame_timer.start(16)
        self.stats_timer = QTimer(self)
        self.stats_timer.timeout.connect(self.update_stats)
        self.stats_timer.start(500)

        self.start_btn.clicked.connect(self.start_stream)
        self.pause_btn.clicked.connect(self.stream_timer.stop)
        self.reset_btn.clicked.connect(self.reset_stream)
        self.mode_combo.currentIndexChanged.connect(self.reset_stream)
        self.outer_scroll_checkbox.stateChanged.connect(self.reset_stream)
        self.preload_spin.valueChanged.connect(self.reset_stream)
        self.loop_checkbox.stateChanged.connect(self.reset_stream)
        self.agent_pipeline_checkbox.stateChanged.connect(self.reset_stream)
        self.update_stats()
        if args.autostart:
            QTimer.singleShot(0, self.start_stream)

    def rebuild_editor(self) -> None:
        while self.host_layout.count():
            item = self.host_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        mode = self.mode_combo.currentText()
        if mode.startswith("markdown"):
            editor = QTextBrowser()
            editor.setOpenExternalLinks(False)
        else:
            editor = QPlainTextEdit()
            editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        editor.setReadOnly(True)
        editor.setMinimumHeight(520 if not self.outer_scroll_checkbox.isChecked() else 210)
        editor.setMaximumHeight(16777215 if not self.outer_scroll_checkbox.isChecked() else 210)
        editor.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        editor.setStyleSheet(
            """
            QPlainTextEdit, QTextBrowser {
                background: #f6f8fc;
                color: #172033;
                border: 1px solid #d8e1f0;
                border-radius: 12px;
                padding: 12px;
                font-size: 14px;
            }
            """
        )
        self.editor = editor
        if not self.outer_scroll_checkbox.isChecked():
            self.outer_scroll = None
            self.chat_body = None
            self.chat_layout = None
            self.host_layout.addWidget(editor)
            return

        self.outer_scroll = QScrollArea()
        self.outer_scroll.setWidgetResizable(True)
        self.outer_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.outer_scroll.setStyleSheet(
            """
            QScrollArea {
                background: #eef3fb;
                border: 1px solid #d8e1f0;
                border-radius: 14px;
            }
            QScrollArea > QWidget > QWidget {
                background: #eef3fb;
            }
            QScrollBar:vertical {
                background: transparent;
                width: 10px;
            }
            QScrollBar::handle:vertical {
                background: #aab8cf;
                border-radius: 5px;
                min-height: 36px;
            }
            """
        )
        self.chat_body = QWidget()
        self.chat_layout = QVBoxLayout(self.chat_body)
        self.chat_layout.setContentsMargins(36, 28, 36, 28)
        self.chat_layout.setSpacing(20)
        self.outer_scroll.setWidget(self.chat_body)
        self.host_layout.addWidget(self.outer_scroll)

        preload_count = min(self.preload_spin.value(), len(self.sections))
        for role, body in self.sections[:preload_count]:
            self.chat_layout.addWidget(self.make_real_card(role, body, streaming=False))
        self.section_index = preload_count
        self.section_pos = 0
        self.streaming_card = None
        self.streaming_editor = None
        self.editor = None
        self.chat_layout.addStretch(1)
        QTimer.singleShot(0, self.scroll_probe_near_bottom)

    def make_static_card(self, index: int, before: bool) -> QFrame:
        card = QFrame()
        card.setStyleSheet(
            """
            QFrame {
                background: white;
                border: 1px solid #d8e1f0;
                border-radius: 18px;
            }
            QLabel {
                background: transparent;
                border: none;
                color: #172033;
                font-size: 14px;
                line-height: 1.35;
            }
            """
        )
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 14, 18, 14)
        title = "上方历史气泡" if before else "下方历史气泡"
        text = (
            f"{title} {index + 1}\n"
            "这是一段模拟的普通聊天内容，用来制造真实外层滚动区高度。"
            "你可以在流式吐字时拖动外层滚动条，观察是否被更新过程抢走。"
        )
        if index % 3 == 0:
            text += "\n" + ("长一点的换行内容。" * 18)
        label = QLabel(text)
        label.setWordWrap(True)
        layout.addWidget(label)
        return card

    def make_real_card(self, role: str, text: str, streaming: bool) -> QFrame:
        card = QFrame()
        palette = {
            "user": ("#eaf2ff", "#d5e2f4", "用户"),
            "ai": ("transparent", "transparent", "AI"),
            "result": ("#ffffff", "#d8e1f0", "执行结果"),
        }
        bg, border, title_text = palette.get(role, palette["ai"])
        radius = 0 if role == "ai" else 18
        card_margin = "2px 0" if role == "ai" else "0"
        card.setStyleSheet(
            f"""
            QFrame {{
                background: {bg};
                border: 1px solid {border};
                border-radius: {radius}px;
                margin: {card_margin};
            }}
            """
        )
        layout = QVBoxLayout(card)
        if role == "ai":
            layout.setContentsMargins(4, 8, 4, 8)
            layout.setSpacing(10)
        else:
            layout.setContentsMargins(18, 14, 18, 14)
            layout.setSpacing(8)
        title = QLabel(title_text)
        title.setStyleSheet("background: transparent; border: none; font-weight: 900; font-size: 15px; color: #172033;")
        layout.addWidget(title)

        if role == "result":
            widget: QPlainTextEdit | QTextBrowser = QPlainTextEdit()
            widget.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
            widget.setPlainText(text)
            widget.setMaximumHeight(210)
            widget.setMinimumHeight(90 if streaming else min(210, 90 + len(text) // 900))
        else:
            if role == "ai":
                widget = QLabel()
                widget.setWordWrap(True)
                widget.setTextFormat(Qt.TextFormat.MarkdownText)
                widget.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            else:
                widget = QTextBrowser()
                widget.setOpenExternalLinks(False)
            if role == "ai":
                widget.setText(mask_low_value_context_markers_for_display(text))
            else:
                widget.setPlainText(text)
            if role == "ai":
                widget.setMinimumHeight(34)
                widget.setMaximumHeight(16777215)
            else:
                widget.setMaximumHeight(260)
                widget.setMinimumHeight(80)

        if hasattr(widget, "setReadOnly"):
            widget.setReadOnly(True)
        if hasattr(widget, "setHorizontalScrollBarPolicy"):
            widget.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        widget.setStyleSheet(
            """
            QPlainTextEdit, QTextBrowser, QLabel {
                background: transparent;
                color: #172033;
                border: none;
                padding: 2px 0;
                font-size: 14px;
            }
            QScrollBar:vertical {
                background: transparent;
                width: 8px;
            }
            QScrollBar::handle:vertical {
                background: #b6c4d8;
                border-radius: 4px;
                min-height: 28px;
            }
            """
        )
        layout.addWidget(widget)
        if isinstance(widget, QLabel):
            self.adjust_label_height(widget)
        elif isinstance(widget, QTextBrowser):
            self.adjust_text_browser_height(widget)
        if streaming:
            self.streaming_card = card
            self.streaming_editor = widget
            widget.previous_code_parts = 0
            widget.previous_signatures = []
        return card

    def scroll_probe_to_streaming_card(self) -> None:
        if self.outer_scroll is None or self.streaming_card is None:
            return
        bar = self.outer_scroll.verticalScrollBar()
        target = max(0, self.streaming_card.y() - 80)
        bar.setValue(min(target, bar.maximum()))

    def scroll_probe_near_bottom(self) -> None:
        if self.outer_scroll is None:
            return
        bar = self.outer_scroll.verticalScrollBar()
        bar.setValue(max(0, bar.maximum() - 260))

    def reset_counters(self) -> None:
        self.last_frame_at = time.perf_counter()
        self.frame_count = 0
        self.lag_count = 0
        self.max_lag_ms = 0.0
        self.flush_count = 0
        self.total_flush_ms = 0.0
        self.max_flush_ms = 0.0

    def reset_stream(self) -> None:
        self.stream_timer.stop()
        self.pos = 0
        self.total_streamed_chars = 0
        self.section_index = 0
        self.section_pos = 0
        self.current_text = ""
        self.reset_counters()
        self.rebuild_editor()
        self.progress.setValue(0)
        self.update_stats()

    def start_stream(self) -> None:
        if self.outer_scroll_checkbox.isChecked():
            if self.section_index >= len(self.sections) and not self.loop_checkbox.isChecked():
                self.reset_stream()
        elif self.pos >= len(self.payload):
            self.reset_stream()
        self.stream_timer.start(self.interval_spin.value())

    def stream_once(self) -> None:
        if self.outer_scroll_checkbox.isChecked():
            self.stream_real_section_once()
            return
        if self.editor is None:
            return
        if self.pos >= len(self.payload):
            self.stream_timer.stop()
            return

        chunk_size = self.chunk_spin.value()
        next_pos = min(len(self.payload), self.pos + chunk_size)
        delta = self.payload[self.pos:next_pos]
        self.pos = next_pos
        next_text = self.current_text + delta
        mode = self.mode_combo.currentText()

        started = time.perf_counter()
        if mode.startswith("baseline"):
            assert isinstance(self.editor, QPlainTextEdit)
            self.editor.setPlainText(next_text)
            self.adjust_height_scan(self.editor, next_text)
        elif mode.startswith("append: insert delta + height"):
            assert isinstance(self.editor, QPlainTextEdit)
            cursor = QTextCursor(self.editor.document())
            cursor.movePosition(QTextCursor.MoveOperation.End)
            cursor.insertText(delta)
            self.adjust_height_scan(self.editor, next_text)
        elif mode.startswith("append: insert delta + fixed"):
            assert isinstance(self.editor, QPlainTextEdit)
            cursor = QTextCursor(self.editor.document())
            cursor.movePosition(QTextCursor.MoveOperation.End)
            cursor.insertText(delta)
        else:
            assert isinstance(self.editor, QTextBrowser)
            self.editor.setMarkdown(next_text)
            self.editor.document().setTextWidth(max(120, self.editor.viewport().width() - 10))

        self.current_text = next_text
        if self.streaming_card is not None:
            self.streaming_card.updateGeometry()
        if self.chat_body is not None:
            self.chat_body.updateGeometry()
        elapsed_ms = (time.perf_counter() - started) * 1000
        self.flush_count += 1
        self.total_flush_ms += elapsed_ms
        self.max_flush_ms = max(self.max_flush_ms, elapsed_ms)
        self.progress.setValue(self.pos)
        self.write_log_sample()

    def stream_real_section_once(self) -> None:
        if self.chat_layout is None:
            return
        if self.section_index >= len(self.sections):
            if not self.loop_checkbox.isChecked():
                self.stream_timer.stop()
                return
            self.section_index = 0
            self.section_pos = 0

        role, body = self.sections[self.section_index]
        if self.streaming_editor is None:
            stretch_index = max(0, self.chat_layout.count() - 1)
            card = self.make_real_card(role, "", streaming=True)
            self.chat_layout.insertWidget(stretch_index, card)
            self.current_text = ""
            self.section_pos = 0
            if self.follow_new_card_checkbox.isChecked():
                QTimer.singleShot(0, self.scroll_probe_to_streaming_card)

        chunk_size = self.chunk_spin.value()
        next_pos = min(len(body), self.section_pos + chunk_size)
        delta = body[self.section_pos:next_pos]
        self.section_pos = next_pos
        next_text = self.current_text + delta
        mode = self.mode_combo.currentText()

        started = time.perf_counter()
        widget = self.streaming_editor
        if isinstance(widget, QPlainTextEdit):
            processed_text = self.apply_agent_pipeline(next_text, role, widget)
            if mode.startswith("baseline"):
                widget.setPlainText(processed_text)
                self.adjust_height_scan(widget, processed_text)
            else:
                cursor = QTextCursor(widget.document())
                cursor.movePosition(QTextCursor.MoveOperation.End)
                old_text = widget.toPlainText()
                if processed_text.startswith(old_text):
                    cursor.insertText(processed_text[len(old_text):])
                else:
                    widget.setPlainText(processed_text)
                if "height scan" in mode:
                    self.adjust_height_scan(widget, processed_text)
        elif isinstance(widget, QLabel):
            processed_text = self.apply_agent_pipeline(next_text, role, widget)
            widget.setText(processed_text)
            self.adjust_label_height(widget)
        elif isinstance(widget, QTextBrowser):
            processed_text = self.apply_agent_pipeline(next_text, role, widget)
            if mode.startswith("markdown") and role == "ai":
                widget.setMarkdown(processed_text)
                widget.document().setTextWidth(max(120, widget.viewport().width() - 10))
            elif mode.startswith("baseline"):
                widget.setPlainText(processed_text)
                widget.document().setTextWidth(max(120, widget.viewport().width() - 10))
            else:
                if role == "ai":
                    # The production AI view is Markdown during streaming, so
                    # keep this path visually accurate even in append modes.
                    widget.setMarkdown(processed_text)
                    widget.document().setTextWidth(max(120, widget.viewport().width() - 10))
                else:
                    cursor = QTextCursor(widget.document())
                    cursor.movePosition(QTextCursor.MoveOperation.End)
                    old_text = widget.toPlainText()
                    if processed_text.startswith(old_text):
                        cursor.insertText(processed_text[len(old_text):])
                    else:
                        widget.setPlainText(processed_text)
            self.adjust_text_browser_height(widget)

        self.current_text = next_text
        self.total_streamed_chars += len(delta)
        if self.streaming_card is not None:
            self.streaming_card.updateGeometry()
        if self.chat_body is not None:
            self.chat_body.updateGeometry()

        elapsed_ms = (time.perf_counter() - started) * 1000
        self.flush_count += 1
        self.total_flush_ms += elapsed_ms
        self.max_flush_ms = max(self.max_flush_ms, elapsed_ms)
        absolute_progress = self.total_streamed_chars % max(1, self.progress.maximum())
        self.progress.setValue(min(self.progress.maximum(), absolute_progress))

        if self.section_pos >= len(body):
            self.section_index += 1
            self.section_pos = 0
            self.current_text = ""
            self.streaming_card = None
            self.streaming_editor = None
        self.write_log_sample()

    def apply_agent_pipeline(self, text: str, role: str, widget) -> str:
        if not self.agent_pipeline_checkbox.isChecked():
            return text
        display_text = mask_low_value_context_markers_for_display(text)
        if role != "ai":
            # Execution result cards still pay marker masking plus line wrapping,
            # but they do not build Markdown child widgets in the app.
            return display_text

        parts = split_markdown_fenced_blocks(display_text)
        signatures = [(part.get("type", ""), part.get("lang", "") if part.get("type") == "code" else "") for part in parts]
        next_code_parts = sum(1 for part in parts if part.get("type") == "code")
        previous_code_parts = int(getattr(widget, "previous_code_parts", 0) or 0)
        # Reproduce the stability guard: if streaming temporarily makes the
        # parsed code-block count shrink, skip the visible update.
        if previous_code_parts > 0 and next_code_parts < previous_code_parts:
            return self.widget_text(widget)

        # Simulate the app's Markdown render compute every flush, but return
        # Markdown text so QTextBrowser still performs real Markdown layout.
        for part in parts:
            if part.get("type") != "code":
                markdown_with_pipe_tables_to_html(part.get("text", ""))

        widget.previous_code_parts = next_code_parts
        widget.previous_signatures = signatures
        return display_text

    def widget_text(self, widget) -> str:
        if isinstance(widget, QLabel):
            return widget.text()
        if hasattr(widget, "toPlainText"):
            return widget.toPlainText()
        return ""

    def adjust_label_height(self, widget: QLabel) -> None:
        width = max(120, widget.width() or (self.chat_body.width() - 96 if self.chat_body else 900))
        target = max(34, widget.heightForWidth(width) + 12)
        if widget.height() != target:
            widget.setFixedHeight(target)

    def adjust_text_browser_height(self, widget: QTextBrowser) -> None:
        width = max(120, widget.viewport().width() - 10)
        widget.document().setTextWidth(width)
        doc_height = int(widget.document().documentLayout().documentSize().height())
        margin = int(widget.document().documentMargin() * 2)
        target = max(34, doc_height + margin + 24)
        if widget.maximumHeight() < 100000:
            target = min(widget.maximumHeight(), target)
        if widget.height() != target:
            widget.setFixedHeight(target)

    def adjust_height_scan(self, editor: QPlainTextEdit, text: str) -> None:
        available_width = max(120, editor.viewport().width() - 10)
        line_spacing = max(1, editor.fontMetrics().lineSpacing())
        max_visual_lines = max(1, (520 - 36 + line_spacing - 1) // line_spacing)
        estimate_wrapped_text_height(text, editor.fontMetrics(), available_width, max_visual_lines)

    def on_frame_tick(self) -> None:
        now = time.perf_counter()
        delta_ms = (now - self.last_frame_at) * 1000
        self.last_frame_at = now
        self.frame_count += 1
        if delta_ms > 50:
            self.lag_count += 1
            self.max_lag_ms = max(self.max_lag_ms, delta_ms)

    def update_stats(self) -> None:
        avg_flush = self.total_flush_ms / self.flush_count if self.flush_count else 0.0
        self.stats_label.setText(
            f"payload={len(self.payload):,} chars, cards={len(self.sections):,} | "
            f"streamed={self.total_streamed_chars:,}, section={self.section_index}/{len(self.sections)} | "
            f"flushes={self.flush_count:,} avg_flush={avg_flush:.2f}ms "
            f"max_flush={self.max_flush_ms:.2f}ms | "
            f"frame_ticks={self.frame_count:,} lag_ticks(>50ms)={self.lag_count:,} "
            f"max_lag={self.max_lag_ms:.1f}ms"
        )

    def stats_payload(self) -> dict:
        avg_flush = self.total_flush_ms / self.flush_count if self.flush_count else 0.0
        return {
            "kind": "widgets",
            "ts": time.time(),
            "payload_chars": len(self.payload),
            "cards_total": len(self.sections),
            "cards_in_layout": self.chat_layout.count() if self.chat_layout is not None else 0,
            "streamed_chars": self.total_streamed_chars,
            "section_index": self.section_index,
            "section_total": len(self.sections),
            "flush_count": self.flush_count,
            "avg_flush_ms": avg_flush,
            "max_flush_ms": self.max_flush_ms,
            "frame_ticks": self.frame_count,
            "lag_ticks_over_50ms": self.lag_count,
            "max_lag_ms": self.max_lag_ms,
            "chunk_chars": self.chunk_spin.value(),
            "interval_ms": self.interval_spin.value(),
            "preload_cards": self.preload_spin.value(),
            "loop": self.loop_checkbox.isChecked(),
            "follow": self.follow_new_card_checkbox.isChecked(),
            "pipeline": self.agent_pipeline_checkbox.isChecked(),
        }

    def write_log_sample(self) -> None:
        if self.flush_count % 10 != 0:
            return
        self.log_file.write(json.dumps(self.stats_payload(), ensure_ascii=False) + "\n")
        self.log_file.flush()

    def closeEvent(self, event):
        try:
            self.log_file.write(json.dumps({**self.stats_payload(), "event": "close"}, ensure_ascii=False) + "\n")
            self.log_file.flush()
            self.log_file.close()
        except Exception:
            pass
        super().closeEvent(event)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--amplify", type=int, default=1)
    parser.add_argument("--max-chars", type=int, default=0, help="0 means no cap")
    parser.add_argument("--chunk-chars", type=int, default=36)
    parser.add_argument("--interval-ms", type=int, default=18)
    parser.add_argument("--preload-cards", type=int, default=24)
    parser.add_argument("--no-loop", action="store_true")
    parser.add_argument("--no-follow", action="store_true")
    parser.add_argument("--no-pipeline", action="store_true")
    parser.add_argument("--autostart", action="store_true")
    parser.add_argument("--log-path", default="/tmp/agentqt_stream_probe_widgets.jsonl")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    payload = load_payload(args.source, args.amplify, args.max_chars)
    sections = parse_export_sections(payload)
    app = QApplication(sys.argv[:1])
    probe = StreamRenderProbe(payload, sections, args)
    probe.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
