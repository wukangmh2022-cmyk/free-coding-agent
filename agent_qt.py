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
import hashlib
import shutil
import shlex
import uuid
import urllib.error
import urllib.parse
import urllib.request
import venv
import time
import tempfile
import locale
import signal
import logging
import threading
import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Dict, List, Optional
from datetime import datetime, timedelta

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTextEdit, QPushButton, QLabel, QScrollArea, QFrame,
    QFileDialog, QMessageBox, QLineEdit, QTreeWidget, QTreeWidgetItem,
    QMenu, QToolButton, QStyle, QPlainTextEdit, QTextBrowser, QStackedWidget,
    QGridLayout, QSizePolicy, QGraphicsOpacityEffect, QAbstractItemView,
    QSpacerItem, QWidgetAction, QAbstractButton, QDialog, QCheckBox, QComboBox
)
from PySide6.QtCore import Qt, QTimer, Signal, QThread, QProcess, QProcessEnvironment, QPropertyAnimation, QEasingCurve, QSize, QByteArray, QEvent, QRectF, QPoint, Property, QObject
from PySide6.QtGui import QFont, QAction, QDesktopServices, QMouseEvent, QTextCursor, QIcon, QPixmap, QPainter, QPen, QColor, QKeySequence, QTextDocument, QImage
from PySide6.QtCore import QUrl

try:
    from PySide6.QtSvg import QSvgRenderer
except ImportError:
    QSvgRenderer = None

PROMPT_BUBBLE_MARKER = "<!-- agent_qt_user_prompt:"
AUTOMATION_DONE_MARKER = "AGENT_DONE"
COMPLETION_LINE_RE = re.compile(r"^\s*(?:AGENT_DONE\b|AGENT_QT_DONE\b)", re.I)
AGENT_HOME_DIR = os.path.expanduser(os.environ.get("AGENT_QT_HOME", "~/.agent_qt"))
_AGENT_RUNTIME_PYTHON: Optional[str] = None
_AGENT_RUNTIME_ERROR = ""
_APP_SETTINGS: Optional[Dict[str, object]] = None
_AGENT_RUNTIME_ENABLED: Optional[bool] = None
_AUTOMATION_ENABLED: Optional[bool] = None
_DEVELOPER_MODE_ENABLED: Optional[bool] = None
_WECHAT_BRIDGE_ENABLED: Optional[bool] = None
QT_WIDGET_MAX_HEIGHT = 16777215
DEFAULT_PIP_INDEX_URL = "https://pypi.tuna.tsinghua.edu.cn/simple"
FALLBACK_PIP_INDEXES = [
    ("阿里云 PyPI 镜像", "https://mirrors.aliyun.com/pypi/simple"),
    ("腾讯云 PyPI 镜像", "https://mirrors.cloud.tencent.com/pypi/simple"),
]
OFFICIAL_PIP_INDEX_URL = "https://pypi.org/simple"
logger = logging.getLogger(__name__)


def ai_border_color() -> str:
    return "#d7ccff" if app_theme_setting() == "light" else COLORS["border"]


def soft_accent_border_color() -> str:
    return "#d8d0ff" if app_theme_setting() == "light" else COLORS["border"]


def subprocess_no_window_kwargs(extra_creationflags: int = 0) -> Dict[str, object]:
    if platform.system() != "Windows":
        return {}
    flags = int(extra_creationflags or 0) | int(getattr(subprocess, "CREATE_NO_WINDOW", 0) or 0)
    return {"creationflags": flags} if flags else {}


def process_alive(pid: int) -> bool:
    try:
        pid = int(pid or 0)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def app_settings_path() -> str:
    return os.path.join(AGENT_HOME_DIR, "settings.json")


def load_app_settings() -> Dict[str, object]:
    global _APP_SETTINGS
    if _APP_SETTINGS is not None:
        return _APP_SETTINGS
    path = app_settings_path()
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                payload = json.load(f)
            _APP_SETTINGS = payload if isinstance(payload, dict) else {}
        except (OSError, json.JSONDecodeError):
            _APP_SETTINGS = {}
    else:
        _APP_SETTINGS = {}
    return _APP_SETTINGS


def save_app_settings(settings: Dict[str, object]) -> bool:
    global _APP_SETTINGS
    _APP_SETTINGS = dict(settings)
    try:
        os.makedirs(AGENT_HOME_DIR, exist_ok=True)
        path = app_settings_path()
        tmp_path = path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(_APP_SETTINGS, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
        return True
    except OSError:
        return False


def agent_runtime_enabled() -> bool:
    global _AGENT_RUNTIME_ENABLED
    if _AGENT_RUNTIME_ENABLED is not None:
        return _AGENT_RUNTIME_ENABLED
    env_value = os.environ.get("AGENT_QT_USE_RUNTIME", "").strip().lower()
    if env_value:
        _AGENT_RUNTIME_ENABLED = env_value not in {"0", "false", "no", "off"}
        return _AGENT_RUNTIME_ENABLED
    settings = load_app_settings()
    _AGENT_RUNTIME_ENABLED = bool(settings.get("agent_runtime_enabled", False))
    return _AGENT_RUNTIME_ENABLED


def set_agent_runtime_enabled(enabled: bool):
    global _AGENT_RUNTIME_ENABLED
    _AGENT_RUNTIME_ENABLED = bool(enabled)
    settings = load_app_settings()
    settings["agent_runtime_enabled"] = _AGENT_RUNTIME_ENABLED
    save_app_settings(settings)


def agent_runtime_ready() -> bool:
    return bool(find_existing_runtime_python())


def automation_enabled_setting() -> bool:
    global _AUTOMATION_ENABLED
    if _AUTOMATION_ENABLED is not None:
        return _AUTOMATION_ENABLED
    env_value = os.environ.get("AGENT_QT_AUTOMATION_ENABLED", "").strip().lower()
    if env_value:
        _AUTOMATION_ENABLED = env_value not in {"0", "false", "no", "off"}
        return _AUTOMATION_ENABLED
    settings = load_app_settings()
    _AUTOMATION_ENABLED = bool(settings.get("automation_enabled", False))
    return _AUTOMATION_ENABLED


def set_automation_enabled_setting(enabled: bool):
    global _AUTOMATION_ENABLED
    _AUTOMATION_ENABLED = bool(enabled)
    settings = load_app_settings()
    settings["automation_enabled"] = _AUTOMATION_ENABLED
    save_app_settings(settings)


def automation_context_mode_setting() -> str:
    value = str(load_app_settings().get("automation_context_mode", "expert") or "expert").strip().lower()
    return value if value in {"expert", "simple"} else "expert"


def set_automation_context_mode_setting(mode: str):
    settings = load_app_settings()
    settings["automation_context_mode"] = "simple" if str(mode).strip().lower() == "simple" else "expert"
    save_app_settings(settings)


def app_theme_setting() -> str:
    value = str(load_app_settings().get("theme", "light") or "light").strip().lower()
    return value if value in {"light", "dark"} else "light"


def set_app_theme_setting(theme: str):
    settings = load_app_settings()
    settings["theme"] = "dark" if str(theme).strip().lower() == "dark" else "light"
    save_app_settings(settings)


def chat_font_scale_setting() -> float:
    try:
        value = float(load_app_settings().get("chat_font_scale", 1.0))
    except (TypeError, ValueError):
        value = 1.0
    return min(1.35, max(0.9, value))


def set_chat_font_scale_setting(scale: float):
    settings = load_app_settings()
    settings["chat_font_scale"] = min(1.35, max(0.9, float(scale)))
    save_app_settings(settings)


def scaled_font_px(base: int) -> int:
    return max(9, int(round(base * chat_font_scale_setting())))


def developer_mode_enabled() -> bool:
    global _DEVELOPER_MODE_ENABLED
    if _DEVELOPER_MODE_ENABLED is not None:
        return _DEVELOPER_MODE_ENABLED
    env_value = os.environ.get("AGENT_QT_SHOW_AUTOMATION_TRACEBACK", "").strip().lower()
    if env_value:
        _DEVELOPER_MODE_ENABLED = env_value in {"1", "true", "yes", "on"}
        return _DEVELOPER_MODE_ENABLED
    settings = load_app_settings()
    _DEVELOPER_MODE_ENABLED = bool(settings.get("developer_mode_enabled", False))
    return _DEVELOPER_MODE_ENABLED


def set_developer_mode_enabled(enabled: bool):
    global _DEVELOPER_MODE_ENABLED
    _DEVELOPER_MODE_ENABLED = bool(enabled)
    settings = load_app_settings()
    settings["developer_mode_enabled"] = _DEVELOPER_MODE_ENABLED
    save_app_settings(settings)


def developer_error_details_enabled() -> bool:
    return developer_mode_enabled()


def compact_code_blocks_by_default() -> bool:
    return not developer_error_details_enabled()


def wechat_bridge_enabled_setting() -> bool:
    global _WECHAT_BRIDGE_ENABLED
    if _WECHAT_BRIDGE_ENABLED is not None:
        return _WECHAT_BRIDGE_ENABLED
    env_value = os.environ.get("AGENT_QT_WECHAT_BRIDGE_ENABLED", "").strip().lower()
    if env_value:
        _WECHAT_BRIDGE_ENABLED = env_value not in {"0", "false", "no", "off"}
        return _WECHAT_BRIDGE_ENABLED
    _WECHAT_BRIDGE_ENABLED = bool(load_app_settings().get("wechat_bridge_enabled", False))
    return _WECHAT_BRIDGE_ENABLED


def set_wechat_bridge_enabled_setting(enabled: bool):
    global _WECHAT_BRIDGE_ENABLED
    _WECHAT_BRIDGE_ENABLED = bool(enabled)
    settings = load_app_settings()
    settings["wechat_bridge_enabled"] = _WECHAT_BRIDGE_ENABLED
    save_app_settings(settings)

def wechat_connector_autostart_setting() -> bool:
    return bool(load_app_settings().get("wechat_connector_autostart", True))


def set_wechat_connector_autostart_setting(enabled: bool):
    settings = load_app_settings()
    settings["wechat_connector_autostart"] = bool(enabled)
    save_app_settings(settings)


def use_system_proxy_setting() -> bool:
    return bool(load_app_settings().get("use_system_proxy", False))


def set_use_system_proxy_setting(enabled: bool):
    settings = load_app_settings()
    settings["use_system_proxy"] = bool(enabled)
    save_app_settings(settings)


def wechat_bridge_settings() -> Dict[str, object]:
    settings = load_app_settings()
    raw = settings.get("wechat_bridge")
    payload = dict(raw) if isinstance(raw, dict) else {}
    host = str(payload.get("host") or os.environ.get("AGENT_QT_WECHAT_BRIDGE_HOST", "127.0.0.1")).strip() or "127.0.0.1"
    try:
        port = int(payload.get("port") or os.environ.get("AGENT_QT_WECHAT_BRIDGE_PORT", "8798"))
    except (TypeError, ValueError):
        port = 8798
    port = max(1, min(65535, port))
    api_key = str(payload.get("api_key") or os.environ.get("AGENT_QT_WECHAT_BRIDGE_API_KEY", "")).strip()
    silent = bool(payload.get("silent", True))
    timeout = payload.get("timeout_seconds", 900)
    try:
        timeout = int(timeout)
    except (TypeError, ValueError):
        timeout = 900
    timeout = max(5, min(3600, timeout))
    return {
        "host": host,
        "port": port,
        "api_key": api_key,
        "silent": silent,
        "timeout_seconds": timeout,
    }


def wechat_connector_root() -> str:
    return os.path.join(AGENT_HOME_DIR, "wechat_connector")


def wechat_connector_account_path() -> str:
    return os.path.join(wechat_connector_root(), "account.json")


def wechat_connector_sync_path(account_id: str) -> str:
    safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", account_id or "default")
    return os.path.join(wechat_connector_root(), f"sync-{safe_id}.txt")


def wechat_inbox_dir(root: str) -> str:
    base = root if root else wechat_connector_root()
    return os.path.join(base, "wechat_inbox")


WECHAT_EXT_TO_MIME = {
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".ppt": "application/vnd.ms-powerpoint",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".txt": "text/plain",
    ".csv": "text/csv",
    ".zip": "application/zip",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
}


def wechat_mime_from_filename(filename: str) -> str:
    return WECHAT_EXT_TO_MIME.get(os.path.splitext(str(filename or ""))[1].lower(), "application/octet-stream")


def wechat_connector_state_text() -> str:
    path = wechat_connector_account_path()
    if not os.path.exists(path):
        return "未登录"
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        account_id = str(payload.get("account_id") or "")
        saved_at = str(payload.get("saved_at") or "")
        return f"已保存登录：{account_id or 'unknown'} {saved_at}".strip()
    except Exception:
        return "登录状态文件损坏，请重新扫码登录"


def set_wechat_bridge_settings(payload: Dict[str, object]):
    settings = load_app_settings()
    current = dict(settings.get("wechat_bridge") or {})
    for key in ("host", "port", "api_key", "silent", "timeout_seconds"):
        if key in payload:
            current[key] = payload[key]
    settings["wechat_bridge"] = current
    save_app_settings(settings)


def remember_wechat_reply_target(to_user: str, context_token: str):
    to_user = str(to_user or "").strip()
    context_token = str(context_token or "").strip()
    if not to_user or not context_token:
        return
    settings = load_app_settings()
    settings["wechat_last_reply_target"] = {
        "to_user": to_user,
        "context_token": context_token,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    save_app_settings(settings)


def last_wechat_reply_target() -> Dict[str, str]:
    raw = load_app_settings().get("wechat_last_reply_target")
    payload = dict(raw) if isinstance(raw, dict) else {}
    return {
        "to_user": str(payload.get("to_user") or "").strip(),
        "context_token": str(payload.get("context_token") or "").strip(),
    }


def parse_boolish(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on", "y"}:
        return True
    if text in {"0", "false", "no", "off", "n"}:
        return False
    return default


def env_int(name: str, default: int, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.environ.get(name, str(default))))
    except (TypeError, ValueError):
        return default


def runtime_cache_root() -> str:
    return os.path.abspath(os.path.expanduser(os.environ.get("AGENT_QT_RUNTIME_DIR", os.path.join(AGENT_HOME_DIR, "runtime"))))


def runtime_venv_root() -> str:
    return os.path.join(runtime_cache_root(), "python")


def runtime_shim_dir() -> str:
    return os.path.join(runtime_cache_root(), "shims")


def runtime_bin_dir(root: str) -> str:
    return os.path.join(root, "Scripts" if platform.system() == "Windows" else "bin")


def runtime_python_in_root(root: str) -> str:
    name = "python.exe" if platform.system() == "Windows" else "python"
    return os.path.join(runtime_bin_dir(root), name)


def bundled_runtime_roots() -> List[str]:
    roots = []
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        roots.extend([
            os.path.join(exe_dir, "runtime", "python"),
            os.path.join(exe_dir, "_internal", "runtime", "python"),
            os.path.abspath(os.path.join(exe_dir, "..", "Resources", "runtime", "python")),
        ])
        meipass = getattr(sys, "_MEIPASS", "")
        if meipass:
            roots.append(os.path.join(meipass, "runtime", "python"))
    else:
        source_dir = os.path.dirname(os.path.abspath(__file__))
        roots.append(os.path.join(source_dir, "runtime", "python"))
    return roots


def bundled_asset_roots() -> List[str]:
    roots: List[str] = []
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        roots.extend([
            os.path.join(exe_dir, "assets"),
            os.path.join(exe_dir, "_internal", "assets"),
            os.path.abspath(os.path.join(exe_dir, "..", "Resources", "assets")),
        ])
        meipass = getattr(sys, "_MEIPASS", "")
        if meipass:
            roots.append(os.path.join(meipass, "assets"))
    else:
        source_dir = os.path.dirname(os.path.abspath(__file__))
        roots.append(os.path.join(source_dir, "assets"))
    return roots


def find_bundled_asset(*relative_parts: str) -> str:
    for root in bundled_asset_roots():
        candidate = os.path.join(root, *relative_parts)
        if os.path.isfile(candidate):
            return os.path.abspath(candidate)
    return ""


def find_existing_runtime_python() -> str:
    explicit = os.path.expanduser(os.environ.get("AGENT_QT_PYTHON", ""))
    candidates = [explicit] if explicit else []
    candidates.append(runtime_python_in_root(runtime_venv_root()))
    for root in bundled_runtime_roots():
        candidates.append(runtime_python_in_root(root))
    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            return os.path.abspath(candidate)
    return ""


def write_text_executable(path: str, content: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)
    try:
        os.chmod(path, 0o755)
    except OSError:
        pass


def ensure_runtime_shims(python_bin: str):
    shim_dir = runtime_shim_dir()
    os.makedirs(shim_dir, exist_ok=True)
    if platform.system() == "Windows":
        quoted = f'"{python_bin}"'
        scripts = {
            "python.cmd": f"@echo off\r\n{quoted} %*\r\n",
            "python3.cmd": f"@echo off\r\n{quoted} %*\r\n",
            "pip.cmd": f"@echo off\r\n{quoted} -m pip %*\r\n",
            "pip3.cmd": f"@echo off\r\n{quoted} -m pip %*\r\n",
        }
        for name, content in scripts.items():
            with open(os.path.join(shim_dir, name), "w", encoding="utf-8", newline="\r\n") as f:
                f.write(content)
        return
    quoted = shlex.quote(python_bin)
    scripts = {
        "python": f"#!/bin/sh\nexec {quoted} \"$@\"\n",
        "python3": f"#!/bin/sh\nexec {quoted} \"$@\"\n",
        "pip": f"#!/bin/sh\nexec {quoted} -m pip \"$@\"\n",
        "pip3": f"#!/bin/sh\nexec {quoted} -m pip \"$@\"\n",
    }
    for name, content in scripts.items():
        write_text_executable(os.path.join(shim_dir, name), content)


def ensure_agent_runtime(create: bool = True) -> str:
    global _AGENT_RUNTIME_PYTHON, _AGENT_RUNTIME_ERROR
    if not agent_runtime_enabled():
        return ""
    if _AGENT_RUNTIME_PYTHON and os.path.isfile(_AGENT_RUNTIME_PYTHON):
        return _AGENT_RUNTIME_PYTHON
    existing = find_existing_runtime_python()
    if existing:
        _AGENT_RUNTIME_PYTHON = existing
        _AGENT_RUNTIME_ERROR = ""
        ensure_runtime_shims(existing)
        return existing
    if not create:
        return ""
    root = runtime_venv_root()
    try:
        os.makedirs(runtime_cache_root(), exist_ok=True)
        venv.EnvBuilder(with_pip=True, clear=False).create(root)
        python_bin = runtime_python_in_root(root)
        if not os.path.isfile(python_bin):
            raise FileNotFoundError(python_bin)
        _AGENT_RUNTIME_PYTHON = python_bin
        _AGENT_RUNTIME_ERROR = ""
        ensure_runtime_shims(python_bin)
        return python_bin
    except Exception as exc:
        _AGENT_RUNTIME_ERROR = str(exc)
        return ""


def agent_runtime_env(create: bool = True) -> Dict[str, str]:
    env = os.environ.copy()
    python_bin = ensure_agent_runtime(create=create)
    path_parts = []
    if python_bin:
        env["AGENT_QT_RUNTIME_PYTHON"] = python_bin
        env["VIRTUAL_ENV"] = os.path.dirname(os.path.dirname(python_bin))
        path_parts.extend([runtime_shim_dir(), os.path.dirname(python_bin)])
    if env.get("PATH"):
        path_parts.append(env["PATH"])
    env["PATH"] = os.pathsep.join(part for part in path_parts if part)
    env["PYTHONUNBUFFERED"] = "1"
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PIP_DISABLE_PIP_VERSION_CHECK", "1")
    return env


def agent_qprocess_environment(create: bool = True) -> QProcessEnvironment:
    env_dict = agent_runtime_env(create=create)
    qenv = QProcessEnvironment()
    for key, value in env_dict.items():
        qenv.insert(str(key), str(value))
    return qenv


def agent_runtime_description() -> str:
    if not agent_runtime_enabled():
        return "系统 Python/PATH（Agent 缓存 Python 已关闭）"
    python_bin = ensure_agent_runtime(create=False)
    if python_bin:
        return f"Agent 缓存 Python ({python_bin})；python/python3/pip/pip3 会优先指向该环境"
    return f"系统 Python/PATH（未安装 Agent 缓存 Python；可在设置里安装到 {runtime_venv_root()}）"


def pip_index_args() -> List[str]:
    index_url = os.environ.get("AGENT_QT_PIP_INDEX_URL", DEFAULT_PIP_INDEX_URL).strip()
    if not index_url:
        return []
    args = ["-i", index_url]
    parsed_host = urllib.parse.urlparse(index_url).hostname
    if parsed_host:
        args.extend(["--trusted-host", parsed_host])
    return args


def posix_join(args: List[str]) -> str:
    return " ".join(shlex.quote(str(arg)) for arg in args if str(arg))


WINDOWS_PYTHON_INSTALLER_URLS = [
    "https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe",
    "https://mirrors.aliyun.com/python-release/windows/python-3.12.10-amd64.exe",
    "https://mirrors.tuna.tsinghua.edu.cn/python/3.12.10/python-3.12.10-amd64.exe",
    "https://mirrors.huaweicloud.com/python/3.12.10/python-3.12.10-amd64.exe",
]
WINDOWS_INSTALLER_PIP_SOURCE_MAP = [
    ("www.python.org", OFFICIAL_PIP_INDEX_URL, "pypi.org files.pythonhosted.org"),
    ("mirrors.aliyun.com", "https://mirrors.aliyun.com/pypi/simple", "mirrors.aliyun.com"),
    ("mirrors.tuna.tsinghua.edu.cn", DEFAULT_PIP_INDEX_URL, "pypi.tuna.tsinghua.edu.cn"),
    ("mirrors.huaweicloud.com", "https://mirrors.huaweicloud.com/repository/pypi/simple", "mirrors.huaweicloud.com"),
]


def ps_quote(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def ps_array(args: List[str]) -> str:
    return "@(" + ", ".join(ps_quote(str(arg)) for arg in args) + ")"


def windows_python_installer_urls() -> List[str]:
    raw = os.environ.get("AGENT_QT_WINDOWS_PYTHON_INSTALLER_URLS", "").strip()
    if raw:
        urls = [part.strip() for part in re.split(r"[;\n]+", raw) if part.strip()]
        if urls:
            return urls
    return WINDOWS_PYTHON_INSTALLER_URLS


def ps_pip_fallback_indexes() -> str:
    rows = []
    for label, url in FALLBACK_PIP_INDEXES:
        host = urllib.parse.urlparse(url).hostname or ""
        rows.append("@(" + ", ".join(ps_quote(part) for part in (label, url, host)) + ")")
    return "@(" + ", ".join(rows) + ")"


def ps_installer_pip_source_map() -> str:
    rows = []
    for installer_host, index_url, trusted_hosts in WINDOWS_INSTALLER_PIP_SOURCE_MAP:
        rows.append("@(" + ", ".join(ps_quote(part) for part in (installer_host, index_url, trusted_hosts)) + ")")
    return "@(" + ", ".join(rows) + ")"


def windows_python_bootstrap_powershell() -> str:
    base_python_dir = os.path.join(runtime_cache_root(), "python312")
    return f"""
$BasePython = $null
$BasePythonDir = {ps_quote(base_python_dir)}
$PythonInstallerUrls = {ps_array(windows_python_installer_urls())}
$PythonInstallerPipSourceMap = {ps_installer_pip_source_map()}

function Test-AgentQtPython($Path) {{
    if (-not $Path -or -not (Test-Path -LiteralPath $Path -PathType Leaf)) {{ return $false }}
    & $Path -c "import sys; raise SystemExit(sys.version_info < (3, 9))" *> $null
    return $LASTEXITCODE -eq 0
}}

function Use-AgentQtPython($Path) {{
    if (Test-AgentQtPython $Path) {{
        $script:BasePython = (Resolve-Path -LiteralPath $Path).Path
        return $true
    }}
    return $false
}}

function Set-AgentQtPreferredPipSourceFromInstaller {{
    param([string]$InstallerUrl)
    if (-not $InstallerUrl -or -not $script:PythonInstallerPipSourceMap) {{ return }}
    $installerHost = ''
    try {{ $installerHost = ([Uri]$InstallerUrl).Host.ToLowerInvariant() }} catch {{ return }}
    foreach ($row in $script:PythonInstallerPipSourceMap) {{
        $installerSourceHost = ([string]$row[0]).ToLowerInvariant()
        $indexUrl = [string]$row[1]
        $trustedHosts = [string]$row[2]
        if (-not $installerSourceHost -or -not $indexUrl) {{ continue }}
        if ($installerHost -eq $installerSourceHost) {{
            $args = @('-i', $indexUrl)
            foreach ($trustedHost in ($trustedHosts -split '\\s+')) {{
                if ($trustedHost) {{
                    $args += @('--trusted-host', $trustedHost)
                }}
            }}
            $script:PipIndexArgs = $args
            Write-Host "根据 Python 安装器成功源，优先使用 pip 源: $indexUrl"
            return
        }}
    }}
}}

function Invoke-AgentQtDownloadFile {{
    param(
        [Parameter(Mandatory=$true)][string[]]$Urls,
        [Parameter(Mandatory=$true)][string]$OutFile,
        [int64]$MinimumBytes = 1048576
    )
    $attemptErrors = New-Object System.Collections.Generic.List[string]
    try {{
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        if ([enum]::GetNames([Net.SecurityProtocolType]) -contains 'Tls13') {{
            [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls13
        }}
        [System.Net.WebRequest]::DefaultWebProxy.Credentials = [System.Net.CredentialCache]::DefaultNetworkCredentials
    }} catch {{ }}

    foreach ($url in $Urls) {{
        if (-not $url) {{ continue }}
        Write-Host "下载 Python 安装器: $url"
        Remove-Item -LiteralPath $OutFile -Force -ErrorAction SilentlyContinue
        $downloaded = $false
        $methods = @('Invoke-WebRequest', 'WebClient', 'curl.exe', 'BitsTransfer')
        foreach ($method in $methods) {{
            if ($downloaded) {{ break }}
            try {{
                Write-Host "  尝试 $method..."
                if ($method -eq 'Invoke-WebRequest') {{
                    Invoke-WebRequest -Uri $url -OutFile $OutFile -UseBasicParsing -Headers @{{ 'User-Agent' = 'AgentQt-PythonBootstrap/1.0' }} -TimeoutSec 120
                }} elseif ($method -eq 'WebClient') {{
                    $client = New-Object System.Net.WebClient
                    $client.Headers.Add('User-Agent', 'AgentQt-PythonBootstrap/1.0')
                    $client.DownloadFile($url, $OutFile)
                }} elseif ($method -eq 'curl.exe') {{
                    $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
                    if (-not $curl) {{ continue }}
                    & curl.exe -L --fail --connect-timeout 20 --retry 2 --retry-delay 2 -A 'AgentQt-PythonBootstrap/1.0' -o $OutFile $url
                    if ($LASTEXITCODE -ne 0) {{ throw "curl.exe 退出码: $LASTEXITCODE" }}
                }} else {{
                    $bits = Get-Command Start-BitsTransfer -ErrorAction SilentlyContinue
                    if (-not $bits) {{ continue }}
                    Start-BitsTransfer -Source $url -Destination $OutFile -ErrorAction Stop
                }}
                $item = Get-Item -LiteralPath $OutFile -ErrorAction Stop
                if ($item.Length -lt $MinimumBytes) {{
                    throw "下载文件过小: $($item.Length) bytes"
                }}
                $downloaded = $true
                $script:AgentQtPythonInstallerUrl = $url
                Write-Host "  下载完成: $($item.Length) bytes"
            }} catch {{
                $message = "$method 失败: $($_.Exception.Message)"
                Write-Host "  $message"
                $attemptErrors.Add("$url -> $message") | Out-Null
                Remove-Item -LiteralPath $OutFile -Force -ErrorAction SilentlyContinue
            }}
        }}
        if ($downloaded) {{ return }}
    }}
    $details = [string]::Join("`n", $attemptErrors.ToArray())
    throw "无法下载 Python 安装器。已尝试官方源和国内镜像。详情:`n$details"
}}

$candidatePaths = @(
    (Join-Path $BasePythonDir 'python.exe'),
    (Join-Path (Join-Path $VenvDir '..') 'python312\\python.exe'),
    (Join-Path $env:LOCALAPPDATA 'Programs\\Python\\Python312\\python.exe')
)
foreach ($candidate in $candidatePaths) {{
    if (Use-AgentQtPython $candidate) {{ break }}
}}

if (-not $BasePython) {{
    $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($pyLauncher) {{
        $candidate = (& py -3 -c "import sys; print(sys.executable if sys.version_info >= (3, 9) else '')" 2>$null).Trim()
        if ($candidate) {{ Use-AgentQtPython $candidate | Out-Null }}
    }}
}}

if (-not $BasePython) {{
    $pythonOnPath = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonOnPath) {{
        $candidate = (& python -c "import sys; print(sys.executable if sys.version_info >= (3, 9) else '')" 2>$null).Trim()
        if ($candidate) {{ Use-AgentQtPython $candidate | Out-Null }}
    }}
}}

if (-not $BasePython) {{
    Write-Host "未找到 Python 3.9+，尝试通过 winget 安装用户级 Python 3.12..."
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($winget) {{
        & winget install --id Python.Python.3.12 -e --scope user --silent --accept-package-agreements --accept-source-agreements
        $candidate = (& py -3 -c "import sys; print(sys.executable if sys.version_info >= (3, 9) else '')" 2>$null).Trim()
        if ($candidate) {{ Use-AgentQtPython $candidate | Out-Null }}
        if (-not $BasePython) {{
            Use-AgentQtPython (Join-Path $env:LOCALAPPDATA 'Programs\\Python\\Python312\\python.exe') | Out-Null
        }}
    }}
}}

if (-not $BasePython) {{
    Write-Host "winget 不可用或安装后仍未定位到 Python，安装独立 Python 到缓存目录..."
    New-Item -ItemType Directory -Force -Path $BasePythonDir | Out-Null
    $installer = Join-Path $env:TEMP ("agent-qt-python-3.12-" + [guid]::NewGuid().ToString("N") + ".exe")
    try {{
        Invoke-AgentQtDownloadFile -Urls $PythonInstallerUrls -OutFile $installer
        Set-AgentQtPreferredPipSourceFromInstaller -InstallerUrl $script:AgentQtPythonInstallerUrl
        & $installer /quiet InstallAllUsers=0 TargetDir="$BasePythonDir" PrependPath=0 Include_pip=1 Include_launcher=0 Include_test=0
        $installExit = $LASTEXITCODE
        if ($installExit -ne 0 -and $installExit -ne 3010) {{
            throw "Python 安装器失败，退出码: $installExit"
        }}
    }} finally {{
        Remove-Item -LiteralPath $installer -Force -ErrorAction SilentlyContinue
    }}
    Use-AgentQtPython (Join-Path $BasePythonDir 'python.exe') | Out-Null
}}

if (-not $BasePython) {{
    throw "无法安装或定位 Python 3.9+。"
}}
"""

def windows_pip_helpers_powershell() -> str:
    return """
function Set-AgentQtPipDefaultSource {
    param(
        [Parameter(Mandatory=$true)][string]$PythonBin,
        [Parameter(Mandatory=$true)][string]$IndexUrl,
        [string]$TrustedHost = ''
    )
    if (-not $IndexUrl) { return }
    Write-Host "记录当前 Python 环境默认 pip 源: $IndexUrl"
    & $PythonBin -m pip config --site set global.index-url $IndexUrl *> $null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "写入 pip index-url 配置失败，继续安装流程。"
        return
    }
    if ($TrustedHost) {
        & $PythonBin -m pip config --site set global.trusted-host $TrustedHost *> $null
        if ($LASTEXITCODE -ne 0) {
            Write-Host "写入 pip trusted-host 配置失败，继续安装流程。"
        }
    }
}

function Invoke-AgentQtPipInstall {
    param(
        [Parameter(Mandatory=$true)][string]$PythonBin,
        [Parameter(Mandatory=$true)][string[]]$Arguments,
        [switch]$Optional
    )
    $lastExit = 1
    if ($script:PipIndexArgs -and $script:PipIndexArgs.Count -gt 0) {
        Write-Host "尝试使用配置的 PyPI 镜像..."
        & $PythonBin -m pip install @script:PipIndexArgs @Arguments
        $lastExit = $LASTEXITCODE
        if ($lastExit -eq 0) {
            $configuredIndex = ''
            $configuredHost = ''
            for ($i = 0; $i -lt $script:PipIndexArgs.Count; $i++) {
                if ($script:PipIndexArgs[$i] -in @('-i', '--index-url') -and $i + 1 -lt $script:PipIndexArgs.Count) {
                    $configuredIndex = [string]$script:PipIndexArgs[$i + 1]
                }
                if ($script:PipIndexArgs[$i] -eq '--trusted-host' -and $i + 1 -lt $script:PipIndexArgs.Count) {
                    $configuredHost = [string]$script:PipIndexArgs[$i + 1]
                }
            }
            Set-AgentQtPipDefaultSource -PythonBin $PythonBin -IndexUrl $configuredIndex -TrustedHost $configuredHost
            return
        }
        Write-Host "配置的 PyPI 镜像失败，尝试国内备用镜像..."
    }
    if ($script:PipFallbackIndexes -and $script:PipFallbackIndexes.Count -gt 0) {
        foreach ($fallback in $script:PipFallbackIndexes) {
            $label = [string]$fallback[0]
            $indexUrl = [string]$fallback[1]
            $trustedHost = [string]$fallback[2]
            if (-not $indexUrl) { continue }
            Write-Host "尝试 $label..."
            if ($trustedHost) {
                & $PythonBin -m pip install --isolated --index-url $indexUrl --trusted-host $trustedHost @Arguments
            } else {
                & $PythonBin -m pip install --isolated --index-url $indexUrl @Arguments
            }
            $lastExit = $LASTEXITCODE
            if ($lastExit -eq 0) {
                Set-AgentQtPipDefaultSource -PythonBin $PythonBin -IndexUrl $indexUrl -TrustedHost $trustedHost
                return
            }
        }
        Write-Host "国内备用 PyPI 镜像失败，回退官方 PyPI..."
    }
    & $PythonBin -m pip install --isolated --index-url __AGENT_QT_OFFICIAL_PIP_INDEX__ --trusted-host pypi.org --trusted-host files.pythonhosted.org @Arguments
    $lastExit = $LASTEXITCODE
    if ($lastExit -eq 0) {
        Set-AgentQtPipDefaultSource -PythonBin $PythonBin -IndexUrl __AGENT_QT_OFFICIAL_PIP_INDEX__ -TrustedHost 'pypi.org files.pythonhosted.org'
        return
    }
    if ($Optional) {
        Write-Host "pip 可选升级失败，检查现有 pip 是否可用..."
        & $PythonBin -m pip --version
        if ($LASTEXITCODE -eq 0) {
            Write-Host "现有 pip 可用，继续安装流程。"
            return
        }
    }
    exit $lastExit
}
""".replace("__AGENT_QT_OFFICIAL_PIP_INDEX__", ps_quote(OFFICIAL_PIP_INDEX_URL))

def build_python_runtime_install_command() -> str:
    pip_args_posix = posix_join(pip_index_args())
    venv_dir = runtime_venv_root()
    python_bin = runtime_python_in_root(venv_dir)
    if platform.system() == "Windows":
        return POWERSHELL_COMMAND_PREFIX + f"""$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = '1'
$env:PYTHONUNBUFFERED = '1'
$env:PIP_DISABLE_PIP_VERSION_CHECK = '1'
$VenvDir = {ps_quote(venv_dir)}
$PythonBin = {ps_quote(python_bin)}
$PipIndexArgs = {ps_array(pip_index_args())}
$PipFallbackIndexes = {ps_pip_fallback_indexes()}

{windows_pip_helpers_powershell()}

Write-Host "[Agent Qt] 创建/修复 Agent Python 环境"
Write-Host "缓存目录: {runtime_cache_root()}"
New-Item -ItemType Directory -Force -Path $VenvDir | Out-Null
if (-not (Test-Path -LiteralPath $PythonBin -PathType Leaf)) {{
    Write-Host "创建虚拟环境..."
    {windows_python_bootstrap_powershell()}
    & $BasePython -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) {{ exit $LASTEXITCODE }}
}}

Write-Host ""
Write-Host "升级 pip"
Invoke-AgentQtPipInstall -PythonBin $PythonBin -Arguments @('--upgrade', 'pip') -Optional

Write-Host ""
Write-Host "验证 Python..."
& $PythonBin -c "import sys; print('Python OK:', sys.executable)"
if ($LASTEXITCODE -ne 0) {{ exit $LASTEXITCODE }}

Write-Host ""
Write-Host "Python 运行环境安装完成: $PythonBin"
Write-Host "后续具体任务需要的库会由 Agent 按需 pip install 到这个环境。"
"""
    return f"""set -e
export PYTHONUTF8=1
export PYTHONUNBUFFERED=1
export PIP_DISABLE_PIP_VERSION_CHECK=1
VENV_DIR={shlex.quote(venv_dir)}
PYTHON_BIN={shlex.quote(python_bin)}
echo "[Agent Qt] 创建/修复 Agent Python 环境"
echo "缓存目录: {runtime_cache_root()}"
mkdir -p "$VENV_DIR"
if [ ! -x "$PYTHON_BIN" ]; then
  echo "创建虚拟环境..."
  if command -v python3 >/dev/null 2>&1; then
    BASE_PYTHON="$(command -v python3)"
  elif command -v python >/dev/null 2>&1; then
    BASE_PYTHON="$(command -v python)"
  else
    echo "未找到 python3/python，无法创建虚拟环境。"
    exit 1
  fi
  "$BASE_PYTHON" -m venv "$VENV_DIR"
fi
echo
echo "升级 pip"
"$PYTHON_BIN" -m pip install --upgrade pip {pip_args_posix}
echo
echo "验证 Python..."
"$PYTHON_BIN" -c "import sys; print('Python OK:', sys.executable)"
echo
echo "Python 运行环境安装完成: $PYTHON_BIN"
echo "后续具体任务需要的库会由 Agent 按需 pip install 到这个环境。"
"""


def run_runtime_command(command: List[str], cwd: str, timeout: int = 900) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            env=agent_runtime_env(create=False),
            capture_output=True,
            text=True,
            timeout=timeout,
            **subprocess_no_window_kwargs(),
        )
    except Exception as exc:
        return False, str(exc)
    output = "\n".join(part for part in (result.stdout.strip(), result.stderr.strip()) if part)
    return result.returncode == 0, output


def installed_runtime_modules_status() -> Dict[str, object]:
    if not agent_runtime_enabled():
        return {
            "ready": False,
            "python": "",
            "missing": [],
            "message": "Agent Python 运行环境已关闭，当前 bash 默认使用系统 PATH。",
        }
    python_bin = ensure_agent_runtime(create=False)
    if not python_bin:
        return {
            "ready": False,
            "python": "",
            "missing": [],
            "message": "未安装 Agent Python 运行环境，当前 bash 默认使用系统 PATH。",
        }
    return {
        "ready": True,
        "python": python_bin,
        "missing": [],
        "message": "Agent Python 环境可用。具体任务需要的库会由 Agent 按需安装到该环境。",
    }


def agent_runtime_status_text() -> str:
    status = installed_runtime_modules_status()
    enabled = agent_runtime_enabled()
    python_bin = str(status.get("python") or "")
    if enabled and python_bin:
        effective = "Agent 缓存 Python"
    elif enabled:
        effective = "系统 PATH（Agent Python 尚未创建）"
    else:
        effective = "系统 PATH（开关关闭）"
    lines = [
        f"开关状态: {'已开启' if enabled else '已关闭'}",
        f"当前生效: {effective}",
        f"缓存目录: {runtime_cache_root()}",
        f"Python: {python_bin or '未安装'}",
        f"pip 源: {os.environ.get('AGENT_QT_PIP_INDEX_URL', DEFAULT_PIP_INDEX_URL)}",
    ]
    if status.get("missing"):
        lines.append("缺失模块: " + ", ".join(str(x) for x in status["missing"]))
    if status.get("message"):
        lines.append(str(status["message"])[-800:])
    return "\n".join(lines)


def install_agent_python_runtime(status_callback=None) -> str:
    os.makedirs(runtime_cache_root(), exist_ok=True)
    set_agent_runtime_enabled(True)
    try:
        python_bin = ensure_agent_runtime(create=True)
        if not python_bin:
            raise RuntimeError(_AGENT_RUNTIME_ERROR or "无法创建 Agent Python 运行环境。")
        commands = [
            [python_bin, "-m", "pip", "install", "--upgrade", "pip", *pip_index_args()],
        ]
        for command in commands:
            if status_callback:
                status_callback("运行: " + " ".join(command[1:]))
            ok, output = run_runtime_command(command, runtime_cache_root(), timeout=1200)
            if not ok:
                raise RuntimeError((output or f"命令失败: {' '.join(command)}")[-3000:])
        ensure_runtime_shims(python_bin)
        return python_bin
    except Exception:
        set_agent_runtime_enabled(False)
        raise

# ============================================================
# 系统提示词
# ============================================================
SYSTEM_PROMPT = """你是本地 Agent 执行引擎的 AI 助手。
## 历史消息与最新消息权衡
- 如果用户最新的消息与之前正在处理的事情不同，那么优先转为新的任务，处理用户最新的对话需求，除非用户主动提到历史任务，否则不用再主动继续历史任务。

## 输出协议
- 自然问答或展示代码时，正常使用 Markdown fenced 代码块即可。
- 需要本地执行时，回复里必须包含一个 Markdown fenced `{command_block_lang}` 终端命令块；命令块前后可以保留必要的简短说明、计划或总结。
- 终端命令只写当前平台命令，不写 JSON/tool_calls；需要写文件占位符时可以继续提供后续文件内容 fenced 代码块。{command_rules}
- 命令块内只能写真实要执行的 shell 代码，或 Agent Qt 终端扩展指令；不要把执行结果、文件变更摘要、结论、`AGENT_DONE` 或任何聊天正文写进命令块。
- 【占位符协议】：替换符只用于“命令块写文件”：命令块用 `<!-- Lang block N -->` 等带编号替换符占位；同一回复里的第 N 个同语言 fenced 代码块提供要写入的完整文件内容。不要把替换符当作待办、摘要、计划、说明或普通正文输出；命令块未引用的替换符没有意义。
- 替换符语言必须和后续文件内容代码块语言一致；写 HTML 就用 `<!-- HTML block 1 -->` 并提供 ```html 代码块，写 SVG 就用 `<!-- SVG block 1 -->` 并提供 ```svg 代码块。不要用 `Game block`、`File block` 这类泛化名称。
- 占位符正例：命令块只放 shell 和占位符，真实文件内容放在后续同编号同语言 fenced 代码块里：
  ```bash
  cat > /Users/pippo/Desktop/my-project/hello.py <<'PYEOF'
  <!-- Python block 1 -->
  PYEOF
  python /Users/pippo/Desktop/my-project/hello.py
  ```
  ```python
  # <desc 打印问候>
  print("hello from file")
  ```
  说明：`<!-- Python block 1 -->` 不会写进文件；真正写入的是后面的第 1 个 `python` 代码块内容。
- 命令块保持短小；不要在命令块内直接嵌入超过 10 行的文件正文，如果要写入超过 10 行的文件内容时，必须拆分为终端指令里使用占位符协议 + 后续md格式的fenced 代码块。
- 代码块首行可用本语言注释写摘要，供界面折叠展示：如 `# <desc 写入配置>`、`// <desc 前端逻辑>`、`/* <desc 样式> */`、`<!-- <desc SVG 图像> -->`；没有也可以，界面会自动截断首行生成摘要。
- 不要在非写文件场景使用替换符；不要把替换符写进文件内容代码块；输出了替换符就必须在同一回复提供对应 fenced 文件内容代码块。
- 工作区根目录：{project_root}。创建/修改用户项目文件时只能写到这里；不要写到 Agent Qt 缓存目录。
- 先建目录再写文件；项目中脚本文件调用以及终端中文件生成与访问使用绝对路径，代码内可以使用相对路径引用同级文件或子级文件。
- 常驻命令会自动进入后台终端，不要加 `&`/`nohup`，不要自己写 pid 文件。启动常驻命令后本轮结束，下一轮从执行结果里的 `Terminal processes:` 摘要获取 pid 再查询控制台输出。
- 不输出备用方案；自己选择一个最高把握路径。
- 自动化循环完成时只回复 `{done_marker}` 加最终总结；总结必须面向用户，至少交代：已完成了什么、当前结论是什么、若未完全完成还差什么/下一步建议。不要只输出空泛一句话。
- 未完成时，如果需要本地执行，继续给下一轮完整命令块；但在命令块之外，必须先用 1 到 3 句简短正文说明：当前整体判断、本轮准备做什么、这个命令块的作用。永远不要只输出命令块而没有任何说明。
- 若当前启用了深度思考/推理模式，也必须把已经得到的高价值判断、排查思路和本轮策略精炼写进可见正文；不要把关键信息只留在隐藏思考里。
- 输出命令块时，命令块里的动作尚未执行；不要在同一回复里声称这些动作“已生成/已写入/已验证/已发送”。执行后会有下一轮结果，再基于结果下结论。
- 微信远控发送文件使用终端扩展指令：在命令块里写 `wx send_file 文件路径1,文件路径2,...`。这只是请求 Agent Qt 发送附件，不代表已经发送完成；同一回复不要声称“已发送/已通过 wx send_file 发送”。
- 定时计划使用终端扩展指令：`schedule create JSON`、`schedule list`、`schedule delete 名称或序号`、`schedule update JSON`。计划 JSON 使用 `{{"title":"短标题","prompt":"到点后真正要做的事","trigger":{{"run_at":"YYYY-MM-DD HH:MM:SS","repeat_every_seconds":86400,"until_at":"YYYY-MM-DD HH:MM:SS"}}}}`。
- 如果任务本质上是搜索或调研，而不是操作本地工作区，请优先使用终端扩展指令 `web research 搜索话题`。程序会直接执行本地网页搜索，并把结果写回执行结果与上下文；不要为了搜索或调研而生成本地 curl/wget/python 抓取脚本，除非用户明确要求脚本，或目标数据只存在当前工作区/本机文件里。
- `skill list` 是 Agent Qt 的内置终端扩展指令，用于查看当前工作区已有技能列表。skill 是一种经验、SOP、方法论的封装，至少包含一个 `SKILL.md`，目录里还可能有补充的 Markdown、脚本、图像等材料，可按需继续读取。
- 如果用户主动提到 `skill`/技能/技巧，或询问“你有什么技能”“有哪些 skill”“介绍一下技能”“当前可用技能是什么”等与技能列表相关的问题，优先使用终端扩展指令 `skill list` 查看当前工作区已有技能列表；不要先主观回答“没有这种内置指令”或“没有加载任何技能包”。在拿到列表后，再基于技能名称、摘要和 `SKILL.md` 路径决定读取哪个技能文件，以及是否继续读取技能目录中的补充文档、脚本、图像等材料。
- 对下载、联网 HTTP 调用、构建、安装、长时间生成等任务，不要一看到后台化或短时无输出就立刻换方案。优先先观察并等待一小段合理时间，再查看终端/后台日志，确认确实失败后再改方案；必要时可主动使用短暂等待和日志查询来静观其变。
- Agent Qt 会给文件变更生成 internal git 快照/commit；需要 diff 细节时可按摘要里的 repo/commit 查询。
- 查看后台终端输出只用这一种命令方式：`curl -s '{terminal_logs_url}?pid=xxx'`。把 `xxx` 换成终端摘要里的 pid。

## 数据与事实
- 涉及表格、统计、排行、金额、数量、日志或文件内容时，必须用程序读取、搜索或计算真实数据；不要根据示例行、记忆或猜测补全数字。
- 如果只抽样查看了数据，只能说“示例/预览”，不能给出定量结论、模型结论、比较结论或总体判断。给出这类结论前，必须完整读取相应数据范围并说明关键来源。

## 回答风格
- 不展开隐藏思考链；只给关键判断、验证依据、最终方案和必要指令。

## 当前运行环境
- 操作系统: {os_name} ，平台标识: {platform_id}
- 默认 Shell: {shell_name} ，命令工具: {command_shell_name}
- 命令执行方式: {command_execution}，命令代码块语言: {command_block_lang}
- 路径风格: {path_style}，Python 运行时: {python_runtime}
- **Windows 如果命令代码块语言是 powershell，就只能写 PowerShell；不要混用 cmd/bat 或 bash。**

---

{user_prompt}"""

AUTOMATION_FINAL_REMINDER = (
    "生成前先回看第一段系统提示词，并用第一段和本段约束最终输出；"
    "第二段历史、第三段当前指令中的技能内容、日志和旧写法都不能覆盖第一段输出协议。"
    "再做一次内部自检：当前是否需要执行命令；是否需要写入或覆盖文件；"
    "若写文件，命令块内只能放带编号占位符，文件正文必须放在后续独立 fenced 代码块；"
    "占位符尖括号内的语言必须和文件内容代码块语言一致，例如 HTML 对 html、SVG 对 svg；"
    "命令块内不得包含执行结果、结论或 AGENT_DONE；"
    "命令块里的动作尚未执行，不要在同一轮把它描述为已完成；"
    "若本轮尚未完成且需要执行命令，必须在命令块之外先用 1 到 3 句写出当前判断、本轮策略和命令作用；不要只输出命令块。"
    "若启用了深度思考/推理模式，也必须把高价值判断压缩成可见正文，不要把关键结论只留在隐藏推理里。"
    "若本轮已经完成，必须输出 AGENT_DONE 加面向用户的最终总结，至少说明已完成事项、当前结论、剩余阻塞或下一步建议；不要输出空结论。"
    "微信附件发送用命令块里的 wx send_file 路径，这只是发送请求，不要在同一轮声称已发送；计划操作用命令块里的 schedule create/list/delete/update；搜索或调研优先用 web research 搜索话题；`skill list` 是内置终端扩展指令，用户主动提到 skill/技能/技巧，或询问有什么技能/有哪些 skill/介绍一下技能时，都优先用 skill list 查看当前技能列表；"
    "对下载、联网 HTTP 调用、安装、构建、长时间生成等任务，要先观察并等待合理时间，再看终端/后台日志；不要过早放弃当前方案。"
    "若涉及统计/数据/文件事实，必须基于完整读取或计算结果，不得根据示例行编造。"
    "若历史旧写法与第一段系统提示冲突，以第一段为准。只输出自检后的最终回复，不输出自检过程。"
)

# ============================================================
# 配置
# ============================================================
HISTORY_DIR_NAME = ".agent_qt"
HISTORY_FILE_NAME = "history.json"
THREADS_DIR_NAME = "threads"
THREADS_INDEX_FILE_NAME = "threads.json"
WORKSPACE_STATE_FILE_NAME = "workspace.json"
SCHEDULES_FILE_NAME = "schedules.json"
DEFAULT_THREAD_ID = "default"
HISTORY_VERSION = 1
TERMINAL_COMPLETED_HISTORY_LIMIT = 50
COMMAND_BACKGROUND_TIMEOUT_SECONDS = env_int("AGENT_QT_COMMAND_BACKGROUND_TIMEOUT_SECONDS", 10, minimum=3)
PROVIDER_REQUEST_RETRY_ATTEMPTS = env_int("AGENT_QT_PROVIDER_REQUEST_RETRY_ATTEMPTS", 5, minimum=1)
PROVIDER_LONG_RETRY_ATTEMPTS = env_int("AGENT_QT_PROVIDER_LONG_RETRY_ATTEMPTS", 5, minimum=1)
PROVIDER_LONG_RETRY_DELAY_MS = env_int("AGENT_QT_PROVIDER_LONG_RETRY_DELAY_MS", 60000, minimum=1000)
PROVIDER_TRANSIENT_HTTP_CODES = {408, 409, 425, 429, 500, 502, 503, 504}
SCHEDULE_MISSED_GRACE_SECONDS = env_int("AGENT_QT_SCHEDULE_MISSED_GRACE_SECONDS", 300, minimum=30)
SCHEDULE_ACTIVE_RUN_TIMEOUT_SECONDS = env_int("AGENT_QT_SCHEDULE_ACTIVE_RUN_TIMEOUT_SECONDS", 900, minimum=60)

FORBIDDEN = [
    "rm -rf /", "sudo rm", "sudo reboot", "shutdown",
    "mkfs", "dd if=", ":(){ :|:& };:",
]
POWERSHELL_COMMAND_PREFIX = "__AGENT_QT_POWERSHELL__\n"

LIGHT_COLORS = {
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

DARK_COLORS = {
    "bg": "#10141d",
    "bg_top": "#151a25",
    "surface": "#171d29",
    "surface_alt": "#202838",
    "sidebar_bg": "#121824",
    "card_user": "#16263a",
    "card_ai": "#221d34",
    "card_system": "#182231",
    "accent": "#7c6dff",
    "accent_dark": "#a99fff",
    "accent_light": "#2d2944",
    "accent_2": "#22d3ee",
    "text": "#ecf2ff",
    "text_secondary": "#a9b5cc",
    "muted": "#748098",
    "border": "#30394c",
    "border_strong": "#46536a",
    "code_bg": "#111827",
    "success": "#2dd48f",
    "success_soft": "#123426",
    "danger": "#ff6b72",
    "danger_soft": "#3b1f25",
    "input_bg": "#111827",
    "terminal_bg": "#10141d",
    "terminal_panel": "#121824",
    "terminal_card": "#111827",
    "terminal_text": "#ecf2ff",
    "terminal_muted": "#a9b5cc",
    "terminal_accent": "#60a5fa",
}

COLORS = dict(DARK_COLORS if app_theme_setting() == "dark" else LIGHT_COLORS)


def apply_theme_palette(theme: str):
    COLORS.clear()
    COLORS.update(DARK_COLORS if str(theme).strip().lower() == "dark" else LIGHT_COLORS)


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


def compact_popup_menu_style() -> str:
    is_windows = platform.system() == "Windows"
    dark = app_theme_setting() == "dark"
    menu_bg = COLORS["surface"] if is_windows else ("rgba(23, 29, 41, 242)" if dark else "rgba(238, 243, 252, 238)")
    selected_bg = "rgba(95, 148, 255, 110)" if dark else "rgba(190, 222, 255, 225)"
    checked_bg = "rgba(95, 148, 255, 138)" if dark else "rgba(176, 214, 255, 235)"
    separator_bg = "rgba(236, 242, 255, 28)" if dark else "rgba(23, 32, 51, 28)"
    menu_radius = 0 if is_windows else 14
    item_radius = 0 if is_windows else 10
    return f"""
        QMenu {{
            background: {menu_bg};
            color: {COLORS['text']};
            border: none;
            border-radius: {menu_radius}px;
            padding: 6px;
            font-size: 12px;
            font-weight: 700;
        }}
        QMenu::item {{
            background: transparent;
            color: {COLORS['text']};
            border: none;
            border-radius: {item_radius}px;
            padding: 7px 28px 7px 12px;
            min-height: 18px;
        }}
        QMenu::item:selected {{
            background: {selected_bg};
            color: {COLORS['text']};
        }}
        QMenu::item:checked {{
            background: {checked_bg};
            color: {COLORS['text']};
        }}
        QMenu::item:disabled {{
            color: {COLORS['muted']};
            background: transparent;
        }}
        QMenu::separator {{
            height: 1px;
            background: {separator_bg};
            margin: 5px 8px;
        }}
        QMenu::indicator {{
            width: 0;
            height: 0;
        }}
        QMenu::right-arrow {{
            width: 7px;
            height: 7px;
            padding-right: 10px;
        }}
    """


def style_compact_popup_menu(menu: QMenu) -> QMenu:
    menu.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, platform.system() != "Windows")
    if hasattr(Qt.WindowType, "NoDropShadowWindowHint"):
        menu.setWindowFlag(Qt.WindowType.NoDropShadowWindowHint, True)
    menu.setStyleSheet(compact_popup_menu_style())
    return menu


def style_skill_popup_menu(menu: QMenu) -> QMenu:
    is_windows = platform.system() == "Windows"
    menu.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, not is_windows)
    if hasattr(Qt.WindowType, "NoDropShadowWindowHint"):
        menu.setWindowFlag(Qt.WindowType.NoDropShadowWindowHint, True)
    dark = app_theme_setting() == "dark"
    menu_bg = COLORS["surface"] if is_windows else ("rgba(23, 29, 41, 242)" if dark else "rgba(238, 243, 252, 238)")
    selected_bg = "rgba(124, 109, 255, 70)" if dark else "rgba(190, 222, 255, 225)"
    separator_bg = "rgba(236, 242, 255, 24)" if dark else "rgba(23, 32, 51, 24)"
    menu_radius = 0 if is_windows else 14
    item_radius = 0 if is_windows else 9
    menu.setStyleSheet(f"""
        QMenu {{
            background: {menu_bg};
            color: {COLORS['text']};
            border: none;
            border-radius: {menu_radius}px;
            padding: 5px;
            font-size: 11px;
            font-weight: 700;
        }}
        QMenu::item {{
            background: transparent;
            color: {COLORS['text']};
            border: none;
            border-radius: {item_radius}px;
            padding: 5px 18px 5px 10px;
            min-height: 16px;
        }}
        QMenu::item:selected {{
            background: {selected_bg};
            color: {COLORS['text']};
        }}
        QMenu::item:checked {{
            background: transparent;
            color: {COLORS['text']};
        }}
        QMenu::item:disabled {{
            color: {COLORS['muted']};
            background: transparent;
        }}
        QMenu::separator {{
            height: 1px;
            background: {separator_bg};
            margin: 3px 7px;
        }}
        QMenu::indicator {{
            width: 0;
            height: 0;
        }}
    """)
    return menu

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
        QScrollBar:horizontal {{
            background: transparent;
            height: 8px;
            margin: 0 6px 2px 6px;
        }}
        QScrollBar::handle:horizontal {{
            background: {COLORS['border_strong']};
            border-radius: 4px;
            min-width: 30px;
        }}
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
            width: 0;
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

def estimate_wrapped_text_height(text: str, metrics, available_width: int, max_visual_lines: Optional[int] = None) -> int:
    visual_lines = 0
    avg_char_width = max(1, metrics.averageCharWidth())
    for line in (text or " ").splitlines() or [""]:
        expanded = line.replace("\t", "    ")
        if len(expanded) > 320:
            line_width = avg_char_width * len(expanded)
        else:
            line_width = metrics.horizontalAdvance(expanded)
        visual_lines += max(1, (line_width + available_width - 1) // available_width)
        if max_visual_lines is not None and visual_lines >= max_visual_lines:
            visual_lines = max_visual_lines
            break
    return visual_lines * max(1, metrics.lineSpacing()) + 36

PIPE_TABLE_SEPARATOR_RE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$")

def is_pipe_table_row(line: str) -> bool:
    stripped = (line or "").strip()
    return "|" in stripped and stripped.count("|") >= (2 if stripped.startswith("|") or stripped.endswith("|") else 1)

def is_pipe_table_separator(line: str) -> bool:
    return PIPE_TABLE_SEPARATOR_RE.match(line or "") is not None

def split_pipe_table_cells(line: str) -> List[str]:
    stripped = (line or "").strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [cell.strip() for cell in stripped.split("|")]

def looks_like_pipe_table(lines: List[str], start: int) -> bool:
    if start + 1 >= len(lines):
        return False
    return is_pipe_table_row(lines[start]) and is_pipe_table_separator(lines[start + 1])

def render_markdown_inline_html(text: str) -> str:
    doc = QTextDocument()
    doc.setMarkdown(text or "")
    body = doc.toHtml()
    match = re.search(r"<body[^>]*>(.*?)</body>", body, re.S | re.I)
    return match.group(1).strip() if match else body

def markdown_table_to_html(table_lines: List[str]) -> str:
    if len(table_lines) < 2:
        return render_markdown_inline_html("\n".join(table_lines))
    header_cells = split_pipe_table_cells(table_lines[0])
    aligns: List[str] = []
    for cell in split_pipe_table_cells(table_lines[1]):
        raw = cell.replace(" ", "")
        if raw.startswith(":") and raw.endswith(":"):
            aligns.append("center")
        elif raw.endswith(":"):
            aligns.append("right")
        else:
            aligns.append("left")
    body_rows = [split_pipe_table_cells(line) for line in table_lines[2:] if is_pipe_table_row(line)]
    column_count = max([len(header_cells), len(aligns), *(len(row) for row in body_rows)] or [0])
    if column_count <= 0:
        return ""

    def padded(cells: List[str]) -> List[str]:
        return cells + [""] * max(0, column_count - len(cells))

    table_style = (
        "border-collapse:collapse; margin:8px 0 10px 0; width:100%; "
        f"color:{COLORS['text']};"
    )
    header_style = (
        f"border:1px solid {COLORS['border_strong']}; padding:6px 8px; "
        f"background:{COLORS['surface_alt']}; font-weight:700;"
    )
    cell_style = (
        f"border:1px solid {COLORS['border_strong']}; padding:6px 8px; "
        "background:#ffffff;"
    )
    parts = [
        f"<table cellspacing='0' cellpadding='0' style='{table_style}'>",
        "<thead><tr>",
    ]
    for index, cell in enumerate(padded(header_cells)[:column_count]):
        align = aligns[index] if index < len(aligns) else "left"
        parts.append(f"<th align='{align}' style='{header_style}'>{render_markdown_inline_html(cell)}</th>")
    parts.append("</tr></thead>")
    if body_rows:
        parts.append("<tbody>")
        for row in body_rows:
            parts.append("<tr>")
            for index, cell in enumerate(padded(row)[:column_count]):
                align = aligns[index] if index < len(aligns) else "left"
                parts.append(f"<td align='{align}' style='{cell_style}'>{render_markdown_inline_html(cell)}</td>")
            parts.append("</tr>")
        parts.append("</tbody>")
    parts.append("</table>")
    return "".join(parts)

def markdown_with_pipe_tables_to_html(markdown_text: str) -> str:
    lines = (markdown_text or "").splitlines()
    if not any(looks_like_pipe_table(lines, index) for index in range(max(0, len(lines) - 1))):
        return render_markdown_inline_html(markdown_text)

    parts: List[str] = []
    buffer: List[str] = []
    index = 0

    def flush_buffer():
        nonlocal buffer
        if buffer:
            parts.append(render_markdown_inline_html("\n".join(buffer)))
            buffer = []

    while index < len(lines):
        if looks_like_pipe_table(lines, index):
            flush_buffer()
            table_lines = [lines[index], lines[index + 1]]
            index += 2
            while index < len(lines) and is_pipe_table_row(lines[index]):
                table_lines.append(lines[index])
                index += 1
            parts.append(markdown_table_to_html(table_lines))
            continue
        buffer.append(lines[index])
        index += 1
    flush_buffer()
    return "\n".join(part for part in parts if part)

DESC_MARKER_RE = re.compile(
    r"^\s*(?:#|//|--|;|<!--|/\*+)\s*<desc\s+(?P<desc>.+?)>\s*(?:-->|-?\*/)?\s*$",
    re.I,
)

def text_within_chars(text: str, limit: int) -> str:
    value = " ".join(str(text or "").split())
    if len(value) <= limit:
        return value
    return value[: max(1, limit - 1)].rstrip() + "…"

def code_block_summary(lang: str, code: str, limit: int = 92) -> str:
    text = str(code or "")
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        desc_match = DESC_MARKER_RE.match(line)
        if desc_match:
            return text_within_chars(desc_match.group("desc").strip(), limit)
        break
    canonical = canonical_lang(lang or "text")
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if canonical in {"bash", "sh", "shell", "zsh", "powershell", "ps1", "pwsh", "cmd", "bat", "batch"}:
            if line.startswith(("#", "//", "REM ", "rem ")):
                continue
            return text_within_chars(line, limit)
        return text_within_chars(line, limit)
    return "空代码块"

def estimate_markdown_table_extra_height(text: str, metrics, available_width: int) -> int:
    lines = (text or "").splitlines()
    extra_height = 0
    index = 0
    line_spacing = max(1, metrics.lineSpacing())
    while index < len(lines):
        if not looks_like_pipe_table(lines, index):
            index += 1
            continue
        table_lines = [lines[index], lines[index + 1]]
        index += 2
        while index < len(lines) and is_pipe_table_row(lines[index]):
            table_lines.append(lines[index])
            index += 1
        column_count = max(len(split_pipe_table_cells(line)) for line in table_lines)
        column_width = max(80, available_width // max(1, column_count))
        table_height = 12
        for line in table_lines:
            if is_pipe_table_separator(line):
                continue
            row_lines = 1
            for cell in split_pipe_table_cells(line):
                row_lines = max(row_lines, max(1, (metrics.horizontalAdvance(cell) + column_width - 1) // column_width))
            table_height += max(28, row_lines * line_spacing + 14)
        plain_table_height = max(1, len(table_lines)) * line_spacing
        extra_height += max(0, table_height - plain_table_height)
    return extra_height

# ============================================================
# 设置面板控件
# ============================================================
class ToggleSwitch(QAbstractButton):
    def __init__(self, checked: bool = False, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(50, 30)
        self._offset = 1.0 if checked else 0.0
        self._animation = QPropertyAnimation(self, b"offset", self)
        self._animation.setDuration(150)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.toggled.connect(self._animate_to_state)

    def sizeHint(self) -> QSize:
        return QSize(50, 30)

    def get_offset(self) -> float:
        return self._offset

    def set_offset(self, value: float):
        self._offset = max(0.0, min(1.0, float(value)))
        self.update()

    offset = Property(float, get_offset, set_offset)

    def _animate_to_state(self, checked: bool):
        self._animation.stop()
        self._animation.setStartValue(self._offset)
        self._animation.setEndValue(1.0 if checked else 0.0)
        self._animation.start()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(1, 1, self.width() - 2, self.height() - 2)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#2f9bff" if self.isChecked() else "#d8deea"))
        painter.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)
        knob_size = 24
        knob_x = 3 + self._offset * (self.width() - knob_size - 6)
        painter.setBrush(QColor("#ffffff"))
        painter.drawEllipse(QRectF(knob_x, 3, knob_size, knob_size))


class SettingsToggleRow(QWidget):
    toggled = Signal(bool)

    def __init__(self, title: str, subtitle: str, checked: bool = False, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumWidth(292)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 9, 10, 9)
        layout.setSpacing(12)
        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(2)
        title_label = QLabel(title)
        subtitle_label = QLabel(subtitle)
        self.title_label = title_label
        self.subtitle_label = subtitle_label
        text_layout.addWidget(title_label)
        text_layout.addWidget(subtitle_label)
        layout.addLayout(text_layout, 1)
        self.switch = ToggleSwitch(checked)
        self.switch.toggled.connect(self.toggled.emit)
        layout.addWidget(self.switch, 0, Qt.AlignmentFlag.AlignVCenter)
        self.apply_style()

    def apply_style(self):
        self.setStyleSheet(f"""
            QWidget {{
                background: {COLORS['surface']};
                border-radius: 12px;
            }}
            QWidget:hover {{
                background: {COLORS['surface_alt']};
            }}
            QLabel {{
                background: transparent;
                border: none;
            }}
        """)
        if hasattr(self, "title_label"):
            self.title_label.setStyleSheet(f"color: {COLORS['text']}; font-size: 13px; font-weight: 900;")
        if hasattr(self, "subtitle_label"):
            self.subtitle_label.setStyleSheet(f"color: {COLORS['text_secondary']}; font-size: 11px; font-weight: 700;")
        self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.switch.toggle()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def setChecked(self, checked: bool):
        previous = self.switch.blockSignals(True)
        self.switch.setChecked(checked)
        self.switch.set_offset(1.0 if checked else 0.0)
        self.switch.blockSignals(previous)

    def isChecked(self) -> bool:
        return self.switch.isChecked()


class ClickableFrame(QFrame):
    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

def split_markdown_fenced_blocks(text: str) -> List[Dict[str, str]]:
    parts: List[Dict[str, str]] = []
    lines = (text or "").splitlines(keepends=True)
    buffer: List[str] = []
    code_buffer: List[str] = []
    in_code = False
    code_lang = ""
    fence_char = ""
    fence_len = 0

    def flush_markdown():
        nonlocal buffer
        if buffer:
            parts.append({"type": "markdown", "text": "".join(buffer)})
            buffer = []

    def flush_code():
        nonlocal code_buffer, code_lang, fence_char, fence_len
        parts.append({
            "type": "code",
            "lang": code_lang.strip(),
            "text": "".join(code_buffer).rstrip("\n"),
        })
        code_buffer = []
        code_lang = ""
        fence_char = ""
        fence_len = 0

    def opening_fence(line: str) -> Optional[re.Match]:
        return re.match(r"^\s{0,3}([`~]{3,})([^\r\n]*)\s*$", line.rstrip("\n\r"))

    def is_closing_fence(line: str) -> bool:
        if not fence_char or fence_len <= 0:
            return False
        pattern = rf"^\s{{0,3}}{re.escape(fence_char)}{{{fence_len},}}\s*$"
        return re.match(pattern, line.rstrip("\n\r")) is not None

    index = 0
    while index < len(lines):
        line = lines[index]
        if in_code:
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
            index += 1
            continue
        buffer.append(line)
        index += 1

    if in_code:
        flush_code()
    elif index >= len(lines):
        flush_markdown()
    return [part for part in parts if part.get("text", "").strip()]

def markdown_fenced_code_block_count(text: str) -> int:
    return sum(1 for part in split_markdown_fenced_blocks(text) if part.get("type") == "code")

def summarize_fenced_code_blocks_for_context(text: str) -> str:
    parts = split_markdown_fenced_blocks(str(text or ""))
    if not any(part.get("type") == "code" for part in parts):
        return str(text or "")
    output: List[str] = []
    for part in parts:
        if part.get("type") != "code":
            value = str(part.get("text") or "").strip()
            if value:
                output.append(value)
            continue
        code = str(part.get("text") or "")
        lang = canonical_lang(str(part.get("lang") or "text"))
        line_count = len([line for line in code.splitlines() if line.strip()])
        digest = hashlib.sha1(code.encode("utf-8", errors="ignore")).hexdigest()[:10]
        summary = code_block_summary(lang, code, limit=140)
        summary_part = f"，摘要：{summary}" if summary and summary != "空代码块" else ""
        output.append(f"【已省略久远代码块：{lang or 'text'}，约 {line_count} 行{summary_part}，sha1={digest}；如需细节请根据文件变更摘要、日志路径或工作区文件恢复。】")
    return "\n\n".join(output).strip()

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
    elif kind == "send":
        cx = size / 2
        painter.drawLine(int(cx), size - 4, int(cx), 4)
        painter.drawLine(4, int(cx), int(cx), 4)
        painter.drawLine(size - 4, int(cx), int(cx), 4)
    elif kind == "pause":
        painter.drawLine(int(size * 0.38), 4, int(size * 0.38), size - 4)
        painter.drawLine(int(size * 0.62), 4, int(size * 0.62), size - 4)
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
    scale = 3
    pixmap = QPixmap(size * scale, size * scale)
    pixmap.setDevicePixelRatio(scale)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color), 1.55)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.drawRoundedRect(QRectF(2.5, 3.5, size - 5, size - 7), 2.8, 2.8)
    painter.drawLine(5.9, 6.7, 8.8, 9.25)
    painter.drawLine(8.8, 9.25, 5.9, 11.8)
    painter.drawLine(10.0, 12.0, size - 4.8, 12.0)
    painter.end()
    return QIcon(pixmap)

def close_icon(color: str = "#657089", size: int = 16) -> QIcon:
    scale = 3
    pixmap = QPixmap(size * scale, size * scale)
    pixmap.setDevicePixelRatio(scale)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color), 1.7)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    painter.drawLine(5.3, 5.3, size - 5.3, size - 5.3)
    painter.drawLine(size - 5.3, 5.3, 5.3, size - 5.3)
    painter.end()
    return QIcon(pixmap)

# ============================================================
# 工具函数
# ============================================================
def is_safe(cmd: str) -> bool:
    cmd = strip_shell_command_marker(cmd)
    for bad in FORBIDDEN:
        if bad in cmd:
            return False
    return True

def is_powershell_command(cmd: str) -> bool:
    return str(cmd or "").startswith(POWERSHELL_COMMAND_PREFIX)

def strip_shell_command_marker(cmd: str) -> str:
    if is_powershell_command(cmd):
        return str(cmd)[len(POWERSHELL_COMMAND_PREFIX):]
    return str(cmd or "")

def runtime_environment() -> Dict[str, str]:
    system = platform.system() or sys.platform
    path_style = "Windows paths (C:\\...)" if system == "Windows" else "POSIX paths (/Users/... 或 /home/...)"
    if system == "Windows":
        command_block_lang = "powershell"
        command_shell_name = "PowerShell"
        command_execution = "PowerShell 脚本文件（powershell.exe -NoProfile -ExecutionPolicy Bypass -File 临时 .ps1）"
        command_rules = (
            "Windows 环境必须输出一个 ```powershell 代码块，内容使用 Windows PowerShell 5.1 兼容语法。"
            "不要输出 bash/sh 语法，不要使用 cat <<EOF/heredoc、chmod、rm -rf、export、source、nohup、& 后台符号或 POSIX 路径。"
            "写文件优先使用 New-Item -ItemType Directory -Force、Set-Content -Encoding UTF8、PowerShell here-string；"
            "切换目录用 Set-Location -LiteralPath；路径使用 C:\\... 或 Join-Path。"
        )
    else:
        command_block_lang = "bash"
        command_shell_name = os.environ.get("SHELL") or "/bin/sh"
        command_execution = "Bash/POSIX shell 脚本"
        command_rules = (
            "macOS/Linux 环境必须输出一个 ```bash 代码块，内容使用 POSIX shell/bash 语法。"
            "不要输出 Windows cmd/PowerShell 专用语法。"
        )
    return {
        "os_name": system,
        "platform_id": sys.platform,
        "shell_name": os.environ.get("SHELL") or os.environ.get("COMSPEC") or "unknown",
        "path_style": path_style,
        "python_runtime": agent_runtime_description(),
        "command_block_lang": command_block_lang,
        "command_shell_name": command_shell_name,
        "command_execution": command_execution,
        "command_rules": command_rules,
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
    "powershell": ("powershell", "ps1", "pwsh"),
    "ps1": ("ps1", "powershell", "pwsh"),
    "pwsh": ("pwsh", "powershell", "ps1"),
    "cmd": ("cmd", "bat", "batch"),
    "bat": ("bat", "cmd", "batch"),
    "batch": ("batch", "cmd", "bat"),
    "bash": ("bash", "sh", "shell", "zsh"),
    "sh": ("sh", "bash", "shell", "zsh"),
    "shell": ("shell", "bash", "sh", "zsh"),
    "zsh": ("zsh", "bash", "sh", "shell"),
    "b64": ("b64", "base64"),
    "base64": ("base64", "b64"),
}

HEREDOC_RE = re.compile(r"<<-?\s*(?P<quote>['\"]?)(?P<tag>[A-Za-z_][A-Za-z0-9_]*)\1")
NUMBERED_HTML_PLACEHOLDER_RE = re.compile(r'<!--\s*(?P<lang>\w+)\s+block\s+(?P<index>\d+)\s*-->', re.I)
NUMBERED_PLACEHOLDER_LINE_RE = re.compile(
    r'^\s*(?:'
    r'<!--\s*(?P<html>\w+)\s+block\s+(?P<html_index>\d+)\s*-->'
    r'|/\*\s*(?P<css>\w+)\s+block\s+(?P<css_index>\d+)\s*\*/'
    r'|//\s*(?P<slash>\w+)\s+block\s+(?P<slash_index>\d+)'
    r'|#\s*(?P<hash>\w+)\s+block\s+(?P<hash_index>\d+)'
    r')\s*$',
    re.I,
)

def canonical_lang(lang: str) -> str:
    """统一语言别名，保证 js/javascript 等代码块按同一队列顺序消费。"""
    lang = lang.strip().lower() or 'text'
    for canonical, aliases in LANG_ALIASES.items():
        if lang == canonical or lang in aliases:
            return canonical
    return lang

def scan_all_code_blocks(text: str) -> Dict[str, List[str]]:
    """扫描所有 Markdown 代码块，返回 {lang: [code1, code2, ...]}"""
    blocks: Dict[str, List[str]] = {}
    lines = (text or "").splitlines(keepends=True)
    in_code = False
    fence_char = ""
    fence_len = 0
    lang = ""
    lang_index = 0
    pending_placeholder_lang = ""
    pending_placeholder_index = 0
    code_lines: List[str] = []

    def opening_fence(line: str) -> Optional[re.Match]:
        return re.match(r"^\s{0,3}([`~]{3,})([^\r\n]*)\s*$", line.rstrip("\n\r"))

    def is_closing_fence(line: str) -> bool:
        if not fence_char or fence_len <= 0:
            return False
        pattern = rf"^\s{{0,3}}{re.escape(fence_char)}{{{fence_len},}}\s*$"
        return re.match(pattern, line.rstrip("\n\r")) is not None

    for line in lines:
        if in_code:
            if is_closing_fence(line):
                code = "".join(code_lines)
                key = canonical_lang(lang)
                if lang_index > 0:
                    values = blocks.setdefault(key, [])
                    while len(values) < lang_index:
                        values.append("")
                    values[lang_index - 1] = code.strip()
                else:
                    blocks.setdefault(key, []).append(code.strip())
                in_code = False
                fence_char = ""
                fence_len = 0
                lang = ""
                lang_index = 0
                code_lines = []
            else:
                code_lines.append(line)
            continue
        match = opening_fence(line)
        if not match:
            stripped = line.strip()
            placeholder_match = NUMBERED_HTML_PLACEHOLDER_RE.fullmatch(stripped)
            if placeholder_match:
                pending_placeholder_lang = canonical_lang(placeholder_match.group("lang"))
                pending_placeholder_index = max(1, int(placeholder_match.group("index") or "1"))
            elif stripped:
                pending_placeholder_lang = ""
                pending_placeholder_index = 0
            continue
        fence = match.group(1)
        raw_info = (match.group(2) or "").strip()
        lang = raw_info.split(maxsplit=1)[0] if raw_info else ""
        if not lang and pending_placeholder_lang:
            lang = pending_placeholder_lang
            lang_index = pending_placeholder_index
        else:
            lang_index = 0
        fence_char = fence[0]
        fence_len = len(fence)
        in_code = True
        code_lines = []
        pending_placeholder_lang = ""
        pending_placeholder_index = 0
    if in_code:
        code = "".join(code_lines)
        key = canonical_lang(lang)
        if lang_index > 0:
            values = blocks.setdefault(key, [])
            while len(values) < lang_index:
                values.append("")
            values[lang_index - 1] = code.strip()
        else:
            blocks.setdefault(key, []).append(code.strip())
    return blocks


def strip_single_outer_fenced_block(text: str, preferred_langs: Optional[set[str]] = None) -> str:
    raw = str(text or "")
    parts = split_markdown_fenced_blocks(raw)
    if not parts:
        return raw.strip()
    code_parts = [part for part in parts if part.get("type") == "code"]
    markdown_parts = [part for part in parts if part.get("type") == "markdown" and part.get("text", "").strip()]
    if len(code_parts) != 1 or markdown_parts:
        return raw.strip()
    code_part = code_parts[0]
    lang = (code_part.get("lang") or "").strip().lower()
    if preferred_langs and lang and lang not in preferred_langs:
        return raw.strip()
    return (code_part.get("text") or "").strip()

def placeholder_line_key(match: re.Match) -> str:
    lang = (
        match.group("html")
        or match.group("css")
        or match.group("slash")
        or match.group("hash")
        or ""
    )
    index_text = (
        match.group("html_index")
        or match.group("css_index")
        or match.group("slash_index")
        or match.group("hash_index")
        or "1"
    )
    return f"{canonical_lang(lang)}:{max(1, int(index_text))}"


def referenced_placeholder_keys(command_text: str) -> set[str]:
    keys: set[str] = set()
    for line in str(command_text or "").splitlines():
        match = NUMBERED_PLACEHOLDER_LINE_RE.match(line)
        if match:
            keys.add(placeholder_line_key(match))
    return keys


def reject_unfenced_file_placeholder_payload(
    text: str,
    blocks: Dict[str, List[str]],
    required_keys: Optional[set[str]] = None,
):
    lines = str(text or "").splitlines()
    in_code = False
    fence_char = ""
    fence_len = 0
    required_keys = required_keys or set()
    if not required_keys:
        return

    def opening_fence(line: str) -> Optional[re.Match]:
        return re.match(r"^\s{0,3}([`~]{3,})([^\r\n]*)\s*$", line.rstrip("\n\r"))

    def is_closing_fence(line: str) -> bool:
        if not in_code:
            return False
        pattern = rf"^\s{{0,3}}{re.escape(fence_char)}{{{fence_len},}}\s*$"
        return re.match(pattern, line.rstrip("\n\r")) is not None

    for index, line in enumerate(lines):
        if in_code:
            if is_closing_fence(line):
                in_code = False
                fence_char = ""
                fence_len = 0
            continue
        fence_match = opening_fence(line)
        if fence_match:
            fence = fence_match.group(1)
            fence_char = fence[0]
            fence_len = len(fence)
            in_code = True
            continue
        placeholder_match = NUMBERED_PLACEHOLDER_LINE_RE.match(line)
        if not placeholder_match:
            continue
        if placeholder_line_key(placeholder_match) not in required_keys:
            continue
        lang = (
            placeholder_match.group("html")
            or placeholder_match.group("css")
            or placeholder_match.group("slash")
            or placeholder_match.group("hash")
            or ""
        )
        index_text = (
            placeholder_match.group("html_index")
            or placeholder_match.group("css_index")
            or placeholder_match.group("slash_index")
            or placeholder_match.group("hash_index")
            or "1"
        )
        block_index = max(0, int(index_text) - 1)
        if get_code_block(blocks, lang, block_index) is not None:
            continue
        next_non_empty = ""
        for later_line in lines[index + 1:]:
            if later_line.strip():
                next_non_empty = later_line.lstrip()
                break
        if next_non_empty and not (next_non_empty.startswith("```") or next_non_empty.startswith("~~~")):
            canonical = canonical_lang(lang)
            raise ValueError(
                f"占位符 {canonical} block {index_text} 对应的文件内容没有放入 Markdown fenced 代码块。"
                f"请在同一回复中提供对应的 ```{canonical} ... ``` 代码块；不要直接输出裸文件正文。"
                "为避免覆盖文件，本轮已停止执行。"
            )

def scan_inline_protocol_examples(text: str) -> Dict[str, List[str]]:
    blocks: Dict[str, List[str]] = {}
    pattern = re.compile(r"对应\s+```(?P<lang>[A-Za-z0-9_+-]+)(?:\s|$)")
    for match in pattern.finditer(text or ""):
        lang = canonical_lang(match.group("lang"))
        if lang in blocks and blocks[lang]:
            continue
        blocks.setdefault(lang, []).append("")
    return blocks

def get_code_block(blocks: Dict[str, List[str]], lang: str, index: int = 0) -> Optional[str]:
    """按语言名读取代码块，兼容 js/javascript、ts/typescript 等别名。"""
    lang = canonical_lang(lang)
    values = blocks.get(lang, [])
    if 0 <= index < len(values):
        return values[index]
    return None

def get_next_code_block(blocks: Dict[str, List[str]], counters: Dict[str, int], lang: str) -> Optional[str]:
    """按占位符出现顺序消费同语言代码块。保留给旧数据迁移，不再用于占位符解析。"""
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
    - <!-- XXX block 1 -->
    - # XXX block 1
    其中 XXX 对应 blocks 中的 key（html/css/js/python/svg/json/yaml/typescript/ts...）
    """
    missing: List[str] = []
    placeholder_pattern = re.compile(
        r'<!--\s*(?P<html>\w+)\s+block\s+(?P<html_index>\d+)\s*-->'
        r'|/\*\s*(?P<css>\w+)\s+block\s+(?P<css_index>\d+)\s*\*/'
        r'|//\s*(?P<slash>\w+)\s+block\s+(?P<slash_index>\d+)\s*(?=\r?\n|$)'
        r'|#\s*(?P<hash>\w+)\s+block\s+(?P<hash_index>\d+)\s*(?=\r?\n|$)'
    )
    unnumbered_placeholder_pattern = re.compile(
        r'<!--\s*(?P<html>\w+)\s+block\s*-->'
        r'|/\*\s*(?P<css>\w+)\s+block\s*\*/'
        r'|//\s*(?P<slash>\w+)\s+block\b(?!\s+\d+)'
        r'|#\s*(?P<hash>\w+)\s+block\b(?!\s+\d+)'
    )

    def replace(match: re.Match) -> str:
        lang = (match.group('html') or match.group('css') or match.group('slash') or match.group('hash') or '').lower()
        index_text = (
            match.group('html_index')
            or match.group('css_index')
            or match.group('slash_index')
            or match.group('hash_index')
            or ''
        )
        block_index = max(0, int(index_text) - 1)
        code = get_code_block(blocks, lang, block_index)
        if code is None:
            missing.append(canonical_lang(lang) + f" block {index_text}")
            return match.group(0)
        return code

    def reject_unnumbered_placeholders(text: str):
        unnumbered = []
        for match in unnumbered_placeholder_pattern.finditer(text):
            lang = (match.group('html') or match.group('css') or match.group('slash') or match.group('hash') or '').lower()
            unnumbered.append(canonical_lang(lang))
        if not unnumbered:
            return
        unique_unnumbered = ", ".join(sorted(set(unnumbered)))
        raise ValueError(
            f"不再支持未编号占位符：{unique_unnumbered}。"
            "请使用带编号占位符，例如 <!-- Python block 1 --> 或 # Python block 1。"
            "为避免覆盖文件，本轮已停止执行。"
        )

    reject_unnumbered_placeholders(bash_text)
    resolved = bash_text
    for _ in range(12):
        before = resolved
        resolved = placeholder_pattern.sub(replace, resolved)
        if before == resolved or not placeholder_pattern.search(resolved):
            break
    if missing:
        unique_missing = ", ".join(sorted(set(missing)))
        missing_counts = ", ".join(
            f"{lang}×{count}" for lang, count in sorted(
                {lang: missing.count(lang) for lang in set(missing)}.items()
            )
        )
        raise ValueError(
            f"缺少占位符对应的代码块：{unique_missing}（缺少 {missing_counts}）。"
            "请确认占位符编号没有超过对应语言代码块数量，例如 <!-- Python block 1 --> 引用第 1 个 python 代码块。"
            "为避免覆盖文件，本轮已停止执行。"
        )
    if placeholder_pattern.search(resolved):
        raise ValueError("仍有未替换的占位符。为避免覆盖文件，本轮已停止执行。")
    reject_unnumbered_placeholders(resolved)
    return resolved

def find_heredoc_tags(line: str) -> List[str]:
    """提取一行 Bash 命令里的 heredoc 结束标记，如 EOF。"""
    return [match.group("tag") for match in HEREDOC_RE.finditer(line)]

def strip_heredoc_bodies_for_detection(cmd: str) -> str:
    """长运行检测只看 shell 命令本身，避免把写入的 Python 内容误判成服务进程。"""
    visible_lines: List[str] = []
    pending_tags: List[str] = []
    for raw_line in (cmd or "").splitlines():
        stripped = raw_line.strip()
        if pending_tags:
            if stripped in pending_tags:
                pending_tags = [tag for tag in pending_tags if tag != stripped]
            continue
        visible_lines.append(raw_line)
        pending_tags.extend(find_heredoc_tags(raw_line))
    return "\n".join(visible_lines)

def validate_shell_command_syntax(cmd: str) -> Optional[str]:
    if platform.system() == "Windows" or is_powershell_command(cmd):
        return None
    command = strip_shell_command_marker(cmd).strip()
    if not command:
        return None
    if has_unclosed_shell_quote(strip_heredoc_bodies_for_detection(command)):
        return "命令块不完整：检测到未闭合的 shell 引号。"
    script_path = write_temp_shell_script(command)
    try:
        shell = os.environ.get("SHELL") or "/bin/sh"
        result = subprocess.run(
            [shell, "-n", script_path],
            cwd=os.getcwd(),
            capture_output=True,
            text=True,
            timeout=5,
            **subprocess_no_window_kwargs(),
        )
        if result.returncode == 0:
            return None
        detail = (result.stderr or result.stdout or "").strip()
        detail = re.sub(rf"^{re.escape(script_path)}", "<command>", detail)
        return "命令块 shell 语法不完整或无法解析：" + (detail or f"退出码 {result.returncode}")
    except Exception as exc:
        logger.debug("Shell syntax validation skipped.", exc_info=True)
        return None if isinstance(exc, subprocess.TimeoutExpired) else f"命令块 shell 语法预检失败：{exc}"
    finally:
        remove_temp_script_later(script_path)

def command_writes_agent_project_cache(cmd: str, project_root: str) -> bool:
    if not project_root:
        return False
    cache_root = os.path.normpath(project_cache_dir(project_root))
    visible = strip_heredoc_bodies_for_detection(cmd or "")
    normalized = visible.replace("\\", "/")
    cache_forward = cache_root.replace("\\", "/")
    if cache_forward not in normalized:
        return False
    quoted_cache = re.escape(cache_forward)
    write_patterns = [
        rf"(?:^|\s)(?:>|>>)\s*['\"]?{quoted_cache}",
        rf"(?:^|\s)cat\s*>\s*['\"]?{quoted_cache}",
        rf"(?:^|\s)tee(?:\s+-a)?\s+['\"]?{quoted_cache}",
        rf"(?:^|\s)(?:touch|mkdir|cp|mv|rm)\b[^\n]*['\"]?{quoted_cache}",
        rf"(?:^|\s)(?:Set-Content|Add-Content|Out-File|New-Item|Copy-Item|Move-Item|Remove-Item)\b[^\n]*['\"]?{quoted_cache}",
    ]
    return any(re.search(pattern, normalized, re.I | re.M) for pattern in write_patterns)

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
    windows_abs = bool(re.match(r"^[a-zA-Z]:[\\/]", target) or target.startswith("\\\\"))
    if not os.path.isabs(target) and not windows_abs:
        target = os.path.join(cwd, target)
    return os.path.normpath(target)


def plain_cd_target(cmd: str, cwd: str) -> Optional[str]:
    text = strip_shell_command_marker(cmd).strip()
    if not text or "\n" in text:
        return None
    if any(separator in text for separator in ("&&", "||", ";", "|", " &")) or text.endswith("&"):
        return None
    try:
        tokens = shlex.split(text, posix=False)
    except ValueError:
        return None
    if len(tokens) < 2:
        return None
    command = tokens[0].strip().lower()
    target_tokens = tokens[1:]
    if command in {"cd", "chdir"}:
        if platform.system() == "Windows" and target_tokens and target_tokens[0].lower() == "/d":
            target_tokens = target_tokens[1:]
    elif command in {"set-location", "sl", "push-location"}:
        if target_tokens and target_tokens[0].lower() in {"-literalpath", "-path"}:
            target_tokens = target_tokens[1:]
    else:
        return None
    if len(target_tokens) != 1:
        return None
    return normalize_cd_target(target_tokens[0], cwd)

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


def shell_compound_end_token(line: str) -> str:
    stripped = line.strip()
    lowered = stripped.lower()
    if lowered.startswith("if ") or lowered.startswith("if\t") or lowered == "if":
        return "fi"
    if lowered.startswith(("for ", "for\t", "while ", "while\t", "until ", "until\t")):
        return "done"
    if lowered.startswith("case ") or lowered.startswith("case\t") or lowered == "case":
        return "esac"
    return ""


def shell_compound_starts_with_end(line: str, end_token: str) -> bool:
    if not end_token:
        return False
    stripped = line.strip().lower()
    return stripped == end_token or stripped.startswith(end_token + " ")


def is_interactive_shell_command(cmd: str) -> bool:
    cmd = strip_shell_command_marker(cmd)
    normalized = " ".join((cmd or "").strip().split()).lower()
    return normalized in {
        "bash",
        "sh",
        "zsh",
        "fish",
        "python",
        "python3",
        "node",
        "cmd",
        "powershell",
        "pwsh",
    }


def first_shell_segment(cmd: str) -> str:
    text = strip_shell_command_marker(cmd).strip()
    if not text:
        return ""
    first_line = strip_heredoc_bodies_for_detection(text).splitlines()[0].strip()
    for separator in ("&&", "||", ";", "|"):
        if separator in first_line:
            first_line = first_line.split(separator, 1)[0].strip()
    return first_line


def is_observation_command(cmd: str) -> bool:
    text = strip_shell_command_marker(cmd).lower()
    if "127.0.0.1:8798/terminallogs" in text or "localhost:8798/terminallogs" in text:
        return True
    first = first_shell_segment(cmd).lower()
    if not first:
        return False
    observation_prefixes = (
        "sleep ",
        "tail ",
        "cat ",
        "grep ",
        "rg ",
        "sed ",
        "awk ",
        "head ",
        "ps ",
        "pgrep ",
        "lsof ",
        "find ",
        "test ",
        "[ ",
        "stat ",
        "wc ",
        "jq ",
        "curl ",
        "python - <<",
        "python3 - <<",
        "start-sleep ",
        "get-content ",
        "select-string ",
        "get-process ",
        "where-object ",
    )
    return first in {"sleep", "ps", "cat", "tail", "grep", "rg", "curl"} or first.startswith(observation_prefixes)


def command_kind(cmd: str) -> str:
    text = strip_heredoc_bodies_for_detection(strip_shell_command_marker(cmd)).lower()
    first = first_shell_segment(cmd).lower()
    if any(token in first for token in ("pip ", "pip3 ", "uv pip ", "npm install", "pnpm install", "yarn install", "brew ", "apt ", "apt-get ")):
        return "install"
    if any(token in text for token in ("npm run build", "pnpm build", "yarn build", "vite build", "next build", "cargo build", "go build")):
        return "build"
    if any(token in text for token in ("curl ", "wget ", "git clone", "git pull", "download", "fetch")):
        return "data_fetch"
    if is_long_running(cmd):
        return "server"
    if is_observation_command(cmd):
        return "observe"
    return "unknown"


def command_block_from_blocks(blocks: Dict[str, List[str]]) -> tuple[str, str]:
    if platform.system() == "Windows":
        powershell_text = get_code_block(blocks, "powershell") or get_code_block(blocks, "ps1") or get_code_block(blocks, "pwsh") or ""
        if powershell_text:
            return powershell_text, "powershell"
        cmd_text = get_code_block(blocks, "cmd") or get_code_block(blocks, "bat") or get_code_block(blocks, "batch") or ""
        if cmd_text:
            return cmd_text, "cmd"
    return get_code_block(blocks, "bash") or "", "bash"


def terminal_extension_payload_text(raw: str) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    if (text.startswith("{") and text.endswith("}")) or (text.startswith("[") and text.endswith("]")):
        return text
    try:
        parts = shlex.split(text, posix=True)
        if len(parts) == 1:
            return parts[0].strip()
    except ValueError:
        pass
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        return text[1:-1].strip()
    return text


TERMINAL_EXTENSION_COMMAND_SPECS = (
    {
        "name": "wx send_file",
        "prefix_re": re.compile(r"^(?:wx(?:\s+send_file)?|AGENT_WECHAT_SEND_FILE)(?:\s|[:：]|$)", re.I),
        "complete_re": re.compile(r"^(?:wx\s+send_file\s+.+|AGENT_WECHAT_SEND_FILE(?:\s*[:：]\s*|\s+).+)$", re.I),
        "internal_prefixes": ("AGENT_WECHAT_SEND_FILE",),
        "usage": "`wx send_file 文件路径`",
    },
    {
        "name": "schedule",
        "prefix_re": re.compile(
            r"^(?:(?:wx\s+)?schedule(?:\s|$)|AGENT_WECHAT_(?:CREATE_SCHEDULE|SCHEDULE_ACTION)(?:\s|[:：]|$))",
            re.I,
        ),
        "complete_re": re.compile(
            r"^(?:(?:wx\s+)?schedule\s+(?:list\b|create\s+.+|(?:delete|remove|del)\s+.+|update\s+.+)"
            r"|AGENT_WECHAT_(?:CREATE_SCHEDULE|SCHEDULE_ACTION)(?:\s*[:：]\s*|\s+).+)$",
            re.I,
        ),
        "internal_prefixes": ("AGENT_WECHAT_CREATE_SCHEDULE", "AGENT_WECHAT_SCHEDULE_ACTION"),
        "usage": "`schedule create/list/delete/update ...`",
    },
    {
        "name": "web research",
        "prefix_re": re.compile(r"^(?:web\s+research(?:\s|$)|AGENT_WEB_RESEARCH(?:\s|[:：]|$))", re.I),
        "complete_re": re.compile(r"^(?:web\s+research\s+.+|AGENT_WEB_RESEARCH(?:\s*[:：]\s*|\s+).+)$", re.I),
        "internal_prefixes": ("AGENT_WEB_RESEARCH",),
        "usage": "`web research 搜索话题`",
    },
    {
        "name": "skill list",
        "prefix_re": re.compile(r"^(?:skill\s+list(?:\s|$)|AGENT_SKILL_LIST(?:\s|[:：]|$))", re.I),
        "complete_re": re.compile(r"^(?:skill\s+list\s*|AGENT_SKILL_LIST(?:\s*[:：]\s*|\s*)$)", re.I),
        "internal_prefixes": ("AGENT_SKILL_LIST",),
        "usage": "`skill list`",
    },
)


def strip_terminal_extension_shell_prefix(command_text: str) -> str:
    stripped = str(command_text or "").strip()
    shell_prefixed = re.match(r"^(?:bash|sh|zsh|shell)\s+(.+)$", stripped, re.I)
    if shell_prefixed:
        return (shell_prefixed.group(1) or "").strip()
    return stripped


def terminal_extension_usage_text() -> str:
    usages = [str(spec.get("usage") or "").strip() for spec in TERMINAL_EXTENSION_COMMAND_SPECS]
    return "；".join(usage for usage in usages if usage)


def terminal_extension_internal_prefixes() -> tuple[str, ...]:
    prefixes: List[str] = []
    for spec in TERMINAL_EXTENSION_COMMAND_SPECS:
        for prefix in spec.get("internal_prefixes") or ():
            prefix_text = str(prefix or "").strip().upper()
            if prefix_text and prefix_text not in prefixes:
                prefixes.append(prefix_text)
    return tuple(prefixes)


def is_terminal_extension_internal_directive_line(line: str) -> bool:
    upper = str(line or "").strip().upper()
    return any(
        upper.startswith(prefix + ":")
        or upper.startswith(prefix + "：")
        or upper == prefix
        or upper.startswith(prefix + " ")
        for prefix in terminal_extension_internal_prefixes()
    )


def normalize_terminal_extension_directive(line: str) -> List[str]:
    stripped = strip_terminal_extension_shell_prefix(line)
    if not stripped:
        return []
    skill_list_match = re.match(r"^skill\s+list\s*$", stripped, re.I)
    if skill_list_match:
        return ["AGENT_SKILL_LIST"]
    web_research_match = re.match(r"^web\s+research\s+(.+)$", stripped, re.I)
    if web_research_match:
        query = terminal_extension_payload_text(web_research_match.group(1) or "")
        if query:
            return [f"AGENT_WEB_RESEARCH: {query}"]
    wx_send_match = re.match(r"^wx\s+send_file\s+(.+)$", stripped, re.I)
    if wx_send_match:
        tail = (wx_send_match.group(1) or "").strip()
        targets = [part.strip().strip("\"'") for part in re.split(r"\s*,\s*", tail) if part.strip()]
        return [f"AGENT_WECHAT_SEND_FILE: {target}" for target in targets]
    schedule_create_match = re.match(r"^(?:wx\s+)?schedule\s+create\s+(.+)$", stripped, re.I)
    if schedule_create_match:
        return [f"AGENT_WECHAT_CREATE_SCHEDULE: {terminal_extension_payload_text(schedule_create_match.group(1) or '')}"]
    schedule_list_match = re.match(r"^(?:wx\s+)?schedule\s+list\s*$", stripped, re.I)
    if schedule_list_match:
        return ['AGENT_WECHAT_SCHEDULE_ACTION: {"action":"list"}']
    schedule_delete_match = re.match(r"^(?:wx\s+)?schedule\s+(?:delete|remove|del)\s+(.+)$", stripped, re.I)
    if schedule_delete_match:
        target = (schedule_delete_match.group(1) or "").strip()
        return [f"AGENT_WECHAT_SCHEDULE_ACTION: {json.dumps({'action': 'delete', 'target': target}, ensure_ascii=False, separators=(',', ':'))}"]
    schedule_update_match = re.match(r"^(?:wx\s+)?schedule\s+update\s+(.+)$", stripped, re.I)
    if schedule_update_match:
        payload_text = terminal_extension_payload_text(schedule_update_match.group(1) or "")
        try:
            payload = json.loads(payload_text)
            if isinstance(payload, dict) and not payload.get("action"):
                payload["action"] = "update"
            return [f"AGENT_WECHAT_SCHEDULE_ACTION: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}"]
        except Exception:
            return [f"AGENT_WECHAT_SCHEDULE_ACTION: {payload_text}"]
    send_match = re.match(r"^(AGENT_WECHAT_SEND_FILE)(?:\s*[:：]\s*|\s+)(.+)$", stripped, re.I)
    if send_match:
        tail = (send_match.group(2) or "").strip()
        targets = [part.strip().strip("\"'") for part in re.split(r"\s*,\s*", tail) if part.strip()]
        return [f"AGENT_WECHAT_SEND_FILE: {target}" for target in targets]
    for name in ("AGENT_WECHAT_CREATE_SCHEDULE", "AGENT_WECHAT_SCHEDULE_ACTION", "AGENT_WEB_RESEARCH", "AGENT_SKILL_LIST"):
        match = re.match(rf"^({name})(?:\s*[:：]\s*|\s+)(.+)$", stripped, re.I)
        if match:
            return [f"{name}: {terminal_extension_payload_text(match.group(2) or '')}"]
    return []


def extract_web_research_queries(text: str) -> List[str]:
    queries: List[str] = []
    for line in str(text or "").splitlines():
        normalized = normalize_terminal_extension_directive(line)
        for directive in normalized:
            upper = directive.upper()
            if not upper.startswith("AGENT_WEB_RESEARCH:") and not upper.startswith("AGENT_WEB_RESEARCH："):
                continue
            _, _, tail = directive.partition(":" if ":" in directive else "：")
            query = terminal_extension_payload_text(tail)
            if query:
                queries.append(query)
    return queries[:3]


def web_research_extension_reply(queries: List[str]) -> str:
    items = [str(item or "").strip() for item in queries if str(item or "").strip()]
    if not items:
        return (
            "网页搜索请求已接收。程序会尝试直接执行本地网页搜索，并把结果写回执行结果与上下文。"
            "如果本轮搜索失败，请检查网络或更换搜索话题后再继续。"
        )
    if len(items) == 1:
        return (
            f"网页搜索请求已接收。程序会直接搜索“{items[0]}”，并把结果写回执行结果与上下文。"
        )
    joined = "；".join(f"“{item}”" for item in items[:3])
    return (
        "网页搜索请求已接收。程序会依次执行以下本地网页搜索，并把结果写回执行结果与上下文："
        f"{joined}。"
    )


def summarize_web_research_response(payload: dict) -> str:
    output_items = payload.get("output")
    if not isinstance(output_items, list):
        return ""
    text_parts: List[str] = []
    sources: List[str] = []
    seen_urls: set[str] = set()
    for item in output_items:
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type") or "")
        if item_type == "message":
            for content_item in item.get("content") or []:
                if not isinstance(content_item, dict):
                    continue
                content_type = str(content_item.get("type") or "")
                if content_type in {"output_text", "text"}:
                    text = str(content_item.get("text") or "").strip()
                    if text:
                        text_parts.append(text)
        elif item_type == "web_search_call":
            action = item.get("action") if isinstance(item.get("action"), dict) else {}
            for source in action.get("sources") or []:
                if not isinstance(source, dict):
                    continue
                title = str(source.get("title") or "").strip()
                url = str(source.get("url") or "").strip()
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                label = title or url
                sources.append(f"- {label}: {url}")
    body = "\n\n".join(part for part in text_parts if part).strip()
    if sources:
        source_block = "参考来源：\n" + "\n".join(sources[:5])
        body = (body + "\n\n" + source_block).strip() if body else source_block
    return body


def looks_like_incomplete_terminal_extension_command(command_text: str) -> bool:
    stripped = strip_terminal_extension_shell_prefix(command_text)
    if not stripped:
        return False
    if normalize_terminal_extension_directive(stripped):
        return False
    normalized = re.sub(r"\s+", " ", stripped).strip()
    for spec in TERMINAL_EXTENSION_COMMAND_SPECS:
        prefix_re = spec.get("prefix_re")
        complete_re = spec.get("complete_re")
        if (
            hasattr(prefix_re, "match")
            and prefix_re.match(normalized)
            and not (hasattr(complete_re, "match") and complete_re.match(normalized))
        ):
            return True
    return False


def split_shell_chain_segments(line: str) -> List[tuple[str, str]]:
    """Split a simple shell chain on unquoted separators while keeping separators."""
    text = str(line or "")
    segments: List[tuple[str, str]] = []
    start = 0
    index = 0
    single = False
    double = False
    escaped = False
    while index < len(text):
        ch = text[index]
        if escaped:
            escaped = False
            index += 1
            continue
        if ch == "\\":
            escaped = True
            index += 1
            continue
        if ch == "'" and not double:
            single = not single
            index += 1
            continue
        if ch == '"' and not single:
            double = not double
            index += 1
            continue
        if not single and not double:
            if text.startswith("&&", index):
                segments.append((text[start:index].strip(), "&&"))
                index += 2
                start = index
                continue
            if text.startswith("||", index):
                segments.append((text[start:index].strip(), "||"))
                index += 2
                start = index
                continue
            if ch == "|":
                segments.append((text[start:index].strip(), "|"))
                index += 1
                start = index
                continue
            if ch == ";":
                segments.append((text[start:index].strip(), ";"))
                index += 1
                start = index
                continue
            if ch == "&" and (index == 0 or text[index - 1] != ">") and (index + 1 >= len(text) or text[index + 1] != ">"):
                segments.append((text[start:index].strip(), "&"))
                index += 1
                start = index
                continue
        index += 1
    segments.append((text[start:].strip(), ""))
    return [(segment, separator) for segment, separator in segments if segment or separator]


def terminal_extension_placeholder_command(prev_separator: str, next_separator: str) -> str:
    if prev_separator == "|":
        return "cat >/dev/null"
    if next_separator == "|":
        return "printf ''"
    return "true"


def terminal_extension_command_display(directive: str) -> str:
    return terminal_extension_command_for_log(directive)


def terminal_extension_inline_warning_message(directives: List[str]) -> str:
    commands = [terminal_extension_command_display(item) for item in directives if str(item or "").strip()]
    command_text = "、".join(commands[:3]) if commands else "终端扩展指令"
    if len(commands) > 3:
        command_text += f" 等 {len(commands)} 条"
    return (
        "⚠️ Agent Qt 风险日志：已把高风险链式命令中的终端扩展指令替换为空操作占位。"
        f"本次涉及：{command_text}。"
        "这些扩展指令不支持和 ||、|、& 混写；这会丢失条件、管道或后台语义。"
        "下一轮请避免把这些扩展指令放进高风险 shell 链。"
    )


def terminal_extension_inline_warning_command(command_lang: str, directives: List[str]) -> str:
    message = terminal_extension_inline_warning_message(directives)
    if command_lang == "powershell":
        return "Write-Output " + json.dumps(message, ensure_ascii=False)
    return "printf '%s\\n' " + shlex.quote(message)


def terminal_extension_inline_rewrite_is_risky(prev_separator: str, next_separator: str) -> bool:
    return prev_separator in {"||", "|", "&"} or next_separator in {"||", "|", "&"}


def strip_inline_terminal_extension_directives_from_line(line: str) -> tuple[str, List[str], List[str]]:
    segments = split_shell_chain_segments(line)
    if len(segments) <= 1:
        return str(line or ""), [], []
    rebuilt: List[tuple[str, str]] = []
    directives: List[str] = []
    risky_directives: List[str] = []
    for index, (segment, separator) in enumerate(segments):
        normalized = normalize_terminal_extension_directive(segment)
        if normalized:
            directives.extend(normalized)
            prev_separator = segments[index - 1][1] if index > 0 else ""
            if terminal_extension_inline_rewrite_is_risky(prev_separator, separator):
                risky_directives.extend(normalized)
            segment = terminal_extension_placeholder_command(prev_separator, separator)
        if segment:
            rebuilt.append((segment, separator))
    if not directives:
        return str(line or ""), [], []
    cleaned_parts: List[str] = []
    for idx, (segment, separator) in enumerate(rebuilt):
        cleaned_parts.append(segment)
        if separator and idx < len(rebuilt) - 1:
            cleaned_parts.append(separator)
    return " ".join(cleaned_parts).strip(), directives, risky_directives


def strip_terminal_extension_directives_from_command(
    command_text: str,
    *,
    include_inline_warning: bool = False,
    command_lang: str = "bash",
) -> tuple[str, List[str]]:
    kept_lines: List[str] = []
    directives: List[str] = []
    for line in str(command_text or "").splitlines():
        cleaned_line, inline_directives, risky_directives = strip_inline_terminal_extension_directives_from_line(line)
        if inline_directives:
            directives.extend(inline_directives)
            if cleaned_line:
                if include_inline_warning and risky_directives:
                    kept_lines.append(terminal_extension_inline_warning_command(command_lang, risky_directives))
                kept_lines.append(cleaned_line)
            continue
        normalized = normalize_terminal_extension_directive(line)
        if normalized:
            directives.extend(normalized)
            continue
        kept_lines.append(line)
    return "\n".join(kept_lines).strip(), directives


def terminal_extension_directives_from_text(text: str) -> List[str]:
    blocks = scan_all_code_blocks(str(text or ""))
    command_text, _command_lang = command_block_from_blocks(blocks)
    if not command_text:
        return []
    _cleaned, directives = strip_terminal_extension_directives_from_command(command_text)
    return directives


def strip_terminal_extension_directives_from_text(text: str) -> str:
    source = str(text or "")
    blocks = scan_all_code_blocks(source)
    command_text, _command_lang = command_block_from_blocks(blocks)
    if not command_text:
        return source
    cleaned, directives = strip_terminal_extension_directives_from_command(command_text)
    if not directives:
        return source
    return source.replace(command_text, cleaned, 1).strip()


def extract_bash_commands(text: str, blocks: Dict[str, List[str]]) -> List[str]:
    """提取当前平台命令块并替换占位符。"""
    command_text, command_lang = command_block_from_blocks(blocks)
    if not command_text:
        return []
    if not command_text:
        return []
    command_text, _terminal_extensions = strip_terminal_extension_directives_from_command(command_text)
    if looks_like_incomplete_terminal_extension_command(command_text):
        raise ValueError(
            "检测到不完整的终端扩展指令。"
            f"请使用完整格式：{terminal_extension_usage_text()}。"
        )
    reject_internal_context_in_command_block(command_text)
    reject_unfenced_file_placeholder_payload(text, blocks, referenced_placeholder_keys(command_text))
    command_text = resolve_all_placeholders(command_text, blocks)
    command_text, _terminal_extensions = strip_terminal_extension_directives_from_command(
        command_text,
        include_inline_warning=True,
        command_lang=command_lang,
    )
    if looks_like_incomplete_terminal_extension_command(command_text):
        raise ValueError(
            "检测到不完整的终端扩展指令。"
            f"请使用完整格式：{terminal_extension_usage_text()}。"
        )
    reject_internal_context_in_command_block(command_text)
    if command_lang == "powershell":
        return [POWERSHELL_COMMAND_PREFIX + command_text.strip()] if command_text.strip() else []
    command_text = command_text.strip()
    return [command_text] if command_text else []


DEEPSEEK_DISCLAIMER_LINE_RE = re.compile(
    r"^\s*(?:本回答由\s*AI\s*生成，内容仅供参考，请仔细甄别。?|内容由\s*AI\s*生成，仅供参考，请仔细甄别。?)\s*$",
    re.I,
)


def strip_provider_ui_artifacts_from_command(cmd: str) -> str:
    lines = str(cmd or "").splitlines()
    while lines and DEEPSEEK_DISCLAIMER_LINE_RE.match(lines[-1].strip()):
        lines.pop()
    return "\n".join(lines).strip()


def reject_internal_context_in_command_block(command_text: str):
    """拒绝把执行结果/终端扩展指令等聊天协议误塞进 shell 块。"""
    text = str(command_text or "")
    internal_markers = (
        "【本地执行结果和文件变更】",
        "【执行结果】",
        "Execution log:",
        "Git diff file names:",
        "文件变更：",
        "===== 执行结果 =====",
        "===== AI 输出 =====",
        "AGENT_DONE",
        "AGENT_QT_DONE",
    )
    for marker in internal_markers:
        if marker in text:
            raise ValueError(
                "命令块混入了执行结果、结论或终端扩展指令。"
                f"请只在命令块内写真实 shell 命令或完整终端扩展指令：{terminal_extension_usage_text()}。"
            )

def is_long_running(cmd: str) -> bool:
    cmd = strip_shell_command_marker(cmd)
    detection_cmd = strip_heredoc_bodies_for_detection(cmd).strip() or cmd
    command_text = detection_cmd.lower()
    first_line = first_shell_segment(detection_cmd).lower()
    if is_observation_command(cmd):
        return False
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
    cmd = strip_shell_command_marker(cmd)
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

def should_use_temp_shell_script(cmd: str) -> bool:
    return platform.system() == "Windows" and "\n" in (cmd or "")

def write_temp_shell_script(cmd: str) -> str:
    is_ps = is_powershell_command(cmd)
    suffix = ".ps1" if is_ps else (".cmd" if platform.system() == "Windows" else ".sh")
    fd, path = tempfile.mkstemp(prefix="agent_qt_", suffix=suffix)
    if is_ps:
        newline = "\r\n"
        encoding = "utf-8-sig"
        content = strip_shell_command_marker(cmd)
    elif platform.system() == "Windows":
        newline = "\r\n"
        encoding = "utf-8"
        content = cmd
        if not content.lstrip().lower().startswith("@echo off"):
            content = "@echo off\n" + content
        if "chcp 65001" not in content.lower().splitlines()[:6]:
            lines = content.splitlines()
            insert_at = 1 if lines and lines[0].strip().lower() == "@echo off" else 0
            lines.insert(insert_at, "chcp 65001 >nul")
            content = "\n".join(lines)
    else:
        newline = "\n"
        encoding = "utf-8"
        content = cmd
    with os.fdopen(fd, "w", encoding=encoding, newline=newline) as f:
        f.write(content)
        if not content.endswith(("\n", "\r")):
            f.write("\n")
    try:
        os.chmod(path, 0o755)
    except OSError:
        pass
    return path

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


def shell_launch_for_command(cmd: str, interactive: bool = False, script_path: str = "") -> tuple[str, List[str]]:
    if platform.system() == "Windows" and script_path and is_powershell_command(cmd):
        shell = shutil.which("powershell.exe") or shutil.which("powershell") or "powershell.exe"
        return shell, ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script_path]
    if platform.system() == "Windows" and script_path:
        shell = os.environ.get("COMSPEC") or "cmd.exe"
        return shell, ["/d", "/c", "call", script_path]
    return shell_launch_args(cmd, interactive=interactive)


class BackgroundProcessStarted(Exception):
    def __init__(self, info: Dict[str, object], output: str = ""):
        super().__init__("process moved to background")
        self.info = info
        self.output = output


def command_log_path(project_root: str, name: str) -> tuple[str, str]:
    terminal_id = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    log_path = os.path.join(terminal_cache_dir(project_root), f"{terminal_id}-{safe_terminal_log_name(name)}.log")
    return terminal_id, log_path


def remove_temp_script_later(path: str):
    if not path:
        return
    try:
        os.remove(path)
    except OSError:
        pass


def append_process_stream_to_log(process: subprocess.Popen, log_path: str, script_path: str = ""):
    try:
        with open(log_path, "a", encoding="utf-8", newline="\n") as f:
            stream = process.stdout
            if stream is not None:
                while True:
                    chunk = stream.readline()
                    if not chunk:
                        break
                    f.write(chunk)
                    f.flush()
            exit_code = process.wait()
            f.write(f"\n--- 退出码: {exit_code} ---")
    except OSError:
        try:
            process.wait()
        except Exception:
            pass
    finally:
        remove_temp_script_later(script_path)


def run_shell_command_capture(cmd: str, cwd: str, timeout: int, project_root: str = "", display_name: str = "") -> subprocess.CompletedProcess:
    script_path = ""
    if platform.system() == "Windows" and is_powershell_command(cmd):
        script_path = write_temp_shell_script(cmd)
    elif should_use_temp_shell_script(cmd):
        script_path = write_temp_shell_script(cmd)
    shell, args = shell_launch_for_command(cmd, script_path=script_path)
    terminal_id = ""
    log_path = ""
    if project_root:
        terminal_id, log_path = command_log_path(project_root, display_name or "timeout-command")
    env = agent_runtime_env(create=False)
    if terminal_id:
        env["AGENT_QT_TERMINAL_ID"] = terminal_id
    process = subprocess.Popen(
        [shell, *args],
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        **subprocess_no_window_kwargs(),
    )
    try:
        stdout, _stderr = process.communicate(timeout=timeout)
        remove_temp_script_later(script_path)
        return subprocess.CompletedProcess([shell, *args], process.returncode, stdout or "", "")
    except subprocess.TimeoutExpired as exc:
        if not project_root:
            process.kill()
            stdout, _stderr = process.communicate()
            remove_temp_script_later(script_path)
            raise subprocess.TimeoutExpired(exc.cmd, exc.timeout, output=stdout)
        captured = exc.output or ""
        if isinstance(captured, bytes):
            captured = decode_process_output(captured)
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(
                f"$ {strip_shell_command_marker(cmd)}\n"
                f"# cwd: {cwd}\n"
                f"# pid: {process.pid}\n"
                f"# terminal_id: {terminal_id}\n"
            )
            if captured:
                f.write(str(captured))
        thread = threading.Thread(
            target=append_process_stream_to_log,
            args=(process, log_path, script_path),
            daemon=True,
        )
        thread.start()
        raise BackgroundProcessStarted({
            "id": terminal_id,
            "cmd": cmd,
            "cwd": cwd,
            "name": display_name or "timeout-command",
            "pid": process.pid,
            "log_path": log_path,
            "launch_reason": "timeout",
            "command_kind": command_kind(cmd),
            "expected_persistent": True,
        }, output=str(captured or ""))


def decode_process_output(data: bytes) -> str:
    if not data:
        return ""
    encodings = ["utf-8-sig", "utf-8", locale.getpreferredencoding(False), "gb18030"]
    seen = set()
    for encoding in encodings:
        if not encoding or encoding.lower() in seen:
            continue
        seen.add(encoding.lower())
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")

SNAPSHOT_SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv",
    "dist", "build", ".next", ".nuxt", ".cache", ".pytest_cache",
    HISTORY_DIR_NAME,
}
SNAPSHOT_MAX_FILE_BYTES = 8 * 1024 * 1024
INTERNAL_GIT_MAX_STORED_FILE_BYTES = SNAPSHOT_MAX_FILE_BYTES


def project_cache_key(root: str) -> str:
    normalized = os.path.abspath(os.path.expanduser(root or "workspace"))
    digest = hashlib.sha1(normalized.encode("utf-8", errors="surrogatepass")).hexdigest()[:16]
    base = re.sub(r"[^a-zA-Z0-9_.-]+", "_", os.path.basename(normalized) or "workspace").strip("._-") or "workspace"
    return f"{base[:48]}-{digest}"


def project_cache_dir(root: str) -> str:
    return os.path.join(AGENT_HOME_DIR, "projects", project_cache_key(root))


def terminal_cache_dir(root: str) -> str:
    return os.path.join(project_cache_dir(root), "terminals")


def terminal_registry_path(root: str) -> str:
    return os.path.join(terminal_cache_dir(root), "registry.json")


def safe_terminal_log_name(name: str) -> str:
    base = re.sub(r"[^a-zA-Z0-9_.-]+", "_", str(name or "terminal")).strip("._-") or "terminal"
    return base[:48]


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


def should_skip_snapshot_dir(dirname: str) -> bool:
    return dirname in SNAPSHOT_SKIP_DIRS or dirname.startswith(".Trash")


class InternalGitChangeTracker:
    """Shadow git repo stored in Agent Qt cache, never inside the user's project."""

    def __init__(self, project_root: str):
        self.project_root = os.path.abspath(os.path.expanduser(project_root))
        self.repo_root = os.path.join(project_cache_dir(self.project_root), "internal-git")
        self.git = shutil.which("git") or ""
        self.available = bool(self.git)

    def _run(
        self,
        args: List[str],
        *,
        check: bool = True,
        text: bool = True,
    ) -> subprocess.CompletedProcess:
        if not self.available:
            raise RuntimeError("git executable not found")
        return subprocess.run(
            [self.git, *args],
            cwd=self.repo_root,
            capture_output=True,
            text=text,
            check=check,
            **subprocess_no_window_kwargs(),
        )

    def ensure_repo(self) -> bool:
        if not self.available or not os.path.isdir(self.project_root):
            return False
        os.makedirs(self.repo_root, exist_ok=True)
        if not os.path.isdir(os.path.join(self.repo_root, ".git")):
            subprocess.run(
                [self.git, "init", "-q"],
                cwd=self.repo_root,
                capture_output=True,
                text=True,
                check=False,
                **subprocess_no_window_kwargs(),
            )
        for key, value in (
            ("user.name", "Agent Qt Internal Git"),
            ("user.email", "agent-qt-internal@example.invalid"),
            ("core.autocrlf", "false"),
            ("core.quotepath", "false"),
        ):
            self._run(["config", key, value], check=False)
        return os.path.isdir(os.path.join(self.repo_root, ".git"))

    def _project_files(self) -> Dict[str, str]:
        files: Dict[str, str] = {}
        shadow_root = os.path.abspath(self.repo_root)
        for dirpath, dirnames, filenames in os.walk(self.project_root):
            abs_dirpath = os.path.abspath(dirpath)
            if abs_dirpath == shadow_root or abs_dirpath.startswith(shadow_root + os.sep):
                dirnames[:] = []
                continue
            dirnames[:] = [dirname for dirname in dirnames if not should_skip_snapshot_dir(dirname)]
            for filename in filenames:
                source = os.path.join(dirpath, filename)
                if not os.path.isfile(source):
                    continue
                rel = os.path.relpath(source, self.project_root).replace(os.sep, "/")
                files[rel] = source
        return files

    def sync_from_project(self) -> bool:
        if not self.ensure_repo():
            return False
        files = self._project_files()
        seen = set(files)
        for rel, source in files.items():
            target = os.path.join(self.repo_root, *rel.split("/"))
            try:
                os.makedirs(os.path.dirname(target), exist_ok=True)
                shutil.copy2(source, target)
            except OSError:
                continue

        for dirpath, dirnames, filenames in os.walk(self.repo_root, topdown=True):
            if ".git" in dirnames:
                dirnames.remove(".git")
            for filename in filenames:
                path = os.path.join(dirpath, filename)
                rel = os.path.relpath(path, self.repo_root).replace(os.sep, "/")
                if rel not in seen:
                    try:
                        os.remove(path)
                    except OSError:
                        pass

        for dirpath, dirnames, filenames in os.walk(self.repo_root, topdown=False):
            if os.path.abspath(dirpath) == os.path.abspath(self.repo_root):
                continue
            if ".git" in os.path.relpath(dirpath, self.repo_root).split(os.sep):
                continue
            for dirname in dirnames:
                path = os.path.join(dirpath, dirname)
                if os.path.basename(path) == ".git":
                    continue
                try:
                    os.rmdir(path)
                except OSError:
                    pass
        return True

    def commit_snapshot(self, label: str) -> str:
        if not self.sync_from_project():
            return ""
        self._run(["add", "-A"], check=False)
        message = f"Agent Qt internal {label} {datetime.now().isoformat(timespec='seconds')}"
        self._run(["commit", "--allow-empty", "-q", "-m", message], check=False)
        result = self._run(["rev-parse", "HEAD"], check=True, text=True)
        return result.stdout.strip()

    def prepare_before(self) -> str:
        return self.commit_snapshot("before")

    def capture_changes(self, before_commit: str) -> List[Dict[str, object]]:
        after_commit = self.commit_snapshot("after")
        if not before_commit or not after_commit:
            return []
        return self.build_change_records(before_commit, after_commit)

    def _decode_git_path(self, value: bytes) -> str:
        return value.decode("utf-8", errors="surrogateescape")

    def _blob_bytes(self, commit: str, path: str) -> tuple[Optional[bytes], bool]:
        spec = f"{commit}:{path}"
        size_result = self._run(["cat-file", "-s", spec], check=False, text=True)
        if size_result.returncode != 0:
            return None, True
        try:
            size = int((size_result.stdout or "0").strip())
        except ValueError:
            size = INTERNAL_GIT_MAX_STORED_FILE_BYTES + 1
        if size > INTERNAL_GIT_MAX_STORED_FILE_BYTES:
            return None, False
        blob = self._run(["show", "--no-ext-diff", spec], check=False, text=False)
        if blob.returncode != 0:
            return None, True
        return blob.stdout, True

    def _diff_text(self, before_commit: str, after_commit: str, path: str) -> str:
        result = self._run(
            ["diff", "--no-ext-diff", "--no-renames", "--unified=3", before_commit, after_commit, "--", path],
            check=False,
            text=True,
        )
        return (result.stdout or "").strip() or "(metadata changed)"

    def _numstat(self, before_commit: str, after_commit: str, path: str) -> tuple[int, int, bool]:
        result = self._run(
            ["diff", "--numstat", "--no-renames", before_commit, after_commit, "--", path],
            check=False,
            text=True,
        )
        line = (result.stdout or "").splitlines()[0] if result.stdout else ""
        parts = line.split("\t")
        if len(parts) < 2:
            return 0, 0, False
        is_binary = parts[0] == "-" or parts[1] == "-"
        try:
            additions = int(parts[0])
        except ValueError:
            additions = 0
        try:
            deletions = int(parts[1])
        except ValueError:
            deletions = 0
        return additions, deletions, is_binary

    def build_change_records(self, before_commit: str, after_commit: str) -> List[Dict[str, object]]:
        result = self._run(
            ["diff", "--name-status", "--no-renames", "-z", before_commit, after_commit, "--"],
            check=False,
            text=False,
        )
        parts = [part for part in (result.stdout or b"").split(b"\0") if part]
        records: List[Dict[str, object]] = []
        index = 0
        while index + 1 < len(parts):
            raw_status = self._decode_git_path(parts[index])
            path = self._decode_git_path(parts[index + 1])
            index += 2
            status_code = (raw_status or "M")[0]
            status = {"A": "added", "D": "deleted"}.get(status_code, "modified")
            before = None
            after = None
            before_ok = True
            after_ok = True
            if status_code != "A":
                before, before_ok = self._blob_bytes(before_commit, path)
            if status_code != "D":
                after, after_ok = self._blob_bytes(after_commit, path)
            diff = self._diff_text(before_commit, after_commit, path)
            additions, deletions, is_binary = self._numstat(before_commit, after_commit, path)
            is_binary = is_binary or "Binary files " in diff
            if additions == 0 and deletions == 0:
                additions = sum(1 for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++"))
                deletions = sum(1 for line in diff.splitlines() if line.startswith("-") and not line.startswith("---"))
            undoable = before_ok and after_ok
            records.append(
                {
                    "path": path,
                    "status": status,
                    "before": before,
                    "after": after,
                    "additions": additions,
                    "deletions": deletions,
                    "diff": diff,
                    "diff_rows": [] if is_binary else parse_unified_diff_lines(diff),
                    "binary": is_binary,
                    "internal_git": True,
                    "internal_git_repo": self.repo_root,
                    "before_commit": before_commit,
                    "after_commit": after_commit,
                    "undoable": undoable,
                }
            )
        return records

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
        diff_rows = []
        binary = True
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
        binary = False

    return {
        "path": path,
        "status": status,
        "before": before,
        "after": after,
        "additions": additions,
        "deletions": deletions,
        "diff": detail,
        "diff_rows": diff_rows,
        "binary": binary,
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
    text_records = [r for r in records if not r.get("binary")]
    binary_count = len(records) - len(text_records)
    additions = sum(int(r["additions"]) for r in text_records)
    deletions = sum(int(r["deletions"]) for r in text_records)
    stat_parts = []
    if text_records:
        stat_parts.append(f"+{additions}  -{deletions}")
    if binary_count:
        stat_parts.append(f"{binary_count} binary")
    stat_suffix = "  " + "  · ".join(stat_parts) if stat_parts else ""
    lines = [
        "",
        "文件变更：",
        f"{len(records)} 个文件变更{stat_suffix}",
    ]
    for record in records:
        if record.get("binary"):
            lines.append(f"- {record['path']}  binary/{record.get('status', 'modified')}")
        else:
            lines.append(f"- {record['path']}  +{record['additions']}  -{record['deletions']}")
    if include_diff:
        lines.append("")
        lines.append("Diff:")
        for record in records:
            lines.append(f"\n--- {record['path']} ---")
            if record.get("binary"):
                lines.append("Binary/Office file changed; textual diff is not available.")
            else:
                lines.append(str(record["diff"]))
    return "\n".join(lines)


LOW_VALUE_CONTEXT_START = "<<<AGENT_QT_LOW_VALUE_CONTEXT_START"
LOW_VALUE_CONTEXT_END = "<<<AGENT_QT_LOW_VALUE_CONTEXT_END>>>"
LOW_VALUE_CONTEXT_BLOCK_RE = re.compile(
    rf"{re.escape(LOW_VALUE_CONTEXT_START)}[^\n]*\n.*?\n{re.escape(LOW_VALUE_CONTEXT_END)}",
    re.S,
)


def low_value_context_block(kind: str, content: str) -> str:
    return f"{LOW_VALUE_CONTEXT_START} kind={kind}>>>\n{str(content or '').strip()}\n{LOW_VALUE_CONTEXT_END}"


def strip_low_value_context_blocks(text: str) -> str:
    return LOW_VALUE_CONTEXT_BLOCK_RE.sub("[低密度工具输出已省略；如需细节请读取具体文件或重新运行命令]", str(text or ""))


def mask_low_value_context_markers_for_display(text: str) -> str:
    lines: List[str] = []
    current_kind = "low_value"
    for line in str(text or "").splitlines(keepends=True):
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


def diff_hunk_headers(diff_text: str, limit: int = 24) -> List[str]:
    headers = []
    for line in str(diff_text or "").splitlines():
        if line.startswith("@@"):
            headers.append(line)
            if len(headers) >= limit:
                break
    return headers


def format_change_context_summary(records: List[Dict[str, object]]) -> str:
    if not records:
        return "Git diff file names:\n未检测到文件改动。"
    text_records = [r for r in records if not r.get("binary")]
    binary_count = len(records) - len(text_records)
    additions = sum(int(r.get("additions", 0)) for r in text_records)
    deletions = sum(int(r.get("deletions", 0)) for r in text_records)
    stat_parts = []
    if text_records:
        stat_parts.append(f"+{additions}  -{deletions}")
    if binary_count:
        stat_parts.append(f"{binary_count} binary")
    stat_suffix = "  " + "  · ".join(stat_parts) if stat_parts else ""
    lines = [
        "Git diff file names:",
        f"{len(records)} files changed{stat_suffix}",
    ]
    internal_records = [record for record in records if record.get("internal_git")]
    if internal_records:
        repo = str(internal_records[0].get("internal_git_repo") or "").strip()
        before_commit = str(internal_records[0].get("before_commit") or "").strip()
        after_commit = str(internal_records[0].get("after_commit") or "").strip()
        lines.extend([
            "",
            "Internal git snapshot:",
            f"repo: {repo or 'unavailable'}",
            f"before_commit: {before_commit or 'unavailable'}",
            f"after_commit: {after_commit or 'unavailable'}",
        ])
        lines.append("")
    for record in records:
        status = str(record.get("status", "modified"))
        marker = {"added": "A", "deleted": "D"}.get(status, "M")
        if record.get("binary"):
            lines.append(f"{marker} {record.get('path', '')}  binary/{status}")
        else:
            lines.append(f"{marker} {record.get('path', '')}  +{record.get('additions', 0)}  -{record.get('deletions', 0)}")
    lines.append("")
    lines.append("Git diff hunks:")
    for record in records:
        headers = diff_hunk_headers(str(record.get("diff", "")))
        if headers:
            lines.append(str(record.get("path", "")))
            lines.extend(f"  {header}" for header in headers)
        else:
            lines.append(f"{record.get('path', '')}  (binary/metadata or full-file change)")
    return "\n".join(lines)


def format_terminal_launch_summary(launches: List[Dict[str, object]]) -> str:
    if not launches:
        return ""
    lines = [
        "Terminal processes:",
        f"{len(launches)} terminal process(es) launched or tracked.",
    ]
    for item in launches:
        launch_text = re.sub(r"\s+", " ", strip_shell_command_marker(str(item.get("cmd") or ""))).strip()
        lines.append(
            " - "
            f"id={item.get('id') or 'unknown'}  "
            f"pid={item.get('pid') or 0}  "
            f"status={item.get('status') or 'unknown'}  "
            f"persistent={bool(item.get('persistent'))}  "
            f"launch_reason={item.get('launch_reason') or 'unknown'}  "
            f"command_kind={item.get('command_kind') or 'unknown'}  "
            f"启动命令={truncate_middle(launch_text, 180) or 'unavailable'}"
        )
    lines.append("查看该后台终端输出：curl -s 'http://127.0.0.1:8798/terminallogs?pid=xxx'，把 xxx 换成上面的 pid。")
    return "\n".join(lines)


def build_execution_context_content(
    full_log: str,
    records: List[Dict[str, object]],
    long_running_launches: int = 0,
    terminal_launches: Optional[List[Dict[str, object]]] = None,
) -> str:
    parts = [
        "Execution log:",
        low_value_context_block("execution_log", truncate_middle(str(full_log or "").strip(), 6000)),
    ]
    if records:
        parts.extend([
            "",
            "File change summary:",
            format_change_context_summary(records),
        ])
    if terminal_launches:
        parts.extend([
            "",
            "Terminal process summary:",
            format_terminal_launch_summary(terminal_launches),
        ])
    elif long_running_launches:
        parts.extend([
            "",
            "Git diff file names:",
            "未检测到文件改动。若命令正在底部终端继续运行，保存/生成文件后需要等待进程结束或再执行一次检查。",
        ])
    if not records and not terminal_launches and not long_running_launches:
        parts.extend(["", "Git diff file names:", "未检测到文件改动。"])
    return "\n".join(parts)


def build_terminal_context_content(info: Dict[str, object]) -> str:
    name = str(info.get("name") or "终端进程").strip()
    cmd = strip_shell_command_marker(str(info.get("cmd") or "")).strip()
    cwd = str(info.get("cwd") or "").strip()
    exit_code = info.get("exit_code")
    pid = info.get("pid") or 0
    log_path = str(info.get("log_path") or "").strip()
    expected_persistent = bool(info.get("expected_persistent"))
    launch_reason = str(info.get("launch_reason") or "").strip()
    kind = str(info.get("command_kind") or "").strip()
    log = str(info.get("log") or "").strip()
    header = [
        "Terminal result:",
        f"name: {name}",
        f"cwd: {cwd or '未知'}",
        f"pid: {pid}",
        f"expected_persistent: {expected_persistent}",
        f"launch_reason: {launch_reason or 'unknown'}",
        f"command_kind: {kind or 'unknown'}",
        f"exit_code: {exit_code}",
        f"log_path: {log_path or 'unavailable'}",
    ]
    if cmd:
        header.append("command:")
        header.append(cmd)
    return "\n".join(header + [
        "",
        low_value_context_block("terminal_log", truncate_middle(log, 8000)),
    ])


AUTOMATION_LOOP_MAX_ROUNDS = env_int("AGENT_QT_AUTOMATION_MAX_ROUNDS", 20, minimum=1)
AUTOMATION_FEEDBACK_CHAR_LIMIT = env_int("AGENT_QT_AUTOMATION_FEEDBACK_CHARS", 14000, minimum=4000)
AUTOMATION_CONTEXT_WINDOW_TOKENS = env_int("AGENT_QT_AUTOMATION_CONTEXT_TOKENS", 1_000_000, minimum=32000)
AUTOMATION_CONTEXT_RESPONSE_RESERVE_TOKENS = env_int("AGENT_QT_AUTOMATION_CONTEXT_RESERVE_TOKENS", 32000, minimum=4000)
AUTOMATION_CONTEXT_COMPACT_TRIGGER_TOKENS = env_int("AGENT_QT_AUTOMATION_COMPACT_TRIGGER_TOKENS", 180000, minimum=20000)
AUTOMATION_CONTEXT_DISPLAY_TOKENS = env_int(
    "AGENT_QT_AUTOMATION_CONTEXT_DISPLAY_TOKENS",
    AUTOMATION_CONTEXT_COMPACT_TRIGGER_TOKENS,
    minimum=20000,
)
AUTOMATION_CONTEXT_COMPACT_RECENT_TOKENS = env_int("AGENT_QT_AUTOMATION_COMPACT_RECENT_TOKENS", 70000, minimum=8000)
AUTOMATION_CONTEXT_COMPACT_SUMMARY_TOKENS = env_int("AGENT_QT_AUTOMATION_COMPACT_SUMMARY_TOKENS", 90000, minimum=12000)
AUTOMATION_CONTEXT_ENTRY_CHAR_LIMIT = env_int("AGENT_QT_AUTOMATION_CONTEXT_ENTRY_CHARS", 16000, minimum=1200)
AUTOMATION_CONTEXT_PROVIDER_PAYLOAD_BYTES = env_int("AGENT_QT_AUTOMATION_PROVIDER_PAYLOAD_BYTES", 175000, minimum=50000)
CHAT_HISTORY_INITIAL_RENDER_ENTRIES = env_int("AGENT_QT_HISTORY_INITIAL_RENDER_ENTRIES", 40, minimum=10)


def iter_non_fenced_lines(text: str):
    in_fence = False
    fence_char = ""
    fence_len = 0
    for line in str(text or "").splitlines():
        stripped = line.lstrip()
        fence_match = re.match(r"^([`~]{3,})(?:[^\r\n]*)?$", stripped)
        if fence_match:
            marker = fence_match.group(1)
            if not in_fence:
                in_fence = True
                fence_char = marker[0]
                fence_len = len(marker)
            elif marker[0] == fence_char and len(marker) >= fence_len:
                in_fence = False
                fence_char = ""
                fence_len = 0
            continue
        if not in_fence:
            yield line


def is_automation_done_response(text: str) -> bool:
    for line in iter_non_fenced_lines(text):
        if COMPLETION_LINE_RE.match(line):
            return True
    return False


def strip_automation_done_marker(text: str) -> str:
    raw = str(text or "")
    if not raw.strip():
        return ""
    marker_pattern = rf"(?:{re.escape(AUTOMATION_DONE_MARKER)}|AGENT_QT_DONE)"
    marker_re = re.compile(rf"(?i)^\s*{marker_pattern}\b\s*:?\s*")
    lines: List[str] = []
    in_fence = False
    fence_char = ""
    fence_len = 0
    for line in raw.splitlines():
        stripped = line.lstrip()
        fence_match = re.match(r"^([`~]{3,})(?:[^\r\n]*)?$", stripped)
        if fence_match:
            marker = fence_match.group(1)
            if not in_fence:
                in_fence = True
                fence_char = marker[0]
                fence_len = len(marker)
            elif marker[0] == fence_char and len(marker) >= fence_len:
                in_fence = False
                fence_char = ""
                fence_len = 0
            lines.append(line)
            continue
        if not in_fence and marker_re.match(line):
            remainder = marker_re.sub("", line, count=1).strip(" \t:-：")
            if remainder:
                lines.append(remainder)
            continue
        lines.append(line)
    return "\n".join(lines).strip()

def looks_like_automation_context_payload(text: str) -> bool:
    content = str(text or "").strip()
    if not content:
        return False
    markers = (
        "第一段：系统提示词",
        "第二段：历史对话",
        "第三段：当前指令",
    )
    marker_count = sum(1 for marker in markers if marker in content)
    if marker_count >= 2:
        return True
    if "第三段：当前指令" in content and re.search(r"```+plaintext", content, re.I) is not None:
        return True
    lowered = content.lower()
    runner_hints = (
        "plain bash agent 模式",
        "backend llm for a local bash-only coding runner",
        "runner 会执行你返回的 fenced bash 终端命令块",
        "no tools are available for this request",
        "session marker: flowflow_system_prompt",
        "continue the existing plain bash agent session already initialized in this chat",
        "follow the previously established bash-only runner instructions",
        "new conversation events since the previous request",
        "conversation:\n\n[user]",
        "conversation:\r\n\r\n[user]",
    )
    runner_hint_count = sum(1 for hint in runner_hints if hint in lowered)
    if runner_hint_count >= 3:
        return True
    if "backend llm for a local bash-only coding runner" in lowered and "conversation:" in lowered:
        return True
    if "plain bash agent 模式" in lowered and "conversation:" in lowered:
        return True
    return False


def truncate_middle(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    marker = "\n\n... 中间日志已截断，保留开头和结尾 ...\n\n"
    head_len = max(1200, limit // 3)
    tail_len = max(1200, limit - head_len - len(marker))
    return text[:head_len] + marker + text[-tail_len:]


def estimate_context_tokens(text: str) -> int:
    """轻量估算上下文 token；DeepSeek 真实 tokenizer 不在本地，UI 用保守近似即可。"""
    if not text:
        return 0
    cjk = 0
    non_cjk = 0
    for ch in text:
        code = ord(ch)
        if (
            0x4E00 <= code <= 0x9FFF
            or 0x3400 <= code <= 0x4DBF
            or 0x3040 <= code <= 0x30FF
            or 0xAC00 <= code <= 0xD7AF
        ):
            cjk += 1
        elif ch.isspace():
            non_cjk += 1
        else:
            non_cjk += 1
    return cjk + max(1, (non_cjk + 3) // 4)


def text_within_token_budget(text: str, token_limit: int) -> str:
    text = str(text or "")
    if token_limit <= 0 or not text:
        return ""
    if estimate_context_tokens(text) <= token_limit:
        return text
    marker = "\n\n... 历史内容已压缩截断，保留开头和最新部分 ...\n\n"
    marker_tokens = estimate_context_tokens(marker)
    available = max(1, token_limit - marker_tokens)
    head_budget = max(1, available // 3)
    tail_budget = max(1, available - head_budget)

    def prefix_by_tokens(value: str, limit: int) -> str:
        lo, hi = 0, len(value)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if estimate_context_tokens(value[:mid]) <= limit:
                lo = mid
            else:
                hi = mid - 1
        return value[:lo].rstrip()

    def suffix_by_tokens(value: str, limit: int) -> str:
        lo, hi = 0, len(value)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if estimate_context_tokens(value[len(value) - mid:]) <= limit:
                lo = mid
            else:
                hi = mid - 1
        return value[len(value) - lo:].lstrip()

    return prefix_by_tokens(text, head_budget) + marker + suffix_by_tokens(text, tail_budget)


def utf8_len(text: str) -> int:
    return len(str(text or "").encode("utf-8"))


def text_within_utf8_budget(text: str, byte_limit: int) -> str:
    text = str(text or "")
    if byte_limit <= 0 or not text:
        return ""
    if utf8_len(text) <= byte_limit:
        return text
    marker = "\n\n... 历史内容已按 DeepSeek 网页输入字节预算压缩，保留开头和最新部分 ...\n\n"
    marker_bytes = utf8_len(marker)
    available = max(1, byte_limit - marker_bytes)
    head_budget = max(4000, available // 3)
    tail_budget = max(4000, available - head_budget)

    def prefix_by_bytes(value: str, limit: int) -> str:
        lo, hi = 0, len(value)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if utf8_len(value[:mid]) <= limit:
                lo = mid
            else:
                hi = mid - 1
        return value[:lo].rstrip()

    def suffix_by_bytes(value: str, limit: int) -> str:
        lo, hi = 0, len(value)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if utf8_len(value[len(value) - mid:]) <= limit:
                lo = mid
            else:
                hi = mid - 1
        return value[len(value) - lo:].lstrip()

    return prefix_by_bytes(text, head_budget) + marker + suffix_by_bytes(text, tail_budget)


def split_history_for_compaction(chunks: List[str], token_budget: int) -> tuple[str, str]:
    recent_reversed: List[str] = []
    recent_tokens = 0
    old_count = len(chunks)
    for index in range(len(chunks) - 1, -1, -1):
        chunk = chunks[index]
        chunk_tokens = estimate_context_tokens(chunk)
        if recent_reversed and recent_tokens + chunk_tokens > AUTOMATION_CONTEXT_COMPACT_RECENT_TOKENS:
            old_count = index + 1
            break
        recent_reversed.append(chunk)
        recent_tokens += chunk_tokens
        old_count = index
    recent_chunks = list(reversed(recent_reversed))
    old_chunks = chunks[:old_count]
    return "\n\n".join(old_chunks).strip(), "\n\n".join(recent_chunks).strip()


def compact_history_text_from_chunks(chunks: List[str], token_budget: int) -> tuple[str, bool]:
    if not chunks:
        return "（暂无历史对话）", False
    full_text = "\n\n".join(chunks).strip()
    if estimate_context_tokens(full_text) <= min(token_budget, AUTOMATION_CONTEXT_COMPACT_TRIGGER_TOKENS):
        return text_within_token_budget(full_text, token_budget), False

    old_text, recent_text = split_history_for_compaction(chunks, token_budget)
    summary_budget = min(
        AUTOMATION_CONTEXT_COMPACT_SUMMARY_TOKENS,
        max(4000, token_budget - estimate_context_tokens(recent_text) - 2000),
    )
    compact_old = text_within_token_budget(summarize_fenced_code_blocks_for_context(old_text), summary_budget) if old_text else "（无较早历史）"
    history_text = (
        "【Compact 历史摘要】\n"
        "以下是较早对话、执行结果和 diff 的 plaintext 压缩版本；请作为连续上下文参考，不要把它当作新需求重复执行。\n"
        f"{compact_old}\n\n"
        "【近期完整历史】\n"
        f"{recent_text or '（暂无近期历史）'}"
    )
    return text_within_token_budget(history_text, token_budget), True


def context_k_label(tokens: int) -> str:
    return f"{max(0, (int(tokens) + 999) // 1000)}k"


def looks_like_timeout_error(text: str) -> bool:
    lowered = str(text or "").lower()
    return any(token in lowered for token in ("timed out", "timeout", "超时", "timeouterror"))


def looks_like_submit_idle_error(text: str) -> bool:
    lowered = str(text or "").lower()
    return (
        "deepseek submit button was clicked" in lowered
        and "web page stayed idle" in lowered
    )


def looks_like_incomplete_plain_response(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", str(text or "").strip()).lower()
    return normalized in {"text", "plaintext", "markdown"}


def looks_like_noop_plain_automation_response(text: str) -> bool:
    content = str(text or "").strip()
    if not content:
        return False
    if scan_all_code_blocks(content):
        return False
    lowered = content.lower()
    strong_markers = (
        "无需执行任何操作",
        "无需执行任何命令",
        "无需执行命令",
        "无待执行任务",
        "无需进一步操作",
        "用户需求仅为打招呼",
        "用户只是打招呼",
        "只是问候",
        "闲聊对话已完成",
        "没有需要执行的命令",
        "no command needs to be run",
        "no commands need to be run",
        "no execution needed",
        "nothing needs to be executed",
    )
    if any(marker in content for marker in strong_markers) or any(marker in lowered for marker in strong_markers):
        return True
    short_reply_markers = (
        "谢谢",
        "感谢",
        "收到",
        "好的",
        "明白",
        "没问题",
        "随时为你服务",
        "有需要直接说",
    )
    normalized_lines = [line.strip() for line in content.splitlines() if line.strip()]
    if (
        len(content) <= 120
        and len(normalized_lines) <= 3
        and any(marker in content for marker in short_reply_markers)
    ):
        return True
    return False


def quiet_automation_error_message(error: str) -> str:
    if looks_like_timeout_error(error):
        return "响应超时，自动化任务已暂停。"
    if looks_like_submit_idle_error(error):
        return "DeepSeek 页面没有开始生成，自动化任务已暂停。可能是页面限流、输入框未接受超长上下文，或提交按钮状态异常。"
    return ""


def looks_like_web_session_busy_error(error: str) -> bool:
    content = str(error or "").strip().lower()
    if not content:
        return False
    markers = (
        "web_session_busy",
        "有消息正在生成，请稍后再试",
        "deepseek web 当前会话仍有消息正在生成",
        "message is being generated",
        "response is being generated",
    )
    return any(marker in content for marker in markers)


def looks_like_web_session_busy_text(text: str) -> bool:
    content = str(text or "").strip().lower()
    if not content or len(content) > 120:
        return False
    markers = (
        "有消息正在生成，请稍后再试",
        "message is being generated",
        "response is being generated",
        "please try again later",
    )
    return any(marker in content for marker in markers)


def provider_retry_status_message(path: str, attempt: int, attempts: int, detail: str = "") -> str:
    target = "网页搜索" if "/v1/responses" in str(path or "") else "AI 回复"
    content = str(detail or "").strip()
    if looks_like_web_session_busy_error(content) or looks_like_web_session_busy_text(content):
        reason = "网页端当前会话仍在生成，正在等待后重试"
    elif "timeout" in content.lower() or "timed out" in content.lower():
        reason = "请求超时，正在重试"
    else:
        reason = "请求波动，正在重试"
    return f"{target}第 {attempt}/{attempts} 次重试：{reason}"


def looks_like_provider_transient_error(error: str) -> bool:
    content = str(error or "").strip()
    lowered = content.lower()
    if looks_like_timeout_error(content) or looks_like_web_session_busy_error(content):
        return True
    transient_markers = (
        "provider 网络请求失败",
        "connection refused",
        "connection reset",
        "temporarily unavailable",
        "service unavailable",
        "bad gateway",
        "gateway timeout",
        "http 408",
        "http 409",
        "http 425",
        "http 429",
        "http 500",
        "http 502",
        "http 503",
        "http 504",
    )
    return any(marker in lowered for marker in transient_markers)


def sanitize_automation_prompt_material(text: str) -> str:
    value = str(text or "")
    replacements = {
        "```": "〔代码块〕",
        "<<<AGENT_QT_LOW_VALUE_CONTEXT_START": "〔低价值上下文开始",
        "<<<AGENT_QT_LOW_VALUE_CONTEXT_END>>>": "低价值上下文结束〕",
        "Execution log:": "执行日志（只读素材）：",
        "Git diff file names:": "文件变更摘要（只读素材）：",
        "Terminal processes:": "后台终端摘要（只读素材）：",
        "【AI 回复】": "【上一轮 AI 回复（只读素材）】",
        "【本地执行结果和文件变更】": "【上一轮执行结果（只读素材）】",
    }
    for src, dst in replacements.items():
        value = value.replace(src, dst)
    return value


def build_automation_feedback_prompt(
    project_root: str,
    goal: str,
    execution_log: str,
    round_number: int,
    max_rounds: int,
    *,
    wechat_file_delivery: bool = False,
    previous_ai_response: str = "",
    force_final_summary: bool = False,
) -> str:
    goal_text = goal.strip() or "用户没有填写一句话需求，请根据前文、执行日志和当前项目状态继续判断。"
    clipped_log = sanitize_automation_prompt_material(truncate_middle(execution_log.strip(), AUTOMATION_FEEDBACK_CHAR_LIMIT))
    clipped_previous_ai = sanitize_automation_prompt_material(truncate_middle(str(previous_ai_response or "").strip(), 4000))
    env = runtime_environment()
    command_block_lang = env["command_block_lang"]
    web_research_followup_note = ""
    log_text = str(execution_log or "")
    if "web research " in log_text.lower() or "AGENT_WEB_RESEARCH" in log_text:
        web_research_followup_note = (
            "\n- 如果上一轮本地执行结果里已经出现 `网页搜索：` 开头的结果，说明 `web research` 已经真的执行完了。"
            " 下一轮不要再次输出 `web research` 命令，也不要解释网页搜索工具协议；"
            "请直接把这些搜索结果当作中间线索，继续完成最初用户任务。"
            "默认把这一步视为“搜索后的继续执行阶段”，而不是完成阶段。"
            "除非用户最初的原话明确只是在让你搜索、调研、整理或解释信息，否则这一轮不要直接输出 AGENT_DONE。"
            "如果你认为搜索结果已经足够，请先用自然语言把当前判断、证据和接下来的完成动作说清楚，再继续原始任务。"
            "只要原始用户需求里还包含下载、生成、修改、保存、发送、验证、运行、安装、整理文件、写脚本、产出文档或任何本地工作区动作，"
            "就必须把任务视为“未完成”，继续输出下一步命令块；不能仅凭搜索结果就结束。"
            "换句话说，搜索只负责提供线索，不代表目标文件、目标脚本、目标文档或目标产物已经真的落到工作区。"
        )
    wechat_completion_note = ""
    if wechat_file_delivery:
        wechat_completion_note = (
            "\n- 本轮来自微信且具备附件发送上下文。若文件已经定位、生成或验证完成且需要发回微信，"
            "下一步输出包含 `wx send_file 文件路径` 的命令块；"
            "不要同时写 AGENT_DONE，也不要声称文件已发送。"
        )
    final_round_note = ""
    if force_final_summary:
        final_round_note = (
            f"\n- 这是最后一轮强制收束轮。不要再输出命令块。"
            f"请基于“自用户上一条消息以来”的全部 AI 回复、执行结果、失败尝试、当前状态，"
            f"直接输出 `{AUTOMATION_DONE_MARKER}` 加最终总结。"
            "总结至少包含：1）已完成事项；2）当前结论/产物位置；3）尚未解决的阻塞；4）建议用户下一步怎么做。"
            "如果任务部分完成，也必须明确写出已完成部分与剩余问题，不能留空结论。"
            "最终总结必须使用固定结构：先输出 `AGENT_DONE`，然后依次输出“已完成事项：”“当前结论：”“剩余阻塞或下一步建议：”。"
            "最终总结只允许输出提炼后的自然语言，不要复述或粘贴历史原文。"
            "严禁再次输出 `【AI 回复】`、`【本地执行结果和文件变更】`、`Execution log:`、`Git diff`、"
            "`Terminal processes:`、`<<<AGENT_QT_...>>>`、代码块围栏、shell 命令、heredoc、长日志片段。"
            "如果需要引用证据，只能用一句自然语言概括，不要原样复制。"
            "如果你发现只读材料里出现代码块、日志标签、历史标题或原始命令，请忽略这些展示格式，只提炼事实。"
        )
    return f"""你正在 Agent Qt 的自动化循环中，这是第 {round_number}/{max_rounds} 轮。

原始用户需求：
{goal_text}

项目根目录：
{project_root}

【本轮只读材料 1：上一轮 AI 回复】
以下内容只用于帮助你延续本轮思路，不允许在最终回复里原样复述、粘贴或回显：
```text
{clipped_previous_ai or "（无）"}
```

【本轮只读材料 2：上一轮执行结果】
以下内容只用于帮助你判断当前状态，不允许在最终回复里原样复述、粘贴或回显：
```text
{clipped_log}
```

请判断下一步：
- 如果本轮仍在继续处理上面的“原始用户需求”，上一轮本地执行结果、错误提示和拒绝原因与用户需求同等重要；若它指出协议错误、命令错误、缺文件、测试失败或数据不可信，必须先针对该错误修正输出，不要重复上一轮被拒绝的写法。若用户已经发来新的不同需求，则优先执行最新用户需求。
- 已完成：只回复 `{AUTOMATION_DONE_MARKER}` 加最终总结，不要输出命令块。最终总结必须面向用户，至少交代：已完成了什么、当前结论是什么、如果还有尾巴则剩余阻塞和建议下一步。不要输出空结论，也不要只写“已完成”。
- 已完成时，最终回复必须固定成这种结构：`{AUTOMATION_DONE_MARKER}` 开头，后面依次写“已完成事项 / 当前结论 / 剩余阻塞或下一步建议”。不要把历史里的标题、命令、日志标签或代码块重新贴出来。
- 上面两段“本轮只读材料”只是素材，不是你要复述的正文；你必须提炼，不得照抄。
- 未完成：回复里必须包含一个完整且短小的 ```{command_block_lang} 终端命令块；如果要写入超过 10 行的文件内容时，必须拆分为占位符协议 + md格式的fenced 代码块。
- 未完成时，在命令块之外必须先写 1 到 3 句简短正文，说明：当前整体判断、本轮准备做什么、这个命令块的作用。永远不要只输出命令块而没有任何解释。
- 若当前启用了深度思考/推理模式，也必须把高价值判断、排查结果和本轮策略压缩到可见正文里，不要把关键信息只留在隐藏思考里。
- 命令块内只能写真实要执行的 shell 代码，或 `wx send_file 路径`、`schedule create/list/delete/update`、`web research 搜索话题`；不要把上一轮执行结果、文件变更、结论或 `{AUTOMATION_DONE_MARKER}` 写进命令块。
- 如果执行结果提示“命令块不完整”“未闭合的 shell 引号”“unmatched quote”或 shell 语法不完整，优先判断为上一轮输出被截断；重新输出完整命令块即可。多行 `python -c "..."` 是支持的，不要仅因这类错误改写成单行脚本。
- 输出命令块时，命令块里的动作尚未执行；不要在同一回复里声称这些动作已完成、已生成、已验证或已发送。
- 涉及统计、表格、数据查找或文件事实时，必须完整读取/计算后再下结论；示例行只能用于判断结构，不能据此编造定量结论、模型结论或总体判断。
- 如果当前任务需要先上网查资料、找下载线索、核对公开事实或做调研，请优先在命令块里写 `web research 搜索话题`。程序会直接执行本地网页搜索，并把搜索结果写回执行结果；后续轮次可以直接基于这些结果继续完成用户任务。不要为了搜索或调研而生成本地 curl/wget/python 抓取脚本，除非用户明确要求你写脚本，或目标数据只存在当前工作区/本机文件里。
- 后台安装/构建/拉取不要当作完成；启动常驻命令时不要加 `&`/`nohup`，等执行结果给出 `Terminal processes:` 摘要后，再使用 `curl -s 'http://127.0.0.1:8798/terminallogs?pid=xxx'` 查询控制台输出。
- 对下载、联网 HTTP 调用、安装、构建、长时间生成等任务，如果已经转入后台或暂时无输出，不要立刻换方案。优先先等待一小段合理时间，并主动查看终端/后台日志；必要时可以使用短暂等待后再查询日志，确认失败后再换方案。
- 优先修复日志错误、补齐缺失文件、做必要验证；不要重复成功步骤，不要给备用方案，不要输出 JSON/tool_calls。
{web_research_followup_note}
{wechat_completion_note}
{final_round_note}
"""

WECHAT_COMMAND_MENU_TEXT = """你可以这样说：
/ 或 /help：查看这份说明
/stop：停止 AI 输出或本地执行
/threads：查看所有会话
切换到 会话ID：切换会话
新建会话 名称：创建一个新会话
显示文件列表：返回当前工作区的多层级文本树
/schedule：查看当前计划
/model：查看当前模型和可选模型
直接发送模型名称或用自然语言说明要切换到哪个模型：由 Agent 切换模型
直接描述提醒或计划：由 Agent 创建一次性或循环计划
删除计划 名称/序号：由 Agent 删除对应计划；也可用 /delete_schedule 名称
发送文件 路径/文件名：把工作区文件发到微信

也可以直接发送自然语言需求，让 Agent Qt 处理当前工作区。"""


def is_wechat_menu_command(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    return normalized in {"/", "/help", "/ help", "help", "/menu", "menu", "菜单", "帮助", "指令", "/指令", "/菜单", "/帮助"}


def automation_model_label(model_id: str) -> str:
    target = str(model_id or "")
    for label, candidate in AUTOMATION_MODELS:
        if candidate == target:
            return label
    return target or AUTOMATION_DEFAULT_MODEL


def automation_thinking_enabled(model_id: str) -> bool:
    text = f"{automation_model_label(model_id)} {model_id}".lower()
    return "thinking" in text or "推理" in text


def automation_model_for_thinking(enabled: bool, current_model: str) -> str:
    current = str(current_model or AUTOMATION_DEFAULT_MODEL)
    if enabled:
        if automation_thinking_enabled(current):
            return current
        for label, model_id in AUTOMATION_MODELS:
            if "thinking" in label.lower() or automation_thinking_enabled(model_id):
                return model_id
        return current
    if not automation_thinking_enabled(current):
        return current
    for _label, model_id in AUTOMATION_MODELS:
        if not automation_thinking_enabled(model_id):
            return model_id
    return AUTOMATION_DEFAULT_MODEL


def automation_model_options_text() -> str:
    lines = [f"当前模型：{automation_context_mode_label_for_state()}" ]
    lines.append("可选模型：")
    for preset in AUTOMATION_CONTEXT_PRESETS:
        lines.append(f"- {str(preset.get('label') or '').strip()}")
    lines.append("可以直接回复模型名称，或说“切换到 DeepSeek PRO web thinking”这类自然语言。")
    return "\n".join(line for line in lines if line).strip()


def normalize_preset_text(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "").strip().lower())


def resolve_automation_preset_from_text(text: str) -> Optional[Dict[str, str]]:
    raw = str(text or "").strip()
    if not raw:
        return None
    normalized = normalize_preset_text(raw)
    if not any(token in normalized for token in ("deepseek", "flash", "thinking", "模型", "/model", "切换")):
        return None
    best = None
    best_len = -1
    for preset in AUTOMATION_CONTEXT_PRESETS:
        label = str(preset.get("label") or "").strip()
        label_norm = normalize_preset_text(label)
        if not label_norm:
            continue
        if normalized == label_norm or label_norm in normalized:
            if len(label_norm) > best_len:
                best = {"mode": str(preset.get("mode") or "expert"), "model": str(preset.get("model") or AUTOMATION_DEFAULT_MODEL), "label": label}
                best_len = len(label_norm)
    return best


def automation_context_mode_label_for_state(mode: str = "", model_id: str = "") -> str:
    target_mode = str(mode or "") or None
    target_model = str(model_id or "") or None
    for preset in AUTOMATION_CONTEXT_PRESETS:
        if (target_mode is None or str(preset.get("mode") or "") == target_mode) and (target_model is None or str(preset.get("model") or "") == target_model):
            return str(preset.get("label") or "")
    return automation_model_label(model_id or AUTOMATION_DEFAULT_MODEL)


def parse_wechat_builtin_command(text: str) -> Dict[str, str]:
    raw = str(text or "").strip()
    compact = re.sub(r"\s+", "", raw).lower()
    if not compact:
        return {}
    model_match = re.match(r"^/model(?:[:：\s]+(.*))?$", raw, re.I)
    if model_match:
        target = (model_match.group(1) or "").strip()
        if target:
            return {"action": "model", "target": target}
        return {"action": "model"}
    if compact in {"/stop", "停止", "暂停", "中断", "停止当前任务", "暂停当前任务", "停止输出", "停止执行"}:
        return {"action": "stop"}
    if compact in {"/threads", "/conversations", "/ls", "会话列表", "对话列表", "列出会话", "列出对话", "显示会话", "显示对话"}:
        return {"action": "threads"}
    if compact in {"/files", "/tree", "文件列表", "文件树", "目录树", "显示文件", "显示文件夹", "显示目录", "列出文件", "列出目录", "有哪些文件", "项目文件"}:
        return {"action": "project_tree"}
    if compact in {"/schedule", "/schedules"}:
        return {"action": "schedules"}
    delete_schedule_match = re.match(r"^(?:/delete_schedule|/del_schedule)[:：\s]+(.+)$", raw, re.I)
    if delete_schedule_match:
        return {"action": "delete_schedule", "target": (delete_schedule_match.group(1) or "").strip()}
    send_file_match = re.match(r"^/sendfile[:：\s]+(.+)$", raw, re.I)
    if send_file_match:
        target = (send_file_match.group(1) or "").strip()
        return {"action": "send_file", "target": target}
    new_match = re.match(r"^/new(?:[:：\s]+(.*))?$", raw, re.I)
    if new_match:
        return {"action": "new_thread", "title": (new_match.group(1) or "").strip() or "微信会话"}
    select_match = re.match(r"^/select[:：\s]+(.+)$", raw, re.I)
    if select_match:
        return {"action": "select_thread", "thread_id": (select_match.group(1) or "").strip()}
    preset = resolve_automation_preset_from_text(raw)
    if preset:
        return {"action": "model", "target": str(preset.get("label") or "")}
    return {}


def build_wechat_user_prompt(text: str, allow_file_delivery: bool = True) -> str:
    now = datetime.now()
    file_delivery_note = ""
    if allow_file_delivery:
        file_delivery_note = (
            "如果用户要求查看图片、PDF、表格或文档，先自主使用文件树、搜索、读取文件或必要命令定位/生成目标文件；"
            "只有探索后仍缺少关键条件、或存在多个同样合理候选时，才简短向微信用户追问。"
            "如果已经确定需要把工作区文件作为微信附件发回用户，在命令块中写 `wx send_file 文件路径`。"
            "多个文件路径用英文逗号分隔；没有这行就不会发送文件。"
            "`wx send_file` 只是发送请求，不代表已经发送完成；输出该命令时不要同时说已发送。"
        )
    env = runtime_environment()
    return (
        "【微信远控消息】\n"
        f"当前时间：{now.strftime('%Y-%m-%d %H:%M:%S')}\n"
        "用户从手机微信发送以下需求。你负责理解自然语言并主动完成；需要程序配合时，请使用下面的终端扩展指令。\n"
        "可用终端扩展指令：\n"
        "- 要执行本地工作：输出一个完整且短小的 "
        f"```{env['command_block_lang']} 命令块。\n"
        "- 要把工作区文件发回微信：先自主定位/生成文件；路径确定后输出一个命令块，使用 `wx send_file 文件路径`。多个路径用英文逗号分隔；写出该指令后由程序发送，AI 不要在同一回复声称已发送。\n"
        "- 要创建时间计划：输出一个命令块，在其中写 `schedule create JSON`。\n"
        "  JSON 结构：{\"title\":\"短标题\",\"prompt\":\"到点后真正要做的事，不要写帮我创建计划\",\"trigger\":{\"run_at\":\"YYYY-MM-DD HH:MM:SS\",\"repeat_every_seconds\":86400,\"until_at\":\"YYYY-MM-DD HH:MM:SS\"}}\n"
        "  `run_at` 是模型根据当前时间和用户表达算出的下一次具体触发时间；一次性计划省略 `repeat_every_seconds` 或设为 0。每日/每周/每隔几小时等循环计划也只输出下一次 `run_at`，并用秒数表达循环间隔，例如每天 86400、每周 604800、每 2 小时 7200。有截止范围时输出 `until_at`，例如“接下来 5 个小时每小时检查”就是下一小时触发、repeat 3600、until_at=当前时间+5小时。\n"
        "  如果用户同时提出多个不同频率的周期任务，优先拆成多条 `schedule create JSON`，不要在单个计划里用脚本计数器模拟另一个周期。\n"
        "  如果无法确定时间、任务或触发参数，先做低成本探索，仍不确定再简短提问，不要输出半截 trigger。\n"
        "- 要查看、删除或修改时间计划：输出命令块。查看写 `schedule list`；删除写 `schedule delete 计划名称、序号或 id`；修改写 `schedule update JSON`，JSON 结构：{\"target\":\"计划名称、序号或 id\",\"enabled\":true,\"trigger\":{\"run_at\":\"YYYY-MM-DD HH:MM:SS\",\"repeat_every_seconds\":3600,\"until_at\":\"YYYY-MM-DD HH:MM:SS\"},\"prompt\":\"可选的新计划内容\",\"title\":\"可选的新标题\"}。`enabled:false` 表示暂停；`enabled:true` 且不带 `trigger` 表示开启并立即安排执行一次；如果只想恢复到未来某个时间，必须同时给 `trigger.run_at`。修改时只写需要变化的字段。如果目标不明确，先简短追问，不要猜。\n"
        "- 要停止/切会话/列列表等控制动作：用户使用 slash/menu 指令时程序会直接处理；自然语言里提到时，你可以解释可用指令。\n"
        "- 如果用户只是问候、闲聊或普通问答，不需要本地命令、文件或计划，直接用简短自然语言回复；不要为了获取时间或构造问候去执行 echo/date。\n"
        "- 如果用户要你做搜索或调研，而不是操作本地工作区，请优先输出一个命令块，写 `web research 搜索话题`；程序会直接执行本地网页搜索，把结果写回上下文，下一轮你再基于这些搜索结果自然语言作答。不要为了搜索或调研而生成本地 curl/wget/python 抓取脚本，除非用户明确要求脚本，或目标数据只在当前工作区/本机文件里。\n"
        "- `skill list` 是内置终端扩展指令，用于查看当前工作区已有技能。skill 是一种经验、SOP、方法论的封装，至少包含一个 `SKILL.md`，目录里还可能有补充的 Markdown、脚本、图像等材料，可按需继续读取。\n"
        "- 如果用户主动提到 skill/技能/技巧，或询问“你有什么技能”“有哪些 skill”“介绍一下技能”“当前可用技能是什么”等与技能列表相关的问题，请优先输出一个命令块，写 `skill list`，先查看当前工作区已有技能；程序会把技能名称、摘要和 SKILL.md 路径写回上下文，下一轮你再决定读取哪个技能文件以及是否继续读取技能目录里的补充材料。不要先主观回答没有这种内置指令或没有加载技能。\n"
        "如果用户目标不清楚，先做低成本探索；探索后仍缺少关键条件或候选无法判断时，再简短提问。最终给微信的回复会被压缩展示，所以结论要短。"
        f"{file_delivery_note}"
        "如果用户询问菜单、帮助或支持哪些指令，请直接说明这些指令，不要执行项目命令。\n\n"
        f"用户消息：\n{text.strip()}"
    )


def plaintext_fence(title: str, content: str) -> str:
    safe_content = str(content or "").strip()
    longest = max((len(match.group(0)) for match in re.finditer(r"`{3,}", safe_content)), default=2)
    fence = "`" * max(3, longest + 1)
    return f"{title}\n{fence}plaintext\n{safe_content}\n{fence}"


def unwrap_provider_text(text: str) -> str:
    """Recover the actual assistant text if a provider still returns a JSON envelope."""
    current = (text or "").strip()
    for _ in range(3):
        candidate = strip_single_outer_fenced_block(current, preferred_langs={"json", ""}).strip()
        if not candidate.startswith("{"):
            return current
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            return current
        if not isinstance(payload, dict):
            return current
        next_text = None
        if isinstance(payload.get("content"), str):
            next_text = payload["content"]
        elif isinstance(payload.get("message"), dict) and isinstance(payload["message"].get("content"), str):
            next_text = payload["message"]["content"]
        else:
            try:
                next_text = payload["choices"][0]["message"]["content"]
            except Exception:
                next_text = None
        if not isinstance(next_text, str) or next_text.strip() == current:
            return current
        current = next_text.strip()
    return current

def render_diff_html(record: Dict[str, object]) -> str:
    rows = record.get("diff_rows") or []
    page_bg = COLORS["code_bg"]
    page_text = COLORS["text"]
    text_secondary = COLORS["text_secondary"]
    if not rows:
        return (
            f"<html><body style='margin:0; background:{page_bg}; font-family: Menlo, monospace; color: {page_text};'>"
            f"<pre>{html.escape(str(record.get('diff', '')))}</pre>"
            "</body></html>"
        )

    html_rows = [
        f"<html><body style='margin:0; background:{page_bg};'>",
        "<table cellspacing='0' cellpadding='0' width='100%' "
        "style='border-collapse:collapse; font-family: Menlo, monospace; font-size:12px;'>",
    ]
    dark_theme = app_theme_setting() == "dark"
    for row in rows:
        row_type = row.get("type")
        text = html.escape(str(row.get("text", ""))).replace(" ", "&nbsp;")
        old_num = html.escape(str(row.get("old", "")))
        new_num = html.escape(str(row.get("new", "")))
        if row_type == "add":
            if dark_theme:
                bg = "#0f2b22"
                border = "#19c37d"
                num_color = "#5fe3a8"
                text_color = "#dcfff0"
            else:
                bg = "#e6f6ed"
                border = "#12b76a"
                num_color = "#079455"
                text_color = page_text
            marker = "+"
        elif row_type == "del":
            if dark_theme:
                bg = "#33171b"
                border = "#ff6b7a"
                num_color = "#ff95a1"
                text_color = "#ffe4e7"
            else:
                bg = "#ffecec"
                border = "#ef4444"
                num_color = "#dc2626"
                text_color = page_text
            marker = "-"
        elif row_type == "hunk":
            if dark_theme:
                bg = "#182235"
                border = "#7598ff"
                num_color = "#9db6ff"
                text_color = "#dbe5ff"
            else:
                bg = "#eef4ff"
                border = "#8ea8ff"
                num_color = text_secondary
                text_color = page_text
            marker = " "
        else:
            bg = page_bg
            border = page_bg
            num_color = text_secondary
            text_color = page_text
            marker = " "
        html_rows.append(
            f"<tr style='background:{bg};'>"
            f"<td width='48' style='color:{num_color}; text-align:right; padding:3px 8px; border-left:4px solid {border};'>{old_num}</td>"
            f"<td width='48' style='color:{num_color}; text-align:right; padding:3px 8px;'>{new_num}</td>"
            f"<td width='18' style='color:{num_color}; padding:3px 4px;'>{marker}</td>"
            f"<td style='color:{text_color}; padding:3px 8px;'>{text}</td>"
            "</tr>"
        )
    html_rows.extend(["</table>", "</body></html>"])
    return "".join(html_rows)

def apply_change_records(root: str, records: List[Dict[str, object]], target_key: str, expected_key: str) -> Dict[str, object]:
    conflicts = []
    for record in records:
        rel = str(record["path"])
        if record.get("undoable") is False:
            conflicts.append(rel)
            continue
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

def workspace_state_path(root: str) -> str:
    return os.path.join(root, HISTORY_DIR_NAME, WORKSPACE_STATE_FILE_NAME)

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

def load_last_thread_id(root: str, threads: List[Dict[str, object]]) -> str:
    valid_thread_ids = {str(thread.get("id")) for thread in normalize_threads(threads)}
    path = workspace_state_path(root)
    if not os.path.isfile(path):
        return DEFAULT_THREAD_ID
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, json.JSONDecodeError):
        return DEFAULT_THREAD_ID
    if payload.get("version") != HISTORY_VERSION:
        return DEFAULT_THREAD_ID
    thread_id = safe_thread_id(str(payload.get("last_thread_id", DEFAULT_THREAD_ID)))
    return thread_id if thread_id in valid_thread_ids else DEFAULT_THREAD_ID

def save_last_thread_id(root: str, thread_id: str) -> bool:
    if not root:
        return False
    try:
        os.makedirs(os.path.join(root, HISTORY_DIR_NAME), exist_ok=True)
        path = workspace_state_path(root)
        tmp_path = path + ".tmp"
        payload = {
            "version": HISTORY_VERSION,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "last_thread_id": safe_thread_id(thread_id),
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

def rename_workspace_thread(root: str, thread_id: str, title: str, threads: List[Dict[str, object]]) -> bool:
    thread_id = safe_thread_id(thread_id)
    title = title.strip()
    if not thread_id or not title:
        return False
    updated = []
    found = False
    for thread in normalize_threads(threads):
        item = dict(thread)
        if str(item.get("id")) == thread_id:
            item["title"] = title
            found = True
        updated.append(item)
    if not found:
        return False
    return save_workspace_threads(root, updated)

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


def ensure_workspace_thread(root: str, threads: List[Dict[str, object]], thread_id: str, title: str) -> Dict[str, object]:
    safe_id = safe_thread_id(thread_id)
    normalized = normalize_threads(threads)
    for thread in normalized:
        if str(thread.get("id")) == safe_id:
            return thread
    thread = {
        "id": safe_id,
        "title": title.strip() or "微信会话",
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    save_workspace_threads(root, [*normalized, thread])
    os.makedirs(history_dir(root, safe_id), exist_ok=True)
    return thread


def project_tree_text(root: str, max_depth: int = 4, max_entries: int = 180) -> str:
    root = os.path.abspath(os.path.expanduser(root or ""))
    if not root or not os.path.isdir(root):
        return "当前工作区目录不可用。"
    skip_dirs = {".git", ".agent_qt", "__pycache__", "node_modules", ".venv", "venv", "dist", "build", ".next"}
    lines = [os.path.basename(root) or root]
    counts = {"dirs": 0, "files": 0, "omitted": 0}

    def visible_entries(path: str):
        try:
            names = sorted(os.listdir(path), key=lambda value: (not os.path.isdir(os.path.join(path, value)), value.lower()))
        except OSError:
            return []
        result = []
        for name in names:
            if name.startswith(".") and name not in {".env", ".gitignore"}:
                continue
            full = os.path.join(path, name)
            if os.path.isdir(full) and name in skip_dirs:
                continue
            result.append((name, full))
        return result

    def walk(path: str, prefix: str = "", depth: int = 0):
        if len(lines) >= max_entries:
            counts["omitted"] += 1
            return
        if depth >= max_depth:
            remaining = len(visible_entries(path))
            if remaining:
                lines.append(f"{prefix}└── ...（还有 {remaining} 项）")
                counts["omitted"] += remaining
            return
        entries = visible_entries(path)
        for index, (name, full) in enumerate(entries):
            if len(lines) >= max_entries:
                counts["omitted"] += len(entries) - index
                break
            last = index == len(entries) - 1
            branch = "└── " if last else "├── "
            if os.path.isdir(full):
                counts["dirs"] += 1
                lines.append(f"{prefix}{branch}{name}/")
                walk(full, prefix + ("    " if last else "│   "), depth + 1)
            else:
                counts["files"] += 1
                lines.append(f"{prefix}{branch}{name}")

    walk(root)
    summary = f"目录 {counts['dirs']} 个，文件 {counts['files']} 个"
    if counts["omitted"]:
        summary += f"，省略 {counts['omitted']} 项"
    return summary + "\n" + "\n".join(lines)


def resolve_project_file_target(root: str, target: str) -> str:
    root = os.path.abspath(os.path.expanduser(root or ""))
    raw = str(target or "").strip().strip("\"'")
    if not root or not os.path.isdir(root) or not raw:
        return ""
    candidates = []
    expanded = os.path.abspath(os.path.expanduser(raw))
    if os.path.isfile(expanded):
        candidates.append(expanded)
    candidates.append(os.path.abspath(os.path.join(root, raw)))
    raw_name = os.path.basename(raw)
    for candidate in candidates:
        try:
            if os.path.isfile(candidate) and os.path.commonpath([root, candidate]) == root:
                return candidate
        except ValueError:
            continue
    matches: List[Tuple[int, float, str]] = []
    needle = raw.lower()
    name_needle = raw_name.lower()
    skip_dirs = {".git", ".agent_qt", "__pycache__", "node_modules", ".venv", "venv", "dist", "build", ".next"}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if name not in skip_dirs and not name.startswith(".")]
        for filename in filenames:
            full = os.path.join(dirpath, filename)
            rel = os.path.relpath(full, root)
            rel_lower = rel.lower()
            filename_lower = filename.lower()
            if rel_lower == needle or filename_lower == needle or name_needle and filename_lower == name_needle:
                return full
            if needle and (needle in rel_lower or needle in filename_lower):
                matches.append((10, os.path.getmtime(full), full))
                continue
        if len(matches) > 20:
            break
    if not matches:
        return ""
    return sorted(matches, key=lambda item: (item[0], -item[1], len(os.path.relpath(item[2], root)), os.path.relpath(item[2], root).lower()))[0][2]


def schedules_path(root: str) -> str:
    return os.path.join(root, HISTORY_DIR_NAME, SCHEDULES_FILE_NAME)


def safe_schedule_id(schedule_id: str = "") -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(schedule_id or "").strip().lower()).strip("-_")
    return cleaned[:72] or f"schedule-{datetime.now().strftime('%Y%m%d-%H%M%S')}"


def schedule_lookup_key(text: str = "") -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "-", str(text or "").strip().lower()).strip("-_")[:72]


def parse_schedule_datetime(value: object) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    text = text.replace("T", " ").replace("/", "-")
    if text.endswith("Z"):
        text = text[:-1].strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(text[:19] if fmt.endswith("%S") else text[:16] if fmt.endswith("%M") else text[:10], fmt)
            if fmt == "%Y-%m-%d":
                return parsed.replace(hour=9, minute=0)
            return parsed
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def format_schedule_datetime(value: object) -> str:
    parsed = value if isinstance(value, datetime) else parse_schedule_datetime(value)
    return parsed.strftime("%Y-%m-%d %H:%M:%S") if parsed else ""


def normalize_repeat_seconds(value: object) -> int:
    if value in (None, "", False):
        return 0
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return 0
    return seconds if seconds > 0 else 0


def next_daily_run_at(hour: int, minute: int, now: Optional[datetime] = None) -> datetime:
    now = now or datetime.now()
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


def normalize_schedule_spec(schedule: Dict[str, object], now: Optional[datetime] = None) -> Optional[Dict[str, object]]:
    now = now or datetime.now()
    run_at = parse_schedule_datetime(
        schedule.get("run_at")
        or schedule.get("timestamp")
        or schedule.get("at")
        or schedule.get("next_run_at")
    )
    repeat_seconds = normalize_repeat_seconds(
        schedule.get("repeat_every_seconds")
        or schedule.get("repeat_seconds")
        or schedule.get("interval_seconds")
    )
    if not repeat_seconds and schedule.get("interval_minutes") not in (None, ""):
        repeat_seconds = normalize_repeat_seconds(int(schedule.get("interval_minutes") or 0) * 60)
    if not run_at and schedule.get("hour") is not None:
        try:
            hour = int(schedule.get("hour"))
            minute = int(schedule.get("minute", 0))
        except (TypeError, ValueError):
            return None
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            return None
        run_at = next_daily_run_at(hour, minute, now)
        repeat_seconds = repeat_seconds or 86400
    if not run_at:
        return None
    normalized: Dict[str, object] = {"run_at": format_schedule_datetime(run_at)}
    if repeat_seconds:
        normalized["repeat_every_seconds"] = repeat_seconds
    until_at = format_schedule_datetime(schedule.get("until_at") or schedule.get("stop_at") or schedule.get("end_at"))
    if until_at:
        normalized["until_at"] = until_at
    return normalized


def format_repeat_seconds(seconds: int) -> str:
    seconds = int(seconds or 0)
    units = (
        (604800, "周"),
        (86400, "天"),
        (3600, "小时"),
        (60, "分钟"),
    )
    for unit_seconds, label in units:
        if seconds >= unit_seconds and seconds % unit_seconds == 0:
            count = seconds // unit_seconds
            return f"每 {count} {label}"
    return f"每 {seconds} 秒"


def format_schedule_spec(schedule: Dict[str, object]) -> str:
    run_at = format_schedule_datetime(schedule.get("run_at"))
    repeat_seconds = normalize_repeat_seconds(schedule.get("repeat_every_seconds"))
    if repeat_seconds:
        suffix = f"，截止 {format_schedule_datetime(schedule.get('until_at'))}" if schedule.get("until_at") else ""
        return f"{format_repeat_seconds(repeat_seconds)}，下次 {run_at}{suffix}"
    return run_at or "一次性计划"


def normalize_schedule(raw: object) -> Optional[Dict[str, object]]:
    if not isinstance(raw, dict):
        return None
    schedule_id = safe_schedule_id(str(raw.get("id") or ""))
    title = str(raw.get("title") or raw.get("name") or "定时计划").strip() or "定时计划"
    prompt = str(raw.get("prompt") or raw.get("content") or "").strip()
    schedule = normalize_schedule_spec(dict(raw.get("schedule") or {}))
    notify_thread_id = str(raw.get("notify_wechat_thread_id") or "").strip()
    if not schedule or not prompt:
        return None
    return {
        "id": schedule_id,
        "title": title[:80],
        "prompt": prompt,
        "enabled": bool(raw.get("enabled", True)),
        "schedule": schedule,
        "schedule_text": str(raw.get("schedule_text") or format_schedule_spec(schedule)).strip(),
        "created_at": str(raw.get("created_at") or datetime.now().isoformat(timespec="seconds")),
        "updated_at": str(raw.get("updated_at") or datetime.now().isoformat(timespec="seconds")),
        "last_run_key": str(raw.get("last_run_key") or ""),
        "last_run_at": str(raw.get("last_run_at") or ""),
        "notify_wechat_user": str(raw.get("notify_wechat_user") or ""),
        "notify_wechat_context_token": str(raw.get("notify_wechat_context_token") or ""),
        "notify_wechat_thread_id": safe_thread_id(notify_thread_id) if notify_thread_id else "",
        "notify_wechat_enabled": bool(raw.get("notify_wechat_enabled", False)),
        "last_success_note": str(raw.get("last_success_note") or ""),
        "expired_at": str(raw.get("expired_at") or ""),
    }


def load_workspace_schedules(root: str) -> List[Dict[str, object]]:
    path = schedules_path(root)
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    if payload.get("version") != HISTORY_VERSION:
        return []
    schedules: List[Dict[str, object]] = []
    for raw in payload.get("tasks") or []:
        schedule = normalize_schedule(raw)
        if schedule:
            schedules.append(schedule)
    for raw in payload.get("schedules") or []:
        schedule = normalize_schedule(raw)
        if schedule and not any(item.get("id") == schedule.get("id") for item in schedules):
            schedules.append(schedule)
    schedules.sort(key=lambda item: str(item.get("created_at") or ""))
    return schedules


def save_workspace_schedules(root: str, schedules: List[Dict[str, object]]) -> bool:
    if not root:
        return False
    normalized = [schedule for schedule in (normalize_schedule(item) for item in schedules) if schedule]
    try:
        os.makedirs(os.path.join(root, HISTORY_DIR_NAME), exist_ok=True)
        path = schedules_path(root)
        tmp_path = path + ".tmp"
        payload = {
            "version": HISTORY_VERSION,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "schedules": normalized,
        }
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
        return True
    except OSError:
        return False


def create_workspace_schedule_from_spec(root: str, title: str, prompt: str, schedule: Dict[str, object], enabled: bool = True) -> Dict[str, object]:
    schedules = load_workspace_schedules(root)
    now = datetime.now().isoformat(timespec="seconds")
    normalized_spec = normalize_schedule_spec(schedule)
    if not normalized_spec:
        raise RuntimeError("计划触发器不完整。")
    base = safe_schedule_id(title or f"{normalized_spec.get('type')}-schedule")
    existing = {str(schedule.get("id") or "") for schedule in schedules}
    schedule_id = base
    suffix = 2
    while schedule_id in existing:
        schedule_id = f"{base}-{suffix}"
        suffix += 1
    schedule_item = {
        "id": schedule_id,
        "title": (title.strip() or "定时计划")[:80],
        "prompt": prompt.strip(),
        "enabled": bool(enabled),
        "schedule": normalized_spec,
        "schedule_text": format_schedule_spec(normalized_spec),
        "created_at": now,
        "updated_at": now,
        "last_run_key": "",
        "last_run_at": "",
    }
    normalized = normalize_schedule(schedule_item)
    if not normalized:
        raise RuntimeError("计划内容不完整。")
    if not save_workspace_schedules(root, [*schedules, normalized]):
        raise RuntimeError("保存计划失败。")
    return normalized


def create_workspace_schedule(root: str, title: str, prompt: str, hour: int, minute: int, enabled: bool = True) -> Dict[str, object]:
    return create_workspace_schedule_from_spec(
        root,
        title,
        prompt,
        {"hour": int(hour), "minute": int(minute), "repeat_every_seconds": 86400},
        enabled,
    )


def schedule_from_wechat_trigger_payload(
    root: str,
    payload: object,
    *,
    notify_user: str = "",
    notify_context_token: str = "",
    notify_thread_id: str = "",
) -> Dict[str, object]:
    if not isinstance(payload, dict):
        raise RuntimeError("计划触发器不是 JSON 对象。")
    title = str(payload.get("title") or payload.get("name") or "定时计划").strip()[:80] or "定时计划"
    prompt = str(payload.get("prompt") or payload.get("content") or "").strip()
    trigger = payload.get("trigger") or payload.get("schedule")
    if not isinstance(trigger, dict):
        raise RuntimeError("计划缺少 trigger。")
    if not prompt:
        raise RuntimeError("计划缺少 prompt。")
    schedule_item = create_workspace_schedule_from_spec(root, title, prompt, trigger, bool(payload.get("enabled", True)))
    if notify_user and notify_context_token:
        notify_thread_id = str(notify_thread_id or "").strip()
        update_workspace_schedule(root, str(schedule_item.get("id") or ""), {
            "notify_wechat_enabled": True,
            "notify_wechat_user": notify_user,
            "notify_wechat_context_token": notify_context_token,
            "notify_wechat_thread_id": safe_thread_id(notify_thread_id) if notify_thread_id else "",
        })
        schedule_item = next(
            (
                item for item in load_workspace_schedules(root)
                if str(item.get("id") or "") == str(schedule_item.get("id") or "")
            ),
            schedule_item,
        )
    return schedule_item


def update_workspace_schedule(root: str, schedule_id: str, patch: Dict[str, object]) -> bool:
    schedules = load_workspace_schedules(root)
    safe_id = safe_schedule_id(schedule_id)
    updated = []
    found = False
    for schedule_item in schedules:
        if str(schedule_item.get("id") or "") == safe_id:
            merged = dict(schedule_item)
            merged.update(patch)
            merged["updated_at"] = datetime.now().isoformat(timespec="seconds")
            schedule_item = merged
            found = True
        updated.append(schedule_item)
    return found and save_workspace_schedules(root, updated)


def schedule_success_note(text: str) -> str:
    content = strip_automation_done_marker(str(text or "")).strip()
    if not content:
        return ""
    content = re.sub(r"\n{3,}", "\n\n", content)
    return truncate_middle(content, 900)


def resolve_schedule_target(schedules: List[Dict[str, object]], target: str) -> str:
    raw = str(target or "").strip()
    if not raw:
        return ""
    if raw.isdigit():
        index = int(raw) - 1
        if 0 <= index < len(schedules):
            return str(schedules[index].get("id") or "")
    safe_target = schedule_lookup_key(raw)
    for schedule_item in schedules:
        schedule_id = str(schedule_item.get("id") or "")
        title = str(schedule_item.get("title") or "")
        if raw == schedule_id or (safe_target and safe_target == schedule_id) or raw == title:
            return schedule_id
    for schedule_item in schedules:
        schedule_id = str(schedule_item.get("id") or "")
        title = str(schedule_item.get("title") or "")
        if raw in title or raw in schedule_id:
            return schedule_id
    return ""


def delete_workspace_schedule(root: str, schedule_id: str) -> bool:
    schedules = load_workspace_schedules(root)
    resolved = resolve_schedule_target(schedules, schedule_id)
    if not resolved:
        return False
    remaining = [schedule for schedule in schedules if str(schedule.get("id") or "") != resolved]
    return len(remaining) != len(schedules) and save_workspace_schedules(root, remaining)


def update_workspace_schedule_from_action(root: str, target: str, payload: Dict[str, object]) -> Dict[str, object]:
    schedules = load_workspace_schedules(root)
    resolved = resolve_schedule_target(schedules, target)
    if not resolved:
        raise RuntimeError(f"未找到计划：{target}")
    current = next((item for item in schedules if str(item.get("id") or "") == resolved), None)
    if not current:
        raise RuntimeError(f"未找到计划：{target}")
    patch: Dict[str, object] = {}
    if str(payload.get("title") or "").strip():
        patch["title"] = str(payload.get("title") or "").strip()[:80]
    if str(payload.get("prompt") or "").strip():
        patch["prompt"] = str(payload.get("prompt") or "").strip()
    if "enabled" in payload:
        enabled = bool(payload.get("enabled"))
        patch["enabled"] = enabled
        if enabled:
            patch["expired_at"] = ""
    trigger = payload.get("trigger") or payload.get("schedule")
    if isinstance(trigger, dict):
        merged_schedule = dict(current.get("schedule") or {})
        for key, value in trigger.items():
            if value in (None, ""):
                continue
            merged_schedule[key] = value
        normalized_spec = normalize_schedule_spec(merged_schedule)
        if not normalized_spec:
            raise RuntimeError("修改后的计划触发器不完整。")
        patch["schedule"] = normalized_spec
        patch["schedule_text"] = format_schedule_spec(normalized_spec)
        patch["last_run_key"] = ""
    elif "enabled" in payload and bool(payload.get("enabled")) and not bool(current.get("enabled", True)):
        current_schedule = dict(current.get("schedule") or {})
        current_schedule["run_at"] = datetime.now().isoformat(timespec="seconds")
        normalized_spec = normalize_schedule_spec(current_schedule)
        if not normalized_spec:
            raise RuntimeError("计划触发器不完整，无法立即开启。")
        patch["schedule"] = normalized_spec
        patch["schedule_text"] = format_schedule_spec(normalized_spec)
        patch["last_run_key"] = ""
        patch["_schedule_action_note"] = "已安排立即执行"
    if not patch:
        raise RuntimeError("没有可更新的计划字段。")
    action_note = str(patch.pop("_schedule_action_note", "") or "")
    if not update_workspace_schedule(root, resolved, patch):
        raise RuntimeError(f"修改计划失败：{target}")
    updated = next(
        (item for item in load_workspace_schedules(root) if str(item.get("id") or "") == resolved),
        current,
    )
    if action_note:
        updated["_schedule_action_note"] = action_note
    return updated


def schedule_run_key(schedule_item: Dict[str, object], now: datetime) -> str:
    schedule = dict(schedule_item.get("schedule") or {})
    run_at = format_schedule_datetime(schedule.get("run_at"))
    compact = run_at.replace("-", "").replace(":", "").replace(" ", "") if run_at else now.strftime("%Y%m%d%H%M%S")
    return f"run-{compact}"


def schedule_due(schedule_item: Dict[str, object], now: datetime) -> bool:
    if str(schedule_item.get("expired_at") or "").strip():
        return False
    schedule = dict(schedule_item.get("schedule") or {})
    until_at = parse_schedule_datetime(schedule.get("until_at"))
    if until_at and now > until_at:
        return False
    run_at = parse_schedule_datetime(schedule.get("run_at"))
    return bool(run_at and now >= run_at)


def schedule_expired(schedule_item: Dict[str, object], now: datetime) -> bool:
    if str(schedule_item.get("expired_at") or "").strip():
        return True
    schedule = dict(schedule_item.get("schedule") or {})
    until_at = parse_schedule_datetime(schedule.get("until_at"))
    repeat_seconds = normalize_repeat_seconds(schedule.get("repeat_every_seconds"))
    if until_at and now > until_at and not schedule_due(schedule_item, now):
        return True
    if repeat_seconds:
        return False
    run_at = parse_schedule_datetime(schedule.get("run_at"))
    if not run_at:
        return False
    if str(schedule_item.get("last_run_key") or "").strip():
        return False
    return now > run_at + timedelta(seconds=SCHEDULE_MISSED_GRACE_SECONDS)


def expire_workspace_schedule(root: str, schedule_id: str, when: Optional[datetime] = None) -> bool:
    return update_workspace_schedule(root, schedule_id, {
        "enabled": False,
        "expired_at": (when or datetime.now()).isoformat(timespec="seconds"),
    })


def schedule_run_thread_id(schedule_id: str, run_key: str = "") -> str:
    del run_key
    return safe_thread_id(f"schedule-{schedule_id or 'schedule'}")


def sanitize_schedule_user_request(text: str) -> str:
    cleaned = str(text or "").strip()
    cleaned = re.sub(
        r"^(帮我|请|麻烦)?\s*(新建|创建|添加|设置|设定|安排)?\s*(一个|一条)?\s*(定时)?(计划|日程|任务)\s*[，,:：。；;]?\s*",
        "",
        cleaned,
    ).strip()
    return cleaned or str(text or "").strip()


def format_schedule_time(schedule_item: Dict[str, object]) -> str:
    return format_schedule_spec(dict(schedule_item.get("schedule") or {}))


def schedules_summary_text(schedules: List[Dict[str, object]]) -> str:
    if not schedules:
        return "当前没有定时计划。"
    lines = ["当前定时计划："]
    for index, schedule_item in enumerate(schedules, start=1):
        state = "过期" if str(schedule_item.get("expired_at") or "").strip() else ("开启" if bool(schedule_item.get("enabled", True)) else "暂停")
        title = str(schedule_item.get("title") or schedule_item.get("id") or "定时计划")
        lines.append(f"{index}. {title}｜{format_schedule_time(schedule_item)}｜{state}")
    return "\n".join(lines)


def skills_summary_text(skills: List[Dict[str, str]]) -> str:
    if not skills:
        return "当前工作区没有已安装技能。"
    lines = ["当前工作区技能："]
    for skill in skills[:30]:
        name = str(skill.get("name") or skill.get("id") or "").strip()
        description = str(skill.get("description") or "").strip()
        lines.append(f"- {name}: {description or '暂无简介'}")
    if len(skills) > 30:
        lines.append(f"- 另外还有 {len(skills) - 30} 个技能未展开。")
    return "\n".join(lines)


def skill_list_extension_reply(skills: List[Dict[str, str]]) -> str:
    lines = [
        "Skill 是经验、SOP、方法论的封装。每个技能至少包含一个 SKILL.md，内部还可能有其他 Markdown、脚本、图像和辅助材料；SKILL.md 有时会提示继续读取同目录下的补充文件来完成工作流。",
    ]
    if not skills:
        lines.append("当前工作区没有已安装技能。")
        return "\n\n".join(lines)
    lines.append("当前工作区技能列表：")
    for skill in skills[:60]:
        name = str(skill.get("name") or skill.get("id") or "").strip()
        description = str(skill.get("description") or "").strip() or "暂无简介"
        path = str(skill.get("path") or "").strip() or "未知路径"
        lines.append(f"- {name}: {description} | SKILL.md: {path}")
    if len(skills) > 60:
        lines.append(f"- 另外还有 {len(skills) - 60} 个技能未展开。")
    lines.append("如果用户随后明确说要用某个技能，请先读取对应的 SKILL.md，必要时再继续读取技能目录里的补充文档、脚本或素材。")
    return "\n".join(lines)


def schedule_skills_catalog_text(skills: List[Dict[str, str]]) -> str:
    if not skills:
        return "当前工作区没有已安装技能。"
    lines = [
        "计划执行可用技能索引：",
        "你可以根据名称、简介和路径自主判断是否需要读取某个 SKILL.md；需要时用命令读取对应文件内容，再按技能约束执行。",
    ]
    for skill in skills[:60]:
        name = str(skill.get("name") or skill.get("id") or "").strip()
        description = str(skill.get("description") or "").strip()
        path = str(skill.get("path") or "").strip()
        lines.append(f"- {name}: {description or '暂无简介'} | SKILL.md: {path or '未知路径'}")
    if len(skills) > 60:
        lines.append(f"- 另外还有 {len(skills) - 60} 个技能未展开。")
    return "\n".join(lines)


def skills_root(root: str) -> str:
    return os.path.join(root, HISTORY_DIR_NAME, "skills")


def safe_skill_id(name: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(name or "").strip().lower()).strip("-_")
    return cleaned[:64] or f"skill-{datetime.now().strftime('%Y%m%d-%H%M%S')}"


def strip_markdown_code_fence(text: str) -> str:
    return strip_single_outer_fenced_block(text, preferred_langs={"markdown", "md", ""})


def parse_skill_frontmatter(markdown_text: str) -> Dict[str, str]:
    content = str(markdown_text or "").strip()
    data = {"name": "", "description": ""}
    match = re.match(r"^---\s*\n(.*?)\n---\s*(?:\n|$)", content, flags=re.S)
    if not match:
        title_match = re.search(r"^#\s+(.+)$", content, flags=re.M)
        if title_match:
            data["name"] = safe_skill_id(title_match.group(1))
        return data
    for line in match.group(1).splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower()
        if key in data:
            data[key] = value.strip().strip('"').strip("'")
    return data


def normalize_skill_markdown(markdown_text: str, fallback_name: str = "") -> str:
    content = strip_markdown_code_fence(markdown_text)
    meta = parse_skill_frontmatter(content)
    name = safe_skill_id(meta.get("name") or fallback_name)
    description = (meta.get("description") or "Use this skill when the user request matches the instructions below.").strip()
    body = re.sub(r"^---\s*\n.*?\n---\s*", "", content, flags=re.S).strip()
    if not body:
        body = "# Instructions\n\nFollow the user's provided process and constraints for this skill."
    return f"---\nname: {name}\ndescription: {description}\n---\n\n{body.strip()}\n"


def load_workspace_skills(root: str) -> List[Dict[str, str]]:
    base = skills_root(root)
    skills: List[Dict[str, str]] = []
    if not os.path.isdir(base):
        return skills
    for entry in sorted(os.listdir(base)):
        path = os.path.join(base, entry)
        skill_file = os.path.join(path, "SKILL.md")
        if not os.path.isdir(path) or not os.path.isfile(skill_file):
            continue
        try:
            with open(skill_file, "r", encoding="utf-8") as f:
                content = f.read()
        except OSError:
            continue
        meta = parse_skill_frontmatter(content)
        display_meta = load_skill_display_meta(path)
        display_name = str(display_meta.get("display_name") or meta.get("name") or entry)
        display_description = str(display_meta.get("display_description") or meta.get("description") or "")
        skills.append({
            "id": safe_skill_id(entry),
            "name": display_name,
            "description": display_description,
            "path": skill_file,
            "dir": path,
        })
    return skills


def skill_display_meta_path(skill_dir: str) -> str:
    return os.path.join(skill_dir, ".agent_qt_skill.json")


def load_skill_display_meta(skill_dir: str) -> Dict[str, str]:
    for filename in (".agent_qt_skill.json", "_meta.json"):
        path = os.path.join(skill_dir, filename)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                payload = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        return {
            "display_name": str(payload.get("display_name") or payload.get("displayName") or payload.get("name") or "").strip(),
            "display_description": str(
                payload.get("display_description")
                or payload.get("description_zh")
                or payload.get("summary_zh")
                or payload.get("description")
                or payload.get("summary")
                or ""
            ).strip(),
        }
    return {}


def write_skill_display_meta(skill_dir: str, display_meta: Optional[Dict[str, str]] = None):
    meta = dict(display_meta or {})
    payload = {
        "display_name": str(meta.get("display_name") or meta.get("name") or "").strip(),
        "display_description": str(meta.get("display_description") or meta.get("description") or "").strip(),
        "source": str(meta.get("source") or "").strip(),
        "slug": str(meta.get("slug") or "").strip(),
    }
    if not any(payload.values()):
        return
    tmp_path = skill_display_meta_path(skill_dir) + ".tmp"
    with open(tmp_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, skill_display_meta_path(skill_dir))


def save_workspace_skill(root: str, markdown_text: str) -> Dict[str, str]:
    content = normalize_skill_markdown(markdown_text)
    meta = parse_skill_frontmatter(content)
    skill_id = safe_skill_id(meta.get("name"))
    base = skills_root(root)
    path = os.path.join(base, skill_id)
    suffix = 2
    while os.path.exists(path):
        existing_file = os.path.join(path, "SKILL.md")
        try:
            with open(existing_file, "r", encoding="utf-8") as f:
                if f.read() == content:
                    break
        except OSError:
            pass
        path = os.path.join(base, f"{skill_id}-{suffix}")
        suffix += 1
    os.makedirs(path, exist_ok=True)
    skill_file = os.path.join(path, "SKILL.md")
    tmp_path = skill_file + ".tmp"
    with open(tmp_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)
    os.replace(tmp_path, skill_file)
    return {
        "id": os.path.basename(path),
        "name": meta.get("name") or os.path.basename(path),
        "description": meta.get("description") or "",
        "path": skill_file,
        "dir": path,
    }


def safe_skill_relative_path(path: str) -> str:
    value = str(path or "").replace("\\", "/").strip()
    if value.startswith("/") or any(part == ".." for part in value.split("/")):
        return ""
    value = re.sub(r"/+", "/", value).lstrip("/")
    parts = []
    for part in value.split("/"):
        if not part or part in {".", ".."}:
            continue
        parts.append(re.sub(r"[^a-zA-Z0-9._ -]+", "_", part)[:120])
    return "/".join(parts)


def save_workspace_skill_package(root: str, files: Dict[str, bytes], fallback_markdown: str = "", display_meta: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    raw_skill = files.get("SKILL.md") or files.get("skill.md") or fallback_markdown.encode("utf-8")
    content = normalize_skill_markdown(raw_skill.decode("utf-8", errors="replace"))
    meta = parse_skill_frontmatter(content)
    skill_id = safe_skill_id(meta.get("name"))
    normalized_files = dict(files)
    normalized_files["SKILL.md"] = content.encode("utf-8")
    base = skills_root(root)
    path = os.path.join(base, skill_id)
    suffix = 2
    while os.path.exists(path):
        path = os.path.join(base, f"{skill_id}-{suffix}")
        suffix += 1
    os.makedirs(path, exist_ok=True)
    for rel_path, data in normalized_files.items():
        safe_rel = safe_skill_relative_path(rel_path)
        if not safe_rel:
            continue
        target = os.path.abspath(os.path.join(path, safe_rel))
        if not target.startswith(os.path.abspath(path) + os.sep):
            continue
        os.makedirs(os.path.dirname(target), exist_ok=True)
        tmp_path = target + ".tmp"
        with open(tmp_path, "wb") as f:
            f.write(data)
        os.replace(tmp_path, target)
    write_skill_display_meta(path, display_meta)
    skill_file = os.path.join(path, "SKILL.md")
    return {
        "id": os.path.basename(path),
        "name": str((display_meta or {}).get("display_name") or meta.get("name") or os.path.basename(path)),
        "description": str((display_meta or {}).get("display_description") or meta.get("description") or ""),
        "path": skill_file,
        "dir": path,
    }


def delete_workspace_skill(root: str, skill_id: str) -> bool:
    skill_id = safe_skill_id(skill_id)
    if not skill_id:
        return False
    base = os.path.abspath(skills_root(root))
    path = os.path.abspath(os.path.join(base, skill_id))
    if path == base or not path.startswith(base + os.sep) or not os.path.isdir(path):
        return False
    try:
        shutil.rmtree(path)
        return True
    except OSError:
        return False


def normalize_remote_skill(item: object) -> Dict[str, str]:
    if not isinstance(item, dict):
        return {}
    name = str(item.get("name") or item.get("slug") or item.get("id") or item.get("title") or item.get("displayName") or "").strip()
    title = str(item.get("displayName") or item.get("title") or item.get("name") or name).strip()
    description = str(
        item.get("description_zh")
        or item.get("summary_zh")
        or item.get("description")
        or item.get("summary")
        or item.get("readme")
        or ""
    ).strip()
    content = str(
        item.get("skill_md")
        or item.get("skillMd")
        or item.get("markdown")
        or item.get("content")
        or item.get("skill")
        or ""
    ).strip()
    author = str(item.get("author") or item.get("owner") or item.get("publisher") or "").strip()
    if not author:
        owner = item.get("owner")
        if isinstance(owner, dict):
            author = str(owner.get("displayName") or owner.get("handle") or "").strip()
        else:
            author = str(item.get("ownerName") or "").strip()
    category = str(item.get("category") or item.get("type") or "").strip()
    source_url = str(item.get("url") or item.get("source_url") or item.get("repository") or item.get("repo") or "").strip()
    if not source_url:
        source_url = str(item.get("homepage") or "").strip()
    install = str(item.get("install") or item.get("install_command") or item.get("installCommand") or "").strip()
    slug = str(item.get("slug") or name or title).strip()
    downloads = ""
    stars = ""
    stats = item.get("stats")
    if isinstance(stats, dict):
        downloads = str(stats.get("downloads") or "").strip()
        stars = str(stats.get("stars") or "").strip()
    else:
        downloads = str(item.get("downloads") or "").strip()
        stars = str(item.get("stars") or "").strip()
    if not name and not title:
        return {}
    return {
        "name": safe_skill_id(name or title),
        "slug": slug,
        "title": title or name,
        "description": description,
        "content": content,
        "author": author,
        "category": category,
        "source_url": source_url,
        "install": install,
        "downloads": downloads,
        "stars": stars,
        "source": str(item.get("source") or "tencent-skillhub").strip(),
    }


def extract_remote_skill_items(payload: object) -> List[Dict[str, str]]:
    candidates: object = payload
    if isinstance(payload, dict):
        for key in ("skills", "results", "items", "data", "catalog"):
            value = payload.get(key)
            if isinstance(value, list):
                candidates = value
                break
            if isinstance(value, dict):
                nested = extract_remote_skill_items(value)
                if nested:
                    return nested
    if not isinstance(candidates, list):
        return []
    skills: List[Dict[str, str]] = []
    for item in candidates:
        skill = normalize_remote_skill(item)
        if skill:
            skills.append(skill)
    return skills


class RemoteSkillSearchWorker(QThread):
    finished_signal = Signal(list, str)

    def __init__(self, query: str = "", category: str = "", fallback_category: str = "", parent=None):
        super().__init__(parent)
        self.query = str(query or "").strip()
        self.category = str(category or "").strip()
        self.fallback_category = str(fallback_category or "").strip()

    @staticmethod
    def split_search_terms(value: str) -> List[str]:
        return [item.strip() for item in str(value or "").split("|") if item.strip()]

    def fetch_skills(self, query: str = "", category: str = "") -> List[Dict[str, str]]:
        params = {
            "page": "1",
            "pageSize": "30",
            "sortBy": "score",
            "order": "desc",
        }
        if query:
            params["keyword"] = query
        if category:
            params["category"] = category
        base = os.environ.get("AGENT_QT_SKILLHUB_BASE_URL", "https://api.skillhub.cn").rstrip("/")
        url = f"{base}/api/skills?" + urllib.parse.urlencode(params)
        request = urllib.request.Request(url, headers={"User-Agent": "AgentQt/1.0"}, method="GET")
        with urllib.request.urlopen(request, timeout=18) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
        return extract_remote_skill_items(payload)

    def run(self):
        try:
            skills: List[Dict[str, str]] = []
            seen = set()

            def append_items(items: List[Dict[str, str]]):
                for skill in items:
                    if len(skills) >= 30:
                        break
                    key = str(skill.get("slug") or skill.get("name") or "")
                    if key and key not in seen:
                        skills.append(skill)
                        seen.add(key)

            queries = self.split_search_terms(self.query) or [""]
            categories = self.split_search_terms(self.category)
            if categories and not any(queries):
                for category in categories:
                    append_items(self.fetch_skills("", category))
                    if len(skills) >= 30:
                        break
            else:
                for query in queries:
                    append_items(self.fetch_skills(query, ""))
                    if len(skills) >= 30:
                        break

            if len(skills) < 12:
                for category in categories:
                    append_items(self.fetch_skills("", category))
                    if len(skills) >= 30:
                        break

            if len(skills) < 12 and self.fallback_category:
                seen = {str(skill.get("slug") or skill.get("name") or "") for skill in skills}
                for category in self.split_search_terms(self.fallback_category):
                    for skill in self.fetch_skills("", category):
                        key = str(skill.get("slug") or skill.get("name") or "")
                        if key and key not in seen:
                            skills.append(skill)
                            seen.add(key)
                        if len(skills) >= 30:
                            break
                    if len(skills) >= 30:
                        break
            self.finished_signal.emit(skills, "" if skills else "没有返回可展示的技能。")
        except urllib.error.HTTPError as exc:
            self.finished_signal.emit([], f"腾讯 SkillHub 请求失败：HTTP {exc.code}")
        except Exception as exc:
            self.finished_signal.emit([], "腾讯 SkillHub 请求失败：" + str(exc))


class RemoteSkillContentWorker(QThread):
    finished_signal = Signal(str, str, dict)

    def __init__(self, slug: str, parent=None):
        super().__init__(parent)
        self.slug = str(slug or "").strip()

    def run(self):
        if not self.slug:
            self.finished_signal.emit("", "缺少远程技能 slug。", {})
            return
        try:
            base = os.environ.get("AGENT_QT_SKILLHUB_BASE_URL", "https://api.skillhub.cn").rstrip("/")
            headers = {"User-Agent": "AgentQt/1.0"}
            files_url = f"{base}/api/v1/skills/{urllib.parse.quote(self.slug)}/files"
            files_request = urllib.request.Request(files_url, headers=headers, method="GET")
            with urllib.request.urlopen(files_request, timeout=18) as response:
                payload = json.loads(response.read().decode("utf-8", errors="replace"))
            file_items = payload.get("files") if isinstance(payload, dict) else []
            if not isinstance(file_items, list):
                file_items = []
            package: Dict[str, bytes] = {}
            for item in file_items:
                if not isinstance(item, dict):
                    continue
                rel_path = safe_skill_relative_path(str(item.get("path") or ""))
                if not rel_path or int(item.get("size") or 0) > 2_000_000:
                    continue
                file_url = (
                    f"{base}/api/v1/skills/{urllib.parse.quote(self.slug)}/file?"
                    + urllib.parse.urlencode({"path": rel_path})
                )
                request = urllib.request.Request(file_url, headers=headers, method="GET")
                with urllib.request.urlopen(request, timeout=18) as response:
                    package[rel_path] = response.read()
            content = (package.get("SKILL.md") or package.get("skill.md") or b"").decode("utf-8", errors="replace")
            self.finished_signal.emit(content, "" if content.strip() else "远程技能内容为空。", package)
        except urllib.error.HTTPError as exc:
            self.finished_signal.emit("", f"获取远程技能包失败：HTTP {exc.code}", {})
        except Exception as exc:
            self.finished_signal.emit("", "获取远程技能包失败：" + str(exc), {})

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
        "additions": int(record.get("additions", 0) or 0),
        "deletions": int(record.get("deletions", 0) or 0),
        "diff": str(record.get("diff", "")),
        "binary": bool(record.get("binary", False)),
        "internal_git": bool(record.get("internal_git", False)),
        "internal_git_repo": str(record.get("internal_git_repo", "")),
        "before_commit": str(record.get("before_commit", "")),
        "after_commit": str(record.get("after_commit", "")),
        "undoable": bool(record.get("undoable", True)),
        "undone": bool(record.get("undone", False)),
    }

def deserialize_change_record(record: Dict[str, object]) -> Optional[Dict[str, object]]:
    path = str(record.get("path", ""))
    if not path:
        return None
    before = bytes_from_store(record.get("before"))
    after = bytes_from_store(record.get("after"))
    rebuilt = build_file_diff(path, before, after)
    rebuilt["status"] = str(record.get("status", rebuilt.get("status", "modified")) or "modified")
    diff = str(record.get("diff", ""))
    if diff:
        rebuilt["diff"] = diff
        rebuilt["diff_rows"] = [] if bool(record.get("binary", False)) else parse_unified_diff_lines(diff)
    for key in ("additions", "deletions"):
        try:
            rebuilt[key] = int(record.get(key, rebuilt.get(key, 0)) or 0)
        except (TypeError, ValueError):
            pass
    rebuilt["internal_git"] = bool(record.get("internal_git", False))
    rebuilt["internal_git_repo"] = str(record.get("internal_git_repo", ""))
    rebuilt["before_commit"] = str(record.get("before_commit", ""))
    rebuilt["after_commit"] = str(record.get("after_commit", ""))
    rebuilt["binary"] = bool(record.get("binary", rebuilt.get("binary", False)))
    rebuilt["undoable"] = bool(record.get("undoable", True))
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
        self.grip = grip
        self.apply_theme_style()

    def apply_theme_style(self):
        self.setStyleSheet(f"""
            QFrame {{
                background: {COLORS['bg']};
                border: none;
                border-top: 1px solid {COLORS['border']};
                border-bottom: 1px solid {COLORS['border']};
            }}
            QFrame:hover {{
                background: {COLORS['accent_light']};
                border-top: 1px solid {soft_accent_border_color()};
                border-bottom: 1px solid {soft_accent_border_color()};
            }}
        """)
        if hasattr(self, "grip"):
            self.grip.setStyleSheet(f"""
                QFrame {{
                    background: {COLORS['border_strong']};
                    border: none;
                    border-radius: 2px;
                }}
            """)

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
    drag_started = Signal()
    drag_finished = Signal()

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
        self.apply_theme_style()

    def apply_theme_style(self):
        self.setStyleSheet(f"""
            QFrame {{
                background: {COLORS['bg']};
                border: none;
            }}
            QFrame:hover {{
                background: {COLORS['surface_alt']};
            }}
        """)
        if hasattr(self, "grip"):
            self.grip.setStyleSheet(f"""
                QFrame {{
                    background: {COLORS['border_strong']};
                    border: none;
                    border-radius: 2px;
                }}
            """)

    def install_toggle_button(self, button: QToolButton):
        self._layout.insertSpacing(0, 8)
        self._layout.insertWidget(1, button, alignment=Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)

    def set_grip_visible(self, visible: bool):
        self.grip.setFixedHeight(72 if visible else 0)
        self.grip.setVisible(visible)

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton and not self.sidebar._collapsed:
            self._dragging = True
            self._start_x = int(event.globalPosition().x())
            self._start_width = self.sidebar.current_width()
            self.drag_started.emit()
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
            self.drag_finished.emit()
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
    state_changed = Signal()
    
    def __init__(
        self,
        cmd: str,
        cwd: str,
        name: str = "",
        interactive: bool = False,
        terminal_id: str = "",
        log_path: str = "",
        expected_persistent: bool = False,
        launch_reason: str = "",
        command_kind_value: str = "",
        external_pid: int = 0,
    ):
        super().__init__()
        self.cmd = cmd
        self.cwd = cwd
        self.name = name or cmd[:40]
        self.interactive = interactive
        self.expected_persistent = bool(expected_persistent or interactive)
        self.launch_reason = launch_reason or ("interactive" if interactive else "manual")
        self.command_kind = command_kind_value or command_kind(cmd)
        self.terminal_id = terminal_id or uuid.uuid4().hex
        self.log_path = log_path
        self.started_at = datetime.now().isoformat(timespec="seconds")
        self.finished_at = ""
        self.exit_code: Optional[int] = None
        self.pid = int(external_pid or 0)
        self.external_process = bool(external_pid)
        self._log_read_pos = 0
        self.log_poll_timer: Optional[QTimer] = None
        self.process: Optional[QProcess] = None
        self.script_path = ""
        self.setup_ui()
        if self.external_process:
            self.attach_external_process()
        else:
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
                border: 1px solid {soft_accent_border_color()};
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

    def refresh_visual_settings(self):
        self.setStyleSheet(f"background: {COLORS['terminal_panel']}; border: none;")
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
        self.copy_btn.setStyleSheet(f"""
            QPushButton {{
                background: {COLORS['surface']};
                color: {COLORS['accent_dark']};
                border: 1px solid {soft_accent_border_color()};
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
        env = agent_qprocess_environment(create=False)
        env.insert("AGENT_QT_TERMINAL_ID", self.terminal_id)
        self.process.setProcessEnvironment(env)
        self.process.readyReadStandardOutput.connect(self.read_output)
        self.process.started.connect(self.on_started)
        self.process.errorOccurred.connect(self.on_error)
        self.process.finished.connect(self.on_finished)
        if not self.interactive and should_use_temp_shell_script(self.cmd):
            self.script_path = write_temp_shell_script(self.cmd)
        shell, args = shell_launch_for_command(self.cmd, interactive=self.interactive, script_path=self.script_path)
        self.process.start(shell, args)

    def attach_external_process(self):
        self.output.set_input_enabled(False)
        self.refresh_external_log()
        self.log_poll_timer = QTimer(self)
        self.log_poll_timer.timeout.connect(self.refresh_external_log)
        self.log_poll_timer.start(1000)

    def append_log_file(self, text: str):
        if not text or not self.log_path:
            return
        try:
            os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
            with open(self.log_path, "a", encoding="utf-8", newline="\n") as f:
                f.write(text)
        except OSError:
            pass

    def process_id(self) -> int:
        if self.pid:
            return self.pid
        try:
            return int(self.process.processId()) if self.process is not None else 0
        except (RuntimeError, TypeError, ValueError):
            return 0

    def is_running(self) -> bool:
        if self.external_process:
            if self.pid <= 0:
                return False
            try:
                os.kill(self.pid, 0)
                return True
            except OSError:
                return False
        try:
            return bool(self.process and self.process.state() != QProcess.ProcessState.NotRunning)
        except RuntimeError:
            return False

    def refresh_external_log(self):
        if not self.log_path or not os.path.isfile(self.log_path):
            return
        try:
            with open(self.log_path, "r", encoding="utf-8", errors="replace") as f:
                f.seek(self._log_read_pos)
                text = f.read()
                self._log_read_pos = f.tell()
        except OSError:
            return
        if text:
            self.output.append_process_text(text)
        if not self.is_running() and self.log_poll_timer is not None:
            if not self.finished_at:
                self.finished_at = datetime.now().isoformat(timespec="seconds")
            self.log_poll_timer.stop()
            self.state_changed.emit()

    def on_started(self):
        try:
            self.pid = int(self.process.processId()) if self.process is not None else 0
        except (RuntimeError, TypeError, ValueError):
            self.pid = 0
        if self.interactive:
            text = (
                f"# shell: {self.name}\n"
                f"# cwd: {self.cwd}\n"
                f"# pid: {self.process_id()}\n"
                f"# terminal_id: {self.terminal_id}\n"
            )
            self.output.append_process_text(text)
            self.append_log_file(text)
            self.output.set_input_enabled(True)
        else:
            self.output.set_input_enabled(False)
            text = (
                f"$ {strip_shell_command_marker(self.cmd)}\n"
                f"# cwd: {self.cwd}\n"
                f"# pid: {self.process_id()}\n"
                f"# terminal_id: {self.terminal_id}\n"
            )
            self.output.append_process_text(text)
            self.append_log_file(text)

    def read_output(self):
        if not self.process:
            return
        text = decode_process_output(self.process.readAllStandardOutput().data())
        if text:
            self.output.append_process_text(text)
            self.append_log_file(text)

    def on_error(self, _err):
        try:
            message = self.process.errorString() if self.process else "未知错误"
        except RuntimeError:
            message = "进程已关闭"
        text = f"\n--- 启动失败: {message} ---"
        self.output.append_process_text(text)
        self.append_log_file(text)

    def on_finished(self, exit_code: int, _exit_status):
        self.exit_code = int(exit_code)
        self.finished_at = datetime.now().isoformat(timespec="seconds")
        self.output.set_input_enabled(False)
        text = f"\n--- 退出码: {exit_code} ---"
        self.output.append_process_text(text)
        self.append_log_file(text)
        if self.script_path:
            try:
                os.remove(self.script_path)
            except OSError:
                pass
            self.script_path = ""
        self.state_changed.emit()

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
        if self.external_process:
            if self.log_poll_timer is not None:
                self.log_poll_timer.stop()
            if self.pid > 0 and self.is_running():
                try:
                    if platform.system() == "Windows":
                        subprocess.run(
                            ["taskkill", "/PID", str(self.pid), "/T", "/F"],
                            capture_output=True,
                            text=True,
                            timeout=8,
                            **subprocess_no_window_kwargs(),
                        )
                    else:
                        os.kill(self.pid, signal.SIGTERM)
                except Exception:
                    pass
            self.output.set_input_enabled(False)
            text = "\n--- 已关闭 ---"
            self.output.append_process_text(text)
            self.append_log_file(text)
            return
        if not self.process:
            return
        try:
            if self.process.state() != QProcess.ProcessState.NotRunning:
                self.process.terminate()
                if not self.process.waitForFinished(1200):
                    self.process.kill()
                    self.process.waitForFinished(1200)
                self.output.set_input_enabled(False)
                text = "\n--- 已关闭 ---"
                self.output.append_process_text(text)
                self.append_log_file(text)
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
        self.hovered = False
        self.setFixedHeight(28)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setup_ui()
        self.apply_style()

    def setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(9, 4, 9, 4)
        layout.setSpacing(4)

        self.icon_btn = QToolButton(self)
        self.icon_btn.setCursor(Qt.PointingHandCursor)
        self.icon_btn.setFixedSize(16, 18)
        self.icon_btn.setIconSize(QSize(16, 16))
        self.icon_btn.clicked.connect(lambda: self.close_requested.emit(self.proc))
        self.icon_btn.setStyleSheet(f"""
            QToolButton {{
                background: transparent;
                border: none;
                border-radius: 8px;
            }}
            QToolButton:hover {{
                background: {COLORS['border']};
            }}
        """)
        layout.addWidget(self.icon_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        self.title_label = QLabel(self.title)
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.title_label.setFixedHeight(18)
        self.title_label.setStyleSheet(f"""
            QLabel {{
                color: {COLORS['text']};
                background: transparent;
                border: none;
                font-size: 12px;
                font-weight: 500;
                padding: 0 0 1px 0;
            }}
        """)
        self.title_label.setMinimumWidth(56)
        self.title_label.setMaximumWidth(170)
        layout.addWidget(self.title_label, 0, Qt.AlignmentFlag.AlignVCenter)

        self.update_icon()

    def update_icon(self):
        if self.hovered:
            self.icon_btn.setIcon(close_icon(COLORS["text_secondary"], 16))
        else:
            self.icon_btn.setIcon(terminal_icon(COLORS["text_secondary"], 16))

    def apply_style(self):
        background = COLORS["surface_alt"] if self.active else "transparent"
        border = COLORS["border"] if self.active else "transparent"
        hover_background = COLORS["surface"] if self.active else COLORS["surface_alt"]
        hover_border = COLORS["border"] if self.active else "transparent"
        self.setStyleSheet(f"""
            QFrame#terminalTabCard {{
                background: {background};
                border: 1px solid {border};
                border-radius: 10px;
            }}
            QFrame#terminalTabCard:hover {{
                background: {hover_background};
                border: 1px solid {hover_border};
            }}
        """)
        self.title_label.setStyleSheet(f"""
            QLabel {{
                color: {COLORS['terminal_text']};
                background: transparent;
                border: none;
                font-size: 12px;
                font-weight: 700;
                padding: 0 0 1px 0;
            }}
        """)
        self.icon_btn.setStyleSheet(f"""
            QToolButton {{
                background: transparent;
                border: none;
                border-radius: 8px;
            }}
            QToolButton:hover {{
                background: {COLORS['surface_alt']};
            }}
        """)
        self.update_icon()

    def set_active(self, active: bool):
        self.active = active
        self.apply_style()

    def enterEvent(self, event):
        self.hovered = True
        self.update_icon()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.hovered = False
        self.update_icon()
        super().leaveEvent(event)

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton and not self.icon_btn.geometry().contains(event.position().toPoint()):
            self.selected.emit(self.proc)
            event.accept()
            return
        super().mousePressEvent(event)

class TerminalPanel(QWidget):
    collapsed_signal = Signal()
    process_finished_signal = Signal(dict)
    
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
        self.header_status_label = QLabel("")
        self.header_status_label.setFixedHeight(22)
        self.header_status_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.header_status_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.header_status_label.setStyleSheet(
            f"color: {COLORS['terminal_muted']}; font-size: 11px; background: transparent;"
        )
        self.header_status_label.setVisible(False)
        self.title_label = title
        self.collapse_btn = QPushButton("─")
        self.collapse_btn.setFixedSize(26, 22)
        self.collapse_btn.setCursor(Qt.PointingHandCursor)
        self.collapse_btn.setToolTip("")
        self.collapse_btn.setStyleSheet(f"""
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
        self.collapse_btn.clicked.connect(self.collapse)
        header.addWidget(self.collapse_btn)
        header.addWidget(title)
        header.addWidget(self.count_label)
        header.addStretch()
        header.addWidget(self.header_status_label, 1)
        layout.addWidget(header_shell, 0, Qt.AlignmentFlag.AlignTop)
        
        self.tab_row_shell = QWidget(self)
        self.tab_row_shell.setFixedHeight(28)
        self.tab_row_shell.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.tab_row_shell.setStyleSheet("background: transparent; border: none;")
        self.tab_row = QHBoxLayout(self.tab_row_shell)
        self.tab_row.setContentsMargins(0, 0, 0, 0)
        self.tab_row.setSpacing(4)
        self.tab_cards: Dict[ManagedProcess, TerminalTabCard] = {}

        self.add_btn = QToolButton(self)
        self.add_btn.setCursor(Qt.PointingHandCursor)
        self.add_btn.setIcon(line_icon("plus", COLORS["text_secondary"], 18))
        self.add_btn.setIconSize(QSize(16, 16))
        self.add_btn.setFixedSize(28, 26)
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
        self.completed_terminal_entries: List[Dict[str, object]] = []
        self.setVisible(False)

    def apply_theme_style(self):
        self.setStyleSheet(f"""
            QWidget#terminalPanel {{
                background: {COLORS['terminal_panel']};
                border-top: 1px solid {COLORS['border']};
            }}
        """)
        for card in self.tab_cards.values():
            card.apply_style()
        for proc in self.processes:
            proc.refresh_visual_settings()
        self.add_btn.setIcon(line_icon("plus", COLORS["text_secondary"], 18))
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
        for label, style in (
            (self.count_label, f"color: {COLORS['terminal_muted']}; font-size: 11px; background: transparent;"),
            (self.header_status_label, f"color: {COLORS['terminal_muted']}; font-size: 11px; background: transparent;"),
        ):
            label.setStyleSheet(style)
        self.title_label.setStyleSheet(f"color: {COLORS['terminal_text']}; font-weight: 800; font-size: 12px; background: transparent;")
        self.collapse_btn.setStyleSheet(f"""
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

    def set_project_root(self, path: str):
        self.project_root = path
        self.load_completed_terminal_entries()
        self.write_process_registry()

    def registry_path(self) -> str:
        root = self.project_root or os.path.expanduser("~")
        return terminal_registry_path(root)

    def logs_dir(self) -> str:
        root = self.project_root or os.path.expanduser("~")
        return terminal_cache_dir(root)

    def process_registry_entry(self, proc: ManagedProcess) -> Dict[str, object]:
        return {
            "id": proc.terminal_id,
            "name": proc.name,
            "cmd": strip_shell_command_marker(proc.cmd),
            "cwd": proc.cwd,
            "interactive": proc.interactive,
            "expected_persistent": proc.expected_persistent,
            "external_process": proc.external_process,
            "launch_reason": proc.launch_reason,
            "command_kind": proc.command_kind,
            "pid": proc.process_id(),
            "running": proc.is_running(),
            "status": "running" if proc.is_running() else "exited",
            "exit_code": proc.exit_code,
            "started_at": proc.started_at,
            "finished_at": proc.finished_at,
            "log_path": proc.log_path,
        }

    def load_completed_terminal_entries(self):
        self.completed_terminal_entries = []
        path = self.registry_path()
        if not os.path.isfile(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                payload = json.load(f)
        except (OSError, json.JSONDecodeError):
            return
        entries = payload.get("completed_processes")
        if not isinstance(entries, list):
            entries = [
                item for item in payload.get("processes", [])
                if isinstance(item, dict) and not bool(item.get("running"))
            ] if isinstance(payload.get("processes"), list) else []
        self.completed_terminal_entries = [
            dict(item) for item in entries[-TERMINAL_COMPLETED_HISTORY_LIMIT:]
            if isinstance(item, dict) and item.get("id")
        ]

    def remember_completed_process(self, proc: ManagedProcess):
        entry = self.process_registry_entry(proc)
        entry["running"] = False
        entry["status"] = "exited"
        entry["remembered_at"] = datetime.now().isoformat(timespec="seconds")
        existing = [item for item in self.completed_terminal_entries if item.get("id") != entry.get("id")]
        existing.append(entry)
        self.completed_terminal_entries = existing[-TERMINAL_COMPLETED_HISTORY_LIMIT:]

    def write_process_registry(self):
        path = self.registry_path()
        active_entries = [self.process_registry_entry(proc) for proc in self.processes]
        payload = {
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "project_root": self.project_root,
            "processes": active_entries + self.completed_terminal_entries,
            "active_processes": active_entries,
            "completed_processes": self.completed_terminal_entries,
        }
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp_path = path + ".tmp"
            with open(tmp_path, "w", encoding="utf-8", newline="\n") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, path)
        except OSError:
            pass

    def clipped_terminal_output(self, text: str, max_lines: int = 1000) -> str:
        lines = str(text or "").splitlines()
        if len(lines) <= max_lines:
            return str(text or "")
        omitted = len(lines) - max_lines
        return "\n".join(lines[:500] + [f"... omitted {omitted} lines ..."] + lines[-500:])

    def terminal_console_entries(self, grep_text: str = "", pid: int = 0) -> List[Dict[str, object]]:
        needle = str(grep_text or "").strip().lower()
        try:
            pid = int(pid or 0)
        except (TypeError, ValueError):
            pid = 0
        entries: List[Dict[str, object]] = []
        for proc in list(self.processes):
            try:
                meta = self.process_registry_entry(proc)
                if pid and int(meta.get("pid") or 0) != pid:
                    continue
                text = proc.output.toPlainText()
                haystack = "\n".join([
                    str(meta.get("cmd") or ""),
                    str(meta.get("cwd") or ""),
                    text,
                ]).lower()
                if needle and needle not in haystack:
                    continue
            except RuntimeError:
                continue
            meta["output"] = self.clipped_terminal_output(text)
            entries.append(meta)
        return entries

    def terminal_console_text(self, grep_text: str = "", pid: int = 0) -> str:
        entries = self.terminal_console_entries(grep_text, pid=pid)
        if not entries:
            if pid:
                return f"No terminal processes matched pid: {pid}"
            return f"No terminal processes matched grep: {grep_text or '*'}"
        parts: List[str] = []
        for item in entries:
            parts.append("===== terminal console =====")
            output = str(item.get("output") or "").rstrip()
            parts.append(output or "(no console output)")
        return "\n".join(parts).rstrip() + "\n"

    def terminal_title(self, cwd: str, index: Optional[int] = None) -> str:
        root = self.project_root or cwd
        name = os.path.basename(os.path.normpath(root)) or "terminal"
        if index and index > 1:
            return f"{name} {index}"
        return name
    
    def add_process(
        self,
        cmd: str,
        cwd: str,
        name: str = "",
        interactive: bool = False,
        expected_persistent: bool = False,
        launch_reason: str = "",
        command_kind_value: str = "",
    ):
        if not interactive and (not cmd.strip() or is_interactive_shell_command(cmd)):
            return None
        label = name.strip() if name.strip() else self.terminal_title(cwd, len(self.processes) + 1)
        terminal_id = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
        log_path = os.path.join(self.logs_dir(), f"{terminal_id}-{safe_terminal_log_name(label)}.log")
        proc = ManagedProcess(
            cmd,
            cwd,
            label,
            interactive=interactive,
            terminal_id=terminal_id,
            log_path=log_path,
            expected_persistent=expected_persistent,
            launch_reason=launch_reason,
            command_kind_value=command_kind_value,
        )
        proc.remove_requested.connect(self.remove_process)
        proc.state_changed.connect(self.write_process_registry)
        if proc.process:
            proc.process.finished.connect(lambda _code, _status, p=proc: self.refresh_process_state(p))
            proc.process.finished.connect(lambda code, _status, p=proc: self.emit_process_finished_log(p, code))
            proc.process.started.connect(self.write_process_registry)
            proc.process.finished.connect(lambda _code, _status: self.write_process_registry())
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
        self.write_process_registry()
        return proc

    def add_external_process(self, info: Dict[str, object]):
        cmd = str(info.get("cmd") or "")
        cwd = str(info.get("cwd") or self.project_root or os.path.expanduser("~"))
        label = str(info.get("name") or "timeout-command")
        proc = ManagedProcess(
            cmd,
            cwd,
            label,
            interactive=False,
            terminal_id=str(info.get("id") or ""),
            log_path=str(info.get("log_path") or ""),
            expected_persistent=bool(info.get("expected_persistent", True)),
            launch_reason=str(info.get("launch_reason") or "timeout"),
            command_kind_value=str(info.get("command_kind") or command_kind(cmd)),
            external_pid=int(info.get("pid") or 0),
        )
        proc.remove_requested.connect(self.remove_process)
        proc.state_changed.connect(self.write_process_registry)
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
        self.write_process_registry()
        return proc

    def emit_process_finished_log(self, proc: ManagedProcess, exit_code: int):
        try:
            log = proc.output.toPlainText()
        except RuntimeError:
            return
        self.remember_completed_process(proc)
        self.write_process_registry()
        self.process_finished_signal.emit({
            "cmd": proc.cmd,
            "cwd": proc.cwd,
            "name": proc.name,
            "interactive": proc.interactive,
            "expected_persistent": proc.expected_persistent,
            "launch_reason": proc.launch_reason,
            "command_kind": proc.command_kind,
            "exit_code": exit_code,
            "log": log,
            "log_path": proc.log_path,
            "pid": proc.process_id(),
        })

    def create_interactive_terminal(self):
        cwd = self.project_root or os.path.expanduser("~")
        self.add_process("", cwd, self.terminal_title(cwd, len(self.processes) + 1), interactive=True, expected_persistent=True)

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
        self.remember_completed_process(proc)
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
        self.write_process_registry()

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

    def set_header_status(self, text: str):
        message = str(text or "").strip()
        self.header_status_label.setText(message)
        self.header_status_label.setVisible(bool(message))

# ============================================================
# 执行线程
# ============================================================
class ExecuteWorker(QThread):
    output_signal = Signal(str)
    long_running_signal = Signal(str, str, str, str, str)
    background_process_signal = Signal(dict)
    finished_signal = Signal(str)
    
    def __init__(self, commands: List[str], cwd: str):
        super().__init__()
        self.commands = commands
        self.cwd = cwd
    
    def run(self):
        cwd = self.cwd
        outputs = []
        for i, cmd in enumerate(self.commands, 1):
            if self.isInterruptionRequested():
                outputs.append(f"[{i}] ⏹️ 已停止后续命令")
                self.output_signal.emit(outputs[-1])
                break
            cmd = strip_provider_ui_artifacts_from_command(cmd)
            if not cmd.strip():
                outputs.append(f"[{i}] ⚠️ 跳过空命令")
                self.output_signal.emit(outputs[-1])
                continue
            display_cmd = command_for_log(cmd)
            target = plain_cd_target(cmd, cwd)
            if target is not None:
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
            if command_writes_agent_project_cache(cmd, self.cwd):
                outputs.append(
                    f"[{i}] ⛔ 拒绝: {display_cmd}\n"
                    "检测到命令尝试把项目文件写入 Agent Qt 缓存目录 .agent_qt/projects。"
                    "请改写到当前工作区根目录。"
                )
                self.output_signal.emit(outputs[-1])
                continue
            if is_interactive_shell_command(cmd):
                outputs.append(f"[{i}] ⚠️ 跳过交互式 Shell: {display_cmd}")
                self.output_signal.emit(outputs[-1])
                continue
            syntax_error = validate_shell_command_syntax(cmd)
            if syntax_error:
                outputs.append(
                    f"[{i}] ⛔ 命令块未执行: {display_cmd}\n"
                    f"{syntax_error}\n"
                    "请让 AI 重新输出一个完整的 ```bash 命令块；不要继续执行被截断的片段。"
                )
                self.output_signal.emit(outputs[-1])
                continue
            if is_long_running(cmd):
                outputs.append(f"[{i}] 🔵 后台: {display_cmd}")
                self.long_running_signal.emit(cmd, cwd, display_cmd.splitlines()[0][:40], "long_running_pattern", command_kind(cmd))
                self.output_signal.emit(outputs[-1])
                continue
            try:
                r = run_shell_command_capture(
                    cmd,
                    cwd,
                    timeout=COMMAND_BACKGROUND_TIMEOUT_SECONDS,
                    project_root=self.cwd,
                    display_name=display_cmd.splitlines()[0][:40],
                )
                out = r.stdout.strip()
                if r.stderr.strip():
                    out += "\n" + r.stderr.strip()
                if r.returncode != 0:
                    out += f"\n[退出码: {r.returncode}]"
                outputs.append(f"[{i}] 💻 {display_cmd}\n📤 {out or '(无输出)'}")
            except BackgroundProcessStarted as exc:
                info = dict(exc.info)
                self.background_process_signal.emit(info)
                outputs.append(
                    f"[{i}] ⏱️ 超时 → 后台: {display_cmd}\n"
                    f"pid={info.get('pid') or 0} log={info.get('log_path') or ''}"
                )
            except subprocess.TimeoutExpired:
                if is_interactive_shell_command(cmd):
                    outputs.append(f"[{i}] ⚠️ 交互式 Shell 已超时，未创建后台终端: {display_cmd}")
                else:
                    outputs.append(f"[{i}] ⏱️ 超时 → 后台: {display_cmd}")
                    self.long_running_signal.emit(cmd, cwd, display_cmd.splitlines()[0][:40], "timeout", command_kind(cmd))
            except Exception as e:
                outputs.append(f"[{i}] ❌ {e}")
            self.output_signal.emit(outputs[-1])
        self.finished_signal.emit('\n\n'.join(outputs))


class WebResearchWorker(QThread):
    finished_signal = Signal(str, str)
    status_signal = Signal(str)

    def __init__(
        self,
        manager: "AutomationProviderManager",
        directives: List[str],
        queries: List[str],
        model: str,
        thread_id: str,
        *,
        thinking_enabled: Optional[bool] = None,
        expert_mode_enabled: Optional[bool] = None,
    ):
        super().__init__()
        self.manager = manager
        self.directives = [str(item or "").strip() for item in directives if str(item or "").strip()]
        self.queries = [str(item or "").strip() for item in queries if str(item or "").strip()]
        self.model = model
        self.thread_id = thread_id
        self.thinking_enabled = thinking_enabled
        self.expert_mode_enabled = expert_mode_enabled

    def run(self):
        try:
            search_outputs: List[str] = []
            for query in self.queries[:3]:
                if self.isInterruptionRequested():
                    search_outputs.append(f"网页搜索：{query}\n已停止后续搜索。")
                    break
                try:
                    summary = self.manager.web_research(
                        query,
                        self.model,
                        self.thread_id,
                        thinking_enabled=self.thinking_enabled,
                        expert_mode_enabled=self.expert_mode_enabled,
                        retry_callback=self.status_signal.emit,
                    )
                    search_outputs.append(f"网页搜索：{query}\n{summary}".strip())
                except Exception as exc:
                    search_outputs.append(f"网页搜索：{query}\n搜索失败：{exc}".strip())
            output_text = "\n\n".join(part for part in search_outputs if part).strip() or "网页搜索未返回结果。"
            self.finished_signal.emit(
                terminal_extension_execution_log(self.directives, output_text),
                "",
            )
        except Exception as exc:
            self.finished_signal.emit("", str(exc))

# ============================================================
# 可选网页 Provider 自动化插件
# ============================================================
AUTOMATION_MODELS = [
    ("DeepSeek V4", "DeepSeekV4"),
    ("DeepSeek V4 Thinking", "DeepSeekV4-thinking"),
    ("DeepSeek V4 Simple Thinking", "DeepSeekV4-simple-thinking"),
    ("DeepSeek V4 Simple", "DeepSeekV4-simple"),
]
AUTOMATION_DEFAULT_MODEL = "DeepSeekV4"
AUTOMATION_CONTEXT_MODES = [
    ("专家模式", "expert"),
    ("简单模式", "simple"),
]
AUTOMATION_CONTEXT_PRESETS = [
    {"label": "DeepSeek PRO web", "mode": "expert", "model": "DeepSeekV4"},
    {"label": "DeepSeek PRO web thinking", "mode": "expert", "model": "DeepSeekV4-thinking"},
    {"label": "DeepSeek Flash web", "mode": "simple", "model": "DeepSeekV4-simple"},
    {"label": "DeepSeek Flash web thinking", "mode": "simple", "model": "DeepSeekV4-simple-thinking"},
]
AUTOMATION_SIMPLE_MODEL_BY_MODEL = {
    "DeepSeekV4": "DeepSeekV4-simple",
    "DeepSeekV4-thinking": "DeepSeekV4-simple-thinking",
}
AUTOMATION_REQUIRED_MODULES = (
    "ddgs",
    "fastapi",
    "uvicorn",
    "playwright",
    "langchain_core",
    "jsonschema",
    "pydantic",
)


def automation_plugin_root() -> str:
    return os.path.expanduser(os.environ.get("AGENT_QT_AUTOMATION_PLUGIN_DIR", "~/.agent_qt/plugins/web_provider"))


def automation_venv_python(root: str) -> str:
    if platform.system() == "Windows":
        return os.path.join(root, ".venv", "Scripts", "python.exe")
    return os.path.join(root, ".venv", "bin", "python")


def browser_channel_probe_code() -> str:
    return """
import sys
from playwright.sync_api import sync_playwright
channels = ["msedge", "chrome"]
errors = []
p = sync_playwright().start()
try:
    for channel in channels:
        try:
            b = p.chromium.launch(channel=channel, headless=True)
            b.close()
            print(channel)
            raise SystemExit(0)
        except Exception as exc:
            errors.append(f"{channel}: {str(exc).splitlines()[0] if str(exc) else type(exc).__name__}")
finally:
    p.stop()
if errors:
    print("\\n".join(errors), file=sys.stderr)
raise SystemExit(1)
"""


def python_exec_base64_code(source: str) -> str:
    payload = base64.b64encode(source.encode("utf-8")).decode("ascii")
    return f"import base64; exec(base64.b64decode('{payload}').decode('utf-8'))"


def is_python_executable_candidate(path: str) -> bool:
    if not path:
        return False
    name = os.path.basename(str(path)).lower()
    if platform.system() == "Windows":
        return name in {"python.exe", "python3.exe"}
    return name in {"python", "python3"} or name.startswith("python")


def supports_automation_python(python_bin: str) -> bool:
    try:
        result = subprocess.run(
            [python_bin, "-c", "import sys\nraise SystemExit(sys.version_info[:2] < (3, 9))"],
            capture_output=True,
            text=True,
            timeout=8,
            **subprocess_no_window_kwargs(),
        )
    except Exception:
        return False
    return result.returncode == 0


def find_automation_backend() -> str:
    app_dir = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.environ.get("AGENT_QT_WEB_PROVIDER_BACKEND", ""),
        os.path.join(app_dir, "plugins", "web_provider", "backend"),
        "/Users/pippo/Downloads/freechat/deepseekwithdeerflow2.0/deer-flow/backend",
        os.path.expanduser("~/Downloads/freechat/deepseekwithdeerflow2.0/deer-flow/backend"),
        os.path.expanduser("~/Downloads/freechat/deepseekwithdeerflow2.0/deer-flow-gha/backend"),
    ]
    for candidate in candidates:
        if not candidate:
            continue
        backend = os.path.abspath(os.path.expanduser(candidate))
        if (
            os.path.isfile(os.path.join(backend, "app", "deepseek_local_provider.py"))
            and os.path.isfile(os.path.join(backend, "packages", "harness", "deerflow", "models", "deepseek_web_bridge.py"))
        ):
            return backend
    return ""


class AutomationProviderManager:
    """Thin adapter around the optional deer-flow web provider process."""

    def __init__(self):
        self.plugin_root = automation_plugin_root()
        self.backend_dir = find_automation_backend()
        self.host = os.environ.get("AGENT_QT_AUTOMATION_HOST", "127.0.0.1")
        self.port = int(os.environ.get("AGENT_QT_AUTOMATION_PORT", "8765"))
        self.base_url = f"http://{self.host}:{self.port}"
        self.log_dir = os.path.join(self.plugin_root, "logs")
        self.pid_file = os.path.join(self.plugin_root, "provider.pid")
        self.log_file = os.path.join(self.log_dir, "provider.log")

    def has_backend(self) -> bool:
        return bool(self.backend_dir and os.path.isdir(self.backend_dir))

    def harness_path(self) -> str:
        return os.path.join(self.backend_dir, "packages", "harness")

    def candidate_pythons(self) -> List[str]:
        plugin_python = automation_venv_python(self.plugin_root)
        runtime_python = ensure_agent_runtime(create=False)
        backend_python = os.path.join(
            self.backend_dir,
            ".venv",
            "Scripts" if platform.system() == "Windows" else "bin",
            "python.exe" if platform.system() == "Windows" else "python",
        )
        system_candidates = [
            shutil.which("python3") or "",
            shutil.which("python") or "",
        ]
        current_executable = "" if getattr(sys, "frozen", False) else sys.executable
        candidates = [plugin_python, runtime_python, backend_python, current_executable, *system_candidates]
        seen = set()
        result = []
        for candidate in candidates:
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            if (
                is_python_executable_candidate(candidate)
                and (os.path.exists(candidate) or shutil.which(candidate))
                and supports_automation_python(candidate)
            ):
                result.append(candidate)
        return result

    def provider_env(self) -> Dict[str, str]:
        env = os.environ.copy()
        if not use_system_proxy_setting():
            # Keep the local web provider on a direct network path instead of
            # inheriting user/global shell proxies. This matches the WeChat
            # connector behavior and avoids proxy-specific TLS /
            # connection-closed failures when loading chat.deepseek.com in
            # Playwright.
            for proxy_key in (
                "http_proxy",
                "https_proxy",
                "HTTP_PROXY",
                "HTTPS_PROXY",
                "all_proxy",
                "ALL_PROXY",
            ):
                env.pop(proxy_key, None)
        pythonpath_parts = [self.harness_path(), self.backend_dir]
        if env.get("PYTHONPATH"):
            pythonpath_parts.append(env["PYTHONPATH"])
        env["PYTHONPATH"] = os.pathsep.join(pythonpath_parts)
        env["DEEPSEEK_LOCAL_PROVIDER_HOST"] = self.host
        env["DEEPSEEK_LOCAL_PROVIDER_PORT"] = str(self.port)
        env["DEEPSEEK_LOCAL_INTERFACE_MODE"] = "both"
        env.setdefault("DEEPSEEK_LOCAL_EXPERT_MODE", "1")
        # Daily automation should stay in the background. The explicit login action still opens a visible page.
        headless_value = os.environ.get("AGENT_QT_WEB_HEADLESS", "1")
        env["DEEPSEEK_WEB_HEADLESS"] = headless_value
        env["XIAOMI_MIMO_WEB_HEADLESS"] = headless_value
        env.setdefault("DEEPSEEK_WEB_RESPONSE_TIMEOUT_MS", "600000")
        env.setdefault("DEEPSEEK_WEB_MAX_CONTINUE_CLICKS", "12")
        env.setdefault("DEEPSEEK_WEB_COPY_PROBE_MAX_MS", "1500")
        env.setdefault("XIAOMI_MIMO_RESPONSE_TIMEOUT_MS", "300000")
        profile_root = os.path.join(self.plugin_root, "profiles")
        env.setdefault("DEEPSEEK_WEB_PROFILE_DEERFLOW", os.path.join(profile_root, "deepseek"))
        env.setdefault("DEEPSEEK_WEB_SESSION_STATE_DEERFLOW", os.path.join(profile_root, "deepseek-session.json"))
        env.setdefault("XIAOMI_MIMO_WEB_PROFILE", os.path.join(profile_root, "mimo"))
        env.setdefault("XIAOMI_MIMO_WEB_SESSION_STATE", os.path.join(profile_root, "mimo-session.json"))
        browser_channel_path = os.path.join(self.plugin_root, "browser-channel.txt")
        if os.path.isfile(browser_channel_path):
            try:
                browser_channel = open(browser_channel_path, "r", encoding="utf-8").read().strip()
            except OSError:
                browser_channel = ""
            if browser_channel:
                env.setdefault("DEEPSEEK_WEB_BROWSER_CHANNEL", browser_channel)
                env.setdefault("XIAOMI_MIMO_WEB_BROWSER_CHANNEL", browser_channel)
        env.setdefault("NO_PROXY", "localhost,127.0.0.1,::1")
        env.setdefault("no_proxy", env["NO_PROXY"])
        env["DEEPSEEK_WEB_DISABLE_PROXY"] = "0" if use_system_proxy_setting() else "1"
        return env

    def run_python_probe(self, python_bin: str, code: str, timeout: int = 20) -> tuple[bool, str]:
        if not self.has_backend():
            return False, "未找到 freechat deer-flow backend。"
        try:
            result = subprocess.run(
                [python_bin, "-c", code],
                cwd=self.backend_dir,
                env=self.provider_env(),
                capture_output=True,
                text=True,
                timeout=timeout,
                **subprocess_no_window_kwargs(),
            )
        except Exception as exc:
            return False, str(exc)
        output = "\n".join(part for part in (result.stdout.strip(), result.stderr.strip()) if part)
        if result.returncode != 0:
            prefix = f"{python_bin} 退出码 {result.returncode}"
            output = f"{prefix}\n{output}" if output else f"{prefix}，无输出。"
        return result.returncode == 0, output

    def dependency_status(self) -> Dict[str, object]:
        if not self.has_backend():
            return {
                "ready": False,
                "python": "",
                "missing": [],
                "message": "未找到 freechat/deer-flow/backend provider 源码。",
            }
        probe = f"""
import importlib.util, json
missing = [name for name in {list(AUTOMATION_REQUIRED_MODULES)!r} if importlib.util.find_spec(name) is None]
try:
    import app.deepseek_local_provider  # noqa: F401
except Exception as exc:
    print(json.dumps({{"missing": missing, "provider_error": str(exc)}}, ensure_ascii=False))
    raise
print(json.dumps({{"missing": missing, "provider_error": ""}}, ensure_ascii=False))
raise SystemExit(1 if missing else 0)
"""
        last_output = ""
        failure_messages = []
        candidates = self.candidate_pythons()
        if not candidates:
            return {
                "ready": False,
                "python": "",
                "missing": list(AUTOMATION_REQUIRED_MODULES),
                "message": "未找到可用 Python。请先开启/安装 Python 运行环境，或重新点击“安装/修复插件依赖”。",
            }
        for python_bin in candidates:
            ok, output = self.run_python_probe(python_bin, probe)
            last_output = output or last_output
            if not ok:
                failure_messages.append(f"[{python_bin}]\n{output or 'provider 依赖探测失败，但没有输出。'}")
                continue
            if ok:
                browser_ok, browser_output = self.browser_status(python_bin)
                if not browser_ok:
                    last_output = browser_output or last_output
                    failure_messages.append(f"[{python_bin}]\n{browser_output or '浏览器探测失败，但没有输出。'}")
                    continue
                return {
                    "ready": True,
                    "python": python_bin,
                    "missing": [],
                    "message": "依赖可用。",
                }
        missing = list(AUTOMATION_REQUIRED_MODULES)
        provider_error = ""
        try:
            payload = {}
            for line in reversed((last_output or "").splitlines()):
                line = line.strip()
                if not line.startswith("{"):
                    continue
                payload = json.loads(line)
                break
            if isinstance(payload.get("missing"), list):
                missing = payload["missing"]
            provider_error = str(payload.get("provider_error") or "")
        except Exception:
            pass
        message = provider_error or "\n\n".join(failure_messages[-4:]) or last_output or "依赖检查失败。"
        return {
            "ready": False,
            "python": "",
            "missing": missing,
            "message": message,
        }

    def browser_status(self, python_bin: str) -> tuple[bool, str]:
        browser_channel_path = os.path.join(self.plugin_root, "browser-channel.txt")
        channel_ok, channel_output = self.run_python_probe(python_bin, browser_channel_probe_code(), timeout=60)
        if channel_ok:
            channel = (channel_output or "").splitlines()[-1].strip()
            try:
                os.makedirs(self.plugin_root, exist_ok=True)
                with open(browser_channel_path, "w", encoding="utf-8") as f:
                    f.write(channel)
            except OSError:
                pass
            return True, f"system browser channel ok: {channel}"
        try:
            if os.path.isfile(browser_channel_path):
                os.remove(browser_channel_path)
        except OSError:
            pass
        channel_detail = channel_output or "未检测到可由 Playwright 启动的系统 Edge/Chrome。"
        probe = """
from playwright.sync_api import sync_playwright
p = sync_playwright().start()
try:
    browser = p.chromium.launch(headless=True)
    browser.close()
finally:
    p.stop()
print("chromium ok")
"""
        chromium_ok, chromium_output = self.run_python_probe(python_bin, probe, timeout=60)
        if chromium_ok:
            return True, chromium_output
        return False, f"{channel_detail}\n\nPlaywright Chromium 不可用：\n{chromium_output or '无输出。'}"

    def install_dependencies(self, status_callback=None) -> str:
        os.makedirs(self.plugin_root, exist_ok=True)
        python_bin = automation_venv_python(self.plugin_root)
        if not os.path.exists(python_bin):
            if status_callback:
                status_callback("创建插件虚拟环境...")
            venv.EnvBuilder(with_pip=True).create(os.path.join(self.plugin_root, ".venv"))
        commands = [
            [python_bin, "-m", "pip", "install", *pip_index_args(), "--upgrade", "pip"],
            [
                python_bin,
                "-m",
                "pip",
                "install",
                *pip_index_args(),
                "ddgs",
                "fastapi>=0.115.0",
                "uvicorn[standard]>=0.34.0",
                "pydantic>=2",
                "jsonschema",
                "langchain-core",
                "playwright",
                "httpx",
            ],
        ]
        for command in commands:
            if status_callback:
                status_callback("运行: " + " ".join(command[1:]))
            result = subprocess.run(
                command,
                cwd=self.plugin_root,
                capture_output=True,
                text=True,
                timeout=900,
                **subprocess_no_window_kwargs(),
            )
            if result.returncode != 0:
                detail = (result.stderr or result.stdout or "").strip()
                raise RuntimeError(detail[-2000:] or f"命令失败: {' '.join(command)}")
        browser_ok, browser_message = self.browser_status(python_bin)
        if not browser_ok:
            if status_callback:
                status_callback("未检测到系统 Edge/Chrome，安装 Playwright Chromium...")
            command = [python_bin, "-m", "playwright", "install", "chromium"]
            result = subprocess.run(
                command,
                cwd=self.plugin_root,
                capture_output=True,
                text=True,
                timeout=900,
                **subprocess_no_window_kwargs(),
            )
            if result.returncode != 0:
                detail = (result.stderr or result.stdout or "").strip()
                raise RuntimeError(detail[-2000:] or f"命令失败: {' '.join(command)}")
            browser_ok, browser_message = self.browser_status(python_bin)
        if not browser_ok:
            raise RuntimeError(browser_message or "Playwright Chromium 启动验证失败。")
        return python_bin

    def install_dependencies_command(self) -> str:
        plugin_root = self.plugin_root
        venv_dir = os.path.join(plugin_root, ".venv")
        python_bin = automation_venv_python(plugin_root)
        package_args = [
            "ddgs",
            "fastapi>=0.115.0",
            "uvicorn[standard]>=0.34.0",
            "pydantic>=2",
            "jsonschema",
            "langchain-core",
            "playwright",
            "httpx",
        ]
        browser_probe = python_exec_base64_code(browser_channel_probe_code())
        if platform.system() == "Windows":
            return POWERSHELL_COMMAND_PREFIX + f"""$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = '1'
$env:PYTHONUNBUFFERED = '1'
$env:PIP_DISABLE_PIP_VERSION_CHECK = '1'
$env:PYTHONPATH = {ps_quote(self.harness_path())} + [IO.Path]::PathSeparator + $env:PYTHONPATH
$VenvDir = {ps_quote(venv_dir)}
$PythonBin = {ps_quote(python_bin)}
$PluginRoot = {ps_quote(plugin_root)}
$BackendDir = {ps_quote(self.backend_dir)}
$PipIndexArgs = {ps_array(pip_index_args())}
$PipFallbackIndexes = {ps_pip_fallback_indexes()}
$PackageArgs = {ps_array(package_args)}

{windows_pip_helpers_powershell()}

Write-Host "[Agent Qt] 安装/修复自动化插件依赖"
Write-Host "插件目录: $PluginRoot"
Write-Host "Provider 源码: {self.backend_dir or '未找到'}"
if (-not $BackendDir) {{
    throw "未找到 provider 源码。"
}}

New-Item -ItemType Directory -Force -Path $VenvDir | Out-Null
if (-not (Test-Path -LiteralPath $PythonBin -PathType Leaf)) {{
    Write-Host "创建插件虚拟环境..."
    {windows_python_bootstrap_powershell()}
    & $BasePython -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) {{ exit $LASTEXITCODE }}
}}

Write-Host ""
Write-Host "[1/3] 升级 pip"
Invoke-AgentQtPipInstall -PythonBin $PythonBin -Arguments @('--upgrade', 'pip') -Optional

Write-Host ""
Write-Host "[2/3] 安装 provider Python 依赖"
Invoke-AgentQtPipInstall -PythonBin $PythonBin -Arguments $PackageArgs

Write-Host ""
Write-Host "[3/3] 检查系统 Edge/Chrome 或安装 Playwright Chromium"
Set-Location -LiteralPath $BackendDir
$BrowserChannel = (& $PythonBin -c {ps_quote(browser_probe)} 2>$null | Select-Object -Last 1).Trim()
if ($BrowserChannel) {{
    Write-Host "使用系统浏览器: $BrowserChannel"
    Set-Content -LiteralPath (Join-Path $VenvDir '..\\browser-channel.txt') -Value $BrowserChannel -Encoding UTF8
}} else {{
    & $PythonBin -m playwright install chromium
    if ($LASTEXITCODE -ne 0) {{ exit $LASTEXITCODE }}
}}

Write-Host ""
Write-Host "验证 provider 依赖包..."
& $PythonBin -c "import ddgs, fastapi, uvicorn, pydantic, jsonschema, langchain_core, playwright; print('provider packages OK')"
if ($LASTEXITCODE -ne 0) {{ exit $LASTEXITCODE }}

Write-Host ""
Write-Host "验证 provider 模块..."
& $PythonBin -c "import app.deepseek_local_provider; print('自动化插件依赖 OK')"
if ($LASTEXITCODE -ne 0) {{ exit $LASTEXITCODE }}

Write-Host ""
Write-Host "验证 Playwright 浏览器..."
if ($BrowserChannel) {{
    $env:BROWSER_CHANNEL = $BrowserChannel
    & $PythonBin -c "from playwright.sync_api import sync_playwright; import os; p=sync_playwright().start(); b=p.chromium.launch(channel=os.environ['BROWSER_CHANNEL'], headless=True); b.close(); p.stop(); print('Playwright browser OK: ' + os.environ['BROWSER_CHANNEL'])"
}} else {{
    & $PythonBin -c "from playwright.sync_api import sync_playwright; p=sync_playwright().start(); b=p.chromium.launch(headless=True); b.close(); p.stop(); print('Playwright browser OK: chromium')"
}}
if ($LASTEXITCODE -ne 0) {{ exit $LASTEXITCODE }}

Write-Host ""
Write-Host "自动化插件依赖安装完成: $PythonBin"
"""
        return f"""set -e
export PYTHONUTF8=1
export PYTHONUNBUFFERED=1
export PIP_DISABLE_PIP_VERSION_CHECK=1
export PYTHONPATH={shlex.quote(self.harness_path())}:$PYTHONPATH
VENV_DIR={shlex.quote(venv_dir)}
PYTHON_BIN={shlex.quote(python_bin)}
BACKEND_DIR={shlex.quote(self.backend_dir)}
echo "[Agent Qt] 安装/修复自动化插件依赖"
echo "插件目录: {plugin_root}"
echo "Provider 源码: {self.backend_dir or '未找到'}"
if [ -z "$BACKEND_DIR" ]; then
  echo "未找到 provider 源码。"
  exit 1
fi
mkdir -p "$VENV_DIR"
if [ ! -x "$PYTHON_BIN" ]; then
  echo "创建插件虚拟环境..."
  if command -v python3 >/dev/null 2>&1; then
    BASE_PYTHON="$(command -v python3)"
  elif command -v python >/dev/null 2>&1; then
    BASE_PYTHON="$(command -v python)"
  else
    echo "未找到 python3/python，无法创建虚拟环境。"
    exit 1
  fi
  "$BASE_PYTHON" -m venv "$VENV_DIR"
fi
echo
echo "[1/3] 升级 pip"
"$PYTHON_BIN" -m pip install {posix_join(pip_index_args())} --upgrade pip
echo
echo "[2/3] 安装 provider Python 依赖"
"$PYTHON_BIN" -m pip install {posix_join(pip_index_args())} {posix_join(package_args)}
echo
echo "[3/3] 检查系统 Edge/Chrome 或安装 Playwright Chromium"
cd "$BACKEND_DIR"
BROWSER_CHANNEL="$("$PYTHON_BIN" -c {shlex.quote(browser_probe)} 2>/dev/null || true)"
if [ -n "$BROWSER_CHANNEL" ]; then
  echo "使用系统浏览器: $BROWSER_CHANNEL"
  printf "%s" "$BROWSER_CHANNEL" > "$VENV_DIR/../browser-channel.txt"
else
  "$PYTHON_BIN" -m playwright install chromium
fi
echo
echo "验证 provider 模块..."
"$PYTHON_BIN" -c "import app.deepseek_local_provider; print('自动化插件依赖 OK')"
echo
echo "验证 Playwright 浏览器..."
if [ -n "$BROWSER_CHANNEL" ]; then
  BROWSER_CHANNEL="$BROWSER_CHANNEL" "$PYTHON_BIN" -c "from playwright.sync_api import sync_playwright; import os; p=sync_playwright().start(); b=p.chromium.launch(channel=os.environ['BROWSER_CHANNEL'], headless=True); b.close(); p.stop(); print('Playwright browser OK: ' + os.environ['BROWSER_CHANNEL'])"
else
  "$PYTHON_BIN" -c "from playwright.sync_api import sync_playwright; p=sync_playwright().start(); b=p.chromium.launch(headless=True); b.close(); p.stop(); print('Playwright browser OK: chromium')"
fi
echo
echo "自动化插件依赖安装完成: $PYTHON_BIN"
"""

    def active_python(self) -> str:
        status = self.dependency_status()
        if status.get("ready"):
            return str(status.get("python") or "")
        plugin_python = automation_venv_python(self.plugin_root)
        return plugin_python if os.path.exists(plugin_python) else ""

    def request_json(
        self,
        method: str,
        path: str,
        payload: Optional[dict] = None,
        timeout: int = 30,
        attempts: int = 1,
        retry_callback: Optional[Callable[[str], None]] = None,
    ) -> dict:
        started = time.perf_counter()
        encode_started = time.perf_counter()
        data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        encode_ms = int((time.perf_counter() - encode_started) * 1000)
        request = urllib.request.Request(f"{self.base_url}{path}", data=data, method=method)
        if payload is not None:
            request.add_header("Content-Type", "application/json")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        attempts = max(1, int(attempts or 1))
        for attempt in range(1, attempts + 1):
            try:
                http_started = time.perf_counter()
                with opener.open(request, timeout=timeout) as response:
                    raw = response.read()
                http_ms = int((time.perf_counter() - http_started) * 1000)
                decode_started = time.perf_counter()
                decoded = json.loads(raw.decode("utf-8"))
                decode_ms = int((time.perf_counter() - decode_started) * 1000)
                total_ms = int((time.perf_counter() - started) * 1000)
                if total_ms >= 250 or encode_ms >= 80 or decode_ms >= 80:
                    logger.warning(
                        "Provider request timing method=%s path=%s request_bytes=%d response_bytes=%d encode_ms=%d http_ms=%d decode_ms=%d total_ms=%d attempt=%d/%d",
                        method,
                        path,
                        len(data or b""),
                        len(raw),
                        encode_ms,
                        http_ms,
                        decode_ms,
                        total_ms,
                        attempt,
                        attempts,
                    )
                return decoded
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                message = detail.strip() or f"HTTP {exc.code}"
                try:
                    payload_detail = json.loads(detail)
                    if isinstance(payload_detail, dict) and payload_detail.get("detail"):
                        message = str(payload_detail["detail"])
                except Exception:
                    pass
                if "input box not found" in message.lower() or "please login" in message.lower():
                    message = (
                        "网页登录还没有准备好，provider 没找到聊天输入框。\n\n"
                        "请在设置里点击“打开网页登录”，完成登录后保持页面在聊天界面，再重新发送。"
                    )
                if exc.code in PROVIDER_TRANSIENT_HTTP_CODES and attempt < attempts:
                    if retry_callback is not None:
                        try:
                            retry_callback(provider_retry_status_message(path, attempt + 1, attempts, message))
                        except Exception:
                            pass
                    logger.warning(
                        "Provider transient HTTP error method=%s path=%s code=%s attempt=%d/%d message=%s",
                        method,
                        path,
                        exc.code,
                        attempt,
                        attempts,
                        message[:300],
                    )
                    time.sleep(min(15.0, 5.0 * attempt))
                    continue
                raise RuntimeError(f"HTTP {exc.code}: {message}") from exc
            except (TimeoutError, urllib.error.URLError, ConnectionError) as exc:
                if attempt < attempts:
                    if retry_callback is not None:
                        try:
                            retry_callback(provider_retry_status_message(path, attempt + 1, attempts, str(exc)))
                        except Exception:
                            pass
                    logger.warning(
                        "Provider transient network error method=%s path=%s attempt=%d/%d error=%s",
                        method,
                        path,
                        attempt,
                        attempts,
                        str(exc)[:300],
                    )
                    time.sleep(min(15.0, 5.0 * attempt))
                    continue
                raise RuntimeError(f"provider 网络请求失败（已重试 {attempts} 次）: {exc}") from exc
        raise RuntimeError("provider 请求失败。")

    def health(self) -> bool:
        try:
            payload = self.request_json("GET", "/health", timeout=3)
            return payload.get("status") == "ok"
        except Exception:
            return False

    def provider_source_mtime(self) -> float:
        roots = [
            os.path.join(self.backend_dir, "app"),
            os.path.join(self.backend_dir, "packages", "harness"),
        ]
        newest = 0.0
        for root in roots:
            if not os.path.isdir(root):
                continue
            for dirpath, _dirnames, filenames in os.walk(root):
                for name in filenames:
                    if not name.endswith(".py"):
                        continue
                    try:
                        newest = max(newest, os.path.getmtime(os.path.join(dirpath, name)))
                    except OSError:
                        pass
        return newest

    def provider_process_stale(self) -> bool:
        try:
            started_at = os.path.getmtime(self.pid_file)
        except OSError:
            return False
        return self.provider_source_mtime() > started_at + 0.5

    def stop_provider_process(self, wait_timeout: float = 6.0, aggressive: bool = False):
        try:
            with open(self.pid_file, "r", encoding="utf-8") as f:
                pid = int(f.read().strip() or "0")
        except (OSError, ValueError):
            return
        if pid <= 0:
            return
        logger.warning("Stopping automation provider pid=%s", pid)
        provider_pgid = 0
        if platform.system() != "Windows":
            try:
                provider_pgid = os.getpgid(pid)
            except OSError:
                provider_pgid = 0
        try:
            if platform.system() == "Windows":
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    capture_output=True,
                    text=True,
                    timeout=8,
                    **subprocess_no_window_kwargs(),
                )
            else:
                os.kill(pid, signal.SIGTERM)
        except Exception:
            pass
        deadline = time.time() + max(0.0, float(wait_timeout))
        while time.time() < deadline:
            if not process_alive(pid):
                break
            time.sleep(0.2)
        if aggressive and process_alive(pid):
            try:
                if platform.system() == "Windows":
                    subprocess.run(
                        ["taskkill", "/PID", str(pid), "/T", "/F"],
                        capture_output=True,
                        text=True,
                        timeout=3,
                        **subprocess_no_window_kwargs(),
                    )
                else:
                    os.kill(pid, signal.SIGKILL)
            except Exception:
                pass
        if platform.system() != "Windows" and provider_pgid > 0 and provider_pgid != os.getpgrp():
            try:
                # Uvicorn gets a graceful SIGTERM first; the process group is only
                # a safety net for Playwright/Chrome children that outlive it.
                os.killpg(provider_pgid, signal.SIGKILL if aggressive or process_alive(pid) else signal.SIGTERM)
            except Exception:
                pass
        try:
            current_pid = 0
            try:
                with open(self.pid_file, "r", encoding="utf-8") as f:
                    current_pid = int(f.read().strip() or "0")
            except (OSError, ValueError):
                current_pid = pid
            if current_pid == pid:
                os.remove(self.pid_file)
        except OSError:
            pass

    def start_provider(self) -> str:
        started = time.perf_counter()
        if self.health() and not self.provider_process_stale():
            logger.warning("Automation provider ready health_ms=%d", int((time.perf_counter() - started) * 1000))
            return f"provider 已运行: {self.base_url}"
        if self.health():
            logger.warning("Automation provider stale; restarting source_mtime=%s", self.provider_source_mtime())
            self.stop_provider_process()
        status = self.dependency_status()
        if not status.get("ready"):
            raise RuntimeError("插件依赖未就绪，请先安装/修复插件依赖。\n" + str(status.get("message", "")))
        python_bin = str(status["python"])
        os.makedirs(self.log_dir, exist_ok=True)
        log = open(self.log_file, "a", encoding="utf-8")
        popen_kwargs = {
            "cwd": self.backend_dir,
            "env": self.provider_env(),
            "stdout": log,
            "stderr": subprocess.STDOUT,
        }
        if platform.system() == "Windows":
            popen_kwargs.update(
                subprocess_no_window_kwargs(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
            )
        else:
            popen_kwargs["start_new_session"] = True
        process = subprocess.Popen(
            [
                python_bin,
                "-m",
                "uvicorn",
                "app.deepseek_local_provider:app",
                "--host",
                self.host,
                "--port",
                str(self.port),
                "--log-level",
                "warning",
            ],
            **popen_kwargs,
        )
        os.makedirs(self.plugin_root, exist_ok=True)
        with open(self.pid_file, "w", encoding="utf-8") as f:
            f.write(str(process.pid))
        deadline = time.time() + 45
        while time.time() < deadline:
            if self.health():
                logger.warning("Automation provider started startup_ms=%d", int((time.perf_counter() - started) * 1000))
                return f"provider 已启动: {self.base_url}"
            time.sleep(0.5)
        tail = ""
        try:
            with open(self.log_file, "r", encoding="utf-8", errors="replace") as f:
                tail = f.read()[-2000:]
        except OSError:
            pass
        raise RuntimeError(f"provider 启动超时: {self.base_url}\n{tail}")

    def open_login(self, model: str) -> str:
        self.start_provider()
        payload = self.request_json("POST", f"/debug/open-login?model={urllib.parse.quote(model)}", timeout=60)
        return f"已打开登录页: {payload.get('url', self.base_url)}"

    def chat(
        self,
        messages: List[Dict[str, str]],
        model: str,
        thread_id: str,
        *,
        thinking_enabled: Optional[bool] = None,
        expert_mode_enabled: Optional[bool] = None,
        retry_callback: Optional[Callable[[str], None]] = None,
    ) -> str:
        self.start_provider()
        payload = self.request_json(
            "POST",
            "/v1/chat/completions",
            {
                "model": model,
                "messages": messages,
                "temperature": 0,
                "user": thread_id,
                "thinking_enabled": thinking_enabled,
                "expert_mode_enabled": expert_mode_enabled,
                "output_protocol": "plain",
                "extra_body": {
                    "output_protocol": "plain",
                    "thinking_enabled": thinking_enabled,
                    "expert_mode_enabled": expert_mode_enabled,
                },
            },
            timeout=900,
            attempts=PROVIDER_REQUEST_RETRY_ATTEMPTS,
            retry_callback=retry_callback,
        )
        try:
            return unwrap_provider_text(str(payload["choices"][0]["message"].get("content") or ""))
        except Exception as exc:
            raise RuntimeError(f"provider 返回格式异常: {payload}") from exc

    def web_research(
        self,
        query: str,
        model: str,
        thread_id: str,
        *,
        thinking_enabled: Optional[bool] = None,
        expert_mode_enabled: Optional[bool] = None,
        max_results: int = 5,
        retry_callback: Optional[Callable[[str], None]] = None,
    ) -> str:
        self.start_provider()
        payload = self.request_json(
            "POST",
            "/v1/responses",
            {
                "model": model,
                "input": query,
                "user": thread_id,
                "stream": False,
                "thinking_enabled": thinking_enabled,
                "expert_mode_enabled": expert_mode_enabled,
                "instructions": (
                    "请先执行网页搜索，再基于搜索结果用中文给出简洁但有用的回答。"
                    "优先提炼用户真正需要的事实、链接线索和下一步建议，不要解释工具协议。"
                ),
                "tools": [
                    {
                        "type": "web_search_preview",
                        "max_results": max(1, int(max_results or 5)),
                    }
                ],
            },
            timeout=900,
            attempts=PROVIDER_REQUEST_RETRY_ATTEMPTS,
            retry_callback=retry_callback,
        )
        summary = summarize_web_research_response(payload)
        if summary:
            return summary
        raise RuntimeError(f"provider 网页搜索返回格式异常: {payload}")

    def response_preview(self, model: str, thread_id: str) -> dict:
        # Preview polling is observational. The chat worker owns provider startup;
        # polling must not get trapped in the long startup path after cancellation.
        return self.request_json(
            "GET",
            f"/debug/response-preview?model={urllib.parse.quote(model)}&user={urllib.parse.quote(thread_id)}",
            timeout=1,
        )

    def cancel_generation(self, model: str, thread_id: str) -> bool:
        if not self.health():
            return False
        try:
            self.request_json(
                "POST",
                f"/debug/cancel-generations?model={urllib.parse.quote(model)}&user={urllib.parse.quote(thread_id)}",
                timeout=3,
            )
            return True
        except Exception:
            logger.warning("Automation provider cancel request failed.", exc_info=True)
            return False

    def log_tail(self, max_chars: int = 3000) -> str:
        try:
            with open(self.log_file, "r", encoding="utf-8", errors="replace") as f:
                return f.read()[-max(1, max_chars):]
        except OSError:
            return ""

    def error_with_log_hint(self, message: str) -> str:
        parts = [str(message or "").strip() or "自动化插件出错。"]
        parts.append(f"日志文件: {self.log_file}")
        tail = self.log_tail(1600).strip()
        if tail:
            parts.append("最近日志:\n" + tail)
        return "\n\n".join(parts)

    def status_text(self) -> str:
        dep = self.dependency_status()
        lines = [
            f"源码: {self.backend_dir or '未找到'}",
            f"服务: {self.base_url} ({'运行中' if self.health() else '未运行'})",
            f"依赖: {'可用' if dep.get('ready') else '缺失'}",
        ]
        if dep.get("python"):
            lines.append(f"Python: {dep['python']}")
        if dep.get("missing"):
            lines.append("缺失模块: " + ", ".join(str(x) for x in dep["missing"]))
        if dep.get("message") and not dep.get("ready"):
            lines.append(str(dep["message"])[-600:])
        return "\n".join(lines)


class AutomationSetupWorker(QThread):
    status_signal = Signal(str)
    finished_signal = Signal(bool, str)

    def __init__(self, manager: AutomationProviderManager, action: str, model: str):
        super().__init__()
        self.manager = manager
        self.action = action
        self.model = model

    def run(self):
        try:
            if self.action == "install":
                self.manager.install_dependencies(self.status_signal.emit)
                message = "插件依赖安装完成。"
            elif self.action == "start":
                message = self.manager.start_provider()
            elif self.action == "login":
                message = self.manager.open_login(self.model)
            else:
                message = self.manager.status_text()
            self.finished_signal.emit(True, message)
        except Exception as exc:
            self.finished_signal.emit(False, str(exc))


class PythonRuntimeSetupWorker(QThread):
    status_signal = Signal(str)
    finished_signal = Signal(bool, str)

    def __init__(self, action: str):
        super().__init__()
        self.action = action

    def run(self):
        try:
            if self.action == "install":
                python_bin = install_agent_python_runtime(self.status_signal.emit)
                message = f"Python 运行环境安装完成：{python_bin}"
            else:
                message = agent_runtime_status_text()
            self.finished_signal.emit(True, message)
        except Exception as exc:
            self.finished_signal.emit(False, str(exc))


class AutomationChatWorker(QThread):
    finished_signal = Signal(str, str)
    status_signal = Signal(str)

    def __init__(
        self,
        manager: AutomationProviderManager,
        messages: List[Dict[str, str]],
        model: str,
        thread_id: str,
        *,
        thinking_enabled: Optional[bool] = None,
        expert_mode_enabled: Optional[bool] = None,
    ):
        super().__init__()
        self.manager = manager
        self.messages = messages
        self.model = model
        self.thread_id = thread_id
        self.thinking_enabled = thinking_enabled
        self.expert_mode_enabled = expert_mode_enabled

    def run(self):
        try:
            started = time.perf_counter()
            text = self.manager.chat(
                self.messages,
                self.model,
                self.thread_id,
                thinking_enabled=self.thinking_enabled,
                expert_mode_enabled=self.expert_mode_enabled,
                retry_callback=self.status_signal.emit,
            )
            logger.warning(
                "Automation chat worker done elapsed_ms=%d message_chars=%d response_chars=%d thinking_enabled=%s expert_mode_enabled=%s",
                int((time.perf_counter() - started) * 1000),
                sum(len(str(message.get("content") or "")) for message in self.messages),
                len(text),
                self.thinking_enabled,
                self.expert_mode_enabled,
            )
            self.finished_signal.emit(text, "")
        except Exception as exc:
            self.finished_signal.emit("", str(exc))


class AutomationContextBuildWorker(QThread):
    status_signal = Signal(str)
    finished_signal = Signal(int, list, str, str)

    def __init__(
        self,
        manager: AutomationProviderManager,
        *,
        request_serial: int,
        system_context: str,
        current_prompt: str,
        full_chunks: List[str],
        lean_chunks: List[str],
        minimal_chunks: List[str],
        token_budget: int,
        provider_byte_budget: int,
        summary_model: str,
        thread_id: str,
    ):
        super().__init__()
        self.manager = manager
        self.request_serial = request_serial
        self.system_context = system_context
        self.current_prompt = current_prompt
        self.full_chunks = list(full_chunks)
        self.lean_chunks = list(lean_chunks)
        self.minimal_chunks = list(minimal_chunks)
        self.token_budget = token_budget
        self.provider_byte_budget = provider_byte_budget
        self.summary_model = summary_model
        self.thread_id = thread_id

    def build_payload(self, history: str) -> str:
        return "\n\n".join([
            plaintext_fence("第一段：系统提示词", self.system_context),
            plaintext_fence("第二段：历史对话", history),
            plaintext_fence("第三段：当前指令", self.current_prompt),
            plaintext_fence("第四段：生成前提醒", AUTOMATION_FINAL_REMINDER),
        ])

    def llm_summarize_history(self, old_text: str, recent_text: str, input_budget: int) -> str:
        old_text = summarize_fenced_code_blocks_for_context(old_text)
        if input_budget > 0:
            old_text = text_within_utf8_budget(old_text, max(20000, min(120000, input_budget)))
        else:
            old_text = text_within_token_budget(old_text, min(AUTOMATION_CONTEXT_COMPACT_SUMMARY_TOKENS, max(12000, self.token_budget // 2)))
        target_tokens = max(2000, min(AUTOMATION_CONTEXT_COMPACT_SUMMARY_TOKENS, self.token_budget // 3))
        prompt = (
            "请把下面的较早 Agent Qt 会话历史压缩成连续上下文摘要，用中文 plaintext 输出。\n"
            "只输出摘要，不要寒暄，不要 Markdown 代码块。\n"
            "必须保留：用户真实目标、关键决策、已完成/未完成事项、文件路径、错误与修复线索、终端 registry_json/log_path/pid、重要 diff/commit/函数名、仍需遵守的约束。\n"
            "可以删除：重复寒暄、大段低价值日志、完整代码正文、已被后续结论覆盖的中间尝试。\n"
            f"目标长度：约 {context_k_label(target_tokens)} 以内。\n\n"
            "【较早历史】\n"
            f"{old_text or '（无较早历史）'}\n\n"
            "【近期历史提示】\n"
            "近期完整历史会原文附在摘要后方；不要重复近期内容，只补足连续性。\n"
            f"{text_within_token_budget(recent_text, 4000) if recent_text else '（暂无近期历史）'}"
        )
        summary = self.manager.chat(
            [{"role": "user", "content": prompt}],
            self.summary_model,
            self.thread_id + "-context-compact",
        ).strip()
        if not summary:
            raise RuntimeError("简单模式上下文压缩返回为空。")
        return summary

    def run(self):
        try:
            history_text, compacted = compact_history_text_from_chunks(self.full_chunks, self.token_budget)
            provider_compaction = "programmatic" if compacted else "none"
            if compacted:
                self.status_signal.emit("compacting")
            payload = self.build_payload(history_text)
            payload_bytes = utf8_len(payload)

            if self.provider_byte_budget > 0 and payload_bytes > self.provider_byte_budget:
                self.status_signal.emit("compacting")
                history_text, _ = compact_history_text_from_chunks(self.lean_chunks, self.token_budget)
                payload = self.build_payload(history_text)
                payload_bytes = utf8_len(payload)
                provider_compaction = "semantic_lean"

            if self.provider_byte_budget > 0 and payload_bytes > self.provider_byte_budget:
                self.status_signal.emit("compacting")
                history_text, _ = compact_history_text_from_chunks(self.minimal_chunks, self.token_budget)
                payload = self.build_payload(history_text)
                payload_bytes = utf8_len(payload)
                provider_compaction = "semantic_minimal"

            payload_tokens = estimate_context_tokens(payload)
            if self.provider_byte_budget <= 0 and payload_tokens > self.token_budget:
                self.status_signal.emit("llm_compacting")
                old_text, recent_text = split_history_for_compaction(self.minimal_chunks, self.token_budget)
                try:
                    compact_old = self.llm_summarize_history(old_text, recent_text, 0)
                    history_text = (
                        "【LLM Compact 历史摘要】\n"
                        "以下是较早对话、执行结果和 diff 经简单模式模型压缩后的 plaintext 摘要；请作为连续上下文参考，不要把它当作新需求重复执行。\n"
                        f"{compact_old}\n\n"
                        "【近期完整历史】\n"
                        f"{recent_text or '（暂无近期历史）'}"
                    )
                    payload = self.build_payload(text_within_token_budget(history_text, self.token_budget))
                    payload_bytes = utf8_len(payload)
                    provider_compaction = "llm_summary"
                except Exception:
                    logger.warning("LLM token-budget context compaction failed; falling back to token truncation.", exc_info=True)

            if self.provider_byte_budget <= 0 and estimate_context_tokens(payload) > self.token_budget:
                empty_payload_tokens = estimate_context_tokens(self.build_payload(""))
                history_token_budget = max(1, self.token_budget - empty_payload_tokens - 200)
                history_text = text_within_token_budget(history_text, history_token_budget)
                payload = self.build_payload(history_text)
                payload_bytes = utf8_len(payload)
                provider_compaction = "token_fallback"

            if self.provider_byte_budget > 0 and payload_bytes > self.provider_byte_budget:
                self.status_signal.emit("llm_compacting")
                old_text, recent_text = split_history_for_compaction(self.minimal_chunks, self.token_budget)
                try:
                    compact_old = self.llm_summarize_history(old_text, recent_text, self.provider_byte_budget)
                    history_text = (
                        "【LLM Compact 历史摘要】\n"
                        "以下是较早对话、执行结果和 diff 经简单模式模型压缩后的 plaintext 摘要；请作为连续上下文参考，不要把它当作新需求重复执行。\n"
                        f"{compact_old}\n\n"
                        "【近期完整历史】\n"
                        f"{recent_text or '（暂无近期历史）'}"
                    )
                    payload = self.build_payload(text_within_token_budget(history_text, self.token_budget))
                    payload_bytes = utf8_len(payload)
                    provider_compaction = "llm_summary"
                except Exception:
                    logger.warning("LLM context compaction failed; falling back to byte truncation.", exc_info=True)

            if self.provider_byte_budget > 0 and payload_bytes > self.provider_byte_budget:
                empty_history_payload = self.build_payload("")
                history_byte_budget = max(0, self.provider_byte_budget - utf8_len(empty_history_payload) - 512)
                history_text = text_within_utf8_budget(history_text, history_byte_budget)
                payload = self.build_payload(history_text)
                provider_compaction = "byte_fallback"

            self.finished_signal.emit(self.request_serial, [{"role": "user", "content": payload}], "", provider_compaction)
        except Exception as exc:
            self.finished_signal.emit(self.request_serial, [], str(exc), "")


class AutomationPreviewWorker(QThread):
    preview_signal = Signal(int, dict)

    def __init__(self, manager: AutomationProviderManager, model: str, thread_id: str, serial: int):
        super().__init__()
        self.manager = manager
        self.model = model
        self.thread_id = thread_id
        self.serial = serial
        self._running = True
        self._last_signature: tuple = ()
        self._last_emit_at = 0.0

    def stop(self):
        self._running = False

    def run(self):
        while self._running:
            try:
                started = time.perf_counter()
                preview = self.manager.response_preview(self.model, self.thread_id)
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                if elapsed_ms >= 150:
                    logger.warning(
                        "Automation preview poll timing elapsed_ms=%d chars=%d source=%s",
                        elapsed_ms,
                        int(preview.get("chars") or 0),
                        str(preview.get("source") or ""),
                    )
                signature = (
                    str(preview.get("source") or ""),
                    int(preview.get("chars") or 0),
                    bool(preview.get("done")),
                    str(preview.get("error") or ""),
                )
                now = time.time()
                if signature != self._last_signature or now - self._last_emit_at >= 1.2:
                    self._last_signature = signature
                    self._last_emit_at = now
                    if self._running:
                        self.preview_signal.emit(self.serial, preview)
            except Exception:
                pass
            self.msleep(300)


class HistorySaveWorker(QThread):
    finished_signal = Signal(int, bool, str)

    def __init__(self, root: str, thread_id: str, entries: List[Dict[str, object]], generation: int):
        super().__init__()
        self.root = root
        self.thread_id = thread_id
        self.entries = entries
        self.generation = generation

    def run(self):
        tmp_path = ""
        ok = False
        try:
            os.makedirs(history_dir(self.root, self.thread_id), exist_ok=True)
            tmp_path = history_path(self.root, self.thread_id) + f".{self.generation}.tmp"
            payload = {
                "version": HISTORY_VERSION,
                "updated_at": datetime.now().isoformat(timespec="seconds"),
                "entries": self.entries,
            }
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            ok = True
        except OSError:
            ok = False
        self.finished_signal.emit(self.generation, ok, tmp_path)


def wechat_strip_markdown_code(text: str, keep_summary: bool = False) -> str:
    parts = split_markdown_fenced_blocks(str(text or ""))
    rebuilt: List[str] = []
    for part in parts:
        if part.get("type") == "code":
            lang = (part.get("lang") or "code").strip() or "code"
            code = part.get("text") or ""
            summary = code_block_summary(lang, code)
            rebuilt.append(f"[{lang} 代码块：{summary}]" if keep_summary else "")
        else:
            rebuilt.append(part.get("text") or "")
    text = "".join(rebuilt)
    text = re.sub(r"<!--\s*[A-Za-z0-9_ -]+ block \d+\s*-->", "", text)
    text = strip_wechat_send_file_markers(text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def strip_inline_heredoc_for_wechat(text: str) -> str:
    lines = str(text or "").splitlines()
    cleaned: List[str] = []
    skipping_until = ""
    heredoc_re = re.compile(r"<<\s*['\"]?([A-Za-z_][A-Za-z0-9_]*)['\"]?")
    for line in lines:
        stripped = line.strip()
        if skipping_until:
            if stripped == skipping_until:
                skipping_until = ""
            continue
        match = heredoc_re.search(line)
        if match:
            skipping_until = match.group(1)
            if not cleaned or cleaned[-1] != "... 已省略文件写入命令和正文 ...":
                cleaned.append("... 已省略文件写入命令和正文 ...")
            continue
        if re.match(r"^\s*(?:<!DOCTYPE\s+html|<html\b|<svg\b|<head\b|<body\b|<style\b|<script\b)", line, re.I):
            cleaned.append("... 已省略文件正文 ...")
            break
        cleaned.append(line)
    return "\n".join(cleaned).strip()


def sanitize_wechat_visible_text(text: str, *, keep_code_summary: bool = False, limit: int = 1200) -> str:
    content = mask_low_value_context_markers_for_display(str(text or ""))
    content = wechat_strip_markdown_code(content, keep_summary=keep_code_summary)
    content = strip_inline_heredoc_for_wechat(content)
    content = strip_automation_done_marker(content)
    content = re.sub(r"\n{3,}", "\n\n", content).strip()
    return text_within_utf8_budget(content, limit)


def is_low_value_wechat_result_summary(text: str) -> bool:
    value = str(text or "").strip()
    if not value:
        return True
    normalized = value.replace("\r\n", "\n")
    if "... 已省略文件写入命令和正文 ..." in normalized:
        simplified = normalized.replace("执行结果：", "").replace("Execution log:", "").strip()
        simplified = simplified.replace("... 已省略文件写入命令和正文 ...", "").replace("... 已省略文件正文 ...", "").strip()
        if not simplified:
            return True
    low_value_markers = [
        "返回空（grep 未匹配到关键词",
        "扩展指令已处理，未返回额外输出。",
    ]
    return any(marker in normalized for marker in low_value_markers) and len(normalized) < 120


def extract_wechat_send_file_targets(text: str) -> List[str]:
    targets: List[str] = []
    for line in str(text or "").splitlines():
        stripped = line.strip()
        upper = stripped.upper()
        if upper.startswith("AGENT_WECHAT_SEND_FILE:") or upper.startswith("AGENT_WECHAT_SEND_FILE："):
            _, _, tail = stripped.partition(":" if ":" in stripped else "：")
            candidates = [part.strip().strip("\"'") for part in re.split(r"\s*,\s*", tail) if part.strip()]
        else:
            candidates = [
                directive.split(":", 1)[1].strip()
                for directive in normalize_terminal_extension_directive(stripped)
                if directive.upper().startswith("AGENT_WECHAT_SEND_FILE:")
            ]
        for target in candidates:
            if target and target not in targets:
                targets.append(target)
    return targets[:5]


def extract_wechat_schedule_trigger_payloads(text: str) -> tuple[List[Dict[str, object]], List[str]]:
    payloads: List[Dict[str, object]] = []
    errors: List[str] = []
    for line in str(text or "").splitlines():
        stripped = line.strip()
        upper = stripped.upper()
        if not upper.startswith("AGENT_WECHAT_CREATE_SCHEDULE:") and not upper.startswith("AGENT_WECHAT_CREATE_SCHEDULE："):
            continue
        _, _, tail = stripped.partition(":" if ":" in stripped else "：")
        tail = terminal_extension_payload_text(tail)
        try:
            payload = json.loads(tail.strip())
        except json.JSONDecodeError as exc:
            errors.append(f"计划 trigger JSON 无法解析：{exc}")
            continue
        if isinstance(payload, dict):
            payloads.append(payload)
        else:
            errors.append("计划 trigger 必须是 JSON 对象。")
    return payloads[:3], errors[:3]


def extract_wechat_schedule_action_payloads(text: str) -> tuple[List[Dict[str, object]], List[str]]:
    payloads: List[Dict[str, object]] = []
    errors: List[str] = []
    for line in str(text or "").splitlines():
        stripped = line.strip()
        upper = stripped.upper()
        if not upper.startswith("AGENT_WECHAT_SCHEDULE_ACTION:") and not upper.startswith("AGENT_WECHAT_SCHEDULE_ACTION："):
            continue
        _, _, tail = stripped.partition(":" if ":" in stripped else "：")
        tail = terminal_extension_payload_text(tail)
        try:
            payload = json.loads(tail.strip())
        except json.JSONDecodeError as exc:
            errors.append(f"计划动作 JSON 无法解析：{exc}")
            continue
        if isinstance(payload, dict):
            payloads.append(payload)
        else:
            errors.append("计划动作必须是 JSON 对象。")
    return payloads[:5], errors[:3]


def collect_schedule_extension_payloads(text: str) -> tuple[List[Dict[str, object]], List[Dict[str, object]], List[str]]:
    payloads, errors = extract_wechat_schedule_trigger_payloads(text)
    actions, action_errors = extract_wechat_schedule_action_payloads(text)
    return payloads, actions, [*errors, *action_errors]


def apply_schedule_extension_payloads(
    root: str,
    schedule_payloads: List[Dict[str, object]],
    schedule_actions: List[Dict[str, object]],
    *,
    notify_user: str = "",
    notify_context_token: str = "",
    notify_thread_id: str = "",
) -> tuple[List[str], List[str], List[str]]:
    created_schedules: List[str] = []
    schedule_action_replies: List[str] = []
    schedule_errors: List[str] = []
    for payload in schedule_payloads[:3]:
        try:
            schedule_item = schedule_from_wechat_trigger_payload(
                root,
                payload,
                notify_user=notify_user,
                notify_context_token=notify_context_token,
                notify_thread_id=notify_thread_id,
            )
            created_schedules.append(f"{schedule_item.get('title')}（{format_schedule_time(schedule_item)}）")
        except Exception as exc:
            schedule_errors.append(str(exc))
    for payload in schedule_actions[:5]:
        action = str(payload.get("action") or "").strip().lower()
        if action == "list":
            schedule_action_replies.append(schedules_summary_text(load_workspace_schedules(root)))
            continue
        if action == "delete":
            target = str(payload.get("target") or payload.get("id") or payload.get("title") or "").strip()
            if not target:
                schedule_errors.append("删除计划缺少 target。")
                continue
            if delete_workspace_schedule(root, target):
                schedule_action_replies.append(f"已删除计划：{target}")
            else:
                schedule_errors.append(f"未找到计划：{target}")
            continue
        if action == "update":
            target = str(payload.get("target") or payload.get("id") or payload.get("title") or "").strip()
            if not target:
                schedule_errors.append("修改计划缺少 target。")
                continue
            try:
                updated = update_workspace_schedule_from_action(root, target, payload)
                note = str(updated.get("_schedule_action_note") or "").strip()
                note_suffix = f"，{note}" if note else ""
                schedule_action_replies.append(f"已修改计划：{updated.get('title')}（{format_schedule_time(updated)}）{note_suffix}")
            except Exception as exc:
                schedule_errors.append(str(exc))
            continue
        schedule_errors.append(f"未知计划动作：{action or '空'}")
    return created_schedules, schedule_action_replies, schedule_errors


def schedule_extension_reply(
    created_schedules: List[str],
    schedule_action_replies: List[str],
    schedule_errors: List[str],
) -> str:
    parts: List[str] = []
    if created_schedules:
        parts.append("已创建计划：" + "、".join(created_schedules))
    if schedule_action_replies:
        parts.extend(schedule_action_replies)
    if schedule_errors:
        parts.append("有计划操作未完成：" + "；".join(schedule_errors[:3]))
    return "\n\n".join(part for part in parts if part).strip()


def terminal_extension_command_for_log(directive: str) -> str:
    stripped = str(directive or "").strip()
    upper = stripped.upper()
    if upper.startswith("AGENT_WEB_RESEARCH:") or upper.startswith("AGENT_WEB_RESEARCH："):
        _, _, tail = stripped.partition(":" if ":" in stripped else "：")
        return "web research " + tail.strip()
    if upper.startswith("AGENT_WECHAT_CREATE_SCHEDULE:") or upper.startswith("AGENT_WECHAT_CREATE_SCHEDULE："):
        _, _, tail = stripped.partition(":" if ":" in stripped else "：")
        return "schedule create " + tail.strip()
    if upper.startswith("AGENT_WECHAT_SEND_FILE:") or upper.startswith("AGENT_WECHAT_SEND_FILE："):
        _, _, tail = stripped.partition(":" if ":" in stripped else "：")
        return "wx send_file " + tail.strip()
    if upper.startswith("AGENT_WECHAT_SCHEDULE_ACTION:") or upper.startswith("AGENT_WECHAT_SCHEDULE_ACTION："):
        _, _, tail = stripped.partition(":" if ":" in stripped else "：")
        try:
            payload = json.loads(terminal_extension_payload_text(tail))
        except Exception:
            return "schedule action " + tail.strip()
        if isinstance(payload, dict):
            action = str(payload.get("action") or "").strip().lower()
            if action == "list":
                return "schedule list"
            if action == "delete":
                target = str(payload.get("target") or payload.get("id") or payload.get("title") or "").strip()
                return "schedule delete " + target
            if action == "update":
                return "schedule update " + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        return "schedule action " + tail.strip()
    return stripped


def terminal_extension_execution_log(directives: List[str], output: str) -> str:
    commands = [terminal_extension_command_for_log(item) for item in directives if str(item or "").strip()]
    lines = ["Terminal extension execution:"]
    if commands:
        lines.append("Commands:")
        lines.extend(f"$ {cmd}" for cmd in commands)
    lines.append("")
    lines.append("Output:")
    lines.append(str(output or "").strip() or "扩展指令已处理，未返回额外输出。")
    return "\n".join(lines).strip()


def strip_wechat_send_file_markers(text: str) -> str:
    lines = []
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if is_terminal_extension_internal_directive_line(stripped):
            continue
        if normalize_terminal_extension_directive(stripped):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def echoed_wechat_trigger_lines(text: str) -> List[str]:
    """Recover legacy WeChat control lines that were incorrectly wrapped in a pure echo shell block."""
    blocks = scan_all_code_blocks(str(text or ""))
    command_text, _command_lang = command_block_from_blocks(blocks)
    if not command_text:
        return []
    triggers: List[str] = []
    saw_effective_line = False
    for raw_line in command_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        saw_effective_line = True
        match = re.match(r"^(?:echo|printf(?:\s+%s(?:\\n)?)?)\s+(.+?)\s*$", line)
        if not match:
            return []
        payload = match.group(1).strip()
        payload = payload.strip("\"'")
        if not is_terminal_extension_internal_directive_line(payload):
            return []
        triggers.append(payload)
    return triggers if saw_effective_line else []


def wechat_trigger_summary(trigger_lines: List[str]) -> str:
    file_targets = extract_wechat_send_file_targets("\n".join(trigger_lines))
    if file_targets:
        names = [os.path.basename(target) or target for target in file_targets]
        return "已准备发送文件：" + "、".join(names[:3])
    if any("AGENT_WECHAT_CREATE_SCHEDULE" in line.upper() for line in trigger_lines):
        return "已准备创建定时计划。"
    if any("AGENT_WECHAT_SCHEDULE_ACTION" in line.upper() for line in trigger_lines):
        return "已准备处理定时计划。"
    return "已处理微信请求。"


def wechat_history_reply(entries: List[Dict[str, object]], silent: bool = True) -> str:
    relevant = [entry for entry in entries if isinstance(entry, dict) and entry.get("type") in {"ai", "result", "terminal_result"}]
    if not relevant:
        return "已处理。"
    result_parts: List[str] = []
    conclusion_parts: List[str] = []
    for entry in relevant[-8:]:
        entry_type = str(entry.get("type") or "")
        content = str(entry.get("context_content") or entry.get("content") or "").strip()
        if not content:
            continue
        if entry_type in {"result", "terminal_result"}:
            sanitized = sanitize_wechat_visible_text(content, keep_code_summary=False, limit=2600)
            if sanitized and not is_low_value_wechat_result_summary(sanitized):
                result_parts.append(sanitized)
        elif entry_type == "ai":
            sanitized = sanitize_wechat_visible_text(content, keep_code_summary=not silent, limit=2200)
            if sanitized:
                conclusion_parts.append(sanitized)
    parts: List[str] = []
    if result_parts:
        parts.append("执行结果：\n" + result_parts[-1])
    if conclusion_parts:
        parts.append("结论：\n" + conclusion_parts[-1])
    return "\n\n".join(part for part in parts if part.strip()).strip() or "已处理。"


class WeChatBridge(QObject):
    request_signal = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.server: Optional[ThreadingHTTPServer] = None
        self.thread: Optional[threading.Thread] = None
        self.host = ""
        self.port = 0
        self.api_key = ""
        self.timeout_seconds = 900
        self.pending: Dict[str, Dict[str, object]] = {}
        self.lock = threading.Lock()

    def url(self) -> str:
        if not self.server:
            cfg = wechat_bridge_settings()
            return f"http://{cfg['host']}:{cfg['port']}"
        return f"http://{self.host}:{self.port}"

    def is_running(self) -> bool:
        return self.server is not None

    def start(self) -> str:
        self.stop()
        cfg = wechat_bridge_settings()
        self.host = str(cfg["host"])
        self.port = int(cfg["port"])
        self.api_key = str(cfg.get("api_key") or "")
        self.timeout_seconds = int(cfg.get("timeout_seconds") or 900)
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, _format, *args):
                return

            def _json_body(self) -> dict:
                length = int(self.headers.get("Content-Length", "0") or "0")
                if length <= 0:
                    return {}
                raw = self.rfile.read(length)
                try:
                    payload = json.loads(raw.decode("utf-8"))
                    return payload if isinstance(payload, dict) else {"text": str(payload)}
                except Exception:
                    return {"text": raw.decode("utf-8", errors="replace")}

            def _authorized(self) -> bool:
                if not bridge.api_key:
                    return True
                header = self.headers.get("Authorization", "")
                token = self.headers.get("X-Agent-Qt-Key", "")
                return header == f"Bearer {bridge.api_key}" or token == bridge.api_key

            def _send(self, status: int, payload: dict):
                raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _send_text(self, status: int, text: str):
                raw = str(text or "").encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                if not self._authorized():
                    self._send(401, {"ok": False, "error": "unauthorized"})
                    return
                parsed = urllib.parse.urlparse(self.path)
                path = parsed.path
                query = urllib.parse.parse_qs(parsed.query)
                if path in {"/", "/health"}:
                    self._send(200, {"ok": True, "status": "ok", "service": "agent-qt-wechat", "url": bridge.url()})
                    return
                if path in {"/terminals", "/api/terminals"}:
                    self._send(200, bridge.dispatch({
                        "action": "terminals",
                        "pid": (query.get("pid") or ["0"])[0],
                        "grep": (query.get("grep") or query.get("q") or [""])[0],
                    }, wait=True))
                    return
                if path in {"/terminallogs", "/terminals/text", "/api/terminals/text"}:
                    result = bridge.dispatch({
                        "action": "terminals_text",
                        "pid": (query.get("pid") or ["0"])[0],
                        "grep": (query.get("grep") or query.get("q") or [""])[0],
                    }, wait=True)
                    self._send_text(200 if result.get("ok") else 500, str(result.get("text") or result.get("error") or ""))
                    return
                if path in {"/threads", "/conversations"}:
                    self._send(200, bridge.dispatch({"action": "threads"}, wait=True))
                    return
                if path == "/state":
                    self._send(200, bridge.dispatch({"action": "state"}, wait=True))
                    return
                if path == "/provider":
                    self._send(200, bridge.dispatch({"action": "provider"}, wait=True))
                    return
                if path == "/v1/models":
                    self._send(200, {"object": "list", "data": [{"id": "agent-qt-wechat", "object": "model"}]})
                    return
                self._send(404, {"ok": False, "error": "not_found"})

            def do_POST(self):
                if not self._authorized():
                    self._send(401, {"ok": False, "error": "unauthorized"})
                    return
                path = urllib.parse.urlparse(self.path).path
                payload = self._json_body()
                if path == "/v1/chat/completions":
                    messages = payload.get("messages") if isinstance(payload, dict) else []
                    text = ""
                    if isinstance(messages, list):
                        for message in reversed(messages):
                            if isinstance(message, dict) and str(message.get("role") or "") == "user":
                                content = message.get("content")
                                text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
                                break
                    if not text:
                        text = str(payload.get("prompt") or payload.get("text") or "")
                    result = bridge.dispatch({
                        "action": "message",
                        "text": text,
                        "silent": payload.get("silent", True),
                        "thread_id": payload.get("user") or payload.get("thread_id") or "",
                        "to_user": payload.get("to_user") or payload.get("user") or payload.get("thread_id") or "",
                        "context_token": payload.get("context_token") or "",
                    }, wait=True)
                    reply = str(result.get("reply") or result.get("text") or result.get("error") or "")
                    if not reply and result.get("ok"):
                        if isinstance(result.get("threads"), list):
                            active = str(result.get("active_thread_id") or "")
                            items = []
                            for thread in result.get("threads") or []:
                                if not isinstance(thread, dict):
                                    continue
                                thread_id = str(thread.get("id") or "")
                                title = str(thread.get("title") or thread_id)
                                mark = "当前 " if thread_id == active else ""
                                items.append(f"{mark}{title} ({thread_id})")
                            reply = "会话列表：\n" + "\n".join(items) if items else "暂无会话。"
                        elif result.get("active_thread_id"):
                            reply = f"当前会话：{result.get('active_thread_id')}"
                        else:
                            reply = json.dumps(result, ensure_ascii=False)
                    finish_reason = "stop" if result.get("ok") else "error"
                    self._send(200, {
                        "id": "chatcmpl-" + uuid.uuid4().hex,
                        "object": "chat.completion",
                        "created": int(time.time()),
                        "model": str(payload.get("model") or "agent-qt-wechat"),
                        "choices": [{
                            "index": 0,
                            "message": {"role": "assistant", "content": reply},
                            "finish_reason": finish_reason,
                        }],
                        "ok": bool(result.get("ok")),
                        "error": "" if result.get("ok") else reply,
                    })
                    return
                if path in {"/message", "/wechat/message", "/api/message", "/send"}:
                    payload["action"] = "message"
                    self._send(200, bridge.dispatch(payload, wait=True))
                    return
                if path in {"/stop", "/wechat/stop", "/api/stop"}:
                    self._send(200, bridge.dispatch({"action": "stop"}, wait=True))
                    return
                if path in {"/threads/select", "/conversation/select"}:
                    payload["action"] = "select_thread"
                    self._send(200, bridge.dispatch(payload, wait=True))
                    return
                if path in {"/threads/new", "/conversation/new"}:
                    payload["action"] = "new_thread"
                    self._send(200, bridge.dispatch(payload, wait=True))
                    return
                self._send(404, {"ok": False, "error": "not_found"})

        try:
            self.server = ThreadingHTTPServer((self.host, self.port), Handler)
        except OSError as exc:
            self.server = None
            raise RuntimeError(f"微信 Bridge 启动失败：{exc}") from exc
        self.thread = threading.Thread(target=self.server.serve_forever, name="AgentQtWeChatBridge", daemon=True)
        self.thread.start()
        return self.url()

    def stop(self):
        server = self.server
        self.server = None
        if server is not None:
            try:
                server.shutdown()
                server.server_close()
            except Exception:
                pass
        with self.lock:
            pending = list(self.pending.values())
            self.pending.clear()
        for item in pending:
            item["response"] = {"ok": False, "error": "wechat_bridge_stopped"}
            event = item.get("event")
            if isinstance(event, threading.Event):
                event.set()

    def dispatch(self, payload: dict, wait: bool = True) -> dict:
        request_id = uuid.uuid4().hex
        event = threading.Event()
        payload = dict(payload or {})
        payload["request_id"] = request_id
        with self.lock:
            self.pending[request_id] = {"event": event, "response": None}
        self.request_signal.emit(payload)
        if not wait:
            return {"ok": True, "request_id": request_id}
        if not event.wait(self.timeout_seconds):
            with self.lock:
                self.pending.pop(request_id, None)
            return {"ok": False, "request_id": request_id, "error": "timeout"}
        with self.lock:
            item = self.pending.pop(request_id, None)
        response = (item or {}).get("response") if isinstance(item, dict) else None
        return response if isinstance(response, dict) else {"ok": False, "request_id": request_id, "error": "empty_response"}

    def finish_request(self, request_id: str, response: dict):
        if not request_id:
            return
        with self.lock:
            item = self.pending.get(request_id)
            if not item:
                return
            item["response"] = dict(response or {})
            event = item.get("event")
        if isinstance(event, threading.Event):
            event.set()


class WeChatConnector(QObject):
    status_signal = Signal(str)
    qr_signal = Signal(str)

    DEFAULT_BASE_URL = "https://ilinkai.weixin.qq.com"
    DEFAULT_CDN_BASE_URL = "https://novac2c.cdn.weixin.qq.com/c2c"
    BOT_TYPE = "3"
    CHANNEL_VERSION = "agent-qt"

    def __init__(self, chat_page):
        super().__init__(chat_page)
        self.chat_page = chat_page
        self.stop_event = threading.Event()
        self.monitor_thread: Optional[threading.Thread] = None
        self.login_thread: Optional[threading.Thread] = None
        self._running_lock = threading.Lock()
        self._login_state_lock = threading.Lock()
        self._active_qr_url = ""
        self._direct_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def is_running(self) -> bool:
        thread = self.monitor_thread
        return bool(thread and thread.is_alive())

    def account(self) -> Optional[Dict[str, object]]:
        path = wechat_connector_account_path()
        try:
            with open(path, "r", encoding="utf-8") as f:
                payload = json.load(f)
            return payload if isinstance(payload, dict) else None
        except Exception:
            return None

    def login_async(self):
        thread = self.login_thread
        if thread and thread.is_alive():
            current_qr = self.current_qr_url()
            if current_qr:
                self.qr_signal.emit(current_qr)
                self.status_signal.emit("微信扫码登录已在进行中，已重新显示当前二维码。")
            else:
                self.status_signal.emit("微信扫码登录已在进行中，请稍候。")
            return
        self.login_thread = threading.Thread(target=self._login_loop, name="AgentQtWeChatLogin", daemon=True)
        self.login_thread.start()

    def current_qr_url(self) -> str:
        with self._login_state_lock:
            return self._active_qr_url

    def start(self):
        with self._running_lock:
            if self.is_running():
                self.status_signal.emit("内置微信连接器已在运行。")
                return
            if not self.account():
                self.status_signal.emit("还没有微信登录信息，请先扫码登录。")
                return
            self.stop_event.clear()
            self.monitor_thread = threading.Thread(target=self._monitor_loop, name="AgentQtWeChatConnector", daemon=True)
            self.monitor_thread.start()
            self.status_signal.emit("内置微信连接器已启动，开始监听微信消息。")

    def stop(self, notify: bool = True):
        self.stop_event.set()
        if notify:
            self.status_signal.emit("内置微信连接器已停止。")

    def clear_login(self):
        self.stop(notify=False)
        try:
            os.remove(wechat_connector_account_path())
        except FileNotFoundError:
            pass
        except OSError as exc:
            self.status_signal.emit(f"清除微信登录信息失败：{exc}")
            return
        self.status_signal.emit("已清除微信登录信息。")

    def _open_url(self, req: urllib.request.Request, timeout: int):
        if use_system_proxy_setting():
            return urllib.request.urlopen(req, timeout=timeout)
        return self._direct_opener.open(req, timeout=timeout)

    def _api_get(self, base_url: str, endpoint: str, timeout: int = 15) -> dict:
        url = urllib.parse.urljoin(base_url.rstrip("/") + "/", endpoint)
        req = urllib.request.Request(url, method="GET")
        with self._open_url(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        payload = json.loads(raw or "{}")
        return payload if isinstance(payload, dict) else {}

    def _api_post(self, base_url: str, endpoint: str, body: dict, token: str = "", timeout: int = 35) -> dict:
        url = urllib.parse.urljoin(base_url.rstrip("/") + "/", endpoint)
        raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
        uin = base64.b64encode(str(int.from_bytes(os.urandom(4), "big")).encode("utf-8")).decode("ascii")
        headers = {
            "Content-Type": "application/json",
            "Content-Length": str(len(raw)),
            "AuthorizationType": "ilink_bot_token",
            "X-WECHAT-UIN": uin,
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(url, data=raw, headers=headers, method="POST")
        with self._open_url(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8", errors="replace")
        payload = json.loads(text or "{}")
        return payload if isinstance(payload, dict) else {}

    def _is_timeout_error(self, exc: Exception) -> bool:
        if isinstance(exc, TimeoutError):
            return True
        reason = getattr(exc, "reason", None)
        return isinstance(reason, TimeoutError) or "timed out" in str(exc).lower()

    def _login_loop(self):
        try:
            qr = self._api_get(
                self.DEFAULT_BASE_URL,
                f"ilink/bot/get_bot_qrcode?bot_type={urllib.parse.quote(self.BOT_TYPE)}",
                timeout=8,
            )
            qrcode = str(qr.get("qrcode") or "")
            qrcode_url = str(qr.get("qrcode_img_content") or "")
            if not qrcode or not qrcode_url:
                raise RuntimeError("微信服务器没有返回二维码。")
            with self._login_state_lock:
                self._active_qr_url = qrcode_url
            self.qr_signal.emit(qrcode_url)
            self.status_signal.emit("二维码已生成，请用微信扫码并在手机上确认。")
            poll_base = self.DEFAULT_BASE_URL
            deadline = time.time() + 480
            refresh_count = 0
            while time.time() < deadline and not self.stop_event.is_set():
                try:
                    status = self._api_get(
                        poll_base,
                        f"ilink/bot/get_qrcode_status?qrcode={urllib.parse.quote(qrcode)}",
                        timeout=38,
                    )
                except Exception as exc:
                    if not self._is_timeout_error(exc):
                        self.status_signal.emit(f"二维码状态查询暂时失败：{exc}")
                    time.sleep(1)
                    continue
                state = str(status.get("status") or "")
                if state == "scaned":
                    self.status_signal.emit("已扫码，请在微信里确认登录。")
                elif state == "scaned_but_redirect":
                    redirect_host = str(status.get("redirect_host") or "").strip()
                    if redirect_host:
                        poll_base = f"https://{redirect_host}"
                elif state == "expired":
                    refresh_count += 1
                    if refresh_count > 3:
                        raise RuntimeError("二维码已过期，请重新扫码登录。")
                    qr = self._api_get(
                        self.DEFAULT_BASE_URL,
                        f"ilink/bot/get_bot_qrcode?bot_type={urllib.parse.quote(self.BOT_TYPE)}",
                        timeout=8,
                    )
                    qrcode = str(qr.get("qrcode") or "")
                    qrcode_url = str(qr.get("qrcode_img_content") or "")
                    with self._login_state_lock:
                        self._active_qr_url = qrcode_url
                    self.qr_signal.emit(qrcode_url)
                    self.status_signal.emit("二维码已刷新，请重新扫码。")
                elif state == "confirmed":
                    account_id = str(status.get("ilink_bot_id") or "").strip()
                    if not account_id:
                        raise RuntimeError("登录成功但服务器没有返回 bot id。")
                    payload = {
                        "account_id": account_id,
                        "token": str(status.get("bot_token") or ""),
                        "base_url": str(status.get("baseurl") or self.DEFAULT_BASE_URL),
                        "cdn_base_url": self.DEFAULT_CDN_BASE_URL,
                        "user_id": str(status.get("ilink_user_id") or ""),
                        "saved_at": datetime.now().isoformat(timespec="seconds"),
                    }
                    os.makedirs(wechat_connector_root(), exist_ok=True)
                    with open(wechat_connector_account_path(), "w", encoding="utf-8") as f:
                        json.dump(payload, f, ensure_ascii=False, indent=2)
                    self.status_signal.emit("微信扫码登录完成，可以启动内置连接器。")
                    return
                time.sleep(1)
            raise RuntimeError("微信扫码登录超时。")
        except Exception as exc:
            self.status_signal.emit(f"微信扫码登录失败：{exc}")
        finally:
            with self._login_state_lock:
                self._active_qr_url = ""

    def _monitor_loop(self):
        account = self.account()
        if not account:
            self.status_signal.emit("没有微信登录信息，请先扫码登录。")
            return
        try:
            self.chat_page.start_wechat_bridge_quietly()
        except Exception:
            pass
        account_id = str(account.get("account_id") or "default")
        base_url = str(account.get("base_url") or self.DEFAULT_BASE_URL)
        token = str(account.get("token") or "")
        sync_path = wechat_connector_sync_path(account_id)
        get_updates_buf = ""
        try:
            with open(sync_path, "r", encoding="utf-8") as f:
                get_updates_buf = f.read()
        except Exception:
            get_updates_buf = ""
        failures = 0
        while not self.stop_event.is_set():
            try:
                resp = self._api_post(
                    base_url,
                    "ilink/bot/getupdates",
                    {
                        "get_updates_buf": get_updates_buf,
                        "base_info": {"channel_version": self.CHANNEL_VERSION},
                    },
                    token=token,
                    timeout=38,
                )
                if resp.get("ret") not in (None, 0) or resp.get("errcode") not in (None, 0):
                    failures += 1
                    self.status_signal.emit(f"微信轮询失败：{resp.get('errmsg') or resp.get('errcode') or resp.get('ret')}")
                    time.sleep(30 if failures >= 3 else 2)
                    if failures >= 3:
                        failures = 0
                    continue
                failures = 0
                if resp.get("get_updates_buf"):
                    get_updates_buf = str(resp.get("get_updates_buf") or "")
                    os.makedirs(os.path.dirname(sync_path), exist_ok=True)
                    with open(sync_path, "w", encoding="utf-8") as f:
                        f.write(get_updates_buf)
                for message in resp.get("msgs") or []:
                    if self.stop_event.is_set() or not isinstance(message, dict):
                        break
                    if int(message.get("message_type") or 0) != 1:
                        continue
                    self._handle_inbound_message(message)
            except Exception as exc:
                if self.stop_event.is_set():
                    break
                if self._is_timeout_error(exc):
                    continue
                failures += 1
                self.status_signal.emit(f"微信连接器异常：{exc}")
                time.sleep(30 if failures >= 3 else 2)
                if failures >= 3:
                    failures = 0

    def _extract_message_text(self, message: dict) -> str:
        parts: List[str] = []
        for item in message.get("item_list") or []:
            if not isinstance(item, dict):
                continue
            text_item = item.get("text_item") if isinstance(item.get("text_item"), dict) else {}
            if text_item.get("text"):
                parts.append(str(text_item.get("text")))
                continue
            voice_item = item.get("voice_item") if isinstance(item.get("voice_item"), dict) else {}
            if voice_item.get("text"):
                parts.append(str(voice_item.get("text")))
        return "\n".join(part for part in parts if part.strip()).strip()

    def _parse_aes_key(self, value: str) -> bytes:
        text = str(value or "").strip()
        if not text:
            return b""
        if re.fullmatch(r"[0-9a-fA-F]{32}", text):
            return bytes.fromhex(text)
        decoded = base64.b64decode(text)
        if len(decoded) == 16:
            return decoded
        if len(decoded) == 32 and re.fullmatch(rb"[0-9a-fA-F]{32}", decoded):
            return bytes.fromhex(decoded.decode("ascii"))
        raise RuntimeError(f"aes_key 长度不支持：{len(decoded)}")

    def _decrypt_aes_ecb(self, data: bytes, key: bytes) -> bytes:
        try:
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
            from cryptography.hazmat.primitives import padding
        except Exception as exc:
            raise RuntimeError("当前 Python 环境缺少 cryptography，无法解密微信附件。") from exc
        decryptor = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
        padded = decryptor.update(data) + decryptor.finalize()
        unpadder = padding.PKCS7(128).unpadder()
        return unpadder.update(padded) + unpadder.finalize()

    def _encrypt_aes_ecb(self, data: bytes, key: bytes) -> bytes:
        try:
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
            from cryptography.hazmat.primitives import padding
        except Exception as exc:
            raise RuntimeError("当前 Python 环境缺少 cryptography，无法上传微信附件。") from exc
        padder = padding.PKCS7(128).padder()
        padded = padder.update(data) + padder.finalize()
        encryptor = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
        return encryptor.update(padded) + encryptor.finalize()

    def _aes_ecb_padded_size(self, size: int) -> int:
        return ((int(size) + 16) // 16) * 16

    def _download_cdn_bytes(self, media: dict, aes_key: str = "") -> bytes:
        full_url = str(media.get("full_url") or "").strip()
        query = str(media.get("encrypt_query_param") or "").strip()
        account = self.account() or {}
        cdn_base = str(account.get("cdn_base_url") or self.DEFAULT_CDN_BASE_URL).rstrip("/")
        if full_url:
            url = full_url
        elif query:
            url = f"{cdn_base}/download?encrypted_query_param={urllib.parse.quote(query)}"
        else:
            raise RuntimeError("缺少微信 CDN 下载参数。")
        req = urllib.request.Request(url, headers={"User-Agent": "AgentQt/5.1"})
        with self._open_url(req, timeout=60) as resp:
            data = resp.read()
        if aes_key:
            return self._decrypt_aes_ecb(data, self._parse_aes_key(aes_key))
        return data

    def _upload_wechat_media(self, path: str, to_user: str, media_type: int) -> Dict[str, object]:
        account = self.account() or {}
        base_url = str(account.get("base_url") or self.DEFAULT_BASE_URL)
        cdn_base = str(account.get("cdn_base_url") or self.DEFAULT_CDN_BASE_URL).rstrip("/")
        token = str(account.get("token") or "")
        with open(path, "rb") as f:
            data = f.read()
        aes_key = os.urandom(16)
        filekey = uuid.uuid4().hex
        ciphertext = self._encrypt_aes_ecb(data, aes_key)
        upload_info = self._api_post(
            base_url,
            "ilink/bot/getuploadurl",
            {
                "filekey": filekey,
                "media_type": int(media_type),
                "to_user_id": to_user,
                "rawsize": len(data),
                "rawfilemd5": hashlib.md5(data).hexdigest(),
                "filesize": len(ciphertext),
                "no_need_thumb": True,
                "aeskey": aes_key.hex(),
                "base_info": {"channel_version": self.CHANNEL_VERSION},
            },
            token=token,
            timeout=20,
        )
        upload_full_url = str(upload_info.get("upload_full_url") or upload_info.get("full_url") or "").strip()
        upload_param = str(upload_info.get("upload_param") or upload_info.get("encrypted_query_param") or "").strip()
        if upload_full_url:
            upload_url = upload_full_url
        elif upload_param:
            upload_url = (
                f"{cdn_base}/upload?"
                f"encrypted_query_param={urllib.parse.quote(upload_param)}&"
                f"filekey={urllib.parse.quote(filekey)}"
            )
        else:
            raise RuntimeError(f"微信没有返回上传地址：{upload_info}")
        req = urllib.request.Request(
            upload_url,
            data=ciphertext,
            headers={"Content-Type": "application/octet-stream", "Content-Length": str(len(ciphertext))},
            method="POST",
        )
        with self._open_url(req, timeout=60) as resp:
            if resp.status != 200:
                raise RuntimeError(f"微信 CDN 上传失败：HTTP {resp.status}")
            response_body = resp.read()
            download_param = (
                resp.headers.get("x-encrypted-param", "")
                or resp.headers.get("X-Encrypted-Param", "")
                or resp.headers.get("x-encrypt-param", "")
            )
        if not download_param and response_body:
            body_text = response_body.decode("utf-8", errors="replace").strip()
            try:
                body_json = json.loads(body_text)
                if isinstance(body_json, dict):
                    download_param = str(
                        body_json.get("encrypt_query_param")
                        or body_json.get("encrypted_query_param")
                        or body_json.get("download_param")
                        or body_json.get("download_url")
                        or ""
                    ).strip()
            except Exception:
                download_param = body_text
        if not download_param:
            raise RuntimeError("微信 CDN 上传响应缺少 x-encrypted-param。")
        return {
            "download_param": download_param,
            "aes_key": aes_key.hex(),
            "aes_key_b64": base64.b64encode(aes_key.hex().encode("ascii")).decode("ascii"),
            "plain_size": len(data),
            "cipher_size": len(ciphertext),
        }

    def _save_wechat_file(self, data: bytes, filename: str) -> str:
        root = str(getattr(self.chat_page, "project_root", "") or "")
        inbox = wechat_inbox_dir(root)
        os.makedirs(inbox, exist_ok=True)
        safe_name = re.sub(r"[\\/:*?\"<>|]+", "_", filename or "wechat-file.bin").strip(" .") or "wechat-file.bin"
        stem, ext = os.path.splitext(safe_name)
        path = os.path.join(inbox, safe_name)
        suffix = 2
        while os.path.exists(path):
            path = os.path.join(inbox, f"{stem}-{suffix}{ext}")
            suffix += 1
        with open(path, "wb") as f:
            f.write(data)
        return path

    def _download_message_files(self, message: dict) -> List[Dict[str, str]]:
        files: List[Dict[str, str]] = []
        for index, item in enumerate(message.get("item_list") or [], start=1):
            if not isinstance(item, dict):
                continue
            try:
                item_type = int(item.get("type") or 0)
                if item_type == 2:
                    image = item.get("image_item") if isinstance(item.get("image_item"), dict) else {}
                    media = image.get("media") if isinstance(image.get("media"), dict) else {}
                    aes_key = str(image.get("aeskey") or media.get("aes_key") or "")
                    data = self._download_cdn_bytes(media, aes_key)
                    path = self._save_wechat_file(data, f"wechat-image-{index}.jpg")
                    files.append({"type": "image", "path": path, "mime": "image/*"})
                elif item_type == 4:
                    file_item = item.get("file_item") if isinstance(item.get("file_item"), dict) else {}
                    media = file_item.get("media") if isinstance(file_item.get("media"), dict) else {}
                    filename = str(file_item.get("file_name") or f"wechat-file-{index}.bin")
                    data = self._download_cdn_bytes(media, str(media.get("aes_key") or ""))
                    path = self._save_wechat_file(data, filename)
                    files.append({"type": "file", "path": path, "mime": wechat_mime_from_filename(filename)})
                elif item_type == 5:
                    video = item.get("video_item") if isinstance(item.get("video_item"), dict) else {}
                    media = video.get("media") if isinstance(video.get("media"), dict) else {}
                    data = self._download_cdn_bytes(media, str(media.get("aes_key") or ""))
                    path = self._save_wechat_file(data, f"wechat-video-{index}.mp4")
                    files.append({"type": "video", "path": path, "mime": "video/mp4"})
                elif item_type == 3:
                    voice = item.get("voice_item") if isinstance(item.get("voice_item"), dict) else {}
                    media = voice.get("media") if isinstance(voice.get("media"), dict) else {}
                    data = self._download_cdn_bytes(media, str(media.get("aes_key") or ""))
                    path = self._save_wechat_file(data, f"wechat-voice-{index}.silk")
                    files.append({"type": "audio", "path": path, "mime": "audio/silk"})
            except Exception as exc:
                files.append({"type": "error", "path": "", "mime": "", "error": str(exc)})
        return files

    def _handle_inbound_message(self, message: dict):
        to_user = str(message.get("from_user_id") or "").strip()
        context_token = str(message.get("context_token") or "")
        text = self._extract_message_text(message)
        files = self._download_message_files(message)
        file_lines = []
        for item in files:
            if item.get("error"):
                file_lines.append(f"- 附件下载失败：{item.get('error')}")
            elif item.get("path"):
                file_lines.append(f"- {item.get('type')} {item.get('mime')}: {item.get('path')}")
        if file_lines:
            attachment_text = "微信附件已保存到本地，后续需要查看内容时请直接读取这些文件：\n" + "\n".join(file_lines)
            text = f"{text}\n\n{attachment_text}".strip()
        if not text:
            text = "[微信消息] 收到空文本消息。"
        if self._should_send_immediate_ack(text, to_user, context_token):
            try:
                self._send_text(to_user, "收到，正在执行，请稍后。", context_token)
            except Exception as exc:
                self.status_signal.emit(f"微信回执发送失败：{exc}")

        def run_agent_call(pending_text: str, pending_user: str, pending_context_token: str):
            try:
                reply = self._call_agent_qt(pending_text, pending_user, pending_context_token)
            except Exception as exc:
                reply = f"Agent Qt 调用失败：{exc}"
            if reply and pending_user:
                try:
                    self._send_text(pending_user, reply, pending_context_token)
                except Exception as exc:
                    self.status_signal.emit(f"微信回复发送失败：{exc}")

        threading.Thread(
            target=run_agent_call,
            args=(text, to_user, context_token),
            name="AgentQtWeChatMessageWorker",
            daemon=True,
        ).start()

    def _bridge_state(self) -> Dict[str, object]:
        cfg = wechat_bridge_settings()
        bridge_url = self.chat_page.wechat_bridge.url().rstrip("/")
        headers = {}
        api_key = str(cfg.get("api_key") or "")
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        req = urllib.request.Request(f"{bridge_url}/state", headers=headers, method="GET")
        with self._open_url(req, timeout=8) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace") or "{}")

    def _should_send_immediate_ack(self, text: str, to_user: str, context_token: str) -> bool:
        command = str(text or "").strip()
        if not command or not to_user:
            return False
        if is_wechat_menu_command(command) or parse_wechat_builtin_command(command):
            return False
        try:
            state = self._bridge_state()
        except Exception:
            return True
        if not bool(state.get("busy")):
            return True
        same_target_busy = bool(
            state.get("wechat_active_request_id")
            and str(state.get("wechat_active_to_user") or "") == str(to_user or "")
        )
        return False

    def _call_agent_qt(self, text: str, conversation_id: str, context_token: str = "") -> str:
        cfg = wechat_bridge_settings()
        bridge_url = self.chat_page.wechat_bridge.url().rstrip("/")
        payload = {
            "model": "agent-qt-wechat",
            "user": conversation_id or "wechat",
            "thread_id": conversation_id or "",
            "to_user": conversation_id or "",
            "context_token": context_token,
            "silent": bool(cfg.get("silent", True)),
            "messages": [{"role": "user", "content": text}],
        }
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json", "Content-Length": str(len(raw))}
        api_key = str(cfg.get("api_key") or "")
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        req = urllib.request.Request(f"{bridge_url}/v1/chat/completions", data=raw, headers=headers, method="POST")
        try:
            with self._open_url(req, timeout=int(cfg.get("timeout_seconds") or 900) + 30) as resp:
                result = json.loads(resp.read().decode("utf-8", errors="replace") or "{}")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace").strip()
            try:
                payload = json.loads(detail)
                message = payload.get("error") or payload.get("detail") or detail
            except Exception:
                message = detail or f"HTTP {exc.code}: {exc.reason}"
            raise RuntimeError(str(message)) from exc
        choices = result.get("choices") if isinstance(result, dict) else None
        if isinstance(choices, list) and choices:
            message = choices[0].get("message") if isinstance(choices[0], dict) else {}
            if isinstance(message, dict) and message.get("content"):
                return str(message.get("content"))
        return str(result.get("reply") or result.get("text") or "已处理。")

    def _send_text(self, to_user: str, text: str, context_token: str):
        account = self.account() or {}
        base_url = str(account.get("base_url") or self.DEFAULT_BASE_URL)
        token = str(account.get("token") or "")
        if not context_token:
            raise RuntimeError("缺少 context_token，无法确定微信回复上下文。")
        client_id = "agent-qt-" + uuid.uuid4().hex
        body = {
            "msg": {
                "from_user_id": "",
                "to_user_id": to_user,
                "client_id": client_id,
                "message_type": 2,
                "message_state": 2,
                "item_list": [{"type": 1, "text_item": {"text": text}}],
                "context_token": context_token,
            },
            "base_info": {"channel_version": self.CHANNEL_VERSION},
        }
        self._api_post(base_url, "ilink/bot/sendmessage", body, token=token, timeout=20)

    def _send_file(self, to_user: str, path: str, context_token: str, caption: str = ""):
        account = self.account() or {}
        base_url = str(account.get("base_url") or self.DEFAULT_BASE_URL)
        token = str(account.get("token") or "")
        if not context_token:
            raise RuntimeError("缺少 context_token，无法确定微信回复上下文。")
        if not os.path.isfile(path):
            raise RuntimeError(f"文件不存在：{path}")
        mime = wechat_mime_from_filename(path)
        if mime.startswith("image/"):
            media_type = 1
            item_type = 2
        elif mime.startswith("video/"):
            media_type = 2
            item_type = 5
        else:
            media_type = 3
            item_type = 4
        uploaded = self._upload_wechat_media(path, to_user, media_type)
        media = {
            "encrypt_query_param": uploaded["download_param"],
            "aes_key": uploaded["aes_key_b64"],
            "encrypt_type": 1,
        }
        if item_type == 2:
            item = {"type": item_type, "image_item": {"media": media, "aeskey": uploaded["aes_key_b64"], "mid_size": int(uploaded["cipher_size"])}}
        elif item_type == 5:
            item = {"type": item_type, "video_item": {"media": media, "video_size": int(uploaded["cipher_size"])}}
        else:
            item = {"type": item_type, "file_item": {"media": media, "file_name": os.path.basename(path), "len": str(uploaded["plain_size"])}}
        items = []
        if caption:
            items.append({"type": 1, "text_item": {"text": caption}})
        items.append(item)
        for out_item in items:
            body = {
                "msg": {
                    "from_user_id": "",
                    "to_user_id": to_user,
                    "client_id": "agent-qt-" + uuid.uuid4().hex,
                    "message_type": 2,
                    "message_state": 2,
                    "item_list": [out_item],
                    "context_token": context_token,
                },
                "base_info": {"channel_version": self.CHANNEL_VERSION},
            }
            self._api_post(base_url, "ilink/bot/sendmessage", body, token=token, timeout=25)


class WeChatQrImageWorker(QThread):
    loaded = Signal(str, bytes, str)

    def __init__(self, qr_url: str):
        super().__init__()
        self.qr_url = qr_url

    def _generate_qr_png_with_coreimage(self, text: str) -> bytes:
        if platform.system() != "Darwin":
            return b""
        swift_bin = shutil.which("swift") or "/usr/bin/swift"
        if not os.path.exists(swift_bin):
            return b""
        fd, output_path = tempfile.mkstemp(prefix="agent_qt_wechat_qr_", suffix=".png")
        os.close(fd)
        script_fd, script_path = tempfile.mkstemp(prefix="agent_qt_wechat_qr_", suffix=".swift")
        os.close(script_fd)
        script = r'''
import AppKit
import CoreImage
import Foundation

let args = CommandLine.arguments
guard args.count >= 3 else { exit(2) }
let text = args[1]
let output = args[2]
guard let data = text.data(using: .utf8) else { exit(3) }
let filter = CIFilter(name: "CIQRCodeGenerator")!
filter.setValue(data, forKey: "inputMessage")
filter.setValue("M", forKey: "inputCorrectionLevel")
guard let image = filter.outputImage else { exit(4) }
let scaled = image.transformed(by: CGAffineTransform(scaleX: 12, y: 12))
let rep = NSCIImageRep(ciImage: scaled)
let bitmap = NSBitmapImageRep(
    bitmapDataPlanes: nil,
    pixelsWide: rep.pixelsWide,
    pixelsHigh: rep.pixelsHigh,
    bitsPerSample: 8,
    samplesPerPixel: 4,
    hasAlpha: true,
    isPlanar: false,
    colorSpaceName: .deviceRGB,
    bytesPerRow: 0,
    bitsPerPixel: 0
)!
NSGraphicsContext.saveGraphicsState()
NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: bitmap)
rep.draw(in: NSRect(x: 0, y: 0, width: rep.pixelsWide, height: rep.pixelsHigh))
NSGraphicsContext.restoreGraphicsState()
guard let png = bitmap.representation(using: .png, properties: [:]) else { exit(5) }
try png.write(to: URL(fileURLWithPath: output))
'''
        try:
            with open(script_path, "w", encoding="utf-8") as f:
                f.write(script)
            result = subprocess.run(
                [swift_bin, script_path, text, output_path],
                capture_output=True,
                text=True,
                timeout=20,
                **subprocess_no_window_kwargs(),
            )
            if result.returncode != 0:
                return b""
            with open(output_path, "rb") as f:
                return f.read()
        except Exception:
            return b""
        finally:
            try:
                os.remove(output_path)
            except OSError:
                pass
            try:
                os.remove(script_path)
            except OSError:
                pass

    def run(self):
        try:
            url = str(self.qr_url or "")
            if url.startswith("data:image/"):
                _, payload = url.split(",", 1)
                self.loaded.emit(url, base64.b64decode(payload), "")
                return
            generated = self._generate_qr_png_with_coreimage(url)
            if generated:
                self.loaded.emit(url, generated, "")
                return
            if url.startswith("http://") or url.startswith("https://"):
                req = urllib.request.Request(url, headers={"User-Agent": "AgentQt/5.1"})
                with urllib.request.urlopen(req, timeout=10) as resp:
                    self.loaded.emit(url, resp.read(), "")
                return
            self.loaded.emit(url, b"", "二维码不是可直接加载的图片链接。")
        except Exception as exc:
            self.loaded.emit(str(self.qr_url or ""), b"", str(exc))


class MarkdownRenderWorker(QThread):
    rendered = Signal(int, str, list, list, dict)

    def __init__(self, request_id: int, text: str):
        super().__init__()
        self.request_id = request_id
        self.text = text

    def run(self):
        total_started = time.perf_counter()
        split_started = time.perf_counter()
        parts = split_markdown_fenced_blocks(self.text)
        split_ms = int((time.perf_counter() - split_started) * 1000)
        html_ms = 0
        rendered_parts: List[Dict[str, str]] = []
        for part in parts:
            if part["type"] == "code":
                rendered_parts.append(part)
                continue
            html_started = time.perf_counter()
            markdown_text = part.get("text", "")
            rendered_parts.append({
                "type": "markdown",
                "text": markdown_text,
                "html": markdown_with_pipe_tables_to_html(markdown_text),
            })
            html_ms += int((time.perf_counter() - html_started) * 1000)
        signatures = [
            (part["type"], (part.get("lang", "") if part["type"] == "code" else ""))
            for part in rendered_parts
        ]
        stats = {
            "chars": len(self.text or ""),
            "parts": len(rendered_parts),
            "split_ms": split_ms,
            "html_ms": html_ms,
            "total_ms": int((time.perf_counter() - total_started) * 1000),
        }
        self.rendered.emit(self.request_id, self.text, rendered_parts, signatures, stats)

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
        markdown: bool = False,
        expand_to_content: bool = False,
        flat: bool = False,
        show_prompt_input: bool = True,
        compact_user: bool = False,
    ):
        super().__init__(parent)
        self.role = role
        self.content = content
        self.display_content = content
        self.show_copy = show_copy
        self.copy_text = copy_text
        self.show_paste_ai = show_paste_ai
        self.prompt_input_text = prompt_input_text
        self.scrollable = scrollable
        self.max_content_height = max_content_height
        self.markdown = markdown
        self.expand_to_content = expand_to_content
        self.flat = flat
        self.plain_system_log = flat and role == "system"
        self.show_prompt_input = show_prompt_input
        self.compact_user = compact_user
        self.code_max_height = 260
        self.streaming_code_fixed_height_threshold = 7
        self.markdown_widgets: List[QWidget] = []
        self.markdown_code_widgets: List[QPlainTextEdit] = []
        self.markdown_part_signatures: List[tuple] = []
        self.stabilize_markdown_height = False
        self.async_markdown_render = False
        self._markdown_render_seq = 0
        self._markdown_render_worker: Optional[MarkdownRenderWorker] = None
        self._markdown_render_pending = False
        self._streaming_height_adjust_min_interval_ms = 240
        self._last_streaming_height_adjust_at = 0.0
        self._stable_markdown_heights: Dict[int, int] = {}
        self.min_content_height = 34 if compact_user else 58
        self._height_adjust_scheduled = False
        self._last_content_width = 0
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setup_ui()
    
    def setup_ui(self):
        self.setObjectName("chatBubblePlainSystemLog" if self.plain_system_log else "chatBubble")
        colors = {
            "user": (COLORS["card_user"], COLORS["border"], "你"),
            "ai": (COLORS["card_ai"], ai_border_color(), "AI 输出"),
            "system": (COLORS["card_system"], COLORS["border"], "执行结果"),
        }
        bg, border, label_text = colors.get(getattr(self, 'role', 'system'), colors["system"])
        setattr(self, '_bg', bg)
        setattr(self, '_border', border)
        
        if self.plain_system_log:
            self.setStyleSheet("QFrame#chatBubblePlainSystemLog { background: transparent; border: none; margin: 0; }")
        elif self.flat:
            self.setStyleSheet("QFrame#chatBubble { background: transparent; border: none; margin: 2px 0; }")
        elif self.compact_user:
            self.setStyleSheet(f"""
                QFrame#chatBubble {{
                    background: {COLORS['card_user']};
                    border: 1px solid {COLORS['border']};
                    border-radius: 18px;
                    margin: 4px 0;
                }}
            """)
        else:
            self.setStyleSheet(f"QFrame#chatBubble {{ background: {bg}; border: 1px solid {border}; border-radius: 18px; margin: 4px 0; }}")
        
        layout = QVBoxLayout(self)
        if self.plain_system_log:
            layout.setContentsMargins(0, 4, 0, 4)
        elif self.flat:
            layout.setContentsMargins(2, 10, 2, 10)
        elif self.compact_user:
            layout.setContentsMargins(16, 12, 16, 12)
        else:
            layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(4 if self.plain_system_log else (6 if self.compact_user else (8 if self.flat else 10)))
        
        if self.compact_user:
            self.content_label = QTextBrowser()
            self.content_label.setOpenExternalLinks(False)
            self.content_label.setReadOnly(True)
            self.content_label.setPlainText(self.content)
            self.content_label.document().setDocumentMargin(0)
            self.content_label.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard
            )
            self.content_label.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.content_label.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            self.content_label.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            self.content_label.customContextMenuRequested.connect(
                lambda pos, editor=self.content_label: show_chinese_edit_menu(editor, editor.mapToGlobal(pos))
            )
            self.content_label.setStyleSheet(f"""
                QTextBrowser {{
                    background: transparent;
                    color: {COLORS['text']};
                    border: none;
                    padding: 0;
                    font-size: {scaled_font_px(14)}px;
                    line-height: 1.35;
                }}
                QScrollBar:vertical {{
                    background: transparent;
                    width: 8px;
                    margin: 2px 0 2px 2px;
                }}
                QScrollBar::handle:vertical {{
                    background: {COLORS['border_strong']};
                    border-radius: 4px;
                    min-height: 22px;
                }}
                QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                    height: 0;
                }}
            """)
            self.content_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            layout.addWidget(self.content_label)
            self.adjust_content_height()
            self.schedule_content_height_adjustment()
            return
        
        if not self.plain_system_log:
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
                        border: 1px solid {soft_accent_border_color()};
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
                        border: 1px solid {soft_accent_border_color()};
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

        if self.role == "user" and self.show_prompt_input:
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
        
        if self.markdown and self.expand_to_content:
            self.content_label = None
            self.render_markdown_parts(layout)
            return

        self.content_label = QTextBrowser() if self.markdown else QPlainTextEdit()
        self.content_label.setReadOnly(True)
        if self.markdown:
            self.content_label.setOpenExternalLinks(False)
            self.content_label.setMarkdown(self.content)
        else:
            self.content_label.setPlainText(self.content)
            self.content_label.setMaximumBlockCount(20000)
        self.content_label.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.content_label.customContextMenuRequested.connect(
            lambda pos, editor=self.content_label: show_chinese_edit_menu(editor, editor.mapToGlobal(pos))
        )
        editor_type = "QTextBrowser" if self.markdown else "QPlainTextEdit"
        font_family = "" if self.markdown or (self.flat and self.role == "ai") else "font-family: 'SF Mono', 'Menlo', monospace;"
        flat_system_log = self.flat and self.role == "system"
        content_bg = COLORS["code_bg"] if flat_system_log else ("transparent" if self.flat else COLORS["code_bg"])
        content_border = COLORS["border"] if flat_system_log else ("transparent" if self.flat else COLORS["border"])
        content_radius = 12 if flat_system_log else (0 if self.flat else 12)
        content_padding = "10px 12px" if flat_system_log else ("2px 0" if self.flat else "10px 12px")
        self.content_label.setStyleSheet(f"""
            {editor_type} {{
                background: {content_bg};
                color: {COLORS['text']};
                border: 1px solid {content_border};
                border-radius: {content_radius}px;
                padding: {content_padding};
                {font_family}
                font-size: {scaled_font_px(12)}px;
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
        if not self.markdown:
            self.content_label.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.content_label.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.content_label.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff if self.expand_to_content else Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.content_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.adjust_content_height()
        if not (self.markdown and self.expand_to_content and self.async_markdown_render):
            self.schedule_content_height_adjustment()
        layout.addWidget(self.content_label)

    def clear_markdown_widgets(self):
        layout = self.layout()
        for widget in self.markdown_widgets:
            if layout is not None:
                layout.removeWidget(widget)
            widget.setParent(None)
            widget.deleteLater()
        self.markdown_widgets = []
        self.markdown_code_widgets = []
        self.markdown_part_signatures = []
        self._stable_markdown_heights = {}

    def capture_markdown_code_scroll_state(self) -> List[Dict[str, object]]:
        states: List[Dict[str, object]] = []
        for widget in self.markdown_code_widgets:
            vertical = widget.verticalScrollBar()
            horizontal = widget.horizontalScrollBar()
            states.append({
                "value": vertical.value(),
                "maximum": vertical.maximum(),
                "at_bottom": vertical.value() >= vertical.maximum() - 4,
                "h_value": horizontal.value(),
            })
        return states

    def restore_markdown_code_scroll_state(self, states: List[Dict[str, object]]):
        if not states:
            return
        code_index = 0
        for widget in self.markdown_code_widgets:
            if code_index >= len(states):
                break
            state = states[code_index]
            vertical = widget.verticalScrollBar()
            horizontal = widget.horizontalScrollBar()
            if bool(state.get("at_bottom")):
                vertical.setValue(vertical.maximum())
            else:
                vertical.setValue(min(int(state.get("value") or 0), vertical.maximum()))
            horizontal.setValue(min(int(state.get("h_value") or 0), horizontal.maximum()))
            code_index += 1

    def markdown_text_style(self) -> str:
        return f"""
            QTextBrowser, QLabel {{
                background: transparent;
                color: {COLORS['text']};
                border: none;
                border-radius: 0;
                padding: 0;
                font-size: {scaled_font_px(12)}px;
            }}
        """

    def markdown_code_style(self) -> str:
        return f"""
            QPlainTextEdit {{
                background: {COLORS['code_bg']};
                color: {COLORS['text']};
                border: none;
                border-radius: 0;
                padding: 10px 12px;
                font-family: 'SF Mono', 'Menlo', monospace;
                font-size: {scaled_font_px(12)}px;
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
            QScrollBar:horizontal {{
                background: transparent;
                height: 8px;
                margin: 0 8px 2px 8px;
            }}
            QScrollBar::handle:horizontal {{
                background: {COLORS['border_strong']};
                border-radius: 4px;
                min-width: 28px;
            }}
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
                width: 0;
            }}
        """

    def markdown_code_frame_style(self) -> str:
        return f"""
            QFrame {{
                background: {COLORS['code_bg']};
                border: 1px solid {COLORS['border']};
                border-radius: 10px;
            }}
        """

    def markdown_code_header_style(self) -> str:
        return f"""
            QFrame#markdownCodeHeader {{
                background: {COLORS['surface_alt']};
                border: none;
                border-bottom: 1px solid {COLORS['border']};
                border-top-left-radius: 10px;
                border-top-right-radius: 10px;
            }}
            QLabel {{
                background: transparent;
                color: {COLORS['text_secondary']};
                border: none;
                padding: 0;
                font-size: 11px;
                font-weight: 800;
                letter-spacing: 0;
            }}
            QToolButton {{
                background: transparent;
                color: {COLORS['text_secondary']};
                border: none;
                font-size: 13px;
                font-weight: 900;
            }}
            QToolButton:hover {{
                color: {COLORS['accent_dark']};
            }}
        """

    def add_markdown_text_widget(self, text: str, layout: QVBoxLayout, html_text: Optional[str] = None):
        viewer = QLabel()
        viewer.setTextFormat(Qt.TextFormat.RichText)
        viewer.setWordWrap(True)
        viewer.setOpenExternalLinks(False)
        viewer.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        viewer.setText(html_text if html_text is not None else markdown_with_pipe_tables_to_html(text))
        viewer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        viewer.setStyleSheet(self.markdown_text_style())
        viewer.markdown_source = text
        layout.addWidget(viewer)
        self.markdown_widgets.append(viewer)
        return viewer

    def add_markdown_code_widget(self, lang: str, code: str, layout: QVBoxLayout):
        code_frame = QFrame()
        code_frame.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        code_frame.setStyleSheet(self.markdown_code_frame_style())
        code_layout = QVBoxLayout(code_frame)
        code_layout.setContentsMargins(0, 0, 0, 0)
        code_layout.setSpacing(0)

        header = ClickableFrame()
        header.setObjectName("markdownCodeHeader")
        header.setFixedHeight(32)
        header.setStyleSheet(self.markdown_code_header_style())
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(10, 0, 8, 0)
        header_layout.setSpacing(4)
        lang_label = QLabel((lang or "text").strip().lower() or "text")
        lang_label.setFixedHeight(24)
        lang_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header_layout.addWidget(lang_label, 0, Qt.AlignmentFlag.AlignVCenter)
        summary_label = QLabel(code_block_summary(lang, code))
        summary_label.setFixedHeight(24)
        summary_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        summary_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        header_layout.addWidget(summary_label, 1, Qt.AlignmentFlag.AlignVCenter)
        collapse_btn = QToolButton(cursor=Qt.CursorShape.PointingHandCursor)
        collapse_btn.setText("-")
        collapse_btn.setFixedSize(22, 22)
        header_layout.addWidget(collapse_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        code_layout.addWidget(header)

        code_box = QPlainTextEdit()
        code_box.setReadOnly(True)
        code_box.setPlainText(code)
        code_box.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        code_box.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        code_box.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        code_box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        code_box.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        code_box.customContextMenuRequested.connect(
            lambda pos, editor=code_box: show_chinese_edit_menu(editor, editor.mapToGlobal(pos))
        )
        code_box.setStyleSheet(self.markdown_code_style())
        code_layout.addWidget(code_box)
        collapsed_height = 34

        def parent_scroll_bar():
            parent = self.parentWidget()
            while parent is not None:
                scroll_area = getattr(parent, "scroll_area", None)
                if scroll_area is not None:
                    try:
                        return scroll_area.verticalScrollBar()
                    except RuntimeError:
                        return None
                parent = parent.parentWidget()
            return None

        def set_code_collapsed(collapsed: bool, preserve_anchor: bool = False):
            bar = parent_scroll_bar() if preserve_anchor else None
            anchor_y = header.mapToGlobal(QPoint(0, 0)).y() if bar is not None else 0
            if collapsed:
                code_box.user_locked_height = 0
                code_box.setVisible(False)
                code_box.setFixedHeight(0)
                code_box.setMaximumHeight(0)
                code_frame.setFixedHeight(collapsed_height)
                code_frame.setMaximumHeight(collapsed_height)
            else:
                code_frame.setMinimumHeight(0)
                code_frame.setMaximumHeight(QT_WIDGET_MAX_HEIGHT)
                code_box.setMinimumHeight(0)
                code_box.setMaximumHeight(QT_WIDGET_MAX_HEIGHT)
                if self.stabilize_markdown_height:
                    code_box.user_locked_height = self.code_max_height
                    code_box.setFixedHeight(self.code_max_height)
                else:
                    code_box.user_locked_height = 0
                code_box.setVisible(True)
            collapse_btn.setText("▸" if collapsed else "-")
            header.setProperty("collapsed", collapsed)
            code_frame.updateGeometry()
            parent_layout = code_frame.parentWidget().layout() if code_frame.parentWidget() is not None else None
            if parent_layout is not None:
                parent_layout.invalidate()
            self.adjust_content_height()
            if bar is not None:
                def restore_anchor():
                    try:
                        next_y = header.mapToGlobal(QPoint(0, 0)).y()
                        delta = next_y - anchor_y
                        if delta:
                            bar.setValue(max(0, min(bar.value() + delta, bar.maximum())))
                    except RuntimeError:
                        return

                QTimer.singleShot(0, restore_anchor)
                QTimer.singleShot(40, restore_anchor)

        def toggle_code():
            set_code_collapsed(code_box.isVisible(), preserve_anchor=True)

        collapse_btn.clicked.connect(toggle_code)
        header.clicked.connect(toggle_code)
        code_box.code_source = code
        code_frame.code_box = code_box
        code_frame.lang_label = lang_label
        code_frame.summary_label = summary_label
        code_frame.collapse_btn = collapse_btn
        code_frame.set_code_collapsed = set_code_collapsed
        layout.addWidget(code_frame)
        self.markdown_widgets.append(code_frame)
        self.markdown_code_widgets.append(code_box)
        if compact_code_blocks_by_default():
            set_code_collapsed(True)
        return code_box

    def update_markdown_parts_in_place(self, parts: List[Dict[str, str]], signatures: List[tuple]) -> bool:
        if signatures != self.markdown_part_signatures or len(parts) != len(self.markdown_widgets):
            return False
        apply_started = time.perf_counter()
        set_text_ms = 0
        code_index = 0
        changed = False
        for part, widget in zip(parts, self.markdown_widgets):
            if part["type"] == "code":
                if code_index >= len(self.markdown_code_widgets):
                    return False
                code_box = self.markdown_code_widgets[code_index]
                text = part.get("text", "")
                if getattr(code_box, "code_source", None) != text:
                    self.update_markdown_code_box_text(code_box, text)
                    summary_label = getattr(widget, "summary_label", None)
                    if isinstance(summary_label, QLabel):
                        lang_label = getattr(widget, "lang_label", None)
                        lang_text = lang_label.text() if isinstance(lang_label, QLabel) else part.get("lang", "")
                        summary = code_block_summary(lang_text, text)
                        summary_label.setText(summary)
                    changed = True
                code_index += 1
            else:
                text = part.get("text", "")
                if not isinstance(widget, QLabel):
                    return False
                if getattr(widget, "markdown_source", None) != text:
                    widget.markdown_source = text
                    html_started = time.perf_counter()
                    widget.setText(part.get("html") or markdown_with_pipe_tables_to_html(text))
                    set_text_ms += int((time.perf_counter() - html_started) * 1000)
                    changed = True
        if changed:
            height_started = time.perf_counter()
            self.adjust_content_height()
            height_ms = int((time.perf_counter() - height_started) * 1000)
            if not self.async_markdown_render:
                self.schedule_content_height_adjustment()
                QTimer.singleShot(80, self.adjust_content_height)
                QTimer.singleShot(180, self.adjust_content_height)
            total_ms = int((time.perf_counter() - apply_started) * 1000)
            if total_ms >= 80 or height_ms >= 50 or set_text_ms >= 50:
                logger.warning(
                    "Markdown UI apply slow changed=in_place chars=%d parts=%d set_text_ms=%d height_ms=%d total_ms=%d",
                    len(self.visible_content()),
                    len(parts),
                    set_text_ms,
                    height_ms,
                    total_ms,
                )
        return True

    def update_markdown_code_box_text(self, code_box: QPlainTextEdit, text: str):
        old_text = str(getattr(code_box, "code_source", "") or "")
        vertical = code_box.verticalScrollBar()
        horizontal = code_box.horizontalScrollBar()
        old_value = vertical.value()
        old_maximum = vertical.maximum()
        old_h_value = horizontal.value()
        was_at_bottom = old_value >= old_maximum - 4
        code_box.code_source = text

        # Streaming updates commonly append one chunk to an open fenced block.
        # Mutating the document in place avoids rebuilding the editor, which is
        # what caused visible shaking inside command/code blocks.
        if text.startswith(old_text):
            suffix = text[len(old_text):]
            if suffix:
                code_box.setUpdatesEnabled(False)
                cursor = QTextCursor(code_box.document())
                cursor.movePosition(QTextCursor.MoveOperation.End)
                cursor.insertText(suffix)
                code_box.setUpdatesEnabled(True)
        else:
            code_box.setUpdatesEnabled(False)
            code_box.setPlainText(text)
            code_box.setUpdatesEnabled(True)

        if was_at_bottom:
            if vertical.maximum() > old_maximum:
                vertical.setValue(vertical.maximum())
            else:
                vertical.setValue(min(old_value, vertical.maximum()))
        else:
            vertical.setValue(min(old_value, vertical.maximum()))
        horizontal.setValue(min(old_h_value, horizontal.maximum()))

    def render_precomputed_markdown_parts(
        self,
        parts: List[Dict[str, str]],
        signatures: List[tuple],
        layout: Optional[QVBoxLayout] = None,
        stats: Optional[Dict[str, int]] = None,
        force_rebuild: bool = False,
    ):
        if layout is None:
            layout = self.layout()
        if layout is None:
            return
        apply_started = time.perf_counter()
        if not force_rebuild and self.update_markdown_parts_in_place(parts, signatures):
            return
        code_scroll_states = self.capture_markdown_code_scroll_state()
        self.clear_markdown_widgets()
        self.markdown_part_signatures = signatures
        for part in parts:
            if part["type"] == "code":
                self.add_markdown_code_widget(part.get("lang", ""), part.get("text", ""), layout)
            else:
                self.add_markdown_text_widget(part.get("text", ""), layout, part.get("html"))
        height_started = time.perf_counter()
        self.adjust_content_height()
        height_ms = int((time.perf_counter() - height_started) * 1000)
        if not self.async_markdown_render:
            self.schedule_content_height_adjustment()
            QTimer.singleShot(80, self.adjust_content_height)
            QTimer.singleShot(180, self.adjust_content_height)
        self.restore_markdown_code_scroll_state(code_scroll_states)
        QTimer.singleShot(0, lambda states=code_scroll_states: self.restore_markdown_code_scroll_state(states))
        total_ms = int((time.perf_counter() - apply_started) * 1000)
        compute_ms = int((stats or {}).get("total_ms", 0))
        if total_ms >= 80 or height_ms >= 50 or compute_ms >= 80:
            logger.warning(
                "Markdown render timing chars=%d parts=%d compute_ms=%d split_ms=%d html_ms=%d ui_ms=%d height_ms=%d",
                len(self.visible_content()),
                len(parts),
                compute_ms,
                int((stats or {}).get("split_ms", 0)),
                int((stats or {}).get("html_ms", 0)),
                total_ms,
                height_ms,
            )

    def render_markdown_parts(self, layout: Optional[QVBoxLayout] = None):
        split_started = time.perf_counter()
        parts = split_markdown_fenced_blocks(self.visible_content())
        split_ms = int((time.perf_counter() - split_started) * 1000)
        signatures = [
            (part["type"], (part.get("lang", "") if part["type"] == "code" else ""))
            for part in parts
        ]
        self.render_precomputed_markdown_parts(parts, signatures, layout=layout, stats={"split_ms": split_ms, "total_ms": split_ms})

    def schedule_async_markdown_render(self):
        self._markdown_render_seq += 1
        if self._markdown_render_worker is not None and self._markdown_render_worker.isRunning():
            self._markdown_render_pending = True
            return
        self.start_async_markdown_render()

    def start_async_markdown_render(self):
        request_id = self._markdown_render_seq
        text = self.visible_content()
        worker = MarkdownRenderWorker(request_id, text)
        self._markdown_render_worker = worker
        worker.rendered.connect(self.apply_async_markdown_render)
        worker.finished.connect(lambda worker=worker: self.finish_async_markdown_render(worker))
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def apply_async_markdown_render(self, request_id: int, text: str, parts: List[Dict[str, str]], signatures: List[tuple], stats: Dict[str, int]):
        if request_id == self._markdown_render_seq and text == self.visible_content():
            self.render_precomputed_markdown_parts(parts, signatures, stats=stats)

    def finish_async_markdown_render(self, worker: MarkdownRenderWorker):
        if self._markdown_render_worker is worker:
            self._markdown_render_worker = None
        if self._markdown_render_pending:
            self._markdown_render_pending = False
            QTimer.singleShot(0, self.start_async_markdown_render)

    def visible_content(self) -> str:
        return str(getattr(self, "display_content", self.content) or "")

    def update_display_content(self, text: str):
        self.display_content = text
        if self.compact_user:
            self.content_label.setPlainText(text)
        elif self.markdown and self.expand_to_content:
            if self.async_markdown_render:
                self.schedule_async_markdown_render()
            else:
                self.render_markdown_parts()
        elif self.markdown:
            self.content_label.setMarkdown(text)
        else:
            self.content_label.setPlainText(text)
        if not (self.markdown and self.expand_to_content and self.async_markdown_render):
            self.adjust_content_height()
            self.schedule_content_height_adjustment()
        self.updateGeometry()
        parent = self.parentWidget()
        if parent is not None:
            parent.updateGeometry()

    def update_content(self, text: str):
        self.content = text
        self.update_display_content(text)

    def refresh_visual_settings(self):
        colors = {
            "user": (COLORS["card_user"], COLORS["border"], "你"),
            "ai": (COLORS["card_ai"], ai_border_color(), "AI 输出"),
            "system": (COLORS["card_system"], COLORS["border"], "执行结果"),
        }
        bg, border, _label_text = colors.get(getattr(self, 'role', 'system'), colors["system"])
        if self.plain_system_log:
            self.setStyleSheet("QFrame#chatBubblePlainSystemLog { background: transparent; border: none; margin: 0; }")
        elif self.flat:
            self.setStyleSheet("QFrame#chatBubble { background: transparent; border: none; margin: 2px 0; }")
        else:
            self.setStyleSheet(f"QFrame#chatBubble {{ background: {bg}; border: 1px solid {border}; border-radius: 18px; margin: 4px 0; }}")
        for label in self.findChildren(QLabel):
            if label in self.markdown_widgets:
                continue
            label.setStyleSheet(f"color: {COLORS['text']}; font-weight: 700; font-size: 13px; background: transparent;")
        copy_btn = getattr(self, "copy_btn", None)
        if isinstance(copy_btn, QPushButton):
            copy_btn.setStyleSheet(f"""
                QPushButton {{
                    background: {COLORS['surface']};
                    color: {COLORS['accent_dark']};
                    border: 1px solid {soft_accent_border_color()};
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
        paste_ai_btn = getattr(self, "paste_ai_btn", None)
        if isinstance(paste_ai_btn, QPushButton):
            paste_ai_btn.setStyleSheet(f"""
                QPushButton {{
                    background: {COLORS['surface']};
                    color: {COLORS['accent_dark']};
                    border: 1px solid {soft_accent_border_color()};
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
        if self.markdown and self.expand_to_content:
            parts = split_markdown_fenced_blocks(self.visible_content())
            signatures = [
                (part["type"], (part.get("lang", "") if part["type"] == "code" else ""))
                for part in parts
            ]
            self.render_precomputed_markdown_parts(parts, signatures, force_rebuild=True)
        elif hasattr(self, "content_label") and self.content_label is not None:
            if self.compact_user:
                self.content_label.setStyleSheet(f"""
                    QTextBrowser {{
                        background: transparent;
                        color: {COLORS['text']};
                        border: none;
                        padding: 0;
                        font-size: {scaled_font_px(14)}px;
                        line-height: 1.35;
                    }}
                    QScrollBar:vertical {{
                        background: transparent;
                        width: 8px;
                        margin: 2px 0 2px 2px;
                    }}
                    QScrollBar::handle:vertical {{
                        background: {COLORS['border_strong']};
                        border-radius: 4px;
                        min-height: 22px;
                    }}
                    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                        height: 0;
                    }}
                """)
            else:
                editor_type = "QTextBrowser" if self.markdown else "QPlainTextEdit"
                font_family = "" if self.markdown or (self.flat and self.role == "ai") else "font-family: 'SF Mono', 'Menlo', monospace;"
                flat_system_log = self.flat and self.role == "system"
                content_bg = COLORS["code_bg"] if flat_system_log else ("transparent" if self.flat else COLORS["code_bg"])
                content_border = COLORS["border"] if flat_system_log else ("transparent" if self.flat else COLORS["border"])
                content_radius = 12 if flat_system_log else (0 if self.flat else 12)
                content_padding = "10px 12px" if flat_system_log else ("2px 0" if self.flat else "10px 12px")
                self.content_label.setStyleSheet(f"""
                    {editor_type} {{
                        background: {content_bg};
                        color: {COLORS['text']};
                        border: 1px solid {content_border};
                        border-radius: {content_radius}px;
                        padding: {content_padding};
                        {font_family}
                        font-size: {scaled_font_px(12)}px;
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
        self.adjust_content_height()

    def eventFilter(self, watched, event):
        return super().eventFilter(watched, event)

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
        if not hasattr(self, "content_label") and not self.markdown_widgets:
            return
        reference = self.content_label or (self.markdown_widgets[0] if self.markdown_widgets else None)
        if reference is None:
            return
        width = reference.viewport().width() if hasattr(reference, "viewport") else reference.width()
        if abs(width - self._last_content_width) >= 18:
            self._last_content_width = width
            self.schedule_content_height_adjustment(delay=70)

    def schedule_content_height_adjustment(self, delay: int = 0):
        if self._height_adjust_scheduled:
            return
        self._height_adjust_scheduled = True
        QTimer.singleShot(delay, self.adjust_content_height)

    def adjust_content_height(self):
        if self.markdown and self.expand_to_content and self.markdown_widgets:
            self._height_adjust_scheduled = False
            next_stable_heights: Dict[int, int] = {}
            for widget in self.markdown_widgets:
                if isinstance(widget, QLabel):
                    text_width = max(120, widget.width())
                    target_height = max(34, widget.heightForWidth(text_width), widget.sizeHint().height())
                    source_text = str(getattr(widget, "markdown_source", "") or "")
                    line_spacing = max(1, widget.fontMetrics().lineSpacing())
                    source_height = estimate_wrapped_text_height(source_text, widget.fontMetrics(), text_width)
                    target_height = max(target_height, source_height + line_spacing * 3)
                    if self.stabilize_markdown_height:
                        key = id(widget)
                        target_height = max(int(self._stable_markdown_heights.get(key, 0)), target_height)
                        next_stable_heights[key] = target_height
                    if widget.height() != target_height:
                        widget.setFixedHeight(target_height)
                        widget.updateGeometry()
                    continue
                if isinstance(widget, QTextBrowser):
                    widget.setMinimumHeight(0)
                    widget.setMaximumHeight(QT_WIDGET_MAX_HEIGHT)
                    text_width = max(120, widget.viewport().width() - 10)
                    widget.document().setTextWidth(text_width)
                    line_spacing = max(1, widget.fontMetrics().lineSpacing())
                    document_height = int(widget.document().documentLayout().documentSize().height())
                    content_margin = int(widget.document().documentMargin() * 2)
                    safety_padding = max(72, line_spacing * 4)
                    target_height = max(34, document_height + content_margin + safety_padding)
                    if self.stabilize_markdown_height:
                        key = id(widget)
                        target_height = max(int(self._stable_markdown_heights.get(key, 0)), target_height)
                        next_stable_heights[key] = target_height
                    if widget.height() != target_height:
                        widget.setFixedHeight(target_height)
                        widget.updateGeometry()
                    bar = widget.verticalScrollBar()
                    overflow = bar.maximum()
                    if overflow > 0:
                        inferred_content_height = overflow + bar.pageStep()
                        fallback_height = int(widget.height() * 2.5) if inferred_content_height > widget.height() * 1.8 else 0
                        target_height = max(
                            target_height + overflow + line_spacing,
                            inferred_content_height + line_spacing * 2,
                            fallback_height,
                        )
                        widget.setFixedHeight(target_height)
                        bar.setValue(0)
                        widget.updateGeometry()
                    widget.setVerticalScrollBarPolicy(
                        Qt.ScrollBarPolicy.ScrollBarAsNeeded
                        if widget.verticalScrollBar().maximum() > 0
                        else Qt.ScrollBarPolicy.ScrollBarAlwaysOff
                    )
            for code_box in self.markdown_code_widgets:
                if not code_box.isVisible():
                    continue
                locked_height = int(getattr(code_box, "user_locked_height", 0) or 0)
                if locked_height > 0:
                    if code_box.height() != locked_height:
                        code_box.setFixedHeight(locked_height)
                        code_box.updateGeometry()
                    key = id(code_box)
                    next_stable_heights[key] = locked_height
                    continue
                code_box.setMinimumHeight(0)
                code_box.setMaximumHeight(QT_WIDGET_MAX_HEIGHT)
                metrics = code_box.fontMetrics()
                text = code_box.toPlainText()
                line_count = max(1, len(text.splitlines()) or 1)
                vertical_padding = 26
                scrollbar_room = 12 if code_box.horizontalScrollBarPolicy() != Qt.ScrollBarPolicy.ScrollBarAlwaysOff else 0
                if self.stabilize_markdown_height and line_count >= self.streaming_code_fixed_height_threshold:
                    target_height = self.code_max_height
                else:
                    target_height = min(self.code_max_height, max(46, line_count * metrics.lineSpacing() + vertical_padding + scrollbar_room))
                if self.stabilize_markdown_height:
                    key = id(code_box)
                    target_height = max(int(self._stable_markdown_heights.get(key, 0)), target_height)
                    next_stable_heights[key] = target_height
                if code_box.height() != target_height:
                    code_box.setFixedHeight(target_height)
                    code_box.updateGeometry()
            if self.stabilize_markdown_height:
                self._stable_markdown_heights = next_stable_heights
            self.updateGeometry()
            parent = self.parentWidget()
            if parent is not None:
                parent.updateGeometry()
            return
        if not hasattr(self, "content_label") or self.content_label is None:
            return
        self._height_adjust_scheduled = False
        if self.compact_user:
            available_width = max(120, self.content_label.width() - 8)
            metrics = self.content_label.fontMetrics()
            line_spacing = max(1, metrics.lineSpacing())
            target_height = max(line_spacing + 4, estimate_wrapped_text_height(self.visible_content() or " ", metrics, available_width) - 30)
            target_height = min(self.max_content_height, target_height)
            self.content_label.setVerticalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAsNeeded if target_height >= self.max_content_height else Qt.ScrollBarPolicy.ScrollBarAlwaysOff
            )
            if self.content_label.height() != target_height:
                self.content_label.setFixedHeight(target_height)
            return
        text = self.visible_content() or self.content_label.toPlainText() or " "
        available_width = max(120, self.content_label.viewport().width() - 10)
        metrics = self.content_label.fontMetrics()
        line_spacing = max(1, metrics.lineSpacing())
        max_visual_lines = None if self.expand_to_content else max(1, (self.max_content_height - 36 + line_spacing - 1) // line_spacing)
        natural_height = estimate_wrapped_text_height(text, metrics, available_width, max_visual_lines)
        target_height = max(self.min_content_height, int(natural_height))
        if not self.expand_to_content:
            target_height = min(self.max_content_height, target_height)
        if self.content_label.height() != target_height:
            self.content_label.setFixedHeight(target_height)


class ExecutionLogPanel(QFrame):
    def __init__(self, content: str = "", parent=None, max_content_height: int = 210, title: str = ""):
        super().__init__(parent)
        self.content = mask_low_value_context_markers_for_display(content)
        self.max_content_height = max_content_height
        self.setObjectName("executionLogPanel")
        self.setStyleSheet("QFrame#executionLogPanel { background: transparent; border: none; margin: 0; }")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(8)

        if title:
            self.title_label = QLabel(title)
            self.title_label.setStyleSheet(f"""
                QLabel {{
                    background: transparent;
                    color: {COLORS['text']};
                    border: none;
                    font-size: {scaled_font_px(14)}px;
                    font-weight: 900;
                    padding: 0;
                }}
            """)
            layout.addWidget(self.title_label)
        else:
            self.title_label = None

        self.editor = QPlainTextEdit()
        self.editor.setReadOnly(True)
        self.editor.setPlainText(self.content)
        self.editor.setMaximumBlockCount(20000)
        self.editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.editor.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.editor.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.editor.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.editor.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.editor.customContextMenuRequested.connect(
            lambda pos, editor=self.editor: show_chinese_edit_menu(editor, editor.mapToGlobal(pos))
        )
        self.editor.setStyleSheet(f"""
            QPlainTextEdit {{
                background: {COLORS['code_bg']};
                color: {COLORS['text']};
                border: 1px solid {COLORS['border']};
                border-radius: 12px;
                padding: 10px 12px;
                font-family: 'SF Mono', 'Menlo', monospace;
                font-size: {scaled_font_px(12)}px;
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
        layout.addWidget(self.editor)
        self.adjust_content_height()

    def visible_content(self) -> str:
        return self.content or self.editor.toPlainText() or " "

    def adjust_content_height(self):
        available_width = max(120, self.editor.viewport().width() - 10)
        metrics = self.editor.fontMetrics()
        line_spacing = max(1, metrics.lineSpacing())
        max_visual_lines = max(1, (self.max_content_height - 36 + line_spacing - 1) // line_spacing)
        natural_height = estimate_wrapped_text_height(self.visible_content(), metrics, available_width, max_visual_lines)
        target_height = min(self.max_content_height, max(58, int(natural_height)))
        if self.editor.height() != target_height:
            self.editor.setFixedHeight(target_height)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.adjust_content_height()

    def update_content(self, text: str):
        self.content = mask_low_value_context_markers_for_display(text)
        self.editor.setPlainText(self.content)
        self.adjust_content_height()

    def refresh_visual_settings(self):
        if self.title_label is not None:
            self.title_label.setStyleSheet(f"""
                QLabel {{
                    background: transparent;
                    color: {COLORS['text']};
                    border: none;
                    font-size: {scaled_font_px(14)}px;
                    font-weight: 900;
                    padding: 0;
                }}
            """)
        self.editor.setStyleSheet(f"""
            QPlainTextEdit {{
                background: {COLORS['code_bg']};
                color: {COLORS['text']};
                border: 1px solid {COLORS['border']};
                border-radius: 12px;
                padding: 10px 12px;
                font-family: 'SF Mono', 'Menlo', monospace;
                font-size: {scaled_font_px(12)}px;
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
        self.adjust_content_height()


class ChangeSummaryCard(QFrame):
    undo_requested = Signal(object)
    redo_requested = Signal(object)

    def __init__(self, records: List[Dict[str, object]], parent=None):
        super().__init__(parent)
        self.records = records
        self.detail_widgets: List[QTextEdit] = []
        self.file_row_widgets: List[QFrame] = []
        self.file_row_toggles: List[QFrame] = []
        self.file_row_text_labels: List[QLabel] = []
        self.file_row_arrow_labels: List[QLabel] = []
        self.file_row_stat_labels: List[QLabel] = []
        self.undo_btn: Optional[QPushButton] = None
        self.title_label: Optional[QLabel] = None
        self.stats_add_label: Optional[QLabel] = None
        self.stats_del_label: Optional[QLabel] = None
        self.status_label: Optional[QLabel] = None
        self.is_undone = False
        self.setup_ui()

    def setup_ui(self):
        self.setObjectName("changeSummaryCard")
        self.apply_state_style(False)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        text_records = [r for r in self.records if not r.get("binary")]
        binary_count = len(self.records) - len(text_records)
        additions = sum(int(r["additions"]) for r in text_records)
        deletions = sum(int(r["deletions"]) for r in text_records)
        header = QHBoxLayout()
        title = QLabel(f"{len(self.records)} files changed")
        self.title_label = title
        title.setStyleSheet(f"color: {COLORS['text']}; font-size: 13px; font-weight: 900; background: transparent; border: none;")
        stat_labels = []
        if text_records:
            stat_labels.append(f"+{additions}")
        if binary_count:
            stat_labels.append(f"{binary_count} binary")
        stats_add = QLabel("  ·  ".join(stat_labels) if stat_labels else "")
        self.stats_add_label = stats_add
        stats_add.setStyleSheet(f"color: {COLORS['success'] if text_records else COLORS['text_secondary']}; font-size: 12px; font-weight: 900; background: transparent; border: none;")
        stats_del = QLabel(f"-{deletions}" if text_records else "")
        self.stats_del_label = stats_del
        stats_del.setStyleSheet(f"color: {COLORS['danger']}; font-size: 12px; font-weight: 900; background: transparent; border: none;")
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
        header.addWidget(stats_add)
        header.addSpacing(6)
        header.addWidget(stats_del)
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

        row = ClickableFrame()
        row.setObjectName("changeFileToggle")
        row.setCursor(Qt.PointingHandCursor)
        row.setStyleSheet(f"""
            QFrame#changeFileToggle {{
                background: transparent;
                border: none;
                padding: 4px 2px;
            }}
            QFrame#changeFileToggle:hover QLabel#changePathLabel {{
                color: {COLORS['accent_dark']};
            }}
            QLabel {{
                background: transparent;
                border: none;
                font-size: 12px;
                font-weight: 800;
            }}
        """)
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(2, 2, 2, 2)
        row_layout.setSpacing(7)
        arrow_label = QLabel("›")
        arrow_label.setFixedWidth(12)
        arrow_label.setStyleSheet(f"color: {COLORS['text_secondary']};")
        arrow_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        path_label = QLabel(str(record["path"]))
        path_label.setObjectName("changePathLabel")
        path_label.setStyleSheet(f"color: {COLORS['text']};")
        path_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        path_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        add_label = QLabel(f"+{record['additions']}")
        if record.get("binary"):
            status_text = {"added": "added", "deleted": "deleted"}.get(str(record.get("status", "")), "changed")
            add_label.setText(status_text)
            add_label.setStyleSheet(f"color: {COLORS['text_secondary']};")
        else:
            add_label.setStyleSheet(f"color: {COLORS['success']};")
        add_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        del_label = QLabel(f"-{record['deletions']}")
        if record.get("binary"):
            del_label.setText("")
        del_label.setStyleSheet(f"color: {COLORS['danger']};")
        del_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        row_layout.addWidget(arrow_label)
        row_layout.addWidget(path_label, 1)
        row_layout.addWidget(add_label)
        row_layout.addWidget(del_label)
        self.file_row_toggles.append(row)
        self.file_row_arrow_labels.append(arrow_label)
        self.file_row_text_labels.append(path_label)
        self.file_row_stat_labels.extend([add_label, del_label])
        layout.addWidget(row)

        diff_view = QTextEdit()
        diff_view.setReadOnly(True)
        if record.get("binary"):
            diff_view.setHtml(self.render_binary_change_html(record))
            diff_view.setFixedHeight(92)
        else:
            diff_view.setHtml(render_diff_html(record))
            diff_view.setFixedHeight(260)
        diff_view.setVisible(False)
        diff_view.setStyleSheet(f"""
            QTextEdit {{
                background: {COLORS['code_bg']};
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
            arrow_label.setText("⌄" if visible else "›")

        row.clicked.connect(toggle)
        return wrapper

    def render_binary_change_html(self, record: Dict[str, object]) -> str:
        status = str(record.get("status", "modified") or "modified")
        status_text = {
            "added": "新增二进制/Office 文件",
            "deleted": "删除二进制/Office 文件",
            "modified": "二进制/Office 文件已修改",
        }.get(status, "二进制/Office 文件已变更")
        path = html.escape(str(record.get("path", "")))
        return (
            f"<html><body style='margin:0; background:{COLORS['code_bg']}; color:{COLORS['text']}; "
            "font-family: -apple-system, BlinkMacSystemFont, \"Segoe UI\", sans-serif;'>"
            "<div style='padding:12px 14px;'>"
            f"<div style='font-weight:700; font-size:13px;'>{status_text}</div>"
            f"<div style='margin-top:6px; color:{COLORS['text_secondary']}; font-size:12px;'>{path}</div>"
            f"<div style='margin-top:8px; color:{COLORS['text_secondary']}; font-size:12px;'>"
            "此类文件无法生成逐行文本 diff，但仍可撤销/重做本轮变更。"
            "</div></div></body></html>"
        )

    def refresh_visual_settings(self):
        for record, diff_view in zip(self.records, self.detail_widgets):
            if record.get("binary"):
                diff_view.setHtml(self.render_binary_change_html(record))
            else:
                diff_view.setHtml(render_diff_html(record))
            diff_view.setStyleSheet(f"""
                QTextEdit {{
                    background: {COLORS['code_bg']};
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
        self.apply_state_style(self.is_undone)

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
        if self.stats_add_label:
            self.stats_add_label.setStyleSheet(f"color: {COLORS['muted'] if undone else COLORS['success']}; font-size: 12px; font-weight: 900; background: transparent; border: none;")
        if self.stats_del_label:
            self.stats_del_label.setStyleSheet(f"color: {COLORS['muted'] if undone else COLORS['danger']}; font-size: 12px; font-weight: 900; background: transparent; border: none;")
        if self.status_label:
            self.status_label.setVisible(undone)
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
        if self.undo_btn:
            accent_text = COLORS['accent_dark'] if undone else COLORS['danger']
            hover_bg = COLORS['accent_light'] if undone else COLORS['danger_soft']
            hover_border = COLORS['accent'] if undone else "#ffd0d2"
            self.undo_btn.setStyleSheet(f"""
                QPushButton {{
                    background: {COLORS['surface']};
                    color: {accent_text};
                    border: 1px solid {hover_border};
                    border-radius: 10px;
                    padding: 6px 12px;
                    font-size: 12px;
                    font-weight: 900;
                }}
                QPushButton:hover {{
                    background: {hover_bg};
                }}
                QPushButton:disabled {{
                    color: {COLORS['muted']};
                    border-color: {COLORS['border']};
                    background: {COLORS['surface_alt']};
                }}
            """)
        for row in self.file_row_widgets:
            row.setStyleSheet(f"""
                QFrame#changeFileRow {{
                    background: {COLORS['code_bg']};
                    border: 1px solid {COLORS['border']};
                    border-radius: 12px;
                }}
            """)
        for row in self.file_row_toggles:
            row.setStyleSheet(f"""
                QFrame#changeFileToggle {{
                    background: transparent;
                    border: none;
                    padding: 4px 2px;
                }}
                QFrame#changeFileToggle:hover QLabel#changePathLabel {{
                    color: {COLORS['accent_dark']};
                }}
                QLabel {{
                    background: transparent;
                    border: none;
                    font-size: 12px;
                    font-weight: 800;
                }}
            """)
        for label in [*self.file_row_text_labels, *self.file_row_stat_labels]:
            font = label.font()
            font.setStrikeOut(undone)
            label.setFont(font)
        for label in self.file_row_text_labels:
            label.setStyleSheet(f"color: {COLORS['muted'] if undone else COLORS['text']};")
        for idx, label in enumerate(self.file_row_stat_labels):
            text = label.text().strip()
            if text in {"added", "deleted", "changed"}:
                color = COLORS["muted"] if undone else COLORS["text_secondary"]
            else:
                color = COLORS["muted"] if undone else (COLORS["success"] if idx % 2 == 0 else COLORS["danger"])
            label.setStyleSheet(f"color: {color};")
        for label in self.file_row_arrow_labels:
            label.setStyleSheet(f"color: {COLORS['muted'] if undone else COLORS['text_secondary']};")

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
    rename_requested = Signal(str, str)

    def __init__(self, thread: Dict[str, object], active: bool = False, parent=None):
        super().__init__(parent)
        self.thread = thread
        self.thread_id = str(thread.get("id", DEFAULT_THREAD_ID))
        self.active = active
        self.deletable = self.thread_id != DEFAULT_THREAD_ID
        self._editing_title = False
        self._rename_cancelled = False
        self.setObjectName("threadCard")
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(50)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setup_ui()
        self.apply_style()

    def setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 8, 10)
        layout.setSpacing(8)
        title = QLabel(str(self.thread.get("title", "会话")))
        title.setStyleSheet("background: transparent; border: none; font-size: 12px; font-weight: 900;")
        title.setMinimumWidth(0)
        title.setTextFormat(Qt.TextFormat.PlainText)
        title.setWordWrap(False)
        title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        layout.addWidget(title)
        self.title_edit = QLineEdit(str(self.thread.get("title", "会话")))
        self.title_edit.setMaxLength(80)
        self.title_edit.setVisible(False)
        self.title_edit.setMinimumWidth(0)
        self.title_edit.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.title_edit.editingFinished.connect(self.commit_title_edit)
        self.title_edit.installEventFilter(self)
        self.title_edit.setStyleSheet(f"""
            QLineEdit {{
                background: {COLORS['surface']};
                color: {COLORS['text']};
                border: 1px solid {COLORS['accent']};
                border-radius: 8px;
                padding: 4px 6px;
                font-size: 12px;
                font-weight: 900;
            }}
        """)
        layout.addWidget(self.title_edit)
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
        border = COLORS["accent"] if self.active else COLORS["border"]
        color = COLORS["accent_dark"] if self.active else COLORS["text"]
        hover_bg = COLORS["accent_light"] if self.active else COLORS["surface_alt"]
        hover_border = COLORS["accent"] if self.active else COLORS["border_strong"]
        self.setStyleSheet(f"""
            QFrame#threadCard {{
                background: {bg};
                border: 1px solid {border};
                border-radius: 12px;
            }}
            QFrame#threadCard:hover {{
                background: {hover_bg};
                border-color: {hover_border};
            }}
        """)
        self.title_label.setStyleSheet(f"background: transparent; border: none; color: {color}; font-size: 12px; font-weight: 900;")
        self.title_edit.setStyleSheet(f"""
            QLineEdit {{
                background: {COLORS['surface']};
                color: {COLORS['text']};
                border: 1px solid {COLORS['accent']};
                border-radius: 8px;
                padding: 4px 6px;
                font-size: 12px;
                font-weight: 900;
            }}
        """)
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
        self.delete_btn.setVisible(self.deletable and self.active and not self._editing_title)

    def begin_title_edit(self):
        if self._editing_title:
            return
        self._editing_title = True
        self._rename_cancelled = False
        self.title_edit.setText(self.title_label.text())
        self.title_label.setVisible(False)
        self.title_edit.setVisible(True)
        self.delete_btn.setVisible(False)
        self.title_edit.setFocus(Qt.FocusReason.MouseFocusReason)
        self.title_edit.selectAll()

    def finish_title_edit(self, commit: bool):
        if not self._editing_title:
            return
        self._editing_title = False
        self.title_edit.setVisible(False)
        self.title_label.setVisible(True)
        self.apply_style()
        if not commit:
            return
        title = self.title_edit.text().strip()
        if title and title != self.title_label.text():
            self.rename_requested.emit(self.thread_id, title)

    def commit_title_edit(self):
        if self._rename_cancelled:
            self.finish_title_edit(False)
        else:
            self.finish_title_edit(True)

    def eventFilter(self, watched, event):
        if watched is self.title_edit and self._editing_title and event.type() == QEvent.Type.KeyPress:
            if event.key() == Qt.Key.Key_Escape:
                self._rename_cancelled = True
                self.finish_title_edit(False)
                return True
        return super().eventFilter(watched, event)

    def set_active(self, active: bool):
        self.active = active
        self.apply_style()

    def enterEvent(self, event):
        super().enterEvent(event)
        if self.deletable and not self._editing_title:
            self.delete_btn.setVisible(True)

    def leaveEvent(self, event):
        super().leaveEvent(event)
        if self.deletable and not self.active and not self._editing_title:
            self.delete_btn.setVisible(False)

    def mousePressEvent(self, event: QMouseEvent):
        if self.delete_btn.geometry().contains(event.position().toPoint()):
            super().mousePressEvent(event)
            return
        self.selected.emit(self.thread_id)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent):
        if self.delete_btn.geometry().contains(event.position().toPoint()):
            super().mouseDoubleClickEvent(event)
            return
        self.selected.emit(self.thread_id)
        self.begin_title_edit()
        event.accept()


class SkillCard(QFrame):
    selected = Signal(str)
    delete_requested = Signal(str)

    def __init__(self, skill: Dict[str, str], parent=None):
        super().__init__(parent)
        self.skill = skill
        self.skill_id = str(skill.get("id") or "")
        self.setObjectName("skillCard")
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(112)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 8, 10)
        layout.setSpacing(4)
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)
        title = QLabel(str(self.skill.get("name") or self.skill_id or "skill"))
        self.title_label = title
        title.setStyleSheet(f"background: transparent; border: none; color: {COLORS['text']}; font-size: 12px; font-weight: 900;")
        title.setWordWrap(True)
        title.setMaximumHeight(34)
        header.addWidget(title, 1)
        self.delete_btn = QToolButton(self, cursor=Qt.PointingHandCursor)
        self.delete_btn.setText("×")
        self.delete_btn.setToolTip("")
        self.delete_btn.setFixedSize(24, 24)
        self.delete_btn.setVisible(False)
        self.delete_btn.clicked.connect(lambda: self.delete_requested.emit(self.skill_id))
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
        header.addWidget(self.delete_btn)
        layout.addLayout(header)
        description = str(self.skill.get("description") or "").strip()
        if description:
            desc = QLabel(description)
            self.desc_label = desc
            desc.setWordWrap(True)
            desc.setMaximumHeight(48)
            desc.setStyleSheet(f"background: transparent; border: none; color: {COLORS['text_secondary']}; font-size: 11px;")
            layout.addWidget(desc)
        else:
            self.desc_label = None
        self.apply_style()

    def apply_style(self):
        self.setStyleSheet(f"""
            QFrame#skillCard {{
                background: {COLORS['surface']};
                border: 1px solid {COLORS['border']};
                border-radius: 12px;
            }}
            QFrame#skillCard:hover {{
                background: {COLORS['surface_alt']};
                border-color: {COLORS['accent']};
            }}
        """)
        self.title_label.setStyleSheet(f"background: transparent; border: none; color: {COLORS['text']}; font-size: 12px; font-weight: 900;")
        if self.desc_label is not None:
            self.desc_label.setStyleSheet(f"background: transparent; border: none; color: {COLORS['text_secondary']}; font-size: 11px;")

    def enterEvent(self, event):
        super().enterEvent(event)
        self.delete_btn.setVisible(True)

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self.delete_btn.setVisible(False)

    def mousePressEvent(self, event: QMouseEvent):
        if self.delete_btn.geometry().contains(event.position().toPoint()):
            super().mousePressEvent(event)
            return
        self.selected.emit(self.skill_id)
        super().mousePressEvent(event)


class Sidebar(QFrame):
    file_opened = Signal(str)
    back_home_requested = Signal()
    thread_selected = Signal(str)
    new_thread_requested = Signal()
    delete_thread_requested = Signal(str)
    rename_thread_requested = Signal(str, str)
    new_skill_requested = Signal()
    skill_selected = Signal(str)
    delete_skill_requested = Signal(str)
    delete_path_requested = Signal(str)
    
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
        self.skill_cards: Dict[str, SkillCard] = {}
        
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
        self.skills_tab_btn = self.create_nav_button("技能列表")
        self.threads_tab_btn.clicked.connect(lambda: self.set_tab("threads"))
        self.files_tab_btn.clicked.connect(lambda: self.set_tab("files"))
        self.skills_tab_btn.clicked.connect(lambda: self.set_tab("skills"))
        nav_row.addWidget(self.threads_tab_btn)
        nav_row.addWidget(self.files_tab_btn)
        nav_row.addWidget(self.skills_tab_btn)
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
        self.thread_section_label = label
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
        self.thread_list.setMinimumWidth(0)
        self.thread_list.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.thread_list_layout = QVBoxLayout(self.thread_list)
        self.thread_list_layout.setContentsMargins(0, 0, 0, 0)
        self.thread_list_layout.setSpacing(8)
        self.thread_list_layout.addStretch()
        self.thread_scroll = QScrollArea()
        self.thread_scroll.setWidgetResizable(True)
        self.thread_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.thread_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.thread_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.thread_scroll.setWidget(self.thread_list)
        self.thread_scroll.setStyleSheet(self.sidebar_list_scroll_style())
        threads_layout.addWidget(self.thread_scroll, 1)
        self.stack.addWidget(self.threads_page)

        self.skills_page = QWidget()
        skills_layout = QVBoxLayout(self.skills_page)
        skills_layout.setContentsMargins(0, 0, 0, 0)
        skills_layout.setSpacing(8)
        skill_label = QLabel("技能")
        self.skill_section_label = skill_label
        skill_label.setStyleSheet(f"color: {COLORS['text']}; font-size: 13px; font-weight: 900; background: transparent; border: none;")
        skills_layout.addWidget(skill_label)
        self.skill_list = QWidget()
        self.skill_list_layout = QVBoxLayout(self.skill_list)
        self.skill_list_layout.setContentsMargins(0, 0, 0, 0)
        self.skill_list_layout.setSpacing(8)
        self.skill_list_layout.addStretch()
        self.skill_scroll = QScrollArea()
        self.skill_scroll.setWidgetResizable(True)
        self.skill_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.skill_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.skill_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.skill_scroll.setWidget(self.skill_list)
        self.skill_scroll.setStyleSheet(self.sidebar_list_scroll_style())
        skills_layout.addWidget(self.skill_scroll, 1)
        self.stack.addWidget(self.skills_page)
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
                border-color: {soft_accent_border_color()};
            }}
        """)
        self.bottom_btn.clicked.connect(lambda: self.refresh_tree(self._root_path))
        layout.addWidget(self.bottom_btn)
        self.set_tab("threads")
        self.setVisible(False)

    def sidebar_list_scroll_style(self) -> str:
        return f"""
            QScrollArea {{
                background: transparent;
                border: none;
            }}
            QScrollArea QWidget#qt_scrollarea_viewport {{
                background: transparent;
            }}
            QScrollBar:vertical {{
                background: transparent;
                width: 8px;
                margin: 4px 0 4px 2px;
            }}
            QScrollBar::handle:vertical {{
                background: {COLORS['border_strong']};
                border-radius: 4px;
                min-height: 28px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0;
            }}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
                background: transparent;
            }}
        """

    def apply_theme_style(self):
        self.setStyleSheet(f"""
            QFrame {{
                background: {COLORS['sidebar_bg']};
                border: none;
            }}
        """)
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
        for label in (getattr(self, "thread_section_label", None), getattr(self, "skill_section_label", None)):
            if label is not None:
                label.setStyleSheet(f"color: {COLORS['text']}; font-size: 13px; font-weight: 900; background: transparent; border: none;")
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
                border-color: {COLORS['accent']};
            }}
        """)
        self.thread_scroll.setStyleSheet(self.sidebar_list_scroll_style())
        self.skill_scroll.setStyleSheet(self.sidebar_list_scroll_style())
        self.apply_tree_style()
        self.set_tab(self._active_tab)
        for card in self.thread_cards.values():
            card.apply_style()
        for card in self.skill_cards.values():
            card.apply_style()

    def create_nav_button(self, text: str) -> QPushButton:
        btn = QPushButton(text, cursor=Qt.PointingHandCursor)
        btn.setFixedHeight(30)
        btn.setStyleSheet("QPushButton { border: none; background: transparent; }")
        return btn

    def nav_button_style(self, active: bool) -> str:
        return f"""
            QPushButton {{
                background: {COLORS['accent_light'] if active else 'transparent'};
                color: {COLORS['accent_dark'] if active else COLORS['text']};
                border: 1px solid {COLORS['accent'] if active else 'transparent'};
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
        page = self.threads_page if tab == "threads" else (self.skills_page if tab == "skills" else self.files_page)
        self.stack.setCurrentWidget(page)
        self.files_tab_btn.setStyleSheet(self.nav_button_style(tab == "files"))
        self.threads_tab_btn.setStyleSheet(self.nav_button_style(tab == "threads"))
        self.skills_tab_btn.setStyleSheet(self.nav_button_style(tab == "skills"))
        self.bottom_btn.setText("新建会话" if tab == "threads" else ("添加技能" if tab == "skills" else "刷新文件树"))
        try:
            self.bottom_btn.clicked.disconnect()
        except (TypeError, RuntimeError):
            pass
        if tab == "threads":
            self.bottom_btn.clicked.connect(self.new_thread_requested.emit)
        elif tab == "skills":
            self.bottom_btn.clicked.connect(self.new_skill_requested.emit)
        else:
            self.bottom_btn.clicked.connect(lambda: self.refresh_tree(self._root_path))

    def apply_tree_style(self):
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
                background: {COLORS['accent_light']};
                color: {COLORS['accent_dark']};
                border: 1px solid {COLORS['accent']};
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

    def setup_tree(self):
        self.tree.setHeaderHidden(True)
        self.tree.setRootIsDecorated(False)
        self.tree.setIndentation(22)
        self.tree.setAnimated(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tree.setAllColumnsShowFocus(False)
        self.tree.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.apply_tree_style()
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
            card.rename_requested.connect(self.rename_thread_requested.emit)
            self.thread_cards[card.thread_id] = card
            self.thread_list_layout.insertWidget(self.thread_list_layout.count() - 1, card)

    def set_active_thread(self, thread_id: str):
        self._active_thread_id = safe_thread_id(thread_id)
        for card in self.thread_cards.values():
            card.set_active(card.thread_id == self._active_thread_id)

    def set_skills(self, skills: List[Dict[str, str]]):
        while self.skill_list_layout.count() > 1:
            item = self.skill_list_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        self.skill_cards = {}
        if not skills:
            empty = QLabel("暂无技能。")
            empty.setWordWrap(True)
            empty.setStyleSheet(f"color: {COLORS['text_secondary']}; background: transparent; border: none; font-size: 12px; padding: 10px;")
            self.skill_list_layout.insertWidget(0, empty)
            return
        for skill in skills:
            card = SkillCard(skill, parent=self.skill_list)
            card.selected.connect(self.skill_selected.emit)
            card.delete_requested.connect(self.delete_skill_requested.emit)
            self.skill_cards[card.skill_id] = card
            self.skill_list_layout.insertWidget(self.skill_list_layout.count() - 1, card)
    
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
        if not path:
            return
        menu = QMenu(self)
        is_dir = os.path.isdir(path)
        if os.path.isfile(path):
            menu.addAction("打开文件", lambda: self.file_opened.emit(path))
        elif is_dir:
            menu.addAction("展开/折叠", lambda: item.setExpanded(not item.isExpanded()))
        target = path if os.path.isdir(path) else os.path.dirname(path)
        menu.addAction("打开目录", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(target)))
        menu.addAction("复制路径", lambda: QApplication.clipboard().setText(path))
        root_path = os.path.abspath(self._root_path) if self._root_path else ""
        if os.path.abspath(path) != root_path:
            menu.addSeparator()
            delete_label = "删除文件夹" if is_dir else "删除文件"
            delete_action = QAction(delete_label, self)
            delete_action.triggered.connect(lambda _checked=False, p=path: self.delete_path_requested.emit(p))
            menu.addAction(delete_action)
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def on_item_click(self, item, col):
        path = item.data(0, Qt.UserRole)
        if os.path.isdir(path):
            item.setExpanded(not item.isExpanded())
    
    def on_double_click(self, item, col):
        path = item.data(0, Qt.UserRole)
        if os.path.isfile(path):
            self.file_opened.emit(path)

    def expand(self):
        self._collapsed = False
        self.setVisible(True)
        self.setFixedWidth(self._expanded_width)

    def collapse(self):
        self._collapsed = True
        self.setVisible(False)
        self.setFixedWidth(0)
    
    def toggle(self):
        if self._collapsed:
            self.expand()
        else:
            self.collapse()

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
                border: 1px solid {soft_accent_border_color()};
                border-radius: 14px;
                padding: 7px 13px;
                font-size: 11px;
                font-weight: 800;
                letter-spacing: 1px;
            }}
        """)
        shell_layout.addWidget(brand_badge, alignment=Qt.AlignCenter)

        title = QLabel("Agent. QT智能体", alignment=Qt.AlignCenter)
        title.setStyleSheet(f"font-size: 34px; font-weight: 900; color: {COLORS['text']}; background: transparent; border: none;")
        shell_layout.addWidget(title)

        subtitle = QLabel("粘贴 AI 回复后，自动识别命令块、文件正文、扩展指令与后台进程。")
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
            ("PROMPT", "复制提示词", "生成带工作区路径、执行协议和扩展指令说明的完整提示词。"),
            ("PASTE", "粘贴 AI 回复", "识别 Bash、HTML、CSS、JS、SVG、JSON 等代码块并安全分离内容。"),
            ("RUN", "自动执行", "按顺序执行命令块、替换占位符、保留 heredoc，并记录执行结果。"),
            ("TERM", "终端与后台", "长任务会进入底部终端区域，可持续查看输出、复制日志和手动停止。"),
            ("FILES", "文件与变更", "侧栏浏览项目文件，展示文件变更卡片，并支持 Undo / Redo。"),
            ("FLOW", "连续协作", "保留对话、执行结果和自动化上下文，方便继续编程、办公和调研工作。"),
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
                    border: 1px solid {soft_accent_border_color()};
                    border-radius: 10px;
                    font-size: 10px;
                    font-weight: 900;
                }}
            """)
            row.addWidget(badge, alignment=Qt.AlignVCenter)
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
        dir_frame = QFrame()
        dir_frame.setStyleSheet(
            f"background: {COLORS['accent_light']}; border-radius: 18px; border: 1px solid {soft_accent_border_color()};"
        )
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
                border: 1px solid {soft_accent_border_color()};
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
                    border: 1px solid {soft_accent_border_color()};
                    border-radius: 12px;
                    padding: 10px 14px;
                    font-size: 12px;
                    font-weight: 700;
                }}
                QPushButton:hover {{
                    background: {COLORS['surface_alt']};
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
        self.skills: List[Dict[str, str]] = []
        self.selected_skill_ids: set[str] = set()
        self.cmd_outputs = []
        self.pending_snapshot: Dict[str, bytes] = {}
        self.change_tracker: Optional[InternalGitChangeTracker] = None
        self.pending_internal_git_commit = ""
        self.pending_long_running_launches = 0
        self.pending_terminal_launches: List[Dict[str, object]] = []
        self.history_entries: List[Dict[str, object]] = []
        self.result_bubble: Optional[ExecutionLogPanel] = None
        self.worker: Optional[ExecuteWorker] = None
        self.worker_thread_id = ""
        self.thread_run_states: Dict[str, Dict[str, object]] = {}
        self.thread_execution_workers: Dict[str, ExecuteWorker] = {}
        self.thread_execution_outputs: Dict[str, List[str]] = {}
        self.thread_execution_bubbles: Dict[str, ExecutionLogPanel] = {}
        self.automation_manager = AutomationProviderManager()
        self.wechat_bridge = WeChatBridge(self)
        self.wechat_bridge.request_signal.connect(self.handle_wechat_bridge_request)
        self.wechat_connector = WeChatConnector(self)
        self.wechat_connector.status_signal.connect(self.handle_wechat_connector_status)
        self.wechat_connector.qr_signal.connect(self.show_wechat_qr_dialog)
        self.wechat_qr_image_workers: List[WeChatQrImageWorker] = []
        self.wechat_qr_dialog: Optional[QDialog] = None
        self.wechat_qr_image_label: Optional[QLabel] = None
        self.wechat_qr_link_edit: Optional[QPlainTextEdit] = None
        self.wechat_qr_open_btn: Optional[QPushButton] = None
        self.wechat_qr_current_url = ""
        self.wechat_active_request_id = ""
        self.wechat_active_start_index = 0
        self.wechat_active_silent = True
        self.wechat_active_to_user = ""
        self.wechat_active_context_token = ""
        self.wechat_active_sent_files: set[str] = set()
        self.wechat_interrupt_confirm_to_user = ""
        self.wechat_interrupt_confirm_context_token = ""
        self.automation_active_messages: List[Dict[str, str]] = []
        self.automation_active_model = ""
        self.automation_enabled = automation_enabled_setting()
        self.automation_model = AUTOMATION_DEFAULT_MODEL
        self.automation_context_mode = automation_context_mode_setting()
        self.automation_context_worker: Optional[AutomationContextBuildWorker] = None
        self.automation_worker: Optional[AutomationChatWorker] = None
        self.web_research_worker: Optional[WebResearchWorker] = None
        self.thread_automation_context_workers: Dict[str, AutomationContextBuildWorker] = {}
        self.thread_automation_workers: Dict[str, AutomationChatWorker] = {}
        self.automation_request_serial = 0
        self.automation_preview_worker: Optional[AutomationPreviewWorker] = None
        self.automation_preview_retired_workers: List[AutomationPreviewWorker] = []
        self.automation_preview_serial = 0
        self.automation_preview_bubble: Optional[QFrame] = None
        self.automation_preview_thread_id = ""
        self.automation_preview_started_at = 0.0
        self.automation_preview_pending_text = ""
        self.automation_preview_last_rendered_text = ""
        self.automation_preview_last_chars = 0
        self.automation_preview_dots = 0
        self.automation_preview_render_timer = QTimer(self)
        self.automation_preview_render_timer.setSingleShot(True)
        self.automation_preview_render_timer.timeout.connect(self.flush_automation_preview_render)
        self.automation_preview_dots_timer = QTimer(self)
        self.automation_preview_dots_timer.setInterval(520)
        self.automation_preview_dots_timer.timeout.connect(self.tick_automation_preview_status)
        self.history_save_timer = QTimer(self)
        self.history_save_timer.setSingleShot(True)
        self.history_save_timer.timeout.connect(self.flush_history_save)
        self.history_save_worker: Optional[HistorySaveWorker] = None
        self.history_save_workers: List[HistorySaveWorker] = []
        self.history_save_dirty = False
        self.history_save_generation = 0
        self.pending_provider_io: Optional[Dict[str, object]] = None
        self.ui_heartbeat_last = time.perf_counter()
        self.ui_heartbeat_timer = QTimer(self)
        self.ui_heartbeat_timer.setInterval(100)
        self.ui_heartbeat_timer.timeout.connect(self.check_ui_heartbeat)
        self.ui_heartbeat_timer.start()
        self.automation_setup_worker: Optional[AutomationSetupWorker] = None
        self.python_runtime_setup_worker: Optional[PythonRuntimeSetupWorker] = None
        self.python_runtime_install_proc: Optional[ManagedProcess] = None
        self.automation_loop_active = False
        self.automation_loop_round = 0
        self.automation_loop_max_rounds = AUTOMATION_LOOP_MAX_ROUNDS
        self.automation_loop_goal = ""
        self.automation_loop_force_final_summary = False
        self.automation_retry_context = ""
        self.automation_retry_attempts = 0
        self.active_schedule_id = ""
        self.active_schedule_notify: Dict[str, str] = {}
        self.active_schedule_started_at = 0.0
        self.active_schedule_run_key = ""
        self.schedule_timer = QTimer(self)
        self.schedule_timer.setInterval(30000)
        self.schedule_timer.timeout.connect(self.check_due_schedules)
        self.schedule_timer.start()
        self._ensure_ai_entry_pending = False
        self._last_status_message = ""
        self._last_status_at = 0.0
        self._status_bar_override_text = ""
        self._status_bar_override_until = 0.0
        self.chat_scroll_user_controlled = False
        self.chat_scroll_programmatic = False
        self.chat_scroll_bottom_tolerance = 12
        self.automation_composer: Optional[QFrame] = None
        self.automation_composer_input_column: Optional[QWidget] = None
        self.automation_input: Optional[QTextEdit] = None
        self.automation_send_btn: Optional[QToolButton] = None
        self.automation_context_mode_btn: Optional[QToolButton] = None
        self.automation_skill_btn: Optional[QToolButton] = None
        self.skill_generation_worker: Optional[AutomationChatWorker] = None
        self.last_automation_provider_compaction = "none"
        self.last_automation_history_compacted = False
        self._last_context_compaction_notice_at = 0.0
        self._shutdown_done = False
        self.preferences_dialog: Optional[QDialog] = None
        self.chat_column_max_width = 1480
        self.chat_column_width_ratio = 0.94
        self.user_bubble_width_ratio = 0.75
        self.setup_ui()
        if wechat_bridge_enabled_setting():
            QTimer.singleShot(0, self.start_wechat_bridge_quietly)
            if wechat_connector_autostart_setting() and self.wechat_connector.account():
                QTimer.singleShot(600, self.start_wechat_connector_from_menu)
        else:
            QTimer.singleShot(0, self.start_console_bridge_quietly)

    def check_ui_heartbeat(self):
        now = time.perf_counter()
        delta_ms = int((now - self.ui_heartbeat_last) * 1000)
        self.ui_heartbeat_last = now
        if delta_ms >= 280:
            logger.warning(
                "UI heartbeat lag delta_ms=%d automation_busy=%s request_running=%s preview_active=%s pending_preview_chars=%d",
                delta_ms,
                self.is_automation_busy(),
                self.is_automation_request_running(),
                self.automation_preview_bubble is not None,
                len(self.automation_preview_pending_text or ""),
            )
    
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
        self.sidebar.rename_thread_requested.connect(self.rename_thread)
        self.sidebar.new_skill_requested.connect(self.show_new_skill_dialog)
        self.sidebar.skill_selected.connect(self.open_skill_file)
        self.sidebar.delete_skill_requested.connect(self.delete_skill)
        self.sidebar.delete_path_requested.connect(self.delete_project_path)
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
                padding-top: 3px;
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
        self.sidebar_resize_handle.drag_started.connect(self.begin_sidebar_resize_feedback)
        self.sidebar_resize_handle.drag_finished.connect(self.end_sidebar_resize_feedback)
        self.sidebar_resize_handle.install_toggle_button(self.sidebar_btn)
        sw_layout.addWidget(self.sidebar)
        sw_layout.addWidget(self.sidebar_resize_handle)
        body.addWidget(sidebar_wrapper, 0)
        
        right_panel = QWidget(styleSheet=f"background: {COLORS['bg']};")
        self.right_panel = right_panel
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 16, 18, 0)
        right_layout.setSpacing(12)
        
        # 路径标签（双击返回首页）
        path_bar = QHBoxLayout()
        path_title = QLabel("工作区")
        self.path_title = path_title
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

        self.copy_prompt_btn = QPushButton("复制系统提示词", clicked=self.on_primary_action_button, cursor=Qt.PointingHandCursor)
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
                border-color: {soft_accent_border_color()};
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
                                           QScrollArea QWidget#qt_scrollarea_viewport {{
                                               background: {COLORS['surface']};
                                               border-radius: 18px;
                                           }}
                                           QScrollArea > QWidget > QWidget {{
                                               background: {COLORS['surface']};
                                               border-radius: 18px;
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
        self.chat_container.setStyleSheet(f"background: {COLORS['surface']};")
        self.chat_layout = QVBoxLayout(self.chat_container)
        self.chat_root_layout = self.chat_layout
        self.chat_root_layout.setAlignment(Qt.AlignTop)
        self.chat_root_layout.setSpacing(0)
        self.chat_layout.setContentsMargins(0, 0, 0, 0)
        self.chat_column = QWidget()
        self.chat_column.setStyleSheet(f"background: {COLORS['surface']};")
        self.chat_column.setMaximumWidth(self.chat_column_max_width)
        self.chat_column.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Maximum)
        self.chat_column_layout = QVBoxLayout(self.chat_column)
        self.chat_column_layout.setAlignment(Qt.AlignTop)
        self.chat_column_layout.setSpacing(10)
        self.chat_column_layout.setContentsMargins(14, 14, 14, 14)
        self.chat_root_layout.addWidget(self.chat_column, 0, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self.chat_layout = self.chat_column_layout
        self.scroll_area.setWidget(self.chat_container)
        self.scroll_area.viewport().setStyleSheet(f"background: {COLORS['surface']}; border-radius: 18px;")
        self.scroll_area.viewport().installEventFilter(self)
        self.scroll_area.verticalScrollBar().valueChanged.connect(self.on_chat_scroll_changed)
        self.sidebar_resize_overlay = QLabel("正在调整侧栏宽度，释放后恢复内容显示", self.scroll_area.viewport())
        self.sidebar_resize_overlay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.sidebar_resize_overlay.setVisible(False)
        self.sidebar_resize_overlay.setStyleSheet(f"""
            QLabel {{
                background: {COLORS['surface']};
                color: {COLORS['text_secondary']};
                border: 1px dashed {COLORS['border']};
                border-radius: 18px;
                font-size: 13px;
                font-weight: 800;
                padding: 20px;
            }}
        """)
        right_layout.addWidget(self.scroll_area, 1)

        self.automation_composer = QFrame()
        self.automation_composer.setObjectName("automationComposer")
        self.automation_composer.setVisible(False)
        self.automation_composer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.automation_composer.setStyleSheet(f"""
            QFrame#automationComposer {{
                background: {COLORS['surface']};
                border: 1px solid {COLORS['border']};
                border-radius: 18px;
            }}
        """)
        composer_layout = QHBoxLayout(self.automation_composer)
        composer_layout.setContentsMargins(10, 8, 8, 8)
        composer_layout.setSpacing(8)
        composer_input_column = QWidget()
        self.automation_composer_input_column = composer_input_column
        composer_input_column.setObjectName("automationComposerInputColumn")
        composer_input_column.setStyleSheet("""
            QWidget#automationComposerInputColumn {
                background: transparent;
                border-radius: 14px;
            }
        """)
        composer_input_layout = QVBoxLayout(composer_input_column)
        composer_input_layout.setContentsMargins(0, 0, 0, 0)
        composer_input_layout.setSpacing(2)
        self.automation_input = QTextEdit()
        self.automation_input.setPlaceholderText(
            f"上下文 0k / {context_k_label(AUTOMATION_CONTEXT_DISPLAY_TOKENS)} · 输入下一步需求..."
        )
        self.automation_input.setFixedHeight(42)
        self.automation_input.setAcceptRichText(False)
        self.automation_input.textChanged.connect(self.on_automation_input_text_changed)
        self.automation_input.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.automation_input.customContextMenuRequested.connect(
            lambda pos, editor=self.automation_input: show_chinese_edit_menu(editor, editor.mapToGlobal(pos))
        )
        self.automation_input.setStyleSheet(self.automation_input_style())
        self.automation_input.viewport().setStyleSheet("background: transparent; border-radius: 14px;")
        composer_input_layout.addWidget(self.automation_input, 1)
        mode_row = QHBoxLayout()
        mode_row.setContentsMargins(0, 0, 0, 0)
        mode_row.setSpacing(0)
        self.automation_context_mode_btn = QToolButton(cursor=Qt.CursorShape.PointingHandCursor)
        self.automation_context_mode_btn.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.automation_context_mode_btn.setPopupMode(QToolButton.InstantPopup)
        self.automation_context_mode_btn.setFixedHeight(22)
        self.update_automation_context_mode_button()
        mode_row.addWidget(self.automation_context_mode_btn, 0, Qt.AlignmentFlag.AlignLeft)
        self.automation_skill_btn = QToolButton(cursor=Qt.CursorShape.PointingHandCursor)
        self.automation_skill_btn.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.automation_skill_btn.setPopupMode(QToolButton.InstantPopup)
        self.automation_skill_btn.setFixedHeight(22)
        self.update_automation_skill_button()
        mode_row.addSpacing(2)
        mode_row.addWidget(self.automation_skill_btn, 0, Qt.AlignmentFlag.AlignLeft)
        mode_row.addStretch(1)
        composer_input_layout.addLayout(mode_row)
        composer_layout.addWidget(composer_input_column, 1)
        self.automation_send_btn = QToolButton(cursor=Qt.CursorShape.PointingHandCursor)
        self.automation_send_btn.setFixedSize(42, 42)
        self.automation_send_btn.setIcon(line_icon("send", "white", 20))
        self.automation_send_btn.setIconSize(QSize(20, 20))
        self.automation_send_btn.clicked.connect(self.on_automation_composer_action)
        self.automation_send_btn.setStyleSheet(f"""
            QToolButton {{
                background: {COLORS['accent']};
                border: none;
                border-radius: 21px;
            }}
            QToolButton:hover {{
                background: {COLORS['accent_dark']};
            }}
        """)
        composer_layout.addWidget(self.automation_send_btn, 0, Qt.AlignmentFlag.AlignBottom)
        right_layout.addWidget(self.automation_composer, 0)

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
        self.terminal_panel.process_finished_signal.connect(self.on_terminal_process_finished)
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
        self.update_prompt_tools_responsive()
    
    def set_project(self, path: str):
        self.flush_history_save(wait=True)
        self.stop_automation_preview(remove_bubble=True)
        self.chat_scroll_user_controlled = False
        self.chat_scroll_programmatic = False
        self.terminal_panel.close_all_processes()
        self.terminal_panel.collapse()
        self.project_root = path
        self.change_tracker = InternalGitChangeTracker(path)
        self.threads = load_workspace_threads(path)
        save_workspace_threads(path, self.threads)
        self.skills = load_workspace_skills(path)
        self.selected_skill_ids = {skill_id for skill_id in self.selected_skill_ids if any(skill.get("id") == skill_id for skill in self.skills)}
        self.thread_id = load_last_thread_id(path, self.threads)
        self.path_label.setText(f"📁 {path}")
        self.terminal_panel.set_project_root(path)
        self.sidebar.refresh_tree(path)
        self.sidebar.set_threads(self.threads, self.thread_id)
        self.sidebar.set_skills(self.skills)
        self.sidebar.set_tab("threads")
        self.expand_sidebar()
        self.load_history()
        if self.automation_enabled:
            self.run_automation_setup("start")
        self.update_prompt_tools_responsive()
        self.update_automation_skill_button()
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
        self.update_chat_column_width()
        self.update_prompt_tools_responsive()

    def eventFilter(self, watched, event):
        try:
            if hasattr(self, "scroll_area") and watched is self.scroll_area.viewport() and event.type() == QEvent.Type.Resize:
                self.update_chat_column_width()
                self.update_sidebar_resize_overlay_geometry()
        except RuntimeError:
            return False
        return super().eventFilter(watched, event)

    def update_chat_column_width(self):
        if not hasattr(self, "chat_column") or not hasattr(self, "scroll_area"):
            return
        viewport_width = self.scroll_area.viewport().width()
        if viewport_width <= 0:
            return
        desired = int(viewport_width * self.chat_column_width_ratio)
        desired = max(620, min(self.chat_column_max_width, desired))
        if viewport_width < desired:
            desired = max(320, viewport_width - 2)
        if self.chat_column.width() != desired:
            self.chat_column.setFixedWidth(desired)
        self.update_user_bubble_widths()

    def user_bubble_width(self) -> int:
        if not hasattr(self, "chat_column"):
            return 560
        margins = self.chat_column_layout.contentsMargins() if hasattr(self, "chat_column_layout") else None
        horizontal_margin = (margins.left() + margins.right()) if margins is not None else 0
        available = max(320, self.chat_column.width() - horizontal_margin)
        return max(320, int(available * self.user_bubble_width_ratio))

    def prepare_chat_widget(self, widget: QWidget):
        if isinstance(widget, ChatBubble) and getattr(widget, "role", "") == "user":
            widget.setFixedWidth(self.user_bubble_width())
            widget.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def add_history_trim_notice(self, hidden_count: int):
        if hidden_count <= 0:
            return
        notice = QLabel(f"已折叠较早 {hidden_count} 条历史。完整历史仍会进入自动化上下文。")
        notice.setAlignment(Qt.AlignmentFlag.AlignCenter)
        notice.setStyleSheet(f"""
            QLabel {{
                background: {COLORS['surface_alt']};
                color: {COLORS['text_secondary']};
                border: 1px solid {COLORS['border']};
                border-radius: 10px;
                padding: 10px 12px;
                font-size: 12px;
                font-weight: 800;
            }}
        """)
        notice.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.add_chat_widget(notice)

    def add_context_compaction_notice(self):
        now = time.time()
        if now - self._last_context_compaction_notice_at < 2:
            return
        self._last_context_compaction_notice_at = now
        notice = QLabel("——————  自动压缩上下文  ———————")
        notice.setAlignment(Qt.AlignmentFlag.AlignCenter)
        notice.setStyleSheet(f"""
            QLabel {{
                background: transparent;
                color: {COLORS['text_secondary']};
                border: none;
                padding: 8px 12px;
                font-size: {scaled_font_px(12)}px;
                font-weight: 800;
            }}
        """)
        notice.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.add_chat_widget(notice)

    def add_chat_widget(self, widget: QWidget, *, animate: bool = False):
        self.prepare_chat_widget(widget)
        if isinstance(widget, ChatBubble) and getattr(widget, "role", "") == "user":
            self.chat_layout.addWidget(widget, 0, Qt.AlignmentFlag.AlignRight)
        else:
            self.chat_layout.addWidget(widget)
        if animate:
            animate_widget_in(widget)

    def insert_chat_widget(self, index: int, widget: QWidget, *, animate: bool = False):
        self.prepare_chat_widget(widget)
        alignment = Qt.AlignmentFlag.AlignRight if isinstance(widget, ChatBubble) and getattr(widget, "role", "") == "user" else Qt.Alignment()
        self.chat_layout.insertWidget(index, widget, 0, alignment)
        if animate:
            animate_widget_in(widget)

    def update_user_bubble_widths(self):
        if not hasattr(self, "chat_layout"):
            return
        width = self.user_bubble_width()
        for idx in range(self.chat_layout.count()):
            widget = self.chat_layout.itemAt(idx).widget()
            if isinstance(widget, ChatBubble) and getattr(widget, "role", "") == "user":
                widget.setFixedWidth(width)

    def update_prompt_tools_responsive(self):
        if not hasattr(self, "copy_prompt_btn"):
            return
        width = self.width()
        narrow = width < 1180
        very_narrow = width < 980
        self.path_label.setMaximumWidth(180 if very_narrow else (260 if narrow else 420))
        self.copy_prompt_btn.setText("分享" if self.automation_enabled else ("复制提示词" if very_narrow else "复制系统提示词"))
        self.top_clear_history_btn.setText("清空" if narrow else "清空记录")
        self.copy_prompt_btn.setFixedWidth(78 if self.automation_enabled else (104 if very_narrow else (118 if narrow else 136)))
        self.top_clear_history_btn.setFixedWidth(58 if very_narrow else (68 if narrow else 86))
    
    def toggle_sidebar(self):
        self.sidebar.toggle()
        self.sidebar_btn.setText("‹" if not self.sidebar._collapsed else "›")
        self.sidebar_resize_handle.set_grip_visible(not self.sidebar._collapsed)
        self.update_sidebar_wrapper_width()

    def expand_sidebar(self):
        self.sidebar.expand()
        self.sidebar_btn.setText("‹")
        self.sidebar_resize_handle.set_grip_visible(True)
        self.update_sidebar_wrapper_width()

    def resize_sidebar(self, width: int):
        self.sidebar.set_expanded_width(width)
        self.update_sidebar_wrapper_width()

    def update_sidebar_wrapper_width(self):
        handle_width = self.sidebar_resize_handle.width()
        self.sidebar_wrapper.setFixedWidth(self.sidebar.current_width() + handle_width)

    def update_sidebar_resize_overlay_geometry(self):
        if not hasattr(self, "scroll_area") or not hasattr(self, "sidebar_resize_overlay"):
            return
        viewport = self.scroll_area.viewport()
        self.sidebar_resize_overlay.setGeometry(viewport.rect())

    def begin_sidebar_resize_feedback(self):
        if hasattr(self, "chat_column"):
            self.chat_column.setVisible(False)
        self.update_sidebar_resize_overlay_geometry()
        if hasattr(self, "sidebar_resize_overlay"):
            self.sidebar_resize_overlay.setVisible(True)
            self.sidebar_resize_overlay.raise_()

    def end_sidebar_resize_feedback(self):
        if hasattr(self, "sidebar_resize_overlay"):
            self.sidebar_resize_overlay.setVisible(False)
        if hasattr(self, "chat_column"):
            self.chat_column.setVisible(True)
            self.chat_column.adjustSize()
        QTimer.singleShot(0, self.update_chat_column_width)
    
    def scroll_to_bottom(self):
        if not self.should_auto_follow_chat_scroll():
            return
        self.scroll_to_bottom_now()
        for delay in (30, 90, 180):
            QTimer.singleShot(delay, self.scroll_to_bottom_if_auto_follow)

    def scroll_to_bottom_now(self):
        bar = self.scroll_area.verticalScrollBar()
        self.chat_scroll_programmatic = True
        try:
            bar.setValue(bar.maximum())
        finally:
            QTimer.singleShot(0, self.clear_programmatic_chat_scroll)

    def clear_programmatic_chat_scroll(self):
        self.chat_scroll_programmatic = False

    def scroll_to_bottom_if_auto_follow(self):
        if self.should_auto_follow_chat_scroll():
            self.scroll_to_bottom_now()

    def is_chat_at_bottom(self) -> bool:
        bar = self.scroll_area.verticalScrollBar()
        return bar.value() >= bar.maximum() - self.chat_scroll_bottom_tolerance

    def should_auto_follow_chat_scroll(self) -> bool:
        return not self.chat_scroll_user_controlled or self.is_chat_at_bottom()

    def capture_chat_scroll_state(self) -> Dict[str, object]:
        bar = self.scroll_area.verticalScrollBar()
        return {
            "value": bar.value(),
            "maximum": bar.maximum(),
            "bottom_gap": max(0, bar.maximum() - bar.value()),
            "at_bottom": self.is_chat_at_bottom(),
            "user_controlled": self.chat_scroll_user_controlled,
        }

    def restore_chat_scroll_state(self, state: Dict[str, object]):
        if bool(state.get("user_controlled")):
            return
        if bool(state.get("at_bottom")):
            if self.should_auto_follow_chat_scroll():
                self.scroll_to_bottom_now()
            return

    def freeze_user_chat_scroll_value(self, state: Dict[str, object]):
        # Respect manual scrolling completely. Trying to "freeze" the outer
        # scrollbar during code-block height changes causes visible tugging.
        return

    def stabilize_chat_scroll_after_update(self, state: Dict[str, object]):
        if bool(state.get("user_controlled")):
            self.freeze_user_chat_scroll_value(state)
            return
        self.restore_chat_scroll_state(state)
        for delay in (0, 30, 90, 180, 300):
            QTimer.singleShot(delay, lambda state=state: self.restore_chat_scroll_state(state))

    def is_execution_running(self) -> bool:
        return bool(self.worker and self.worker.isRunning())

    def handle_wechat_bridge_request(self, payload: dict):
        request_id = str((payload or {}).get("request_id") or "")
        action = str((payload or {}).get("action") or "").strip().lower()
        try:
            if action in {"state", "status"}:
                self.wechat_bridge.finish_request(request_id, {
                    "ok": True,
                    "project_root": self.project_root,
                    "thread_id": self.thread_id,
                    "busy": self.is_automation_busy() or self.is_execution_running(),
                    "automation_enabled": self.automation_enabled,
                    "wechat_bridge": self.wechat_bridge.url(),
                    "wechat_active_request_id": self.wechat_active_request_id,
                    "wechat_active_to_user": self.wechat_active_to_user,
                    "wechat_active_context_token": self.wechat_active_context_token,
                    "wechat_interrupt_confirm_to_user": self.wechat_interrupt_confirm_to_user,
                    "wechat_interrupt_confirm_context_token": self.wechat_interrupt_confirm_context_token,
                })
                return
            if action in {"provider"}:
                self.wechat_bridge.finish_request(request_id, {"ok": True, **self.provider_info_payload()})
                return
            if action in {"model", "models"}:
                target = str((payload or {}).get("target") or "").strip()
                if target:
                    preset = resolve_automation_preset_from_text(target)
                    if not preset:
                        reply = automation_model_options_text()
                    else:
                        self.set_automation_preset(str(preset.get("mode") or "expert"), str(preset.get("model") or AUTOMATION_DEFAULT_MODEL))
                        reply = f"已切换模型：{automation_context_mode_label_for_state(self.automation_context_mode, self.automation_model)}"
                else:
                    reply = automation_model_options_text()
                self.wechat_bridge.finish_request(request_id, {"ok": True, "reply": reply, "text": reply, "model": self.automation_model})
                return
            if action in {"terminals", "terminal_list"}:
                grep_text = str((payload or {}).get("grep") or (payload or {}).get("q") or "").strip()
                try:
                    pid = int((payload or {}).get("pid") or 0)
                except (TypeError, ValueError):
                    pid = 0
                self.wechat_bridge.finish_request(request_id, {
                    "ok": True,
                    "pid": pid,
                    "grep": grep_text,
                    "terminals": self.terminal_panel.terminal_console_entries(grep_text, pid=pid),
                })
                return
            if action in {"terminals_text", "terminal_text"}:
                grep_text = str((payload or {}).get("grep") or (payload or {}).get("q") or "").strip()
                try:
                    pid = int((payload or {}).get("pid") or 0)
                except (TypeError, ValueError):
                    pid = 0
                self.wechat_bridge.finish_request(request_id, {
                    "ok": True,
                    "pid": pid,
                    "grep": grep_text,
                    "text": self.terminal_panel.terminal_console_text(grep_text, pid=pid),
                })
                return
            if action in {"threads", "conversations"}:
                self.wechat_bridge.finish_request(request_id, {
                    "ok": True,
                    "active_thread_id": self.thread_id,
                    "threads": normalize_threads(self.threads),
                })
                return
            if action == "select_thread":
                if not self.project_root:
                    raise RuntimeError("尚未打开工作区。")
                requested = str((payload or {}).get("thread_id") or (payload or {}).get("id") or "").strip()
                thread_id = safe_thread_id(requested)
                if not thread_id:
                    raise RuntimeError("缺少 thread_id。")
                if self.is_automation_busy() or self.is_execution_running():
                    raise RuntimeError("当前还有任务在运行，无法切换会话。")
                threads = normalize_threads(self.threads)
                if not any(str(thread.get("id")) == thread_id for thread in threads):
                    title_match = next((thread for thread in threads if str(thread.get("title") or "") == requested), None)
                    if title_match:
                        thread_id = str(title_match.get("id") or thread_id)
                if not any(str(thread.get("id")) == thread_id for thread in threads):
                    raise RuntimeError(f"会话不存在：{thread_id}")
                self.switch_thread(thread_id)
                self.wechat_bridge.finish_request(request_id, {"ok": True, "active_thread_id": self.thread_id})
                return
            if action == "new_thread":
                if not self.project_root:
                    raise RuntimeError("尚未打开工作区。")
                if self.is_automation_busy() or self.is_execution_running():
                    raise RuntimeError("当前还有任务在运行，无法新建会话。")
                title = str((payload or {}).get("title") or (payload or {}).get("name") or "微信会话").strip() or "微信会话"
                self.threads = load_workspace_threads(self.project_root)
                thread = create_workspace_thread(self.project_root, self.threads)
                thread["title"] = title
                rename_workspace_thread(self.project_root, str(thread.get("id") or ""), title, load_workspace_threads(self.project_root))
                self.threads = load_workspace_threads(self.project_root)
                self.thread_id = str(thread.get("id", DEFAULT_THREAD_ID))
                save_last_thread_id(self.project_root, self.thread_id)
                self.sidebar.set_threads(self.threads, self.thread_id)
                self.load_history()
                self.wechat_bridge.finish_request(request_id, {"ok": True, "thread": thread, "active_thread_id": self.thread_id})
                return
            if action == "stop":
                if self.is_automation_busy() or self.is_execution_running():
                    self.cancel_automation_request()
                    if self.is_execution_running() and self.worker is not None:
                        self.worker.requestInterruption()
                    self.wechat_bridge.finish_request(request_id, {"ok": True, "reply": "已发送停止指令。"})
                else:
                    self.wechat_bridge.finish_request(request_id, {"ok": True, "reply": "当前没有正在运行的任务。"})
                return
            if action == "project_tree":
                if not self.project_root:
                    raise RuntimeError("尚未打开工作区。")
                tree_text = project_tree_text(self.project_root)
                self.wechat_bridge.finish_request(request_id, {"ok": True, "reply": tree_text, "text": tree_text})
                return
            if action in {"schedules", "schedule_list"}:
                if not self.project_root:
                    raise RuntimeError("尚未打开工作区。")
                reply = schedules_summary_text(load_workspace_schedules(self.project_root))
                self.wechat_bridge.finish_request(request_id, {"ok": True, "reply": reply, "text": reply})
                return
            if action == "delete_schedule":
                if not self.project_root:
                    raise RuntimeError("尚未打开工作区。")
                target = str((payload or {}).get("target") or (payload or {}).get("id") or "").strip()
                if not target:
                    raise RuntimeError("缺少要删除的计划名称或序号。")
                schedules = load_workspace_schedules(self.project_root)
                resolved = resolve_schedule_target(schedules, target)
                if not resolved:
                    raise RuntimeError(f"没有找到计划：{target}")
                deleted_title = next((str(item.get("title") or resolved) for item in schedules if str(item.get("id") or "") == resolved), resolved)
                if not delete_workspace_schedule(self.project_root, resolved):
                    raise RuntimeError(f"删除计划失败：{target}")
                reply = f"已删除定时计划：{deleted_title}"
                self.wechat_bridge.finish_request(request_id, {"ok": True, "reply": reply, "text": reply})
                return
            if action == "send_file":
                if not self.project_root:
                    raise RuntimeError("尚未打开工作区。")
                target = str((payload or {}).get("target") or (payload or {}).get("path") or "").strip()
                to_user = str((payload or {}).get("to_user") or (payload or {}).get("user") or (payload or {}).get("thread_id") or "").strip()
                context_token = str((payload or {}).get("context_token") or "")
                if not target:
                    raise RuntimeError("缺少要发送的文件路径或文件名。")
                path = resolve_project_file_target(self.project_root, target)
                if not path:
                    raise RuntimeError(f"没有找到文件：{target}")
                if not to_user or not context_token:
                    raise RuntimeError("当前通道缺少微信回复上下文，无法直接发送附件。")
                self.wechat_connector._send_file(to_user, path, context_token)
                reply = f"已发送文件：{os.path.relpath(path, self.project_root)}"
                self.wechat_bridge.finish_request(request_id, {"ok": True, "reply": reply, "text": reply, "path": path})
                return
            if action == "create_schedule":
                if not self.project_root:
                    raise RuntimeError("尚未打开工作区。")
                hour = int((payload or {}).get("hour"))
                minute = int((payload or {}).get("minute") or 0)
                prompt = str((payload or {}).get("prompt") or "").strip()
                title = str((payload or {}).get("title") or prompt[:36] or "每日计划").strip()
                if not prompt:
                    raise RuntimeError("缺少计划内容。")
                self.create_schedule_plain(
                    title=title,
                    user_request=prompt,
                    hour=hour,
                    minute=minute,
                    request_id=request_id,
                    notify_wechat_user=str((payload or {}).get("to_user") or (payload or {}).get("user") or "").strip(),
                    notify_wechat_context_token=str((payload or {}).get("context_token") or "").strip(),
                    notify_wechat_thread_id=self.thread_id,
                )
                return
            if action == "message":
                self.start_wechat_message_request(request_id, payload or {})
                return
            raise RuntimeError(f"未知 action：{action or '(empty)'}")
        except Exception as exc:
            self.wechat_bridge.finish_request(request_id, {"ok": False, "error": str(exc)})

    def start_wechat_message_request(self, request_id: str, payload: dict):
        text = str(
            payload.get("text")
            or payload.get("content")
            or payload.get("message")
            or payload.get("query")
            or ""
        ).strip()
        if not text:
            raise RuntimeError("缺少消息内容。")
        if not self.project_root:
            raise RuntimeError("尚未打开工作区。")
        command = text.strip()
        if is_wechat_menu_command(command):
            self.wechat_bridge.finish_request(request_id, {
                "ok": True,
                "reply": WECHAT_COMMAND_MENU_TEXT,
                "text": WECHAT_COMMAND_MENU_TEXT,
                "thread_id": self.thread_id,
            })
            return
        builtin = parse_wechat_builtin_command(command)
        to_user = str(payload.get("to_user") or payload.get("user") or payload.get("thread_id") or "").strip()
        context_token = str(payload.get("context_token") or "").strip()
        if builtin:
            payload_for_command = {
                "request_id": request_id,
                "to_user": to_user,
                "context_token": context_token,
                **builtin,
            }
            self.handle_wechat_bridge_request(payload_for_command)
            return
        if self.is_automation_busy() or self.is_execution_running():
            same_active_wechat = bool(
                self.wechat_active_request_id
                and to_user
                and to_user == self.wechat_active_to_user
            )
            if same_active_wechat:
                compact = re.sub(r"\s+", "", command).strip().lower()
                pending_same_target = bool(
                    to_user
                    and to_user == self.wechat_interrupt_confirm_to_user
                )
                if pending_same_target and compact in {"y", "yes", "是", "好", "结束", "停止", "确认"}:
                    self.wechat_interrupt_confirm_to_user = ""
                    self.wechat_interrupt_confirm_context_token = ""
                    self.cancel_automation_request()
                    if self.is_execution_running() and self.worker is not None:
                        self.worker.requestInterruption()
                    reply = "已发送停止指令。当前微信会话生成结束后，请重新发送你的新需求。"
                elif pending_same_target and compact in {"n", "no", "否", "不用", "继续"}:
                    self.wechat_interrupt_confirm_to_user = ""
                    self.wechat_interrupt_confirm_context_token = ""
                    reply = "好的，继续当前微信会话生成，请稍后。"
                else:
                    self.wechat_interrupt_confirm_to_user = to_user
                    self.wechat_interrupt_confirm_context_token = context_token
                    reply = "当前微信对话还在生成中，是否要立刻结束？请回复 y/n。"
            else:
                reply = "请稍后再发，目前有其他请求正在生成中。"
            self.wechat_bridge.finish_request(request_id, {
                "ok": True,
                "busy": True,
                "reply": reply,
                "text": reply,
                "thread_id": self.thread_id,
            })
            return
        requested_thread_id = str(payload.get("thread_id") or payload.get("conversation_id") or "").strip()
        if requested_thread_id and safe_thread_id(requested_thread_id) != self.thread_id:
            target_thread_id = safe_thread_id(requested_thread_id)
            if not any(str(thread.get("id")) == target_thread_id for thread in normalize_threads(self.threads)):
                self.threads = load_workspace_threads(self.project_root)
                thread = ensure_workspace_thread(self.project_root, self.threads, target_thread_id, "微信会话")
                self.threads = load_workspace_threads(self.project_root)
                self.sidebar.set_threads(self.threads, self.thread_id)
            self.switch_thread(target_thread_id)
        if not self.automation_enabled:
            dep = self.automation_manager.dependency_status()
            if not dep.get("ready"):
                raise RuntimeError("自动化插件依赖未就绪，请先在设置里安装/修复插件依赖。")
            self.automation_enabled = True
            set_automation_enabled_setting(True)
            self.load_history()
            self.update_prompt_tools_responsive()
            self.show_automation_composer(focus=False)
        settings = wechat_bridge_settings()
        silent = parse_boolish(payload.get("silent"), bool(settings.get("silent", True)))
        remember_wechat_reply_target(to_user, context_token)
        allow_file_delivery = bool(wechat_bridge_enabled_setting() and to_user and context_token)
        self.wechat_interrupt_confirm_to_user = ""
        self.wechat_interrupt_confirm_context_token = ""
        self.wechat_active_request_id = request_id
        self.wechat_active_start_index = len(self.history_entries)
        self.wechat_active_silent = silent
        self.wechat_active_to_user = to_user if allow_file_delivery else ""
        self.wechat_active_context_token = context_token if allow_file_delivery else ""
        self.wechat_active_sent_files = set()
        prompt_text = build_wechat_user_prompt(text, allow_file_delivery=allow_file_delivery)
        full_prompt = self.build_system_prompt(prompt_text)
        clean_context = f"【微信用户需求】\n{text.strip()}"
        prompt_entry_id = self.add_automation_user_prompt_bubble(
            full_prompt,
            animate=True,
            display_text=clean_context,
            context_content=clean_context,
        )
        self.begin_automation_loop(text)
        self.start_automation_worker(prompt_text, "", None, None, prompt_entry_id)
        if self.is_automation_request_running() and self.selected_skill_ids:
            self.clear_automation_skills()

    def finish_wechat_active_request(self, fallback_message: str = ""):
        request_id = self.wechat_active_request_id
        if not request_id:
            return
        start = max(0, int(self.wechat_active_start_index or 0))
        entries = self.history_entries[start:]
        reply = wechat_history_reply(entries, silent=self.wechat_active_silent)
        if fallback_message and (not entries or reply == "已处理。"):
            reply = fallback_message
        silent = self.wechat_active_silent
        to_user = self.wechat_active_to_user
        context_token = self.wechat_active_context_token
        already_sent_files = set(self.wechat_active_sent_files)
        requested_files: List[str] = []
        schedule_payloads: List[Dict[str, object]] = []
        schedule_actions: List[Dict[str, object]] = []
        schedule_errors: List[str] = []
        history_context_changed = False
        allow_file_delivery = bool(wechat_bridge_enabled_setting() and to_user and context_token)
        if allow_file_delivery:
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                entry_texts = [
                    str(entry.get("content") or ""),
                    str(entry.get("context_content") or ""),
                ]
                seen_entry_texts: set[str] = set()
                unique_entry_texts: List[str] = []
                for entry_text in entry_texts:
                    if not entry_text or entry_text in seen_entry_texts:
                        continue
                    seen_entry_texts.add(entry_text)
                    unique_entry_texts.append(entry_text)
                    requested_files.extend(extract_wechat_send_file_targets(entry_text))
                    payloads, errors = extract_wechat_schedule_trigger_payloads(entry_text)
                    schedule_payloads.extend(payloads)
                    schedule_errors.extend(errors)
                    actions, action_errors = extract_wechat_schedule_action_payloads(entry_text)
                    schedule_actions.extend(actions)
                    schedule_errors.extend(action_errors)
                entry_text = "\n".join(unique_entry_texts)
                if (
                    "AGENT_WECHAT_CREATE_SCHEDULE" in entry_text
                    or "AGENT_WECHAT_SCHEDULE_ACTION" in entry_text
                    or "AGENT_WECHAT_SEND_FILE" in entry_text
                ):
                    clean_entry_text = strip_wechat_send_file_markers(entry_text).strip()
                    if clean_entry_text:
                        entry["context_content"] = clean_entry_text
                    else:
                        entry["exclude_from_context"] = True
                    history_context_changed = True
            reply = strip_wechat_send_file_markers(reply)
        if history_context_changed:
            self.save_history()
        self.wechat_active_request_id = ""
        self.wechat_active_start_index = 0
        self.wechat_active_silent = True
        self.wechat_active_to_user = ""
        self.wechat_active_context_token = ""
        self.wechat_active_sent_files = set()
        sent_files: List[str] = []
        failed_files: List[str] = []
        created_schedules: List[str] = []
        schedule_action_replies: List[str] = []
        if self.project_root:
            notify_user = to_user if allow_file_delivery else ""
            notify_context_token = context_token if allow_file_delivery else ""
            created, action_replies, errors = apply_schedule_extension_payloads(
                self.project_root,
                schedule_payloads,
                schedule_actions,
                notify_user=notify_user,
                notify_context_token=notify_context_token,
                notify_thread_id=self.thread_id if allow_file_delivery else "",
            )
            created_schedules.extend(created)
            schedule_action_replies.extend(action_replies)
            schedule_errors.extend(errors)
            if created or action_replies:
                QTimer.singleShot(1200, self.check_due_schedules)
        if allow_file_delivery and self.project_root:
            seen_finish_targets: set[str] = set()
            for target in requested_files[:3]:
                path = resolve_project_file_target(self.project_root, target)
                if not path:
                    failed_files.append(target)
                    continue
                canonical = os.path.normcase(os.path.abspath(path))
                if canonical in already_sent_files or canonical in seen_finish_targets:
                    continue
                seen_finish_targets.add(canonical)
                try:
                    self.wechat_connector._send_file(to_user, path, context_token)
                    sent_files.append(os.path.relpath(path, self.project_root))
                except Exception as exc:
                    failed_files.append(f"{target}（{exc}）")
        if created_schedules:
            reply = (reply + "\n\n已创建计划：" + "、".join(created_schedules)).strip()
        if schedule_action_replies:
            reply = (reply + "\n\n" + "\n\n".join(schedule_action_replies)).strip()
        if schedule_errors:
            reply = (reply + "\n\n有计划操作未完成：" + "；".join(schedule_errors[:3])).strip()
        if failed_files:
            reply = (reply + "\n\n有文件未能发送：" + "、".join(failed_files)).strip()
        self.wechat_bridge.finish_request(request_id, {
            "ok": True,
            "reply": reply,
            "text": reply,
            "thread_id": self.thread_id,
            "silent": silent,
        })

    def create_schedule_plain(
        self,
        *,
        title: str,
        user_request: str,
        hour: int,
        minute: int,
        on_done=None,
        request_id: str = "",
        notify_wechat_user: str = "",
        notify_wechat_context_token: str = "",
        notify_wechat_thread_id: str = "",
    ):
        if not self.project_root:
            raise RuntimeError("尚未打开工作区。")
        raw_request = sanitize_schedule_user_request(user_request)
        raw_title = (title.strip() or raw_request[:36] or "每日计划")[:80]
        if not raw_request:
            raise RuntimeError("缺少计划内容。")
        try:
            schedule_item = create_workspace_schedule(self.project_root, raw_title, raw_request, hour, minute)
            if notify_wechat_user and notify_wechat_context_token:
                notify_wechat_thread_id = str(notify_wechat_thread_id or "").strip()
                update_workspace_schedule(self.project_root, str(schedule_item.get("id") or ""), {
                    "notify_wechat_enabled": True,
                    "notify_wechat_user": notify_wechat_user,
                    "notify_wechat_context_token": notify_wechat_context_token,
                    "notify_wechat_thread_id": safe_thread_id(notify_wechat_thread_id) if notify_wechat_thread_id else "",
                })
                schedule_item = next(
                    (
                        item for item in load_workspace_schedules(self.project_root)
                        if str(item.get("id") or "") == str(schedule_item.get("id") or "")
                    ),
                    schedule_item,
                )
        except Exception as exc:
            if request_id:
                self.wechat_bridge.finish_request(request_id, {"ok": False, "error": str(exc)})
            if on_done:
                on_done(None, str(exc))
            else:
                styled_warning(self, "定时计划", str(exc))
            return
        reply = f"已创建定时计划：{schedule_item.get('title')}，{format_schedule_time(schedule_item)} 触发。"
        self.add_status_bubble(reply)
        if request_id:
            self.wechat_bridge.finish_request(request_id, {"ok": True, "reply": reply, "schedule": schedule_item})
        if on_done:
            on_done(schedule_item, "")

    def is_automation_request_running(self) -> bool:
        return bool(
            (self.automation_context_worker and self.automation_context_worker.isRunning())
            or (self.automation_worker and self.automation_worker.isRunning())
            or (self.web_research_worker and self.web_research_worker.isRunning())
        )

    def is_automation_busy(self) -> bool:
        return self.automation_loop_active or self.is_automation_request_running()

    def begin_automation_loop(self, goal: str):
        self.automation_loop_active = True
        self.automation_loop_round = 1
        self.automation_loop_goal = goal.strip()
        self.automation_loop_force_final_summary = False
        self.refresh_prompt_bubble_buttons()
        self.update_automation_composer_state()

    def stop_automation_loop(self, message: str = "", ensure_manual_entry: bool = False):
        self.automation_loop_active = False
        self.automation_loop_round = 0
        self.automation_loop_goal = ""
        self.automation_loop_force_final_summary = False
        self.active_schedule_id = ""
        self.active_schedule_notify = {}
        self.active_schedule_started_at = 0.0
        self.active_schedule_run_key = ""
        self.refresh_prompt_bubble_buttons()
        self.update_automation_composer_state()
        if message:
            self.add_status_bubble(message)
        if ensure_manual_entry:
            if self.automation_enabled:
                self.show_automation_composer(focus=False)
            else:
                self.ensure_ai_response_entry(focus=False, animate=True, keep_visible=False)
        if self.wechat_active_request_id and not self.is_automation_request_running() and not self.is_execution_running():
            self.finish_wechat_active_request(message or "")
    
    def on_chat_scroll_changed(self, _value: int):
        if self.chat_scroll_programmatic:
            return
        if self.is_chat_at_bottom():
            self.chat_scroll_user_controlled = False
        else:
            self.chat_scroll_user_controlled = True

    def schedule_ensure_ai_response_entry(self):
        if self.automation_enabled or self._ensure_ai_entry_pending or self.is_execution_running():
            return
        self._ensure_ai_entry_pending = True

        def run():
            self._ensure_ai_entry_pending = False
            if self.is_chat_at_bottom():
                self.ensure_ai_response_entry()

        QTimer.singleShot(0, run)

    def ensure_ai_response_entry(self, focus: bool = False, animate: bool = True, keep_visible: bool = True):
        if self.automation_enabled or self.is_execution_running():
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
        if self.automation_enabled or self.find_open_ai_response_frame() is None:
            return
        for delay in (0, 80, 180):
            QTimer.singleShot(delay, self.scroll_to_bottom_if_auto_follow)

    def toggle_prompt_tools(self):
        return

    def automation_context_mode_label(self) -> str:
        for preset in AUTOMATION_CONTEXT_PRESETS:
            if (
                str(preset.get("mode") or "") == self.automation_context_mode
                and str(preset.get("model") or "") == self.automation_model
            ):
                return str(preset.get("label") or "")
        if self.automation_context_mode == "simple":
            return "DeepSeek PRO web thinking"
        return "DeepSeek PRO web"

    def create_automation_context_mode_menu(self) -> QMenu:
        menu = style_skill_popup_menu(QMenu(self))
        for preset in AUTOMATION_CONTEXT_PRESETS:
            label = str(preset.get("label") or "")
            mode = str(preset.get("mode") or "expert")
            model_id = str(preset.get("model") or AUTOMATION_DEFAULT_MODEL)
            action = QAction(label, self)
            action.setCheckable(True)
            action.setChecked(mode == self.automation_context_mode and model_id == self.automation_model)
            action.triggered.connect(
                lambda _checked=False, value_mode=mode, value_model=model_id: self.set_automation_preset(value_mode, value_model)
            )
            menu.addAction(action)
        return menu

    def attach_button_sized_menu(self, button: QToolButton, menu: QMenu):
        def sync_width():
            width = button.width() or button.sizeHint().width()
            menu.setFixedWidth(max(1, width))

        menu.aboutToShow.connect(sync_width)
        sync_width()
        button.setMenu(menu)

    def attach_button_min_width_menu(self, button: QToolButton, menu: QMenu):
        def sync_width():
            button_width = button.width() or button.sizeHint().width()
            label_widths = [button.fontMetrics().horizontalAdvance(action.text()) for action in menu.actions() if action.text()]
            content_width = max(label_widths or [0]) + 44
            target = max(button_width, min(340, max(170, content_width)))
            menu.setFixedWidth(target)

        menu.aboutToShow.connect(sync_width)
        sync_width()
        button.setMenu(menu)

    def attach_skill_menu(self, button: QToolButton, menu: QMenu):
        def sync_width():
            label_widths = [button.fontMetrics().horizontalAdvance(action.text()) for action in menu.actions()]
            content_width = max(label_widths or [0]) + 44
            target = max(button.sizeHint().width(), min(260, max(170, content_width)))
            menu.setFixedWidth(target)

        menu.aboutToShow.connect(sync_width)
        sync_width()
        button.setMenu(menu)

    def update_automation_context_mode_button(self):
        button = self.automation_context_mode_btn
        if button is None:
            return
        button.setText(self.automation_context_mode_label() + " ▾")
        self.attach_button_min_width_menu(button, self.create_automation_context_mode_menu())
        button.setStyleSheet(f"""
            QToolButton {{
                background: transparent;
                color: {COLORS['text_secondary']};
                border: none;
                border-radius: 8px;
                padding: 2px 7px;
                font-size: 11px;
                font-weight: 700;
            }}
            QToolButton:hover {{
                background: {COLORS['surface_alt']};
                color: {COLORS['text']};
            }}
            QToolButton:pressed, QToolButton:open {{
                background: {COLORS['surface_alt']};
                color: {COLORS['text']};
            }}
            QToolButton::menu-indicator {{
                image: none;
                width: 0;
            }}
        """)

    def selected_skills(self) -> List[Dict[str, str]]:
        selected = []
        selected_ids = set(self.selected_skill_ids)
        for skill in self.skills:
            if str(skill.get("id") or "") in selected_ids:
                selected.append(skill)
        return selected

    def selected_skills_context(self) -> str:
        chunks: List[str] = []
        for skill in self.selected_skills():
            name = str(skill.get("name") or skill.get("id") or "未命名技能")
            description = str(skill.get("description") or "").strip()
            path = str(skill.get("path") or "")
            try:
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read().strip()
            except OSError:
                continue
            if content:
                header_lines = [f"【手动选择的 Skill: {name}】"]
                if description:
                    header_lines.append(f"摘要：{description}")
                if path:
                    header_lines.append(f"SKILL.md 路径：{path}")
                header = "\n".join(header_lines)
                chunks.append(f"{header}\n{content}")
        return "\n\n".join(chunks).strip()

    def create_automation_skill_menu(self) -> QMenu:
        menu = style_skill_popup_menu(QMenu(self))
        if not self.skills:
            action = QAction("暂无技能", self)
            action.setEnabled(False)
            menu.addAction(action)
        for skill in self.skills:
            skill_id = str(skill.get("id") or "")
            label = str(skill.get("name") or skill_id)
            row = ClickableFrame(menu)
            row.setObjectName("automationSkillOptionRow")
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(8, 4, 8, 4)
            row_layout.setSpacing(8)
            row.setStyleSheet(f"""
                QFrame#automationSkillOptionRow {{
                    background: transparent;
                    border: none;
                    border-radius: 10px;
                }}
                QFrame#automationSkillOptionRow:hover {{
                    background: {COLORS['accent_light']};
                }}
                QLabel {{
                    background: transparent;
                    border: none;
                }}
            """)
            checkbox = QCheckBox("", row)
            checkbox.setChecked(skill_id in self.selected_skill_ids)
            checkbox.setCursor(Qt.CursorShape.PointingHandCursor)
            checkbox.setStyleSheet(f"""
                QCheckBox {{
                    background: transparent;
                    border: none;
                    spacing: 0;
                }}
                QCheckBox::indicator {{
                    width: 12px;
                    height: 12px;
                    border: 1px solid rgba(23, 32, 51, 70);
                    border-radius: 4px;
                    background: rgba(255, 255, 255, 110);
                }}
                QCheckBox::indicator:checked {{
                    background: {COLORS['accent']};
                    border-color: {COLORS['accent']};
                    image: none;
                }}
            """)
            label_widget = QLabel(label, row)
            label_widget.setStyleSheet(
                f"color: {COLORS['text']}; font-size: 11px; font-weight: 800; background: transparent; border: none;"
            )
            label_widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            row_layout.addWidget(checkbox, 0)
            row_layout.addWidget(label_widget, 1)
            action = QWidgetAction(menu)
            action.setText(label)
            action.setDefaultWidget(row)
            checkbox.toggled.connect(lambda checked=False, value=skill_id: self.set_automation_skill_selected(value, checked, rebuild_menu=False))
            row.clicked.connect(lambda box=checkbox: box.setChecked(not box.isChecked()))
            menu.addAction(action)
        if self.skills:
            menu.addSeparator()
            clear_action = QAction("清空选择", self)
            clear_action.triggered.connect(self.clear_automation_skills)
            clear_action.setEnabled(bool(self.selected_skill_ids))
            menu.addAction(clear_action)
        return menu

    def update_automation_skill_button(self):
        button = self.automation_skill_btn
        if button is None:
            return
        self.update_automation_skill_button_text()
        self.attach_skill_menu(button, self.create_automation_skill_menu())
        button.setStyleSheet(f"""
            QToolButton {{
                background: transparent;
                color: {COLORS['text_secondary']};
                border: none;
                border-radius: 8px;
                padding: 2px 7px;
                font-size: 11px;
                font-weight: 700;
            }}
            QToolButton:hover {{
                background: {COLORS['surface_alt']};
                color: {COLORS['text']};
            }}
            QToolButton:pressed, QToolButton:open {{
                background: {COLORS['surface_alt']};
                color: {COLORS['text']};
            }}
            QToolButton::menu-indicator {{
                image: none;
                width: 0;
            }}
        """)

    def set_automation_preset(self, mode: str, model_id: str):
        self.automation_model = str(model_id or AUTOMATION_DEFAULT_MODEL)
        self.automation_context_mode = "simple" if str(mode).strip().lower() == "simple" else "expert"
        set_automation_context_mode_setting(self.automation_context_mode)
        self.update_automation_context_mode_button()
        self.update_automation_composer_state()

    def update_automation_skill_button_text(self):
        button = self.automation_skill_btn
        if button is None:
            return
        count = len(self.selected_skills())
        button.setText((f"技能 {count}" if count else "技能") + " ▾")

    def set_automation_skill_selected(self, skill_id: str, checked: bool, rebuild_menu: bool = True):
        if checked:
            self.selected_skill_ids.add(skill_id)
        else:
            self.selected_skill_ids.discard(skill_id)
        if rebuild_menu:
            self.update_automation_skill_button()
        else:
            self.update_automation_skill_button_text()
        self.update_automation_composer_state()

    def toggle_automation_skill(self, skill_id: str, checked: bool):
        self.set_automation_skill_selected(skill_id, checked, rebuild_menu=True)

    def clear_automation_skills(self):
        self.selected_skill_ids.clear()
        self.update_automation_skill_button()
        self.update_automation_composer_state()

    def refresh_skills(self):
        if not self.project_root:
            return
        self.skills = load_workspace_skills(self.project_root)
        valid = {str(skill.get("id") or "") for skill in self.skills}
        self.selected_skill_ids = {skill_id for skill_id in self.selected_skill_ids if skill_id in valid}
        self.sidebar.set_skills(self.skills)
        self.update_automation_skill_button()
        self.update_automation_composer_state()

    def open_skill_file(self, skill_id: str):
        skill = next((item for item in self.skills if item.get("id") == skill_id), None)
        path = str((skill or {}).get("path") or "")
        if path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def delete_skill(self, skill_id: str):
        if not self.project_root:
            return
        skill_id = safe_skill_id(skill_id)
        skill = next((item for item in self.skills if item.get("id") == skill_id), None)
        name = str((skill or {}).get("name") or skill_id or "这个技能")
        ok = styled_confirm(
            self,
            "删除技能",
            f"确定删除「{name}」吗？它会从当前工作区技能列表移除，技能文件夹也会被删除。",
            confirm_text="删除",
            destructive=True,
        )
        if not ok:
            return
        if not delete_workspace_skill(self.project_root, skill_id):
            styled_warning(self, "删除失败", "无法删除这个技能文件夹。")
            return
        self.selected_skill_ids.discard(skill_id)
        self.refresh_skills()
        self.sidebar.set_tab("skills")

    def build_skill_generation_prompt(self, raw_text: str) -> str:
        return f"""请把用户粘贴的内容整理成一个 Codex Skill 的标准 SKILL.md。

要求：
- 只输出 Markdown 文档本身，不要解释，不要包裹代码围栏。
- 必须包含 YAML frontmatter，且只有必要字段：
  ---
  name: kebab-case-skill-name
  description: 中文简介，用于显示在技能侧栏卡片上
  ---
- name 使用英文 kebab-case，简短稳定。
- description 必须使用中文，清楚说明什么时候触发这个 skill；这段会展示在技能侧栏卡片上，要简洁、自然、可读。
- 正文用 Markdown，保留用户真正需要的流程、约束、模板、判断标准。
- 内容要精炼，不写 README、安装说明、变更日志等无关材料。
- 不要编造用户没有提供的事实；信息不足时写成可执行的通用约束。

用户粘贴内容：
```text
{raw_text.strip()}
```"""

    def show_new_skill_dialog(self):
        if not self.project_root:
            return
        existing = getattr(self, "skill_dialog", None)
        if existing is not None and existing.isVisible():
            existing.raise_()
            existing.activateWindow()
            return
        dialog = QDialog(self)
        self.skill_dialog = dialog
        dialog.setWindowTitle("添加技能")
        dialog.setModal(False)
        dialog.setWindowModality(Qt.WindowModality.NonModal)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        dialog.setMinimumSize(820, 660)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        hint = QLabel("在线列表来自腾讯 SkillHub 免费公开接口；也可切到自主添加，用简单模式 DeepSeek 生成标准 SKILL.md。")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {COLORS['text_secondary']}; background: transparent; font-size: 12px;")
        layout.addWidget(hint)

        switch_row = QHBoxLayout()
        switch_row.setSpacing(8)
        online_btn = QPushButton("在线列表", cursor=Qt.PointingHandCursor)
        custom_btn = QPushButton("自主添加", cursor=Qt.PointingHandCursor)
        switch_row.addWidget(online_btn)
        switch_row.addWidget(custom_btn)
        switch_row.addStretch()
        layout.addLayout(switch_row)

        stack = QStackedWidget()
        stack.setStyleSheet("QStackedWidget { background: transparent; border: none; }")
        layout.addWidget(stack, 1)

        def segment_style(active: bool) -> str:
            return f"""
                QPushButton {{
                    background: {COLORS['accent'] if active else COLORS['surface']};
                    color: {'white' if active else COLORS['text']};
                    border: 1px solid {COLORS['accent'] if active else COLORS['border']};
                    border-radius: 11px;
                    padding: 7px 14px;
                    font-size: 12px;
                    font-weight: 900;
                }}
                QPushButton:hover {{
                    background: {COLORS['accent_dark'] if active else COLORS['surface_alt']};
                }}
            """

        def set_page(index: int):
            stack.setCurrentIndex(index)
            online_btn.setStyleSheet(segment_style(index == 0))
            custom_btn.setStyleSheet(segment_style(index == 1))

        online_page = QWidget()
        online_layout = QVBoxLayout(online_page)
        online_layout.setContentsMargins(0, 0, 0, 0)
        online_layout.setSpacing(10)

        search_row = QHBoxLayout()
        search_row.setSpacing(8)
        search_edit = QLineEdit()
        search_edit.setPlaceholderText("搜索技能，例如 frontend、后端、产品、PDF、Excel...")
        search_edit.setStyleSheet(f"""
            QLineEdit {{
                background: {COLORS['surface']};
                color: {COLORS['text']};
                border: 1px solid {COLORS['border']};
                border-radius: 10px;
                padding: 8px 10px;
                font-size: 12px;
            }}
        """)
        search_btn = QPushButton("搜索", cursor=Qt.PointingHandCursor)
        reload_btn = QPushButton("热门列表", cursor=Qt.PointingHandCursor)
        for btn in (search_btn, reload_btn):
            btn.setFixedHeight(34)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: {COLORS['surface']};
                    color: {COLORS['text']};
                    border: none;
                    border-radius: 10px;
                    padding: 6px 12px;
                    font-size: 12px;
                    font-weight: 900;
                }}
                QPushButton:hover {{ background: {COLORS['surface_alt']}; }}
            """)
        search_row.addWidget(search_edit, 1)
        search_row.addWidget(search_btn)
        search_row.addWidget(reload_btn)
        online_layout.addLayout(search_row)

        category_row = QHBoxLayout()
        category_row.setSpacing(6)
        quick_queries = [
            ("前端", "frontend|ui|react|tailwind", "developer-tools", ""),
            ("后端", "backend|api|database|server", "developer-tools", ""),
            ("产品", "product|prd|requirements|user research", "", "productivity|content-creation"),
            ("办公", "office document|word docx|excel xlsx|ppt", "", "productivity|data-analysis"),
            ("数据", "data analysis|excel|chart|database", "data-analysis", ""),
            ("研究", "research|academic|paper|search", "", "productivity|data-analysis"),
        ]
        for label, query, category, fallback_category in quick_queries:
            chip = QPushButton(label, cursor=Qt.PointingHandCursor)
            chip.setFixedHeight(28)
            chip.setStyleSheet(f"""
                QPushButton {{
                    background: {COLORS['surface']};
                    color: {COLORS['text_secondary']};
                    border: none;
                    border-radius: 9px;
                    padding: 4px 10px;
                    font-size: 12px;
                    font-weight: 800;
                }}
                QPushButton:hover {{
                    background: {COLORS['accent_light']};
                    color: {COLORS['accent_dark']};
                }}
            """)
            chip.clicked.connect(
                lambda _checked=False, q=query, c=category, f=fallback_category: (
                    search_edit.setText(q or c),
                    start_remote_search(q, c, f),
                )
            )
            category_row.addWidget(chip)
        category_row.addStretch()
        online_layout.addLayout(category_row)

        online_status = QLabel("点击“热门列表”加载腾讯 SkillHub 推荐，或输入关键词搜索；选择后会拉取远程 SKILL.md 供你预览确认。")
        online_status.setWordWrap(True)
        online_status.setStyleSheet(f"color: {COLORS['text_secondary']}; background: transparent; font-size: 12px;")
        online_layout.addWidget(online_status)

        result_scroll = QScrollArea()
        result_scroll.setWidgetResizable(True)
        result_scroll.setFrameShape(QFrame.NoFrame)
        result_scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        result_container = QWidget()
        result_layout = QVBoxLayout(result_container)
        result_layout.setContentsMargins(0, 0, 0, 0)
        result_layout.setSpacing(8)
        result_layout.addStretch()
        result_scroll.setWidget(result_container)
        online_layout.addWidget(result_scroll, 1)

        import_preview = QTextEdit()
        import_preview.setAcceptRichText(False)
        import_preview.setPlaceholderText("选择在线技能后，这里会显示将保存的 SKILL.md，可编辑后添加。")
        import_preview.setFixedHeight(150)
        import_preview.setStyleSheet(f"""
            QTextEdit {{
                background: {COLORS['surface']};
                color: {COLORS['text']};
                border: 1px solid {COLORS['border']};
                border-radius: 10px;
                padding: 10px;
                font-size: 12px;
            }}
        """)
        online_layout.addWidget(import_preview)

        stack.addWidget(online_page)

        custom_page = QWidget()
        custom_layout = QVBoxLayout(custom_page)
        custom_layout.setContentsMargins(0, 0, 0, 0)
        custom_layout.setSpacing(10)

        source_edit = QTextEdit()
        source_edit.setAcceptRichText(False)
        source_edit.setPlaceholderText("粘贴流程、规则、模板、偏好或任意说明...")
        source_edit.setFixedHeight(150)
        source_edit.setStyleSheet(f"""
            QTextEdit {{
                background: {COLORS['surface']};
                color: {COLORS['text']};
                border: 1px solid {COLORS['border']};
                border-radius: 10px;
                padding: 10px;
                font-size: 12px;
            }}
        """)
        custom_layout.addWidget(source_edit)

        preview = QTextEdit()
        preview.setAcceptRichText(False)
        preview.setPlaceholderText("生成后的 SKILL.md 会显示在这里，可手动微调后保存。")
        preview.setStyleSheet(source_edit.styleSheet())
        custom_layout.addWidget(preview, 1)
        stack.addWidget(custom_page)

        status = QLabel("")
        status.setStyleSheet(f"color: {COLORS['text_secondary']}; background: transparent; font-size: 12px;")
        layout.addWidget(status)

        row = QHBoxLayout()
        row.addStretch()
        save_import_btn = QPushButton("添加所选技能", cursor=Qt.PointingHandCursor)
        generate_btn = QPushButton("生成预览", cursor=Qt.PointingHandCursor)
        save_btn = QPushButton("保存技能", cursor=Qt.PointingHandCursor)
        cancel_btn = QPushButton("取消", cursor=Qt.PointingHandCursor)
        save_import_btn.setEnabled(False)
        save_btn.setEnabled(False)
        for btn in (save_import_btn, generate_btn, save_btn, cancel_btn):
            btn.setFixedHeight(32)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: {COLORS['surface']};
                    color: {COLORS['text']};
                    border: 1px solid {COLORS['border']};
                    border-radius: 9px;
                    padding: 6px 14px;
                    font-size: 12px;
                    font-weight: 800;
                }}
                QPushButton:hover {{
                    background: {COLORS['accent_light']};
                    color: {COLORS['accent_dark']};
                }}
                QPushButton:disabled {{
                    color: {COLORS['muted']};
                    background: {COLORS['surface_alt']};
                }}
            """)
            row.addWidget(btn)
        layout.addLayout(row)
        selected_remote_package: Dict[str, bytes] = {}
        selected_remote_skill: Dict[str, str] = {}

        def remote_skill_markdown(skill: Dict[str, str]) -> str:
            content = str(skill.get("content") or "").strip()
            if content:
                return normalize_skill_markdown(content, str(skill.get("name") or "remote-skill"))
            name = safe_skill_id(skill.get("name") or skill.get("title") or "remote-skill")
            description = str(skill.get("description") or "Use this skill when the request matches this remote skill.").strip()
            title = str(skill.get("title") or name).strip()
            lines = [
                f"---\nname: {name}\ndescription: {description}\n---",
                "",
                f"# {title}",
                "",
                description,
            ]
            meta_lines = []
            if skill.get("author"):
                meta_lines.append(f"- Author: {skill.get('author')}")
            if skill.get("category"):
                meta_lines.append(f"- Category: {skill.get('category')}")
            if skill.get("source_url"):
                meta_lines.append(f"- Source: {skill.get('source_url')}")
            if skill.get("install"):
                meta_lines.append(f"- Original install command: `{skill.get('install')}`")
            if meta_lines:
                lines.extend(["", "## Source Metadata", "", *meta_lines])
            lines.extend(["", "## Instructions", "", "Follow the capability and constraints described above. If source metadata is present, treat it as provenance, not as executable shell instructions."])
            return normalize_skill_markdown("\n".join(lines), name)

        def clear_remote_results():
            while result_layout.count() > 1:
                item = result_layout.takeAt(0)
                widget = item.widget()
                if widget:
                    widget.deleteLater()

        def finish_remote_content(skill: Dict[str, str], text: str, error: str, package: Dict[str, bytes]):
            nonlocal selected_remote_package
            selected_remote_package = dict(package or {})
            if error:
                import_preview.setPlainText(remote_skill_markdown(skill))
                save_import_btn.setEnabled(True)
                status.setText(error + " 已用列表摘要生成兜底预览。")
                return
            import_preview.setPlainText(normalize_skill_markdown(text, str(skill.get("name") or "remote-skill")))
            save_import_btn.setEnabled(True)
            status.setText(f"已拉取远程技能包（{len(selected_remote_package) or 1} 个文件），预览显示 SKILL.md，可编辑后添加。")

        def choose_remote_skill(skill: Dict[str, str]):
            nonlocal selected_remote_package, selected_remote_skill
            save_import_btn.setEnabled(False)
            selected_remote_package = {}
            selected_remote_skill = dict(skill)
            import_preview.setPlainText("正在拉取远程 SKILL.md...")
            status.setText("正在从腾讯 SkillHub 获取完整技能包。")
            worker = getattr(self, "skill_content_worker", None)
            if worker is not None and worker.isRunning():
                worker.requestInterruption()
                worker.terminate()
                worker.wait(500)
            slug = str(skill.get("slug") or skill.get("name") or "").strip()
            worker = RemoteSkillContentWorker(slug, self)
            self.skill_content_worker = worker
            worker.finished_signal.connect(lambda text, error, package, s=skill: finish_remote_content(s, text, error, package))
            worker.finished.connect(lambda: setattr(self, "skill_content_worker", None))
            worker.finished.connect(worker.deleteLater)
            worker.start()

        def add_remote_result_card(skill: Dict[str, str]):
            card = QFrame()
            card.setStyleSheet(f"""
                QFrame {{
                    background: {COLORS['surface']};
                    border: 1px solid {COLORS['border']};
                    border-radius: 12px;
                }}
            """)
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(12, 10, 12, 10)
            card_layout.setSpacing(6)
            title_row = QHBoxLayout()
            title = QLabel(str(skill.get("title") or skill.get("name") or "skill"))
            title.setWordWrap(True)
            title.setStyleSheet(f"color: {COLORS['text']}; background: transparent; border: none; font-size: 12px; font-weight: 900;")
            title_row.addWidget(title, 1)
            add_btn = QPushButton("预览", cursor=Qt.PointingHandCursor)
            add_btn.setFixedHeight(28)
            add_btn.setStyleSheet(f"""
                QPushButton {{
                    background: {COLORS['accent_light']};
                    color: {COLORS['accent_dark']};
                    border: none;
                    border-radius: 9px;
                    padding: 4px 10px;
                    font-size: 12px;
                    font-weight: 900;
                }}
            """)
            add_btn.clicked.connect(lambda _checked=False, s=skill: choose_remote_skill(s))
            title_row.addWidget(add_btn)
            card_layout.addLayout(title_row)
            desc = QLabel(str(skill.get("description") or "暂无描述"))
            desc.setWordWrap(True)
            desc.setStyleSheet(f"color: {COLORS['text_secondary']}; background: transparent; border: none; font-size: 11px;")
            card_layout.addWidget(desc)
            metric_parts = []
            if skill.get("downloads"):
                metric_parts.append(f"{skill.get('downloads')} downloads")
            if skill.get("stars"):
                metric_parts.append(f"{skill.get('stars')} stars")
            meta = " · ".join([x for x in [skill.get("category"), skill.get("author"), *metric_parts] if x])
            if meta:
                meta_label = QLabel(meta)
                meta_label.setWordWrap(True)
                meta_label.setStyleSheet(f"color: {COLORS['muted']}; background: transparent; border: none; font-size: 10px;")
                card_layout.addWidget(meta_label)
            result_layout.insertWidget(result_layout.count() - 1, card)

        def finish_remote_search(skills: List[Dict[str, str]], error: str):
            search_btn.setEnabled(True)
            reload_btn.setEnabled(True)
            clear_remote_results()
            if error:
                online_status.setText(error)
                return
            online_status.setText(f"已加载 {len(skills)} 个在线技能。选择一个后可预览并添加到当前工作区。")
            for skill in skills:
                add_remote_result_card(skill)

        def start_remote_search(query: str = "", category: str = "", fallback_category: str = ""):
            nonlocal selected_remote_package, selected_remote_skill
            worker = getattr(self, "skill_search_worker", None)
            if worker is not None and worker.isRunning():
                worker.requestInterruption()
                worker.terminate()
                worker.wait(500)
            search_btn.setEnabled(False)
            reload_btn.setEnabled(False)
            save_import_btn.setEnabled(False)
            selected_remote_package = {}
            selected_remote_skill = {}
            import_preview.clear()
            online_status.setText("正在加载腾讯 SkillHub 技能列表...")
            worker = RemoteSkillSearchWorker(query.strip(), category.strip(), fallback_category.strip(), self)
            self.skill_search_worker = worker
            worker.finished_signal.connect(finish_remote_search)
            worker.finished.connect(lambda: setattr(self, "skill_search_worker", None))
            worker.finished.connect(worker.deleteLater)
            worker.start()

        search_btn.clicked.connect(lambda: start_remote_search(search_edit.text()))
        reload_btn.clicked.connect(lambda: start_remote_search(""))
        search_edit.returnPressed.connect(lambda: start_remote_search(search_edit.text()))
        generation_preview_timer = QTimer(dialog)
        generation_preview_timer.setInterval(420)
        generation_preview_tick = {"value": 0}

        def update_generation_placeholder():
            generation_preview_tick["value"] += 1
            dots = "." * ((generation_preview_tick["value"] % 3) + 1)
            steps = [
                "读取你粘贴的规则和流程",
                "整理触发条件与使用边界",
                "生成中文侧栏简介",
                "组织 SKILL.md 正文结构",
                "检查 frontmatter 和 Markdown 格式",
            ]
            visible = steps[: min(len(steps), 1 + generation_preview_tick["value"] // 2)]
            preview.setPlainText(
                "正在生成技能预览" + dots + "\n\n"
                + "\n".join(f"- {step}" for step in visible)
                + "\n\n生成完成后这里会替换为可编辑的 SKILL.md。"
            )
            preview.moveCursor(QTextCursor.MoveOperation.End)

        generation_preview_timer.timeout.connect(update_generation_placeholder)

        def finish_generation(text: str, error: str):
            self.skill_generation_worker = None
            generation_preview_timer.stop()
            generate_btn.setEnabled(True)
            if error:
                status.setText("生成失败：" + error[-500:])
                return
            content = normalize_skill_markdown(text, "custom-skill")
            preview.setPlainText(content)
            save_btn.setEnabled(True)
            status.setText("已生成预览，可编辑后保存。")

        def generate():
            raw = source_edit.toPlainText().strip()
            if not raw:
                status.setText("请先粘贴内容。")
                return
            generate_btn.setEnabled(False)
            save_btn.setEnabled(False)
            generation_preview_tick["value"] = 0
            update_generation_placeholder()
            generation_preview_timer.start()
            status.setText("正在调用简单模式 DeepSeek 生成，完成前先显示生成进度...")
            worker = AutomationChatWorker(
                self.automation_manager,
                [{"role": "user", "content": self.build_skill_generation_prompt(raw)}],
                "DeepSeekV4-simple",
                self.thread_id + "-skill",
            )
            self.skill_generation_worker = worker
            worker.finished_signal.connect(finish_generation)
            worker.finished.connect(worker.deleteLater)
            worker.start()

        def cleanup_worker():
            generation_preview_timer.stop()
            for attr in ("skill_generation_worker", "skill_search_worker", "skill_content_worker"):
                worker = getattr(self, attr, None)
                if worker is not None and worker.isRunning():
                    worker.requestInterruption()
                    worker.terminate()
                    worker.wait(800)
                setattr(self, attr, None)

        def save_content_from_edit(edit: QTextEdit):
            content = edit.toPlainText().strip()
            if not content:
                status.setText("没有可保存的 SKILL.md。")
                return
            try:
                skill = save_workspace_skill(self.project_root, content)
            except OSError as exc:
                status.setText("保存失败：" + str(exc))
                return
            self.refresh_skills()
            self.selected_skill_ids.add(str(skill.get("id") or ""))
            self.update_automation_skill_button()
            self.sidebar.set_tab("skills")
            status.setText(f"已添加技能：{skill.get('name') or skill.get('id') or ''}。可以继续添加其他技能。")
            save_btn.setEnabled(False)

        def save():
            save_content_from_edit(preview)

        def save_import():
            content = import_preview.toPlainText().strip()
            if not content:
                status.setText("没有可保存的 SKILL.md。")
                return
            try:
                package = dict(selected_remote_package)
                if package:
                    package["SKILL.md"] = normalize_skill_markdown(content).encode("utf-8")
                    skill = save_workspace_skill_package(
                        self.project_root,
                        package,
                        content,
                        {
                            "display_name": selected_remote_skill.get("title") or selected_remote_skill.get("name") or "",
                            "display_description": selected_remote_skill.get("description") or "",
                            "source": selected_remote_skill.get("source") or "tencent-skillhub",
                            "slug": selected_remote_skill.get("slug") or "",
                        },
                    )
                else:
                    skill = save_workspace_skill(self.project_root, content)
            except OSError as exc:
                status.setText("保存失败：" + str(exc))
                return
            self.refresh_skills()
            self.selected_skill_ids.add(str(skill.get("id") or ""))
            self.update_automation_skill_button()
            self.sidebar.set_tab("skills")
            status.setText(f"已添加技能：{skill.get('name') or skill.get('id') or ''}。可以继续添加其他技能。")
            save_import_btn.setEnabled(False)

        def update_footer_buttons():
            online = stack.currentIndex() == 0
            save_import_btn.setVisible(online)
            generate_btn.setVisible(not online)
            save_btn.setVisible(not online)

        def switch_to(index: int):
            set_page(index)
            update_footer_buttons()

        online_btn.clicked.connect(lambda: switch_to(0))
        custom_btn.clicked.connect(lambda: switch_to(1))
        generate_btn.clicked.connect(generate)
        save_btn.clicked.connect(save)
        save_import_btn.clicked.connect(save_import)
        cancel_btn.clicked.connect(lambda: (cleanup_worker(), dialog.reject()))
        dialog.finished.connect(lambda _result: (cleanup_worker(), setattr(self, "skill_dialog", None)))
        switch_to(0)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def set_automation_context_mode(self, mode: str):
        self.automation_context_mode = "simple" if str(mode).strip().lower() == "simple" else "expert"
        set_automation_context_mode_setting(self.automation_context_mode)
        self.update_automation_context_mode_button()
        self.update_automation_composer_state()

    def effective_automation_model(self) -> str:
        if self.automation_context_mode == "simple":
            return AUTOMATION_SIMPLE_MODEL_BY_MODEL.get(self.automation_model, self.automation_model)
        return self.automation_model

    def automation_input_style(self) -> str:
        return f"""
            QTextEdit {{
                background: transparent;
                color: {COLORS['text']};
                border: none;
                padding: 3px 4px;
                font-size: {scaled_font_px(13)}px;
            }}
            QScrollBar:vertical {{
                width: 0;
                background: transparent;
            }}
        """

    def ai_manual_input_style(self) -> str:
        return f"""
            QTextEdit {{
                background: {COLORS['input_bg']};
                color: {COLORS['text']};
                border: 1px solid {COLORS['border']};
                border-radius: 12px;
                padding: 12px;
                font-size: {scaled_font_px(13)}px;
                font-family: 'SF Mono', 'Menlo', monospace;
            }}
            QTextEdit:focus {{
                border: 1px solid {COLORS['accent']};
            }}
        """

    def apply_chat_visual_settings(self):
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(app_global_style())
        self.setStyleSheet(f"background: {COLORS['bg']};")
        if hasattr(self, "sidebar"):
            self.sidebar.apply_theme_style()
        if hasattr(self, "sidebar_resize_handle"):
            self.sidebar_resize_handle.apply_theme_style()
        if hasattr(self, "sidebar_btn"):
            self.sidebar_btn.setStyleSheet(f"""
                QToolButton {{
                    background: transparent;
                    border: none;
                    color: {COLORS['accent_dark']};
                    font-size: 20px;
                    font-weight: 800;
                    padding-top: 3px;
                }}
                QToolButton:hover {{
                    background: {COLORS['accent_light']};
                    border-radius: 8px;
                }}
            """)
        if hasattr(self, "right_panel"):
            self.right_panel.setStyleSheet(f"background: {COLORS['bg']};")
        if hasattr(self, "path_title"):
            self.path_title.setStyleSheet(f"color: {COLORS['text']}; font-size: 18px; font-weight: 900; background: transparent;")
        if hasattr(self, "path_label"):
            self.path_label.setStyleSheet(f"""
                QLabel {{
                    color: {COLORS['text_secondary']};
                    font-size: 12px;
                    padding: 8px 12px;
                    background: {COLORS['surface']};
                    border: 1px solid {COLORS['border']};
                    border-radius: 12px;
                }}
            """)
        if hasattr(self, "copy_prompt_btn"):
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
        if self.settings_btn is not None:
            self.settings_btn.setIcon(line_icon("settings", COLORS["text"], 18))
            self.settings_btn.setStyleSheet(f"""
                QToolButton {{
                    background: {COLORS['surface']};
                    color: {COLORS['text']};
                    border: 1px solid {COLORS['border']};
                    border-radius: 12px;
                    font-size: 15px;
                    font-weight: 900;
                }}
                QToolButton:hover {{
                    background: {COLORS['accent_light']};
                    color: {COLORS['accent_dark']};
                }}
            """)
        if hasattr(self, "top_clear_history_btn"):
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
        if hasattr(self, "chat_container"):
            self.chat_container.setStyleSheet(f"background: {COLORS['surface']}; border-radius: 18px;")
        if hasattr(self, "chat_column"):
            self.chat_column.setStyleSheet(f"background: {COLORS['surface']};")
        if hasattr(self, "scroll_area"):
            self.scroll_area.setStyleSheet(f"""
                QScrollArea {{
                    background: {COLORS['surface']};
                    border: 1px solid {COLORS['border']};
                    border-radius: 18px;
                }}
                QScrollArea QWidget#qt_scrollarea_viewport {{
                    background: {COLORS['surface']};
                    border-radius: 18px;
                }}
                QScrollArea > QWidget > QWidget {{
                    background: {COLORS['surface']};
                    border-radius: 18px;
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
            self.scroll_area.viewport().setStyleSheet(f"background: {COLORS['surface']}; border-radius: 18px;")
        if hasattr(self, "sidebar_resize_overlay"):
            self.sidebar_resize_overlay.setStyleSheet(f"""
                QLabel {{
                    background: {COLORS['surface']};
                    color: {COLORS['text_secondary']};
                    border: 1px dashed {COLORS['border']};
                    border-radius: 18px;
                    font-size: 13px;
                    font-weight: 800;
                    padding: 20px;
                }}
            """)
        if self.automation_composer is not None:
            self.automation_composer.setStyleSheet(f"""
                QFrame {{
                    background: {COLORS['surface']};
                    border: 1px solid {COLORS['border']};
                    border-radius: 18px;
                }}
            """)
        if self.automation_composer_input_column is not None:
            self.automation_composer_input_column.setStyleSheet("""
                QWidget#automationComposerInputColumn {
                    background: transparent;
                    border-radius: 14px;
                }
            """)
        if self.automation_input is not None:
            self.automation_input.setStyleSheet(self.automation_input_style())
            self.automation_input.viewport().setStyleSheet("background: transparent; border-radius: 14px;")
        if self.automation_context_mode_btn is not None:
            self.update_automation_context_mode_button()
        if self.automation_skill_btn is not None:
            self.update_automation_skill_button()
        for idx in range(self.chat_layout.count()):
            widget = self.chat_layout.itemAt(idx).widget()
            if isinstance(widget, (ChatBubble, ExecutionLogPanel, ChangeSummaryCard)):
                widget.refresh_visual_settings()
                continue
            if isinstance(widget, QLabel):
                widget.setStyleSheet(f"""
                    QLabel {{
                        background: transparent;
                        color: {COLORS['text_secondary']};
                        border: none;
                        padding: 8px 12px;
                        font-size: {scaled_font_px(12)}px;
                        font-weight: 800;
                    }}
                """)
                widget.updateGeometry()
                continue
            if widget is not None and widget.objectName() == "aiResponseFrame":
                ai_input = getattr(widget, "ai_input", None)
                if ai_input is not None:
                    ai_input.setStyleSheet(self.ai_manual_input_style())
        if hasattr(self, "terminal_panel"):
            self.terminal_panel.apply_theme_style()
        if hasattr(self, "terminal_resize_handle"):
            self.terminal_resize_handle.apply_theme_style()
        if hasattr(self, "status_bar"):
            self.status_bar.setStyleSheet(f"""
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
        window = self.window()
        if isinstance(window, QMainWindow):
            window.setStyleSheet(f"QMainWindow {{ background: {COLORS['bg']}; }}")
        self.apply_preferences_dialog_theme()
        self.update_automation_composer_state()
        if hasattr(self, "chat_column"):
            self.chat_column.adjustSize()
        if hasattr(self, "chat_container"):
            self.chat_container.adjustSize()
        self.update()
        QApplication.processEvents()

    def set_chat_font_scale(self, scale: float):
        set_chat_font_scale_setting(scale)
        self.apply_chat_visual_settings()

    def toggle_app_theme(self):
        next_theme = "light" if app_theme_setting() == "dark" else "dark"
        set_app_theme_setting(next_theme)
        apply_theme_palette(next_theme)
        self.apply_chat_visual_settings()

    def apply_preferences_dialog_theme(self):
        dialog = self.preferences_dialog
        if dialog is None:
            return
        dialog.setStyleSheet(f"""
            QDialog {{
                background: {COLORS['bg_top']};
                color: {COLORS['text']};
            }}
        """)
        for attr in ("preferences_title_label", "preferences_font_label", "preferences_language_label"):
            label = getattr(dialog, attr, None)
            if isinstance(label, QLabel):
                label.setStyleSheet(f"color: {COLORS['text']}; background: transparent; font-size: 13px; font-weight: 900;")
        if hasattr(dialog, "preferences_title_label") and isinstance(dialog.preferences_title_label, QLabel):
            dialog.preferences_title_label.setStyleSheet(f"color: {COLORS['text']}; background: transparent; font-size: 15px; font-weight: 900;")
        language_value = getattr(dialog, "preferences_language_value", None)
        if isinstance(language_value, QLabel):
            language_value.setStyleSheet(
                f"color: {COLORS['text_secondary']}; background: {COLORS['surface_alt']}; "
                f"border: 1px solid {COLORS['border']}; border-radius: 10px; padding: 9px 12px; font-size: 12px; font-weight: 800;"
            )
        theme_toggle = getattr(dialog, "preferences_theme_toggle", None)
        if isinstance(theme_toggle, SettingsToggleRow):
            theme_toggle.apply_style()
        proxy_toggle = getattr(dialog, "preferences_proxy_toggle", None)
        if isinstance(proxy_toggle, SettingsToggleRow):
            proxy_toggle.apply_style()
        font_buttons = getattr(dialog, "preferences_font_buttons", []) or []
        for item in font_buttons:
            if not isinstance(item, QPushButton):
                continue
            item.setStyleSheet(f"""
                QPushButton {{
                    background: {COLORS['accent'] if item.isChecked() else COLORS['surface']};
                    color: {'white' if item.isChecked() else COLORS['text']};
                    border: 1px solid {COLORS['accent'] if item.isChecked() else COLORS['border']};
                    border-radius: 10px;
                    padding: 6px 12px;
                    font-size: 12px;
                    font-weight: 900;
                }}
                QPushButton:hover {{ background: {COLORS['accent_dark'] if item.isChecked() else COLORS['surface_alt']}; }}
            """)
        close_btn = getattr(dialog, "preferences_close_btn", None)
        if isinstance(close_btn, QPushButton):
            close_btn.setStyleSheet(f"""
                QPushButton {{
                    background: {COLORS['surface']};
                    color: {COLORS['text']};
                    border: 1px solid {COLORS['border']};
                    border-radius: 10px;
                    padding: 6px 18px;
                    font-size: 12px;
                    font-weight: 900;
                }}
                QPushButton:hover {{ background: {COLORS['surface_alt']}; }}
            """)
        dialog.update()

    def show_preferences_dialog(self):
        dialog = self.preferences_dialog
        if dialog is not None:
            dialog.show()
            dialog.raise_()
            dialog.activateWindow()
            return

        dialog = QDialog(self)
        self.preferences_dialog = dialog
        dialog.setWindowTitle("偏好设置")
        dialog.setMinimumWidth(460)
        dialog.setModal(False)
        dialog.setWindowModality(Qt.WindowModality.NonModal)
        dialog.finished.connect(lambda _code: setattr(self, "preferences_dialog", None))
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)

        title = QLabel("偏好设置")
        dialog.preferences_title_label = title
        layout.addWidget(title)

        theme_toggle = SettingsToggleRow(
            "夜间模式",
            "切换深色界面",
            app_theme_setting() == "dark",
            parent=dialog,
        )
        dialog.preferences_theme_toggle = theme_toggle
        layout.addWidget(theme_toggle)

        proxy_toggle = SettingsToggleRow(
            "使用系统代理",
            "让 Provider 与微信连接器继承系统 HTTP/HTTPS 代理",
            use_system_proxy_setting(),
            parent=dialog,
        )
        dialog.preferences_proxy_toggle = proxy_toggle
        layout.addWidget(proxy_toggle)

        font_label = QLabel("对话字号")
        dialog.preferences_font_label = font_label
        layout.addWidget(font_label)
        font_row = QHBoxLayout()
        font_row.setSpacing(8)
        current_scale = chat_font_scale_setting()
        font_buttons: List[QPushButton] = []
        for label, scale in (("小", 0.9), ("标准", 1.0), ("大", 1.15), ("特大", 1.3)):
            btn = QPushButton(label, cursor=Qt.CursorShape.PointingHandCursor)
            btn.setCheckable(True)
            btn.setChecked(abs(current_scale - scale) < 0.01)
            btn.setFixedHeight(34)
            font_buttons.append(btn)

            def choose_font(_checked=False, value=scale, clicked=btn):
                for item in font_buttons:
                    item.setChecked(item is clicked)
                self.set_chat_font_scale(value)

            btn.clicked.connect(choose_font)
            font_row.addWidget(btn, 1)
        dialog.preferences_font_buttons = font_buttons
        layout.addLayout(font_row)

        language_label = QLabel("语言设置")
        dialog.preferences_language_label = language_label
        layout.addWidget(language_label)
        language_value = QLabel("简体中文")
        dialog.preferences_language_value = language_value
        layout.addWidget(language_value)

        for btn in font_buttons:
            btn.toggled.connect(lambda _checked=False: self.apply_preferences_dialog_theme())

        def on_theme_toggled(enabled: bool):
            set_app_theme_setting("dark" if enabled else "light")
            apply_theme_palette(app_theme_setting())
            self.apply_chat_visual_settings()
            self.apply_preferences_dialog_theme()

        theme_toggle.toggled.connect(on_theme_toggled)

        def on_proxy_toggled(enabled: bool):
            set_use_system_proxy_setting(enabled)
            self.add_status_bubble(
                "已开启系统代理继承：后续 Provider / 微信连接器请求将走系统代理。"
                if enabled
                else "已关闭系统代理继承：后续 Provider / 微信连接器请求将直连。"
            )

        proxy_toggle.toggled.connect(on_proxy_toggled)

        row = QHBoxLayout()
        row.addStretch()
        close_btn = QPushButton("关闭", cursor=Qt.CursorShape.PointingHandCursor)
        close_btn.setFixedHeight(34)
        dialog.preferences_close_btn = close_btn
        close_btn.clicked.connect(dialog.close)
        row.addWidget(close_btn)
        layout.addLayout(row)
        self.apply_preferences_dialog_theme()
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def show_schedules_dialog(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("定时计划")
        dialog.setMinimumSize(620, 520)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)

        title = QLabel("定时计划")
        title.setStyleSheet(f"color: {COLORS['text']}; background: transparent; font-size: 15px; font-weight: 900;")
        layout.addWidget(title)

        list_area = QScrollArea()
        list_area.setWidgetResizable(True)
        list_area.setFrameShape(QFrame.Shape.NoFrame)
        list_area.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        list_body = QWidget()
        list_layout = QVBoxLayout(list_body)
        list_layout.setContentsMargins(0, 0, 0, 0)
        list_layout.setSpacing(8)
        list_area.setWidget(list_body)
        layout.addWidget(list_area, 1)

        def button_style(primary: bool = False) -> str:
            return f"""
                QPushButton {{
                    background: {COLORS['accent'] if primary else COLORS['surface']};
                    color: {'white' if primary else COLORS['text']};
                    border: 1px solid {COLORS['accent'] if primary else COLORS['border']};
                    border-radius: 10px;
                    padding: 7px 13px;
                    font-size: 12px;
                    font-weight: 900;
                }}
                QPushButton:hover {{
                    background: {COLORS['accent_dark'] if primary else COLORS['surface_alt']};
                }}
            """

        def refresh_list():
            while list_layout.count():
                item = list_layout.takeAt(0)
                widget = item.widget()
                if widget:
                    widget.deleteLater()
            schedules = load_workspace_schedules(self.project_root) if self.project_root else []
            if not schedules:
                empty = QLabel("当前没有定时计划。可以在微信里说“每天 7 点整理项目进度”，或在这里新增。")
                empty.setWordWrap(True)
                empty.setStyleSheet(f"color: {COLORS['text_secondary']}; background: {COLORS['surface_alt']}; border: 1px solid {COLORS['border']}; border-radius: 10px; padding: 14px; font-size: 12px; font-weight: 800;")
                list_layout.addWidget(empty)
            for schedule_item in schedules:
                card = QFrame()
                card.setStyleSheet(f"QFrame {{ background: {COLORS['surface']}; border: 1px solid {COLORS['border']}; border-radius: 10px; }}")
                card_layout = QVBoxLayout(card)
                card_layout.setContentsMargins(12, 10, 12, 10)
                card_layout.setSpacing(8)
                header = QHBoxLayout()
                name = QLabel(str(schedule_item.get("title") or "定时计划"))
                name.setStyleSheet(f"color: {COLORS['text']}; background: transparent; font-size: 13px; font-weight: 900; border: none;")
                header.addWidget(name, 1)
                is_expired = bool(str(schedule_item.get("expired_at") or "").strip())
                state = SettingsToggleRow("", "", bool(schedule_item.get("enabled", True)) and not is_expired, parent=card)
                state.setFixedWidth(76)
                state.setEnabled(not is_expired)
                state.toggled.connect(lambda enabled, sid=str(schedule_item.get("id") or ""): (update_workspace_schedule(self.project_root, sid, {"enabled": enabled}) if self.project_root else False, refresh_list()))
                header.addWidget(state, 0)
                card_layout.addLayout(header)
                meta = QLabel(format_schedule_time(schedule_item))
                if is_expired:
                    meta.setText(f"{meta.text()}｜已过期")
                meta.setStyleSheet(f"color: {COLORS['text_secondary']}; background: transparent; font-size: 12px; font-weight: 800; border: none;")
                card_layout.addWidget(meta)
                prompt = QLabel(str(schedule_item.get("prompt") or ""))
                prompt.setWordWrap(True)
                prompt.setStyleSheet(f"color: {COLORS['text']}; background: transparent; font-size: 12px; font-weight: 700; border: none;")
                card_layout.addWidget(prompt)
                action_row = QHBoxLayout()
                run_btn = QPushButton("立即执行", cursor=Qt.CursorShape.PointingHandCursor)
                run_btn.setStyleSheet(button_style(primary=True))
                run_btn.clicked.connect(lambda _checked=False, item=dict(schedule_item): (dialog.close(), self.start_schedule(item)))
                action_row.addWidget(run_btn)
                delete_btn = QPushButton("删除", cursor=Qt.CursorShape.PointingHandCursor)
                delete_btn.setStyleSheet(button_style())
                delete_btn.clicked.connect(lambda _checked=False, sid=str(schedule_item.get("id") or ""): (delete_workspace_schedule(self.project_root, sid) if self.project_root else False, refresh_list()))
                action_row.addWidget(delete_btn)
                action_row.addStretch()
                card_layout.addLayout(action_row)
                list_layout.addWidget(card)
            list_layout.addStretch()

        form = QFrame()
        form.setStyleSheet(f"QFrame {{ background: {COLORS['surface_alt']}; border: 1px solid {COLORS['border']}; border-radius: 10px; }}")
        form_layout = QVBoxLayout(form)
        form_layout.setContentsMargins(12, 10, 12, 10)
        form_layout.setSpacing(8)
        form_title = QLabel("新增计划")
        form_title.setStyleSheet(f"color: {COLORS['text']}; background: transparent; font-size: 13px; font-weight: 900; border: none;")
        form_layout.addWidget(form_title)
        title_input = QLineEdit()
        title_input.setPlaceholderText("计划名称")
        prompt_input = QTextEdit()
        prompt_input.setPlaceholderText("到点后真正要让智能体做什么")
        prompt_input.setFixedHeight(72)
        run_at_input = QLineEdit()
        run_at_input.setPlaceholderText("首次触发时间，例如 2026-05-01 18:00:00")
        default_run_at = (datetime.now() + timedelta(hours=1)).replace(second=0, microsecond=0)
        run_at_input.setText(default_run_at.strftime("%Y-%m-%d %H:%M:%S"))
        repeat_row = QHBoxLayout()
        repeat_value_input = QLineEdit()
        repeat_value_input.setPlaceholderText("重复间隔")
        repeat_unit_input = QComboBox()
        repeat_unit_input.addItem("不重复", 0)
        repeat_unit_input.addItem("分钟", 60)
        repeat_unit_input.addItem("小时", 3600)
        repeat_unit_input.addItem("天", 86400)
        repeat_unit_input.addItem("周", 604800)
        repeat_unit_input.setCurrentIndex(3)
        repeat_value_input.setText("1")
        until_at_input = QLineEdit()
        until_at_input.setPlaceholderText("可选截止时间，例如 2026-05-02 18:00:00")
        for editor in (title_input, run_at_input, repeat_value_input, repeat_unit_input, until_at_input):
            editor.setFixedHeight(34)
        editor_style = f"background: {COLORS['surface']}; color: {COLORS['text']}; border: 1px solid {COLORS['border']}; border-radius: 9px; padding: 7px 10px; font-size: 12px; font-weight: 800;"
        for editor in (title_input, run_at_input, repeat_value_input, until_at_input, prompt_input):
            editor.setStyleSheet(f"background: {COLORS['surface']}; color: {COLORS['text']}; border: 1px solid {COLORS['border']}; border-radius: 9px; padding: 7px 10px; font-size: 12px; font-weight: 800;")
        repeat_unit_input.setStyleSheet(editor_style)
        repeat_row.addWidget(repeat_value_input, 1)
        repeat_row.addWidget(repeat_unit_input, 1)
        form_layout.addWidget(title_input)
        form_layout.addWidget(run_at_input)
        form_layout.addLayout(repeat_row)
        form_layout.addWidget(until_at_input)
        form_layout.addWidget(prompt_input)
        add_btn = QPushButton("新增计划", cursor=Qt.CursorShape.PointingHandCursor)
        add_btn.setFixedHeight(36)
        add_btn.setStyleSheet(button_style(primary=True))

        def add_schedule():
            run_at = format_schedule_datetime(run_at_input.text().strip())
            if not run_at:
                styled_warning(dialog, "定时计划", "首次触发时间格式不正确。请使用 2026-05-01 18:00:00。")
                return
            prompt = prompt_input.toPlainText().strip()
            if not prompt:
                styled_warning(dialog, "定时计划", "请填写计划内容。")
                return
            repeat_seconds = 0
            unit_seconds = int(repeat_unit_input.currentData() or 0)
            if unit_seconds:
                try:
                    repeat_value = int(repeat_value_input.text().strip() or "0")
                except ValueError:
                    styled_warning(dialog, "定时计划", "重复间隔必须是数字。")
                    return
                if repeat_value <= 0:
                    styled_warning(dialog, "定时计划", "重复间隔必须大于 0，或选择“不重复”。")
                    return
                repeat_seconds = repeat_value * unit_seconds
            until_at_raw = until_at_input.text().strip()
            until_at = format_schedule_datetime(until_at_raw) if until_at_raw else ""
            if until_at_raw and not until_at:
                styled_warning(dialog, "定时计划", "截止时间格式不正确。请使用 2026-05-02 18:00:00。")
                return
            schedule_spec: Dict[str, object] = {"run_at": run_at}
            if repeat_seconds:
                schedule_spec["repeat_every_seconds"] = repeat_seconds
            if until_at:
                schedule_spec["until_at"] = until_at
            try:
                add_btn.setEnabled(False)
                add_btn.setText("新增中...")
                create_workspace_schedule_from_spec(
                    self.project_root,
                    title_input.text().strip() or prompt[:36],
                    prompt,
                    schedule_spec,
                )
                title_input.clear()
                prompt_input.clear()
                until_at_input.clear()
                refresh_list()
            except Exception as exc:
                styled_warning(dialog, "定时计划", str(exc))
                return
            finally:
                add_btn.setEnabled(True)
                add_btn.setText("新增计划")

        add_btn.clicked.connect(add_schedule)
        form_layout.addWidget(add_btn)
        layout.addWidget(form)

        close_row = QHBoxLayout()
        close_row.addStretch()
        close_btn = QPushButton("关闭", cursor=Qt.CursorShape.PointingHandCursor)
        close_btn.setFixedHeight(34)
        close_btn.setStyleSheet(button_style())
        close_btn.clicked.connect(dialog.close)
        close_row.addWidget(close_btn)
        layout.addLayout(close_row)
        refresh_list()
        dialog.exec()

    def show_settings_menu(self):
        if agent_runtime_enabled() and not agent_runtime_ready():
            set_agent_runtime_enabled(False)
        menu = style_compact_popup_menu(QMenu(self))
        self._settings_menu = menu
        runtime_toggle = SettingsToggleRow(
            "Python 运行环境",
            "让命令优先使用 Agent 缓存 Python",
            agent_runtime_enabled(),
            parent=menu,
        )
        runtime_toggle.toggled.connect(lambda enabled, row=runtime_toggle: self.set_python_runtime_enabled(enabled, row))
        runtime_toggle_action = QWidgetAction(menu)
        runtime_toggle_action.setDefaultWidget(runtime_toggle)
        menu.addAction(runtime_toggle_action)

        automation_toggle = SettingsToggleRow(
            "自动化插件",
            "后台自动复制与执行循环",
            self.automation_enabled,
            parent=menu,
        )
        automation_toggle.toggled.connect(lambda enabled, row=automation_toggle: self.set_automation_enabled(enabled, row))
        automation_toggle_action = QWidgetAction(menu)
        automation_toggle_action.setDefaultWidget(automation_toggle)
        menu.addAction(automation_toggle_action)

        wechat_bridge_toggle = SettingsToggleRow(
            "微信远控",
            "开启微信手机端控制智能体的功能",
            wechat_bridge_enabled_setting(),
            parent=menu,
        )
        wechat_bridge_toggle.toggled.connect(lambda enabled, row=wechat_bridge_toggle: self.set_wechat_bridge_enabled(enabled, row))
        wechat_bridge_toggle_action = QWidgetAction(menu)
        wechat_bridge_toggle_action.setDefaultWidget(wechat_bridge_toggle)
        menu.addAction(wechat_bridge_toggle_action)

        developer_toggle = SettingsToggleRow(
            "开发者模式",
            "显示完整代码块和详细错误信息",
            developer_mode_enabled(),
            parent=menu,
        )
        developer_toggle.toggled.connect(lambda enabled, row=developer_toggle: self.set_developer_mode(enabled, row))
        developer_toggle_action = QWidgetAction(menu)
        developer_toggle_action.setDefaultWidget(developer_toggle)
        menu.addAction(developer_toggle_action)
        menu.addSeparator()

        runtime_menu = style_compact_popup_menu(menu.addMenu("Python 运行环境"))
        runtime_status_action = QAction("检查运行环境", self)
        runtime_status_action.triggered.connect(self.show_python_runtime_status)
        runtime_menu.addAction(runtime_status_action)
        runtime_install_action = QAction("创建/修复 Agent Python 环境", self)
        runtime_install_action.triggered.connect(lambda: self.run_python_runtime_setup("install"))
        runtime_menu.addAction(runtime_install_action)
        runtime_open_action = QAction("打开运行环境目录", self)
        runtime_open_action.triggered.connect(self.open_python_runtime_dir)
        runtime_menu.addAction(runtime_open_action)

        automation_menu = style_compact_popup_menu(menu.addMenu("自动化插件"))
        login_action = QAction("打开网页登录", self)
        login_action.triggered.connect(lambda: self.run_automation_setup("login"))
        automation_menu.addAction(login_action)
        install_action = QAction("安装/修复", self)
        install_action.triggered.connect(lambda: self.run_automation_setup("install"))
        automation_menu.addAction(install_action)
        automation_menu.addSeparator()

        model_menu = style_compact_popup_menu(automation_menu.addMenu("模型"))
        for label, model_id in AUTOMATION_MODELS:
            action = QAction(label, self)
            action.setCheckable(True)
            action.setChecked(self.automation_model == model_id)
            action.triggered.connect(lambda _checked=False, value=model_id: self.set_automation_model(value))
            model_menu.addAction(action)

        rounds_menu = style_compact_popup_menu(automation_menu.addMenu("最大轮数"))
        for rounds in (8, 12, 20, 50, 100):
            action = QAction(f"{rounds} 轮", self)
            action.setCheckable(True)
            action.setChecked(self.automation_loop_max_rounds == rounds)
            action.triggered.connect(lambda _checked=False, value=rounds: self.set_automation_max_rounds(value))
            rounds_menu.addAction(action)

        automation_menu.addSeparator()
        diagnostics_menu = style_compact_popup_menu(automation_menu.addMenu("高级诊断"))
        status_action = QAction("检查状态", self)
        status_action.triggered.connect(self.show_automation_status)
        diagnostics_menu.addAction(status_action)
        open_log_action = QAction("打开日志", self)
        open_log_action.triggered.connect(self.open_automation_log_file)
        diagnostics_menu.addAction(open_log_action)
        copy_log_action = QAction("复制日志路径", self)
        copy_log_action.triggered.connect(self.copy_automation_log_path)
        diagnostics_menu.addAction(copy_log_action)
        open_plugin_dir_action = QAction("打开插件目录", self)
        open_plugin_dir_action.triggered.connect(self.open_automation_plugin_dir)
        diagnostics_menu.addAction(open_plugin_dir_action)
        copy_plugin_dir_action = QAction("复制插件目录路径", self)
        copy_plugin_dir_action.triggered.connect(self.copy_automation_plugin_dir)
        diagnostics_menu.addAction(copy_plugin_dir_action)
        provider_action = QAction("Provider 查看", self)
        provider_action.triggered.connect(self.show_provider_info_dialog)
        diagnostics_menu.addAction(provider_action)

        wechat_config_action = QAction("微信配置", self)
        wechat_config_action.triggered.connect(self.show_wechat_config_dialog)
        menu.addAction(wechat_config_action)

        schedules_action = QAction("定时计划", self)
        schedules_action.triggered.connect(self.show_schedules_dialog)
        menu.addAction(schedules_action)

        menu.addSeparator()
        home_action = QAction("返回首页", self)
        home_action.triggered.connect(self.confirm_back_home)
        menu.addAction(home_action)
        menu.addSeparator()
        preferences_action = QAction("偏好设置", self)
        preferences_action.triggered.connect(self.show_preferences_dialog)
        menu.addAction(preferences_action)
        menu.aboutToHide.connect(lambda menu=menu: setattr(self, "_settings_menu", None))
        menu.popup(self.settings_btn.mapToGlobal(self.settings_btn.rect().bottomRight()))

    def set_automation_model(self, model_id: str):
        self.automation_model = model_id
        self.update_automation_context_mode_button()
        self.update_automation_composer_state()

    def set_automation_max_rounds(self, rounds: int):
        self.automation_loop_max_rounds = max(1, int(rounds))

    def set_developer_mode(self, enabled: bool, toggle_row: Optional[SettingsToggleRow] = None):
        set_developer_mode_enabled(enabled)
        self.apply_chat_visual_settings()
        self.add_status_bubble("开发者模式已开启：显示完整代码块和详细错误。" if enabled else "开发者模式已关闭：默认折叠代码块并隐藏长错误细节。")

    def set_python_runtime_enabled(self, enabled: bool, toggle_row: Optional[SettingsToggleRow] = None):
        if enabled and not agent_runtime_ready():
            set_agent_runtime_enabled(False)
            if toggle_row is not None:
                toggle_row.setChecked(False)
            styled_warning(
                self,
                "Python 运行环境",
                "Agent Python 运行环境尚未安装。已开始创建/修复环境，安装成功后才会启用这个开关。",
            )
            self.run_python_runtime_setup("install")
            return
        set_agent_runtime_enabled(enabled)
        self.refresh_prompt_bubble_buttons()
        self.update_prompt_tools_responsive()

    def set_automation_enabled(self, enabled: bool, toggle_row: Optional[SettingsToggleRow] = None):
        if not enabled:
            self.automation_enabled = False
            set_automation_enabled_setting(False)
            self.automation_loop_active = False
            self.automation_loop_round = 0
            self.automation_loop_goal = ""
            self.stop_automation_preview(remove_bubble=True)
            self.hide_automation_composer()
            self.load_history()
            self.update_prompt_tools_responsive()
            return
        dep = self.automation_manager.dependency_status()
        if not dep.get("ready"):
            self.automation_enabled = False
            set_automation_enabled_setting(False)
            if toggle_row is not None:
                toggle_row.setChecked(False)
            styled_warning(
                self,
                "自动化插件",
                "自动化插件依赖尚未就绪。请先点击“安装/修复插件依赖”，安装成功后再开启这个开关。\n\n"
                + str(dep.get("message") or ""),
            )
            return
        if toggle_row is not None:
            toggle_row.setChecked(True)
        self.automation_enabled = True
        set_automation_enabled_setting(True)
        self.stop_automation_preview(remove_bubble=True)
        self.load_history()
        self.update_prompt_tools_responsive()
        self.run_automation_setup("start")

    def show_automation_status(self):
        styled_warning(self, "自动化插件状态", self.automation_manager.status_text())

    def provider_info_payload(self) -> Dict[str, object]:
        return {
            "api_url": self.automation_manager.base_url,
            "chat_completions_url": f"{self.automation_manager.base_url}/v1/chat/completions",
            "responses_url": f"{self.automation_manager.base_url}/v1/responses",
            "models_url": f"{self.automation_manager.base_url}/v1/models",
            "api_key": "not-required",
            "model": self.effective_automation_model(),
            "status": "running" if self.automation_manager.health() else "stopped",
        }

    def show_provider_info_dialog(self):
        payload = self.provider_info_payload()
        dialog = QDialog(self)
        dialog.setWindowTitle("Provider 查看")
        dialog.setMinimumWidth(560)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)
        title = QLabel("OpenAI-compatible Provider")
        title.setStyleSheet(f"color: {COLORS['text']}; background: transparent; font-size: 15px; font-weight: 900;")
        layout.addWidget(title)
        text = QPlainTextEdit()
        text.setReadOnly(True)
        text.setPlainText(json.dumps(payload, ensure_ascii=False, indent=2))
        text.setFixedHeight(210)
        text.setStyleSheet(f"""
            QPlainTextEdit {{
                background: {COLORS['surface_alt']};
                color: {COLORS['text']};
                border: 1px solid {COLORS['border']};
                border-radius: 10px;
                padding: 10px;
                font-size: 12px;
            }}
        """)
        layout.addWidget(text)
        row = QHBoxLayout()
        row.addStretch()
        copy_btn = QPushButton("复制", cursor=Qt.CursorShape.PointingHandCursor)
        copy_btn.clicked.connect(lambda: QApplication.clipboard().setText(text.toPlainText()))
        close_btn = QPushButton("关闭", cursor=Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(dialog.close)
        for btn in (copy_btn, close_btn):
            btn.setFixedHeight(34)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: {COLORS['surface']};
                    color: {COLORS['text']};
                    border: 1px solid {COLORS['border']};
                    border-radius: 10px;
                    padding: 6px 16px;
                    font-size: 12px;
                    font-weight: 900;
                }}
                QPushButton:hover {{ background: {COLORS['surface_alt']}; }}
            """)
            row.addWidget(btn)
        layout.addLayout(row)
        dialog.exec()

    def start_wechat_bridge_quietly(self):
        try:
            self.wechat_bridge.start()
            set_wechat_bridge_enabled_setting(True)
            self.ensure_wechat_connector_autostart()
        except Exception:
            logger.warning("WeChat bridge startup failed.", exc_info=True)
            set_wechat_bridge_enabled_setting(False)

    def start_console_bridge_quietly(self):
        try:
            self.wechat_bridge.start()
        except Exception:
            logger.warning("Console bridge startup failed.", exc_info=True)

    def start_wechat_bridge_from_menu(self):
        try:
            url = self.wechat_bridge.start()
            set_wechat_bridge_enabled_setting(True)
            self.ensure_wechat_connector_autostart()
            self.add_status_bubble(f"微信 Bridge 已启动：{url}")
        except Exception as exc:
            set_wechat_bridge_enabled_setting(False)
            styled_warning(self, "微信 Bridge", str(exc))

    def stop_wechat_bridge_from_menu(self):
        self.wechat_bridge.stop()
        set_wechat_bridge_enabled_setting(False)
        self.add_status_bubble("微信 Bridge 已停止。")

    def set_wechat_bridge_enabled(self, enabled: bool, toggle_row: Optional[SettingsToggleRow] = None):
        if enabled:
            try:
                url = self.wechat_bridge.start()
                set_wechat_bridge_enabled_setting(True)
                self.ensure_wechat_connector_autostart()
                if toggle_row is not None:
                    toggle_row.setChecked(True)
                self.add_status_bubble(f"微信本地接口已开启：{url}")
            except Exception as exc:
                set_wechat_bridge_enabled_setting(False)
                if toggle_row is not None:
                    toggle_row.setChecked(False)
                styled_warning(self, "微信接入", str(exc))
            return
        self.wechat_bridge.stop()
        set_wechat_bridge_enabled_setting(False)
        if toggle_row is not None:
            toggle_row.setChecked(False)
        self.add_status_bubble("微信本地接口已关闭。")

    def copy_wechat_bridge_url(self):
        QApplication.clipboard().setText(self.wechat_bridge.url())
        self.add_status_bubble(f"已复制微信 Bridge 地址：{self.wechat_bridge.url()}")

    def start_wechat_connector_login(self):
        self.start_wechat_bridge_quietly()
        self.wechat_connector.login_async()

    def ensure_wechat_connector_autostart(self):
        if not wechat_connector_autostart_setting():
            return
        if not self.wechat_connector.account():
            return
        QTimer.singleShot(150, self.wechat_connector.start)

    def start_wechat_connector_from_menu(self):
        self.start_wechat_bridge_quietly()
        set_wechat_connector_autostart_setting(True)
        self.wechat_connector.start()

    def stop_wechat_connector_from_menu(self):
        set_wechat_connector_autostart_setting(False)
        self.wechat_connector.stop()

    def show_wechat_qr_dialog(self, qr_url: str):
        self.wechat_qr_current_url = str(qr_url or "").strip()
        dialog = self.wechat_qr_dialog
        if dialog is None:
            dialog = QDialog(self)
            dialog.setWindowTitle("微信扫码登录")
            dialog.setMinimumWidth(520)
            dialog.setModal(False)
            dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
            layout = QVBoxLayout(dialog)
            layout.setContentsMargins(14, 14, 14, 14)
            layout.setSpacing(10)
            title = QLabel("用微信扫描二维码链接")
            title.setStyleSheet(f"color: {COLORS['text']}; background: transparent; font-size: 15px; font-weight: 900;")
            layout.addWidget(title)
            hint = QLabel("如果没有直接显示二维码，点“打开二维码链接”，用微信扫码后在手机上确认。")
            hint.setWordWrap(True)
            hint.setStyleSheet(f"color: {COLORS['text_secondary']}; background: transparent; font-size: 12px;")
            layout.addWidget(hint)
            image_label = QLabel("二维码加载中...")
            image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            image_label.setFixedSize(260, 260)
            image_label.setStyleSheet(f"""
                QLabel {{
                    background: {COLORS['surface']};
                    color: {COLORS['text_secondary']};
                    border: 1px solid {COLORS['border']};
                    border-radius: 14px;
                    padding: 10px;
                    font-size: 12px;
                }}
            """)
            image_row = QHBoxLayout()
            image_row.addStretch()
            image_row.addWidget(image_label)
            image_row.addStretch()
            layout.addLayout(image_row)
            success_hint = QLabel("如果扫码成功，微信已创建 ClawBot，可关闭此页面。")
            success_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
            success_hint.setStyleSheet(f"color: {COLORS['text_secondary']}; background: transparent; font-size: 12px; font-weight: 800;")
            layout.addWidget(success_hint)
            link = QPlainTextEdit()
            link.setReadOnly(True)
            link.setFixedHeight(90)
            link.setStyleSheet(f"""
                QPlainTextEdit {{
                    background: {COLORS['surface_alt']};
                    color: {COLORS['text']};
                    border: 1px solid {COLORS['border']};
                    border-radius: 10px;
                    padding: 10px;
                    font-size: 12px;
                }}
            """)
            layout.addWidget(link)
            row = QHBoxLayout()
            row.addStretch()
            open_btn = QPushButton("打开二维码链接", cursor=Qt.CursorShape.PointingHandCursor)
            copy_btn = QPushButton("复制链接", cursor=Qt.CursorShape.PointingHandCursor)
            close_btn = QPushButton("关闭", cursor=Qt.CursorShape.PointingHandCursor)
            for btn in (open_btn, copy_btn, close_btn):
                btn.setFixedHeight(34)
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: {COLORS['surface']};
                        color: {COLORS['text']};
                        border: 1px solid {COLORS['border']};
                        border-radius: 10px;
                        padding: 6px 16px;
                        font-size: 12px;
                        font-weight: 900;
                    }}
                    QPushButton:hover {{ background: {COLORS['surface_alt']}; }}
                """)
                row.addWidget(btn)
            open_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(self.wechat_qr_current_url)))
            copy_btn.clicked.connect(lambda: QApplication.clipboard().setText(self.wechat_qr_current_url))
            close_btn.clicked.connect(dialog.close)
            layout.addLayout(row)
            self.wechat_qr_dialog = dialog
            self.wechat_qr_image_label = image_label
            self.wechat_qr_link_edit = link
            self.wechat_qr_open_btn = open_btn

        image_label = self.wechat_qr_image_label
        link = self.wechat_qr_link_edit
        if image_label is not None:
            image_label.setPixmap(QPixmap())
            image_label.setText("二维码加载中...")
        if link is not None:
            link.setPlainText(self.wechat_qr_current_url)

        worker = WeChatQrImageWorker(self.wechat_qr_current_url)
        self.wechat_qr_image_workers.append(worker)

        def on_loaded(url: str, data: bytes, error: str):
            current_label = self.wechat_qr_image_label
            if current_label is None:
                return
            if url != self.wechat_qr_current_url:
                try:
                    self.wechat_qr_image_workers.remove(worker)
                except ValueError:
                    pass
                worker.deleteLater()
                return
            if error or not data:
                current_label.setText("二维码图片加载失败\n请打开或复制下方链接")
            else:
                pixmap = QPixmap()
                if pixmap.loadFromData(QByteArray(data)):
                    target_size = QSize(max(1, current_label.width() - 20), max(1, current_label.height() - 20))
                    current_label.setPixmap(pixmap.scaled(
                        target_size,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    ))
                else:
                    current_label.setText("二维码图片解析失败\n请打开或复制下方链接")
            try:
                self.wechat_qr_image_workers.remove(worker)
            except ValueError:
                pass
            worker.deleteLater()

        worker.loaded.connect(on_loaded)
        worker.start()
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def show_wechat_config_dialog(self):
        cfg = wechat_bridge_settings()
        dialog = QDialog(self)
        dialog.setWindowTitle("微信机器人配置")
        dialog.setMinimumWidth(560)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)
        hint = QLabel("扫码登录后启动连接器，即可用微信消息驱动当前工作区。")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {COLORS['text_secondary']}; background: transparent; font-size: 12px; font-weight: 800;")
        layout.addWidget(hint)

        login_status = QLabel(f"当前登录状态：{wechat_connector_state_text()}")
        login_status.setStyleSheet(f"color: {COLORS['text']}; background: transparent; font-size: 13px; font-weight: 900;")
        layout.addWidget(login_status)

        primary_row = QHBoxLayout()
        primary_row.setContentsMargins(0, 4, 0, 4)
        primary_row.setSpacing(10)
        login_btn = QPushButton("扫码登录微信", cursor=Qt.CursorShape.PointingHandCursor)
        start_connector_btn = QPushButton("启动内置连接器", cursor=Qt.CursorShape.PointingHandCursor)
        stop_connector_btn = QPushButton("停止连接器", cursor=Qt.CursorShape.PointingHandCursor)
        for btn in (login_btn, start_connector_btn):
            btn.setMinimumHeight(44)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: {COLORS['success']};
                    color: white;
                    border: none;
                    border-radius: 12px;
                    padding: 9px 18px;
                    font-size: 13px;
                    font-weight: 900;
                }}
                QPushButton:hover {{ background: #0f8a59; }}
            """)
            primary_row.addWidget(btn, 1)
        stop_connector_btn.setMinimumHeight(44)
        stop_connector_btn.setStyleSheet(f"""
            QPushButton {{
                background: {COLORS['surface']};
                color: {COLORS['text']};
                border: 1px solid {COLORS['border']};
                border-radius: 12px;
                padding: 9px 18px;
                font-size: 13px;
                font-weight: 900;
            }}
            QPushButton:hover {{ background: {COLORS['surface_alt']}; }}
        """)
        primary_row.addWidget(stop_connector_btn, 1)
        layout.addLayout(primary_row)

        advanced_btn = QToolButton(cursor=Qt.CursorShape.PointingHandCursor)
        advanced_btn.setText("高级设置")
        advanced_btn.setCheckable(True)
        advanced_btn.setToolButtonStyle(Qt.ToolButtonTextOnly)
        advanced_btn.setFixedHeight(28)
        advanced_btn.setStyleSheet(f"""
            QToolButton {{
                background: transparent;
                color: {COLORS['text_secondary']};
                border: none;
                font-size: 12px;
                font-weight: 900;
                text-align: left;
            }}
            QToolButton:hover {{ color: {COLORS['text']}; }}
        """)
        layout.addWidget(advanced_btn, 0, Qt.AlignmentFlag.AlignLeft)

        advanced_panel = QWidget(dialog)
        advanced_panel.setVisible(False)
        advanced_panel.setStyleSheet("background: transparent;")
        advanced_layout = QVBoxLayout(advanced_panel)
        advanced_layout.setContentsMargins(0, 0, 0, 0)
        advanced_layout.setSpacing(8)

        silent_box = QCheckBox("静默模式：不把代码块/命令块正文回给微信")
        silent_box.setChecked(bool(cfg.get("silent", True)))
        silent_box.setStyleSheet(f"color: {COLORS['text']}; background: transparent; font-size: 12px; font-weight: 800;")
        advanced_layout.addWidget(silent_box)

        host_edit = QLineEdit(str(cfg["host"]))
        port_edit = QLineEdit(str(cfg["port"]))
        key_edit = QLineEdit(str(cfg.get("api_key") or ""))
        timeout_edit = QLineEdit(str(cfg.get("timeout_seconds") or 900))
        fields_grid = QGridLayout()
        fields_grid.setContentsMargins(0, 0, 0, 0)
        fields_grid.setHorizontalSpacing(10)
        fields_grid.setVerticalSpacing(8)
        fields = [
            ("Host", host_edit, 0, 0),
            ("Port", port_edit, 0, 2),
            ("API Key（可空）", key_edit, 1, 0),
            ("等待超时秒数", timeout_edit, 1, 2),
        ]
        for label, editor, row_index, col_index in fields:
            lab = QLabel(label)
            lab.setFixedWidth(92)
            lab.setStyleSheet(f"color: {COLORS['text_secondary']}; background: transparent; font-size: 12px; font-weight: 800;")
            editor.setStyleSheet(f"""
                QLineEdit {{
                    background: {COLORS['surface']};
                    color: {COLORS['text']};
                    border: 1px solid {COLORS['border']};
                    border-radius: 9px;
                    padding: 8px 10px;
                    font-size: 12px;
                }}
            """)
            fields_grid.addWidget(lab, row_index, col_index)
            fields_grid.addWidget(editor, row_index, col_index + 1)
        fields_grid.setColumnStretch(1, 1)
        fields_grid.setColumnStretch(3, 1)
        advanced_layout.addLayout(fields_grid)

        endpoint = QPlainTextEdit()
        endpoint.setReadOnly(True)
        endpoint.setPlainText(
            f"Base URL: http://{cfg['host']}:{cfg['port']}/v1\n"
            "Model: agent-qt-wechat\n"
            f"POST http://{cfg['host']}:{cfg['port']}/message\n"
            '{"text":"帮我继续完成项目","thread_id":"default","silent":true}'
        )
        endpoint.setFixedHeight(92)
        endpoint.setStyleSheet(f"""
            QPlainTextEdit {{
                background: {COLORS['surface_alt']};
                color: {COLORS['text']};
                border: 1px solid {COLORS['border']};
                border-radius: 10px;
                padding: 10px;
                font-size: 12px;
            }}
        """)
        advanced_layout.addWidget(endpoint)
        layout.addWidget(advanced_panel)
        advanced_btn.toggled.connect(advanced_panel.setVisible)

        row = QHBoxLayout()
        row.addStretch()
        save_btn = QPushButton("保存配置", cursor=Qt.CursorShape.PointingHandCursor)
        clear_login_btn = QPushButton("清除登录", cursor=Qt.CursorShape.PointingHandCursor)
        copy_btn = QPushButton("复制示例", cursor=Qt.CursorShape.PointingHandCursor)
        close_btn = QPushButton("关闭", cursor=Qt.CursorShape.PointingHandCursor)
        for btn in (save_btn, clear_login_btn, copy_btn, close_btn):
            btn.setFixedHeight(34)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: {COLORS['surface']};
                    color: {COLORS['text']};
                    border: 1px solid {COLORS['border']};
                    border-radius: 10px;
                    padding: 6px 16px;
                    font-size: 12px;
                    font-weight: 900;
                }}
                QPushButton:hover {{ background: {COLORS['surface_alt']}; }}
            """)
            row.addWidget(btn)

        def save():
            try:
                payload = {
                    "host": host_edit.text().strip() or "127.0.0.1",
                    "port": int(port_edit.text().strip() or "8798"),
                    "api_key": key_edit.text().strip(),
                    "silent": silent_box.isChecked(),
                    "timeout_seconds": int(timeout_edit.text().strip() or "900"),
                }
            except ValueError:
                styled_warning(dialog, "微信配置", "端口和超时必须是数字。")
                return
            set_wechat_bridge_settings(payload)
            dialog.close()

        save_btn.clicked.connect(save)
        login_btn.clicked.connect(self.start_wechat_connector_login)
        start_connector_btn.clicked.connect(self.start_wechat_connector_from_menu)
        stop_connector_btn.clicked.connect(self.stop_wechat_connector_from_menu)
        clear_login_btn.clicked.connect(self.wechat_connector.clear_login)
        copy_btn.clicked.connect(lambda: QApplication.clipboard().setText(endpoint.toPlainText()))
        close_btn.clicked.connect(dialog.close)
        layout.addLayout(row)
        dialog.exec()

    def open_automation_log_file(self):
        path = self.automation_manager.log_file
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if not os.path.exists(path):
            with open(path, "w", encoding="utf-8") as f:
                f.write("")
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        self.add_status_bubble(f"插件日志：{path}")

    def copy_automation_log_path(self):
        path = self.automation_manager.log_file
        QApplication.clipboard().setText(path)
        self.add_status_bubble(f"已复制插件日志路径：{path}")

    def open_automation_plugin_dir(self):
        path = self.automation_manager.plugin_root
        os.makedirs(path, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        self.add_status_bubble(f"插件目录：{path}")

    def copy_automation_plugin_dir(self):
        path = self.automation_manager.plugin_root
        os.makedirs(path, exist_ok=True)
        QApplication.clipboard().setText(path)
        self.add_status_bubble(f"已复制插件目录路径：{path}")

    def show_python_runtime_status(self):
        styled_warning(self, "Python 运行环境", agent_runtime_status_text())

    def open_python_runtime_dir(self):
        path = runtime_cache_root()
        os.makedirs(path, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        self.add_status_bubble(f"Python 运行环境目录：{path}")

    def copy_python_runtime_path(self):
        python_bin = ensure_agent_runtime(create=False)
        text = python_bin or "未安装 Agent Python 运行环境，当前使用系统 PATH。"
        QApplication.clipboard().setText(text)
        self.add_status_bubble(f"已复制 Python 路径：{text}")

    def reset_primary_button_text(self):
        self.update_prompt_tools_responsive()

    def export_dir(self) -> str:
        path = os.path.join(project_cache_dir(self.project_root or os.path.expanduser("~")), "exports")
        os.makedirs(path, exist_ok=True)
        return path

    def conversation_export_text(self, fmt: str = "md") -> str:
        if fmt == "raw":
            lines: List[str] = [
                "Agent Qt Provider 原始记录",
                f"工作区: {self.project_root}",
                f"会话: {self.thread_id}",
                f"导出时间: {datetime.now().isoformat(timespec='seconds')}",
                "",
            ]
            raw_count = 0
            for index, entry in enumerate(self.history_entries, start=1):
                provider_io = entry.get("provider_io") if isinstance(entry, dict) else None
                if not isinstance(provider_io, dict):
                    continue
                raw_count += 1
                lines.append(f"===== Provider Round {raw_count} / History Entry {index} =====")
                lines.append(json.dumps(provider_io, ensure_ascii=False, indent=2))
                lines.append("")
            live_response = (self.automation_preview_pending_text or self.automation_preview_last_rendered_text or "").strip()
            if live_response:
                raw_count += 1
                lines.append(f"===== Provider Round {raw_count} / Live Interrupted Or Running =====")
                lines.append(json.dumps({
                    "created_at": datetime.now().isoformat(timespec="seconds"),
                    "model": self.automation_active_model or self.effective_automation_model(),
                    "thread_id": self.thread_id,
                    "messages": self.automation_active_messages,
                    "response": live_response,
                    "error": "live_or_interrupted",
                }, ensure_ascii=False, indent=2))
                lines.append("")
            if raw_count == 0:
                lines.append("当前会话历史里没有 provider_io 原始字段；可能是旧版本会话或非自动化对话。下面附上当前 history_entries 原始 JSON，便于排查。")
                lines.append("")
                lines.append(json.dumps(self.history_entries, ensure_ascii=False, indent=2))
                lines.append("")
            return "\n".join(lines).rstrip() + "\n"

        lines: List[str] = []
        if fmt == "md":
            lines.append("# Agent Qt 会话导出")
            lines.append("")
            lines.append(f"- 工作区: `{self.project_root}`")
            lines.append(f"- 会话: `{self.thread_id}`")
            lines.append(f"- 导出时间: {datetime.now().isoformat(timespec='seconds')}")
            lines.append("")
        else:
            lines.append("Agent Qt 会话导出")
            lines.append(f"工作区: {self.project_root}")
            lines.append(f"会话: {self.thread_id}")
            lines.append(f"导出时间: {datetime.now().isoformat(timespec='seconds')}")
            lines.append("")

        for entry in self.history_entries:
            entry_type = str(entry.get("type") or "")
            content = str(entry.get("content") or "").strip()
            if not content:
                continue
            if entry_type == "prompt":
                title = "用户需求"
                content = str(entry.get("context_content") or "").strip()
                if not content:
                    content = self.prompt_bubble_display_text(str(entry.get("content") or "")).strip() or self.prompt_text_from_system_prompt(str(entry.get("content") or "")).strip()
            elif entry_type == "ai":
                title = "AI 输出"
            elif entry_type == "result":
                title = "执行结果"
            elif entry_type == "terminal_result":
                title = "终端执行结果"
            elif entry_type == "provider_io":
                title = "Provider 请求失败"
            else:
                title = entry_type or "记录"
            if fmt == "md":
                lines.append(f"## {title}")
                lines.append("")
                lines.append(content)
                lines.append("")
            else:
                lines.append(f"===== {title} =====")
                lines.append(content)
                lines.append("")
        live_ai_text = (self.automation_preview_pending_text or self.automation_preview_last_rendered_text or "").strip()
        if live_ai_text:
            if fmt == "md":
                lines.append("## AI 输出")
                lines.append("")
                lines.append(live_ai_text)
                lines.append("")
            else:
                lines.append("===== AI 输出 =====")
                lines.append(live_ai_text)
                lines.append("")
        live_result_text = "\n\n".join(self.cmd_outputs).strip() if self.is_execution_running() else ""
        if live_result_text:
            if fmt == "md":
                lines.append("## 执行结果")
                lines.append("")
                lines.append(live_result_text)
                lines.append("")
            else:
                lines.append("===== 执行结果 =====")
                lines.append(live_result_text)
                lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    def export_conversation_text(self, fmt: str):
        ext = "md" if fmt == "md" else "txt"
        suffix = "-provider-raw" if fmt == "raw" else ""
        path = os.path.join(self.export_dir(), f"agent-qt-{self.thread_id}{suffix}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.{ext}")
        try:
            text = self.conversation_export_text(fmt)
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
        except Exception as exc:
            styled_warning(self, "导出失败", str(exc))
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path)))
        self.add_status_bubble(f"已导出：{path}")

    def export_conversation_screenshot(self):
        QApplication.processEvents()
        self.chat_column.adjustSize()
        self.chat_container.adjustSize()
        QApplication.processEvents()
        width = max(self.chat_column.width(), self.chat_column.sizeHint().width(), 640)
        height = max(self.chat_column.height(), self.chat_column.sizeHint().height(), 240)
        max_pixels = 120_000_000
        if width * height > max_pixels:
            styled_warning(self, "导出失败", f"会话太长，长截图约 {width}×{height}，已超过安全导出尺寸。请先分享为 Markdown 或 TXT。")
            return
        image = QImage(width, height, QImage.Format.Format_ARGB32)
        if image.isNull():
            styled_warning(self, "导出失败", "无法创建长截图画布。")
            return
        image.fill(QColor(COLORS["surface"]))
        painter = QPainter(image)
        if not painter.isActive():
            styled_warning(self, "导出失败", "无法开始绘制长截图。")
            return
        try:
            painter.translate((width - self.chat_column.width()) // 2, 0)
            self.chat_column.render(painter, QPoint(0, 0))
        finally:
            painter.end()
        path = os.path.join(self.export_dir(), f"agent-qt-{self.thread_id}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.png")
        if not image.save(path, "PNG"):
            styled_warning(self, "导出失败", "长截图保存失败。")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path)))
        self.add_status_bubble(f"已导出长截图：{path}")

    def show_share_menu(self):
        menu = style_compact_popup_menu(QMenu(self))
        md_action = QAction("分享为 Markdown (.md)", self)
        md_action.triggered.connect(lambda: self.export_conversation_text("md"))
        menu.addAction(md_action)
        txt_action = QAction("分享为 TXT (.txt)", self)
        txt_action.triggered.connect(lambda: self.export_conversation_text("txt"))
        menu.addAction(txt_action)
        raw_txt_action = QAction("分享原始 Provider TXT (.txt)", self)
        raw_txt_action.triggered.connect(lambda: self.export_conversation_text("raw"))
        menu.addAction(raw_txt_action)
        screenshot_action = QAction("分享为长截图 (.png)", self)
        screenshot_action.triggered.connect(self.export_conversation_screenshot)
        menu.addAction(screenshot_action)
        menu.exec(self.copy_prompt_btn.mapToGlobal(self.copy_prompt_btn.rect().bottomLeft()))

    def run_python_runtime_setup(self, action: str):
        if action == "install":
            self.run_python_runtime_install_terminal()
            return
        if self.python_runtime_setup_worker and self.python_runtime_setup_worker.isRunning():
            styled_warning(self, "Python 运行环境", "已有 Python 环境任务正在运行。")
            return
        worker = PythonRuntimeSetupWorker(action)
        self.python_runtime_setup_worker = worker

        def on_status(_message: str):
            self.copy_prompt_btn.setText("Python处理中")

        def on_finished(ok: bool, message: str):
            self.python_runtime_setup_worker = None
            self.update_prompt_tools_responsive()
            if ok:
                self.add_status_bubble(message)
            else:
                styled_warning(self, "Python 运行环境", message)
            worker.deleteLater()

        worker.status_signal.connect(on_status)
        worker.finished_signal.connect(on_finished)
        worker.start()

    def run_python_runtime_install_terminal(self):
        if (
            self.python_runtime_install_proc is not None
            and self.python_runtime_install_proc.process is not None
            and self.python_runtime_install_proc.process.state() != QProcess.ProcessState.NotRunning
        ):
            styled_warning(self, "Python 运行环境", "已有 Python 环境安装任务正在运行，请等待当前安装结束。")
            self.terminal_panel.select_process(self.python_runtime_install_proc)
            self.terminal_panel.expand()
            return
        cmd = build_python_runtime_install_command()
        cwd = runtime_cache_root()
        os.makedirs(cwd, exist_ok=True)
        proc = self.terminal_panel.add_process(cmd, cwd, "Python 环境安装", interactive=False)
        if proc is not None:
            self.python_runtime_install_proc = proc
            if proc.process is not None:
                proc.process.finished.connect(lambda code, _status: self.on_python_runtime_install_terminal_finished(code))
            self.terminal_panel.expand()
            self.update_status_bar()
            self.copy_prompt_btn.setText("Python安装中")

    def on_python_runtime_install_terminal_finished(self, exit_code: int):
        self.python_runtime_install_proc = None
        self.update_prompt_tools_responsive()
        if exit_code == 0:
            python_bin = ensure_agent_runtime(create=False)
            if python_bin:
                ensure_runtime_shims(python_bin)
                set_agent_runtime_enabled(True)
                self.refresh_prompt_bubble_buttons()
                self.update_prompt_tools_responsive()
                return
        set_agent_runtime_enabled(False)
        self.refresh_prompt_bubble_buttons()
        self.update_prompt_tools_responsive()

    def run_automation_setup(self, action: str):
        if action == "install":
            self.run_automation_install_terminal()
            return
        if self.automation_setup_worker and self.automation_setup_worker.isRunning():
            styled_warning(self, "自动化插件", "已有插件任务正在运行。")
            return
        worker = AutomationSetupWorker(self.automation_manager, action, self.automation_model)
        self.automation_setup_worker = worker

        def on_status(message: str):
            self.copy_prompt_btn.setText("插件处理中")

        def on_finished(ok: bool, message: str):
            self.automation_setup_worker = None
            self.update_prompt_tools_responsive()
            if ok:
                if action == "start":
                    self.automation_enabled = True
                    set_automation_enabled_setting(True)
                    self.show_automation_composer(focus=False)
                if action != "start":
                    self.add_status_bubble(message)
            else:
                self.automation_enabled = False if action == "start" else self.automation_enabled
                if action == "start":
                    set_automation_enabled_setting(False)
                    self.stop_automation_preview(remove_bubble=True)
                    self.hide_automation_composer()
                    self.load_history()
                self.refresh_prompt_bubble_buttons()
                styled_warning(self, "自动化插件", message)
            worker.deleteLater()

        worker.status_signal.connect(on_status)
        worker.finished_signal.connect(on_finished)
        worker.start()

    def run_automation_install_terminal(self):
        if not self.automation_manager.has_backend():
            styled_warning(self, "自动化插件", "未找到 provider 源码，无法安装插件依赖。")
            return
        cmd = self.automation_manager.install_dependencies_command()
        cwd = self.automation_manager.plugin_root
        os.makedirs(cwd, exist_ok=True)
        proc = self.terminal_panel.add_process(cmd, cwd, "插件依赖安装", interactive=False)
        if proc is not None:
            if proc.process is not None:
                proc.process.finished.connect(lambda _code, _status: self.update_prompt_tools_responsive())
            self.terminal_panel.expand()
            self.update_status_bar()
            self.copy_prompt_btn.setText("插件安装中")

    def delete_project_path(self, path: str):
        if not self.project_root:
            return
        if self.is_execution_running() or self.is_automation_busy():
            styled_warning(self, "正在执行", "当前还有本地命令或自动化任务在运行，先等它结束后再删除文件。")
            return

        root = os.path.abspath(self.project_root)
        target = os.path.abspath(path)
        try:
            inside_workspace = os.path.commonpath([root, target]) == root
        except ValueError:
            inside_workspace = False
        if not inside_workspace:
            styled_warning(self, "删除失败", "只能删除当前工作区内部的文件或文件夹。")
            return
        if target == root:
            styled_warning(self, "删除失败", "不能从文件树里删除整个工作区。")
            return
        if not os.path.lexists(target):
            styled_warning(self, "删除失败", "这个路径已经不存在。")
            self.sidebar.refresh_tree(self.project_root)
            return

        rel_path = os.path.relpath(target, root)
        is_dir = os.path.isdir(target) and not os.path.islink(target)
        text = (
            f"确定删除文件夹「{rel_path}」吗？\n\n文件夹内的内容会一起删除。"
            if is_dir
            else f"确定删除文件「{rel_path}」吗？"
        )
        ok = styled_confirm(
            self,
            "删除文件夹" if is_dir else "删除文件",
            text,
            confirm_text="删除",
            destructive=True,
        )
        if not ok:
            return

        try:
            if is_dir:
                shutil.rmtree(target)
            else:
                os.remove(target)
        except OSError as exc:
            styled_warning(self, "删除失败", str(exc))
            return
        self.sidebar.refresh_tree(self.project_root)
        self.add_status_bubble(f"已删除：{rel_path}")

    def refresh_prompt_bubble_buttons(self):
        for idx in range(self.chat_layout.count()):
            widget = self.chat_layout.itemAt(idx).widget()
            if isinstance(widget, ChatBubble) and getattr(widget, "role", "") == "user":
                widget.max_content_height = 90 if self.automation_enabled else 180
                widget.update_display_content(self.prompt_bubble_display_text(widget.content))
                paste_btn = getattr(widget, "paste_ai_btn", None)
                if paste_btn is not None:
                    paste_btn.setVisible(not self.automation_enabled)
                button = getattr(widget, "copy_btn", None)
                if button is not None:
                    if self.automation_loop_active:
                        widget.copy_text = "自动执行中"
                        button.setEnabled(False)
                    else:
                        widget.copy_text = "发送给 AI" if self.automation_enabled else "复制提示词"
                        button.setEnabled(True)
                    button.setText(widget.copy_text)

    def add_status_bubble(self, text: str):
        text = str(text or "").strip()
        now = time.time()
        if text and text == self._last_status_message and now - self._last_status_at < 3:
            return
        self._last_status_message = text
        self._last_status_at = now
        self.hide_empty_state()
        bubble = ExecutionLogPanel(
            text,
            parent=self.chat_container,
            max_content_height=120,
        )
        self.add_chat_widget(bubble, animate=True)
        self.scroll_to_bottom()

    def set_status_bar_override(self, text: str, duration_ms: int = 12000):
        self._status_bar_override_text = str(text or "").strip()
        self._status_bar_override_until = time.time() + max(1, int(duration_ms)) / 1000.0 if self._status_bar_override_text else 0.0
        self.terminal_panel.set_header_status(self._status_bar_override_text)
        self.update_status_bar()
        if self._status_bar_override_text:
            QTimer.singleShot(max(1, int(duration_ms)), self.update_status_bar)

    def handle_wechat_connector_status(self, text: str):
        message = str(text or "").strip()
        if not message:
            return
        lowered = message.lower()
        if any(token in lowered for token in ("微信连接器异常", "微信轮询失败", "微信回复发送失败", "二维码状态查询暂时失败")):
            self.set_status_bar_override(message, duration_ms=15000)
            return
        self.add_status_bubble(message)

    def add_execution_result_entry(self, content: str, *, context_content: str = ""):
        content = str(content or "").strip()
        if not content:
            return
        result_bubble = ExecutionLogPanel(
            content,
            parent=self.chat_container,
            max_content_height=210,
            title="执行结果",
        )
        self.add_chat_widget(result_bubble, animate=True)
        context = context_content or build_execution_context_content(content, [])
        self.append_history({
            "type": "result",
            "content": content,
            "context_content": context,
            "changes": [],
            "undone": False,
        })
        self.scroll_to_bottom()

    def send_wechat_files_to_last_target(self, file_targets: List[str]) -> tuple[List[str], List[str]]:
        sent_files: List[str] = []
        failed_files: List[str] = []
        if not file_targets:
            return sent_files, failed_files
        if not wechat_bridge_enabled_setting():
            return sent_files, ["微信本地接口未开启"]
        if not self.project_root:
            return sent_files, ["尚未打开工作区"]
        target_info = last_wechat_reply_target()
        to_user = str(target_info.get("to_user") or "").strip()
        context_token = str(target_info.get("context_token") or "").strip()
        if not to_user or not context_token:
            return sent_files, ["缺少最近微信回复上下文"]
        for target in file_targets[:3]:
            path = resolve_project_file_target(self.project_root, target)
            if not path:
                failed_files.append(f"未找到文件：{target}")
                continue
            try:
                self.wechat_connector._send_file(to_user, path, context_token)
                rel_path = os.path.relpath(path, self.project_root)
                sent_files.append(rel_path)
                if self.wechat_active_request_id:
                    self.wechat_active_sent_files.add(os.path.normcase(os.path.abspath(path)))
            except Exception as exc:
                failed_files.append(f"{target}（{exc}）")
        return sent_files, failed_files

    def run_web_research_responsive(self, query: str, provider_model: str) -> str:
        if QApplication.instance() is None or QThread.currentThread() is not QApplication.instance().thread():
            return self.automation_manager.web_research(
                query,
                provider_model,
                self.thread_id,
                thinking_enabled=automation_thinking_enabled(self.automation_model),
                expert_mode_enabled=(self.automation_context_mode == "expert"),
            )
        result: Dict[str, object] = {"done": False, "summary": "", "error": None}

        def _worker():
            try:
                result["summary"] = self.automation_manager.web_research(
                    query,
                    provider_model,
                    self.thread_id,
                    thinking_enabled=automation_thinking_enabled(self.automation_model),
                    expert_mode_enabled=(self.automation_context_mode == "expert"),
                )
            except Exception as exc:
                result["error"] = exc
            finally:
                result["done"] = True

        thread = threading.Thread(target=_worker, name="AgentQtWebResearch", daemon=True)
        thread.start()
        while not bool(result.get("done")):
            QApplication.processEvents()
            time.sleep(0.03)
        error = result.get("error")
        if error:
            raise error
        return str(result.get("summary") or "")


    def execute_terminal_extension_directives(self, directives: List[str]) -> str:
        cleaned_directives = [str(item or "").strip() for item in directives if str(item or "").strip()]
        if not cleaned_directives:
            return ""
        schedule_payloads, schedule_actions, schedule_errors = collect_schedule_extension_payloads(
            "\n".join(cleaned_directives)
        )
        file_targets = extract_wechat_send_file_targets("\n".join(cleaned_directives))
        web_research_queries = extract_web_research_queries("\n".join(cleaned_directives))
        skill_list_requested = any(
            directive.strip().upper() == "AGENT_SKILL_LIST"
            for directive in cleaned_directives
        )
        output_parts: List[str] = []
        if schedule_payloads or schedule_actions or schedule_errors:
            created: List[str] = []
            action_replies: List[str] = []
            errors: List[str] = list(schedule_errors)
            if self.project_root:
                c, a, e = apply_schedule_extension_payloads(
                    self.project_root,
                    schedule_payloads,
                    schedule_actions,
                )
                created.extend(c)
                action_replies.extend(a)
                errors.extend(e)
                if c or a:
                    QTimer.singleShot(1200, self.check_due_schedules)
            else:
                errors.append("未设置工作区，无法处理定时计划。")
            reply = schedule_extension_reply(created, action_replies, errors) or "已处理定时计划。"
            output_parts.append(reply)
        if file_targets:
            sent_files, failed_files = self.send_wechat_files_to_last_target(file_targets)
            delivery_parts: List[str] = []
            if sent_files:
                delivery_parts.append("微信附件已发送：" + "、".join(sent_files))
            if failed_files:
                delivery_parts.append("微信附件发送失败：" + "；".join(failed_files))
            output_parts.append(
                (wechat_trigger_summary(cleaned_directives) + "\n\n" + "\n".join(delivery_parts or ["未发送附件。"])).strip()
            )
        if web_research_queries:
            provider_model = self.effective_automation_model()
            search_outputs: List[str] = []
            for query in web_research_queries[:3]:
                try:
                    summary = self.run_web_research_responsive(query, provider_model)
                    search_outputs.append(f"网页搜索：{query}\n{summary}".strip())
                except Exception as exc:
                    search_outputs.append(f"网页搜索：{query}\n搜索失败：{exc}".strip())
            output_parts.append("\n\n".join(part for part in search_outputs if part).strip())
        if skill_list_requested:
            if self.project_root:
                output_parts.append(skill_list_extension_reply(load_workspace_skills(self.project_root)))
            else:
                output_parts.append("未设置工作区，无法查看技能列表。")
        if not output_parts:
            output_parts.append("已处理终端扩展指令。")
        return terminal_extension_execution_log(cleaned_directives, "\n\n".join(part for part in output_parts if part).strip())

    def start_web_research_extension_run(self, directives: List[str]):
        cleaned_directives = [str(item or "").strip() for item in directives if str(item or "").strip()]
        queries = extract_web_research_queries("\n".join(cleaned_directives))
        if not cleaned_directives or not queries:
            return
        placeholder_lines = [
            "⏳ 网页搜索进行中...",
            "",
            "Commands:",
        ] + [f"$ {directive}" for directive in cleaned_directives]
        result_bubble = ExecutionLogPanel(
            "\n".join(placeholder_lines),
            parent=self.chat_container,
            max_content_height=210,
            title="执行结果",
        )
        self.add_chat_widget(result_bubble, animate=True)
        self.update_automation_composer_state()
        self.scroll_to_bottom()

        def start_worker(long_retry_round: int = 0):
            worker = WebResearchWorker(
                self.automation_manager,
                cleaned_directives,
                queries,
                self.effective_automation_model(),
                self.thread_id,
                thinking_enabled=automation_thinking_enabled(self.automation_model),
                expert_mode_enabled=(self.automation_context_mode == "expert"),
            )
            self.web_research_worker = worker
            self.update_automation_composer_state()

            def on_status(message: str):
                if self.web_research_worker is worker:
                    self.set_status_bar_override(message, duration_ms=12000)

            def on_finished(execution_text: str, error: str):
                if self.web_research_worker is not worker:
                    return
                self.web_research_worker = None
                worker.deleteLater()
                self.update_automation_composer_state()
                if error and looks_like_provider_transient_error(error) and long_retry_round < PROVIDER_LONG_RETRY_ATTEMPTS:
                    retry_round = long_retry_round + 1
                    wait_seconds = max(1, int(PROVIDER_LONG_RETRY_DELAY_MS / 1000))
                    retry_message = f"网页搜索短重试仍未恢复，{wait_seconds} 秒后进行第 {retry_round}/{PROVIDER_LONG_RETRY_ATTEMPTS} 轮后台重试…"
                    self.set_status_bar_override(retry_message, duration_ms=min(PROVIDER_LONG_RETRY_DELAY_MS + 8000, 180000))
                    result_bubble.update_content(
                        "\n".join(
                            placeholder_lines
                            + [
                                "",
                                f"Status:",
                                retry_message,
                            ]
                        )
                    )
                    if self.automation_loop_active:
                        QTimer.singleShot(PROVIDER_LONG_RETRY_DELAY_MS, lambda rr=retry_round: start_worker(rr))
                    return
                final_text = execution_text.strip()
                if error:
                    final_text = terminal_extension_execution_log(
                        cleaned_directives,
                        f"网页搜索失败：{error}",
                    )
                result_bubble.update_content(final_text)
                context_content = build_execution_context_content(final_text, [])
                self.append_history({
                    "type": "result",
                    "content": final_text,
                    "context_content": context_content,
                    "changes": [],
                    "undone": False,
                })
                if self.automation_loop_active:
                    QTimer.singleShot(0, lambda cc=context_content: self.request_next_automation_step(cc))
                elif self.automation_enabled:
                    self.show_automation_composer(focus=False)
                else:
                    self.ensure_ai_response_entry(focus=False, animate=True, keep_visible=False)
                self.scroll_to_bottom()

            worker.status_signal.connect(on_status)
            worker.finished_signal.connect(on_finished)
            worker.start()

        start_worker()

    def schedule_execution_prompt(self, schedule_item: Dict[str, object]) -> str:
        title = str(schedule_item.get("title") or "定时计划").strip()
        prompt = str(schedule_item.get("prompt") or "").strip()
        last_success = str(schedule_item.get("last_success_note") or "").strip()
        wechat_note = ""
        has_wechat_notify_target = bool(
            wechat_bridge_enabled_setting()
            and (
                schedule_item.get("notify_wechat_enabled")
                or (
                    last_wechat_reply_target().get("to_user")
                    and last_wechat_reply_target().get("context_token")
                )
            )
        )
        if has_wechat_notify_target:
            wechat_note = (
                "\n\n本计划完成后会通知微信用户。完成时用 AGENT_DONE 加一段面向用户的简短结论；"
                "程序会把这段结论发到微信。若本次生成了用户应直接查看的报告、图片、表格、文档等工作区文件，"
                "请在最终阶段用命令块写 `wx send_file 文件路径`。"
                "多个文件用英文逗号分隔；只发送真正有交付价值的文件，不要发送临时脚本或中间缓存。"
                "`wx send_file` 是发送请求，不代表已经发送完成；输出该命令时不要同时声称已发送。"
            )
        stable_note = (
            "\n\n上次成功执行摘要：\n"
            f"{last_success}\n\n"
            "如果上次已经沉淀出稳定脚本或固定方法，本次优先复用；只有失败或需求变化时再修复。"
        ) if last_success else (
            "\n\n如果本次需要探索、修复或生成脚本，请在成功后沉淀一个稳定脚本/固定方法；后续执行应优先复用，避免每天重复试错。"
        )
        return (
            f"【定时计划触发】\n"
            f"计划名称：{title}\n"
            f"触发时间：{format_schedule_time(schedule_item)}\n\n"
            f"计划内容：\n{prompt}\n\n"
            f"{stable_note}\n\n"
            f"{wechat_note}\n\n"
            f"{schedule_skills_catalog_text(self.skills)}"
        )

    def release_stuck_active_schedule_if_needed(self, now: datetime) -> bool:
        schedule_id = str(getattr(self, "active_schedule_id", "") or "").strip()
        if not schedule_id:
            return False
        if not (self.automation_loop_active or self.is_automation_request_running() or self.is_execution_running()):
            return False
        started_at = float(getattr(self, "active_schedule_started_at", 0.0) or 0.0)
        elapsed = (time.time() - started_at) if started_at > 0 else 0.0
        active_schedule = next(
            (item for item in load_workspace_schedules(self.project_root) if str(item.get("id") or "") == schedule_id),
            None,
        )
        title = str((active_schedule or {}).get("title") or "定时计划")
        next_due = False
        if active_schedule is not None and schedule_due(active_schedule, now):
            current_run_key = str(getattr(self, "active_schedule_run_key", "") or "")
            next_due = schedule_run_key(active_schedule, now) != current_run_key
        timed_out = elapsed >= SCHEDULE_ACTIVE_RUN_TIMEOUT_SECONDS
        if not next_due and not timed_out:
            return False
        if next_due:
            message = f"定时计划上一轮未结束，已释放以执行下一次：{title}"
        else:
            message = f"定时计划运行超过 {SCHEDULE_ACTIVE_RUN_TIMEOUT_SECONDS // 60} 分钟，已释放：{title}"
        self.add_status_bubble(message)
        if self.is_automation_request_running():
            self.cancel_automation_request()
        else:
            if self.is_execution_running() and self.worker is not None:
                self.worker.requestInterruption()
            self.stop_automation_loop("", ensure_manual_entry=False)
        QTimer.singleShot(1500, self.check_due_schedules)
        return True

    def check_due_schedules(self):
        if not self.project_root:
            return
        now = datetime.now()
        if self.release_stuck_active_schedule_if_needed(now):
            return
        if self.is_automation_request_running() or self.is_execution_running():
            return
        if self.automation_loop_active:
            self.stop_automation_loop("", ensure_manual_entry=False)
        for schedule_item in load_workspace_schedules(self.project_root):
            if not bool(schedule_item.get("enabled", True)):
                continue
            if schedule_expired(schedule_item, now):
                expire_workspace_schedule(self.project_root, str(schedule_item.get("id") or ""), now)
                continue
            schedule_spec = dict(schedule_item.get("schedule") or {})
            until_at = parse_schedule_datetime(schedule_spec.get("until_at"))
            if until_at and now > until_at and not schedule_due(schedule_item, now):
                expire_workspace_schedule(self.project_root, str(schedule_item.get("id") or ""), now)
                continue
            if not schedule_due(schedule_item, now):
                continue
            run_key = schedule_run_key(schedule_item, now)
            if str(schedule_item.get("last_run_key") or "") == run_key:
                continue
            self.start_schedule(schedule_item, run_key)
            break

    def switch_to_schedule_thread(self, schedule_item: Dict[str, object], run_key: str):
        if not self.project_root:
            return
        schedule_id = str(schedule_item.get("id") or "schedule").strip() or "schedule"
        title = str(schedule_item.get("title") or "定时计划").strip()
        thread_id = schedule_run_thread_id(schedule_id)
        thread_title = f"计划：{title}"
        self.flush_history_save(wait=True)
        self.threads = load_workspace_threads(self.project_root)
        ensure_workspace_thread(self.project_root, self.threads, thread_id, thread_title)
        rename_workspace_thread(self.project_root, thread_id, thread_title, load_workspace_threads(self.project_root))
        self.threads = load_workspace_threads(self.project_root)
        self.thread_id = thread_id
        save_last_thread_id(self.project_root, self.thread_id)
        self.sidebar.set_threads(self.threads, self.thread_id)
        self.sidebar.set_tab("threads")
        self.load_history()

    def start_schedule(self, schedule_item: Dict[str, object], run_key: str = ""):
        if not self.project_root:
            return
        if self.is_automation_request_running() or self.is_execution_running():
            return
        if self.automation_loop_active:
            self.stop_automation_loop("", ensure_manual_entry=False)
        if not self.automation_enabled:
            dep = self.automation_manager.dependency_status()
            if not dep.get("ready"):
                self.add_status_bubble("定时计划未执行：自动化插件依赖未就绪。")
                return
            self.automation_enabled = True
            set_automation_enabled_setting(True)
            self.show_automation_composer(focus=False)
        self.refresh_skills()
        schedule_id = str(schedule_item.get("id") or "")
        now = datetime.now()
        run_key = run_key or schedule_run_key(schedule_item, now)
        self.switch_to_schedule_thread(schedule_item, run_key)
        self.active_schedule_id = schedule_id
        self.active_schedule_notify = {}
        self.active_schedule_started_at = time.time()
        self.active_schedule_run_key = run_key
        if bool(wechat_bridge_enabled_setting()):
            to_user = str(schedule_item.get("notify_wechat_user") or "").strip()
            context_token = str(schedule_item.get("notify_wechat_context_token") or "").strip()
            if not to_user or not context_token:
                fallback_target = last_wechat_reply_target()
                to_user = to_user or str(fallback_target.get("to_user") or "")
                context_token = context_token or str(fallback_target.get("context_token") or "")
            if to_user and context_token:
                self.active_schedule_notify = {
                    "to_user": to_user,
                    "context_token": context_token,
                    "thread_id": str(schedule_item.get("notify_wechat_thread_id") or ""),
                    "title": str(schedule_item.get("title") or "定时计划"),
                }
        schedule_patch: Dict[str, object] = {
            "last_run_key": run_key,
            "last_run_at": now.isoformat(timespec="seconds"),
        }
        schedule_spec = dict(schedule_item.get("schedule") or {})
        repeat_seconds = normalize_repeat_seconds(schedule_spec.get("repeat_every_seconds"))
        until_at = parse_schedule_datetime(schedule_spec.get("until_at"))
        if repeat_seconds:
            base_run_at = parse_schedule_datetime(schedule_spec.get("run_at")) or now
            next_run_at = base_run_at
            while next_run_at <= now:
                next_run_at += timedelta(seconds=repeat_seconds)
            if until_at and next_run_at > until_at:
                schedule_patch["enabled"] = False
                repeat_seconds = 0
        if repeat_seconds:
            next_spec = dict(schedule_spec)
            next_spec["run_at"] = format_schedule_datetime(next_run_at)
            next_spec["repeat_every_seconds"] = repeat_seconds
            schedule_patch["schedule"] = next_spec
            schedule_patch["schedule_text"] = format_schedule_spec(next_spec)
        else:
            schedule_patch["enabled"] = False
        update_workspace_schedule(self.project_root, schedule_id, schedule_patch)
        title = str(schedule_item.get("title") or "定时计划")
        self.add_status_bubble(f"定时计划开始执行：{title}")
        prompt_text = self.schedule_execution_prompt(schedule_item)
        full_prompt = self.build_system_prompt(prompt_text)
        prompt_entry_id = self.add_automation_user_prompt_bubble(full_prompt, animate=True)
        self.begin_automation_loop(f"定时计划：{title}")
        self.start_automation_worker(prompt_text, "", None, None, prompt_entry_id)

    def resolve_schedule_wechat_thread_id(self, preferred_thread_id: str, to_user: str = "") -> str:
        if not self.project_root:
            return ""
        threads = normalize_threads(load_workspace_threads(self.project_root))
        thread_ids = {str(thread.get("id") or "") for thread in threads}
        raw_preferred = str(preferred_thread_id or "").strip()
        preferred = safe_thread_id(raw_preferred) if raw_preferred else ""
        if preferred and preferred in thread_ids:
            return preferred
        if to_user:
            for candidate in (
                safe_thread_id(to_user),
                safe_thread_id(f"{to_user}_im_wechat"),
                safe_thread_id(f"{to_user}-im-wechat"),
            ):
                if candidate in thread_ids:
                    return candidate
        for thread in reversed(threads):
            thread_id = str(thread.get("id") or "")
            title = str(thread.get("title") or "")
            if thread_id.endswith("_im_wechat") or title == "微信会话":
                return thread_id
        return ""

    def append_schedule_event_to_wechat_thread(
        self,
        *,
        preferred_thread_id: str,
        to_user: str,
        schedule_id: str,
        title: str,
        summary: str,
        sent_files: List[str],
        failed_files: List[str],
        notification_error: str = "",
    ):
        if not self.project_root:
            return
        thread_id = self.resolve_schedule_wechat_thread_id(preferred_thread_id, to_user)
        if not thread_id:
            return
        now_text = datetime.now().isoformat(timespec="seconds")
        clean_summary = re.sub(r"\s+", " ", wechat_strip_markdown_code(summary, keep_summary=True)).strip()
        clean_summary = text_within_utf8_budget(clean_summary or "任务已执行完成。", 1200)
        lines = [
            "【定时计划执行】",
            f"时间：{now_text}",
            f"计划：{title or '定时计划'}",
            f"计划ID：{schedule_id}",
            f"结果：{clean_summary}",
        ]
        if sent_files:
            lines.append("发送文件：" + "、".join(sent_files))
        if failed_files:
            lines.append("文件发送失败：" + "、".join(failed_files[:3]))
        if notification_error:
            lines.append(f"微信通知失败：{notification_error}")
        event_text = "\n".join(lines).strip()
        entry = {
            "id": uuid.uuid4().hex,
            "type": "ai",
            "content": event_text,
            "context_content": event_text,
            "created_at": now_text,
            "schedule_event": True,
            "schedule_id": schedule_id,
        }
        if thread_id == self.thread_id:
            self.append_history(entry)
            return
        entries = load_workspace_history(self.project_root, thread_id)
        entries.append(entry)
        save_workspace_history(self.project_root, entries, thread_id)

    def finish_active_schedule_success(self, summary: str):
        schedule_id = str(getattr(self, "active_schedule_id", "") or "").strip()
        if not schedule_id or not self.project_root:
            return
        requested_files = extract_wechat_send_file_targets(summary) if wechat_bridge_enabled_setting() else []
        clean_summary = strip_wechat_send_file_markers(schedule_success_note(summary) or "任务已执行完成。")
        update_workspace_schedule(self.project_root, schedule_id, {
            "last_success_note": clean_summary,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        })
        notify_info = dict(getattr(self, "active_schedule_notify", {}) or {})
        to_user = str(notify_info.get("to_user") or "").strip()
        context_token = str(notify_info.get("context_token") or "").strip()
        notify_thread_id = str(notify_info.get("thread_id") or "").strip()
        if wechat_bridge_enabled_setting() and (not to_user or not context_token):
            fallback_target = last_wechat_reply_target()
            to_user = to_user or str(fallback_target.get("to_user") or "")
            context_token = context_token or str(fallback_target.get("context_token") or "")
        if not wechat_bridge_enabled_setting() or not to_user or not context_token:
            return
        title = str(notify_info.get("title") or "定时计划").strip()
        reply_text = wechat_strip_markdown_code(clean_summary, keep_summary=True)
        reply_text = re.sub(r"\s+", " ", reply_text).strip()
        reply_text = text_within_utf8_budget(reply_text or "任务已执行完成。", 1600)
        sent_files: List[str] = []
        failed_files: List[str] = []
        notification_error = ""
        try:
            self.wechat_connector._send_text(to_user, f"{reply_text}\n[定时计划已完成：{title}]", context_token)
            for target in requested_files[:3]:
                path = resolve_project_file_target(self.project_root, target)
                if not path:
                    failed_files.append(target)
                    continue
                try:
                    self.wechat_connector._send_file(to_user, path, context_token)
                    sent_files.append(os.path.relpath(path, self.project_root))
                except Exception as file_exc:
                    failed_files.append(f"{target}（{file_exc}）")
            if sent_files:
                self.add_status_bubble("微信计划附件已发送：" + "、".join(sent_files))
            if failed_files:
                self.add_status_bubble("微信计划附件发送失败：" + "、".join(failed_files))
        except Exception as exc:
            notification_error = str(exc)
            self.add_status_bubble(f"微信计划通知发送失败：{exc}")
        self.append_schedule_event_to_wechat_thread(
            preferred_thread_id=notify_thread_id,
            to_user=to_user,
            schedule_id=schedule_id,
            title=title,
            summary=clean_summary,
            sent_files=sent_files,
            failed_files=failed_files,
            notification_error=notification_error,
        )

    def show_automation_composer(self, focus: bool = False):
        if self.automation_composer is None:
            return
        self.automation_composer.setVisible(bool(self.automation_enabled))
        self.update_automation_composer_state()
        if focus and self.automation_input is not None:
            QTimer.singleShot(60, self.automation_input.setFocus)

    def hide_automation_composer(self):
        if self.automation_composer is not None:
            self.automation_composer.setVisible(False)

    def automation_send_button_style(self, busy: bool = False) -> str:
        if busy:
            return f"""
                QToolButton {{
                    background: #8b95aa;
                    border: none;
                    border-radius: 21px;
                }}
                QToolButton:hover {{
                    background: #6f7788;
                }}
                QToolButton:pressed {{
                    background: #626a7b;
                }}
            """
        return f"""
            QToolButton {{
                background: {COLORS['accent']};
                border: none;
                border-radius: 21px;
            }}
            QToolButton:hover {{
                background: {COLORS['accent_dark']};
            }}
            QToolButton:pressed {{
                background: {COLORS['accent_dark']};
            }}
        """

    def stop_provider_async(self, wait_timeout: float = 0.8, aggressive: bool = True):
        if getattr(self, "_provider_stop_async_running", False):
            return
        self._provider_stop_async_running = True

        def _runner():
            try:
                self.automation_manager.stop_provider_process(wait_timeout=wait_timeout, aggressive=aggressive)
            except Exception:
                logger.warning("Asynchronous provider stop failed.", exc_info=True)
            finally:
                self._provider_stop_async_running = False

        threading.Thread(target=_runner, name="AgentQtStopProvider", daemon=True).start()

    def on_automation_input_text_changed(self):
        if (
            self.automation_input is not None
            and not self.automation_input.toPlainText().strip()
            and not self.is_automation_busy()
            and not self.is_execution_running()
        ):
            self.automation_input.setPlaceholderText(self.automation_context_placeholder_text())

    def update_automation_composer_state(self):
        if self.automation_send_btn is None or self.automation_input is None:
            return
        busy = self.is_automation_busy()
        local_execution_running = self.is_execution_running()
        if self.automation_composer_input_column is not None:
            self.automation_composer_input_column.setVisible(True)
        self.automation_input.setEnabled(True)
        if self.automation_skill_btn is not None:
            self.automation_skill_btn.setEnabled(not local_execution_running)
            self.update_automation_skill_button()
        if busy:
            self.automation_input.setPlaceholderText("等待执行完成。现在可以输入草稿")
            self.automation_send_btn.setIcon(line_icon("pause", "#ffffff", 20))
            self.automation_send_btn.setStyleSheet(self.automation_send_button_style(busy=True))
        else:
            self.automation_input.setPlaceholderText(self.automation_context_placeholder_text())
            self.automation_send_btn.setIcon(line_icon("send", "white", 20))
            self.automation_send_btn.setStyleSheet(self.automation_send_button_style(busy=False))

    def on_automation_composer_action(self):
        if self.is_automation_busy():
            self.cancel_automation_request()
            return
        self.submit_automation_prompt_from_composer()

    def cancel_automation_request(self):
        self.automation_request_serial += 1
        partial_text = (
            self.automation_preview_pending_text
            or self.automation_preview_last_rendered_text
            or ""
        ).strip()
        partial_provider_io = None
        if partial_text:
            partial_provider_io = {
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "model": self.automation_active_model or self.effective_automation_model(),
                "thread_id": self.thread_id,
                "messages": copy.deepcopy(self.automation_active_messages),
                "response": partial_text,
                "error": "interrupted",
            }
        self.preserve_interrupted_automation_preview(partial_provider_io)
        context_worker = self.automation_context_worker
        if context_worker is not None:
            self.automation_manager.cancel_generation(
                AUTOMATION_SIMPLE_MODEL_BY_MODEL.get(self.automation_model, self.automation_model),
                self.thread_id + "-context-compact",
            )
            context_worker.requestInterruption()
            context_worker.finished.connect(context_worker.deleteLater)
            if self.automation_context_worker is context_worker:
                self.automation_context_worker = None
        worker = self.automation_worker
        self.automation_manager.cancel_generation(self.effective_automation_model(), self.thread_id)
        if worker is not None:
            worker.requestInterruption()
            worker.finished.connect(worker.deleteLater)
            if self.automation_worker is worker:
                self.automation_worker = None
        web_research_worker = self.web_research_worker
        if web_research_worker is not None:
            web_research_worker.requestInterruption()
            web_research_worker.finished.connect(web_research_worker.deleteLater)
            if self.web_research_worker is web_research_worker:
                self.web_research_worker = None
        if context_worker is not None or worker is not None or web_research_worker is not None:
            self.stop_provider_async(wait_timeout=0.8, aggressive=True)
        self.automation_active_messages = []
        self.automation_active_model = ""
        if self.is_execution_running() and self.worker is not None:
            self.worker.requestInterruption()
        self.stop_automation_preview(remove_bubble=True)
        self.stop_automation_loop("", ensure_manual_entry=True)
        if self.wechat_active_request_id:
            self.finish_wechat_active_request("已停止当前 AI 输出或执行。")

    def submit_automation_prompt_from_composer(self):
        if not self.automation_enabled:
            return
        if self.automation_loop_active or self.is_automation_request_running():
            styled_warning(self, "自动化执行中", "当前自动化循环还没有结束。")
            return
        if self.is_execution_running():
            styled_warning(self, "正在执行", "当前本地命令还没有执行完成。")
            return
        text = self.automation_input.toPlainText().strip() if self.automation_input is not None else ""
        if not text:
            styled_warning(self, "缺少需求", "请先输入一句你想让 Agent 完成的需求。")
            if self.automation_input is not None:
                self.automation_input.setFocus()
            return
        if self.automation_input is not None:
            self.automation_input.clear()
        provider_text = self.schedule_thread_manual_prompt(text)
        full_prompt = self.build_system_prompt(provider_text, marker_user_text=text)
        prompt_entry_id = self.add_automation_user_prompt_bubble(full_prompt, animate=True, display_text=text)
        self.begin_automation_loop(provider_text)
        self.start_automation_worker(
            provider_text,
            "",
            None,
            None,
            prompt_entry_id,
        )
        if self.is_automation_request_running() and self.selected_skill_ids:
            self.clear_automation_skills()

    def add_automation_user_prompt_bubble(
        self,
        full_prompt: str,
        animate: bool = True,
        display_text: str = "",
        context_content: str = "",
    ) -> str:
        self.hide_empty_state()
        entry_id = uuid.uuid4().hex
        bubble = ChatBubble(
            "user",
            display_text.strip() or self.prompt_bubble_display_text(full_prompt),
            parent=self.chat_container,
            show_copy=False,
            show_prompt_input=False,
            scrollable=False,
            max_content_height=90,
            compact_user=True,
        )
        bubble.content = full_prompt
        bubble.history_entry_id = entry_id
        self.add_chat_widget(bubble, animate=animate)
        entry = {
            "id": entry_id,
            "type": "prompt",
            "content": full_prompt,
        }
        if context_content.strip():
            entry["context_content"] = context_content.strip()
        self.append_history(entry)
        self.scroll_to_bottom()
        return entry_id

    def remove_empty_automation_prompt_bubbles(self):
        for idx in range(self.chat_layout.count() - 1, -1, -1):
            widget = self.chat_layout.itemAt(idx).widget()
            if not isinstance(widget, ChatBubble) or getattr(widget, "role", "") != "user":
                continue
            content = getattr(widget, "content", "")
            if self.prompt_text_from_system_prompt(content).strip():
                continue
            self.chat_layout.removeWidget(widget)
            widget.deleteLater()

    def create_automation_preview_bubble(self) -> QFrame:
        self.hide_empty_state()
        frame = ChatBubble(
            "ai",
            "",
            parent=self.chat_container,
            markdown=True,
            expand_to_content=True,
            flat=True,
            max_content_height=560,
        )
        frame.async_markdown_render = False
        frame.stabilize_markdown_height = True
        role_label = frame.findChild(QLabel)
        if role_label is not None:
            role_label.setText("AI 正在回复")
        status = QLabel("AI 正在回复.")
        status.setStyleSheet(f"color: {COLORS['text_secondary']}; font-size: 12px; background: transparent;")
        frame.layout().insertWidget(1, status)
        frame.preview_status = status
        self.add_chat_widget(frame, animate=True)
        self.scroll_to_bottom()
        return frame

    def start_automation_preview(self):
        self.stop_automation_preview(remove_bubble=True)
        self.automation_preview_serial += 1
        serial = self.automation_preview_serial
        self.automation_preview_started_at = time.time()
        self.automation_preview_pending_text = ""
        self.automation_preview_last_rendered_text = ""
        self.automation_preview_last_chars = 0
        self.automation_preview_dots = 0
        self.automation_preview_thread_id = self.thread_id
        self.automation_preview_bubble = self.create_automation_preview_bubble()
        worker = AutomationPreviewWorker(self.automation_manager, self.effective_automation_model(), self.thread_id, serial)
        self.automation_preview_worker = worker
        worker.preview_signal.connect(self.update_automation_preview)
        worker.start()

    def stop_automation_preview(self, remove_bubble: bool = False):
        self.automation_preview_serial += 1
        worker = self.automation_preview_worker
        if worker is not None:
            worker.stop()
            try:
                worker.preview_signal.disconnect(self.update_automation_preview)
            except (RuntimeError, TypeError):
                pass
            if worker.wait(1500):
                worker.deleteLater()
            else:
                self.automation_preview_retired_workers.append(worker)
                worker.finished.connect(lambda worker=worker: self.cleanup_retired_preview_worker(worker))
                worker.finished.connect(worker.deleteLater)
            self.automation_preview_worker = None
        self.automation_preview_render_timer.stop()
        self.automation_preview_dots_timer.stop()
        self.automation_preview_pending_text = ""
        self.automation_preview_last_rendered_text = ""
        self.automation_preview_last_chars = 0
        self.automation_preview_thread_id = ""
        if remove_bubble and self.automation_preview_bubble is not None:
            bubble = self.automation_preview_bubble
            self.automation_preview_bubble = None
            self.chat_layout.removeWidget(bubble)
            bubble.hide()
            bubble.setParent(None)
            bubble.deleteLater()
            self.chat_container.adjustSize()

    def preserve_interrupted_automation_preview(
        self,
        provider_io: Optional[Dict[str, object]] = None,
        *,
        interrupted: bool = True,
        failure_reason: str = "",
    ) -> bool:
        text = (
            self.automation_preview_pending_text
            or self.automation_preview_last_rendered_text
            or ""
        ).strip()
        bubble = self.automation_preview_bubble
        if not text and isinstance(bubble, ChatBubble):
            text = str(getattr(bubble, "content", "") or "").strip()
        if not text:
            return False
        if isinstance(bubble, ChatBubble):
            self.finalize_automation_preview_bubble(text)
        else:
            ai_bubble = ChatBubble(
                "ai",
                text,
                show_copy=True,
                parent=self.chat_container,
                copy_text="复制 AI 输出",
                scrollable=True,
                max_content_height=QT_WIDGET_MAX_HEIGHT,
                markdown=self.automation_enabled,
                expand_to_content=True,
                flat=self.automation_enabled,
            )
            self.add_chat_widget(ai_bubble, animate=True)
        history_entry = {
            "type": "ai",
            "content": text,
        }
        if interrupted:
            history_entry["interrupted"] = True
        if failure_reason:
            history_entry["failure_reason"] = failure_reason
        if isinstance(provider_io, dict):
            history_entry["provider_io"] = copy.deepcopy(provider_io)
        self.append_history(history_entry)
        self.add_status_bubble("已保留中断前的 AI 输出。" if interrupted else "已保留异常前的 AI 输出。")
        return True

    def cleanup_retired_preview_worker(self, worker: AutomationPreviewWorker):
        try:
            self.automation_preview_retired_workers.remove(worker)
        except ValueError:
            pass

    def update_automation_preview_status(self, chars: Optional[int] = None):
        started = time.perf_counter()
        bubble = self.automation_preview_bubble
        if bubble is None:
            return
        if chars is not None:
            self.automation_preview_last_chars = max(0, int(chars))
        status = getattr(bubble, "preview_status", None)
        if status is None:
            return
        if self.automation_preview_last_chars > 0:
            text = f"AI 正在回复... 已生成约 {self.automation_preview_last_chars} 字"
        else:
            text = "AI 正在回复..."
        if status.text() != text:
            status.setText(text)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        if elapsed_ms >= 40:
            logger.warning("Automation preview status UI slow elapsed_ms=%d chars=%d", elapsed_ms, self.automation_preview_last_chars)

    def tick_automation_preview_status(self):
        if self.automation_preview_bubble is None:
            self.automation_preview_dots_timer.stop()
            return
        self.automation_preview_dots = (self.automation_preview_dots + 1) % 3
        self.update_automation_preview_status()

    def schedule_automation_preview_render(self):
        if not self.automation_preview_render_timer.isActive():
            delay = 20 if not self.automation_preview_last_rendered_text else 360
            self.automation_preview_render_timer.start(delay)

    def flush_automation_preview_render(self):
        started = time.perf_counter()
        bubble = self.automation_preview_bubble
        if bubble is None:
            return
        if self.automation_preview_thread_id and self.automation_preview_thread_id != self.thread_id:
            return
        text = self.automation_preview_pending_text
        if not text or text == self.automation_preview_last_rendered_text:
            return
        if self.automation_preview_last_rendered_text:
            previous_code_blocks = markdown_fenced_code_block_count(self.automation_preview_last_rendered_text)
            next_code_blocks = markdown_fenced_code_block_count(text)
            if previous_code_blocks > next_code_blocks:
                return
        scroll_state = self.capture_chat_scroll_state()
        bubble.update_content(text)
        self.automation_preview_last_rendered_text = text
        self.stabilize_chat_scroll_after_update(scroll_state)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        if elapsed_ms >= 60:
            logger.warning("Automation preview render UI slow elapsed_ms=%d chars=%d", elapsed_ms, len(text))

    def update_automation_preview(self, serial: int, preview: dict):
        started = time.perf_counter()
        if serial != self.automation_preview_serial:
            return
        if self.automation_preview_thread_id and self.automation_preview_thread_id != self.thread_id:
            return
        bubble = self.automation_preview_bubble
        if bubble is None:
            return
        text = str(preview.get("text") or "").strip()
        preview_updated_at = float(preview.get("updated_at") or 0.0)
        if text and preview_updated_at and preview_updated_at < self.automation_preview_started_at:
            return
        if text and not preview_updated_at and self.automation_preview_started_at:
            return
        chars = int(preview.get("chars") or len(text))
        self.update_automation_preview_status(chars)
        if text:
            if looks_like_automation_context_payload(text):
                return
            if looks_like_web_session_busy_text(text):
                self.set_status_bar_override("网页端当前消息仍在生成，正在自动重试…", duration_ms=12000)
                return
            if text != self.automation_preview_pending_text:
                self.automation_preview_pending_text = text
                self.schedule_automation_preview_render()
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        if elapsed_ms >= 40:
            logger.warning(
                "Automation preview slot UI slow elapsed_ms=%d chars=%d source=%s",
                elapsed_ms,
                chars,
                str(preview.get("source") or ""),
            )

    def is_schedule_thread(self) -> bool:
        return str(self.thread_id or "").startswith("schedule-")

    def schedule_thread_manual_prompt(self, user_text: str) -> str:
        text = str(user_text or "").strip()
        if not text or not self.is_schedule_thread():
            return text
        return (
            "用户正在计划任务会话中手动追加新指令。当前输入优先级高于本计划原始任务；"
            "除非用户明确要求继续执行计划，否则不要按计划任务重新生成内容。\n"
            "请把上面这句话作为当前临时的系统提示词。\n\n"
            "然后，我现在的需求是：\n"
            f"{text}"
        )

    def build_system_prompt(self, user_text: str, marker_user_text: Optional[str] = None) -> str:
        raw_prompt = user_text.strip()
        marker_prompt = raw_prompt if marker_user_text is None else str(marker_user_text or "").strip()
        prompt = raw_prompt or "请根据当前工作区创建或修改项目，并输出可直接执行的完整指令。"
        base_prompt = SYSTEM_PROMPT.format(
            project_root=self.project_root,
            user_prompt=prompt,
            done_marker=AUTOMATION_DONE_MARKER,
            terminal_registry_path=self.terminal_panel.registry_path() if hasattr(self, "terminal_panel") else terminal_registry_path(self.project_root or os.path.expanduser("~")),
            terminal_logs_url=(self.wechat_bridge.url().rstrip("/") + "/terminallogs") if hasattr(self, "wechat_bridge") else "http://127.0.0.1:8798/terminallogs",
            **runtime_environment(),
        )
        skills_context = self.selected_skills_context()
        skills_text = (
            "\n\n【本轮手动启用的技能】\n"
            "以下技能由用户在界面中手动勾选，已经作为本轮对话的已载入技能加入提示词。"
            "它们是本轮第一优先级：开始处理任务前，必须先阅读并遵循这些 SKILL.md；"
            "只有在这些已选技能不足以覆盖任务时，才考虑自主探索其他技能或方案。"
            "不要先否认自己没有技能；如果用户询问当前上下文里有哪些技能，也应基于这里直接回答。\n"
            f"{skills_context}"
        ) if skills_context else ""
        return base_prompt + skills_text + f"\n{PROMPT_BUBBLE_MARKER}{base64.b64encode(marker_prompt.encode('utf-8')).decode('ascii')} -->"

    def build_automation_system_text(self) -> str:
        return SYSTEM_PROMPT.format(
            project_root=self.project_root,
            user_prompt="当前指令见第三段 plaintext，不要把本段当作用户需求重复执行。",
            done_marker=AUTOMATION_DONE_MARKER,
            terminal_registry_path=self.terminal_panel.registry_path() if hasattr(self, "terminal_panel") else terminal_registry_path(self.project_root or os.path.expanduser("~")),
            terminal_logs_url=(self.wechat_bridge.url().rstrip("/") + "/terminallogs") if hasattr(self, "wechat_bridge") else "http://127.0.0.1:8798/terminallogs",
            **runtime_environment(),
        )

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

    def prompt_bubble_display_text(self, full_prompt: str) -> str:
        if not self.automation_enabled:
            return self.display_prompt_text(full_prompt)
        user_text = self.prompt_text_from_system_prompt(full_prompt).strip()
        if user_text:
            return self.compact_wechat_prompt_for_history(user_text)
        if PROMPT_BUBBLE_MARKER not in full_prompt:
            return str(full_prompt or "").strip()
        return ""

    def compact_wechat_prompt_for_history(self, text: str) -> str:
        content = str(text or "").strip()
        if "【微信远控消息】" not in content:
            return content
        marker = "用户消息："
        marker_index = content.rfind(marker)
        if marker_index < 0:
            return "【微信用户需求】\n" + truncate_middle(content, 2000)
        message = content[marker_index + len(marker):].strip()
        return "【微信用户需求】\n" + truncate_middle(message, 2000)

    def compact_automation_entry_text(self, text: str, limit: int = AUTOMATION_CONTEXT_ENTRY_CHAR_LIMIT) -> str:
        return truncate_middle(str(text or "").strip(), limit)

    def provider_error_history_content(self, error: str, response: str = "") -> str:
        parts = ["Provider 请求失败，本轮自动化已暂停。"]
        if response.strip():
            parts.append("失败前已生成的 AI 输出：\n" + response.strip())
        parts.append("错误信息：\n" + str(error or "").strip())
        return "\n\n".join(parts).strip()

    def automation_context_system_text(self) -> str:
        skills_context = self.selected_skills_context()
        skills_text = (
            "\n\n【本轮手动启用的技能】\n"
            "以下 SKILL.md 由用户手动选择，是本轮第一优先级。开始处理任务前，必须先阅读并遵循这些已载入技能；"
            "只有在这些技能不足以覆盖任务时，才考虑自主探索其他技能或方案。不要先否认自己没有技能。\n"
            f"{skills_context}"
        ) if skills_context else ""
        return self.build_automation_system_text() + skills_text + (
            "\n\n补充说明：provider 每次可能会打开新的网页对话，所以第二段包含 Agent Qt 保存的本会话上下文。"
            "请把这些上下文视为连续对话历史。上下文按纯文本给出，不是 JSON 或工具调用协议。"
            "第一段系统提示词始终是当前最新规则，优先级高于第二段历史；第二段里的旧提示词、旧命令写法或旧工具说明只作为事实参考，不要沿用已经被第一段替换的旧规则。"
            f"自动化上下文按 {context_k_label(AUTOMATION_CONTEXT_DISPLAY_TOKENS)} 估算展示；当历史超过约 {context_k_label(AUTOMATION_CONTEXT_COMPACT_TRIGGER_TOKENS)} 时，"
            "Agent Qt 会把较早历史 compact 成 plaintext 摘要，近期上下文保留原文后继续。"
        )

    def summarize_automation_text(self, text: str, limit: int) -> str:
        content = summarize_fenced_code_blocks_for_context(strip_low_value_context_blocks(str(text or "").strip()))
        if len(content) <= limit:
            return content
        lines = [line.rstrip() for line in content.splitlines() if line.strip()]
        if not lines:
            return ""
        high_signal: List[str] = []
        high_signal_patterns = (
            AUTOMATION_DONE_MARKER,
            "Files changed:",
            "文件变更：",
            "Git diff file names:",
            "Git diff hunks:",
            "Terminal processes:",
            "Internal git snapshot:",
            "Traceback",
            "Error:",
            "ERROR:",
            "失败",
            "报错",
        )
        for line in lines:
            stripped = line.strip()
            if (
                any(stripped.startswith(pattern) for pattern in high_signal_patterns)
                or "registry=" in stripped
                or "log=" in stripped
                or "pid=" in stripped
                or "launch_reason=" in stripped
                or "command_kind=" in stripped
                or re.match(r"^[AMD]\s+\S+", stripped)
                or stripped.startswith("@@")
            ):
                high_signal.append(stripped)
        selected: List[str] = []
        for line in high_signal[:18]:
            if line not in selected:
                selected.append(line)
        for line in lines[:10]:
            if line not in selected:
                selected.append(line)
        for line in lines[-8:]:
            if line not in selected:
                selected.append(line)
        summary = "\n".join(selected).strip()
        if len(summary) > limit:
            summary = summary[: max(0, limit - 40)].rstrip()
        omitted = max(0, len(lines) - len(selected))
        if omitted:
            suffix = f"\n（已省略 {omitted} 行低优先级历史细节）"
            summary = (summary[: max(0, limit - len(suffix))].rstrip() + suffix).strip()
        return summary

    def summarize_result_history_text(self, text: str, limit: int) -> str:
        content = strip_low_value_context_blocks(str(text or "").strip())
        if not content:
            return "命令执行完成，未产生可保留的高信号输出。"
        marker = "File change summary:"
        if marker in content:
            content = content[content.index(marker):].strip()
        return self.summarize_automation_text(content, limit)

    def automation_history_text_for_entry(self, entry: Dict[str, object], detail: str = "full") -> str:
        entry_type = str(entry.get("type") or "")
        content = str(entry.get("context_content") or entry.get("content") or "").strip()
        if not content:
            return ""
        if entry_type == "prompt":
            user_text = self.prompt_text_from_system_prompt(content).strip()
            if not user_text and PROMPT_BUBBLE_MARKER not in content:
                user_text = content.strip()
            if not user_text:
                return ""
            user_text = self.compact_wechat_prompt_for_history(user_text)
            prompt_limit = 2000 if detail == "full" else (900 if detail == "lean" else 500)
            return "【用户需求】\n" + self.compact_automation_entry_text(user_text, prompt_limit)
        if entry_type == "ai":
            prefix = ""
            if entry.get("interrupted"):
                prefix = "状态：用户强制中断，本条是中断前已生成的 AI 输出。\n\n"
            elif entry.get("failure_reason"):
                prefix = "状态：Provider 请求失败，本条是失败前已生成的 AI 输出。\n错误信息：\n" + str(entry.get("failure_reason") or "").strip() + "\n\n"
            if detail == "full":
                return "【AI 回复】\n" + prefix + self.compact_automation_entry_text(content)
            ai_limit = 2600 if detail == "lean" else 1000
            return "【AI 回复摘要】\n" + prefix + self.summarize_automation_text(content, ai_limit)
        if entry_type == "result":
            if detail == "full":
                if "File change summary:" in content:
                    return "【本地执行结果和文件变更】\n" + self.summarize_result_history_text(content, AUTOMATION_CONTEXT_ENTRY_CHAR_LIMIT)
                return "【本地执行结果和文件变更】\n" + self.compact_automation_entry_text(content)
            result_limit = 2400 if detail == "lean" else 1100
            return "【本地执行结果摘要】\n" + self.summarize_result_history_text(content, result_limit)
        if entry_type == "terminal_result":
            if detail == "full":
                return "【终端执行结果】\n" + self.compact_automation_entry_text(content)
            result_limit = 1800 if detail == "lean" else 800
            return "【终端执行结果摘要】\n" + self.summarize_automation_text(content, result_limit)
        if entry_type == "provider_io":
            if detail == "full":
                return "【Provider 请求失败】\n" + self.compact_automation_entry_text(content)
            result_limit = 1800 if detail == "lean" else 800
            return "【Provider 请求失败摘要】\n" + self.summarize_automation_text(content, result_limit)
        return ""

    def collapse_repeated_history_chunks(self, chunks: List[str]) -> List[str]:
        collapsed: List[str] = []
        previous = ""
        count = 0

        def flush() -> None:
            nonlocal previous, count
            if not previous:
                return
            if count > 1:
                collapsed.append(f"{previous}\n（上面这条历史连续重复 {count} 次，已合并）")
            else:
                collapsed.append(previous)

        for chunk in chunks:
            if chunk == previous:
                count += 1
                continue
            flush()
            previous = chunk
            count = 1
        flush()
        return collapsed

    def automation_history_chunks(
        self,
        skip_entry_id: str = "",
        detail: str = "full",
        recent_full_count: int = 0,
    ) -> List[str]:
        entries = [
            entry for entry in self.history_entries
            if not (skip_entry_id and str(entry.get("id") or "") == skip_entry_id)
            and not bool(entry.get("exclude_from_context", False))
        ]
        chunks: List[str] = []
        full_start = max(0, len(entries) - max(0, recent_full_count))
        for index, entry in enumerate(entries):
            entry_detail = "full" if recent_full_count and index >= full_start else detail
            text = self.automation_history_text_for_entry(entry, detail=entry_detail)
            if text:
                chunks.append(text)
        return self.collapse_repeated_history_chunks(chunks)

    def compact_automation_history_text(self, chunks: List[str], token_budget: int) -> str:
        history_text, compacted = compact_history_text_from_chunks(chunks, token_budget)
        if compacted:
            self.last_automation_history_compacted = True
        return history_text

    def build_automation_context_payload(
        self,
        current_prompt: str,
        skip_entry_id: str = "",
        log_stats: bool = True,
        apply_provider_budget: Optional[bool] = None,
    ) -> str:
        self.last_automation_history_compacted = False
        system_context = self.automation_context_system_text()
        current_prompt = str(current_prompt or "").strip()
        token_budget = max(
            4000,
            AUTOMATION_CONTEXT_WINDOW_TOKENS
            - AUTOMATION_CONTEXT_RESPONSE_RESERVE_TOKENS
            - estimate_context_tokens(system_context)
            - estimate_context_tokens(current_prompt),
        )
        history_text = self.compact_automation_history_text(
            self.automation_history_chunks(skip_entry_id=skip_entry_id),
            token_budget,
        )
        def build_payload(history: str) -> str:
            return "\n\n".join([
                plaintext_fence("第一段：系统提示词", system_context),
                plaintext_fence("第二段：历史对话", history),
                plaintext_fence("第三段：当前指令", current_prompt),
                plaintext_fence("第四段：生成前提醒", AUTOMATION_FINAL_REMINDER),
            ])

        payload = build_payload(history_text)
        payload_bytes = utf8_len(payload)
        if apply_provider_budget is None:
            apply_provider_budget = self.automation_context_mode == "expert"
        provider_byte_budget = AUTOMATION_CONTEXT_PROVIDER_PAYLOAD_BYTES if apply_provider_budget else 0
        provider_compaction = "none"
        if provider_byte_budget > 0 and payload_bytes > provider_byte_budget:
            history_text = self.compact_automation_history_text(
                self.automation_history_chunks(skip_entry_id=skip_entry_id, detail="lean", recent_full_count=16),
                token_budget,
            )
            payload = build_payload(history_text)
            payload_bytes = utf8_len(payload)
            provider_compaction = "semantic_lean"
        if provider_byte_budget > 0 and payload_bytes > provider_byte_budget:
            history_text = self.compact_automation_history_text(
                self.automation_history_chunks(skip_entry_id=skip_entry_id, detail="minimal", recent_full_count=6),
                token_budget,
            )
            payload = build_payload(history_text)
            payload_bytes = utf8_len(payload)
            provider_compaction = "semantic_minimal"
        if provider_byte_budget > 0 and payload_bytes > provider_byte_budget:
            empty_history_payload = build_payload("")
            history_byte_budget = max(0, provider_byte_budget - utf8_len(empty_history_payload) - 512)
            history_text = text_within_utf8_budget(history_text, history_byte_budget)
            payload = build_payload(history_text)
            provider_compaction = "byte_fallback"
        self.last_automation_provider_compaction = provider_compaction
        if provider_compaction != "none":
            self.last_automation_history_compacted = True
        if log_stats:
            logger.warning(
                "Automation context payload chars=%d bytes=%d tokens=%d token_budget=%d provider_byte_budget=%d provider_compaction=%s",
                len(payload),
                utf8_len(payload),
                estimate_context_tokens(payload),
                token_budget,
                provider_byte_budget,
                provider_compaction,
            )
        return payload

    def automation_context_tokens_for_next_request(self, current_prompt: str = "", skip_entry_id: str = "") -> int:
        return estimate_context_tokens(self.build_automation_context_payload(current_prompt, skip_entry_id=skip_entry_id, log_stats=False))

    def automation_context_placeholder_text(self) -> str:
        skill_suffix = f" · 已选 {len(self.selected_skills())} 个技能" if self.selected_skill_ids else ""
        if self.automation_context_mode == "simple":
            return f"简单模式 · 上下文到约 {context_k_label(AUTOMATION_CONTEXT_COMPACT_TRIGGER_TOKENS)} 自动压缩{skill_suffix} · 输入下一步需求..."
        return f"专家模式 · DeepSeek Web 约45k 上下文阈值{skill_suffix} · 输入下一步需求..."

    def build_automation_messages(self, current_prompt: str, skip_entry_id: str = "") -> List[Dict[str, str]]:
        payload = self.build_automation_context_payload(current_prompt, skip_entry_id=skip_entry_id)
        if self.last_automation_history_compacted:
            self.add_context_compaction_notice()
        return [{"role": "user", "content": payload}]

    def append_prompt_bubble_from_toolbar(self):
        if self.automation_enabled:
            self.show_automation_composer(focus=True)
            return
        full_prompt = self.build_system_prompt("")
        self.copy_prompt_btn.setText("已添加")
        QTimer.singleShot(1200, self.reset_primary_button_text)
        self.add_prompt_bubble(full_prompt, save=True, animate=True)
        self.scroll_to_bottom()

    def on_primary_action_button(self):
        if self.automation_enabled:
            self.show_share_menu()
            return
        self.append_prompt_bubble_from_toolbar()

    def copy_prompt_bubble(self, bubble: ChatBubble):
        prompt_input = getattr(bubble, "prompt_input", None)
        user_text = prompt_input.text() if prompt_input is not None else self.prompt_text_from_system_prompt(bubble.content)
        if self.automation_enabled and not user_text.strip():
            styled_warning(self, "缺少需求", "请先输入一句你想让 Agent 完成的需求。")
            if prompt_input is not None:
                prompt_input.setFocus()
            return
        full_prompt = self.build_system_prompt(user_text)
        bubble.content = full_prompt
        bubble.update_display_content(self.prompt_bubble_display_text(full_prompt))
        if self.automation_enabled:
            self.update_prompt_history_entry(getattr(bubble, "history_entry_id", ""), full_prompt)
            self.send_prompt_bubble_to_provider(bubble, full_prompt)
            return
        QApplication.clipboard().setText(self.display_prompt_text(full_prompt))
        copy_btn = getattr(bubble, "copy_btn", None)
        if copy_btn is not None:
            copy_btn.setText("已复制")
            QTimer.singleShot(1000, lambda: copy_btn.setText(bubble.copy_text))
        self.update_prompt_history_entry(getattr(bubble, "history_entry_id", ""), full_prompt)

    def send_prompt_bubble_to_provider(self, bubble: ChatBubble, full_prompt: str):
        if self.automation_loop_active or self.is_automation_request_running():
            styled_warning(self, "自动化执行中", "当前自动化循环还没有结束。")
            return
        if self.is_execution_running():
            styled_warning(self, "正在执行", "当前本地命令还没有执行完成。")
            return
        goal = self.prompt_text_from_system_prompt(full_prompt).strip()
        self.begin_automation_loop(goal)
        self.start_automation_worker(
            goal,
            "",
            getattr(bubble, "copy_btn", None),
            bubble,
            getattr(bubble, "history_entry_id", ""),
        )

    def start_automation_worker(
        self,
        prompt: str,
        status_text: str,
        copy_btn: Optional[QPushButton] = None,
        source_bubble: Optional[ChatBubble] = None,
        skip_entry_id: str = "",
    ):
        if self.is_automation_request_running():
            styled_warning(self, "自动化执行中", "已有一轮 AI 自动化请求正在等待回复。")
            return
        if self.is_execution_running():
            styled_warning(self, "正在执行", "当前本地命令还没有执行完成。")
            return
        if copy_btn is not None:
            copy_btn.setEnabled(False)
            copy_btn.setText("等待 AI...")
        if status_text:
            self.add_status_bubble(status_text)
        self.automation_request_serial += 1
        request_serial = self.automation_request_serial
        self.last_automation_history_compacted = False
        self.last_automation_provider_compaction = "none"
        system_context = self.automation_context_system_text()
        current_prompt = str(prompt or "").strip()
        token_budget = max(
            4000,
            AUTOMATION_CONTEXT_WINDOW_TOKENS
            - AUTOMATION_CONTEXT_RESPONSE_RESERVE_TOKENS
            - estimate_context_tokens(system_context)
            - estimate_context_tokens(current_prompt),
        )
        provider_byte_budget = (
            AUTOMATION_CONTEXT_PROVIDER_PAYLOAD_BYTES
            if self.automation_context_mode == "expert"
            else 0
        )
        context_worker = AutomationContextBuildWorker(
            self.automation_manager,
            request_serial=request_serial,
            system_context=system_context,
            current_prompt=current_prompt,
            full_chunks=self.automation_history_chunks(skip_entry_id=skip_entry_id),
            lean_chunks=self.automation_history_chunks(skip_entry_id=skip_entry_id, detail="lean", recent_full_count=16),
            minimal_chunks=self.automation_history_chunks(skip_entry_id=skip_entry_id, detail="minimal", recent_full_count=6),
            token_budget=token_budget,
            provider_byte_budget=provider_byte_budget,
            summary_model=AUTOMATION_SIMPLE_MODEL_BY_MODEL.get(self.automation_model, self.automation_model),
            thread_id=self.thread_id,
        )
        self.automation_context_worker = context_worker
        context_worker.finished.connect(context_worker.deleteLater)
        self.update_automation_composer_state()

        def start_chat_worker(messages: List[Dict[str, str]], long_retry_round: int = 0):
            provider_model = self.effective_automation_model()
            worker = AutomationChatWorker(
                self.automation_manager,
                messages,
                provider_model,
                self.thread_id,
                thinking_enabled=automation_thinking_enabled(self.automation_model),
                expert_mode_enabled=(self.automation_context_mode == "expert"),
            )
            self.automation_worker = worker
            worker.finished.connect(worker.deleteLater)
            self.automation_active_messages = copy.deepcopy(messages)
            self.automation_active_model = provider_model
            self.update_automation_composer_state()
            self.start_automation_preview()

            def on_status(message: str):
                if request_serial == self.automation_request_serial and self.automation_worker is worker:
                    self.set_status_bar_override(message, duration_ms=12000)

            def on_finished(text: str, error: str):
                if request_serial != self.automation_request_serial or self.automation_worker is not worker:
                    return
                self.automation_worker = None
                self.update_automation_composer_state()
                if copy_btn is not None and not self.automation_loop_active:
                    copy_btn.setEnabled(True)
                    copy_btn.setText(source_bubble.copy_text if source_bubble is not None else "发送给 AI")
                if error:
                    if looks_like_provider_transient_error(error) and long_retry_round < PROVIDER_LONG_RETRY_ATTEMPTS:
                        retry_round = long_retry_round + 1
                        wait_seconds = max(1, int(PROVIDER_LONG_RETRY_DELAY_MS / 1000))
                        retry_message = f"短重试仍未恢复，{wait_seconds} 秒后进行第 {retry_round}/{PROVIDER_LONG_RETRY_ATTEMPTS} 轮后台重试…"
                        self.set_status_bar_override(retry_message, duration_ms=min(PROVIDER_LONG_RETRY_DELAY_MS + 8000, 180000))
                        self.stop_automation_preview(remove_bubble=False)
                        if self.automation_loop_active:
                            QTimer.singleShot(PROVIDER_LONG_RETRY_DELAY_MS, lambda m=copy.deepcopy(messages), rr=retry_round: start_chat_worker(m, rr))
                            return
                    partial_text = (
                        self.automation_preview_pending_text
                        or self.automation_preview_last_rendered_text
                        or ""
                    ).strip()
                    self.pending_provider_io = {
                        "created_at": datetime.now().isoformat(timespec="seconds"),
                        "model": provider_model,
                        "thread_id": self.thread_id,
                        "messages": copy.deepcopy(messages),
                        "response": partial_text,
                        "error": str(error or ""),
                    }
                    if partial_text:
                        self.preserve_interrupted_automation_preview(
                            self.pending_provider_io,
                            interrupted=False,
                            failure_reason=str(error or ""),
                        )
                    else:
                        self.append_history({
                            "type": "provider_io",
                            "content": self.provider_error_history_content(str(error or "")),
                            "provider_io": copy.deepcopy(self.pending_provider_io),
                            "failed": True,
                        })
                    self.pending_provider_io = None
                    self.automation_active_messages = []
                    self.automation_active_model = ""
                    self.stop_automation_preview(remove_bubble=True)
                    quiet_message = quiet_automation_error_message(error)
                    was_wechat_request = bool(self.wechat_active_request_id)
                    self.stop_automation_loop(
                        quiet_message or "自动化循环已暂停。",
                        ensure_manual_entry=True,
                    )
                    if "网页登录还没有准备好" in error:
                        self.add_status_bubble("需要先完成网页登录。请使用设置里的“打开网页登录”，登录后重新发送。")
                    if was_wechat_request:
                        self.add_status_bubble("微信远控请求失败，已把错误返回到微信，不弹出阻塞窗口。")
                    elif (not quiet_message) or developer_error_details_enabled():
                        styled_warning(self, "AI 自动化失败", self.automation_manager.error_with_log_hint(error))
                else:
                    provider_io = {
                        "created_at": datetime.now().isoformat(timespec="seconds"),
                        "model": provider_model,
                        "thread_id": self.thread_id,
                        "messages": copy.deepcopy(messages),
                        "response": text,
                        "error": "",
                    }
                    self.automation_active_messages = []
                    self.automation_active_model = ""
                    preview_bubble = self.finalize_automation_preview_bubble(text)
                    self.handle_ai_response_text(text, existing_bubble=preview_bubble, provider_io=provider_io)
            worker.status_signal.connect(on_status)
            worker.finished_signal.connect(on_finished)
            worker.start()

        def on_context_status(_status: str):
            self.add_context_compaction_notice()

        def on_context_finished(serial: int, messages: List[Dict[str, str]], error: str, provider_compaction: str):
            if serial != self.automation_request_serial or self.automation_context_worker is not context_worker:
                return
            self.automation_context_worker = None
            self.last_automation_provider_compaction = provider_compaction or "none"
            self.last_automation_history_compacted = provider_compaction not in {"", "none"}
            self.update_automation_composer_state()
            if error:
                was_wechat_request = bool(self.wechat_active_request_id)
                self.stop_automation_loop("上下文压缩失败，自动化循环已暂停。", ensure_manual_entry=True)
                if copy_btn is not None:
                    copy_btn.setEnabled(True)
                    copy_btn.setText(source_bubble.copy_text if source_bubble is not None else "发送给 AI")
                if was_wechat_request:
                    self.add_status_bubble("微信远控上下文压缩失败，已把错误返回到微信，不弹出阻塞窗口。")
                elif developer_error_details_enabled():
                    styled_warning(self, "上下文压缩失败", self.automation_manager.error_with_log_hint(error))
                return
            start_chat_worker(messages)

        context_worker.status_signal.connect(on_context_status)
        context_worker.finished_signal.connect(on_context_finished)
        context_worker.start()

    def update_prompt_history_entry(self, entry_id: str, full_prompt: str):
        if not entry_id:
            return
        for entry in self.history_entries:
            if entry.get("id") == entry_id and entry.get("type") == "prompt":
                entry["content"] = full_prompt
                self.save_history()
                return

    def copy_system_prompt(self):
        if self.automation_enabled:
            self.show_automation_composer(focus=True)
            return
        full_prompt = self.build_system_prompt("")
        QApplication.clipboard().setText(self.display_prompt_text(full_prompt))
        self.copy_prompt_btn.setText("已复制")
        QTimer.singleShot(1200, self.reset_primary_button_text)
        self.add_prompt_bubble(full_prompt, save=True, animate=True)
        self.scroll_to_bottom()

    def add_prompt_bubble(self, full_prompt: str, save: bool = False, animate: bool = False):
        self.hide_empty_state()
        history_entry_id = uuid.uuid4().hex if save else ""
        prompt_bubble = ChatBubble(
            "user",
            self.prompt_bubble_display_text(full_prompt),
            show_copy=True,
            parent=self.chat_container,
            copy_text="自动执行中" if self.automation_loop_active else ("发送给 AI" if self.automation_enabled else "复制提示词"),
            show_paste_ai=not self.automation_enabled,
            prompt_input_text=self.prompt_text_from_system_prompt(full_prompt),
            scrollable=True,
            max_content_height=90 if self.automation_enabled else 180,
        )
        prompt_bubble.content = full_prompt
        prompt_bubble.history_entry_id = history_entry_id
        prompt_bubble.copy_requested.connect(lambda bubble=prompt_bubble: self.copy_prompt_bubble(bubble))
        if not self.automation_enabled:
            prompt_bubble.paste_ai_requested.connect(lambda: self.add_ai_response_frame(focus=True))
        if self.automation_loop_active and getattr(prompt_bubble, "copy_btn", None) is not None:
            prompt_bubble.copy_btn.setEnabled(False)
        self.add_chat_widget(prompt_bubble, animate=animate)
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

    def last_chat_widget(self) -> Optional[QWidget]:
        for idx in range(self.chat_layout.count() - 1, -1, -1):
            widget = self.chat_layout.itemAt(idx).widget()
            if widget is not None and widget is not self.empty_state:
                return widget
        return None

    def ensure_prompt_input_entry(self, focus: bool = False, animate: bool = True):
        last_widget = self.last_chat_widget()
        if isinstance(last_widget, ChatBubble) and getattr(last_widget, "role", "") == "user":
            prompt_input = getattr(last_widget, "prompt_input", None)
            if focus and prompt_input is not None:
                QTimer.singleShot(60, prompt_input.setFocus)
            self.scroll_to_bottom()
            return
        self.add_prompt_bubble(self.build_system_prompt(""), save=True, animate=animate)
        if focus:
            new_widget = self.last_chat_widget()
            prompt_input = getattr(new_widget, "prompt_input", None)
            if prompt_input is not None:
                QTimer.singleShot(60, prompt_input.setFocus)
        self.scroll_to_bottom()
    
    def update_status_bar(self):
        n = self.terminal_panel.count()
        is_collapsed = self.terminal_panel.maximumHeight() == 0
        override_active = bool(self._status_bar_override_text and time.time() < self._status_bar_override_until)
        if is_collapsed:
            self.status_bar.setText(self._status_bar_override_text if override_active else f"终端 · {n} 个进程")
            self.status_bar.setVisible(True)
            self.terminal_resize_handle.setVisible(False)
        else:
            self.status_bar.setVisible(False)
            self.terminal_resize_handle.setVisible(True)
        if not override_active and self._status_bar_override_text:
            self._status_bar_override_text = ""
            self._status_bar_override_until = 0.0
            self.terminal_panel.set_header_status("")

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
        if self.automation_enabled and not self.is_automation_busy() and not self.is_execution_running():
            QTimer.singleShot(0, self.update_automation_composer_state)

    def update_history_entry(self, entry_id: str, patch: Dict[str, object]) -> bool:
        if not entry_id:
            return False
        for entry in self.history_entries:
            if str(entry.get("id") or "") == str(entry_id):
                entry.update(patch)
                self.save_history()
                return True
        return False

    def save_history(self, immediate: bool = False):
        if not self.project_root:
            return
        self.history_save_dirty = True
        if immediate:
            self.flush_history_save(wait=True)
            return
        self.history_save_timer.start(650)

    def flush_history_save(self, wait: bool = False):
        if self.history_save_timer.isActive():
            self.history_save_timer.stop()
        if self.history_save_worker is not None and self.history_save_worker.isRunning():
            if wait:
                self.history_save_worker.wait(3000)
            if self.history_save_worker is not None and self.history_save_worker.isRunning():
                self.history_save_dirty = True
                return
        if not self.history_save_dirty or not self.project_root:
            return
        self.history_save_dirty = False
        self.history_save_generation += 1
        generation = self.history_save_generation
        entries = copy.deepcopy(self.history_entries)
        worker = HistorySaveWorker(self.project_root, self.thread_id, entries, generation)
        self.history_save_worker = worker
        self.history_save_workers.append(worker)
        worker.finished_signal.connect(self.on_history_save_finished)
        worker.finished.connect(lambda w=worker: self.cleanup_history_save_worker(w))
        worker.start()
        if wait:
            worker.wait(3000)
            QApplication.processEvents()

    def on_history_save_finished(self, generation: int, ok: bool, tmp_path: str):
        worker = self.history_save_worker
        if worker is not None and generation == self.history_save_generation:
            self.history_save_worker = None
        current_generation = generation == self.history_save_generation
        if ok and current_generation and self.project_root:
            try:
                os.makedirs(history_dir(self.project_root, self.thread_id), exist_ok=True)
                os.replace(tmp_path, history_path(self.project_root, self.thread_id))
            except OSError:
                ok = False
        elif tmp_path:
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except OSError:
                pass
        if not ok and current_generation:
            logger.warning("History save failed generation=%d thread_id=%s", generation, self.thread_id)
        if self.history_save_dirty:
            self.history_save_timer.start(300)

    def cleanup_history_save_worker(self, worker: HistorySaveWorker):
        try:
            self.history_save_workers.remove(worker)
        except ValueError:
            pass
        if self.history_save_worker is worker:
            self.history_save_worker = None
        worker.deleteLater()

    def wait_for_history_save_workers(self, timeout_ms: int = 3000):
        deadline = time.monotonic() + max(0, timeout_ms) / 1000.0
        workers = list(self.history_save_workers)
        if self.history_save_worker is not None and self.history_save_worker not in workers:
            workers.append(self.history_save_worker)
        for worker in workers:
            if worker is None:
                continue
            remaining = int(max(0.0, deadline - time.monotonic()) * 1000)
            if worker.isRunning() and remaining > 0:
                worker.wait(remaining)
        QApplication.processEvents()

    def load_history(self):
        self.flush_history_save(wait=True)
        started = time.perf_counter()
        self.history_entries = load_workspace_history(self.project_root, self.thread_id)
        self.setUpdatesEnabled(False)
        try:
            self.clear_chat_widgets()
            if not self.history_entries:
                if self.automation_enabled:
                    self.show_automation_composer(focus=False)
                else:
                    self.ensure_initial_prompt_bubble()
                self.scroll_to_bottom()
                return
            self.hide_empty_state()
            render_entries = self.history_entries[-CHAT_HISTORY_INITIAL_RENDER_ENTRIES:]
            hidden_count = max(0, len(self.history_entries) - len(render_entries))
            self.add_history_trim_notice(hidden_count)
            for entry in render_entries:
                self.restore_history_entry(entry)
            if self.automation_enabled:
                self.remove_ai_response_frame()
                self.remove_empty_automation_prompt_bubbles()
                self.show_automation_composer(focus=False)
            elif any(entry.get("type") != "prompt" for entry in self.history_entries):
                self.ensure_ai_response_entry(focus=False, animate=False, keep_visible=False)
            self.scroll_to_bottom()
        finally:
            self.setUpdatesEnabled(True)
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            if elapsed_ms >= 250:
                logger.warning(
                    "History load timing entries=%d rendered=%d elapsed_ms=%d",
                    len(self.history_entries),
                    min(len(self.history_entries), CHAT_HISTORY_INITIAL_RENDER_ENTRIES),
                    elapsed_ms,
                )

    def switch_thread(self, thread_id: str):
        thread_id = safe_thread_id(thread_id)
        if thread_id == self.thread_id or self.is_execution_running() or self.is_automation_request_running():
            return
        self.stop_automation_preview(remove_bubble=True)
        if self.automation_loop_active:
            self.stop_automation_loop("", ensure_manual_entry=False)
        self.thread_id = thread_id
        save_last_thread_id(self.project_root, self.thread_id)
        self.sidebar.set_active_thread(thread_id)
        self.load_history()

    def create_thread(self):
        if not self.project_root:
            return
        thread = create_workspace_thread(self.project_root, self.threads)
        self.threads = load_workspace_threads(self.project_root)
        self.thread_id = str(thread.get("id", DEFAULT_THREAD_ID))
        save_last_thread_id(self.project_root, self.thread_id)
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
        save_last_thread_id(self.project_root, self.thread_id)
        self.sidebar.set_threads(self.threads, self.thread_id)
        self.sidebar.set_tab("threads")

    def rename_thread(self, thread_id: str, title: str):
        thread_id = safe_thread_id(thread_id)
        title = title.strip()
        if not thread_id or not title:
            return
        if not rename_workspace_thread(self.project_root, thread_id, title, self.threads):
            styled_warning(self, "重命名失败", "无法保存这个会话名。")
            self.sidebar.set_threads(self.threads, self.thread_id)
            self.sidebar.set_tab("threads")
            return
        self.threads = load_workspace_threads(self.project_root)
        self.sidebar.set_threads(self.threads, self.thread_id)
        self.sidebar.set_tab("threads")

    def restore_history_entry(self, entry: Dict[str, object]):
        entry_type = entry.get("type")
        if entry_type == "prompt":
            if self.automation_enabled:
                full_prompt = str(entry.get("content", ""))
                display_text = str(entry.get("context_content") or "").strip() or self.prompt_bubble_display_text(full_prompt)
                if display_text.strip():
                    bubble = ChatBubble(
                        "user",
                        display_text,
                        parent=self.chat_container,
                        show_copy=False,
                        show_prompt_input=False,
                        scrollable=False,
                        max_content_height=90,
                        compact_user=True,
                    )
                    bubble.content = full_prompt
                    bubble.history_entry_id = entry.get("id")
                    self.add_chat_widget(bubble)
            else:
                self.add_prompt_bubble(str(entry.get("content", "")), save=False, animate=False)
        elif entry_type == "ai":
            bubble = ChatBubble(
                "ai",
                str(entry.get("content", "")),
                show_copy=True,
                parent=self.chat_container,
                copy_text="复制 AI 输出",
                scrollable=True,
                max_content_height=QT_WIDGET_MAX_HEIGHT,
                markdown=self.automation_enabled,
                expand_to_content=True,
                flat=self.automation_enabled,
            )
            self.add_chat_widget(bubble)
        elif entry_type == "result":
            bubble = ExecutionLogPanel(
                str(entry.get("content", "")),
                parent=self.chat_container,
                max_content_height=210,
                title="执行结果",
            )
            self.add_chat_widget(bubble)
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
                self.add_chat_widget(change_card)
        elif entry_type == "terminal_result":
            bubble = ExecutionLogPanel(
                str(entry.get("content", "")),
                parent=self.chat_container,
                max_content_height=210,
                title="终端执行结果",
            )
            self.add_chat_widget(bubble)

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
        self.flush_history_save(wait=True)
        self.history_save_generation += 1
        self.history_save_dirty = False
        if self.history_save_timer.isActive():
            self.history_save_timer.stop()
        if clear_workspace_history(self.project_root, self.thread_id):
            self.history_entries = []
            self.stop_automation_preview(remove_bubble=True)
            self.automation_active_messages = []
            self.automation_active_model = ""
            self.pending_provider_io = None
            self.cmd_outputs = []
            self.wechat_active_start_index = 0
            self.clear_chat_widgets()
            if self.automation_enabled:
                self.show_automation_composer(focus=False)
                self.update_automation_composer_state()
            else:
                self.ensure_initial_prompt_bubble()
            self.update_prompt_tools_responsive()
            self.scroll_to_bottom()
        else:
            styled_warning(self, "清空失败", "无法删除当前工作区的 .agent_qt 缓存目录。")

    def shutdown(self):
        if self._shutdown_done:
            return
        self._shutdown_done = True
        self.flush_history_save(wait=True)
        self.wait_for_history_save_workers(timeout_ms=5000)
        self.stop_automation_preview(remove_bubble=False)
        try:
            self.automation_request_serial += 1
            for worker in (
                self.automation_worker,
                self.automation_context_worker,
                self.skill_generation_worker,
            ):
                if worker is not None:
                    worker.requestInterruption()
            self.automation_manager.stop_provider_process(wait_timeout=0.8, aggressive=True)
        except Exception:
            logger.warning("Failed to stop automation provider during app shutdown.", exc_info=True)
        if hasattr(self, "wechat_connector"):
            self.wechat_connector.stop(notify=False)
        if hasattr(self, "wechat_bridge"):
            self.wechat_bridge.stop()

    def add_ai_response_frame(self, focus: bool = True, animate: bool = True, keep_visible: bool = True):
        if self.automation_enabled:
            self.show_automation_composer(focus=focus)
            return
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
                border: 1px solid {ai_border_color()};
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
        ai_input.setStyleSheet(self.ai_manual_input_style())
        ai_frame.ai_input = ai_input
        ai_layout.addWidget(ai_input)
        
        self.add_chat_widget(ai_frame, animate=animate)
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

    def remove_ai_response_frame(self):
        frame = self.find_open_ai_response_frame()
        if frame is None:
            return
        frame.is_closing = True
        self.chat_layout.removeWidget(frame)
        frame.deleteLater()
    
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
        self.handle_ai_response_text(text, insert_index=idx)

    def finalize_automation_preview_bubble(self, text: str) -> Optional[ChatBubble]:
        bubble = self.automation_preview_bubble
        if not isinstance(bubble, ChatBubble):
            return None
        scroll_state = self.capture_chat_scroll_state()
        status = getattr(bubble, "preview_status", None)
        if status is not None:
            status.hide()
            status.setParent(None)
            status.deleteLater()
            bubble.preview_status = None
        role_label = bubble.findChild(QLabel)
        if role_label is not None:
            role_label.setText("AI")
        bubble.update_content(text)
        self.stabilize_chat_scroll_after_update(scroll_state)
        self.automation_preview_bubble = None
        return bubble

    def handle_ai_response_text(
        self,
        text: str,
        insert_index: Optional[int] = None,
        existing_bubble: Optional[ChatBubble] = None,
        provider_io: Optional[Dict[str, object]] = None,
    ):
        text = (text or "").strip()
        if not text:
            return
        if self.automation_enabled and looks_like_automation_context_payload(text):
            self.stop_automation_loop(
                "provider 返回了内部上下文包，自动化循环已暂停。请重新发送或检查 provider 页面复制结果。",
                ensure_manual_entry=True,
            )
            self.scroll_to_bottom()
            return
        done_response = self.automation_loop_active and is_automation_done_response(text)
        display_text = strip_automation_done_marker(text) if done_response else text
        wechat_triggers: List[str] = []
        wechat_trigger_context_text = ""
        done_context_text = ""
        terminal_extension_triggers: List[str] = []
        terminal_extension_result_log = ""
        web_research_directives: List[str] = []
        if self.automation_loop_active:
            terminal_extension_triggers = terminal_extension_directives_from_text(display_text)
        else:
            terminal_extension_triggers = terminal_extension_directives_from_text(display_text)
        if self.automation_loop_active and self.wechat_active_request_id:
            if terminal_extension_triggers:
                wechat_triggers = terminal_extension_triggers
                stripped_display_text = strip_terminal_extension_directives_from_text(display_text)
                blocks_after_strip = scan_all_code_blocks(stripped_display_text)
                command_after_strip, _command_lang = command_block_from_blocks(blocks_after_strip)
                has_real_command = bool((command_after_strip or "").strip())
                if not has_real_command:
                    schedule_payloads, schedule_actions, schedule_errors = collect_schedule_extension_payloads(
                        "\n".join(terminal_extension_triggers)
                    )
                    file_targets = extract_wechat_send_file_targets("\n".join(terminal_extension_triggers))
                    has_schedule_directive = bool(schedule_payloads or schedule_actions or schedule_errors)
                    if has_schedule_directive:
                        created: List[str] = []
                        action_replies: List[str] = []
                        errors: List[str] = list(schedule_errors)
                        if self.project_root:
                            c, a, e = apply_schedule_extension_payloads(
                                self.project_root,
                                schedule_payloads,
                                schedule_actions,
                            )
                            created.extend(c)
                            action_replies.extend(a)
                            errors.extend(e)
                            if c or a:
                                QTimer.singleShot(1200, self.check_due_schedules)
                        else:
                            errors.append("未设置工作区，无法处理定时计划。")
                        display_text = schedule_extension_reply(created, action_replies, errors) or "已处理定时计划。"
                    elif file_targets:
                        display_text = wechat_trigger_summary(terminal_extension_triggers)
                        if not getattr(self, "active_schedule_notify", None):
                            sent_files, failed_files = self.send_wechat_files_to_last_target(file_targets)
                            delivery_parts: List[str] = []
                            if sent_files:
                                delivery_parts.append("微信附件已发送：" + "、".join(sent_files))
                            if failed_files:
                                delivery_parts.append("微信附件发送失败：" + "；".join(failed_files))
                            display_text = (display_text + "\n\n" + "\n".join(delivery_parts or ["未发送附件。"])).strip()
                    else:
                        display_text = wechat_trigger_summary(wechat_triggers)
                    terminal_extension_result_log = terminal_extension_execution_log(wechat_triggers, display_text)
                    done_response = True
                else:
                    display_text = stripped_display_text
            else:
                wechat_triggers = echoed_wechat_trigger_lines(display_text)
            if wechat_triggers:
                wechat_trigger_context_text = (
                    wechat_trigger_summary(wechat_triggers)
                    + "\n\n"
                    + "\n".join(wechat_triggers)
                ).strip()
                done_context_text = wechat_trigger_context_text
                if not terminal_extension_triggers:
                    display_text = wechat_trigger_summary(wechat_triggers)
                    done_response = True
            elif (
                not done_response
                and display_text.strip()
                and not looks_like_incomplete_plain_response(display_text)
                and not scan_all_code_blocks(display_text)
            ):
                done_response = True
                done_context_text = display_text
        elif self.automation_loop_active and terminal_extension_triggers:
            stripped_display_text = strip_terminal_extension_directives_from_text(display_text)
            blocks_after_strip = scan_all_code_blocks(stripped_display_text)
            command_after_strip, _command_lang = command_block_from_blocks(blocks_after_strip)
            has_real_command = bool((command_after_strip or "").strip())
            schedule_payloads, schedule_actions, schedule_errors = collect_schedule_extension_payloads(
                "\n".join(terminal_extension_triggers)
            )
            file_targets = extract_wechat_send_file_targets("\n".join(terminal_extension_triggers))
            web_research_queries = extract_web_research_queries("\n".join(terminal_extension_triggers))
            has_schedule_directive = bool(schedule_payloads or schedule_actions or schedule_errors)
            if has_schedule_directive and not has_real_command:
                created: List[str] = []
                action_replies: List[str] = []
                errors: List[str] = list(schedule_errors)
                if self.project_root:
                    c, a, e = apply_schedule_extension_payloads(
                        self.project_root,
                        schedule_payloads,
                        schedule_actions,
                    )
                    created.extend(c)
                    action_replies.extend(a)
                    errors.extend(e)
                    if c or a:
                        QTimer.singleShot(1200, self.check_due_schedules)
                else:
                    errors.append("未设置工作区，无法处理定时计划。")
                display_text = schedule_extension_reply(created, action_replies, errors) or "已处理定时计划。"
                terminal_extension_result_log = terminal_extension_execution_log(terminal_extension_triggers, display_text)
                done_response = True
            elif web_research_queries and not has_real_command:
                self.set_status_bar_override("网页搜索进行中，正在等待结果…", duration_ms=15000)
                web_research_directives = list(terminal_extension_triggers)
            elif file_targets and not has_real_command:
                display_text = wechat_trigger_summary(terminal_extension_triggers)
                done_context_text = (display_text + "\n\n" + "\n".join(terminal_extension_triggers)).strip()
                if not getattr(self, "active_schedule_notify", None):
                    sent_files, failed_files = self.send_wechat_files_to_last_target(file_targets)
                    delivery_parts: List[str] = []
                    if sent_files:
                        delivery_parts.append("微信附件已发送：" + "、".join(sent_files))
                    if failed_files:
                        delivery_parts.append("微信附件发送失败：" + "；".join(failed_files))
                    display_text = (display_text + "\n\n" + "\n".join(delivery_parts or ["未发送附件。"])).strip()
                terminal_extension_result_log = terminal_extension_execution_log(terminal_extension_triggers, display_text)
                done_response = True
        if done_response and not display_text:
            self.finish_active_schedule_success("任务已执行完成。")
            self.stop_automation_loop("自动化执行完成。", ensure_manual_entry=True)
            self.scroll_to_bottom()
            return
        scroll_state = self.capture_chat_scroll_state()
        self.hide_empty_state()
        if existing_bubble is not None:
            ai_bubble = existing_bubble
            ai_bubble.content = display_text
            ai_bubble.update_content(display_text)
        else:
            ai_bubble = ChatBubble(
                "ai",
                display_text,
                show_copy=True,
                parent=self.chat_container,
                copy_text="复制 AI 输出",
                scrollable=True,
                max_content_height=QT_WIDGET_MAX_HEIGHT,
                markdown=self.automation_enabled,
                expand_to_content=True,
                flat=self.automation_enabled,
            )
            if insert_index is None:
                self.add_chat_widget(ai_bubble)
            else:
                self.insert_chat_widget(insert_index, ai_bubble)
            animate_widget_in(ai_bubble)
        history_entry = {
            "type": "ai",
            "content": display_text,
        }
        if wechat_trigger_context_text:
            history_entry["context_content"] = wechat_trigger_context_text
        if isinstance(provider_io, dict):
            history_entry["provider_io"] = copy.deepcopy(provider_io)
        self.append_history(history_entry)
        self.stabilize_chat_scroll_after_update(scroll_state)
        QApplication.processEvents()

        if done_response:
            if terminal_extension_result_log:
                self.add_execution_result_entry(
                    terminal_extension_result_log,
                    context_content=build_execution_context_content(terminal_extension_result_log, []),
                )
            self.finish_active_schedule_success(done_context_text or display_text)
            self.stop_automation_loop("", ensure_manual_entry=True)
            self.scroll_to_bottom()
            return

        if web_research_directives:
            self.start_web_research_extension_run(web_research_directives)
            self.scroll_to_bottom()
            return
        
        blocks = scan_all_code_blocks(display_text)
        try:
            commands = extract_bash_commands(display_text, blocks)
        except ValueError as exc:
            rejection_text = f"⚠️ {exc}"
            warning_bubble = ChatBubble(
                "system",
                rejection_text,
                parent=self.chat_container,
                scrollable=False,
                max_content_height=130,
            )
            self.add_chat_widget(warning_bubble, animate=True)
            if self.automation_loop_active:
                context_content = build_execution_context_content(
                    f"Local execution rejected:\n{rejection_text}",
                    [],
                )
                self.append_history({
                    "type": "result",
                    "content": rejection_text,
                    "context_content": context_content,
                })
                self.request_next_automation_step(context_content)
            self.scroll_to_bottom()
            return
        
        if not commands:
            if terminal_extension_triggers:
                web_research_queries = extract_web_research_queries("\n".join(terminal_extension_triggers))
                if web_research_queries:
                    self.start_web_research_extension_run(terminal_extension_triggers)
                    self.scroll_to_bottom()
                    return
                execution_text = terminal_extension_result_log or self.execute_terminal_extension_directives(
                    terminal_extension_triggers
                )
                if execution_text:
                    context_content = build_execution_context_content(execution_text, [])
                    self.add_execution_result_entry(execution_text, context_content=context_content)
                    if self.automation_loop_active:
                        self.request_next_automation_step(context_content)
                    else:
                        self.ensure_ai_response_entry(focus=False, animate=True, keep_visible=False)
                    self.scroll_to_bottom()
                    return
            if self.automation_loop_active:
                if looks_like_noop_plain_automation_response(display_text):
                    self.finish_active_schedule_success(display_text)
                    self.stop_automation_loop("", ensure_manual_entry=True)
                    self.scroll_to_bottom()
                    return
                rejection_text = (
                    f"⚠️ 未识别到可执行命令，也没有检测到 {AUTOMATION_DONE_MARKER} 完成标记。\n"
                    f"自动化循环需要继续时必须输出一个完整的 ```{runtime_environment()['command_block_lang']} "
                    f"命令块；已完成时必须输出 {AUTOMATION_DONE_MARKER} 加简短总结。"
                )
                warning_bubble = ChatBubble(
                    "system",
                    rejection_text,
                    parent=self.chat_container,
                    scrollable=False,
                    max_content_height=140,
                )
                self.add_chat_widget(warning_bubble, animate=True)
                context_content = build_execution_context_content(
                    f"Local execution rejected:\n{rejection_text}",
                    [],
                )
                self.append_history({
                    "type": "result",
                    "content": rejection_text,
                    "context_content": context_content,
                })
                self.request_next_automation_step(context_content)
                self.scroll_to_bottom()
                return
            warning_bubble = ChatBubble(
                "system",
                f"⚠️ 未识别到可执行命令\n请确保 AI 输出包含 ```{runtime_environment()['command_block_lang']} 代码块",
                parent=self.chat_container,
                scrollable=False,
                max_content_height=110,
            )
            self.add_chat_widget(warning_bubble, animate=True)
            self.ensure_ai_response_entry(focus=False, animate=True, keep_visible=False)
            self.scroll_to_bottom()
            return
        if self.automation_loop_active:
            self.refresh_prompt_bubble_buttons()
        
        self.result_bubble = ExecutionLogPanel(
            "⏳ 执行中...",
            parent=self.chat_container,
            max_content_height=210,
            title="执行结果",
        )
        self.add_chat_widget(self.result_bubble, animate=True)
        
        self.cmd_outputs = []
        self.pending_snapshot = {}
        self.pending_internal_git_commit = ""
        if self.change_tracker is not None:
            try:
                self.pending_internal_git_commit = self.change_tracker.prepare_before()
            except Exception:
                self.pending_internal_git_commit = ""
        if not self.pending_internal_git_commit:
            self.pending_snapshot = snapshot_project(self.project_root)
        self.pending_long_running_launches = 0
        self.pending_terminal_launches = []
        self.pending_terminal_extension_triggers = list(terminal_extension_triggers)

        self.worker = ExecuteWorker(commands, self.project_root)
        self.worker.output_signal.connect(self.on_output)
        self.worker.long_running_signal.connect(self.on_long_running)
        self.worker.background_process_signal.connect(self.on_background_process_started)
        self.worker.finished_signal.connect(self.on_finished)
        self.worker.start()
        self.update_automation_composer_state()
        self.scroll_to_bottom()
    
    def on_output(self, output: str):
        self.cmd_outputs.append(output)
        self.result_bubble.update_content('\n'.join(self.cmd_outputs))
    
    def on_long_running(self, cmd: str, cwd: str, name: str, launch_reason: str = "long_running_pattern", kind: str = "unknown"):
        proc = self.terminal_panel.add_process(
            cmd,
            cwd,
            name,
            expected_persistent=True,
            launch_reason=launch_reason,
            command_kind_value=kind or command_kind(cmd),
        )
        if proc is not None:
            if proc.process is not None and not proc.process_id():
                try:
                    proc.process.waitForStarted(800)
                except RuntimeError:
                    pass
            self.terminal_panel.write_process_registry()
            self.pending_terminal_launches.append({
                "id": proc.terminal_id,
                "name": proc.name,
                "cmd": strip_shell_command_marker(proc.cmd),
                "cwd": proc.cwd,
                "pid": proc.process_id(),
                "status": "running" if proc.is_running() else "starting",
                "persistent": True,
                "launch_reason": launch_reason,
                "command_kind": kind or command_kind(cmd),
                "log_path": proc.log_path,
                "registry_path": self.terminal_panel.registry_path(),
                "_proc": proc,
            })
        self.pending_long_running_launches += 1
        self.update_status_bar()
        self.keep_ai_response_visible()

    def on_background_process_started(self, info: Dict[str, object]):
        proc = self.terminal_panel.add_external_process(info)
        if proc is not None:
            self.pending_terminal_launches.append({
                "id": proc.terminal_id,
                "name": proc.name,
                "cmd": strip_shell_command_marker(proc.cmd),
                "cwd": proc.cwd,
                "pid": proc.process_id(),
                "status": "running" if proc.is_running() else "starting",
                "persistent": proc.expected_persistent,
                "launch_reason": proc.launch_reason,
                "command_kind": proc.command_kind,
                "log_path": proc.log_path,
                "registry_path": self.terminal_panel.registry_path(),
                "_proc": proc,
            })
        self.pending_long_running_launches += 1
        self.update_status_bar()
        self.keep_ai_response_visible()

    def on_terminal_process_finished(self, info: Dict[str, object]):
        log = str(info.get("log") or "").strip()
        if not log:
            return
        content = build_terminal_context_content(info)
        self.append_history({
            "type": "terminal_result",
            "content": content,
            "context_content": content,
            "terminal": {
                "name": str(info.get("name") or ""),
                "cmd": str(info.get("cmd") or ""),
                "cwd": str(info.get("cwd") or ""),
                "exit_code": info.get("exit_code"),
                "interactive": bool(info.get("interactive")),
                "expected_persistent": bool(info.get("expected_persistent")),
                "launch_reason": str(info.get("launch_reason") or ""),
                "command_kind": str(info.get("command_kind") or ""),
                "pid": info.get("pid") or 0,
                "log_path": str(info.get("log_path") or ""),
            },
        })
    
    def on_finished(self, full_log: str):
        worker = self.worker
        self.worker = None
        if worker is not None:
            worker.deleteLater()
        change_records: List[Dict[str, object]] = []
        if self.change_tracker is not None and self.pending_internal_git_commit:
            try:
                change_records = self.change_tracker.capture_changes(self.pending_internal_git_commit)
            except Exception:
                change_records = []
        if not change_records and self.pending_snapshot:
            after_snapshot = snapshot_project(self.project_root)
            change_records = build_change_records(self.pending_snapshot, after_snapshot)
        self.pending_snapshot = {}
        self.pending_internal_git_commit = ""
        terminal_extension_triggers = list(getattr(self, "pending_terminal_extension_triggers", []) or [])
        self.pending_terminal_extension_triggers = []
        long_running_launches = self.pending_long_running_launches
        self.pending_long_running_launches = 0
        terminal_launches = []
        for item in self.pending_terminal_launches:
            normalized = dict(item)
            proc = normalized.pop("_proc", None)
            if isinstance(proc, ManagedProcess):
                normalized["pid"] = proc.process_id()
                normalized["status"] = "running" if proc.is_running() else "exited"
                normalized["log_path"] = proc.log_path
            terminal_launches.append(normalized)
        self.pending_terminal_launches = []
        log_with_changes = full_log
        if change_records:
            log_with_changes += format_change_summary(change_records, include_diff=False)
        elif long_running_launches:
            log_with_changes += "\n\n文件变更：\n未检测到文件改动。若命令正在底部终端继续运行，保存/生成文件后需要等待进程结束或再执行一次检查。"
        elif not log_with_changes.strip():
            log_with_changes = "命令执行完成，未产生终端输出或文件变更。"
        terminal_extension_log = ""
        if terminal_extension_triggers:
            terminal_extension_log = self.execute_terminal_extension_directives(terminal_extension_triggers)
            if terminal_extension_log:
                log_with_changes = (log_with_changes + "\n\n" + terminal_extension_log).strip()
                full_log = (full_log + "\n\n" + terminal_extension_log).strip()
        context_content = build_execution_context_content(full_log, change_records, long_running_launches, terminal_launches)
        self.result_bubble.update_content(log_with_changes)
        if change_records:
            change_card = ChangeSummaryCard(change_records, parent=self.chat_container)
            change_card.undo_requested.connect(self.undo_changes)
            change_card.redo_requested.connect(self.redo_changes)
            self.add_chat_widget(change_card, animate=True)
        result_entry = {
            "type": "result",
            "content": log_with_changes,
            "context_content": context_content,
            "changes": [serialize_change_record(record) for record in change_records],
            "undone": False,
        }
        if change_records:
            result_entry["id"] = uuid.uuid4().hex
            change_card.history_entry_id = result_entry["id"]
        self.append_history(result_entry)
        self.sidebar.refresh_tree(self.project_root)
        self.update_status_bar()
        self.update_automation_composer_state()
        if self.automation_enabled and self.automation_loop_active:
            self.request_next_automation_step(context_content, skip_entry_id=str(result_entry.get("id") or ""))
        elif self.automation_enabled:
            self.show_automation_composer(focus=False)
        else:
            self.ensure_ai_response_entry(focus=False, animate=True, keep_visible=False)
        self.scroll_to_bottom()

    def request_next_automation_step(self, log_with_changes: str, skip_entry_id: str = ""):
        if not self.automation_loop_active:
            return
        if self.automation_loop_round >= self.automation_loop_max_rounds:
            if self.automation_loop_force_final_summary:
                self.stop_automation_loop(
                    f"已达到自动化最大轮数 {self.automation_loop_max_rounds}，且最终总结轮仍未正常收束，循环已暂停。你可以检查结果后继续发送。",
                    ensure_manual_entry=True,
                )
                return
            self.automation_loop_force_final_summary = True
        else:
            self.automation_loop_round += 1
        previous_ai_response = ""
        for entry in reversed(self.history_entries):
            if str(entry.get("type") or "") == "ai":
                previous_ai_response = str(entry.get("context_content") or entry.get("content") or "").strip()
                break
        prompt = build_automation_feedback_prompt(
            self.project_root,
            self.automation_loop_goal,
            log_with_changes,
            self.automation_loop_round,
            self.automation_loop_max_rounds,
            wechat_file_delivery=bool(
                self.wechat_active_request_id
                and self.wechat_active_to_user
                and self.wechat_active_context_token
            ),
            previous_ai_response=previous_ai_response,
            force_final_summary=self.automation_loop_force_final_summary,
        )
        self.start_automation_worker(
            prompt,
            "",
            skip_entry_id=skip_entry_id,
        )

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
        self.setWindowTitle("Agent. QT智能体 · 你的编程办公调研实验远控助手")
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

    def shutdown(self):
        self.chat_page.shutdown()

    def closeEvent(self, event):
        self.hide()
        QApplication.processEvents()
        self.shutdown()
        super().closeEvent(event)

def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setFont(QFont("PingFang SC", 13))
    app_icon_path = find_bundled_asset("app_icon.png")
    if app_icon_path:
        app_icon = QIcon(app_icon_path)
        app.setWindowIcon(app_icon)
    app.setStyleSheet(app_global_style())
    window = MainWindow()
    if app_icon_path:
        window.setWindowIcon(QIcon(app_icon_path))
    app.aboutToQuit.connect(window.shutdown)
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
