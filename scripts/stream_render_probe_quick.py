#!/usr/bin/env python3
"""Qt Quick version of the AgentQT streaming-render probe.

This script uses the same exported session data as stream_render_probe.py, but
renders it with QML ListView delegates. It is meant for side-by-side testing
against the Widgets probe while exploring whether a Quick chat view helps.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

from PySide6.QtCore import (
    QAbstractListModel,
    QByteArray,
    QModelIndex,
    QObject,
    Property,
    QTimer,
    Qt,
    Signal,
    Slot,
)
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine


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
                    nested_markdown_fence_depth = 2 if nested_markdown_fence_depth <= 1 else nested_markdown_fence_depth - 1
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
    text = str(markdown_text or "").lstrip("\r\n")
    text = re.sub(r"&", "&amp;", text)
    text = re.sub(r"<", "&lt;", text)
    text = re.sub(r">", "&gt;", text)
    text = re.sub(r"`([^`\n]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*([^*\n]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?m)^\s*[-*]\s+(.+)$", r"<li>\1</li>", text)
    text = re.sub(r"(?m)^(#{1,6})\s+(.+)$", lambda m: f"<h{len(m.group(1))}>{m.group(2)}</h{len(m.group(1))}>", text)
    if "|" in text:
        rows = [line for line in text.splitlines() if line.count("|") >= 2]
        if rows:
            text += "\n" + "".join(f"<!-- table-row {len(row)} -->" for row in rows)
    return text.replace("\n", "<br>")


def load_payload(path: Path, amplify: int, max_chars: int) -> str:
    if path.exists():
        text = path.read_text(errors="replace")
    else:
        text = "Fallback payload\n\n```bash\necho hello\n```\n\n" * 200
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


class MessageModel(QAbstractListModel):
    RoleRole = Qt.ItemDataRole.UserRole + 1
    TextRole = Qt.ItemDataRole.UserRole + 2

    def __init__(self):
        super().__init__()
        self.items: list[dict[str, str]] = []

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.items)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() < 0 or index.row() >= len(self.items):
            return None
        item = self.items[index.row()]
        if role == self.RoleRole:
            return item["role"]
        if role == self.TextRole:
            return item["text"]
        return None

    def roleNames(self):
        return {
            self.RoleRole: QByteArray(b"messageRole"),
            self.TextRole: QByteArray(b"messageText"),
        }

    def clear(self) -> None:
        if not self.items:
            return
        self.beginResetModel()
        self.items.clear()
        self.endResetModel()

    def append(self, role: str, text: str) -> None:
        row = len(self.items)
        self.beginInsertRows(QModelIndex(), row, row)
        self.items.append({"role": role, "text": text})
        self.endInsertRows()

    def update_last(self, text: str) -> None:
        if not self.items:
            return
        row = len(self.items) - 1
        self.items[row]["text"] = text
        index = self.index(row, 0)
        self.dataChanged.emit(index, index, [self.TextRole])


class Controller(QObject):
    statsChanged = Signal()

    def __init__(self, sections: list[tuple[str, str]], model: MessageModel, args: argparse.Namespace):
        super().__init__()
        self.sections = sections or [("ai", "No sections parsed.")]
        self.model = model
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.stream_once)
        self.stats_timer = QTimer(self)
        self.stats_timer.timeout.connect(self.update_frame_stats)
        self.stats_timer.start(16)
        self.section_index = 0
        self.section_pos = 0
        self.current_text = ""
        self.total_streamed_chars = 0
        self.flush_count = 0
        self.total_flush_ms = 0.0
        self.max_flush_ms = 0.0
        self.frame_count = 0
        self.lag_count = 0
        self.max_lag_ms = 0.0
        self.last_frame_at = time.perf_counter()
        self.chunk_chars = args.chunk_chars
        self.interval_ms = args.interval_ms
        self.preload_cards = args.preload_cards
        self.loop = not args.no_loop
        self.follow_new_card = not args.no_follow
        self.pipeline = not args.no_pipeline
        self.log_path = Path(args.log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_file = self.log_path.open("w", encoding="utf-8")
        self._stats = ""
        self._previous_code_parts = 0
        self.reset()
        if args.autostart:
            QTimer.singleShot(0, self.start)

    def get_stats(self) -> str:
        return self._stats

    stats = Property(str, get_stats, notify=statsChanged)

    @Slot()
    def start(self) -> None:
        self.timer.start(self.interval_ms)

    @Slot()
    def pause(self) -> None:
        self.timer.stop()

    @Slot()
    def reset(self) -> None:
        self.timer.stop()
        self.model.clear()
        count = min(self.preload_cards, len(self.sections))
        for role, text in self.sections[:count]:
            self.model.append(role, self.prepare_static_text(role, text))
        self.section_index = count
        self.section_pos = 0
        self.current_text = ""
        self.total_streamed_chars = 0
        self.flush_count = 0
        self.total_flush_ms = 0.0
        self.max_flush_ms = 0.0
        self.frame_count = 0
        self.lag_count = 0
        self.max_lag_ms = 0.0
        self.last_frame_at = time.perf_counter()
        self._previous_code_parts = 0
        self.update_stats()
        self.write_log_sample()

    @Slot(int)
    def setChunkChars(self, value: int) -> None:
        self.chunk_chars = max(1, int(value))

    @Slot(int)
    def setIntervalMs(self, value: int) -> None:
        self.interval_ms = max(0, int(value))
        if self.timer.isActive():
            self.timer.start(self.interval_ms)

    @Slot(int)
    def setPreloadCards(self, value: int) -> None:
        self.preload_cards = max(0, int(value))
        self.reset()

    @Slot(bool)
    def setLoop(self, value: bool) -> None:
        self.loop = bool(value)

    @Slot(bool)
    def setFollowNewCard(self, value: bool) -> None:
        self.follow_new_card = bool(value)

    @Slot(bool)
    def setPipeline(self, value: bool) -> None:
        self.pipeline = bool(value)
        self.reset()

    def stream_once(self) -> None:
        if self.section_index >= len(self.sections):
            if not self.loop:
                self.timer.stop()
                return
            self.section_index = 0
            self.section_pos = 0

        role, body = self.sections[self.section_index]
        if self.section_pos == 0 and not self.current_text:
            self.model.append(role, "")
            self._previous_code_parts = 0

        next_pos = min(len(body), self.section_pos + self.chunk_chars)
        delta = body[self.section_pos:next_pos]
        self.section_pos = next_pos
        next_text = self.current_text + delta

        started = time.perf_counter()
        processed = self.prepare_streaming_text(role, next_text)
        self.model.update_last(processed)
        elapsed_ms = (time.perf_counter() - started) * 1000

        self.current_text = next_text
        self.total_streamed_chars += len(delta)
        self.flush_count += 1
        self.total_flush_ms += elapsed_ms
        self.max_flush_ms = max(self.max_flush_ms, elapsed_ms)
        if self.section_pos >= len(body):
            self.section_index += 1
            self.section_pos = 0
            self.current_text = ""
        self.update_stats()
        self.write_log_sample()

    def prepare_static_text(self, role: str, text: str) -> str:
        if role in {"ai", "result"}:
            return mask_low_value_context_markers_for_display(text)
        return text

    def prepare_streaming_text(self, role: str, text: str) -> str:
        if not self.pipeline:
            return text
        display_text = mask_low_value_context_markers_for_display(text)
        if role != "ai":
            return display_text
        parts = split_markdown_fenced_blocks(display_text)
        next_code_parts = sum(1 for part in parts if part.get("type") == "code")
        if self._previous_code_parts > 0 and next_code_parts < self._previous_code_parts:
            return self.model.items[-1]["text"] if self.model.items else ""
        for part in parts:
            if part.get("type") != "code":
                markdown_with_pipe_tables_to_html(part.get("text", ""))
        self._previous_code_parts = next_code_parts
        return display_text

    def update_frame_stats(self) -> None:
        now = time.perf_counter()
        delta_ms = (now - self.last_frame_at) * 1000
        self.last_frame_at = now
        self.frame_count += 1
        if delta_ms > 50:
            self.lag_count += 1
            self.max_lag_ms = max(self.max_lag_ms, delta_ms)

    def update_stats(self) -> None:
        avg_flush = self.total_flush_ms / self.flush_count if self.flush_count else 0.0
        self._stats = (
            f"QuickView | cards={len(self.model.items):,}/{len(self.sections):,} "
            f"streamed={self.total_streamed_chars:,} section={self.section_index}/{len(self.sections)} | "
            f"flushes={self.flush_count:,} avg_flush={avg_flush:.2f}ms max_flush={self.max_flush_ms:.2f}ms | "
            f"frame_ticks={self.frame_count:,} lag_ticks(>50ms)={self.lag_count:,} max_lag={self.max_lag_ms:.1f}ms"
        )
        self.statsChanged.emit()

    def stats_payload(self) -> dict:
        avg_flush = self.total_flush_ms / self.flush_count if self.flush_count else 0.0
        return {
            "kind": "quick",
            "ts": time.time(),
            "cards_in_model": len(self.model.items),
            "section_total": len(self.sections),
            "streamed_chars": self.total_streamed_chars,
            "section_index": self.section_index,
            "flush_count": self.flush_count,
            "avg_flush_ms": avg_flush,
            "max_flush_ms": self.max_flush_ms,
            "frame_ticks": self.frame_count,
            "lag_ticks_over_50ms": self.lag_count,
            "max_lag_ms": self.max_lag_ms,
            "chunk_chars": self.chunk_chars,
            "interval_ms": self.interval_ms,
            "preload_cards": self.preload_cards,
            "loop": self.loop,
            "follow": self.follow_new_card,
            "pipeline": self.pipeline,
        }

    def write_log_sample(self) -> None:
        if self.flush_count % 10 != 0:
            return
        self.log_file.write(json.dumps(self.stats_payload(), ensure_ascii=False) + "\n")
        self.log_file.flush()

    @Slot()
    def closeLog(self) -> None:
        try:
            self.log_file.write(json.dumps({**self.stats_payload(), "event": "close"}, ensure_ascii=False) + "\n")
            self.log_file.flush()
            self.log_file.close()
        except Exception:
            pass


QML = r"""
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ApplicationWindow {
    id: root
    width: 1180
    height: 820
    visible: true
    title: "AgentQT QuickView streaming render probe"
    color: "#eef3fb"
    onClosing: controller.closeLog()

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 14
        spacing: 8

        RowLayout {
            Layout.fillWidth: true
            spacing: 8

            Label { text: "Chunk"; color: "#172033" }
            SpinBox {
                from: 1; to: 32768; value: 36; stepSize: 12
                onValueModified: controller.setChunkChars(value)
            }
            Label { text: "Interval"; color: "#172033" }
            SpinBox {
                from: 0; to: 1000; value: 18; stepSize: 5
                onValueModified: controller.setIntervalMs(value)
            }
            Label { text: "Preload"; color: "#172033" }
            SpinBox {
                from: 0; to: 500; value: 24; stepSize: 4
                onValueModified: controller.setPreloadCards(value)
            }
            CheckBox {
                text: "Loop add cards"; checked: true
                onToggled: controller.setLoop(checked)
            }
            CheckBox {
                id: followBox
                text: "Follow new card"; checked: true
                onToggled: controller.setFollowNewCard(checked)
            }
            CheckBox {
                text: "AgentQT MD/regex pipeline"; checked: true
                onToggled: controller.setPipeline(checked)
            }
            Button { text: "Start"; onClicked: controller.start() }
            Button { text: "Pause"; onClicked: controller.pause() }
            Button { text: "Reset"; onClicked: controller.reset() }
        }

        Label {
            Layout.fillWidth: true
            text: controller.stats
            color: "#172033"
            font.pixelSize: 13
            elide: Text.ElideRight
        }

        ListView {
            id: list
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            spacing: 20
            model: messageModel
            boundsBehavior: Flickable.StopAtBounds
            cacheBuffer: 1800
            ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
            onCountChanged: {
                if (followBox.checked && count > 0)
                    positionViewAtEnd()
            }

            delegate: Item {
                id: delegateRoot
                width: ListView.view.width
                implicitHeight: contentColumn.implicitHeight + 2

                Column {
                    id: contentColumn
                    width: parent.width - 72
                    x: 36
                    spacing: 8

                    Label {
                        text: messageRole === "user" ? "用户" : (messageRole === "result" ? "执行结果" : "AI")
                        color: "#172033"
                        font.bold: true
                        font.pixelSize: 15
                    }

                    Loader {
                        width: parent.width
                        sourceComponent: messageRole === "user" ? userCard : (messageRole === "result" ? resultCard : aiText)
                    }
                }

                Component {
                    id: userCard
                    Rectangle {
                        width: contentColumn.width
                        radius: 18
                        color: "#eaf2ff"
                        border.color: "#d5e2f4"
                        implicitHeight: userText.implicitHeight + 28
                        Text {
                            id: userText
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.top: parent.top
                            anchors.margins: 18
                            text: messageText
                            wrapMode: Text.Wrap
                            textFormat: Text.PlainText
                            color: "#172033"
                            font.pixelSize: 15
                        }
                    }
                }

                Component {
                    id: aiText
                    Text {
                        width: contentColumn.width
                        text: messageText
                        wrapMode: Text.Wrap
                        textFormat: Text.MarkdownText
                        color: "#172033"
                        font.pixelSize: 15
                        lineHeight: 1.22
                    }
                }

                Component {
                    id: resultCard
                    Rectangle {
                        width: contentColumn.width
                        height: 228
                        radius: 18
                        color: "#ffffff"
                        border.color: "#d8e1f0"
                        Flickable {
                            anchors.fill: parent
                            anchors.margins: 16
                            clip: true
                            contentWidth: width
                            contentHeight: resultText.implicitHeight
                            boundsBehavior: Flickable.StopAtBounds
                            Text {
                                id: resultText
                                width: parent.width
                                text: messageText
                                wrapMode: Text.Wrap
                                textFormat: Text.PlainText
                                color: "#172033"
                                font.family: "Menlo"
                                font.pixelSize: 13
                            }
                            ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
                        }
                    }
                }
            }
        }
    }
}
"""


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
    parser.add_argument("--log-path", default="/tmp/agentqt_stream_probe_quick.jsonl")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    payload = load_payload(args.source, args.amplify, args.max_chars)
    sections = parse_export_sections(payload)
    app = QGuiApplication(sys.argv[:1])
    model = MessageModel()
    controller = Controller(sections, model, args)
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("messageModel", model)
    engine.rootContext().setContextProperty("controller", controller)
    engine.loadData(QML.encode("utf-8"))
    if not engine.rootObjects():
        return 1
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
