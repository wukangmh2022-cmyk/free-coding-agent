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
from typing import Dict, List, Optional
from datetime import datetime

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTextEdit, QPushButton, QLabel, QScrollArea, QFrame,
    QFileDialog, QMessageBox, QLineEdit, QTreeWidget, QTreeWidgetItem,
    QMenu, QToolButton, QStyle, QPlainTextEdit, QTextBrowser, QStackedWidget,
    QGridLayout, QSizePolicy, QGraphicsOpacityEffect, QAbstractItemView,
    QSpacerItem, QWidgetAction, QAbstractButton
)
from PySide6.QtCore import Qt, QTimer, Signal, QThread, QProcess, QProcessEnvironment, QPropertyAnimation, QEasingCurve, QSize, QByteArray, QEvent, QRectF, QPoint, Property
from PySide6.QtGui import QFont, QAction, QDesktopServices, QMouseEvent, QTextCursor, QIcon, QPixmap, QPainter, QPen, QColor, QKeySequence, QTextDocument, QImage
from PySide6.QtCore import QUrl

try:
    from PySide6.QtSvg import QSvgRenderer
except ImportError:
    QSvgRenderer = None

PROMPT_BUBBLE_MARKER = "<!-- agent_qt_user_prompt:"
AUTOMATION_DONE_MARKER = "AGENT_QT_DONE"
COMPLETION_LINE_RE = re.compile(r"^\s*(?:FINAL\s*:|AGENT_QT_DONE\b)", re.I)
AGENT_HOME_DIR = os.path.expanduser(os.environ.get("AGENT_QT_HOME", "~/.agent_qt"))
_AGENT_RUNTIME_PYTHON: Optional[str] = None
_AGENT_RUNTIME_ERROR = ""
_APP_SETTINGS: Optional[Dict[str, object]] = None
_AGENT_RUNTIME_ENABLED: Optional[bool] = None
_AUTOMATION_ENABLED: Optional[bool] = None
QT_WIDGET_MAX_HEIGHT = 16777215
DEFAULT_PIP_INDEX_URL = "https://pypi.tuna.tsinghua.edu.cn/simple"
logger = logging.getLogger(__name__)


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


def developer_error_details_enabled() -> bool:
    return os.environ.get("AGENT_QT_SHOW_AUTOMATION_TRACEBACK", "").strip().lower() in {"1", "true", "yes", "on"}


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


WINDOWS_PYTHON_INSTALLER_URL = "https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe"


def ps_quote(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def ps_array(args: List[str]) -> str:
    return "@(" + ", ".join(ps_quote(str(arg)) for arg in args) + ")"


def windows_python_bootstrap_powershell() -> str:
    base_python_dir = os.path.join(runtime_cache_root(), "python312")
    return f"""
$BasePython = $null
$BasePythonDir = {ps_quote(base_python_dir)}

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
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -Uri {ps_quote(WINDOWS_PYTHON_INSTALLER_URL)} -OutFile $installer
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
        if ($lastExit -eq 0) { return }
        Write-Host "配置的 PyPI 镜像失败，回退官方 PyPI..."
    }
    & $PythonBin -m pip install @Arguments
    $lastExit = $LASTEXITCODE
    if ($lastExit -eq 0) { return }
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
"""

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

## 协议说明
本 Agent 采用"占位符 + 代码块"协议：你在命令代码块中用注释占位符（如 <!-- HTML block -->）替代大段代码，
然后将完整代码放在对应的 Markdown 代码块中。Agent 会先缓存所有代码块，再执行命令代码块并自动替换占位符。
这样既能保持指令清晰，又能直接写入完整文件。

## 当前运行环境
- 操作系统: {os_name}
- 平台标识: {platform_id}
- 默认 Shell: {shell_name}
- 本轮命令工具: {command_shell_name}
- 命令执行方式: {command_execution}
- 命令代码块语言: {command_block_lang}
- 路径风格: {path_style}
- Python 运行时: {python_runtime}
- **重要：所有命令行指令必须匹配当前操作系统和本轮命令工具，不要输出其他系统或其他 shell 的命令。**
- **Windows 特别重要：如果命令代码块语言是 powershell，就只能写 PowerShell；不要混用 cmd/bat 或 bash。**

## 推理要求
- Reasoning Effort: absolute maximum with no shortcuts permitted. You must thoroughly decompose the task, identify the root cause, and stress-test the solution against likely paths, edge cases, and adversarial scenarios before answering.
- 输出时不要展开隐藏思考链；只给出关键判断、可验证依据、最终方案和必要的执行指令。

## 输出规则
1. **所有命令放在一个 Markdown fenced `{command_block_lang}` 代码块中**，不要拆分多个命令块。
   - 不要输出 JSON 对象，不要输出 content/tool_calls 包装，不要使用结构化工具调用协议。
   - 不要把 `{command_block_lang}` 作为普通正文单独输出；语言标识只能写在 Markdown 代码围栏里。
   - {command_rules}
2. 大段文件内容用占位符替代，支持的占位符：
   - <!-- HTML block --> 对应 html 代码块
   - <!-- CSS block --> 或 /* CSS block */ 对应 css 代码块
   - <!-- JS block --> 或 // JS block 对应 js/javascript 代码块
   - <!-- Python block --> 或 # Python block 对应 python 代码块
   - <!-- SVG block --> 对应 svg 代码块
   - <!-- JSON block --> 对应 json 代码块
   - <!-- YAML block --> 对应 yaml 代码块
   - <!-- TypeScript block --> 对应 typescript/ts 代码块
   - <!-- 其他任意类型 block --> 对应该类型名的代码块（如 svg/xml/toml 等）
3. **占位符与代码块的对应规则**：
   - 未编号占位符按出现顺序消费同语言代码块：两个 `<!-- Python block -->` 需要两个后续 ```python 代码块。
   - 编号占位符固定引用对应序号的同语言代码块：`<!-- Python block 1 -->`、`# Python block 1` 都引用第 1 个 ```python 代码块。
   - 同一个编号可以重复使用；如果同一份代码需要替换到多个位置，重复写 `<!-- Python block 1 -->` 即可，不要重复提供同一份代码块。
4. 各代码块在命令块之后单独给出。
5. 指令按顺序排列，先创建目录再写文件，确保可直接执行。
6. 项目根目录: {project_root}，所有路径使用绝对路径。
7. **重要：一个命令块包含所有指令，不要输出多个命令块。**
8. **禁止输出备用方案或二选一方案**：
   - 不要写“如果上面失败/或者改用/备用方案/方案 A 和方案 B”。
   - 不要在同一轮里同时保留两种互相替代的做法。
   - 你必须自己选择一个最高把握方案，只输出这一种可执行路径。
9. 安装依赖用 pip install，启动后端用 python server.py 或 python3 -m http.server。
10. 常驻进程命令（python server.py 等）会自动进入后台终端，不要加 & 或 nohup。
11. 自动化循环中，如果根据执行日志判断任务已经完成，不要再输出命令块，回复 `{done_marker}` 加简短总结即可；如果未完成，继续输出下一轮完整命令块。

---

{user_prompt}"""

# ============================================================
# 配置
# ============================================================
HISTORY_DIR_NAME = ".agent_qt"
HISTORY_FILE_NAME = "history.json"
THREADS_DIR_NAME = "threads"
THREADS_INDEX_FILE_NAME = "threads.json"
WORKSPACE_STATE_FILE_NAME = "workspace.json"
DEFAULT_THREAD_ID = "default"
HISTORY_VERSION = 1

FORBIDDEN = [
    "rm -rf /", "sudo rm", "sudo reboot", "shutdown",
    "mkfs", "dd if=", ":(){ :|:& };:",
]
POWERSHELL_COMMAND_PREFIX = "__AGENT_QT_POWERSHELL__\n"

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
        self.setStyleSheet(f"""
            QWidget {{
                background: {COLORS['surface']};
                border-radius: 12px;
            }}
            QWidget:hover {{
                background: #f8fbff;
            }}
            QLabel {{
                background: transparent;
                border: none;
            }}
        """)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 9, 10, 9)
        layout.setSpacing(12)
        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(2)
        title_label = QLabel(title)
        title_label.setStyleSheet(f"color: {COLORS['text']}; font-size: 13px; font-weight: 900;")
        subtitle_label = QLabel(subtitle)
        subtitle_label.setStyleSheet(f"color: {COLORS['text_secondary']}; font-size: 11px; font-weight: 700;")
        text_layout.addWidget(title_label)
        text_layout.addWidget(subtitle_label)
        layout.addLayout(text_layout, 1)
        self.switch = ToggleSwitch(checked)
        self.switch.toggled.connect(self.toggled.emit)
        layout.addWidget(self.switch, 0, Qt.AlignmentFlag.AlignVCenter)

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

    for line in lines:
        if in_code:
            if is_closing_fence(line):
                flush_code()
                in_code = False
                continue
            if COMPLETION_LINE_RE.match(line):
                flush_code()
                in_code = False
                buffer.append(line)
                continue
            code_buffer.append(line)
            continue

        match = opening_fence(line)
        if match:
            flush_markdown()
            in_code = True
            fence = match.group(1)
            fence_char = fence[0]
            fence_len = len(fence)
            code_lang = (match.group(2) or "").strip().split(maxsplit=1)[0] if (match.group(2) or "").strip() else ""
            continue
        buffer.append(line)

    if in_code:
        flush_code()
    else:
        flush_markdown()
    return [part for part in parts if part.get("text", "").strip()]

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
    blocks: Dict[str, List[str]] = {}
    lines = (text or "").splitlines(keepends=True)
    in_code = False
    fence_char = ""
    fence_len = 0
    lang = ""
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
                blocks.setdefault(key, []).append(code.strip())
                in_code = False
                fence_char = ""
                fence_len = 0
                lang = ""
                code_lines = []
            else:
                code_lines.append(line)
            continue
        match = opening_fence(line)
        if not match:
            continue
        fence = match.group(1)
        raw_info = (match.group(2) or "").strip()
        lang = raw_info.split(maxsplit=1)[0] if raw_info else ""
        fence_char = fence[0]
        fence_len = len(fence)
        in_code = True
        code_lines = []
    return blocks

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
    - <!-- XXX block 1 -->
    - # XXX block
    - # XXX block 1
    其中 XXX 对应 blocks 中的 key（html/css/js/python/svg/json/yaml/typescript/ts...）
    """
    counters: Dict[str, int] = {}
    missing: List[str] = []
    placeholder_pattern = re.compile(
        r'<!--\s*(?P<html>\w+)\s+block(?:\s+(?P<html_index>\d+))?\s*-->'
        r'|/\*\s*(?P<css>\w+)\s+block(?:\s+(?P<css_index>\d+))?\s*\*/'
        r'|//\s*(?P<slash>\w+)\s+block(?:\s+(?P<slash_index>\d+))?\b'
        r'|#\s*(?P<hash>\w+)\s+block(?:\s+(?P<hash_index>\d+))?\b'
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
        if index_text:
            block_index = max(0, int(index_text) - 1)
            code = get_code_block(blocks, lang, block_index)
        else:
            code = get_next_code_block(blocks, counters, lang)
        if code is None:
            suffix = f" block {index_text}" if index_text else ""
            missing.append(canonical_lang(lang) + suffix)
            return match.group(0)
        return code

    resolved = placeholder_pattern.sub(replace, bash_text)
    if missing:
        unique_missing = ", ".join(sorted(set(missing)))
        missing_counts = ", ".join(
            f"{lang}×{count}" for lang, count in sorted(
                {lang: missing.count(lang) for lang in set(missing)}.items()
            )
        )
        raise ValueError(
            f"缺少占位符对应的代码块：{unique_missing}（缺少 {missing_counts}）。"
            "未编号占位符会按顺序消费代码块；如需多处复用同一代码块，请使用编号占位符，例如 <!-- Python block 1 -->。"
            "为避免覆盖文件，本轮已停止执行。"
        )
    if placeholder_pattern.search(resolved):
        raise ValueError("仍有未替换的占位符。为避免覆盖文件，本轮已停止执行。")
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


def looks_like_raw_shell_script(text: str) -> bool:
    """仅在用户粘贴的是纯命令片段时，才允许无 fenced 命令块的兼容模式。"""
    stripped = (text or "").strip()
    if not stripped or "```" in stripped:
        return False
    lines = [line.strip() for line in stripped.splitlines() if line.strip()]
    if not lines:
        return False
    if lines[0].lower() in {"bash", "sh", "shell", "zsh", "powershell", "pwsh", "cmd"}:
        return False
    shell_starters = (
        "$ ",
        "cd ",
        "mkdir ",
        "touch ",
        "cat ",
        "tee ",
        "echo ",
        "printf ",
        "python ",
        "python3 ",
        "node ",
        "npm ",
        "pnpm ",
        "yarn ",
        "npx ",
        "pip ",
        "uv ",
        "git ",
        "cp ",
        "mv ",
        "rm ",
        "chmod ",
        "open ",
        "curl ",
        "cat >",
    )
    commandish = 0
    for line in lines:
        lowered = line.lower()
        if lowered.startswith(("#", "//")):
            commandish += 1
            continue
        if lowered.startswith(shell_starters) or any(token in line for token in (" && ", " | ", " > ", " <<")):
            commandish += 1
            continue
        return False
    return commandish > 0

def command_block_from_blocks(blocks: Dict[str, List[str]]) -> tuple[str, str]:
    if platform.system() == "Windows":
        powershell_text = get_code_block(blocks, "powershell") or get_code_block(blocks, "ps1") or get_code_block(blocks, "pwsh") or ""
        if powershell_text:
            return powershell_text, "powershell"
        cmd_text = get_code_block(blocks, "cmd") or get_code_block(blocks, "bat") or get_code_block(blocks, "batch") or ""
        if cmd_text:
            return cmd_text, "cmd"
    return get_code_block(blocks, "bash") or "", "bash"


def extract_bash_commands(text: str, blocks: Dict[str, List[str]]) -> List[str]:
    """提取当前平台命令块并替换占位符。"""
    command_text, command_lang = command_block_from_blocks(blocks)
    if not command_text:
        plain_block = get_code_block(blocks, "text") or ""
        if plain_block and looks_like_raw_shell_script(plain_block):
            command_text = plain_block
        elif not looks_like_raw_shell_script(text):
            return []
        else:
            command_text = text
    if not command_text:
        return []
    command_text = resolve_all_placeholders(command_text, blocks)
    if command_lang == "powershell":
        return [POWERSHELL_COMMAND_PREFIX + command_text.strip()] if command_text.strip() else []
    lines = command_text.strip().splitlines()
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
        if is_interactive_shell_command(line):
            continue

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
    cmd = strip_shell_command_marker(cmd)
    detection_cmd = strip_heredoc_bodies_for_detection(cmd).strip() or cmd
    command_text = detection_cmd.lower()
    first_line = detection_cmd.splitlines()[0].lower().strip() if detection_cmd.splitlines() else ""
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


def run_shell_command_capture(cmd: str, cwd: str, timeout: int) -> subprocess.CompletedProcess:
    if platform.system() == "Windows" and is_powershell_command(cmd):
        script_path = write_temp_shell_script(cmd)
        shell, args = shell_launch_for_command(cmd, script_path=script_path)
        try:
            return subprocess.run(
                [shell, *args],
                cwd=cwd,
                env=agent_runtime_env(create=False),
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        finally:
            try:
                os.remove(script_path)
            except OSError:
                pass
    return subprocess.run(
        strip_shell_command_marker(cmd),
        shell=True,
        cwd=cwd,
        env=agent_runtime_env(create=False),
        capture_output=True,
        text=True,
        timeout=timeout,
    )


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
        )

    def ensure_repo(self) -> bool:
        if not self.available or not os.path.isdir(self.project_root):
            return False
        os.makedirs(self.repo_root, exist_ok=True)
        if not os.path.isdir(os.path.join(self.repo_root, ".git")):
            subprocess.run([self.git, "init", "-q"], cwd=self.repo_root, capture_output=True, text=True, check=False)
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
        "Files changed:",
        f"{len(records)} files changed{stat_suffix}",
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
    lines.append("")
    lines.append("未附完整 diff。若下一步需要具体内容，请用 git diff/读取文件命令按文件名查询。")
    return "\n".join(lines)


def build_execution_context_content(full_log: str, records: List[Dict[str, object]], long_running_launches: int = 0) -> str:
    parts = [
        "【执行日志（低密度，自动压缩优先）】",
        low_value_context_block("execution_log", truncate_middle(str(full_log or "").strip(), 6000)),
    ]
    if records:
        parts.extend([
            "",
            "【文件变更摘要（高密度，优先保留）】",
            format_change_context_summary(records),
        ])
    elif long_running_launches:
        parts.extend([
            "",
            "Git diff file names:",
            "未检测到文件改动。若命令正在底部终端继续运行，保存/生成文件后需要等待进程结束或再执行一次检查。",
        ])
    else:
        parts.extend(["", "Git diff file names:", "未检测到文件改动。"])
    return "\n".join(parts)


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
CHAT_HISTORY_INITIAL_RENDER_ENTRIES = env_int("AGENT_QT_HISTORY_INITIAL_RENDER_ENTRIES", 40, minimum=10)


def is_automation_done_response(text: str) -> bool:
    return AUTOMATION_DONE_MARKER in (text or "")


def strip_automation_done_marker(text: str) -> str:
    content = str(text or "").strip()
    if not content:
        return ""
    pattern = rf"(?im)^\s*{re.escape(AUTOMATION_DONE_MARKER)}\s*:?\s*$"
    content = re.sub(pattern, "", content).strip()
    content = re.sub(rf"(?i)\b{re.escape(AUTOMATION_DONE_MARKER)}\b\s*:?", "", content).strip()
    return content


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
        "runner 会执行你返回的单个 fenced bash 代码块",
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
    head_budget = max(1200, available // 3)
    tail_budget = max(1200, available - head_budget)

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


def quiet_automation_error_message(error: str) -> str:
    if looks_like_timeout_error(error):
        return "响应超时，自动化任务已暂停。"
    if looks_like_submit_idle_error(error):
        return "DeepSeek 页面没有开始生成，自动化任务已暂停。可能是页面限流、输入框未接受超长上下文，或提交按钮状态异常。"
    return ""


def build_automation_feedback_prompt(project_root: str, goal: str, execution_log: str, round_number: int, max_rounds: int) -> str:
    goal_text = goal.strip() or "用户没有填写一句话需求，请根据前文、执行日志和当前项目状态继续判断。"
    clipped_log = truncate_middle(execution_log.strip(), AUTOMATION_FEEDBACK_CHAR_LIMIT)
    env = runtime_environment()
    command_block_lang = env["command_block_lang"]
    return f"""你正在 Agent Qt 的自动化循环中，这是第 {round_number}/{max_rounds} 轮。

原始用户需求：
{goal_text}

项目根目录：
{project_root}

上一轮本地执行结果如下：
```text
{clipped_log}
```

请基于日志和 diff 判断下一步：
- 如果任务已经完成，只回复 `{AUTOMATION_DONE_MARKER}` 加简短总结，不要输出命令块。
- 如果任务还没有完成，继续输出一个完整的 ```{command_block_lang} 代码块，并按既有“占位符 + 后续代码块”协议补齐需要写入的大段文件内容。
- 不要重复已经成功完成的步骤；优先修复日志里的错误、补齐缺失文件或做必要验证。
- 禁止输出备用方案或“如果上面失败就改用...”这类二选一命令；必须自己选择一个最高把握方案，只保留一种可执行路径。
- 未编号占位符按顺序消费代码块；编号占位符可复用同一个代码块，例如多处 `<!-- Python block 1 -->` 都引用第 1 个 python 代码块。
- 不要输出 JSON，不要输出 content/tool_calls 包装；这里需要的是普通 Markdown 文本和 fenced {command_block_lang}。
"""


def plaintext_fence(title: str, content: str) -> str:
    safe_content = str(content or "").strip()
    longest = max((len(match.group(0)) for match in re.finditer(r"`{3,}", safe_content)), default=2)
    fence = "`" * max(3, longest + 1)
    return f"{title}\n{fence}plaintext\n{safe_content}\n{fence}"


def unwrap_provider_text(text: str) -> str:
    """Recover the actual assistant text if a provider still returns a JSON envelope."""
    current = (text or "").strip()
    for _ in range(3):
        candidate = current
        fence_match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", candidate, flags=re.S | re.I)
        if fence_match:
            candidate = fence_match.group(1).strip()
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
        self.script_path = ""
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
        self.process.setProcessEnvironment(agent_qprocess_environment(create=False))
        self.process.readyReadStandardOutput.connect(self.read_output)
        self.process.started.connect(self.on_started)
        self.process.errorOccurred.connect(self.on_error)
        self.process.finished.connect(self.on_finished)
        if not self.interactive and should_use_temp_shell_script(self.cmd):
            self.script_path = write_temp_shell_script(self.cmd)
        shell, args = shell_launch_for_command(self.cmd, interactive=self.interactive, script_path=self.script_path)
        self.process.start(shell, args)

    def on_started(self):
        if self.interactive:
            self.output.append_process_text(f"# shell: {self.name}\n# cwd: {self.cwd}\n")
            self.output.set_input_enabled(True)
        else:
            self.output.set_input_enabled(False)
            self.output.append_process_text(f"$ {strip_shell_command_marker(self.cmd)}\n# cwd: {self.cwd}\n")

    def read_output(self):
        if not self.process:
            return
        text = decode_process_output(self.process.readAllStandardOutput().data())
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
        if self.script_path:
            try:
                os.remove(self.script_path)
            except OSError:
                pass
            self.script_path = ""

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
        background = "#eef1f6" if self.active else "transparent"
        border = COLORS["border"] if self.active else "transparent"
        hover_background = "#f4f6fa" if self.active else "#f7f8fb"
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
        if not interactive and (not cmd.strip() or is_interactive_shell_command(cmd)):
            return None
        label = name.strip() if name.strip() else self.terminal_title(cwd, len(self.processes) + 1)
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
            if is_interactive_shell_command(cmd):
                outputs.append(f"[{i}] ⚠️ 跳过交互式 Shell: {display_cmd}")
                self.output_signal.emit(outputs[-1])
                continue
            if is_long_running(cmd):
                outputs.append(f"[{i}] 🔵 后台: {display_cmd}")
                self.long_running_signal.emit(cmd, cwd, display_cmd.splitlines()[0][:40])
                self.output_signal.emit(outputs[-1])
                continue
            try:
                r = run_shell_command_capture(cmd, cwd, timeout=30)
                out = r.stdout.strip()
                if r.stderr.strip():
                    out += "\n" + r.stderr.strip()
                if r.returncode != 0:
                    out += f"\n[退出码: {r.returncode}]"
                outputs.append(f"[{i}] 💻 {display_cmd}\n📤 {out or '(无输出)'}")
            except subprocess.TimeoutExpired:
                if is_interactive_shell_command(cmd):
                    outputs.append(f"[{i}] ⚠️ 交互式 Shell 已超时，未创建后台终端: {display_cmd}")
                else:
                    outputs.append(f"[{i}] ⏱️ 超时 → 后台: {display_cmd}")
                    self.long_running_signal.emit(cmd, cwd, display_cmd.splitlines()[0][:40])
            except Exception as e:
                outputs.append(f"[{i}] ❌ {e}")
            self.output_signal.emit(outputs[-1])
        self.finished_signal.emit('\n\n'.join(outputs))

# ============================================================
# 可选网页 Provider 自动化插件
# ============================================================
AUTOMATION_MODELS = [
    ("DeepSeek V4", "DeepSeekV4"),
    ("DeepSeek V4 Thinking", "DeepSeekV4-thinking"),
    ("MiMo V2.5 Pro", "xiaomi-mimo-v2.5-pro"),
    ("MiMo V2.5", "xiaomi-mimo-v2.5"),
]
AUTOMATION_DEFAULT_MODEL = "DeepSeekV4"
AUTOMATION_REQUIRED_MODULES = (
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

    def request_json(self, method: str, path: str, payload: Optional[dict] = None, timeout: int = 30) -> dict:
        started = time.perf_counter()
        encode_started = time.perf_counter()
        data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        encode_ms = int((time.perf_counter() - encode_started) * 1000)
        request = urllib.request.Request(f"{self.base_url}{path}", data=data, method=method)
        if payload is not None:
            request.add_header("Content-Type", "application/json")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
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
                    "Provider request timing method=%s path=%s request_bytes=%d response_bytes=%d encode_ms=%d http_ms=%d decode_ms=%d total_ms=%d",
                    method,
                    path,
                    len(data or b""),
                    len(raw),
                    encode_ms,
                    http_ms,
                    decode_ms,
                    total_ms,
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
            raise RuntimeError(message) from exc

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

    def stop_provider_process(self):
        try:
            with open(self.pid_file, "r", encoding="utf-8") as f:
                pid = int(f.read().strip() or "0")
        except (OSError, ValueError):
            return
        if pid <= 0:
            return
        try:
            if platform.system() == "Windows":
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, text=True, timeout=8)
            else:
                os.kill(pid, signal.SIGTERM)
        except Exception:
            pass
        deadline = time.time() + 6
        while time.time() < deadline:
            if not self.health():
                break
            time.sleep(0.2)
        try:
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
            popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
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

    def chat(self, messages: List[Dict[str, str]], model: str, thread_id: str) -> str:
        self.start_provider()
        payload = self.request_json(
            "POST",
            "/v1/chat/completions",
            {
                "model": model,
                "messages": messages,
                "temperature": 0,
                "user": thread_id,
                "output_protocol": "plain",
                "extra_body": {"output_protocol": "plain"},
            },
            timeout=900,
        )
        try:
            return unwrap_provider_text(str(payload["choices"][0]["message"].get("content") or ""))
        except Exception as exc:
            raise RuntimeError(f"provider 返回格式异常: {payload}") from exc

    def response_preview(self, model: str, thread_id: str) -> dict:
        self.start_provider()
        return self.request_json(
            "GET",
            f"/debug/response-preview?model={urllib.parse.quote(model)}&user={urllib.parse.quote(thread_id)}",
            timeout=1,
        )

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

    def __init__(self, manager: AutomationProviderManager, messages: List[Dict[str, str]], model: str, thread_id: str):
        super().__init__()
        self.manager = manager
        self.messages = messages
        self.model = model
        self.thread_id = thread_id

    def run(self):
        try:
            started = time.perf_counter()
            text = self.manager.chat(self.messages, self.model, self.thread_id)
            logger.warning(
                "Automation chat worker done elapsed_ms=%d message_chars=%d response_chars=%d",
                int((time.perf_counter() - started) * 1000),
                sum(len(str(message.get("content") or "")) for message in self.messages),
                len(text),
            )
            self.finished_signal.emit(text, "")
        except Exception as exc:
            self.finished_signal.emit("", str(exc))


class AutomationPreviewWorker(QThread):
    preview_signal = Signal(dict)

    def __init__(self, manager: AutomationProviderManager, model: str, thread_id: str):
        super().__init__()
        self.manager = manager
        self.model = model
        self.thread_id = thread_id
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
                    self.preview_signal.emit(preview)
            except Exception:
                pass
            self.msleep(300)


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
            "ai": (COLORS["card_ai"], "#d7ccff", "AI 输出"),
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
                    background: #edf5ff;
                    border: 1px solid #cfe0ff;
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
            layout.setContentsMargins(14, 10, 14, 10)
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
                    font-size: 13px;
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
                font-size: 12px;
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
        header_layout.addStretch()
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

        def toggle_code():
            visible = code_box.isVisible()
            code_box.setVisible(not visible)
            collapse_btn.setText("+" if visible else "-")
            header.setProperty("collapsed", visible)
            self.adjust_content_height()

        collapse_btn.clicked.connect(toggle_code)
        code_box.code_source = code
        code_frame.code_box = code_box
        code_frame.lang_label = lang_label
        code_frame.collapse_btn = collapse_btn
        layout.addWidget(code_frame)
        self.markdown_widgets.append(code_frame)
        self.markdown_code_widgets.append(code_box)
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
                    vertical = code_box.verticalScrollBar()
                    horizontal = code_box.horizontalScrollBar()
                    old_value = vertical.value()
                    old_h_value = horizontal.value()
                    was_at_bottom = old_value >= vertical.maximum() - 4
                    code_box.code_source = text
                    code_box.setUpdatesEnabled(False)
                    code_box.setPlainText(text)
                    code_box.setUpdatesEnabled(True)
                    if was_at_bottom:
                        vertical.setValue(vertical.maximum())
                        QTimer.singleShot(0, lambda editor=code_box: editor.verticalScrollBar().setValue(editor.verticalScrollBar().maximum()))
                    else:
                        vertical.setValue(min(old_value, vertical.maximum()))
                    horizontal.setValue(min(old_h_value, horizontal.maximum()))
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

    def render_precomputed_markdown_parts(self, parts: List[Dict[str, str]], signatures: List[tuple], layout: Optional[QVBoxLayout] = None, stats: Optional[Dict[str, int]] = None):
        if layout is None:
            layout = self.layout()
        if layout is None:
            return
        apply_started = time.perf_counter()
        if self.update_markdown_parts_in_place(parts, signatures):
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
                code_box.setMinimumHeight(0)
                code_box.setMaximumHeight(QT_WIDGET_MAX_HEIGHT)
                metrics = code_box.fontMetrics()
                text = code_box.toPlainText()
                line_count = max(1, len(text.splitlines()) or 1)
                vertical_padding = 26
                scrollbar_room = 12 if code_box.horizontalScrollBarPolicy() != Qt.ScrollBarPolicy.ScrollBarAlwaysOff else 0
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
            target_height = max(26, estimate_wrapped_text_height(self.visible_content() or " ", metrics, available_width))
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
        self.content = content
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
                    font-size: 14px;
                    font-weight: 900;
                    padding: 0;
                }}
            """)
            layout.addWidget(self.title_label)
        else:
            self.title_label = None

        self.editor = QPlainTextEdit()
        self.editor.setReadOnly(True)
        self.editor.setPlainText(content)
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
        self.content = text
        self.editor.setPlainText(text)
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
            "<html><body style='margin:0; background:#ffffff; color:#172033; "
            "font-family: -apple-system, BlinkMacSystemFont, \"Segoe UI\", sans-serif;'>"
            "<div style='padding:12px 14px;'>"
            f"<div style='font-weight:700; font-size:13px;'>{status_text}</div>"
            f"<div style='margin-top:6px; color:#657089; font-size:12px;'>{path}</div>"
            "<div style='margin-top:8px; color:#657089; font-size:12px;'>"
            "此类文件无法生成逐行文本 diff，但仍可撤销/重做本轮变更。"
            "</div></div></body></html>"
        )

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
        for row in self.file_row_widgets:
            row.setStyleSheet(f"""
                QFrame#changeFileRow {{
                    background: {COLORS['code_bg']};
                    border: 1px solid {COLORS['border']};
                    border-radius: 12px;
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
        self.title_edit = QLineEdit(str(self.thread.get("title", "会话")))
        self.title_edit.setMaxLength(80)
        self.title_edit.setVisible(False)
        self.title_edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
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


class Sidebar(QFrame):
    file_opened = Signal(str)
    back_home_requested = Signal()
    thread_selected = Signal(str)
    new_thread_requested = Signal()
    delete_thread_requested = Signal(str)
    rename_thread_requested = Signal(str, str)
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
            card.rename_requested.connect(self.rename_thread_requested.emit)
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
        self.change_tracker: Optional[InternalGitChangeTracker] = None
        self.pending_internal_git_commit = ""
        self.pending_long_running_launches = 0
        self.history_entries: List[Dict[str, object]] = []
        self.result_bubble: Optional[ExecutionLogPanel] = None
        self.worker: Optional[ExecuteWorker] = None
        self.automation_manager = AutomationProviderManager()
        self.automation_enabled = automation_enabled_setting()
        self.automation_model = AUTOMATION_DEFAULT_MODEL
        self.automation_worker: Optional[AutomationChatWorker] = None
        self.automation_preview_worker: Optional[AutomationPreviewWorker] = None
        self.automation_preview_bubble: Optional[QFrame] = None
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
        self._ensure_ai_entry_pending = False
        self._last_status_message = ""
        self._last_status_at = 0.0
        self.automation_composer: Optional[QFrame] = None
        self.automation_input: Optional[QTextEdit] = None
        self.automation_send_btn: Optional[QToolButton] = None
        self.chat_column_max_width = 1480
        self.chat_column_width_ratio = 0.94
        self.user_bubble_width_ratio = 0.75
        self.setup_ui()

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
        right_layout.setContentsMargins(0, 16, 18, 0)
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
                                           QScrollArea QWidget#qt_scrollarea_viewport {{
                                               background: {COLORS['surface']};
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
        self.scroll_area.viewport().setStyleSheet(f"background: {COLORS['surface']};")
        self.scroll_area.viewport().installEventFilter(self)
        self.scroll_area.verticalScrollBar().valueChanged.connect(self.on_chat_scroll_changed)
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
        self.automation_input = QTextEdit()
        self.automation_input.setPlaceholderText(
            f"上下文 0k / {context_k_label(AUTOMATION_CONTEXT_DISPLAY_TOKENS)} · 输入下一步需求..."
        )
        self.automation_input.setFixedHeight(54)
        self.automation_input.setAcceptRichText(False)
        self.automation_input.textChanged.connect(self.on_automation_input_text_changed)
        self.automation_input.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.automation_input.customContextMenuRequested.connect(
            lambda pos, editor=self.automation_input: show_chinese_edit_menu(editor, editor.mapToGlobal(pos))
        )
        self.automation_input.setStyleSheet(f"""
            QTextEdit {{
                background: transparent;
                color: {COLORS['text']};
                border: none;
                padding: 3px 4px;
                font-size: 13px;
            }}
            QScrollBar:vertical {{
                width: 0;
                background: transparent;
            }}
        """)
        composer_layout.addWidget(self.automation_input, 1)
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
        self.terminal_panel.close_all_processes()
        self.terminal_panel.collapse()
        self.project_root = path
        self.change_tracker = InternalGitChangeTracker(path)
        self.threads = load_workspace_threads(path)
        save_workspace_threads(path, self.threads)
        self.thread_id = load_last_thread_id(path, self.threads)
        self.path_label.setText(f"📁 {path}")
        self.terminal_panel.set_project_root(path)
        self.sidebar.refresh_tree(path)
        self.sidebar.set_threads(self.threads, self.thread_id)
        self.sidebar.set_tab("threads")
        self.expand_sidebar()
        self.load_history()
        if self.automation_enabled:
            self.run_automation_setup("start")
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
        self.update_chat_column_width()
        self.update_prompt_tools_responsive()

    def eventFilter(self, watched, event):
        if hasattr(self, "scroll_area") and watched is self.scroll_area.viewport() and event.type() == QEvent.Type.Resize:
            self.update_chat_column_width()
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

    def is_automation_request_running(self) -> bool:
        return bool(self.automation_worker and self.automation_worker.isRunning())

    def is_automation_busy(self) -> bool:
        return self.automation_loop_active or self.is_automation_request_running()

    def begin_automation_loop(self, goal: str):
        self.automation_loop_active = True
        self.automation_loop_round = 1
        self.automation_loop_goal = goal.strip()
        self.refresh_prompt_bubble_buttons()
        self.update_automation_composer_state()

    def stop_automation_loop(self, message: str = "", ensure_manual_entry: bool = False):
        self.automation_loop_active = False
        self.automation_loop_round = 0
        self.automation_loop_goal = ""
        self.refresh_prompt_bubble_buttons()
        self.update_automation_composer_state()
        if message:
            self.add_status_bubble(message)
        if ensure_manual_entry:
            if self.automation_enabled:
                self.show_automation_composer(focus=False)
            else:
                self.ensure_ai_response_entry(focus=False, animate=True, keep_visible=False)
    
    def on_chat_scroll_changed(self, _value: int):
        return

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
            QTimer.singleShot(delay, self.scroll_to_bottom_now)

    def toggle_prompt_tools(self):
        return

    def show_settings_menu(self):
        if agent_runtime_enabled() and not agent_runtime_ready():
            set_agent_runtime_enabled(False)
        if self.automation_enabled:
            dep = self.automation_manager.dependency_status()
            if not dep.get("ready"):
                self.automation_enabled = False
                set_automation_enabled_setting(False)
                self.stop_automation_preview(remove_bubble=True)
                self.hide_automation_composer()
        menu = QMenu(self)
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
        menu.addSeparator()

        runtime_menu = menu.addMenu("Python 运行环境")
        runtime_status_action = QAction("检查运行环境", self)
        runtime_status_action.triggered.connect(self.show_python_runtime_status)
        runtime_menu.addAction(runtime_status_action)
        runtime_install_action = QAction("创建/修复 Agent Python 环境", self)
        runtime_install_action.triggered.connect(lambda: self.run_python_runtime_setup("install"))
        runtime_menu.addAction(runtime_install_action)
        runtime_open_action = QAction("打开运行环境目录", self)
        runtime_open_action.triggered.connect(self.open_python_runtime_dir)
        runtime_menu.addAction(runtime_open_action)
        runtime_copy_action = QAction("复制 Python 路径", self)
        runtime_copy_action.triggered.connect(self.copy_python_runtime_path)
        runtime_menu.addAction(runtime_copy_action)

        automation_menu = menu.addMenu("自动化插件")

        model_menu = automation_menu.addMenu("模型")
        for label, model_id in AUTOMATION_MODELS:
            action = QAction(label, self)
            action.setCheckable(True)
            action.setChecked(self.automation_model == model_id)
            action.triggered.connect(lambda _checked=False, value=model_id: self.set_automation_model(value))
            model_menu.addAction(action)

        rounds_menu = automation_menu.addMenu("自动化最大轮数")
        for rounds in (8, 12, 20, 50, 100):
            action = QAction(f"{rounds} 轮", self)
            action.setCheckable(True)
            action.setChecked(self.automation_loop_max_rounds == rounds)
            action.triggered.connect(lambda _checked=False, value=rounds: self.set_automation_max_rounds(value))
            rounds_menu.addAction(action)

        automation_menu.addSeparator()
        status_action = QAction("检查插件状态", self)
        status_action.triggered.connect(self.show_automation_status)
        automation_menu.addAction(status_action)
        open_log_action = QAction("打开插件日志", self)
        open_log_action.triggered.connect(self.open_automation_log_file)
        automation_menu.addAction(open_log_action)
        copy_log_action = QAction("复制插件日志路径", self)
        copy_log_action.triggered.connect(self.copy_automation_log_path)
        automation_menu.addAction(copy_log_action)
        install_action = QAction("安装/修复插件依赖", self)
        install_action.triggered.connect(lambda: self.run_automation_setup("install"))
        automation_menu.addAction(install_action)
        login_action = QAction("打开网页登录", self)
        login_action.triggered.connect(lambda: self.run_automation_setup("login"))
        automation_menu.addAction(login_action)
        open_plugin_dir_action = QAction("打开插件目录", self)
        open_plugin_dir_action.triggered.connect(self.open_automation_plugin_dir)
        automation_menu.addAction(open_plugin_dir_action)
        copy_plugin_dir_action = QAction("复制插件目录路径", self)
        copy_plugin_dir_action.triggered.connect(self.copy_automation_plugin_dir)
        automation_menu.addAction(copy_plugin_dir_action)

        menu.addSeparator()
        for title in ("字号大小（待实现）", "主题颜色（待实现）", "语言设置（待实现）"):
            action = QAction(title, self)
            action.setEnabled(False)
            menu.addAction(action)
        menu.exec(self.settings_btn.mapToGlobal(self.settings_btn.rect().bottomRight()))

    def set_automation_model(self, model_id: str):
        self.automation_model = model_id

    def set_automation_max_rounds(self, rounds: int):
        self.automation_loop_max_rounds = max(1, int(rounds))

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
                content = self.prompt_bubble_display_text(content).strip() or self.prompt_text_from_system_prompt(content).strip()
            elif entry_type == "ai":
                title = "AI 输出"
            elif entry_type == "result":
                title = "执行结果"
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
        return "\n".join(lines).rstrip() + "\n"

    def export_conversation_text(self, fmt: str):
        ext = "md" if fmt == "md" else "txt"
        path = os.path.join(self.export_dir(), f"agent-qt-{self.thread_id}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.{ext}")
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.conversation_export_text(fmt))
        except OSError as exc:
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
        menu = QMenu(self)
        md_action = QAction("分享为 Markdown (.md)", self)
        md_action.triggered.connect(lambda: self.export_conversation_text("md"))
        menu.addAction(md_action)
        txt_action = QAction("分享为 TXT (.txt)", self)
        txt_action.triggered.connect(lambda: self.export_conversation_text("txt"))
        menu.addAction(txt_action)
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
        self.automation_input.setEnabled(not busy and not self.is_execution_running())
        if busy:
            self.automation_input.setPlaceholderText("AI 正在处理...")
            self.automation_send_btn.setIcon(line_icon("pause", "#ffffff", 20))
            self.automation_send_btn.setStyleSheet(f"""
                QToolButton {{
                    background: #8b95aa;
                    border: none;
                    border-radius: 21px;
                }}
                QToolButton:hover {{
                    background: #6f7788;
                }}
            """)
        else:
            self.automation_input.setPlaceholderText(self.automation_context_placeholder_text())
            self.automation_send_btn.setIcon(line_icon("send", "white", 20))
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

    def on_automation_composer_action(self):
        if self.is_automation_busy():
            self.cancel_automation_request()
            return
        self.submit_automation_prompt_from_composer()

    def cancel_automation_request(self):
        worker = self.automation_worker
        if worker is not None:
            worker.requestInterruption()
            worker.terminate()
            if worker.wait(1200):
                worker.deleteLater()
            else:
                worker.finished.connect(worker.deleteLater)
            self.automation_worker = None
        self.stop_automation_preview(remove_bubble=True)
        self.stop_automation_loop("", ensure_manual_entry=True)

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
        full_prompt = self.build_system_prompt(text)
        prompt_entry_id = self.add_automation_user_prompt_bubble(full_prompt, animate=True)
        self.begin_automation_loop(text)
        self.start_automation_worker(
            text,
            "",
            None,
            None,
            prompt_entry_id,
        )

    def add_automation_user_prompt_bubble(self, full_prompt: str, animate: bool = True) -> str:
        self.hide_empty_state()
        entry_id = uuid.uuid4().hex
        bubble = ChatBubble(
            "user",
            self.prompt_bubble_display_text(full_prompt),
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
        self.append_history({
            "id": entry_id,
            "type": "prompt",
            "content": full_prompt,
        })
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
            expand_to_content=False,
            flat=True,
            max_content_height=560,
        )
        frame.async_markdown_render = False
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
        self.automation_preview_started_at = time.time()
        self.automation_preview_pending_text = ""
        self.automation_preview_last_rendered_text = ""
        self.automation_preview_last_chars = 0
        self.automation_preview_dots = 0
        self.automation_preview_bubble = self.create_automation_preview_bubble()
        worker = AutomationPreviewWorker(self.automation_manager, self.automation_model, self.thread_id)
        self.automation_preview_worker = worker
        worker.preview_signal.connect(self.update_automation_preview)
        worker.start()

    def stop_automation_preview(self, remove_bubble: bool = False):
        worker = self.automation_preview_worker
        if worker is not None:
            worker.stop()
            if worker.wait(1500):
                worker.deleteLater()
            else:
                worker.finished.connect(worker.deleteLater)
            self.automation_preview_worker = None
        self.automation_preview_render_timer.stop()
        self.automation_preview_dots_timer.stop()
        self.automation_preview_pending_text = ""
        self.automation_preview_last_rendered_text = ""
        self.automation_preview_last_chars = 0
        if remove_bubble and self.automation_preview_bubble is not None:
            bubble = self.automation_preview_bubble
            self.automation_preview_bubble = None
            self.chat_layout.removeWidget(bubble)
            bubble.hide()
            bubble.setParent(None)
            bubble.deleteLater()
            self.chat_container.adjustSize()

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
        text = self.automation_preview_pending_text
        if not text or text == self.automation_preview_last_rendered_text:
            return
        should_follow_bottom = self.is_chat_at_bottom()
        bubble.update_content(text)
        self.automation_preview_last_rendered_text = text
        if should_follow_bottom:
            QTimer.singleShot(0, self.scroll_to_bottom_now)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        if elapsed_ms >= 60:
            logger.warning("Automation preview render UI slow elapsed_ms=%d chars=%d", elapsed_ms, len(text))

    def update_automation_preview(self, preview: dict):
        started = time.perf_counter()
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

    def build_system_prompt(self, user_text: str) -> str:
        raw_prompt = user_text.strip()
        prompt = raw_prompt or "请根据当前工作区创建或修改项目，并输出可直接执行的完整指令。"
        return SYSTEM_PROMPT.format(
            project_root=self.project_root,
            user_prompt=prompt,
            done_marker=AUTOMATION_DONE_MARKER,
            **runtime_environment(),
        ) + f"\n{PROMPT_BUBBLE_MARKER}{base64.b64encode(raw_prompt.encode('utf-8')).decode('ascii')} -->"

    def build_automation_system_text(self) -> str:
        return SYSTEM_PROMPT.format(
            project_root=self.project_root,
            user_prompt="当前指令见第三段 plaintext，不要把本段当作用户需求重复执行。",
            done_marker=AUTOMATION_DONE_MARKER,
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
            return user_text
        if PROMPT_BUBBLE_MARKER not in full_prompt:
            return str(full_prompt or "").strip()
        return ""

    def compact_automation_entry_text(self, text: str, limit: int = AUTOMATION_CONTEXT_ENTRY_CHAR_LIMIT) -> str:
        return truncate_middle(str(text or "").strip(), limit)

    def automation_context_system_text(self) -> str:
        return self.build_automation_system_text() + (
            "\n\n补充说明：provider 每次可能会打开新的网页对话，所以第二段包含 Agent Qt 保存的本会话上下文。"
            "请把这些上下文视为连续对话历史。上下文按纯文本给出，不是 JSON 或工具调用协议。"
            f"自动化上下文按 {context_k_label(AUTOMATION_CONTEXT_DISPLAY_TOKENS)} 估算展示；当历史超过约 {context_k_label(AUTOMATION_CONTEXT_COMPACT_TRIGGER_TOKENS)} 时，"
            "Agent Qt 会把较早历史 compact 成 plaintext 摘要，近期上下文保留原文后继续。"
        )

    def automation_history_text_for_entry(self, entry: Dict[str, object]) -> str:
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
            return "【用户需求】\n" + self.compact_automation_entry_text(user_text, 2000)
        if entry_type == "ai":
            return "【AI 回复】\n" + self.compact_automation_entry_text(content)
        if entry_type == "result":
            high_signal = strip_low_value_context_blocks(content)
            return "【本地执行结果和文件变更】\n" + self.compact_automation_entry_text(high_signal)
        return ""

    def automation_history_chunks(self, skip_entry_id: str = "") -> List[str]:
        chunks: List[str] = []
        for entry in self.history_entries:
            if skip_entry_id and str(entry.get("id") or "") == skip_entry_id:
                continue
            text = self.automation_history_text_for_entry(entry)
            if text:
                chunks.append(text)
        return chunks

    def compact_automation_history_text(self, chunks: List[str], token_budget: int) -> str:
        if not chunks:
            return "（暂无历史对话）"
        full_text = "\n\n".join(chunks).strip()
        if estimate_context_tokens(full_text) <= min(token_budget, AUTOMATION_CONTEXT_COMPACT_TRIGGER_TOKENS):
            return text_within_token_budget(full_text, token_budget)

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

        old_text = "\n\n".join(old_chunks).strip()
        recent_text = "\n\n".join(recent_chunks).strip()
        summary_budget = min(
            AUTOMATION_CONTEXT_COMPACT_SUMMARY_TOKENS,
            max(4000, token_budget - estimate_context_tokens(recent_text) - 2000),
        )
        compact_old = text_within_token_budget(old_text, summary_budget) if old_text else "（无较早历史）"
        history_text = (
            "【Compact 历史摘要】\n"
            "以下是较早对话、执行结果和 diff 的 plaintext 压缩版本；请作为连续上下文参考，不要把它当作新需求重复执行。\n"
            f"{compact_old}\n\n"
            "【近期完整历史】\n"
            f"{recent_text or '（暂无近期历史）'}"
        )
        return text_within_token_budget(history_text, token_budget)

    def build_automation_context_payload(self, current_prompt: str, skip_entry_id: str = "", log_stats: bool = True) -> str:
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
        payload = "\n\n".join([
            plaintext_fence("第一段：系统提示词", system_context),
            plaintext_fence("第二段：历史对话", history_text),
            plaintext_fence("第三段：当前指令", current_prompt),
        ])
        if log_stats:
            logger.warning(
                "Automation context payload chars=%d tokens=%d token_budget=%d",
                len(payload),
                estimate_context_tokens(payload),
                token_budget,
            )
        return payload

    def automation_context_tokens_for_next_request(self, current_prompt: str = "", skip_entry_id: str = "") -> int:
        return estimate_context_tokens(self.build_automation_context_payload(current_prompt, skip_entry_id=skip_entry_id, log_stats=False))

    def automation_context_placeholder_text(self) -> str:
        return f"上下文自动压缩到约 {context_k_label(AUTOMATION_CONTEXT_DISPLAY_TOKENS)} 内 · 输入下一步需求..."

    def build_automation_messages(self, current_prompt: str, skip_entry_id: str = "") -> List[Dict[str, str]]:
        return [{"role": "user", "content": self.build_automation_context_payload(current_prompt, skip_entry_id=skip_entry_id)}]

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
        messages = self.build_automation_messages(prompt, skip_entry_id=skip_entry_id)
        worker = AutomationChatWorker(
            self.automation_manager,
            messages,
            self.automation_model,
            self.thread_id,
        )
        self.automation_worker = worker
        self.update_automation_composer_state()
        self.start_automation_preview()

        def on_finished(text: str, error: str):
            self.automation_worker = None
            self.stop_automation_preview(remove_bubble=True)
            self.update_automation_composer_state()
            if copy_btn is not None and not self.automation_loop_active:
                copy_btn.setEnabled(True)
                copy_btn.setText(source_bubble.copy_text if source_bubble is not None else "发送给 AI")
            if error:
                quiet_message = quiet_automation_error_message(error)
                self.stop_automation_loop(
                    quiet_message or "自动化循环已暂停。",
                    ensure_manual_entry=True,
                )
                if "网页登录还没有准备好" in error:
                    self.add_status_bubble("需要先完成网页登录。请使用设置里的“打开网页登录”，登录后重新发送。")
                if (not quiet_message) or developer_error_details_enabled():
                    styled_warning(self, "AI 自动化失败", self.automation_manager.error_with_log_hint(error))
            else:
                self.handle_ai_response_text(text)
            worker.deleteLater()

        worker.finished_signal.connect(on_finished)
        worker.start()

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
        if self.automation_enabled and not self.is_automation_busy() and not self.is_execution_running():
            QTimer.singleShot(0, self.update_automation_composer_state)

    def save_history(self):
        if self.project_root:
            save_workspace_history(self.project_root, self.history_entries, self.thread_id)

    def load_history(self):
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
        if thread_id == self.thread_id or self.is_execution_running() or self.is_automation_busy():
            return
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
                display_text = self.prompt_bubble_display_text(full_prompt)
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
            if self.automation_enabled:
                self.show_automation_composer(focus=False)
                self.update_automation_composer_state()
            else:
                self.ensure_initial_prompt_bubble()
            self.update_prompt_tools_responsive()
            self.scroll_to_bottom()
        else:
            styled_warning(self, "清空失败", "无法删除当前工作区的 .agent_qt 缓存目录。")

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

    def handle_ai_response_text(self, text: str, insert_index: Optional[int] = None):
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
        if done_response and not display_text:
            self.stop_automation_loop("自动化执行完成。", ensure_manual_entry=True)
            self.scroll_to_bottom()
            return
        self.hide_empty_state()
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
        self.append_history({
            "type": "ai",
            "content": display_text,
        })

        if done_response:
            self.stop_automation_loop("自动化执行完成。", ensure_manual_entry=True)
            self.scroll_to_bottom()
            return
        
        blocks = scan_all_code_blocks(display_text)
        try:
            commands = extract_bash_commands(display_text, blocks)
        except ValueError as exc:
            if self.automation_loop_active:
                self.stop_automation_loop(str(exc), ensure_manual_entry=True)
            warning_bubble = ChatBubble(
                "system",
                f"⚠️ {exc}",
                parent=self.chat_container,
                scrollable=False,
                max_content_height=130,
            )
            self.add_chat_widget(warning_bubble, animate=True)
            self.scroll_to_bottom()
            return
        
        if not commands:
            if self.automation_loop_active:
                self.stop_automation_loop("自动化执行完成。", ensure_manual_entry=True)
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
        
        self.worker = ExecuteWorker(commands, self.project_root)
        self.worker.output_signal.connect(self.on_output)
        self.worker.long_running_signal.connect(self.on_long_running)
        self.worker.finished_signal.connect(self.on_finished)
        self.worker.start()
        self.update_automation_composer_state()
        self.scroll_to_bottom()
    
    def on_output(self, output: str):
        self.cmd_outputs.append(output)
        self.result_bubble.update_content('\n'.join(self.cmd_outputs))
    
    def on_long_running(self, cmd: str, cwd: str, name: str):
        self.terminal_panel.add_process(cmd, cwd, name)
        self.pending_long_running_launches += 1
        self.update_status_bar()
        self.keep_ai_response_visible()
    
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
        long_running_launches = self.pending_long_running_launches
        self.pending_long_running_launches = 0
        log_with_changes = full_log
        if change_records:
            log_with_changes += format_change_summary(change_records, include_diff=True)
        elif long_running_launches:
            log_with_changes += "\n\nFiles changed:\n未检测到文件改动。若命令正在底部终端继续运行，保存/生成文件后需要等待进程结束或再执行一次检查。"
        context_content = build_execution_context_content(full_log, change_records, long_running_launches)
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
            self.stop_automation_loop(
                f"已达到自动化最大轮数 {self.automation_loop_max_rounds}，循环已暂停。你可以检查结果后继续发送。",
                ensure_manual_entry=True,
            )
            return
        self.automation_loop_round += 1
        prompt = build_automation_feedback_prompt(
            self.project_root,
            self.automation_loop_goal,
            log_with_changes,
            self.automation_loop_round,
            self.automation_loop_max_rounds,
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
