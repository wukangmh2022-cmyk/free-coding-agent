#!/usr/bin/env python3
"""
Agent 控制台 v5.1
- 支持所有常见代码块占位符（CSS/JS/SVG/JSON/YAML...）
- 优化提示词：解释协议 + 避免重复 Bash
- 首页 + 对话双界面
"""

import sys
import os
import re
import subprocess
import difflib
import platform
import html
import json
import base64
import shutil
import uuid
from typing import Dict, List, Optional
from datetime import datetime

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTextEdit, QPushButton, QLabel, QScrollArea, QFrame,
    QFileDialog, QMessageBox, QLineEdit, QTreeWidget, QTreeWidgetItem,
    QMenu, QToolButton, QStyle, QPlainTextEdit, QStackedWidget,
    QGridLayout, QSizePolicy, QGraphicsOpacityEffect, QAbstractItemView,
    QSpacerItem
)
from PySide6.QtCore import Qt, QTimer, Signal, QThread, QProcess, QProcessEnvironment, QPropertyAnimation, QEasingCurve, QSize, QByteArray
from PySide6.QtGui import QFont, QAction, QDesktopServices, QMouseEvent, QTextCursor, QIcon, QPixmap, QPainter, QPen, QColor, QKeySequence
from PySide6.QtCore import QUrl

try:
    from PySide6.QtSvg import QSvgRenderer
except ImportError:
    QSvgRenderer = None

PROMPT_BUBBLE_MARKER = "<!-- agent_qt_user_prompt:"

# ============================================================
# 系统提示词
# ============================================================
SYSTEM_PROMPT = """你是本地 Agent 执行引擎的 AI 助手。

## 协议说明
本 Agent 采用"占位符 + 代码块"协议：你在 Bash 指令中用注释占位符（如 <!-- HTML block -->）替代大段代码，
然后将完整代码放在对应的 Markdown 代码块中。Agent 会先缓存所有代码块，再依次执行 Bash 指令并自动替换占位符。
这样既能保持指令清晰，又能直接写入完整文件。

## 当前运行环境
- 操作系统: {os_name}
- 平台标识: {platform_id}
- 默认 Shell: {shell_name}
- 路径风格: {path_style}
- **重要：所有 Bash/命令行指令必须匹配当前操作系统，不要输出其他系统的命令。**

## 推理要求
- Reasoning Effort: absolute maximum with no shortcuts permitted. You must thoroughly decompose the task, identify the root cause, and stress-test the solution against likely paths, edge cases, and adversarial scenarios before answering.
- 输出时不要展开隐藏思考链；只给出关键判断、可验证依据、最终方案和必要的执行指令。

## 输出规则
1. **所有 Bash 指令放在一个 ```bash 代码块中**，不要拆分多个 Bash 块。
2. 大段文件内容用占位符替代，支持的占位符：
   - <!-- HTML block --> 对应 ```html
   - <!-- CSS block --> 对应 ```css
   - <!-- JS block --> 对应 ```js 或 ```javascript
   - <!-- Python block --> 或 # Python block 对应 ```python
   - <!-- SVG block --> 对应 ```svg
   - <!-- JSON block --> 对应 ```json
   - <!-- YAML block --> 对应 ```yaml
   - <!-- TypeScript block --> 对应 ```typescript 或 ```ts
   - <!-- 其他任意类型 block --> 对应 ```类型名（如 ```svg ```xml ```toml 等）
3. 各代码块在 Bash 块之后单独给出。
4. 指令按顺序排列，先创建目录再写文件，确保可直接执行。
5. 项目根目录: {project_root}，所有路径使用绝对路径。
6. **重要：一个 Bash 块包含所有指令，不要输出多个 Bash 块。**
7. **重要：二选一的操作只保留一种**（如启动服务器 OR 手动打开，选前者）。
8. 安装依赖用 pip install，启动后端用 python server.py 或 python3 -m http.server。
9. 常驻进程命令（python server.py 等）会自动进入后台终端，不要加 & 或 nohup。

---

{user_prompt}"""

# ============================================================
# 配置
# ============================================================
HISTORY_DIR_NAME = ".agent_qt"
HISTORY_FILE_NAME = "history.json"
THREADS_DIR_NAME = "threads"
THREADS_INDEX_FILE_NAME = "threads.json"
DEFAULT_THREAD_ID = "default"
HISTORY_VERSION = 1

FORBIDDEN = [
    "rm -rf /", "sudo rm", "sudo reboot", "shutdown",
    "mkfs", "dd if=", ":(){ :|:& };:",
]

COLORS = {
    "bg": "#f6f7fb",
    "bg_top": "#fbfcff",
    "surface": "#ffffff",
    "surface_alt": "#f0f3fb",
    "sidebar_bg": "#f3f5fb",
    "card_user": "#eef4ff",
    "card_ai": "#f4f0ff",
    "card_system": "#f6f9fc",
    "accent": "#6d4aff",
    "accent_dark": "#5636d9",
    "accent_light": "#eee9ff",
    "accent_2": "#06b6d4",
    "text": "#172033",
    "text_secondary": "#657089",
    "muted": "#8b95aa",
    "border": "#dce2f0",
    "border_strong": "#cbd4e6",
    "code_bg": "#f8faff",
    "success": "#16a36a",
    "success_soft": "#e8f8f1",
    "danger": "#e5484d",
    "danger_soft": "#ffeded",
    "input_bg": "#ffffff",
    "terminal_bg": "#ffffff",
    "terminal_panel": "#ffffff",
    "terminal_card": "#f8faff",
    "terminal_text": "#172033",
    "terminal_muted": "#657089",
    "terminal_accent": "#2563eb",
}

def message_box_style() -> str:
    return f"""
        QMessageBox {{
            background: {COLORS['bg_top']};
        }}
        QMessageBox QLabel {{
            color: {COLORS['text']};
            background: transparent;
            font-size: 13px;
            font-weight: 700;
            selection-background-color: {COLORS['accent_light']};
            selection-color: {COLORS['text']};
        }}
        QMessageBox QPushButton {{
            min-width: 88px;
            min-height: 32px;
            background: {COLORS['surface']};
            color: {COLORS['text']};
            border: 1px solid {COLORS['border_strong']};
            border-radius: 9px;
            padding: 6px 16px;
            font-size: 13px;
            font-weight: 800;
        }}
        QMessageBox QPushButton:hover {{
            background: {COLORS['surface_alt']};
            border-color: {COLORS['accent']};
            color: {COLORS['accent_dark']};
        }}
        QMessageBox QPushButton#primaryButton {{
            background: {COLORS['accent']};
            color: white;
            border-color: {COLORS['accent']};
        }}
        QMessageBox QPushButton#primaryButton:hover {{
            background: {COLORS['accent_dark']};
        }}
        QMessageBox QPushButton#dangerButton {{
            background: {COLORS['danger']};
            color: white;
            border-color: {COLORS['danger']};
        }}
        QMessageBox QPushButton#dangerButton:hover {{
            background: #c83238;
        }}
    """

def app_global_style() -> str:
    return f"""
        QToolTip {{
            background-color: {COLORS['text']};
            color: white;
            border: 1px solid #334155;
            border-radius: 8px;
            padding: 6px 10px;
            font-size: 12px;
            font-weight: 800;
        }}
    """

def show_chinese_edit_menu(widget, global_pos):
    menu = QMenu(widget)
    is_read_only = widget.isReadOnly() if hasattr(widget, "isReadOnly") else False
    cursor = widget.textCursor()
    has_selection = cursor.hasSelection()
    text = widget.toPlainText()
    if is_read_only:
        copy_action = QAction("复制" if has_selection else "复制全部", widget)
        copy_action.setEnabled(bool(has_selection or text))
        copy_action.triggered.connect(
            lambda: QApplication.clipboard().setText(
                cursor.selectedText().replace("\u2029", "\n") if has_selection else text
            )
        )
        menu.addAction(copy_action)
        select_all_action = QAction("全选", widget)
        select_all_action.setEnabled(bool(text))
        select_all_action.triggered.connect(widget.selectAll)
        menu.addAction(select_all_action)
        menu.exec(global_pos)
        return

    actions = [
        ("撤销", widget.undo, hasattr(widget, "isUndoRedoEnabled") and widget.isUndoRedoEnabled()),
        ("重做", widget.redo, hasattr(widget, "isUndoRedoEnabled") and widget.isUndoRedoEnabled()),
        (None, None, True),
        ("剪切", widget.cut, has_selection),
        ("复制", widget.copy, has_selection),
        ("粘贴", widget.paste, True),
        ("删除", lambda: widget.textCursor().removeSelectedText(), has_selection),
        (None, None, True),
        ("全选", widget.selectAll, bool(text)),
    ]
    for label, callback, enabled in actions:
        if label is None:
            menu.addSeparator()
            continue
        action = QAction(label, widget)
        action.setEnabled(bool(enabled))
        action.triggered.connect(callback)
        menu.addAction(action)
    menu.exec(global_pos)

def styled_confirm(parent, title: str, text: str, confirm_text: str = "确定", destructive: bool = False) -> bool:
    dialog = QMessageBox(parent)
    dialog.setWindowTitle(title)
    dialog.setText(text)
    dialog.setIcon(QMessageBox.Icon.Question)
    cancel_btn = dialog.addButton("取消", QMessageBox.ButtonRole.RejectRole)
    confirm_btn = dialog.addButton(confirm_text, QMessageBox.ButtonRole.AcceptRole)
    confirm_btn.setObjectName("dangerButton" if destructive else "primaryButton")
    dialog.setStyleSheet(message_box_style())
    dialog.setDefaultButton(cancel_btn)
    dialog.exec()
    return dialog.clickedButton() == confirm_btn

def styled_warning(parent, title: str, text: str):
    dialog = QMessageBox(parent)
    dialog.setWindowTitle(title)
    dialog.setText(text)
    dialog.setIcon(QMessageBox.Icon.Warning)
    ok_btn = dialog.addButton("知道了", QMessageBox.ButtonRole.AcceptRole)
    ok_btn.setObjectName("primaryButton")
    dialog.setStyleSheet(message_box_style())
    dialog.setDefaultButton(ok_btn)
    dialog.exec()

def line_icon(kind: str, color: str = "#172033", size: int = 18) -> QIcon:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color), 1.8)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    if kind == "settings":
        cx = cy = size / 2
        painter.drawEllipse(int(cx - 3), int(cy - 3), 6, 6)
        for dx, dy in ((0, -7), (0, 7), (-7, 0), (7, 0), (-5, -5), (5, 5), (-5, 5), (5, -5)):
            painter.drawLine(int(cx + dx * 0.55), int(cy + dy * 0.55), int(cx + dx), int(cy + dy))
    elif kind == "trash":
        left = 5
        right = size - 5
        lid_y = 6
        bottom = size - 4
        painter.drawLine(left, lid_y, right, lid_y)
        painter.drawLine(left + 3, lid_y - 3, right - 3, lid_y - 3)
        painter.drawLine(int(size / 2 - 2), lid_y - 4, int(size / 2 + 2), lid_y - 4)
        painter.drawLine(left + 2, lid_y + 2, left + 3, bottom)
        painter.drawLine(right - 2, lid_y + 2, right - 3, bottom)
        painter.drawLine(left + 3, bottom, right - 3, bottom)
        painter.drawLine(left + 6, lid_y + 5, left + 6, bottom - 3)
        painter.drawLine(right - 6, lid_y + 5, right - 6, bottom - 3)
    elif kind == "close":
        painter.drawLine(5, 5, size - 5, size - 5)
        painter.drawLine(size - 5, 5, 5, size - 5)
    elif kind == "terminal":
        painter.drawRoundedRect(3, 4, size - 6, size - 8, 3, 3)
        mid_y = int(size / 2)
        painter.drawLine(6, mid_y - 3, 9, mid_y)
        painter.drawLine(9, mid_y, 6, mid_y + 3)
        painter.drawLine(int(size / 2) + 1, mid_y + 4, size - 6, mid_y + 4)
    elif kind == "plus":
        painter.drawLine(int(size / 2), 4, int(size / 2), size - 4)
        painter.drawLine(4, int(size / 2), size - 4, int(size / 2))
    painter.end()
    return QIcon(pixmap)

def svg_icon(svg: str, size: int = 18) -> QIcon:
    if QSvgRenderer is None:
        return QIcon()
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter)
    painter.end()
    return QIcon(pixmap)

def terminal_icon(color: str = "#172033", size: int = 16) -> QIcon:
    icon = svg_icon(f"""
        <svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 24 24" fill="none">
          <rect x="3.5" y="5" width="17" height="14" rx="3" stroke="{color}" stroke-width="2.2"/>
          <path d="M7.5 10l3 2.5-3 2.5" stroke="{color}" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/>
          <path d="M12.5 15.5h4" stroke="{color}" stroke-width="2.2" stroke-linecap="round"/>
        </svg>
    """, size)
    return icon if not icon.isNull() else line_icon("terminal", color, size)

# ============================================================
# 工具函数
# ============================================================
def is_safe(cmd: str) -> bool:
    for bad in FORBIDDEN:
        if bad in cmd:
            return False
    return True

def runtime_environment() -> Dict[str, str]:
    system = platform.system() or sys.platform
    path_style = "Windows paths (C:\\...)" if system == "Windows" else "POSIX paths (/Users/... 或 /home/...)"
    return {
        "os_name": system,
        "platform_id": sys.platform,
        "shell_name": os.environ.get("SHELL") or os.environ.get("COMSPEC") or "unknown",
        "path_style": path_style,
    }

LANG_ALIASES = {
    "js": ("js", "javascript"),
    "javascript": ("javascript", "js"),
    "ts": ("ts", "typescript"),
    "typescript": ("typescript", "ts"),
    "yaml": ("yaml", "yml"),
    "yml": ("yml", "yaml"),
    "python": ("python", "py"),
    "py": ("py", "python"),
    "bash": ("bash", "sh", "shell", "zsh"),
    "sh": ("sh", "bash", "shell", "zsh"),
    "shell": ("shell", "bash", "sh", "zsh"),
    "zsh": ("zsh", "bash", "sh", "shell"),
}

HEREDOC_RE = re.compile(r"<<-?\s*(?P<quote>['\"]?)(?P<tag>[A-Za-z_][A-Za-z0-9_]*)\1")

def canonical_lang(lang: str) -> str:
    """统一语言别名，保证 js/javascript 等代码块按同一队列顺序消费。"""
    lang = lang.strip().lower() or 'text'
    for canonical, aliases in LANG_ALIASES.items():
        if lang == canonical or lang in aliases:
            return canonical
    return lang

def scan_all_code_blocks(text: str) -> Dict[str, List[str]]:
    """扫描所有 Markdown 代码块，返回 {lang: [code1, code2, ...]}"""
    pattern = r'```(\S*)\n(.*?)```'
    matches = re.findall(pattern, text, re.DOTALL)
    blocks = {}
    for lang, code in matches:
        lang = canonical_lang(lang)
        blocks.setdefault(lang, []).append(code.strip())
    return blocks

def get_code_block(blocks: Dict[str, List[str]], lang: str, index: int = 0) -> Optional[str]:
    """按语言名读取代码块，兼容 js/javascript、ts/typescript 等别名。"""
    lang = canonical_lang(lang)
    values = blocks.get(lang, [])
    if 0 <= index < len(values):
        return values[index]
    return None

def get_next_code_block(blocks: Dict[str, List[str]], counters: Dict[str, int], lang: str) -> Optional[str]:
    """按占位符出现顺序消费同语言代码块。"""
    lang = canonical_lang(lang)
    index = counters.get(lang, 0)
    code = get_code_block(blocks, lang, index)
    if code is not None:
        counters[lang] = index + 1
    return code

def resolve_all_placeholders(bash_text: str, blocks: Dict[str, List[str]]) -> str:
    """
    替换所有占位符。
    支持格式:
    - <!-- XXX block -->
    - # XXX block
    其中 XXX 对应 blocks 中的 key（html/css/js/python/svg/json/yaml/typescript/ts...）
    """
    counters: Dict[str, int] = {}
    placeholder_pattern = re.compile(r'<!--\s*(?P<html>\w+)\s+block\s*-->|#\s*(?P<hash>\w+)\s+block')

    def replace(match: re.Match) -> str:
        lang = (match.group('html') or match.group('hash') or '').lower()
        code = get_next_code_block(blocks, counters, lang)
        return code if code is not None else match.group(0)

    return placeholder_pattern.sub(replace, bash_text)

def find_heredoc_tags(line: str) -> List[str]:
    """提取一行 Bash 命令里的 heredoc 结束标记，如 EOF。"""
    return [match.group("tag") for match in HEREDOC_RE.finditer(line)]

def split_cd_chain(line: str) -> List[str]:
    """
    将 `cd path && command` 拆成两条命令，避免执行器把整段当目录路径。
    只处理简单且最常见的 cd 链式写法，复杂 shell 仍交给 shell 执行。
    """
    if not line.startswith("cd ") or "&&" not in line:
        return [line]
    cd_part, rest = line.split("&&", 1)
    cd_cmd = cd_part.strip()
    rest_cmd = rest.strip()
    return [cmd for cmd in (cd_cmd, rest_cmd) if cmd]

def normalize_cd_target(raw_target: str, cwd: str) -> str:
    target = raw_target.strip().strip('"').strip("'")
    target = os.path.expandvars(os.path.expanduser(target))
    if not os.path.isabs(target):
        target = os.path.join(cwd, target)
    return os.path.normpath(target)

def has_unclosed_shell_quote(text: str) -> bool:
    """判断 shell 命令是否还有未闭合的单/双引号。"""
    single = False
    double = False
    escaped = False
    for ch in text:
        if escaped:
            escaped = False
            continue
        if ch == "\\":
            escaped = True
            continue
        if ch == "'" and not double:
            single = not single
        elif ch == '"' and not single:
            double = not double
    return single or double

def extract_bash_commands(text: str, blocks: Dict[str, List[str]]) -> List[str]:
    """提取 Bash 命令并替换占位符"""
    bash_text = get_code_block(blocks, 'bash') or ''
    if not bash_text:
        # 没有 ```bash 块，尝试用全文
        bash_text = text
    bash_text = resolve_all_placeholders(bash_text, blocks)
    lines = bash_text.strip().splitlines()
    cmds = []
    i = 0
    while i < len(lines):
        raw_line = lines[i].rstrip()
        line = raw_line.strip()
        i += 1
        if not line or line.startswith('#') or line.startswith('//'):
            continue
        if line.startswith('$ '):
            line = line[2:]

        command_lines = [line]
        for tag in find_heredoc_tags(line):
            while i < len(lines):
                heredoc_line = lines[i].rstrip()
                command_lines.append(heredoc_line)
                i += 1
                if heredoc_line.strip() == tag:
                    break

        while not find_heredoc_tags(command_lines[0]) and has_unclosed_shell_quote('\n'.join(command_lines)) and i < len(lines):
            continuation_line = lines[i].rstrip()
            command_lines.append(continuation_line)
            i += 1

        command = '\n'.join(command_lines)
        if len(command_lines) == 1:
            cmds.extend(split_cd_chain(command))
        else:
            cmds.append(command)
    return cmds

def is_long_running(cmd: str) -> bool:
    command_text = cmd.lower()
    first_line = cmd.splitlines()[0].lower().strip()
    if any(kw in first_line for kw in ['pip', 'install', 'brew', 'apt', 'git clone']):
        return False

    patterns = [
        'python -m http.server',
        'python3 -m http.server',
        'http.server',
        'serve_forever',
        'socketserver.tcpserver',
        'app.run(',
        'flask run',
        'uvicorn',
        'gunicorn',
        'runserver',
        'nodemon',
        '--watch',
        'npm start',
        'npm run dev',
        'npm run serve',
        'npm run watch',
        'vite --host',
        'next dev',
    ]
    for p in patterns:
        if p in command_text:
            return True

    server_script_patterns = [
        'python server.py',
        'python3 server.py',
        'python app.py',
        'python3 app.py',
        'node server.js',
        'node app.js',
    ]
    for p in server_script_patterns:
        if p in first_line:
            return True
    return False

def command_for_log(cmd: str) -> str:
    """日志里压缩 heredoc 正文，避免执行结果面板被文件内容淹没。"""
    lines = cmd.splitlines()
    if len(lines) <= 8:
        return cmd
    first = lines[0]
    tags = find_heredoc_tags(first)
    if tags:
        end = lines[-1] if lines[-1].strip() in tags else tags[-1]
        omitted = max(0, len(lines) - 2)
        return f"{first}\n... 已省略 {omitted} 行写入内容 ...\n{end}"
    return "\n".join(lines[:6] + [f"... 已省略 {len(lines) - 6} 行 ..."])

def unbuffer_python_command(cmd: str) -> str:
    """后台终端里自动关闭 Python 缓冲，让 http.server 等启动日志立即出现。"""
    first_line, sep, rest = cmd.partition("\n")
    if first_line.startswith("python3 ") and not first_line.startswith("python3 -u "):
        first_line = "python3 -u " + first_line[len("python3 "):]
    elif first_line.startswith("python ") and not first_line.startswith("python -u "):
        first_line = "python -u " + first_line[len("python "):]
    return first_line + (sep + rest if sep else "")

def shell_launch_args(cmd: str, interactive: bool = False) -> tuple[str, List[str]]:
    """为 QProcess 选择当前系统 shell。"""
    if platform.system() == "Windows":
        shell = os.environ.get("COMSPEC") or "cmd.exe"
        if interactive:
            return shell, ["/d", "/q"]
        return shell, ["/d", "/s", "/c", unbuffer_python_command(cmd)]
    shell = os.environ.get("SHELL") or "/bin/sh"
    if interactive:
        return shell, ["-l"]
    return shell, ["-lc", unbuffer_python_command(cmd)]

SNAPSHOT_SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv",
    "dist", "build", ".next", ".nuxt", ".cache", ".pytest_cache",
    HISTORY_DIR_NAME,
}
SNAPSHOT_MAX_FILE_BYTES = 8 * 1024 * 1024

def snapshot_project(root: str) -> Dict[str, bytes]:
    """记录执行前/后的项目文件快照，用于展示 diff 和撤销本轮修改。"""
    snapshot: Dict[str, bytes] = {}
    if not root or not os.path.isdir(root):
        return snapshot
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SNAPSHOT_SKIP_DIRS and not d.startswith(".Trash")]
        for filename in filenames:
            path = os.path.join(dirpath, filename)
            rel = os.path.relpath(path, root)
            try:
                if os.path.getsize(path) > SNAPSHOT_MAX_FILE_BYTES:
                    continue
                with open(path, "rb") as f:
                    snapshot[rel] = f.read()
            except OSError:
                continue
    return snapshot

def decode_text(data: Optional[bytes]) -> Optional[str]:
    if data is None:
        return ""
    if b"\x00" in data:
        return None
    for encoding in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return None

def parse_unified_diff_lines(diff_text: str) -> List[Dict[str, object]]:
    rows: List[Dict[str, object]] = []
    old_line: Optional[int] = None
    new_line: Optional[int] = None
    hunk_pattern = re.compile(r"@@ -(?P<old>\d+)(?:,\d+)? \+(?P<new>\d+)(?:,\d+)? @@")
    for line in diff_text.splitlines():
        if line.startswith("---") or line.startswith("+++"):
            continue
        if line.startswith("@@"):
            match = hunk_pattern.match(line)
            if match:
                old_line = int(match.group("old"))
                new_line = int(match.group("new"))
            rows.append({"type": "hunk", "old": "", "new": "", "text": line})
            continue
        if old_line is None or new_line is None:
            continue
        if line.startswith("+"):
            rows.append({"type": "add", "old": "", "new": new_line, "text": line[1:]})
            new_line += 1
        elif line.startswith("-"):
            rows.append({"type": "del", "old": old_line, "new": "", "text": line[1:]})
            old_line += 1
        else:
            text = line[1:] if line.startswith(" ") else line
            rows.append({"type": "ctx", "old": old_line, "new": new_line, "text": text})
            old_line += 1
            new_line += 1
    return rows

def build_file_diff(path: str, before: Optional[bytes], after: Optional[bytes]) -> Dict[str, object]:
    before_text = decode_text(before)
    after_text = decode_text(after)
    if before is None:
        status = "added"
    elif after is None:
        status = "deleted"
    else:
        status = "modified"

    if before_text is None or after_text is None:
        detail = "Binary file changed"
        additions = 0
        deletions = 0
    else:
        diff_lines = list(difflib.unified_diff(
            before_text.splitlines(),
            after_text.splitlines(),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            lineterm="",
        ))
        additions = sum(1 for line in diff_lines if line.startswith("+") and not line.startswith("+++"))
        deletions = sum(1 for line in diff_lines if line.startswith("-") and not line.startswith("---"))
        detail = "\n".join(diff_lines) if diff_lines else "(metadata changed)"
        diff_rows = parse_unified_diff_lines(detail)

    return {
        "path": path,
        "status": status,
        "before": before,
        "after": after,
        "additions": additions,
        "deletions": deletions,
        "diff": detail,
        "diff_rows": [] if before_text is None or after_text is None else diff_rows,
    }

def build_change_records(before: Dict[str, bytes], after: Dict[str, bytes]) -> List[Dict[str, object]]:
    records = []
    for path in sorted(set(before) | set(after)):
        before_bytes = before.get(path)
        after_bytes = after.get(path)
        if before_bytes != after_bytes:
            records.append(build_file_diff(path, before_bytes, after_bytes))
    return records

def format_change_summary(records: List[Dict[str, object]], include_diff: bool = True) -> str:
    if not records:
        return ""
    additions = sum(int(r["additions"]) for r in records)
    deletions = sum(int(r["deletions"]) for r in records)
    lines = [
        "",
        "Files changed:",
        f"{len(records)} files changed  +{additions}  -{deletions}",
    ]
    for record in records:
        lines.append(f"- {record['path']}  +{record['additions']}  -{record['deletions']}")
    if include_diff:
        lines.append("")
        lines.append("Diff:")
        for record in records:
            lines.append(f"\n--- {record['path']} ---")
            lines.append(str(record["diff"]))
    return "\n".join(lines)

def render_diff_html(record: Dict[str, object]) -> str:
    rows = record.get("diff_rows") or []
    if not rows:
        return (
            "<html><body style='font-family: Menlo, monospace; color: #172033;'>"
            f"<pre>{html.escape(str(record.get('diff', '')))}</pre>"
            "</body></html>"
        )

    html_rows = [
        "<html><body style='margin:0; background:#ffffff;'>",
        "<table cellspacing='0' cellpadding='0' width='100%' "
        "style='border-collapse:collapse; font-family: Menlo, monospace; font-size:12px;'>",
    ]
    for row in rows:
        row_type = row.get("type")
        text = html.escape(str(row.get("text", ""))).replace(" ", "&nbsp;")
        old_num = html.escape(str(row.get("old", "")))
        new_num = html.escape(str(row.get("new", "")))
        if row_type == "add":
            bg = "#e6f6ed"
            border = "#12b76a"
            num_color = "#079455"
            marker = "+"
        elif row_type == "del":
            bg = "#ffecec"
            border = "#ef4444"
            num_color = "#dc2626"
            marker = "-"
        elif row_type == "hunk":
            bg = "#eef4ff"
            border = "#8ea8ff"
            num_color = "#657089"
            marker = " "
        else:
            bg = "#ffffff"
            border = "#ffffff"
            num_color = "#657089"
            marker = " "
        html_rows.append(
            f"<tr style='background:{bg};'>"
            f"<td width='48' style='color:{num_color}; text-align:right; padding:3px 8px; border-left:4px solid {border};'>{old_num}</td>"
            f"<td width='48' style='color:{num_color}; text-align:right; padding:3px 8px;'>{new_num}</td>"
            f"<td width='18' style='color:{num_color}; padding:3px 4px;'>{marker}</td>"
            f"<td style='color:#172033; padding:3px 8px;'>{text}</td>"
            "</tr>"
        )
    html_rows.extend(["</table>", "</body></html>"])
    return "".join(html_rows)

def apply_change_records(root: str, records: List[Dict[str, object]], target_key: str, expected_key: str) -> Dict[str, object]:
    conflicts = []
    for record in records:
        rel = str(record["path"])
        path = os.path.join(root, rel)
        expected = record.get(expected_key)
        try:
            current = None
            if os.path.exists(path):
                with open(path, "rb") as f:
                    current = f.read()
            if current != expected:
                conflicts.append(rel)
        except OSError:
            conflicts.append(rel)

    if conflicts:
        return {"applied": 0, "skipped": len(conflicts), "conflicts": conflicts}

    applied = 0
    write_errors = []
    applied_records = []
    for record in records:
        rel = str(record["path"])
        path = os.path.join(root, rel)
        target = record.get(target_key)
        try:
            if target is None:
                if os.path.exists(path):
                    os.remove(path)
            else:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "wb") as f:
                    f.write(target)
            applied_records.append(record)
            applied += 1
        except OSError:
            write_errors.append(rel)
            break

    if write_errors:
        for record in reversed(applied_records):
            rel = str(record["path"])
            path = os.path.join(root, rel)
            expected = record.get(expected_key)
            try:
                if expected is None:
                    if os.path.exists(path):
                        os.remove(path)
                else:
                    os.makedirs(os.path.dirname(path), exist_ok=True)
                    with open(path, "wb") as f:
                        f.write(expected)
            except OSError:
                pass
        applied = 0

    return {
        "applied": applied,
        "skipped": len(write_errors),
        "conflicts": write_errors,
    }

def restore_change_records(root: str, records: List[Dict[str, object]]) -> Dict[str, object]:
    return apply_change_records(root, records, "before", "after")

def redo_change_records(root: str, records: List[Dict[str, object]]) -> Dict[str, object]:
    return apply_change_records(root, records, "after", "before")

def safe_thread_id(thread_id: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]", "_", thread_id or DEFAULT_THREAD_ID)
    return cleaned or DEFAULT_THREAD_ID

def history_dir(root: str, thread_id: str = DEFAULT_THREAD_ID) -> str:
    thread_id = safe_thread_id(thread_id)
    if thread_id == DEFAULT_THREAD_ID:
        return os.path.join(root, HISTORY_DIR_NAME)
    return os.path.join(root, HISTORY_DIR_NAME, THREADS_DIR_NAME, thread_id)

def threads_index_path(root: str) -> str:
    return os.path.join(root, HISTORY_DIR_NAME, THREADS_INDEX_FILE_NAME)

def history_path(root: str, thread_id: str = DEFAULT_THREAD_ID) -> str:
    return os.path.join(history_dir(root, thread_id), HISTORY_FILE_NAME)

def default_thread() -> Dict[str, object]:
    return {
        "id": DEFAULT_THREAD_ID,
        "title": "默认会话",
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }

def normalize_threads(raw_threads: object) -> List[Dict[str, object]]:
    threads: List[Dict[str, object]] = []
    if isinstance(raw_threads, list):
        for raw in raw_threads:
            if not isinstance(raw, dict):
                continue
            thread_id = safe_thread_id(str(raw.get("id", "")))
            title = str(raw.get("title", "")).strip() or ("默认会话" if thread_id == DEFAULT_THREAD_ID else "新会话")
            threads.append({
                "id": thread_id,
                "title": title,
                "created_at": str(raw.get("created_at", "")) or datetime.now().isoformat(timespec="seconds"),
            })
    if not any(thread.get("id") == DEFAULT_THREAD_ID for thread in threads):
        threads.insert(0, default_thread())
    seen = set()
    normalized = []
    for thread in threads:
        thread_id = str(thread["id"])
        if thread_id in seen:
            continue
        seen.add(thread_id)
        normalized.append(thread)
    normalized.sort(key=lambda item: (0 if item["id"] == DEFAULT_THREAD_ID else 1, str(item.get("created_at", ""))))
    return normalized

def load_workspace_threads(root: str) -> List[Dict[str, object]]:
    path = threads_index_path(root)
    if not os.path.isfile(path):
        return [default_thread()]
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, json.JSONDecodeError):
        return [default_thread()]
    if payload.get("version") != HISTORY_VERSION:
        return [default_thread()]
    return normalize_threads(payload.get("threads"))

def save_workspace_threads(root: str, threads: List[Dict[str, object]]) -> bool:
    if not root:
        return False
    try:
        os.makedirs(os.path.join(root, HISTORY_DIR_NAME), exist_ok=True)
        path = threads_index_path(root)
        tmp_path = path + ".tmp"
        payload = {
            "version": HISTORY_VERSION,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "threads": normalize_threads(threads),
        }
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
        return True
    except OSError:
        return False

def clear_thread_history(root: str, thread_id: str) -> bool:
    path = history_dir(root, thread_id)
    if not os.path.exists(path):
        return True
    if safe_thread_id(thread_id) == DEFAULT_THREAD_ID:
        history = history_path(root, DEFAULT_THREAD_ID)
        if os.path.isfile(history):
            try:
                os.remove(history)
            except OSError:
                return False
        return True
    try:
        shutil.rmtree(path)
        return True
    except OSError:
        return False

def delete_workspace_thread(root: str, thread_id: str, threads: List[Dict[str, object]]) -> bool:
    thread_id = safe_thread_id(thread_id)
    if thread_id == DEFAULT_THREAD_ID:
        return False
    if not clear_thread_history(root, thread_id):
        return False
    remaining = [thread for thread in normalize_threads(threads) if thread.get("id") != thread_id]
    return save_workspace_threads(root, remaining)

def make_thread_title(index: int) -> str:
    return f"会话 {index}"

def create_workspace_thread(root: str, threads: List[Dict[str, object]]) -> Dict[str, object]:
    existing = {str(thread.get("id")) for thread in threads}
    now = datetime.now()
    base = now.strftime("session-%Y%m%d-%H%M%S")
    thread_id = base
    suffix = 2
    while thread_id in existing:
        thread_id = f"{base}-{suffix}"
        suffix += 1
    thread = {
        "id": thread_id,
        "title": make_thread_title(len(threads) + 1),
        "created_at": now.isoformat(timespec="seconds"),
    }
    updated = normalize_threads([*threads, thread])
    save_workspace_threads(root, updated)
    os.makedirs(history_dir(root, thread_id), exist_ok=True)
    return thread

def bytes_to_store(value: Optional[bytes]) -> Optional[str]:
    if value is None:
        return None
    return base64.b64encode(value).decode("ascii")

def bytes_from_store(value: Optional[str]) -> Optional[bytes]:
    if value is None:
        return None
    try:
        return base64.b64decode(value.encode("ascii"))
    except (ValueError, TypeError):
        return None

def serialize_change_record(record: Dict[str, object]) -> Dict[str, object]:
    return {
        "path": record.get("path", ""),
        "status": record.get("status", "modified"),
        "before": bytes_to_store(record.get("before") if isinstance(record.get("before"), bytes) else None),
        "after": bytes_to_store(record.get("after") if isinstance(record.get("after"), bytes) else None),
        "undone": bool(record.get("undone", False)),
    }

def deserialize_change_record(record: Dict[str, object]) -> Optional[Dict[str, object]]:
    path = str(record.get("path", ""))
    if not path:
        return None
    before = bytes_from_store(record.get("before"))
    after = bytes_from_store(record.get("after"))
    rebuilt = build_file_diff(path, before, after)
    rebuilt["undone"] = bool(record.get("undone", False))
    return rebuilt

def load_workspace_history(root: str, thread_id: str = DEFAULT_THREAD_ID) -> List[Dict[str, object]]:
    path = history_path(root, thread_id)
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    if payload.get("version") != HISTORY_VERSION:
        return []
    entries = payload.get("entries")
    return entries if isinstance(entries, list) else []

def save_workspace_history(root: str, entries: List[Dict[str, object]], thread_id: str = DEFAULT_THREAD_ID) -> bool:
    if not root:
        return False
    try:
        os.makedirs(history_dir(root, thread_id), exist_ok=True)
        tmp_path = history_path(root, thread_id) + ".tmp"
        payload = {
            "version": HISTORY_VERSION,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "entries": entries,
        }
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, history_path(root, thread_id))
        return True
    except OSError:
        return False

def clear_workspace_history(root: str, thread_id: str = DEFAULT_THREAD_ID) -> bool:
    path = history_dir(root, thread_id)
    if not os.path.exists(path):
        return True
    if safe_thread_id(thread_id) == DEFAULT_THREAD_ID:
        return clear_thread_history(root, thread_id)
    try:
        shutil.rmtree(path)
        return True
    except OSError:
        return False

def animate_widget_in(widget: QWidget, duration: int = 180):
    """轻量淡入动画，让聊天卡片出现得更自然。"""
    effect = QGraphicsOpacityEffect(widget)
    widget.setGraphicsEffect(effect)
    animation = QPropertyAnimation(effect, b"opacity", widget)
    animation.setDuration(duration)
    animation.setStartValue(0.0)
    animation.setEndValue(1.0)
    animation.setEasingCurve(QEasingCurve.Type.OutCubic)

    def cleanup():
        widget.setGraphicsEffect(None)
        widget._fade_animation = None

    animation.finished.connect(cleanup)
    widget._fade_animation = animation
    animation.start()

def animate_widget_out(widget: QWidget, on_finished, duration: int = 120):
    """轻量淡出动画，结束后执行移除逻辑。"""
    effect = QGraphicsOpacityEffect(widget)
    widget.setGraphicsEffect(effect)
    animation = QPropertyAnimation(effect, b"opacity", widget)
    animation.setDuration(duration)
    animation.setStartValue(1.0)
    animation.setEndValue(0.0)
    animation.setEasingCurve(QEasingCurve.Type.InCubic)
    animation.finished.connect(on_finished)
    widget._fade_animation = animation
    animation.start()

class TerminalResizeHandle(QFrame):
    resize_requested = Signal(int)

    def __init__(self, terminal_panel: "TerminalPanel", parent=None):
        super().__init__(parent)
        self.terminal_panel = terminal_panel
        self._dragging = False
        self._start_y = 0
        self._start_height = 0
        self.setFixedHeight(7)
        self.setCursor(Qt.CursorShape.SizeVerCursor)
        self.setVisible(False)
        self.setStyleSheet(f"""
            QFrame {{
                background: {COLORS['bg']};
                border: none;
                border-top: 1px solid {COLORS['border']};
                border-bottom: 1px solid {COLORS['border']};
            }}
            QFrame:hover {{
                background: {COLORS['accent_light']};
                border-top: 1px solid #d2c6ff;
                border-bottom: 1px solid #d2c6ff;
            }}
        """)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        grip = QFrame()
        grip.setFixedSize(72, 3)
        grip.setStyleSheet(f"""
            QFrame {{
                background: {COLORS['border_strong']};
                border: none;
                border-radius: 2px;
            }}
        """)
        layout.addStretch()
        layout.addWidget(grip, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addStretch()

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._start_y = int(event.globalPosition().y())
            self._start_height = self.terminal_panel.current_height()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._dragging:
            current_y = int(event.globalPosition().y())
            self.resize_requested.emit(self._start_height + self._start_y - current_y)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._dragging = False
            event.accept()
            return
        super().mouseReleaseEvent(event)

class SidebarResizeHandle(QFrame):
    resize_requested = Signal(int)

    def __init__(self, sidebar: "Sidebar", parent=None):
        super().__init__(parent)
        self.sidebar = sidebar
        self._dragging = False
        self._start_x = 0
        self._start_width = 0
        self.setFixedWidth(24)
        self.setCursor(Qt.CursorShape.SizeHorCursor)
        self.setStyleSheet(f"""
            QFrame {{
                background: {COLORS['bg']};
                border: none;
            }}
            QFrame:hover {{
                background: {COLORS['surface_alt']};
            }}
        """)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)
        self._layout.addStretch()
        self.grip = QFrame()
        self.grip.setFixedSize(3, 72)
        self.grip.setStyleSheet(f"""
            QFrame {{
                background: {COLORS['border_strong']};
                border: none;
                border-radius: 2px;
            }}
        """)
        self._layout.addWidget(self.grip, alignment=Qt.AlignmentFlag.AlignCenter)
        self._layout.addStretch()
        self.set_grip_visible(False)

    def install_toggle_button(self, button: QToolButton):
        self._layout.insertWidget(0, button, alignment=Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)

    def set_grip_visible(self, visible: bool):
        self.grip.setFixedHeight(72 if visible else 0)
        self.grip.setVisible(visible)

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton and not self.sidebar._collapsed:
            self._dragging = True
            self._start_x = int(event.globalPosition().x())
            self._start_width = self.sidebar.current_width()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._dragging:
            current_x = int(event.globalPosition().x())
            self.resize_requested.emit(self._start_width + current_x - self._start_x)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._dragging = False
            event.accept()
            return
        super().mouseReleaseEvent(event)

# ============================================================
# 进程管理
# ============================================================
class TerminalOutputEdit(QPlainTextEdit):
    command_submitted = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.prompt = "$ "
        self.input_start = 0
        self.input_enabled = False
        self.setUndoRedoEnabled(False)

    def set_input_enabled(self, enabled: bool):
        self.input_enabled = enabled
        self.setCursorWidth(2 if enabled else 0)
        if enabled and not self.has_prompt():
            self.show_prompt()

    def has_prompt(self) -> bool:
        return self.input_start > 0

    def show_prompt(self):
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        if self.toPlainText() and not self.toPlainText().endswith("\n"):
            cursor.insertText("\n")
        cursor.insertText(self.prompt)
        self.input_start = cursor.position()
        self.setTextCursor(cursor)
        self.ensureCursorVisible()

    def current_command(self) -> str:
        end = max(self.input_start, self.document().characterCount() - 1)
        cursor = QTextCursor(self.document())
        cursor.setPosition(self.input_start)
        cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
        return cursor.selectedText().replace("\u2029", "\n")

    def append_process_text(self, text: str):
        if not text:
            return
        command = self.current_command() if self.has_prompt() else ""
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        if self.has_prompt():
            prompt_start = max(0, self.input_start - len(self.prompt))
            cursor.setPosition(prompt_start)
            cursor.movePosition(QTextCursor.MoveOperation.End, QTextCursor.MoveMode.KeepAnchor)
            cursor.removeSelectedText()
        cursor.insertText(text)
        if self.input_enabled:
            if text and not text.endswith("\n"):
                cursor.insertText("\n")
            cursor.insertText(self.prompt)
            self.input_start = cursor.position()
            cursor.insertText(command)
        else:
            self.input_start = 0
        self.setTextCursor(cursor)
        self.ensureCursorVisible()

    def ensure_input_cursor(self):
        cursor = self.textCursor()
        if cursor.position() < self.input_start:
            cursor.movePosition(QTextCursor.MoveOperation.End)
            self.setTextCursor(cursor)

    def selection_crosses_prompt(self) -> bool:
        cursor = self.textCursor()
        return cursor.hasSelection() and min(cursor.selectionStart(), cursor.selectionEnd()) < self.input_start

    def keyPressEvent(self, event):
        if not self.input_enabled:
            if event.matches(QKeySequence.StandardKey.Copy):
                self.copy()
                return
            if event.matches(QKeySequence.StandardKey.SelectAll):
                self.selectAll()
                return
            if event.key() in (
                Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down,
                Qt.Key.Key_Home, Qt.Key.Key_End, Qt.Key.Key_PageUp, Qt.Key.Key_PageDown,
            ):
                super().keyPressEvent(event)
                return
            event.ignore()
            return
        if event.matches(QKeySequence.StandardKey.Copy):
            self.copy()
            return
        if event.matches(QKeySequence.StandardKey.SelectAll):
            self.selectAll()
            return
        if event.matches(QKeySequence.StandardKey.Paste):
            self.ensure_input_cursor()
            super().keyPressEvent(event)
            return

        key = event.key()
        cursor = self.textCursor()
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.ensure_input_cursor()
            command = self.current_command().strip()
            cursor = self.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            cursor.insertText("\n")
            self.setTextCursor(cursor)
            if command:
                self.command_submitted.emit(command)
            if self.input_enabled:
                self.show_prompt()
            return
        if key == Qt.Key.Key_Backspace and (cursor.position() <= self.input_start or self.selection_crosses_prompt()):
            return
        if key == Qt.Key.Key_Left and cursor.position() <= self.input_start:
            return
        if key == Qt.Key.Key_Home:
            cursor.setPosition(self.input_start)
            self.setTextCursor(cursor)
            return
        if self.selection_crosses_prompt():
            cursor.clearSelection()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            self.setTextCursor(cursor)
        self.ensure_input_cursor()
        super().keyPressEvent(event)

class ManagedProcess(QWidget):
    remove_requested = Signal(object)
    
    def __init__(self, cmd: str, cwd: str, name: str = "", interactive: bool = False):
        super().__init__()
        self.cmd = cmd
        self.cwd = cwd
        self.name = name or cmd[:40]
        self.interactive = interactive
        self.process: Optional[QProcess] = None
        self.setup_ui()
        self.start_process()
    
    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 5, 10, 10)
        layout.setSpacing(6)
        self.setStyleSheet(f"background: {COLORS['terminal_panel']}; border: none;")

        self.output = TerminalOutputEdit()
        self.output.setReadOnly(False)
        self.output.command_submitted.connect(self.send_input)
        self.output.setMaximumBlockCount(3000)
        self.output.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.output.customContextMenuRequested.connect(self.show_output_context_menu)
        self.output.setStyleSheet(f"""
            QPlainTextEdit {{
                background: {COLORS['terminal_card']};
                color: {COLORS['terminal_text']};
                border: 1px solid {COLORS['border']};
                border-radius: 12px;
                font-family: 'SF Mono', 'Menlo', monospace;
                font-size: 11px;
                padding: 8px;
                selection-background-color: {COLORS['accent_light']};
            }}
            QScrollBar:vertical {{
                background: transparent;
                width: 8px;
                margin: 4px 2px 4px 0;
            }}
            QScrollBar::handle:vertical {{
                background: {COLORS['border_strong']};
                border-radius: 4px;
                min-height: 30px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0;
            }}
        """)
        layout.addWidget(self.output)

        self.copy_btn = QPushButton("复制日志", self.output)
        self.copy_btn.setCursor(Qt.PointingHandCursor)
        self.copy_btn.setFixedHeight(24)
        self.copy_btn.clicked.connect(self.copy_output)
        self.copy_btn.setStyleSheet(f"""
            QPushButton {{
                background: {COLORS['surface']};
                color: {COLORS['accent_dark']};
                border: 1px solid #d8d0ff;
                border-radius: 8px;
                padding: 3px 10px;
                font-size: 11px;
                font-weight: 800;
            }}
            QPushButton:hover {{
                background: {COLORS['accent_light']};
                border-color: {COLORS['accent']};
            }}
        """)
        self.copy_btn.adjustSize()
        self.position_copy_button()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.position_copy_button()

    def position_copy_button(self):
        if not hasattr(self, "copy_btn"):
            return
        x = max(8, self.output.width() - self.copy_btn.width() - 14)
        self.copy_btn.move(x, 10)
    
    def start_process(self):
        self.process = QProcess()
        self.process.setWorkingDirectory(self.cwd)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONUNBUFFERED", "1")
        self.process.setProcessEnvironment(env)
        self.process.readyReadStandardOutput.connect(self.read_output)
        self.process.started.connect(self.on_started)
        self.process.errorOccurred.connect(self.on_error)
        self.process.finished.connect(self.on_finished)
        shell, args = shell_launch_args(self.cmd, interactive=self.interactive)
        self.process.start(shell, args)

    def on_started(self):
        if self.interactive:
            self.output.append_process_text(f"# shell: {self.name}\n# cwd: {self.cwd}\n")
            self.output.set_input_enabled(True)
        else:
            self.output.set_input_enabled(False)
            self.output.append_process_text(f"$ {self.cmd}\n# cwd: {self.cwd}\n")

    def read_output(self):
        if not self.process:
            return
        text = self.process.readAllStandardOutput().data().decode('utf-8', errors='replace')
        if text:
            self.output.append_process_text(text)

    def on_error(self, _err):
        try:
            message = self.process.errorString() if self.process else "未知错误"
        except RuntimeError:
            message = "进程已关闭"
        self.output.append_process_text(f"\n--- 启动失败: {message} ---")

    def on_finished(self, exit_code: int, _exit_status):
        self.output.set_input_enabled(False)
        self.output.append_process_text(f"\n--- 退出码: {exit_code} ---")

    def copy_output(self):
        QApplication.clipboard().setText(self.output.toPlainText())

    def copy_output_smart(self):
        cursor = self.output.textCursor()
        selected = cursor.selectedText().replace("\u2029", "\n")
        QApplication.clipboard().setText(selected if selected else self.output.toPlainText())

    def show_output_context_menu(self, pos):
        menu = QMenu(self.output)
        has_selection = self.output.textCursor().hasSelection()
        copy_action = QAction("复制" if has_selection else "复制全部", self.output)
        copy_action.triggered.connect(self.copy_output_smart)
        menu.addAction(copy_action)
        select_all_action = QAction("全选", self.output)
        select_all_action.triggered.connect(self.output.selectAll)
        menu.addAction(select_all_action)
        menu.exec(self.output.mapToGlobal(pos))

    def send_input(self, command: str):
        if not self.process or self.process.state() != QProcess.Running:
            self.output.append_process_text("\n--- 终端未运行 ---")
            return
        command = command.rstrip("\n")
        if not command:
            return
        self.process.write((command + "\n").encode("utf-8"))
        self.process.waitForBytesWritten(800)
    
    def kill(self):
        if not self.process:
            return
        try:
            if self.process.state() != QProcess.ProcessState.NotRunning:
                self.process.terminate()
                if not self.process.waitForFinished(1200):
                    self.process.kill()
                    self.process.waitForFinished(1200)
                self.output.set_input_enabled(False)
                self.output.append_process_text("\n--- 已关闭 ---")
        except RuntimeError:
            pass
        finally:
            self.process = None

# ============================================================
# 底部终端面板
# ============================================================
class TerminalTabCard(QFrame):
    selected = Signal(object)
    close_requested = Signal(object)

    def __init__(self, proc: ManagedProcess, title: str, active: bool = False, parent=None):
        super().__init__(parent)
        self.proc = proc
        self.title = title
        self.active = active
        self.setObjectName("terminalTabCard")
        self.setCursor(Qt.PointingHandCursor)
        self.setup_ui()
        self.apply_style()

    def setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 5, 7, 5)
        layout.setSpacing(7)

        icon_label = QLabel()
        icon_label.setPixmap(terminal_icon(COLORS["text"], 16).pixmap(16, 16))
        icon_label.setFixedSize(18, 18)
        icon_label.setStyleSheet("background: transparent; border: none;")
        layout.addWidget(icon_label)

        self.title_label = QLabel(self.title)
        self.title_label.setStyleSheet(f"""
            QLabel {{
                color: {COLORS['text']};
                background: transparent;
                border: none;
                font-size: 13px;
                font-weight: 900;
            }}
        """)
        self.title_label.setMinimumWidth(56)
        self.title_label.setMaximumWidth(170)
        layout.addWidget(self.title_label)

        self.close_btn = QToolButton(self)
        self.close_btn.setCursor(Qt.PointingHandCursor)
        self.close_btn.setText("×")
        self.close_btn.setFixedSize(20, 20)
        self.close_btn.setStyleSheet(f"""
            QToolButton {{
                background: transparent;
                color: {COLORS['text_secondary']};
                border: none;
                border-radius: 8px;
                font-size: 16px;
                font-weight: 900;
                padding-bottom: 1px;
            }}
            QToolButton:hover {{
                background: {COLORS['border']};
                color: {COLORS['text']};
            }}
        """)
        self.close_btn.clicked.connect(lambda: self.close_requested.emit(self.proc))
        layout.addWidget(self.close_btn)

    def apply_style(self):
        background = "#eef1f6" if self.active else "#f4f5f8"
        border = COLORS["border"] if self.active else "transparent"
        self.setStyleSheet(f"""
            QFrame#terminalTabCard {{
                background: {background};
                border: 1px solid {border};
                border-radius: 12px;
            }}
            QFrame#terminalTabCard:hover {{
                background: #edf0f5;
            }}
        """)

    def set_active(self, active: bool):
        self.active = active
        self.apply_style()

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton and not self.close_btn.geometry().contains(event.position().toPoint()):
            self.selected.emit(self.proc)
            event.accept()
            return
        super().mousePressEvent(event)

class TerminalPanel(QWidget):
    collapsed_signal = Signal()
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("terminalPanel")
        self.project_root = ""
        self.expanded_height = 240
        self.min_expanded_height = 130
        self.max_expanded_height = 560
        self.setMinimumHeight(0)
        self.setMaximumHeight(0)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 8)
        layout.setSpacing(5)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.setStyleSheet(f"""
            QWidget#terminalPanel {{
                background: {COLORS['terminal_panel']};
                border-top: 1px solid {COLORS['border']};
            }}
        """)

        header_shell = QWidget(self)
        header_shell.setFixedHeight(24)
        header_shell.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        header_shell.setStyleSheet("background: transparent; border: none;")
        header = QHBoxLayout(header_shell)
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)
        title = QLabel("终端")
        title.setFixedHeight(22)
        title.setStyleSheet(f"color: {COLORS['terminal_text']}; font-weight: 800; font-size: 12px; background: transparent;")
        self.count_label = QLabel("0 个进程")
        self.count_label.setFixedHeight(22)
        self.count_label.setStyleSheet(f"color: {COLORS['terminal_muted']}; font-size: 11px; background: transparent;")
        header.addWidget(title)
        header.addWidget(self.count_label)
        header.addStretch()
        collapse_btn = QPushButton("─")
        collapse_btn.setFixedSize(26, 22)
        collapse_btn.setCursor(Qt.PointingHandCursor)
        collapse_btn.setToolTip("")
        collapse_btn.setStyleSheet(f"""
            QPushButton {{
                background: {COLORS['surface_alt']};
                color: {COLORS['text']};
                border: 1px solid {COLORS['border']};
                border-radius: 7px;
                font-size: 12px;
                font-weight: 500;
            }}
            QPushButton:hover {{
                background: {COLORS['accent_light']};
                color: {COLORS['accent_dark']};
            }}
        """)
        collapse_btn.clicked.connect(self.collapse)
        header.addWidget(collapse_btn)
        layout.addWidget(header_shell, 0, Qt.AlignmentFlag.AlignTop)
        
        self.tab_row_shell = QWidget(self)
        self.tab_row_shell.setFixedHeight(34)
        self.tab_row_shell.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.tab_row_shell.setStyleSheet("background: transparent; border: none;")
        self.tab_row = QHBoxLayout(self.tab_row_shell)
        self.tab_row.setContentsMargins(0, 0, 0, 0)
        self.tab_row.setSpacing(8)
        self.tab_cards: Dict[ManagedProcess, TerminalTabCard] = {}

        self.add_btn = QToolButton(self)
        self.add_btn.setCursor(Qt.PointingHandCursor)
        self.add_btn.setIcon(line_icon("plus", COLORS["text_secondary"], 20))
        self.add_btn.setIconSize(QSize(18, 18))
        self.add_btn.setFixedSize(32, 30)
        self.add_btn.setStyleSheet(f"""
            QToolButton {{
                background: transparent;
                border: none;
                border-radius: 10px;
            }}
            QToolButton:hover {{
                background: {COLORS['surface_alt']};
            }}
        """)
        self.add_btn.clicked.connect(self.create_interactive_terminal)
        self.tab_row.addWidget(self.add_btn)
        self.tab_row.addStretch()
        layout.addWidget(self.tab_row_shell, 0, Qt.AlignmentFlag.AlignTop)

        self.stack = QStackedWidget()
        self.stack.setStyleSheet("QStackedWidget { background: transparent; border: none; }")
        self.stack.setVisible(False)
        layout.addWidget(self.stack, 1)
        layout.addItem(QSpacerItem(0, 0, QSizePolicy.Minimum, QSizePolicy.Expanding))

        self.processes: List[ManagedProcess] = []
        self.setVisible(False)

    def set_project_root(self, path: str):
        self.project_root = path

    def terminal_title(self, cwd: str, index: Optional[int] = None) -> str:
        root = self.project_root or cwd
        name = os.path.basename(os.path.normpath(root)) or "terminal"
        if index and index > 1:
            return f"{name} {index}"
        return name
    
    def add_process(self, cmd: str, cwd: str, name: str = "", interactive: bool = False):
        label = self.terminal_title(cwd, len(self.processes) + 1)
        proc = ManagedProcess(cmd, cwd, label, interactive=interactive)
        proc.remove_requested.connect(self.remove_process)
        if proc.process:
            proc.process.finished.connect(lambda _code, _status, p=proc: self.refresh_process_state(p))
        self.processes.append(proc)
        self.stack.addWidget(proc)
        card = TerminalTabCard(proc, label, parent=self)
        card.selected.connect(self.select_process)
        card.close_requested.connect(self.remove_process)
        self.tab_cards[proc] = card
        insert_at = max(0, self.tab_row.count() - 2)
        self.tab_row.insertWidget(insert_at, card)
        self.select_process(proc)
        self.stack.setVisible(True)
        self.setVisible(True)
        self.refresh_header()
        self.expand()
        return proc

    def create_interactive_terminal(self):
        cwd = self.project_root or os.path.expanduser("~")
        self.add_process("", cwd, self.terminal_title(cwd, len(self.processes) + 1), interactive=True)

    def select_process(self, proc: ManagedProcess):
        if proc not in self.processes:
            return
        self.stack.setCurrentWidget(proc)
        for process, card in self.tab_cards.items():
            card.set_active(process is proc)
        proc.output.setFocus()
        proc.output.ensure_input_cursor()

    def refresh_process_state(self, proc: ManagedProcess):
        try:
            if self.stack.currentWidget() is proc:
                proc.output.setFocus()
        except RuntimeError:
            return
    
    def remove_process(self, proc: ManagedProcess):
        proc.kill()
        card = self.tab_cards.pop(proc, None)
        if card:
            self.tab_row.removeWidget(card)
            card.deleteLater()
        idx = self.stack.indexOf(proc)
        if idx >= 0:
            self.stack.removeWidget(proc)
        if proc in self.processes:
            self.processes.remove(proc)
        proc.deleteLater()
        if not self.processes:
            self.stack.setVisible(False)
            self.setVisible(False)
            self.collapse()
        else:
            self.select_process(self.processes[min(idx if idx >= 0 else 0, len(self.processes) - 1)])
        self.refresh_header()

    def close_all_processes(self):
        for proc in list(self.processes):
            self.remove_process(proc)

    def closeEvent(self, event):
        self.close_all_processes()
        super().closeEvent(event)
    
    def current_height(self) -> int:
        return self.maximumHeight() if self.maximumHeight() > 0 else self.expanded_height

    def set_expanded_height(self, height: int):
        available_parent_height = self.parentWidget().height() if self.parentWidget() else self.max_expanded_height
        max_height = min(self.max_expanded_height, max(self.min_expanded_height, available_parent_height - 240))
        self.expanded_height = max(self.min_expanded_height, min(height, max_height))
        if self.maximumHeight() > 0:
            self.setMaximumHeight(self.expanded_height)
            self.setMinimumHeight(self.expanded_height)
    
    def expand(self):
        self.setVisible(True)
        self.setMaximumHeight(self.expanded_height)
        self.setMinimumHeight(self.expanded_height)
    
    def collapse(self):
        self.setMaximumHeight(0)
        self.setMinimumHeight(0)
        self.collapsed_signal.emit()
    
    def toggle(self):
        if self.maximumHeight() > 0:
            self.collapse()
        else:
            self.expand()
            self.collapsed_signal.emit()
    
    def count(self) -> int:
        return len(self.processes)

    def refresh_header(self):
        self.count_label.setText(f"{self.count()} 个进程")

# ============================================================
# 执行线程
# ============================================================
class ExecuteWorker(QThread):
    output_signal = Signal(str)
    long_running_signal = Signal(str, str, str)
    finished_signal = Signal(str)
    
    def __init__(self, commands: List[str], cwd: str):
        super().__init__()
        self.commands = commands
        self.cwd = cwd
    
    def run(self):
        cwd = self.cwd
        outputs = []
        for i, cmd in enumerate(self.commands, 1):
            display_cmd = command_for_log(cmd)
            if cmd.startswith('cd '):
                target = normalize_cd_target(cmd[3:], cwd)
                if os.path.isdir(target):
                    cwd = target
                    outputs.append(f"[{i}] 📂 {display_cmd}\n✅ → {cwd}")
                else:
                    outputs.append(f"[{i}] 📂 {display_cmd}\n⚠️ 目录不存在")
                self.output_signal.emit(outputs[-1])
                continue
            if not is_safe(cmd):
                outputs.append(f"[{i}] ⛔ 拒绝: {display_cmd}")
                self.output_signal.emit(outputs[-1])
                continue
            if is_long_running(cmd):
                outputs.append(f"[{i}] 🔵 后台: {display_cmd}")
                self.long_running_signal.emit(cmd, cwd, display_cmd.splitlines()[0][:40])
                self.output_signal.emit(outputs[-1])
                continue
            try:
                r = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True, timeout=30)
                out = r.stdout.strip()
                if r.stderr.strip():
                    out += "\n" + r.stderr.strip()
                if r.returncode != 0:
                    out += f"\n[退出码: {r.returncode}]"
                outputs.append(f"[{i}] 💻 {display_cmd}\n📤 {out or '(无输出)'}")
            except subprocess.TimeoutExpired:
                outputs.append(f"[{i}] ⏱️ 超时 → 后台: {display_cmd}")
                self.long_running_signal.emit(cmd, cwd, display_cmd.splitlines()[0][:40])
            except Exception as e:
                outputs.append(f"[{i}] ❌ {e}")
            self.output_signal.emit(outputs[-1])
        self.finished_signal.emit('\n\n'.join(outputs))

# ============================================================
# 对话气泡
# ============================================================
class ChatBubble(QFrame):
    paste_ai_requested = Signal()
    copy_requested = Signal()

    def __init__(
        self,
        role: str,
        content: str = "",
        show_copy: bool = False,
        parent=None,
        copy_text: str = "复制",
        show_paste_ai: bool = False,
        prompt_input_text: str = "",
        scrollable: bool = False,
        max_content_height: int = 220,
    ):
        super().__init__(parent)
        self.role = role
        self.content = content
        self.show_copy = show_copy
        self.copy_text = copy_text
        self.show_paste_ai = show_paste_ai
        self.prompt_input_text = prompt_input_text
        self.scrollable = scrollable
        self.max_content_height = max_content_height
        self.min_content_height = 58
        self._height_adjust_scheduled = False
        self._last_content_width = 0
        self.setup_ui()
    
    def setup_ui(self):
        colors = {
            "user": (COLORS["card_user"], COLORS["border"], "你"),
            "ai": (COLORS["card_ai"], "#d7ccff", "AI 输出"),
            "system": (COLORS["card_system"], COLORS["border"], "执行结果"),
        }
        bg, border, label_text = colors.get(getattr(self, 'role', 'system'), colors["system"])
        setattr(self, '_bg', bg)
        setattr(self, '_border', border)
        
        self.setStyleSheet(f"QFrame {{ background: {bg}; border: 1px solid {border}; border-radius: 18px; margin: 4px 0; }}")
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)
        
        header = QHBoxLayout()
        role_label = QLabel(label_text)
        role_label.setStyleSheet(f"color: {COLORS['text']}; font-weight: 700; font-size: 13px; background: transparent;")
        header.addWidget(role_label)
        header.addStretch()
        if self.show_copy:
            self.copy_btn = QPushButton(self.copy_text)
            self.copy_btn.setCursor(Qt.PointingHandCursor)
            self.copy_btn.setFixedHeight(28)
            self.copy_btn.setStyleSheet(f"""
                QPushButton {{
                    background: {COLORS['surface']};
                    color: {COLORS['accent_dark']};
                    border: 1px solid #d8d0ff;
                    border-radius: 9px;
                    padding: 5px 12px;
                    font-size: 12px;
                    font-weight: 700;
                }}
                QPushButton:hover {{
                    background: {COLORS['accent_light']};
                    border-color: {COLORS['accent']};
                }}
            """)
            self.copy_btn.clicked.connect(self.copy_content)
            header.addWidget(self.copy_btn)
        if self.show_paste_ai:
            self.paste_ai_btn = QPushButton("跳过")
            self.paste_ai_btn.setCursor(Qt.PointingHandCursor)
            self.paste_ai_btn.setFixedHeight(28)
            self.paste_ai_btn.setStyleSheet(f"""
                QPushButton {{
                    background: {COLORS['surface']};
                    color: {COLORS['accent_dark']};
                    border: 1px solid #d8d0ff;
                    border-radius: 9px;
                    padding: 5px 12px;
                    font-size: 12px;
                    font-weight: 700;
                }}
                QPushButton:hover {{
                    background: {COLORS['accent_light']};
                    border-color: {COLORS['accent']};
                }}
            """)
            self.paste_ai_btn.clicked.connect(self.paste_ai_requested.emit)
            header.addWidget(self.paste_ai_btn)
        layout.addLayout(header)

        if self.role == "user":
            prompt_row = QHBoxLayout()
            prompt_row.setSpacing(8)
            self.prompt_input = QLineEdit()
            self.prompt_input.setPlaceholderText("一句需求，例如：创建一个地狱卡牌游戏")
            self.prompt_input.setText(self.prompt_input_text)
            self.prompt_input.setFixedHeight(32)
            self.prompt_input.setStyleSheet(f"""
                QLineEdit {{
                    background: {COLORS['surface']};
                    color: {COLORS['text']};
                    border: 1px solid {COLORS['border']};
                    border-radius: 10px;
                    padding: 7px 10px;
                    font-size: 12px;
                }}
                QLineEdit:focus {{
                    border: 1px solid {COLORS['accent']};
                }}
            """)
            prompt_row.addWidget(self.prompt_input)
            layout.addLayout(prompt_row)
        
        self.content_label = QPlainTextEdit()
        self.content_label.setReadOnly(True)
        self.content_label.setPlainText(self.content)
        self.content_label.setMaximumBlockCount(20000)
        self.content_label.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.content_label.customContextMenuRequested.connect(
            lambda pos, editor=self.content_label: show_chinese_edit_menu(editor, editor.mapToGlobal(pos))
        )
        self.content_label.setStyleSheet(f"""
            QPlainTextEdit {{
                background: {COLORS['code_bg']};
                color: {COLORS['text']};
                border: 1px solid {COLORS['border']};
                border-radius: 12px;
                padding: 10px 12px;
                font-family: 'SF Mono', 'Menlo', monospace;
                font-size: 12px;
                selection-background-color: #d8e6ff;
                selection-color: {COLORS['text']};
            }}
            QScrollBar:vertical {{
                background: transparent;
                width: 8px;
                margin: 4px 2px 4px 0;
            }}
            QScrollBar::handle:vertical {{
                background: {COLORS['border_strong']};
                border-radius: 4px;
                min-height: 28px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0;
            }}
        """)
        self.content_label.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.content_label.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.content_label.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.content_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.adjust_content_height()
        self.schedule_content_height_adjustment()
        layout.addWidget(self.content_label)
        
    def update_content(self, text: str):
        self.content = text
        self.content_label.setPlainText(text)
        self.adjust_content_height()
        self.schedule_content_height_adjustment()

    def copy_content(self):
        if self.role == "user" and hasattr(self, "prompt_input"):
            self.copy_requested.emit()
            return
        QApplication.clipboard().setText(self.content)
        copy_btn = getattr(self, "copy_btn", None)
        if copy_btn is None:
            return
        copy_btn.setText("已复制")
        QTimer.singleShot(1000, lambda: copy_btn.setText(self.copy_text))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not hasattr(self, "content_label"):
            return
        width = self.content_label.viewport().width()
        if abs(width - self._last_content_width) >= 18:
            self._last_content_width = width
            self.schedule_content_height_adjustment(delay=70)

    def schedule_content_height_adjustment(self, delay: int = 0):
        if self._height_adjust_scheduled:
            return
        self._height_adjust_scheduled = True
        QTimer.singleShot(delay, self.adjust_content_height)

    def adjust_content_height(self):
        if not hasattr(self, "content_label"):
            return
        self._height_adjust_scheduled = False
        text = self.content_label.toPlainText() or " "
        available_width = max(120, self.content_label.viewport().width() - 10)
        metrics = self.content_label.fontMetrics()
        line_spacing = max(1, metrics.lineSpacing())
        max_visual_lines = max(1, (self.max_content_height - 36 + line_spacing - 1) // line_spacing)
        avg_char_width = max(1, metrics.averageCharWidth())
        visual_lines = 0
        for line in text.splitlines() or [""]:
            expanded = line.replace("\t", "    ")
            if len(expanded) > 320:
                line_width = avg_char_width * len(expanded)
            else:
                line_width = metrics.horizontalAdvance(expanded)
            visual_lines += max(1, (line_width + available_width - 1) // available_width)
            if visual_lines >= max_visual_lines:
                visual_lines = max_visual_lines
                break
        natural_height = visual_lines * line_spacing + 36
        target_height = min(self.max_content_height, max(self.min_content_height, int(natural_height)))
        if self.content_label.height() != target_height:
            self.content_label.setFixedHeight(target_height)

class ChangeSummaryCard(QFrame):
    undo_requested = Signal(object)
    redo_requested = Signal(object)

    def __init__(self, records: List[Dict[str, object]], parent=None):
        super().__init__(parent)
        self.records = records
        self.detail_widgets: List[QTextEdit] = []
        self.file_row_widgets: List[QFrame] = []
        self.file_row_buttons: List[QPushButton] = []
        self.undo_btn: Optional[QPushButton] = None
        self.title_label: Optional[QLabel] = None
        self.stats_label: Optional[QLabel] = None
        self.status_label: Optional[QLabel] = None
        self.is_undone = False
        self.setup_ui()

    def setup_ui(self):
        self.setObjectName("changeSummaryCard")
        self.apply_state_style(False)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        additions = sum(int(r["additions"]) for r in self.records)
        deletions = sum(int(r["deletions"]) for r in self.records)
        header = QHBoxLayout()
        title = QLabel(f"{len(self.records)} files changed")
        self.title_label = title
        title.setStyleSheet(f"color: {COLORS['text']}; font-size: 13px; font-weight: 900; background: transparent; border: none;")
        stats = QLabel(f"+{additions}  -{deletions}")
        self.stats_label = stats
        stats.setStyleSheet(f"color: {COLORS['text_secondary']}; font-size: 12px; font-weight: 800; background: transparent; border: none;")
        self.status_label = QLabel("已撤销")
        self.status_label.setVisible(False)
        self.status_label.setStyleSheet(f"""
            QLabel {{
                background: {COLORS['surface_alt']};
                color: {COLORS['muted']};
                border: 1px solid {COLORS['border']};
                border-radius: 9px;
                padding: 4px 8px;
                font-size: 11px;
                font-weight: 900;
            }}
        """)
        header.addWidget(title)
        header.addSpacing(10)
        header.addWidget(stats)
        header.addSpacing(8)
        header.addWidget(self.status_label)
        header.addStretch()
        self.undo_btn = QPushButton("Undo")
        self.undo_btn.setCursor(Qt.PointingHandCursor)
        self.undo_btn.setFixedHeight(30)
        self.undo_btn.setStyleSheet(f"""
            QPushButton {{
                background: {COLORS['surface']};
                color: {COLORS['danger']};
                border: 1px solid #ffd0d2;
                border-radius: 10px;
                padding: 6px 12px;
                font-size: 12px;
                font-weight: 900;
            }}
            QPushButton:hover {{
                background: {COLORS['danger_soft']};
            }}
            QPushButton:disabled {{
                color: {COLORS['muted']};
                border-color: {COLORS['border']};
                background: {COLORS['surface_alt']};
            }}
        """)
        self.undo_btn.clicked.connect(lambda: self.undo_requested.emit(self))
        header.addWidget(self.undo_btn)
        layout.addLayout(header)

        for record in self.records:
            layout.addWidget(self.create_file_row(record))

    def create_file_row(self, record: Dict[str, object]) -> QWidget:
        wrapper = QFrame()
        wrapper.setObjectName("changeFileRow")
        wrapper.setStyleSheet(f"""
            QFrame#changeFileRow {{
                background: {COLORS['code_bg']};
                border: 1px solid {COLORS['border']};
                border-radius: 12px;
            }}
        """)
        self.file_row_widgets.append(wrapper)
        layout = QVBoxLayout(wrapper)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(8)

        row = QPushButton()
        row.setCursor(Qt.PointingHandCursor)
        row.setStyleSheet(f"""
            QPushButton {{
                text-align: left;
                background: transparent;
                color: {COLORS['text']};
                border: none;
                padding: 4px 2px;
                font-size: 12px;
                font-weight: 800;
            }}
            QPushButton:hover {{
                color: {COLORS['accent_dark']};
            }}
        """)
        row.setText(f"› {record['path']}    +{record['additions']} -{record['deletions']}")
        self.file_row_buttons.append(row)
        layout.addWidget(row)

        diff_view = QTextEdit()
        diff_view.setReadOnly(True)
        diff_view.setHtml(render_diff_html(record))
        diff_view.setFixedHeight(260)
        diff_view.setVisible(False)
        diff_view.setStyleSheet(f"""
            QTextEdit {{
                background: #ffffff;
                color: {COLORS['text']};
                border: 1px solid {COLORS['border']};
                border-radius: 10px;
                padding: 0;
                font-family: 'SF Mono', 'Menlo', monospace;
                font-size: 12px;
            }}
            QScrollBar:vertical {{
                background: transparent;
                width: 8px;
            }}
            QScrollBar::handle:vertical {{
                background: {COLORS['border_strong']};
                border-radius: 4px;
                min-height: 28px;
            }}
        """)
        layout.addWidget(diff_view)
        self.detail_widgets.append(diff_view)

        def toggle():
            visible = not diff_view.isVisible()
            diff_view.setVisible(visible)
            row.setText(f"{'⌄' if visible else '›'} {record['path']}    +{record['additions']} -{record['deletions']}")

        row.clicked.connect(toggle)
        return wrapper

    def apply_state_style(self, undone: bool):
        self.setStyleSheet(f"""
            QFrame#changeSummaryCard {{
                background: {COLORS['surface']};
                border: 1px solid {COLORS['border']};
                border-radius: 16px;
                margin: 4px 0;
            }}
        """)
        if self.title_label:
            self.title_label.setStyleSheet(f"color: {COLORS['text']}; font-size: 13px; font-weight: 900; background: transparent; border: none;")
        if self.stats_label:
            self.stats_label.setStyleSheet(f"color: {COLORS['text_secondary']}; font-size: 12px; font-weight: 800; background: transparent; border: none;")
        if self.status_label:
            self.status_label.setVisible(undone)
        for row in self.file_row_widgets:
            row.setStyleSheet(f"""
                QFrame#changeFileRow {{
                    background: {COLORS['code_bg']};
                    border: 1px solid {COLORS['border']};
                    border-radius: 12px;
                }}
            """)
        for button in self.file_row_buttons:
            font = button.font()
            font.setStrikeOut(undone)
            button.setFont(font)
            button.setStyleSheet(f"""
                QPushButton {{
                    text-align: left;
                    background: transparent;
                    color: {COLORS['muted'] if undone else COLORS['text']};
                    border: none;
                    padding: 4px 2px;
                    font-size: 12px;
                    font-weight: 800;
                }}
                QPushButton:hover {{
                    color: {COLORS['muted'] if undone else COLORS['accent_dark']};
                }}
            """)

    def mark_undone(self, applied: int, skipped: int):
        if self.undo_btn:
            self.is_undone = True
            self.apply_state_style(True)
            self.undo_btn.setText("Redo")
            self.undo_btn.clicked.disconnect()
            self.undo_btn.clicked.connect(lambda: self.redo_requested.emit(self))

    def mark_redone(self, applied: int, skipped: int):
        if self.undo_btn:
            self.is_undone = False
            self.apply_state_style(False)
            self.undo_btn.setText("Undo")
            self.undo_btn.clicked.disconnect()
            self.undo_btn.clicked.connect(lambda: self.undo_requested.emit(self))

# ============================================================
# 侧栏
# ============================================================
class ThreadCard(QFrame):
    selected = Signal(str)
    delete_requested = Signal(str)

    def __init__(self, thread: Dict[str, object], active: bool = False, parent=None):
        super().__init__(parent)
        self.thread = thread
        self.thread_id = str(thread.get("id", DEFAULT_THREAD_ID))
        self.active = active
        self.deletable = self.thread_id != DEFAULT_THREAD_ID
        self.setObjectName("threadCard")
        self.setCursor(Qt.PointingHandCursor)
        self.setup_ui()
        self.apply_style()

    def setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 8, 10)
        layout.setSpacing(8)
        title = QLabel(str(self.thread.get("title", "会话")))
        title.setStyleSheet("background: transparent; border: none; font-size: 12px; font-weight: 900;")
        title.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout.addWidget(title)
        self.delete_btn = QToolButton(self, cursor=Qt.PointingHandCursor)
        self.delete_btn.setText("×")
        self.delete_btn.setToolTip("")
        self.delete_btn.setFixedSize(24, 24)
        self.delete_btn.setVisible(False)
        self.delete_btn.clicked.connect(lambda: self.delete_requested.emit(self.thread_id))
        self.delete_btn.setStyleSheet(f"""
            QToolButton {{
                background: transparent;
                color: {COLORS['muted']};
                border: none;
                border-radius: 8px;
                font-size: 17px;
                font-weight: 900;
                padding-bottom: 1px;
            }}
            QToolButton:hover {{
                background: {COLORS['surface_alt']};
                color: {COLORS['text']};
            }}
        """)
        layout.addWidget(self.delete_btn)
        self.title_label = title

    def apply_style(self):
        bg = COLORS["accent_light"] if self.active else COLORS["surface"]
        border = "#d8d0ff" if self.active else COLORS["border"]
        color = COLORS["accent_dark"] if self.active else COLORS["text"]
        self.setStyleSheet(f"""
            QFrame#threadCard {{
                background: {bg};
                border: 1px solid {border};
                border-radius: 12px;
            }}
            QFrame#threadCard:hover {{
                background: {COLORS['surface_alt'] if not self.active else '#e7ddff'};
                border-color: #d8d0ff;
            }}
        """)
        self.title_label.setStyleSheet(f"background: transparent; border: none; color: {color}; font-size: 12px; font-weight: 900;")
        self.delete_btn.setVisible(self.deletable and self.active)

    def set_active(self, active: bool):
        self.active = active
        self.apply_style()

    def enterEvent(self, event):
        super().enterEvent(event)
        if self.deletable:
            self.delete_btn.setVisible(True)

    def leaveEvent(self, event):
        super().leaveEvent(event)
        if self.deletable and not self.active:
            self.delete_btn.setVisible(False)

    def mousePressEvent(self, event: QMouseEvent):
        if self.delete_btn.geometry().contains(event.position().toPoint()):
            super().mousePressEvent(event)
            return
        self.selected.emit(self.thread_id)
        super().mousePressEvent(event)


class Sidebar(QFrame):
    file_opened = Signal(str)
    back_home_requested = Signal()
    thread_selected = Signal(str)
    new_thread_requested = Signal()
    delete_thread_requested = Signal(str)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(0)
        self._expanded_width = 260
        self._min_width = 220
        self._max_width = 560
        self._collapsed = True
        self._root_path = None
        self._active_tab = "threads"
        self._active_thread_id = DEFAULT_THREAD_ID
        self.thread_cards: Dict[str, ThreadCard] = {}
        
        self.setStyleSheet(f"""
            QFrame {{
                background: {COLORS['sidebar_bg']};
                border: none;
            }}
        """)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)

        nav_row = QHBoxLayout()
        nav_row.setSpacing(6)
        self.threads_tab_btn = self.create_nav_button("会话列表")
        self.files_tab_btn = self.create_nav_button("项目文件")
        self.home_tab_btn = self.create_nav_button("返回首页")
        self.threads_tab_btn.clicked.connect(lambda: self.set_tab("threads"))
        self.files_tab_btn.clicked.connect(lambda: self.set_tab("files"))
        self.home_tab_btn.clicked.connect(self.back_home_requested.emit)
        nav_row.addWidget(self.threads_tab_btn)
        nav_row.addWidget(self.files_tab_btn)
        nav_row.addWidget(self.home_tab_btn)
        layout.addLayout(nav_row)

        self.root_label = QLabel("未选择目录")
        self.root_label.setWordWrap(True)
        self.root_label.setStyleSheet(f"""
            QLabel {{
                background: {COLORS['surface']};
                color: {COLORS['text_secondary']};
                border: 1px solid {COLORS['border']};
                border-radius: 12px;
                padding: 10px 12px;
                font-size: 12px;
            }}
        """)
        layout.addWidget(self.root_label)

        self.stack = QStackedWidget()
        self.stack.setStyleSheet("QStackedWidget { background: transparent; border: none; }")
        self.files_page = QWidget()
        files_layout = QVBoxLayout(self.files_page)
        files_layout.setContentsMargins(0, 0, 0, 0)
        files_layout.setSpacing(0)
        self.tree = QTreeWidget()
        self.setup_tree()
        files_layout.addWidget(self.tree)
        self.stack.addWidget(self.files_page)

        self.threads_page = QWidget()
        threads_layout = QVBoxLayout(self.threads_page)
        threads_layout.setContentsMargins(0, 0, 0, 0)
        threads_layout.setSpacing(8)
        add_row = QHBoxLayout()
        label = QLabel("会话")
        label.setStyleSheet(f"color: {COLORS['text']}; font-size: 13px; font-weight: 900; background: transparent; border: none;")
        add_row.addWidget(label)
        add_row.addStretch()
        self.add_thread_btn = QToolButton(cursor=Qt.PointingHandCursor)
        self.add_thread_btn.setText("+")
        self.add_thread_btn.setToolTip("新建会话")
        self.add_thread_btn.setFixedSize(28, 28)
        self.add_thread_btn.clicked.connect(self.new_thread_requested.emit)
        self.add_thread_btn.setStyleSheet(f"""
            QToolButton {{
                background: {COLORS['accent']};
                color: white;
                border: none;
                border-radius: 10px;
                font-size: 18px;
                font-weight: 900;
            }}
            QToolButton:hover {{
                background: {COLORS['accent_dark']};
            }}
        """)
        add_row.addWidget(self.add_thread_btn)
        threads_layout.addLayout(add_row)
        self.thread_list = QWidget()
        self.thread_list_layout = QVBoxLayout(self.thread_list)
        self.thread_list_layout.setContentsMargins(0, 0, 0, 0)
        self.thread_list_layout.setSpacing(8)
        self.thread_list_layout.addStretch()
        threads_layout.addWidget(self.thread_list, 1)
        self.stack.addWidget(self.threads_page)
        layout.addWidget(self.stack, 1)

        self.bottom_btn = QPushButton("刷新文件树")
        self.bottom_btn.setCursor(Qt.PointingHandCursor)
        self.bottom_btn.setStyleSheet(f"""
            QPushButton {{
                background: {COLORS['surface']};
                color: {COLORS['text']};
                border: 1px solid {COLORS['border']};
                border-radius: 12px;
                padding: 10px 12px;
                font-size: 12px;
                font-weight: 700;
            }}
            QPushButton:hover {{
                background: {COLORS['accent_light']};
                color: {COLORS['accent_dark']};
                border-color: #d6ccff;
            }}
        """)
        self.bottom_btn.clicked.connect(lambda: self.refresh_tree(self._root_path))
        layout.addWidget(self.bottom_btn)
        self.set_tab("threads")
        self.setVisible(False)

    def create_nav_button(self, text: str) -> QPushButton:
        btn = QPushButton(text, cursor=Qt.PointingHandCursor)
        btn.setFixedHeight(30)
        btn.setStyleSheet("QPushButton { border: none; background: transparent; }")
        return btn

    def nav_button_style(self, active: bool) -> str:
        return f"""
            QPushButton {{
                background: {'#ebe6ff' if active else 'transparent'};
                color: {COLORS['accent_dark'] if active else COLORS['text']};
                border: 1px solid {'#d8d0ff' if active else 'transparent'};
                border-radius: 9px;
                padding: 4px 6px;
                font-size: 12px;
                font-weight: 900;
            }}
            QPushButton:hover {{
                background: {COLORS['accent_light']};
                color: {COLORS['accent_dark']};
            }}
        """

    def set_tab(self, tab: str):
        self._active_tab = tab
        self.stack.setCurrentWidget(self.threads_page if tab == "threads" else self.files_page)
        self.files_tab_btn.setStyleSheet(self.nav_button_style(tab == "files"))
        self.threads_tab_btn.setStyleSheet(self.nav_button_style(tab == "threads"))
        self.home_tab_btn.setStyleSheet(self.nav_button_style(False))
        self.bottom_btn.setText("新建会话" if tab == "threads" else "刷新文件树")
        try:
            self.bottom_btn.clicked.disconnect()
        except (TypeError, RuntimeError):
            pass
        if tab == "threads":
            self.bottom_btn.clicked.connect(self.new_thread_requested.emit)
        else:
            self.bottom_btn.clicked.connect(lambda: self.refresh_tree(self._root_path))

    def setup_tree(self):
        self.tree.setHeaderHidden(True)
        self.tree.setRootIsDecorated(False)
        self.tree.setIndentation(22)
        self.tree.setAnimated(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tree.setAllColumnsShowFocus(False)
        self.tree.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.tree.setStyleSheet(f"""
            QTreeWidget {{
                background: {COLORS['surface']};
                color: {COLORS['text']};
                border: 1px solid {COLORS['border']};
                border-radius: 14px;
                padding: 8px;
                font-size: 12px;
                outline: none;
                show-decoration-selected: 0;
            }}
            QTreeWidget::item {{
                min-height: 28px;
                padding: 3px 8px;
                border-radius: 8px;
            }}
            QTreeWidget::item:hover {{
                background: {COLORS['surface_alt']};
            }}
            QTreeWidget::item:selected,
            QTreeWidget::item:selected:active,
            QTreeWidget::item:selected:!active {{
                background: #ebe6ff;
                color: {COLORS['accent_dark']};
                border: 1px solid #d8d0ff;
            }}
            QTreeWidget::branch,
            QTreeView::branch {{
                background: transparent;
                width: 18px;
                min-width: 18px;
                image: none;
            }}
            QTreeWidget::branch:selected,
            QTreeWidget::branch:selected:active,
            QTreeWidget::branch:selected:!active,
            QTreeWidget::branch:has-siblings:selected,
            QTreeWidget::branch:adjoins-item:selected,
            QTreeWidget::branch:has-children:selected,
            QTreeWidget::branch:open:selected,
            QTreeWidget::branch:closed:selected,
            QTreeView::branch:selected,
            QTreeView::branch:selected:active,
            QTreeView::branch:selected:!active,
            QTreeView::branch:has-siblings:selected,
            QTreeView::branch:adjoins-item:selected,
            QTreeView::branch:has-children:selected,
            QTreeView::branch:open:selected,
            QTreeView::branch:closed:selected {{
                background: transparent;
                image: none;
            }}
            QScrollBar:vertical {{
                background: transparent;
                width: 8px;
                margin: 4px 1px 4px 0;
            }}
            QScrollBar::handle:vertical {{
                background: {COLORS['border_strong']};
                border-radius: 4px;
                min-height: 30px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0;
            }}
        """)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.show_context_menu)
        self.tree.itemClicked.connect(self.on_item_click)
        self.tree.itemDoubleClicked.connect(self.on_double_click)
        self.tree.itemExpanded.connect(self.update_folder_indicator)
        self.tree.itemCollapsed.connect(self.update_folder_indicator)
    
    def refresh_tree(self, root_path: str = None):
        self.tree.clear()
        if root_path is None:
            return
        self._root_path = root_path
        self.root_label.setText(root_path)
        style = self.tree.style()
        dir_icon = style.standardIcon(QStyle.StandardPixmap.SP_DirIcon)
        file_icon = style.standardIcon(QStyle.StandardPixmap.SP_FileIcon)
        
        root_item = QTreeWidgetItem(self.tree, [os.path.basename(root_path) or root_path])
        root_item.setData(0, Qt.UserRole, root_path)
        root_item.setData(0, Qt.UserRole + 1, os.path.basename(root_path) or root_path)
        root_item.setIcon(0, dir_icon)
        self._populate(root_item, root_path, dir_icon, file_icon)
        root_item.setExpanded(True)
        self.refresh_folder_indicators(root_item)

    def set_threads(self, threads: List[Dict[str, object]], active_thread_id: str):
        self._active_thread_id = safe_thread_id(active_thread_id)
        while self.thread_list_layout.count() > 1:
            item = self.thread_list_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        self.thread_cards = {}
        for thread in normalize_threads(threads):
            card = ThreadCard(thread, active=str(thread.get("id")) == self._active_thread_id, parent=self.thread_list)
            card.selected.connect(self.thread_selected.emit)
            card.delete_requested.connect(self.delete_thread_requested.emit)
            self.thread_cards[card.thread_id] = card
            self.thread_list_layout.insertWidget(self.thread_list_layout.count() - 1, card)

    def set_active_thread(self, thread_id: str):
        self._active_thread_id = safe_thread_id(thread_id)
        for card in self.thread_cards.values():
            card.set_active(card.thread_id == self._active_thread_id)
    
    def _populate(self, parent_item, path, dir_icon, file_icon, depth=0):
        if depth > 3:
            return
        try:
            items = sorted(os.listdir(path))
        except OSError:
            return
        for d in sorted([x for x in items if os.path.isdir(os.path.join(path, x)) and not x.startswith('.') and x != '__pycache__']):
            full = os.path.join(path, d)
            item = QTreeWidgetItem(parent_item, [d])
            item.setData(0, Qt.UserRole, full)
            item.setData(0, Qt.UserRole + 1, d)
            item.setIcon(0, dir_icon)
            self._populate(item, full, dir_icon, file_icon, depth + 1)
        for f in sorted([x for x in items if os.path.isfile(os.path.join(path, x))]):
            full = os.path.join(path, f)
            item = QTreeWidgetItem(parent_item, [f])
            item.setData(0, Qt.UserRole, full)
            item.setData(0, Qt.UserRole + 1, f)
            item.setIcon(0, file_icon)

    def update_folder_indicator(self, item: QTreeWidgetItem):
        path = item.data(0, Qt.UserRole)
        name = item.data(0, Qt.UserRole + 1) or item.text(0).lstrip("▾▸ ").strip()
        if os.path.isdir(path):
            marker = "▾" if item.isExpanded() else "▸"
            item.setText(0, f"{marker} {name}")

    def refresh_folder_indicators(self, item: QTreeWidgetItem):
        self.update_folder_indicator(item)
        for i in range(item.childCount()):
            self.refresh_folder_indicators(item.child(i))
    
    def show_context_menu(self, pos):
        item = self.tree.itemAt(pos)
        if not item:
            return
        path = item.data(0, Qt.UserRole)
        menu = QMenu(self)
        menu.addAction("打开文件", lambda: self.file_opened.emit(path))
        target = path if os.path.isdir(path) else os.path.dirname(path)
        menu.addAction("打开目录", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(target)))
        if os.path.isfile(path):
            menu.addAction("复制路径", lambda: QApplication.clipboard().setText(path))
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def on_item_click(self, item, col):
        path = item.data(0, Qt.UserRole)
        if os.path.isdir(path):
            item.setExpanded(not item.isExpanded())
    
    def on_double_click(self, item, col):
        path = item.data(0, Qt.UserRole)
        if os.path.isfile(path):
            self.file_opened.emit(path)
    
    def toggle(self):
        self._collapsed = not self._collapsed
        if self._collapsed:
            self.setVisible(False)
            self.setFixedWidth(0)
        else:
            self.setVisible(True)
            self.setFixedWidth(self._expanded_width)

    def set_expanded_width(self, width: int):
        self._expanded_width = max(self._min_width, min(width, self._max_width))
        if not self._collapsed:
            self.setFixedWidth(self._expanded_width)

    def current_width(self) -> int:
        return 0 if self._collapsed else self._expanded_width

    def handle_width(self) -> int:
        return self._expanded_width

# ============================================================
# 首页
# ============================================================
class HomePage(QWidget):
    enter_chat = Signal(str)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setup_ui()
    
    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)
        layout.setContentsMargins(34, 30, 34, 30)
        layout.setSpacing(18)

        shell = QFrame()
        shell.setMaximumWidth(780)
        shell.setStyleSheet(f"""
            QFrame {{
                background: {COLORS['surface']};
                border: 1px solid {COLORS['border']};
                border-radius: 28px;
            }}
        """)
        shell_layout = QVBoxLayout(shell)
        shell_layout.setContentsMargins(28, 26, 28, 28)
        shell_layout.setSpacing(18)
        layout.addWidget(shell, alignment=Qt.AlignCenter)

        brand_badge = QLabel("LOCAL AGENT WORKSPACE", alignment=Qt.AlignCenter)
        brand_badge.setStyleSheet(f"""
            QLabel {{
                color: {COLORS['accent_dark']};
                background: {COLORS['accent_light']};
                border: 1px solid #dbd2ff;
                border-radius: 14px;
                padding: 7px 13px;
                font-size: 11px;
                font-weight: 800;
                letter-spacing: 1px;
            }}
        """)
        shell_layout.addWidget(brand_badge, alignment=Qt.AlignCenter)

        title = QLabel("Agent 控制台", alignment=Qt.AlignCenter)
        title.setStyleSheet(f"font-size: 34px; font-weight: 900; color: {COLORS['text']}; background: transparent; border: none;")
        shell_layout.addWidget(title)

        subtitle = QLabel("把 AI 输出粘贴回来，自动解析占位符、写入文件并管理本地进程。")
        subtitle.setAlignment(Qt.AlignCenter)
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet(f"font-size: 14px; color: {COLORS['text_secondary']}; background: transparent; border: none;")
        shell_layout.addWidget(subtitle)
        
        # 功能卡片
        features_frame = QFrame()
        features_frame.setStyleSheet(f"QFrame {{ background: transparent; border: none; }}")
        features_layout = QGridLayout(features_frame)
        features_layout.setContentsMargins(0, 4, 0, 0)
        features_layout.setHorizontalSpacing(12)
        features_layout.setVerticalSpacing(12)
        
        for idx, (emoji, name, desc) in enumerate([
            ("PROMPT", "复制提示词", "自动生成带项目路径和执行协议的完整提示词。"),
            ("PASTE", "粘贴 AI 回复", "识别 Bash、HTML、CSS、JS 等代码块并缓存。"),
            ("RUN", "自动执行", "替换占位符后按顺序执行，heredoc 会保持完整。"),
            ("TERM", "后台进程", "本地服务器会进入底部终端，随时查看和停止。"),
            ("FILES", "文件管理", "侧栏浏览项目文件，支持刷新、打开和复制路径。"),
            ("FLOW", "连续工作", "第二天继续也能从目录结构快速恢复上下文。"),
        ]):
            card = QFrame()
            card.setMinimumHeight(92)
            card.setStyleSheet(f"""
                QFrame {{
                    background: {COLORS['surface_alt']};
                    border: 1px solid {COLORS['border']};
                    border-radius: 18px;
                }}
            """)
            row = QHBoxLayout(card)
            row.setContentsMargins(14, 12, 14, 12)
            row.setSpacing(12)
            badge = QLabel(emoji, alignment=Qt.AlignCenter)
            badge.setFixedSize(54, 30)
            badge.setStyleSheet(f"""
                QLabel {{
                    background: {COLORS['surface']};
                    color: {COLORS['accent_dark']};
                    border: 1px solid #dcd4ff;
                    border-radius: 10px;
                    font-size: 10px;
                    font-weight: 900;
                }}
            """)
            row.addWidget(badge, alignment=Qt.AlignTop)
            text_col = QVBoxLayout()
            text_col.setSpacing(4)
            name_label = QLabel(name)
            name_label.setStyleSheet(f"font-weight: 800; font-size: 13px; color: {COLORS['text']}; background: transparent; border: none;")
            desc_label = QLabel(desc)
            desc_label.setWordWrap(True)
            desc_label.setStyleSheet(f"font-size: 12px; line-height: 150%; color: {COLORS['text_secondary']}; background: transparent; border: none;")
            text_col.addWidget(name_label)
            text_col.addWidget(desc_label)
            row.addLayout(text_col)
            row.addStretch()
            features_layout.addWidget(card, idx // 2, idx % 2)
        
        shell_layout.addWidget(features_frame)
        
        # 目录选择
        dir_frame = QFrame(styleSheet=f"background: {COLORS['accent_light']}; border-radius: 18px; border: 1px solid #d8d0ff;")
        dir_layout = QVBoxLayout(dir_frame)
        dir_layout.setContentsMargins(18, 16, 18, 16)
        dir_layout.setSpacing(12)
        
        dir_layout.addWidget(QLabel("选择工作目录", styleSheet=f"font-weight: 800; font-size: 15px; color: {COLORS['text']}; background: transparent; border: none;"))
        
        path_row = QHBoxLayout()
        self.path_edit = QLineEdit(os.path.expanduser("~/Desktop/my-project"))
        self.path_edit.setPlaceholderText("项目目录路径...")
        self.path_edit.setMinimumHeight(42)
        self.path_edit.setStyleSheet(f"""
            QLineEdit {{
                background: {COLORS['surface']};
                color: {COLORS['text']};
                border: 1px solid #d7cffc;
                border-radius: 12px;
                padding: 10px 14px;
                font-size: 13px;
            }}
            QLineEdit:focus {{
                border: 1px solid {COLORS['accent']};
            }}
        """)
        path_row.addWidget(self.path_edit)
        
        for text, func in [("📂 浏览", self.browse_folder), ("➕ 新建", self.create_folder)]:
            btn = QPushButton(text)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setMinimumHeight(42)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: {COLORS['surface']};
                    color: {COLORS['text']};
                    border: 1px solid #d7cffc;
                    border-radius: 12px;
                    padding: 10px 14px;
                    font-size: 12px;
                    font-weight: 700;
                }}
                QPushButton:hover {{
                    background: #f7f4ff;
                    color: {COLORS['accent_dark']};
                }}
            """)
            btn.clicked.connect(func)
            path_row.addWidget(btn)
        dir_layout.addLayout(path_row)
        shell_layout.addWidget(dir_frame)
        
        enter_btn = QPushButton("进入工作区")
        enter_btn.setCursor(Qt.PointingHandCursor)
        enter_btn.setMinimumHeight(48)
        enter_btn.setStyleSheet(f"""
            QPushButton {{
                background: {COLORS['accent']};
                color: white;
                border: none;
                border-radius: 14px;
                padding: 14px;
                font-size: 15px;
                font-weight: 900;
            }}
            QPushButton:hover {{
                background: {COLORS['accent_dark']};
            }}
        """)
        enter_btn.clicked.connect(self.on_enter)
        shell_layout.addWidget(enter_btn)

        layout.addStretch()
    
    def browse_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "选择项目目录", self.path_edit.text())
        if folder:
            self.path_edit.setText(folder)
    
    def create_folder(self):
        base = os.path.expanduser("~/Desktop")
        name = f"project-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        folder = QFileDialog.getSaveFileName(self, "新建项目目录", os.path.join(base, name))[0]
        if folder:
            os.makedirs(folder, exist_ok=True)
            self.path_edit.setText(folder)
    
    def on_enter(self):
        path = self.path_edit.text().strip()
        if not path:
            styled_warning(self, "提示", "请选择或输入项目目录")
            return
        path = os.path.expanduser(path)
        os.makedirs(path, exist_ok=True)
        self.enter_chat.emit(path)

# ============================================================
# 对话页面
# ============================================================
class ChatPage(QWidget):
    back_home = Signal()
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.project_root = ""
        self.thread_id = DEFAULT_THREAD_ID
        self.threads: List[Dict[str, object]] = [default_thread()]
        self.cmd_outputs = []
        self.pending_snapshot: Dict[str, bytes] = {}
        self.history_entries: List[Dict[str, object]] = []
        self.result_bubble: Optional[ChatBubble] = None
        self.worker: Optional[ExecuteWorker] = None
        self._ensure_ai_entry_pending = False
        self.setup_ui()
    
    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.setStyleSheet(f"background: {COLORS['bg']};")
        
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        
        self.sidebar = Sidebar()
        self.sidebar.file_opened.connect(lambda p: QDesktopServices.openUrl(QUrl.fromLocalFile(p)))
        self.sidebar.back_home_requested.connect(self.confirm_back_home)
        self.sidebar.thread_selected.connect(self.switch_thread)
        self.sidebar.new_thread_requested.connect(self.create_thread)
        self.sidebar.delete_thread_requested.connect(self.delete_thread)
        self.sidebar_btn = QToolButton()
        self.sidebar_btn.setText("›")
        self.sidebar_btn.setFixedSize(22, 34)
        self.sidebar_btn.clicked.connect(self.toggle_sidebar)
        self.sidebar_btn.setStyleSheet(f"""
            QToolButton {{
                background: transparent;
                border: none;
                color: {COLORS['accent_dark']};
                font-size: 20px;
                font-weight: 800;
            }}
            QToolButton:hover {{
                background: {COLORS['accent_light']};
                border-radius: 8px;
            }}
        """)
        
        sidebar_wrapper = QWidget()
        self.sidebar_wrapper = sidebar_wrapper
        sidebar_wrapper.setFixedWidth(24)
        sw_layout = QHBoxLayout(sidebar_wrapper)
        sw_layout.setContentsMargins(0, 0, 0, 0)
        sw_layout.setSpacing(0)
        self.sidebar_resize_handle = SidebarResizeHandle(self.sidebar)
        self.sidebar_resize_handle.resize_requested.connect(self.resize_sidebar)
        self.sidebar_resize_handle.install_toggle_button(self.sidebar_btn)
        sw_layout.addWidget(self.sidebar)
        sw_layout.addWidget(self.sidebar_resize_handle)
        body.addWidget(sidebar_wrapper, 0)
        
        right_panel = QWidget(styleSheet=f"background: {COLORS['bg']};")
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(18, 16, 18, 0)
        right_layout.setSpacing(12)
        
        # 路径标签（双击返回首页）
        path_bar = QHBoxLayout()
        path_title = QLabel("工作区")
        path_title.setStyleSheet(f"color: {COLORS['text']}; font-size: 18px; font-weight: 900; background: transparent;")
        path_bar.addWidget(path_title)
        self.path_label = QLabel("", cursor=Qt.PointingHandCursor,
                                 styleSheet=f"""
                                     QLabel {{
                                         color: {COLORS['text_secondary']};
                                         font-size: 12px;
                                         padding: 8px 12px;
                                         background: {COLORS['surface']};
                                         border: 1px solid {COLORS['border']};
                                         border-radius: 12px;
                                     }}
                                 """)
        self.path_label.setMaximumWidth(420)
        self.path_label.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.path_label.setToolTip("")
        self.path_label.mouseDoubleClickEvent = lambda e: self.confirm_back_home()
        path_bar.addSpacing(10)
        path_bar.addWidget(self.path_label)
        path_bar.addStretch()

        self.copy_prompt_btn = QPushButton("复制系统提示词", clicked=self.append_prompt_bubble_from_toolbar, cursor=Qt.PointingHandCursor)
        self.copy_prompt_btn.setFixedHeight(36)
        self.copy_prompt_btn.setFixedWidth(136)
        self.copy_prompt_btn.setStyleSheet(f"""
            QPushButton {{
                background: {COLORS['accent']};
                color: white;
                border: none;
                border-radius: 10px;
                padding: 6px 12px;
                font-size: 12px;
                font-weight: 900;
            }}
            QPushButton:hover {{
                background: {COLORS['accent_dark']};
            }}
        """)
        path_bar.addWidget(self.copy_prompt_btn)

        self.settings_btn = QToolButton(cursor=Qt.PointingHandCursor)
        self.settings_btn.setText("")
        self.settings_btn.setIcon(line_icon("settings", COLORS["text"], 18))
        self.settings_btn.setFixedSize(36, 36)
        self.settings_btn.setIconSize(self.settings_btn.size())
        self.settings_btn.setToolTip("")
        self.settings_btn.clicked.connect(self.show_settings_menu)
        self.settings_btn.setStyleSheet(f"""
            QToolButton {{
                background: {COLORS['surface']};
                color: {COLORS['text']};
                border: 1px solid {COLORS['border']};
                border-radius: 10px;
                font-size: 15px;
                font-weight: 900;
            }}
            QToolButton:hover {{
                background: {COLORS['accent_light']};
                color: {COLORS['accent_dark']};
                border-color: #d8d0ff;
            }}
        """)
        path_bar.addWidget(self.settings_btn)

        self.top_clear_history_btn = QPushButton("清空记录", clicked=self.clear_chat_history, cursor=Qt.PointingHandCursor)
        self.top_clear_history_btn.setFixedHeight(36)
        self.top_clear_history_btn.setFixedWidth(68)
        self.top_clear_history_btn.setStyleSheet(f"""
            QPushButton {{
                background: {COLORS['surface']};
                color: {COLORS['danger']};
                border: 1px solid #ffd0d2;
                border-radius: 10px;
                padding: 6px 12px;
                font-size: 12px;
                font-weight: 800;
            }}
            QPushButton:hover {{
                background: {COLORS['danger_soft']};
            }}
        """)
        path_bar.addWidget(self.top_clear_history_btn)
        right_layout.addLayout(path_bar)
        
        self.scroll_area = QScrollArea(widgetResizable=True,
                                       styleSheet=f"""
                                           QScrollArea {{
                                               background: {COLORS['surface']};
                                               border: 1px solid {COLORS['border']};
                                               border-radius: 18px;
                                           }}
                                           QScrollArea > QWidget > QWidget {{
                                               background: {COLORS['surface']};
                                           }}
                                           QScrollBar:vertical {{
                                               background: transparent;
                                               width: 8px;
                                               margin: 6px 2px 6px 0;
                                           }}
                                           QScrollBar::handle:vertical {{
                                               background: {COLORS['border_strong']};
                                               border-radius: 4px;
                                               min-height: 30px;
                                           }}
                                           QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                                               height: 0;
                                           }}
                                       """)
        self.chat_container = QWidget()
        self.chat_layout = QVBoxLayout(self.chat_container)
        self.chat_layout.setAlignment(Qt.AlignTop)
        self.chat_layout.setSpacing(10)
        self.chat_layout.setContentsMargins(14, 14, 14, 14)
        self.scroll_area.setWidget(self.chat_container)
        self.scroll_area.verticalScrollBar().valueChanged.connect(self.on_chat_scroll_changed)
        right_layout.addWidget(self.scroll_area, 1)

        self.empty_state = QLabel("新会话会先生成提示词气泡；点击气泡右上角的跳过继续。", alignment=Qt.AlignCenter)
        self.empty_state.setStyleSheet(f"""
            QLabel {{
                color: {COLORS['muted']};
                background: transparent;
                font-size: 14px;
                font-weight: 700;
                padding: 36px;
            }}
        """)
        self.chat_layout.addWidget(self.empty_state)
        
        body.addWidget(right_panel, 1)
        layout.addLayout(body, 1)
        
        self.terminal_panel = TerminalPanel()
        self.terminal_panel.collapsed_signal.connect(self.update_status_bar)
        self.terminal_resize_handle = TerminalResizeHandle(self.terminal_panel)
        self.terminal_resize_handle.resize_requested.connect(self.resize_terminal_panel)
        layout.addWidget(self.terminal_resize_handle)
        layout.addWidget(self.terminal_panel)
        
        self.status_bar = QPushButton("", clicked=self.terminal_panel.toggle, cursor=Qt.PointingHandCursor,
                                      styleSheet=f"""
                                          QPushButton {{
                                              background: {COLORS['terminal_panel']};
                                              color: {COLORS['terminal_text']};
                                              border: none;
                                              border-top: 1px solid {COLORS['border']};
                                              padding: 7px 16px;
                                              font-size: 12px;
                                              font-weight: 800;
                                              text-align: left;
                                          }}
                                          QPushButton:hover {{
                                              background: {COLORS['surface_alt']};
                                          }}
                                      """)
        layout.addWidget(self.status_bar)
        self.update_status_bar()
    
    def set_project(self, path: str):
        self.terminal_panel.close_all_processes()
        self.terminal_panel.collapse()
        self.project_root = path
        self.thread_id = DEFAULT_THREAD_ID
        self.threads = load_workspace_threads(path)
        save_workspace_threads(path, self.threads)
        self.path_label.setText(f"📁 {path}")
        self.terminal_panel.set_project_root(path)
        self.sidebar.refresh_tree(path)
        self.sidebar.set_threads(self.threads, self.thread_id)
        self.load_history()
        self.update_prompt_tools_responsive()
        self.update_status_bar()

    def confirm_back_home(self):
        ok = styled_confirm(
            self,
            "返回首页",
            "确定返回首页吗？返回首页后可以切换工作区，当前会话记录会保存在工作区缓存中。",
            confirm_text="返回首页",
        )
        if ok:
            self.back_home.emit()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update_prompt_tools_responsive()

    def update_prompt_tools_responsive(self):
        if not hasattr(self, "copy_prompt_btn"):
            return
        width = self.width()
        narrow = width < 1180
        very_narrow = width < 980
        self.path_label.setMaximumWidth(180 if very_narrow else (260 if narrow else 420))
        self.copy_prompt_btn.setText("复制提示词" if very_narrow else "复制系统提示词")
        self.top_clear_history_btn.setText("清空" if narrow else "清空记录")
        self.copy_prompt_btn.setFixedWidth(104 if very_narrow else (118 if narrow else 136))
        self.top_clear_history_btn.setFixedWidth(58 if very_narrow else (68 if narrow else 86))
    
    def toggle_sidebar(self):
        self.sidebar.toggle()
        self.sidebar_btn.setText("‹" if not self.sidebar._collapsed else "›")
        self.sidebar_resize_handle.set_grip_visible(not self.sidebar._collapsed)
        self.update_sidebar_wrapper_width()

    def resize_sidebar(self, width: int):
        self.sidebar.set_expanded_width(width)
        self.update_sidebar_wrapper_width()

    def update_sidebar_wrapper_width(self):
        handle_width = self.sidebar_resize_handle.width()
        self.sidebar_wrapper.setFixedWidth(self.sidebar.current_width() + handle_width)
    
    def scroll_to_bottom(self):
        self.scroll_to_bottom_now()
        for delay in (30, 90, 180):
            QTimer.singleShot(delay, self.scroll_to_bottom_now)

    def scroll_to_bottom_now(self):
        bar = self.scroll_area.verticalScrollBar()
        bar.setValue(bar.maximum())

    def is_chat_at_bottom(self) -> bool:
        bar = self.scroll_area.verticalScrollBar()
        return bar.value() >= bar.maximum() - 8

    def is_execution_running(self) -> bool:
        return bool(self.worker and self.worker.isRunning())

    def on_chat_scroll_changed(self, _value: int):
        return

    def schedule_ensure_ai_response_entry(self):
        if self._ensure_ai_entry_pending or self.is_execution_running():
            return
        self._ensure_ai_entry_pending = True

        def run():
            self._ensure_ai_entry_pending = False
            if self.is_chat_at_bottom():
                self.ensure_ai_response_entry()

        QTimer.singleShot(0, run)

    def ensure_ai_response_entry(self, focus: bool = False, animate: bool = True, keep_visible: bool = True):
        if self.is_execution_running():
            return
        existing_frame = self.find_open_ai_response_frame()
        if existing_frame is None:
            self.add_ai_response_frame(focus=focus, animate=animate, keep_visible=keep_visible)
            return
        if focus:
            ai_input = getattr(existing_frame, "ai_input", None)
            if ai_input is not None:
                QTimer.singleShot(60, ai_input.setFocus)

    def keep_ai_response_visible(self):
        if self.find_open_ai_response_frame() is None:
            return
        for delay in (0, 80, 180):
            QTimer.singleShot(delay, self.scroll_to_bottom_now)

    def toggle_prompt_tools(self):
        return

    def show_settings_menu(self):
        menu = QMenu(self)
        for title in (
            "字号大小（待实现）",
            "主题颜色（待实现）",
            "语言设置（待实现）",
            "自动化插件（待实现）",
        ):
            action = QAction(title, self)
            action.setEnabled(False)
            menu.addAction(action)
        menu.addSeparator()
        note = QAction("自动化将作为可选扩展下载", self)
        note.setEnabled(False)
        menu.addAction(note)
        menu.exec(self.settings_btn.mapToGlobal(self.settings_btn.rect().bottomRight()))

    def build_system_prompt(self, user_text: str) -> str:
        raw_prompt = user_text.strip()
        prompt = raw_prompt or "请根据当前工作区创建或修改项目，并输出可直接执行的完整指令。"
        return SYSTEM_PROMPT.format(
            project_root=self.project_root,
            user_prompt=prompt,
            **runtime_environment(),
        ) + f"\n{PROMPT_BUBBLE_MARKER}{base64.b64encode(raw_prompt.encode('utf-8')).decode('ascii')} -->"

    def prompt_text_from_system_prompt(self, full_prompt: str) -> str:
        match = re.search(r"<!-- agent_qt_user_prompt:([A-Za-z0-9+/=]*) -->\s*$", full_prompt)
        if not match:
            return ""
        try:
            return base64.b64decode(match.group(1).encode("ascii")).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return ""

    def display_prompt_text(self, full_prompt: str) -> str:
        return re.sub(r"\n?<!-- agent_qt_user_prompt:[A-Za-z0-9+/=]* -->\s*$", "", full_prompt)

    def append_prompt_bubble_from_toolbar(self):
        full_prompt = self.build_system_prompt("")
        self.copy_prompt_btn.setText("已添加")
        QTimer.singleShot(1200, lambda: self.copy_prompt_btn.setText("复制系统提示词"))
        self.add_prompt_bubble(full_prompt, save=True, animate=True)
        self.scroll_to_bottom()

    def copy_prompt_bubble(self, bubble: ChatBubble):
        prompt_input = getattr(bubble, "prompt_input", None)
        user_text = prompt_input.text() if prompt_input is not None else self.prompt_text_from_system_prompt(bubble.content)
        full_prompt = self.build_system_prompt(user_text)
        bubble.update_content(self.display_prompt_text(full_prompt))
        bubble.content = full_prompt
        QApplication.clipboard().setText(self.display_prompt_text(full_prompt))
        copy_btn = getattr(bubble, "copy_btn", None)
        if copy_btn is not None:
            copy_btn.setText("已复制")
            QTimer.singleShot(1000, lambda: copy_btn.setText(bubble.copy_text))
        self.update_prompt_history_entry(getattr(bubble, "history_entry_id", ""), full_prompt)

    def update_prompt_history_entry(self, entry_id: str, full_prompt: str):
        if not entry_id:
            return
        for entry in self.history_entries:
            if entry.get("id") == entry_id and entry.get("type") == "prompt":
                entry["content"] = full_prompt
                self.save_history()
                return

    def copy_system_prompt(self):
        full_prompt = self.build_system_prompt("")
        QApplication.clipboard().setText(self.display_prompt_text(full_prompt))
        self.copy_prompt_btn.setText("已复制")
        QTimer.singleShot(1200, lambda: self.copy_prompt_btn.setText("复制系统提示词"))
        self.add_prompt_bubble(full_prompt, save=True, animate=True)
        self.scroll_to_bottom()

    def add_prompt_bubble(self, full_prompt: str, save: bool = False, animate: bool = False):
        self.hide_empty_state()
        history_entry_id = uuid.uuid4().hex if save else ""
        prompt_bubble = ChatBubble(
            "user",
            self.display_prompt_text(full_prompt),
            show_copy=True,
            parent=self.chat_container,
            copy_text="复制提示词",
            show_paste_ai=True,
            prompt_input_text=self.prompt_text_from_system_prompt(full_prompt),
            scrollable=True,
            max_content_height=180,
        )
        prompt_bubble.content = full_prompt
        prompt_bubble.history_entry_id = history_entry_id
        prompt_bubble.copy_requested.connect(lambda bubble=prompt_bubble: self.copy_prompt_bubble(bubble))
        prompt_bubble.paste_ai_requested.connect(lambda: self.add_ai_response_frame(focus=True))
        self.chat_layout.addWidget(prompt_bubble)
        if animate:
            animate_widget_in(prompt_bubble)
        if save:
            self.append_history({
                "id": history_entry_id,
                "type": "prompt",
                "content": full_prompt,
            })
        return prompt_bubble

    def ensure_initial_prompt_bubble(self):
        full_prompt = self.build_system_prompt("")
        self.add_prompt_bubble(full_prompt, save=True, animate=False)
    
    def update_status_bar(self):
        n = self.terminal_panel.count()
        is_collapsed = self.terminal_panel.maximumHeight() == 0
        if is_collapsed:
            self.status_bar.setText(f"终端 · {n} 个进程")
            self.status_bar.setVisible(True)
            self.terminal_resize_handle.setVisible(False)
        else:
            self.status_bar.setVisible(False)
            self.terminal_resize_handle.setVisible(True)

    def resize_terminal_panel(self, height: int):
        was_at_bottom = self.is_chat_at_bottom()
        self.terminal_panel.set_expanded_height(height)
        self.update_status_bar()
        if was_at_bottom:
            self.keep_ai_response_visible()

    def clear_chat_widgets(self):
        while self.chat_layout.count():
            item = self.chat_layout.takeAt(0)
            widget = item.widget()
            if widget and widget is not self.empty_state:
                widget.deleteLater()
            elif widget is self.empty_state:
                widget.setParent(None)
        self.empty_state.setParent(self.chat_container)
        self.chat_layout.addWidget(self.empty_state)
        self.empty_state.setVisible(True)

    def append_history(self, entry: Dict[str, object]):
        if not entry.get("id"):
            entry["id"] = uuid.uuid4().hex
        if not entry.get("created_at"):
            entry["created_at"] = datetime.now().isoformat(timespec="seconds")
        self.history_entries.append(entry)
        self.save_history()

    def save_history(self):
        if self.project_root:
            save_workspace_history(self.project_root, self.history_entries, self.thread_id)

    def load_history(self):
        self.history_entries = load_workspace_history(self.project_root, self.thread_id)
        self.setUpdatesEnabled(False)
        try:
            self.clear_chat_widgets()
            if not self.history_entries:
                self.ensure_initial_prompt_bubble()
                self.scroll_to_bottom()
                return
            self.hide_empty_state()
            for entry in self.history_entries:
                self.restore_history_entry(entry)
            if any(entry.get("type") != "prompt" for entry in self.history_entries):
                self.ensure_ai_response_entry(focus=False, animate=False, keep_visible=False)
            self.scroll_to_bottom()
        finally:
            self.setUpdatesEnabled(True)

    def switch_thread(self, thread_id: str):
        thread_id = safe_thread_id(thread_id)
        if thread_id == self.thread_id or self.is_execution_running():
            return
        self.thread_id = thread_id
        self.sidebar.set_active_thread(thread_id)
        self.load_history()

    def create_thread(self):
        if not self.project_root:
            return
        thread = create_workspace_thread(self.project_root, self.threads)
        self.threads = load_workspace_threads(self.project_root)
        self.thread_id = str(thread.get("id", DEFAULT_THREAD_ID))
        self.sidebar.set_threads(self.threads, self.thread_id)
        self.sidebar.set_tab("threads")
        self.load_history()

    def delete_thread(self, thread_id: str):
        thread_id = safe_thread_id(thread_id)
        if thread_id == DEFAULT_THREAD_ID:
            return
        thread = next((item for item in self.threads if item.get("id") == thread_id), None)
        title = str(thread.get("title", "这个会话")) if thread else "这个会话"
        ok = styled_confirm(
            self,
            "删除会话",
            f"确定删除「{title}」吗？它的聊天记录和 diff 缓存会被清理，项目文件不会被删除。",
            confirm_text="删除",
            destructive=True,
        )
        if not ok:
            return
        if not delete_workspace_thread(self.project_root, thread_id, self.threads):
            styled_warning(self, "删除失败", "无法删除这个会话缓存。")
            return
        self.threads = load_workspace_threads(self.project_root)
        if self.thread_id == thread_id:
            self.thread_id = DEFAULT_THREAD_ID
            self.load_history()
        self.sidebar.set_threads(self.threads, self.thread_id)
        self.sidebar.set_tab("threads")

    def restore_history_entry(self, entry: Dict[str, object]):
        entry_type = entry.get("type")
        if entry_type == "prompt":
            self.add_prompt_bubble(str(entry.get("content", "")), save=False, animate=False)
        elif entry_type == "ai":
            bubble = ChatBubble(
                "ai",
                str(entry.get("content", "")),
                show_copy=True,
                parent=self.chat_container,
                copy_text="复制 AI 输出",
                scrollable=True,
                max_content_height=190,
            )
            self.chat_layout.addWidget(bubble)
        elif entry_type == "result":
            bubble = ChatBubble(
                "system",
                str(entry.get("content", "")),
                show_copy=True,
                parent=self.chat_container,
                copy_text="复制执行日志",
                scrollable=True,
                max_content_height=210,
            )
            self.chat_layout.addWidget(bubble)
            records = []
            for raw_record in entry.get("changes", []) if isinstance(entry.get("changes"), list) else []:
                record = deserialize_change_record(raw_record)
                if record:
                    records.append(record)
            if records:
                change_card = ChangeSummaryCard(records, parent=self.chat_container)
                change_card.history_entry_id = entry.get("id")
                change_card.undo_requested.connect(self.undo_changes)
                change_card.redo_requested.connect(self.redo_changes)
                if bool(entry.get("undone", False)):
                    change_card.mark_undone(len(records), 0)
                self.chat_layout.addWidget(change_card)

    def update_change_history_state(self, entry_id: str, undone: bool):
        if not entry_id:
            return
        for entry in self.history_entries:
            if entry.get("id") == entry_id and entry.get("type") == "result":
                entry["undone"] = undone
                for record in entry.get("changes", []) if isinstance(entry.get("changes"), list) else []:
                    record["undone"] = undone
                self.save_history()
                return

    def clear_chat_history(self):
        if not self.project_root:
            return
        ok = styled_confirm(
            self,
            "清空当前会话",
            "确定清空当前会话卡的聊天记录和缓存的 diff 吗？这不会影响其他会话，也不会删除项目文件。",
            confirm_text="清空",
            destructive=True,
        )
        if not ok:
            return
        if clear_workspace_history(self.project_root, self.thread_id):
            self.history_entries = []
            self.clear_chat_widgets()
            self.ensure_initial_prompt_bubble()
            self.scroll_to_bottom()
        else:
            styled_warning(self, "清空失败", "无法删除当前工作区的 .agent_qt 缓存目录。")

    def add_ai_response_frame(self, focus: bool = True, animate: bool = True, keep_visible: bool = True):
        self.hide_empty_state()
        existing_frame = self.find_open_ai_response_frame()
        if existing_frame is not None:
            if keep_visible:
                self.keep_ai_response_visible()
            ai_input = getattr(existing_frame, "ai_input", None)
            if focus and ai_input is not None:
                QTimer.singleShot(60, ai_input.setFocus)
            return
        ai_frame = QFrame(self.chat_container)
        ai_frame.setObjectName("aiResponseFrame")
        ai_frame.is_closing = False
        ai_frame.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        ai_frame.setStyleSheet(f"""
            QFrame#aiResponseFrame {{
                background: {COLORS['card_ai']};
                border: 1px solid #d7ccff;
                border-radius: 16px;
                margin: 4px 0;
            }}
        """)
        ai_layout = QVBoxLayout(ai_frame)
        ai_layout.setContentsMargins(16, 12, 16, 12)
        ai_layout.setSpacing(8)

        header_row = QHBoxLayout()
        title_label = QLabel("AI 回复区")
        title_label.setFixedHeight(22)
        title_label.setStyleSheet(f"color: {COLORS['text']}; font-weight: 900; font-size: 13px; background: transparent; border: none;")
        hint_label = QLabel("粘贴完整输出，包含 Bash 与后续代码块。")
        hint_label.setFixedHeight(22)
        hint_label.setStyleSheet(f"color: {COLORS['text_secondary']}; font-size: 12px; background: transparent; border: none;")
        header_row.addWidget(title_label)
        header_row.addSpacing(10)
        header_row.addWidget(hint_label)
        header_row.addStretch()
        confirm_btn = QPushButton("确定执行", clicked=lambda: self.process_ai_response(ai_frame), cursor=Qt.PointingHandCursor)
        confirm_btn.setFixedHeight(30)
        confirm_btn.setStyleSheet(f"""
            QPushButton {{
                background: {COLORS['success']};
                color: white;
                border: none;
                border-radius: 10px;
                padding: 6px 16px;
                font-size: 12px;
                font-weight: 900;
            }}
            QPushButton:hover {{
                background: #0f8a59;
            }}
        """)
        header_row.addWidget(confirm_btn)
        ai_layout.addLayout(header_row)
        
        ai_input = QTextEdit(placeholderText="在此粘贴 AI 的完整输出（含代码块）...")
        ai_input.setFixedHeight(190)
        ai_input.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        ai_input.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        ai_input.customContextMenuRequested.connect(
            lambda pos, editor=ai_input: show_chinese_edit_menu(editor, editor.mapToGlobal(pos))
        )
        ai_input.setStyleSheet(f"""
            QTextEdit {{
                background: {COLORS['input_bg']};
                color: {COLORS['text']};
                border: 1px solid #d7ccff;
                border-radius: 12px;
                padding: 12px;
                font-size: 13px;
                font-family: 'SF Mono', 'Menlo', monospace;
            }}
            QTextEdit:focus {{
                border: 1px solid {COLORS['accent']};
            }}
        """)
        ai_frame.ai_input = ai_input
        ai_layout.addWidget(ai_input)
        
        self.chat_layout.addWidget(ai_frame)
        if animate:
            animate_widget_in(ai_frame)
        if keep_visible:
            self.keep_ai_response_visible()
        if focus:
            QTimer.singleShot(80, ai_input.setFocus)

    def find_open_ai_response_frame(self) -> Optional[QFrame]:
        for idx in range(self.chat_layout.count()):
            widget = self.chat_layout.itemAt(idx).widget()
            if (
                widget
                and widget.objectName() == "aiResponseFrame"
                and hasattr(widget, "ai_input")
                and not getattr(widget, "is_closing", False)
            ):
                return widget
        return None
    
    def send_prompt(self):
        self.append_prompt_bubble_from_toolbar()
    
    def process_ai_response(self, ai_frame: QFrame):
        ai_input = getattr(ai_frame, 'ai_input', None)
        if ai_input is None:
            return
        text = ai_input.toPlainText().strip()
        if not text:
            return
        self.hide_empty_state()
        
        idx = self.chat_layout.indexOf(ai_frame)
        ai_frame.is_closing = True

        def remove_input_frame():
            self.chat_layout.removeWidget(ai_frame)
            ai_frame.deleteLater()

        animate_widget_out(ai_frame, remove_input_frame)
        
        ai_bubble = ChatBubble(
            "ai",
            text,
            show_copy=True,
            parent=self.chat_container,
            copy_text="复制 AI 输出",
            scrollable=True,
            max_content_height=190,
        )
        self.chat_layout.insertWidget(idx, ai_bubble)
        animate_widget_in(ai_bubble)
        self.append_history({
            "type": "ai",
            "content": text,
        })
        
        blocks = scan_all_code_blocks(text)
        commands = extract_bash_commands(text, blocks)
        
        if not commands:
            warning_bubble = ChatBubble(
                "system",
                "⚠️ 未识别到可执行命令\n请确保 AI 输出包含 ```bash 代码块",
                parent=self.chat_container,
                scrollable=False,
                max_content_height=110,
            )
            self.chat_layout.addWidget(warning_bubble)
            animate_widget_in(warning_bubble)
            self.ensure_ai_response_entry(focus=False, animate=True, keep_visible=False)
            self.scroll_to_bottom()
            return
        
        self.result_bubble = ChatBubble(
            "system",
            "⏳ 执行中...",
            parent=self.chat_container,
            show_copy=True,
            copy_text="复制执行日志",
            scrollable=True,
            max_content_height=210,
        )
        self.chat_layout.addWidget(self.result_bubble)
        animate_widget_in(self.result_bubble)
        
        self.cmd_outputs = []
        self.pending_snapshot = snapshot_project(self.project_root)
        
        self.worker = ExecuteWorker(commands, self.project_root)
        self.worker.output_signal.connect(self.on_output)
        self.worker.long_running_signal.connect(self.on_long_running)
        self.worker.finished_signal.connect(self.on_finished)
        self.worker.start()
        self.scroll_to_bottom()
    
    def on_output(self, output: str):
        self.cmd_outputs.append(output)
        self.result_bubble.update_content('\n'.join(self.cmd_outputs))
    
    def on_long_running(self, cmd: str, cwd: str, name: str):
        self.terminal_panel.add_process(cmd, cwd, name)
        self.update_status_bar()
        self.keep_ai_response_visible()
    
    def on_finished(self, full_log: str):
        worker = self.worker
        self.worker = None
        if worker is not None:
            worker.deleteLater()
        after_snapshot = snapshot_project(self.project_root)
        change_records = build_change_records(self.pending_snapshot, after_snapshot)
        self.pending_snapshot = {}
        log_with_changes = full_log
        if change_records:
            log_with_changes += format_change_summary(change_records, include_diff=True)
        self.result_bubble.update_content(log_with_changes)
        if change_records:
            change_card = ChangeSummaryCard(change_records, parent=self.chat_container)
            change_card.undo_requested.connect(self.undo_changes)
            change_card.redo_requested.connect(self.redo_changes)
            self.chat_layout.addWidget(change_card)
            animate_widget_in(change_card)
        result_entry = {
            "type": "result",
            "content": log_with_changes,
            "changes": [serialize_change_record(record) for record in change_records],
            "undone": False,
        }
        if change_records:
            result_entry["id"] = uuid.uuid4().hex
            change_card.history_entry_id = result_entry["id"]
        self.append_history(result_entry)
        self.sidebar.refresh_tree(self.project_root)
        self.update_status_bar()
        self.ensure_ai_response_entry(focus=False, animate=True, keep_visible=False)
        self.scroll_to_bottom()

    def hide_empty_state(self):
        if getattr(self, 'empty_state', None) and self.empty_state.isVisible():
            self.empty_state.setVisible(False)

    def undo_changes(self, card: ChangeSummaryCard):
        result = restore_change_records(self.project_root, card.records)
        if int(result["skipped"]) > 0:
            self.show_change_conflict("Undo", result)
            return
        card.mark_undone(int(result["applied"]), 0)
        if int(result["skipped"]) == 0:
            self.update_change_history_state(getattr(card, "history_entry_id", ""), True)
        self.sidebar.refresh_tree(self.project_root)

    def redo_changes(self, card: ChangeSummaryCard):
        result = redo_change_records(self.project_root, card.records)
        if int(result["skipped"]) > 0:
            self.show_change_conflict("Redo", result)
            return
        card.mark_redone(int(result["applied"]), 0)
        if int(result["skipped"]) == 0:
            self.update_change_history_state(getattr(card, "history_entry_id", ""), False)
        self.sidebar.refresh_tree(self.project_root)

    def show_change_conflict(self, action: str, result: Dict[str, object]):
        conflicts = [str(path) for path in result.get("conflicts", [])]
        preview = "\n".join(f"- {path}" for path in conflicts[:8])
        if len(conflicts) > 8:
            preview += f"\n... 还有 {len(conflicts) - 8} 个文件"
        styled_warning(
            self,
            f"{action} 失败",
            "当前文件内容和这条变更记录不一致，已取消整组操作。\n"
            "请按顺序 Undo/Redo，或确认没有手动改过这些文件。\n\n"
            f"冲突文件：\n{preview or '(未知)'}",
        )

# ============================================================
# 主窗口
# ============================================================
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Agent 控制台 v5.1")
        self.setMinimumSize(960, 680)
        self.resize(1120, 820)
        self.setStyleSheet(f"""
            QMainWindow {{
                background: {COLORS['bg']};
            }}
            QMenu {{
                background: {COLORS['surface']};
                color: {COLORS['text']};
                border: 1px solid {COLORS['border']};
                border-radius: 10px;
                padding: 6px;
            }}
            QMenu::item {{
                padding: 8px 18px;
                border-radius: 7px;
            }}
            QMenu::item:selected {{
                background: {COLORS['accent_light']};
                color: {COLORS['accent_dark']};
            }}
        """)
        
        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)
        
        self.home_page = HomePage()
        self.home_page.enter_chat.connect(self.open_chat)
        self.stack.addWidget(self.home_page)
        
        self.chat_page = ChatPage()
        self.chat_page.back_home.connect(lambda: self.stack.setCurrentWidget(self.home_page))
        self.stack.addWidget(self.chat_page)
        
        self.stack.setCurrentWidget(self.home_page)
    
    def open_chat(self, path: str):
        self.stack.setCurrentWidget(self.chat_page)
        self.chat_page.set_project(path)

def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setFont(QFont("PingFang SC", 13))
    app.setStyleSheet(app_global_style())
    MainWindow().show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
