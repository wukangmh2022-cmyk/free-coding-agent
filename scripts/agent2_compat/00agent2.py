import os
import asyncio
import sys
import re
import json
import time
from pathlib import Path
from datetime import datetime
from types import SimpleNamespace

import httpx
import tiktoken
from openai import AsyncOpenAI

from agents import (
    Agent,
    ModelSettings,
    OpenAIChatCompletionsModel,
    RunConfig,
    Runner,
    function_tool,
    set_default_openai_client,
    set_tracing_disabled,
)
from agents.items import ItemHelpers
from agents.models.openai_responses import OpenAIResponsesModel
from agents.extensions.experimental.codex import (
    Codex,
    ItemCompletedEvent,
    ThreadErrorEvent,
    TurnCompletedEvent,
    TurnFailedEvent,
    TurnOptions,
)
from agents.extensions.experimental.codex.items import is_agent_message_item
from typing import Any

# --- 终端增强库 ---
try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.formatted_text import HTML
    from prompt_toolkit.patch_stdout import patch_stdout
    from prompt_toolkit.completion import NestedCompleter
except ImportError:
    print("❌ 缺少依赖，请执行: pip install prompt_toolkit tiktoken httpx openai duckduckgo_search")
    sys.exit(1)

# ==========================================
# ⚙️ 1. 全局配置与沙盒动态路径拼接
# ==========================================
BASE_URL = os.getenv("OPENAI_BASE_URL", "http://127.0.0.1:18765/v1")
API_KEY = os.getenv("OPENAI_API_KEY", "sk-placeholder")
DEFAULT_MODEL = os.getenv("OPENAI_MODEL", "DeepSeekV4-thinking")
CODEX_BASE_URL = os.getenv("CODEX_BASE_URL", os.getenv("CODEX_OPENAI_BASE_URL", BASE_URL))
CODEX_API_KEY_VALUE = os.getenv("CODEX_API_KEY", API_KEY)
CODEX_MODEL = os.getenv("CODEX_MODEL", DEFAULT_MODEL)
CODEX_REASONING_EFFORT = os.getenv("CODEX_REASONING_EFFORT", os.getenv("PYCLI_REASONING_EFFORT", "medium")).strip().lower()
CODEX_APPROVAL_POLICY = os.getenv("PYCLI_CODEX_APPROVAL_POLICY", "on-failure").strip().lower() or "on-failure"
VALID_REASONING_EFFORTS = {"none", "minimal", "low", "medium", "high", "xhigh"}
DEFAULT_REASONING_EFFORT = os.getenv("PYCLI_REASONING_EFFORT", "medium").strip().lower()
if DEFAULT_REASONING_EFFORT not in VALID_REASONING_EFFORTS:
    DEFAULT_REASONING_EFFORT = "medium"
if CODEX_REASONING_EFFORT not in VALID_REASONING_EFFORTS:
    CODEX_REASONING_EFFORT = "medium"

def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "off", "no", ""}

def _response_store_enabled() -> bool:
    return _bool_env("PYCLI_RESPONSE_STORE", True)

def _model_has_thinking(model_name: str) -> bool:
    return model_name.strip().lower().endswith("-thinking")

THINKING_ENABLED = _bool_env("PYCLI_THINKING", _model_has_thinking(DEFAULT_MODEL))

# 🚀 动态拼接沙盒地址
# Defaults are repo-local so this compatibility runner can be tested without
# writing into the historical /Users/pippo/cpt-widget/00agent workspace.
BASE_DIR = os.getenv("AGENT_BASE_DIR", os.getcwd())
WORKSPACE_NAME = os.getenv("AGENT_WORKSPACE_NAME", "agent2_workspace")
TARGET_WORKSPACE = os.getenv("AGENT_TARGET_WORKSPACE", os.path.join(BASE_DIR, WORKSPACE_NAME))

SESSION_DIR = Path(TARGET_WORKSPACE) / ".sessions"
CODEX_WRAPPER_PATH = str(Path(__file__).with_name("codex_deepseek_local.sh"))

def _int_env(name: str, default: int, *, minimum: int = 1, maximum: int = 8) -> int:
    raw = os.getenv(name)
    if raw is None: return default
    try: value = int(raw)
    except ValueError: return default
    return max(minimum, min(maximum, value))

SUBTASK_CONCURRENCY_LIMIT = _int_env("PYCLI_SUBTASK_CONCURRENCY", 1, minimum=1, maximum=3)
TOKEN_LIMIT = 200000
TOKEN_FRAMING_OVERHEAD = _int_env(
    "PYCLI_TOKEN_FRAMING_OVERHEAD",
    512,
    minimum=0,
    maximum=10000,
)

# 确保目录存在
os.makedirs(SESSION_DIR, exist_ok=True)
os.makedirs(TARGET_WORKSPACE, exist_ok=True)
os.chdir(TARGET_WORKSPACE)

# ==========================================
# 🔍 1.1 自动探测、子目录扫描与兜底创建
# ==========================================
def _auto_discover_venv(workspace: Path) -> str:
    import subprocess  # 引入进程模块用于创建环境
    
    print("🔍 正在扫描工作区及子目录虚拟环境...")
    venv_names = ["VENV", ".venv", "venv", "env", ".VENV"]

    # 1. 扫描根目录及一级子目录
    for venv_name in venv_names:
        # 检查根目录
        root_env = workspace / venv_name / "bin" / "activate"
        if root_env.exists():
            print(f"✅ 已锁定沙盒根目录虚拟py环境: {venv_name}")
            return str(root_env.absolute())

        # 检查一级子目录
        for child_env in workspace.glob(f"*/{venv_name}/bin/activate"):
            if child_env.exists():
                print(f"✅ 已锁定子目录虚拟py环境: {str(child_env.absolute())}")
                return str(child_env.absolute())

    # 2. 兜底机制：如果都没找到，不要退缩，直接原地建一个！
    print("⚠️ 未发现本地虚拟环境。为彻底杜绝全局环境污染，正在自动创建专属 .venv ...")
    default_venv = workspace / ".venv"
    try:
        # 调用当前宿主机的 Python 环境来秒建一个干净的沙盒 venv
        subprocess.run([sys.executable, "-m", "venv", str(default_venv)], check=True)
        activate_script = default_venv / "bin" / "activate"
        print("✅ 专属虚拟环境创建成功并已锁定: .venv")
        return str(activate_script.absolute())
    except Exception as e:
        print(f"❌ 自动创建虚拟环境失败: {e}")
        print("⚠️ 警告：大模型将被迫使用受限的全局环境。")
        return ""

DETECTED_VENV_ACTIVATE = _auto_discover_venv(Path(TARGET_WORKSPACE))

# 🚀 坦诚相见：告诉大模型真实的路径，并告知已自动挂载
VENV_INJECT_PROMPT = (
    f"【环境自动挂载说明】：\n"
    f"系统已在该工作区配置了专属虚拟环境（真实路径: `{Path(DETECTED_VENV_ACTIVATE).parent.parent}`）。\n"
    f"底层基础设施已为你**自动激活**了该环境。你直接执行 `python`、`python3` 或 `pip` 时，"
    f"默认就会安全地使用该沙盒专属环境，**不用**你输入 `source activate`。\n"
    f"请知悉此环境状态，直接输入执行指令即可，但严禁试图逃逸或修改系统全局依赖！\n"
) if DETECTED_VENV_ACTIVATE else ""

# --- 🚀 手动新增：底层 Shell 劫持逻辑 ---
if DETECTED_VENV_ACTIVATE:
    # 1. 在沙盒内生成一个隐藏的注入脚本
    env_inject_script = Path(TARGET_WORKSPACE) / ".agent_env_inject.sh"
    env_inject_script.write_text(f"source {DETECTED_VENV_ACTIVATE}\n", encoding="utf-8")
    
    # 2. 设置环境变量，强制 Bash 执行任何指令前先 source 它
    os.environ["BASH_ENV"] = str(env_inject_script)
    os.environ["ENV"] = str(env_inject_script)
    
    # 3. 针对 macOS 默认的 Zsh，通过 ZDOTDIR 劫持配置
    os.environ["ZDOTDIR"] = TARGET_WORKSPACE
    # 生成 zsh 专用启动文件
    for z_file in [".zshenv", ".zprofile", ".zshrc"]:
        (Path(TARGET_WORKSPACE) / z_file).write_text(f"source {DETECTED_VENV_ACTIVATE}\n", encoding="utf-8")

    print("🔌 已在系统底层配置 Shell 劫持，所有指令将自动激活环境！")
    
# ==========================================
# ⚙️ 1.2 Codex 运行配置
# ==========================================
CODEX_OPTIONS = {
    "codex_path_override": CODEX_WRAPPER_PATH,
    "base_url": CODEX_BASE_URL,
    "api_key": CODEX_API_KEY_VALUE,
    "codex_subprocess_stream_limit_bytes": 16 * 1024 * 1024,
}

CODEX_THREAD_OPTIONS = {
    "model": CODEX_MODEL,
    "model_reasoning_effort": "medium",
    "approval_policy": CODEX_APPROVAL_POLICY,
    "sandbox_mode": "workspace-write",
    "working_directory": TARGET_WORKSPACE,
    "skip_git_repo_check": True,
    "network_access_enabled": True,
    "web_search_mode": "disabled",
}

CODEX_EXPLORE_THREAD_OPTIONS = dict(CODEX_THREAD_OPTIONS)
CODEX_EXPLORE_THREAD_OPTIONS["sandbox_mode"] = "read-only"

CODEX_WEB_THREAD_OPTIONS = dict(CODEX_THREAD_OPTIONS)
CODEX_WEB_SEARCH_MODE = os.getenv("CODEX_WEB_SEARCH_MODE", "live").strip().lower()
if os.getenv("CODEX_NETWORK_ACCESS", "1").strip().lower() not in {"0", "false", "off", "no"}:
    CODEX_WEB_THREAD_OPTIONS["network_access_enabled"] = True
if CODEX_WEB_SEARCH_MODE not in {"", "0", "false", "off", "none", "disabled"}:
    CODEX_WEB_THREAD_OPTIONS["web_search_mode"] = CODEX_WEB_SEARCH_MODE

os.environ["OPENAI_BASE_URL"] = BASE_URL
os.environ["OPENAI_API_KEY"] = API_KEY
os.environ["CODEX_API_KEY"] = CODEX_API_KEY_VALUE
local_no_proxy = "localhost,127.0.0.1,::1"
os.environ["NO_PROXY"] = local_no_proxy
os.environ["no_proxy"] = local_no_proxy

# ==========================================
# 💾 1.5 会话管理系统
# ==========================================
class SessionManager:
    current_file: Path | None = None

    @staticmethod
    def _history_to_md_strings(history: list[dict[str, Any]]) -> list[str]:
        entries: list[str] = []
        for message in history:
            role = str(message.get("role") or "user").strip().lower()
            speaker = "you" if role == "assistant" else "user" if role == "user" else role
            content = str(message.get("content", "")).strip()
            if speaker == "you" and "[执行记录" in content:
                before, marker, after = content.partition("[执行记录")
                record_block = marker + after
                indented = "\n".join(f"  {line}" if line else "" for line in record_block.splitlines())
                content = f"{before.strip()}\n\n  [subtask/context summary]\n{indented}".strip()
            entries.append(f"{speaker}:\n{content}".rstrip())
        return entries

    @staticmethod
    def _md_strings_to_history(entries: list[Any]) -> list[dict[str, str]]:
        history: list[dict[str, str]] = []
        for entry in entries:
            if isinstance(entry, dict) and "role" in entry:
                history.append({
                    "role": str(entry.get("role") or "user"),
                    "content": str(entry.get("content", "")),
                })
                continue
            text = str(entry or "").strip()
            if not text:
                continue
            first_line, _, rest = text.partition("\n")
            speaker = first_line.rstrip(":：").strip().lower()
            if speaker in {"you", "assistant", "ai"}:
                role = "assistant"
            elif speaker in {"user", "human"}:
                role = "user"
            else:
                role = "assistant"
                rest = text
            history.append({"role": role, "content": rest.strip()})
        return history

    @staticmethod
    def save(history, model_name, thinking_enabled: bool | None = None, reasoning_effort: str | None = None):
        if SessionManager.current_file is None:
            SessionManager.current_file = SESSION_DIR / f"session_{time.strftime('%Y%m%d_%H%M%S')}.json"
        data = {
            "timestamp": time.time(),
            "model": model_name,
            "thinking_enabled": THINKING_ENABLED if thinking_enabled is None else thinking_enabled,
            "reasoning_effort": _normalize_reasoning_effort(reasoning_effort),
            "history_format": "mdhistory_v1",
            "history": SessionManager._history_to_md_strings(history),
        }
        tmp_path = SessionManager.current_file.with_suffix(".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        tmp_path.replace(SessionManager.current_file)
        return SessionManager.current_file.name

    @staticmethod
    def list_sessions():
        files = sorted(SESSION_DIR.glob("session_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        return files[:10]

    @staticmethod
    def load(file_path):
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        SessionManager.current_file = Path(file_path)
        raw_history = data.get("history", [])
        if isinstance(raw_history, list):
            data["history"] = SessionManager._md_strings_to_history(raw_history)
        else:
            data["history"] = []
        return data

# ==========================================
# 🔌 2. 客户端与模型工厂
# ==========================================
def _make_openai_client(base_url: str, api_key: str) -> AsyncOpenAI:
    return AsyncOpenAI(
        base_url=base_url,
        api_key=api_key,
        http_client=httpx.AsyncClient(trust_env=False),
        timeout=120.0,
        max_retries=2,
    )

custom_client = _make_openai_client(BASE_URL, API_KEY)
set_default_openai_client(custom_client, use_for_tracing=False)
set_tracing_disabled(True)

def _build_model_profiles() -> dict[str, dict[str, Any]]:
    deepseek_base_url = os.getenv("PYCLI_DEEPSEEK_BASE_URL", BASE_URL)
    deepseek_api_key = os.getenv("PYCLI_DEEPSEEK_API_KEY", API_KEY)
    doubao_base_url = os.getenv("PYCLI_DOUBAO_BASE_URL", "http://127.0.0.1:8877/v1")
    doubao_api_key = os.getenv("PYCLI_DOUBAO_API_KEY", "proxy-placeholder")
    doubao_model = os.getenv("PYCLI_DOUBAO_MODEL", "doubao-seed-2-0-mini-260215") #doubao-seed-2-0-pro-260215
    xiaomi_base_url = os.getenv("PYCLI_XIAOMI_BASE_URL", deepseek_base_url)
    xiaomi_api_key = os.getenv("PYCLI_XIAOMI_API_KEY", deepseek_api_key)
    xiaomi_model = os.getenv("PYCLI_XIAOMI_MODEL", "xiaomi-mimo-v2.5-pro")
    xiaomi_base_model = os.getenv("PYCLI_XIAOMI_BASE_MODEL", "xiaomi-mimo-v2.5")
    return {
        "deepseek": {
            "label": "本地 DeepSeek",
            "model": os.getenv("PYCLI_DEEPSEEK_MODEL", os.getenv("OPENAI_MODEL", "DeepSeekV4")),
            "base_url": deepseek_base_url,
            "api_key": deepseek_api_key,
            "provider_kind": "deepseek",
            "lead_wire_api": os.getenv("PYCLI_DEEPSEEK_WIRE_API", "responses"),
            "codex_base_url": deepseek_base_url,
            "codex_api_key": os.getenv("PYCLI_DEEPSEEK_CODEX_API_KEY", deepseek_api_key),
            "codex_model": os.getenv("PYCLI_DEEPSEEK_CODEX_MODEL", os.getenv("CODEX_MODEL", "DeepSeekV4")),
            "codex_provider_id": os.getenv("PYCLI_DEEPSEEK_CODEX_PROVIDER_ID", "deepseek-local"),
            "codex_wire_api": os.getenv("PYCLI_DEEPSEEK_CODEX_WIRE_API", "responses"),
            "thinking_enabled": False,
        },
        "deepseek-thinking": {
            "label": "本地 DeepSeek Thinking",
            "model": os.getenv("PYCLI_DEEPSEEK_THINKING_MODEL", os.getenv("OPENAI_MODEL", "DeepSeekV4-thinking")),
            "base_url": deepseek_base_url,
            "api_key": deepseek_api_key,
            "provider_kind": "deepseek",
            "lead_wire_api": os.getenv("PYCLI_DEEPSEEK_WIRE_API", "responses"),
            "codex_base_url": deepseek_base_url,
            "codex_api_key": os.getenv("PYCLI_DEEPSEEK_CODEX_API_KEY", deepseek_api_key),
            "codex_model": os.getenv("PYCLI_DEEPSEEK_THINKING_CODEX_MODEL", os.getenv("CODEX_MODEL", "DeepSeekV4-thinking")),
            "codex_provider_id": os.getenv("PYCLI_DEEPSEEK_CODEX_PROVIDER_ID", "deepseek-local"),
            "codex_wire_api": os.getenv("PYCLI_DEEPSEEK_CODEX_WIRE_API", "responses"),
            "thinking_enabled": True,
        },
        "doubao": {
            "label": "豆包 Seed 2.0 Pro",
            "model": doubao_model,
            "base_url": doubao_base_url,
            "api_key": doubao_api_key,
            "provider_kind": "ark_proxy",
            "lead_wire_api": os.getenv("PYCLI_DOUBAO_WIRE_API", "responses"),
            "codex_base_url": os.getenv("PYCLI_DOUBAO_CODEX_BASE_URL", doubao_base_url),
            "codex_api_key": os.getenv("PYCLI_DOUBAO_CODEX_API_KEY", doubao_api_key),
            "codex_model": os.getenv("PYCLI_DOUBAO_CODEX_MODEL", doubao_model),
            "codex_provider_id": os.getenv("PYCLI_DOUBAO_CODEX_PROVIDER_ID", "ark-proxy"),
            "codex_wire_api": os.getenv("PYCLI_DOUBAO_CODEX_WIRE_API", "responses"),
            "thinking_enabled": True,
        },
        "xiaomi": {
            "label": "小米 MiMo V2.5 Pro",
            "model": xiaomi_model,
            "base_url": xiaomi_base_url,
            "api_key": xiaomi_api_key,
            "provider_kind": "xiaomi_web",
            "lead_wire_api": os.getenv("PYCLI_XIAOMI_WIRE_API", "responses"),
            "codex_base_url": os.getenv("PYCLI_XIAOMI_CODEX_BASE_URL", xiaomi_base_url),
            "codex_api_key": os.getenv("PYCLI_XIAOMI_CODEX_API_KEY", xiaomi_api_key),
            "codex_model": os.getenv("PYCLI_XIAOMI_CODEX_MODEL", xiaomi_model),
            "codex_provider_id": os.getenv("PYCLI_XIAOMI_CODEX_PROVIDER_ID", "xiaomi-mimo-local"),
            "codex_wire_api": os.getenv("PYCLI_XIAOMI_CODEX_WIRE_API", "responses"),
            "thinking_enabled": False,
        },
        "xiaomi-base": {
            "label": "小米 MiMo V2.5",
            "model": xiaomi_base_model,
            "base_url": xiaomi_base_url,
            "api_key": xiaomi_api_key,
            "provider_kind": "xiaomi_web",
            "lead_wire_api": os.getenv("PYCLI_XIAOMI_WIRE_API", "responses"),
            "codex_base_url": os.getenv("PYCLI_XIAOMI_CODEX_BASE_URL", xiaomi_base_url),
            "codex_api_key": os.getenv("PYCLI_XIAOMI_CODEX_API_KEY", xiaomi_api_key),
            "codex_model": os.getenv("PYCLI_XIAOMI_BASE_CODEX_MODEL", xiaomi_base_model),
            "codex_provider_id": os.getenv("PYCLI_XIAOMI_CODEX_PROVIDER_ID", "xiaomi-mimo-local"),
            "codex_wire_api": os.getenv("PYCLI_XIAOMI_CODEX_WIRE_API", "responses"),
            "thinking_enabled": False,
        },
    }

MODEL_PROFILE_ALIASES = {
    "local": "deepseek",
    "ds": "deepseek",
    "deepseekv4": "deepseek",
    "deepseekv4-thinking": "deepseek-thinking",
    "dst": "deepseek-thinking",
    "think": "deepseek-thinking",
    "thinking": "deepseek-thinking",
    "doubao-pro": "doubao",
    "db": "doubao",
    "ark": "doubao",
    "xm": "xiaomi",
    "mimo": "xiaomi",
    "mimo-pro": "xiaomi",
    "mimo-2.5-pro": "xiaomi",
    "mimo-v2.5-pro": "xiaomi",
    "mimo-v2-pro": "xiaomi",
    "xiaomi-mimo": "xiaomi",
    "xiaomi-mimo-v2-pro": "xiaomi",
    "xiaomi-mimo-v2.5-pro": "xiaomi",
    "xiaomi-pro": "xiaomi",
    "mimo-2.5": "xiaomi-base",
    "mimo-v2.5": "xiaomi-base",
    "xiaomi-mimo-v2.5": "xiaomi-base",
    "xiaomi-base": "xiaomi-base",
    "mimo-base": "xiaomi-base",
}

def _refresh_provider_clients() -> None:
    global custom_client, codex_client, CODEX_OPTIONS, CODEX_THREAD_OPTIONS, CODEX_EXPLORE_THREAD_OPTIONS, CODEX_WEB_THREAD_OPTIONS
    custom_client = _make_openai_client(BASE_URL, API_KEY)
    set_default_openai_client(custom_client, use_for_tracing=False)

    os.environ["OPENAI_BASE_URL"] = BASE_URL
    os.environ["OPENAI_API_KEY"] = API_KEY
    os.environ["CODEX_API_KEY"] = CODEX_API_KEY_VALUE

    CODEX_OPTIONS = {
        "codex_path_override": CODEX_WRAPPER_PATH,
        "base_url": CODEX_BASE_URL,
        "api_key": CODEX_API_KEY_VALUE,
        "codex_subprocess_stream_limit_bytes": 16 * 1024 * 1024,
    }
    CODEX_THREAD_OPTIONS = {
        "model": CODEX_MODEL,
        "model_reasoning_effort": "medium",
        "approval_policy": CODEX_APPROVAL_POLICY,
        "sandbox_mode": "workspace-write",
        "working_directory": TARGET_WORKSPACE,
        "skip_git_repo_check": True,
        "network_access_enabled": True,
        "web_search_mode": "disabled",
    }
    CODEX_EXPLORE_THREAD_OPTIONS = dict(CODEX_THREAD_OPTIONS)
    CODEX_EXPLORE_THREAD_OPTIONS["sandbox_mode"] = "read-only"
    CODEX_WEB_THREAD_OPTIONS = dict(CODEX_THREAD_OPTIONS)
    if os.getenv("CODEX_NETWORK_ACCESS", "1").strip().lower() not in {"0", "false", "off", "no"}:
        CODEX_WEB_THREAD_OPTIONS["network_access_enabled"] = True
    if CODEX_WEB_SEARCH_MODE not in {"", "0", "false", "off", "none", "disabled"}:
        CODEX_WEB_THREAD_OPTIONS["web_search_mode"] = CODEX_WEB_SEARCH_MODE
    codex_client = Codex(CODEX_OPTIONS)

def _apply_model_profile(profile_name: str) -> dict[str, Any]:
    global BASE_URL, API_KEY, DEFAULT_MODEL, CODEX_BASE_URL, CODEX_API_KEY_VALUE, CODEX_MODEL
    global CODEX_REASONING_EFFORT, THINKING_ENABLED

    profiles = _build_model_profiles()
    key = MODEL_PROFILE_ALIASES.get(profile_name.lower(), profile_name.lower())
    if key not in profiles:
        raise KeyError(profile_name)
    profile = profiles[key]

    BASE_URL = profile["base_url"]
    API_KEY = profile["api_key"]
    DEFAULT_MODEL = profile["model"]
    CODEX_BASE_URL = profile["codex_base_url"]
    CODEX_API_KEY_VALUE = profile["codex_api_key"]
    CODEX_MODEL = profile["codex_model"]
    CODEX_REASONING_EFFORT = _normalize_reasoning_effort(os.getenv("CODEX_REASONING_EFFORT", DEFAULT_REASONING_EFFORT))
    THINKING_ENABLED = bool(profile.get("thinking_enabled", _model_has_thinking(DEFAULT_MODEL)))

    os.environ["PYCLI_PROVIDER_KIND"] = profile["provider_kind"]
    os.environ["PYCLI_LEAD_WIRE_API"] = profile["lead_wire_api"]
    os.environ["CODEX_LOCAL_PROVIDER_ID"] = profile["codex_provider_id"]
    os.environ["CODEX_LOCAL_WIRE_API"] = profile["codex_wire_api"]
    _refresh_provider_clients()
    return profile

def _print_model_profiles(current_model: str) -> list[tuple[str, dict[str, Any]]]:
    profiles = _build_model_profiles()
    ordered = ["deepseek", "deepseek-thinking", "doubao", "xiaomi", "xiaomi-base"]
    print(f"📝 当前模型: {current_model} ({_thinking_status_label()})")
    print("可选模型预设:")
    for index, key in enumerate(ordered, start=1):
        profile = profiles[key]
        marker = "*" if profile["model"] == current_model else " "
        print(
            f"  [{index}] {marker} {key:<17} {profile['label']} | "
            f"lead={profile['model']} @ {profile['base_url']} | codex={profile['codex_model']}"
        )
    print("也可以输入自定义模型名，或输入 /model <预设名> 直接切换。")
    return [(key, profiles[key]) for key in ordered]

def make_model(model_override=None):
    use_model = model_override or DEFAULT_MODEL
    if os.getenv("PYCLI_LEAD_WIRE_API", "responses").strip().lower() == "chat":
        return OpenAIChatCompletionsModel(model=use_model, openai_client=custom_client)
    return OpenAIResponsesModel(model=use_model, openai_client=custom_client)

RUN_CONFIG = RunConfig(tracing_disabled=True)
SHOW_CODEX_STREAM = os.getenv("SHOW_CODEX_STREAM", "1") != "0"
TURN_EXECUTION_RECORDS: list[dict[str, Any]] = []
SESSION_USAGE_TOTALS = {"input": 0, "cached": 0, "output": 0, "total": 0}

def _normalize_reasoning_effort(effort: str | None) -> str:
    normalized = (effort or DEFAULT_REASONING_EFFORT or "medium").strip().lower()
    return normalized if normalized in VALID_REASONING_EFFORTS else "medium"

def _thinking_variant_for_model(model_name: str) -> str:
    raw = model_name.strip()
    lowered = raw.lower()
    if lowered.endswith("-thinking"):
        return raw
    if raw in {"DeepSeekV4", "DeepSeekV3"}:
        return "DeepSeekV4-thinking"
    if raw in {"DeepSeek V4", "DeepSeek V3"}:
        return "DeepSeekV4-thinking"
    if lowered in {"deepseek-web", "deepseek-web-deerflow", "deepseek-web-deerflow-sticky"}:
        return "DeepSeekV4-thinking"
    return f"{raw}-thinking"

def _is_local_deepseek_provider() -> bool:
    provider_kind = os.getenv("PYCLI_PROVIDER_KIND", "").strip().lower()
    if provider_kind in {"ark", "doubao", "openai", "proxy", "ark_proxy", "responses_proxy", "xiaomi", "xiaomi_web", "mimo"}:
        return False
    if provider_kind in {"deepseek", "deepseek_local", "deerflow", "deerflow_local"}:
        return True
    return (
        (BASE_URL.startswith("http://127.0.0.1") or BASE_URL.startswith("http://localhost"))
        and (
            ":18765" in BASE_URL
            or BASE_URL.rstrip("/").endswith(":18765/v1")
            or ":8765" in BASE_URL
            or BASE_URL.rstrip("/").endswith(":8765/v1")
        )
    )

def _request_extra_body(thinking_enabled: bool | None = None) -> dict[str, Any]:
    if _is_local_deepseek_provider():
        return {"thinking_enabled": THINKING_ENABLED if thinking_enabled is None else thinking_enabled}
    return {}

def _reasoning_payload(thinking_enabled: bool | None = None, effort: str | None = None) -> dict[str, Any]:
    enabled = THINKING_ENABLED if thinking_enabled is None else thinking_enabled
    if not enabled:
        return {"effort": "none"}
    normalized = _normalize_reasoning_effort(effort)
    if normalized == "none":
        normalized = "medium"
    return {"effort": normalized}

def make_model_settings(
    thinking_enabled: bool | None = None,
    reasoning_effort: str | None = None,
) -> ModelSettings:
    enabled = THINKING_ENABLED if thinking_enabled is None else thinking_enabled
    settings: dict[str, Any] = {}
    extra_body = _request_extra_body(enabled)
    wire_api = os.getenv("PYCLI_LEAD_WIRE_API", "responses").strip().lower()
    reasoning_style = os.getenv("PYCLI_REASONING_STYLE", "auto").strip().lower()
    is_local_deepseek = bool(extra_body)
    if reasoning_style == "auto":
        if "ark.cn-beijing.volces.com" in BASE_URL and wire_api == "chat":
            reasoning_style = "chat_object"
        else:
            reasoning_style = "responses"

    if is_local_deepseek or enabled:
        reasoning_payload = _reasoning_payload(enabled, reasoning_effort)
        if reasoning_style == "none" and not is_local_deepseek:
            pass
        elif reasoning_style == "chat_object" and wire_api == "chat" and not is_local_deepseek:
            extra_body["reasoning"] = reasoning_payload
        else:
            settings["reasoning"] = reasoning_payload

    if extra_body:
        settings["extra_body"] = extra_body
    if wire_api == "responses":
        settings["store"] = _response_store_enabled()
    raw_temperature = os.getenv("PYCLI_TEMPERATURE")
    if raw_temperature not in {None, ""}:
        try:
            settings["temperature"] = float(raw_temperature)
        except ValueError:
            pass
    raw_top_p = os.getenv("PYCLI_TOP_P")
    if raw_top_p not in {None, ""}:
        try:
            settings["top_p"] = float(raw_top_p)
        except ValueError:
            pass
    return ModelSettings(**settings)

def _codex_model_for_current_mode(model_name: str) -> str:
    deepseek_names = {"deepseek-web", "deepseek-web-deerflow", "deepseek-web-deerflow-sticky", "deepseekv4", "deepseekv3", "deepseek v4", "deepseek v3"}
    if THINKING_ENABLED and not _model_has_thinking(model_name) and model_name.strip().lower() in deepseek_names:
        return _thinking_variant_for_model(model_name)
    return model_name

def _thinking_status_label() -> str:
    status = "thinking:on" if THINKING_ENABLED else "thinking:off"
    return f"{status}/{_normalize_reasoning_effort(DEFAULT_REASONING_EFFORT)}"

def _usage_value(obj: Any, key: str) -> int:
    if obj is None:
        return 0
    if isinstance(obj, dict):
        value = obj.get(key)
    else:
        value = getattr(obj, key, None)
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0

def _usage_child(obj: Any, key: str) -> Any:
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)

def _usage_summary_from_obj(usage: Any) -> dict[str, int]:
    input_tokens = _usage_value(usage, "input_tokens") or _usage_value(usage, "prompt_tokens")
    output_tokens = _usage_value(usage, "output_tokens") or _usage_value(usage, "completion_tokens")
    details = _usage_child(usage, "input_tokens_details") or _usage_child(usage, "prompt_tokens_details")
    cached_tokens = (
        _usage_value(usage, "cached_input_tokens")
        or _usage_value(details, "cached_tokens")
        or _usage_value(details, "cached_input_tokens")
    )
    total_tokens = _usage_value(usage, "total_tokens") or input_tokens + output_tokens
    return {
        "input": input_tokens,
        "cached": cached_tokens,
        "output": output_tokens,
        "total": total_tokens,
    }

def _context_token_breakdown(chat_history: list) -> dict[str, int]:
    summary = {"input": 0, "output": 0, "total": 0}
    try:
        static_context = _lead_static_context_text()
        if static_context:
            summary["input"] += _count_text_tokens(static_context)
        for message in chat_history:
            if isinstance(message, dict):
                role = str(message.get("role", "user"))
                content = _context_text(message.get("content", ""))
                chunk = f"{role}:\n{content}"
            else:
                role = "user"
                chunk = _context_text(message)
            chunk_tokens = _count_text_tokens(chunk) + 6
            if role == "assistant":
                summary["output"] += chunk_tokens
            else:
                summary["input"] += chunk_tokens
        summary["input"] += TOKEN_FRAMING_OVERHEAD
        summary["total"] = summary["input"] + summary["output"]
        return summary
    except Exception:
        raw = _context_text(chat_history)
        summary["input"] = TOKEN_FRAMING_OVERHEAD + max(1, len(raw.encode("utf-8")) // 3)
        summary["total"] = summary["input"]
        return summary

def _collect_result_usage(result: Any) -> dict[str, int]:
    summary = {"input": 0, "cached": 0, "output": 0, "total": 0}
    for response in getattr(result, "raw_responses", []) or []:
        usage_summary = _usage_summary_from_obj(getattr(response, "usage", None))
        for key in summary:
            summary[key] += usage_summary[key]
    return summary

def _add_session_usage(summary: dict[str, int]) -> None:
    for key in SESSION_USAGE_TOTALS:
        SESSION_USAGE_TOTALS[key] += int(summary.get(key, 0) or 0)

def _format_usage_summary(summary: dict[str, int]) -> str:
    return (
        f"in={summary.get('input', 0)/1000:.1f}k, "
        f"cached={summary.get('cached', 0)/1000:.1f}k, "
        f"out={summary.get('output', 0)/1000:.1f}k, "
        f"total={summary.get('total', 0)/1000:.1f}k"
    )

# 🚀 完整注入纪律与效率规范
TOOL_CALLING_HINT = (
    "工具调用兼容规则：当你需要调用任何 tool 时，必须真的输出 tool_calls，"
    "并为每个 tool call 使用一个具体 id，例如 user_task_1、user_task_2。"
    "这些 id 是本地网关要求的有效调用 id，不是占位符。"
    "不要只用文字描述你要调用工具。"
    "如果任务要求调用 codex 或 codex_web，arguments 必须形如 "
    '{"task":"具体任务"}。'
    "传给 codex/codex_web 的 task 必须明确写入：第一步必须执行真实工具动作"
    "（命令、读文件、写文件或 web_search），不允许凭空回答。"
    "不要回复“正在调用/我将调用/已调用”来代替 tool_calls。\n"
    "【特权与沙盒纪律(Append)】：\n"
    "1. 你的物理世界仅限于工作区！严禁在命令中使用 `../` 向上跳出目录。\n"
    "2. 如果遇到 Network Error 或 Permission Denied，说明已被沙盒拦截。禁止反复重试 pip，明确回复‘我需要访问网络/权限，请授权’触发审批。\n"
    "【执行效率规范(Efficiency Directives)】：\n"
    "1. 优先使用 Python 处理各类任务。\n"
    "2. 对于简短、一次性或测试逻辑（如发请求、验证库等），绝对不要新建 `.py` 文件！必须直接使用 `python -c '代码串'` 一步执行，免除文件读写。\n"
    "3. 正式产物必须写在当前 workspace 内；只有当用户明确给出外部路径时，才允许把脚本、数据库或关键中间文件写到 workspace 之外。\n"
    "4. 涉及数据准备、数据爬取、入库或回测前置数据时，必须先从用户上下文和工作区文件推断真实数据域、标的范围、时间范围和字段需求；"
    "优先使用该数据域的专门库或官方/权威接口，不得用演示标的或示例数据替代真实需求。完成后必须验证入库记录数、样例行和下游脚本可读性。\n"
    f"{VENV_INJECT_PROMPT}"
)

LEAD_ROUTING_PROTOCOL = (
    "路由协议：对于单个 bugfix、单条验证链路、单个交互修复、"
    "或只涉及 1 到 3 个紧密相关文件的工程任务，优先直接调用 codex，"
    "不要默认先 execute_sub_tasks。\n"
    "如果任务同时满足这两个条件：1) 需要实时网页信息、官网内容、搜索结果或在线资料；"
    "2) 还要求在当前 workspace 里生成文件、保存抓取结果、写总结、整理证据或继续多步执行，"
    "那么外层不要停在 web_search 摘要阶段，必须直接调用 codex_web"
    "（若已知准确 URL 且不再需要搜索，可改为 codex）让子任务完成搜索、抓取、落盘和验证。\n"
    "外层 web_search 只适合纯信息问答、快速事实补充或为下一步路由补一条短证据；"
    "不适合作为需要真实产物的最终执行路径。\n"
    "只有当任务明显包含两个以上相对独立的工作流、可分配的文件责任边界，"
    "或 codex 的只读探索已经证明需要拆分时，才调用 execute_sub_tasks。\n"
    "如果决定拆分，子任务描述必须写明目标文件/模块边界、已有失败证据、"
    "以及每个子任务自己的交付物和验证方式。"
)

SUBTASK_EVIDENCE_PROTOCOL = (
    "子任务证据协议：每个 execute_sub_tasks 子任务默认必须产生可观察证据，"
    "例如实际命令执行、文件变更、测试输出或读取验证。"
    "不要把“我将要做/我会做”当成完成。"
    "只有当任务明确标记为 [analysis-only] 或说明纯分析/无需本地执行时，才允许无命令完成。"
)

SUBTASK_ACTION_FIRST_PROTOCOL = (
    "子任务执行协议：如果任务需要本地操作，你的第一轮就应该发起真实工具动作，"
    "例如 command/file/mcp/web，而不是先写“我来创建/我将执行/已完成”。"
    "没有真实工具项，就不算开始执行。"
)

POST_SUBTASK_MAINLINE_PROTOCOL = (
    "子任务回收协议：每次 execute_sub_tasks 返回后，你必须先回到用户主线任务做一次显式复盘，"
    "包括：1) 原计划是否仍合理；2) 子任务是否真的完成；3) 是否需要修改计划；"
    "4) 当前能否向用户交差。"
    "如果可以交差，最终 content 必须给出这个复盘摘要和结果。"
    "如果不能交差，必须继续发起下一步 tool_calls（codex、codex_web、web_search 或新的 execute_sub_tasks），"
    "不能忘掉主线后直接结束。"
)

# ==========================================
# 🧠 4. 记忆与 Token 管理
# ==========================================
def _get_token_encoder():
    try:
        return tiktoken.get_encoding("cl100k_base")
    except Exception:
        return None

def _count_text_tokens(text: str) -> int:
    enc = _get_token_encoder()
    if enc is None:
        return max(1, len(text.encode("utf-8")) // 3)
    return len(enc.encode(text))

def _context_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        parts: list[str] = []
        for key, item in value.items():
            text = _context_text(item)
            if text:
                parts.append(f"{key}: {text}")
        return "\n".join(parts)
    if isinstance(value, (list, tuple, set)):
        return "\n".join(text for item in value if (text := _context_text(item)))
    return str(value)

def _lead_static_context_text() -> str:
    tool_context = {
        "tools": [
            {
                "name": "codex",
                "description": "Run a checked Codex workspace task with command/file evidence required.",
                "arguments": {"task": "string"},
            },
            {
                "name": "codex_web",
                "description": "Run a checked Codex workspace task with native web_search enabled.",
                "arguments": {"task": "string"},
            },
            {
                "name": "web_search",
                "description": "Search the web for real-time information.",
                "arguments": {"query": "string"},
            },
            {
                "name": "execute_sub_tasks",
                "description": "Break complex tasks into isolated Codex sub-tasks and execute.",
                "arguments": {"tasks": ["string"]},
            },
        ]
    }
    lead_instructions = (
        "你是一个高级架构师。对于复杂开发任务，必须调用 execute_sub_tasks 将任务分派给下属。\n"
        "涉及明确文件修改、命令执行或产物生成时，调用 codex 工具；需要实时网络资料时调用 web_search。\n"
        "只有当 Codex 子任务自身需要搜索实时网络资料时，才调用 codex_web。\n"
        "当信息不足时，可以继续调用 codex，但在 task 开头加入 [read-only-explore]，表示先执行一次短只读探索片段；"
        "这不是默认前置阶段，只在缺少事实时使用。该探索片段只负责收集事实、关键路径、风险和信息充足性，不负责选择下一步工具。\n"
        "调用 codex/codex_web 时，传入 {\"task\":\"任务说明\"}。\n"
        "如果是在延续既有工作，传给 codex/codex_web/execute_sub_tasks 的任务必须显式包含必要上下文，"
        "例如相关路径、已生成文件、数据库名、失败命令、错误原因、用户纠正点和下一步目标。\n"
        "对于跨轮、多步骤、依赖既有文件或强逻辑连续性的工作，必须先利用最近的[执行记录]、"
        "前情摘要和用户纠正点，明确已完成内容、失败点、产物路径和下一步，再继续推进；"
        "不要丢弃上下文后重启一个不相关方案。\n"
        f"当前推理模式: {_thinking_status_label()}。\n"
    )
    return "\n\n".join(
        [
            lead_instructions,
            LEAD_ROUTING_PROTOCOL,
            SUBTASK_EVIDENCE_PROTOCOL,
            SUBTASK_ACTION_FIRST_PROTOCOL,
            POST_SUBTASK_MAINLINE_PROTOCOL,
            TOOL_CALLING_HINT,
            VENV_INJECT_PROMPT,
            json.dumps(tool_context, ensure_ascii=False, separators=(",", ":")),
        ]
    )

def _history_context_text(chat_history: list) -> str:
    lines: list[str] = []
    for message in chat_history:
        if isinstance(message, dict):
            role = str(message.get("role", "user"))
            content = _context_text(message.get("content", ""))
            lines.append(f"{role}:\n{content}")
        else:
            lines.append(_context_text(message))
    return "\n\n".join(line for line in lines if line)

def get_token_count(chat_history: list) -> int:
    return _context_token_breakdown(chat_history)["total"]


LEAD_SYSTEM_RECALL_TEXT = (
    "【系统提示词召回】\n"
    "你是当前回合的 Lead，不是普通聊天机器人。最后决策前，必须再次遵守这些系统规则：\n"
    "1. 只要任务需要搜索、抓取网页、运行命令、读写文件、生成产物或继续既有工作，就必须输出真实 tool_calls，不能只做口头规划。\n"
    "2. 如果任务同时需要实时网络信息和本地工作区产物，外层不要停在 web_search 摘要阶段，必须继续调用 codex_web 或 codex 完成落盘、验证和总结。\n"
    "3. 没有真实工具证据时，不能声称已完成、已派发、已生成或已验证；如果不能交差，继续调用下一步工具。\n"
    "4. 必须利用最近的执行记录、用户纠正点、已有文件路径、失败原因和搜索线索继续推进，不能丢掉上下文后重启无关方案。\n"
    "5. 最终交付必须与真实工作区一致；需要文件时，不能只给摘要或计划。"
)


def _build_lead_run_history(chat_history: list[dict[str, str]]) -> list[dict[str, str]]:
    if not chat_history:
        return []
    run_history = [dict(message) for message in chat_history]
    insert_at = len(run_history)
    if run_history[-1].get("role") == "user":
        insert_at -= 1
    run_history.insert(insert_at, {"role": "system", "content": LEAD_SYSTEM_RECALL_TEXT})
    return run_history


SUBAGENT_SYSTEM_RECALL_TEXT = (
    "Execution recall:\n"
    "- Start with a real tool action when local work is required; do not begin with planning prose.\n"
    "- Do not say a file, summary, crawl, or verification is complete unless the corresponding command/file evidence exists in this workspace.\n"
    "- If the task requires web lookup plus local artifacts, treat search as only the first step; continue until files are saved and verified.\n"
    "- Reuse the exact task context, known paths, prior evidence, and user corrections instead of restarting with a disconnected plan.\n"
    "- If the requested output files are missing, the task is not complete."
)

async def compress_history(chat_history: list) -> list:
    if len(chat_history) <= 4:
        return chat_history
    print("\n⏳ 正在触发深层记忆压缩...", end="\r")
    try:
        keep_tail = 8
        text_to_compress = "\n".join([f"{m['role']}: {m['content']}" for m in chat_history[:-keep_tail]])
        response = await custom_client.chat.completions.create(
            model=DEFAULT_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是一个记忆压缩引擎。请保留技术决策、文件路径、命令结果、"
                        "Codex/子任务执行摘要、失败原因、用户纠正点和下一步待办。"
                        "如果出现[执行记录]，必须把其中的关键命令、生成文件、数据库路径、"
                        "错误和未完成步骤写进摘要，方便用户说“继续”时准确接上。"
                    ),
                },
                {"role": "user", "content": text_to_compress}
            ],
            extra_body=_request_extra_body(),
        )
        summary = response.choices[0].message.content
        print("✅ 记忆压缩完成。")
        return [{"role": "user", "content": f"[前情摘要]:\n{summary}"}] + chat_history[-keep_tail:]
    except Exception as e:
        print(f"⚠️ 压缩失败: {e}")
        return chat_history

# ==========================================
# 🛠️ 5. 工具定义与判断逻辑
# ==========================================
def _truncate_text(text: str, limit: int = 1200) -> str:
    text = text or ""
    return text if len(text) <= limit else text[-limit:]

def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default

CONSOLE_BLOCK_MAX_LINES = max(1, _env_int("PYCLI_CONSOLE_BLOCK_MAX_LINES", 6))
CONSOLE_BLOCK_MAX_CHARS = max(80, _env_int("PYCLI_CONSOLE_BLOCK_MAX_CHARS", 900))

def _console_block_preview(
    text: str,
    *,
    max_lines: int | None = None,
    max_chars: int | None = None,
) -> str:
    text = (text or "").rstrip()
    if not text:
        return ""

    line_limit = max(1, max_lines or CONSOLE_BLOCK_MAX_LINES)
    char_limit = max(80, max_chars or CONSOLE_BLOCK_MAX_CHARS)
    lines = text.splitlines()
    preview_lines = lines[:line_limit]
    preview = "\n".join(preview_lines)

    truncated_by_lines = len(lines) > line_limit
    truncated_by_chars = len(preview) > char_limit
    if truncated_by_chars:
        preview = preview[:char_limit].rstrip()

    suffix_parts: list[str] = []
    if truncated_by_lines:
        suffix_parts.append(f"省略 {len(lines) - line_limit} 行")
    if truncated_by_chars:
        suffix_parts.append("省略后续字符")
    if suffix_parts:
        preview = f"{preview}\n... [{'，'.join(suffix_parts)}]"
    return preview

def _indent_block(text: str, prefix: str = "      ") -> str:
    lines = (text or "").rstrip().splitlines()
    return "\n".join(f"{prefix}{line}" for line in lines) if lines else ""

def _print_codex_line(text: str = "") -> None:
    if not SHOW_CODEX_STREAM: return
    sys.stdout.write("\033[K")
    print(text, flush=True)

def _print_codex_block(
    text: str,
    *,
    prefix: str = "      ",
    max_lines: int | None = None,
    max_chars: int | None = None,
) -> None:
    preview = _console_block_preview(text, max_lines=max_lines, max_chars=max_chars)
    if preview:
        _print_codex_line(_indent_block(preview, prefix))

class _AsyncStatusIndicator:
    _active_owner: int | None = None
    _active_priority = -1
    _active_touched = 0.0
    _owner_stale_seconds = 3.0

    def __init__(self, *, enabled: bool = True, priority: int = 10) -> None:
        self.enabled = enabled
        self.priority = priority
        self._owner_id = id(self)
        self.label = ""
        self.detail = ""
        self.deadline: float | None = None
        self.mode = "idle"
        self.started_at = time.monotonic()
        self._stop = False
        self._frame = 0

    def _claim_render_slot(self, now: float) -> bool:
        owner = type(self)._active_owner
        stale = owner is not None and now - type(self)._active_touched > type(self)._owner_stale_seconds
        if owner in {None, self._owner_id} or stale or self.priority > type(self)._active_priority:
            type(self)._active_owner = self._owner_id
            type(self)._active_priority = self.priority
            type(self)._active_touched = now
            return True
        return False

    def _release_render_slot(self) -> bool:
        if type(self)._active_owner != self._owner_id:
            return False
        type(self)._active_owner = None
        type(self)._active_priority = -1
        type(self)._active_touched = time.monotonic()
        return True

    def countdown_active(self) -> bool:
        return self.deadline is not None and self.deadline > time.monotonic()

    def set(
        self,
        label: str,
        detail: str = "",
        countdown_seconds: float | None = None,
        *,
        force: bool = False,
    ) -> None:
        if not self.enabled:
            return
        now = time.monotonic()
        compact_detail = _compact_line(detail, 110) if detail else ""
        countdown_active = self.deadline is not None and self.deadline > now
        if countdown_active and countdown_seconds is None and not force:
            return
        if countdown_active and countdown_seconds is not None and label == self.label and compact_detail == self.detail:
            return
        deadline = now + countdown_seconds if countdown_seconds and countdown_seconds > 0 else None
        same_label = label == self.label
        same_detail = compact_detail == self.detail
        same_deadline = (
            (deadline is None and self.deadline is None)
            or (deadline is not None and self.deadline is not None and abs(deadline - self.deadline) < 0.75)
        )
        if same_label and same_detail and same_deadline:
            return
        self.label = label
        self.detail = compact_detail
        self.deadline = deadline
        self.mode = "countdown" if deadline is not None else "normal"
        self.started_at = now

    def clear(self) -> None:
        if not self.enabled:
            return
        owned_line = self._release_render_slot()
        self.label = ""
        self.detail = ""
        self.deadline = None
        self.mode = "idle"
        if owned_line:
            sys.stdout.write("\r\033[K")
            sys.stdout.flush()

    async def run(self) -> None:
        frames = (".", "..", "...")
        while not self._stop:
            if self.enabled and self.label:
                now = time.monotonic()
                if not self._claim_render_slot(now):
                    await asyncio.sleep(1)
                    continue
                elapsed = int(now - self.started_at)
                if self.deadline is not None:
                    remaining = int(self.deadline - now)
                    if remaining > 0:
                        suffix = f" 倒计时 {remaining}s"
                    else:
                        self.deadline = None
                        self.mode = "normal"
                        self.started_at = now
                        suffix = f" {frames[self._frame % len(frames)]}"
                else:
                    suffix = f" {elapsed}s {frames[self._frame % len(frames)]}"
                detail = f" | {self.detail}" if self.detail else ""
                sys.stdout.write(f"\r\033[K{self.label}{detail}{suffix}")
                sys.stdout.flush()
                self._frame += 1
            await asyncio.sleep(1)
        self.clear()

    async def stop(self) -> None:
        self._stop = True

def _coerce_seconds(value: Any, *, milliseconds: bool = False) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        seconds = float(value) / 1000 if milliseconds else float(value)
        return seconds if seconds > 0 else None
    if isinstance(value, str):
        match = re.search(r"\d+(?:\.\d+)?", value)
        if match:
            seconds = float(match.group(0)) / 1000 if milliseconds or "ms" in value.lower() else float(match.group(0))
            return seconds if seconds > 0 else None
    return None

def _event_countdown_seconds(event: Any) -> float | None:
    for name in ("yield_time_ms", "yield_timeout_ms", "wait_time_ms", "retry_after_ms"):
        seconds = _coerce_seconds(getattr(event, name, None), milliseconds=True)
        if seconds:
            return seconds
    for name in ("yield_time", "yield_time_seconds", "yield_timeout_seconds", "retry_after", "wait_time"):
        seconds = _coerce_seconds(getattr(event, name, None))
        if seconds:
            return seconds
    data = getattr(event, "__dict__", {}) or {}
    if isinstance(data, dict):
        for name in ("yield_time_ms", "yield_timeout_ms", "wait_time_ms", "retry_after_ms"):
            seconds = _coerce_seconds(data.get(name), milliseconds=True)
            if seconds:
                return seconds
        for name in ("yield_time", "yield_time_seconds", "yield_timeout_seconds", "retry_after", "wait_time"):
            seconds = _coerce_seconds(data.get(name))
            if seconds:
                return seconds
    return None

def _update_codex_progress(progress: _AsyncStatusIndicator, event: Any, worker_name: str) -> None:
    event_type = getattr(event, "type", "")
    if event_type in {"turn.completed", "turn.failed", "error"}:
        progress.clear()
        return

    countdown = _event_countdown_seconds(event)
    if countdown:
        progress.set(f"  ⏳ [{worker_name}] 等待模型继续", countdown_seconds=countdown)
        return

    item = getattr(event, "item", None)
    item_type = getattr(item, "type", "") if item is not None else ""

    if event_type == "turn.started":
        progress.set(f"  ⏳ [{worker_name}] 等待模型生成")
    elif event_type == "item.started" and item_type == "command_execution":
        progress.set(f"  ⏳ [{worker_name}] 命令执行中", _compact_line(getattr(item, "command", ""), 140))
    elif event_type == "item.started" and item_type:
        progress.set(f"  ⏳ [{worker_name}] {item_type} 执行中")
    elif event_type == "item.completed":
        progress.set(f"  ⏳ [{worker_name}] 等待模型整理结果")

async def _await_with_status(awaitable: Any, label: str, detail: str = "") -> Any:
    progress = _AsyncStatusIndicator(enabled=True, priority=10)
    progress.set(label, detail)
    progress_task = asyncio.create_task(progress.run())
    try:
        return await awaitable
    finally:
        await progress.stop()
        await progress_task

async def _cancel_running_task(task: asyncio.Task | None, *, timeout: float = 1.5) -> None:
    if task is None or task.done():
        return
    task.cancel()
    try:
        await asyncio.wait_for(task, timeout=timeout)
    except (asyncio.CancelledError, asyncio.TimeoutError):
        pass
    except Exception:
        pass

def _tool_receipt(tool_name: str, intent: str, result: str) -> str:
    clean_intent = _compact_line(intent, 220)
    clean_result = (result or "").strip() or "[工具已执行，但这次没有产出可展示正文。]"
    return (
        f"[tool:{tool_name}]\n"
        f"意图: {clean_intent}\n"
        f"结果:\n{clean_result}"
    )

def _compact_line(text: str, limit: int = 220) -> str:
    compact = " ".join((text or "").split())
    return compact if len(compact) <= limit else compact[: limit - 1] + "…"

def _append_turn_execution_record(record: dict[str, Any]) -> None:
    TURN_EXECUTION_RECORDS.append(record)

def _format_turn_execution_records(records: list[dict[str, Any]] | None = None) -> str:
    records = records if records is not None else TURN_EXECUTION_RECORDS
    if not records:
        return ""

    lines = ["[执行记录 - 自动写入，供恢复/继续使用]"]
    for index, record in enumerate(records[-12:], start=1):
        task = _compact_line(str(record.get("task", "")), 260)
        worker = record.get("worker", record.get("kind", "tool"))
        status = record.get("status", "?")
        lines.append(f"{index}. {worker} status={status}; task={task}")

        error = record.get("error")
        if error:
            lines.append(f"   error: {_compact_line(str(error), 300)}")

        for command in (record.get("commands") or [])[:5]:
            cmd = _compact_line(str(command.get("command", "")), 220)
            exit_code = command.get("exit_code")
            lines.append(f"   command[{exit_code}]: {cmd}")
            output = _compact_line(str(command.get("output_tail", "")), 360)
            if output:
                lines.append(f"   output_tail: {output}")

        files = record.get("files") or []
        if files:
            lines.append("   files: " + "; ".join(_compact_line(str(f), 160) for f in files[:8]))

        tools = record.get("tools") or []
        if tools:
            lines.append("   tools: " + "; ".join(_compact_line(str(t), 160) for t in tools[:8]))

        result = _compact_line(str(record.get("result", "")), 700)
        if result:
            lines.append(f"   result: {result}")

    return "\n".join(lines)

def _history_content_with_execution_record(final_output: str) -> str:
    record_text = _format_turn_execution_records()
    if not record_text:
        return final_output
    visible_output = final_output or "[本轮无最终文本输出]"
    return f"{visible_output}\n\n{record_text}"

def _format_change(change) -> str:
    kind = getattr(change, "kind", "?")
    path = getattr(change, "path", "")
    return f"{kind}: {path}"

def _task_allows_text_only(task_prompt: str) -> bool:
    lowered = task_prompt.lower()
    text_only_markers = (
        "[analysis-only]", "analysis only", "pure analysis", "text only",
        "no local execution", "no shell", "no command", "no commands",
        "no file changes", "无需本地执行", "不用执行", "不要执行",
        "不用命令", "不要命令", "不改文件", "纯分析", "只分析", "仅分析", "只讨论"
    )
    return any(marker in lowered for marker in text_only_markers)

def _task_requires_local_evidence(task_prompt: str) -> bool:
    lowered = task_prompt.lower()
    local_markers = (
        "run ", "execute", "command", "shell", "create", "write", "edit",
        "read", "verify", "file", "运行", "执行", "命令", "创建", "写入",
        "修改", "读取", "验证", "文件"
    )
    return any(marker in lowered for marker in local_markers)

_FILE_PATH_RE = re.compile(
    r"(?P<path>(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\."
    r"(?:py|txt|md|json|csv|tsv|ya?ml|toml|html|css|js|ts|tsx|jsx|sh|log))"
)

def _normalize_expected_path(path_text: str) -> Path | None:
    cleaned = path_text.strip().strip("`'\"“”‘’。，,;；:")
    if not cleaned or "*" in cleaned: return None
    path = Path(cleaned)
    return path if path.is_absolute() else Path(TARGET_WORKSPACE) / path

def _extract_expected_written_paths(task_prompt: str) -> list[Path]:
    patterns = [
        r"(?:create|write|edit|save|append to|output to|merge into)\s+(?:the\s+)?(?:file\s+)?(?P<path>{file})",
        r"(?:创建|写入|写|生成|保存|输出到|合并成|修改|补齐)\s*(?:一个|文件|到|为|：|:)?\s*(?P<path>{file})",
        r">\s*(?P<path>{file})",
    ]
    file_pattern = _FILE_PATH_RE.pattern.replace("(?P<path>", "(?:")
    expected: list[Path] = []
    seen: set[str] = set()
    for template in patterns:
        pattern = re.compile(template.format(file=file_pattern), re.IGNORECASE)
        for match in pattern.finditer(task_prompt):
            path = _normalize_expected_path(match.group("path"))
            if path:
                key = str(path)
                if key not in seen:
                    seen.add(key)
                    expected.append(path)
    return expected


def _extract_candidate_file_hints(text: str, *, limit: int = 8) -> list[str]:
    seen: set[str] = set()
    hints: list[str] = []
    for match in _FILE_PATH_RE.finditer(text or ""):
        path_text = match.group("path").strip()
        if path_text and path_text not in seen:
            seen.add(path_text)
            hints.append(path_text)
            if len(hints) >= limit:
                break
    return hints


def _build_target_file_guidance(task_prompt: str) -> str:
    hints = _extract_candidate_file_hints(task_prompt)
    if not hints:
        return ""
    joined = ", ".join(hints)
    return (
        "Target-file guidance:\n"
        f"- User/context already mentioned these files or paths: {joined}\n"
        "- Inspect these hinted files first before guessing alternate filenames or app structures.\n"
        "- If a hinted file exists and is relevant, keep working there instead of pivoting to a same-role filename elsewhere.\n"
    )

def _looks_like_claimed_completion(text: str) -> bool:
    lowered = (text or "").lower()
    completion_markers = (
        "已完成", "已经完成", "完成了", "任务完成", "创建完成", "处理完成",
        "done", "completed", "finished", "created", "verified",
        "i created", "i have created", "i finished", "i completed"
    )
    return any(marker in lowered for marker in completion_markers)

_ABS_ARTIFACT_RE = re.compile(r"(?<!\S)/(?:[^/\s'\"`]+/)*[^/\s'\"`]+?\.(?:py|db|sqlite|sqlite3|csv|tsv|json|txt|md|parquet)\b")

def _task_allows_external_artifacts(task_prompt: str) -> bool:
    lowered = task_prompt.lower()
    return "[allow-external-artifacts]" in lowered or "允许写到外部路径" in lowered or "allow external artifacts" in lowered

def _is_within_workspace(path_text: str) -> bool:
    try:
        path = Path(path_text).expanduser().resolve()
        workspace = Path(TARGET_WORKSPACE).resolve()
        return path == workspace or path.is_relative_to(workspace)
    except Exception:
        return False

def _find_external_artifact_paths(text: str) -> list[str]:
    seen: set[str] = set()
    paths: list[str] = []
    for match in _ABS_ARTIFACT_RE.finditer(text or ""):
        path = match.group(0)
        if path not in seen and not _is_within_workspace(path):
            seen.add(path)
            paths.append(path)
    return paths


def _latest_execution_record_for_worker(worker_name: str) -> dict[str, Any] | None:
    prefix = worker_name.split("-retry", 1)[0]
    for record in reversed(TURN_EXECUTION_RECORDS):
        worker = str(record.get("worker", ""))
        if worker == worker_name or worker.startswith(prefix):
            return record
    return None


def _build_retry_evidence_context(worker_name: str) -> str:
    record = _latest_execution_record_for_worker(worker_name)
    if not record:
        return ""

    lines = ["Previous attempt evidence to reuse before restarting blind:"]
    error = record.get("error")
    if error:
        lines.append(f"- prior_error: {_compact_line(str(error), 260)}")

    commands = record.get("commands") or []
    for command in commands[:6]:
        cmd = _compact_line(str(command.get("command", "")), 220)
        exit_code = command.get("exit_code")
        lines.append(f"- prior_command[{exit_code}]: {cmd}")
        output_tail = _compact_line(str(command.get("output_tail", "")), 260)
        if output_tail:
            lines.append(f"  output_tail: {output_tail}")

    files = record.get("files") or []
    for file_change in files[:6]:
        lines.append(f"- prior_file: {_compact_line(str(file_change), 220)}")

    result = _compact_line(str(record.get("result", "")), 320)
    if result:
        lines.append(f"- prior_result: {result}")

    return "\n".join(lines)


def _fallback_final_response_from_evidence(
    worker_name: str,
    command_summaries: list[dict[str, Any]],
    file_summaries: list[str],
    tool_summaries: list[str],
    output_schema: dict[str, Any] | None,
) -> str:
    evidence_lines: list[str] = []
    for index, command in enumerate(command_summaries[:6], start=1):
        evidence_lines.append(
            f"- command[{index}] exit={command.get('exit_code')}: "
            f"{_compact_line(str(command.get('command', '')), 180)}"
        )
        output_tail = _compact_line(str(command.get("output_tail", "")), 260)
        if output_tail:
            evidence_lines.append(f"  output_tail: {output_tail}")

    for file_change in file_summaries[:8]:
        evidence_lines.append(f"- file: {_compact_line(str(file_change), 180)}")

    for tool in tool_summaries[:8]:
        evidence_lines.append(f"- tool: {_compact_line(str(tool), 180)}")

    if not evidence_lines:
        return ""

    content = (
        "[auto-summary]\n"
        f"{worker_name} 本轮产生了真实工具证据，但没有返回最终自然语言总结。\n"
        "以下是可复用证据；不要把它视为任务已完成，而应视为当前进度摘要。\n"
        "证据:\n"
        + "\n".join(evidence_lines)
    )
    if output_schema is not None:
        return json.dumps({"content": content}, ensure_ascii=False)
    return content

def on_codex_stream(payload) -> None:
    if not SHOW_CODEX_STREAM: return
    event = payload.event
    event_type = getattr(event, "type", "")
    item = getattr(event, "item", None)

    if event_type == "thread.started":
        _print_codex_line(f"\n  [codex] thread started: {event.thread_id}")
        return
    if event_type == "turn.started":
        _print_codex_line("  [codex] turn started")
        return
    if event_type == "turn.completed":
        usage = getattr(event, "usage", None)
        if usage is None:
            _print_codex_line("  [codex] turn completed")
        else:
            _print_codex_line(f"  [codex] turn completed (in={usage.input_tokens}, cached={usage.cached_input_tokens}, out={usage.output_tokens})")
        return
    if event_type == "turn.failed":
        _print_codex_line(f"  [codex] turn failed: {getattr(getattr(event, 'error', None), 'message', '')}")
        return
    if event_type == "error":
        _print_codex_line(f"  [codex] error: {getattr(event, 'message', '')}")
        return

    if item is None: return
    item_type = getattr(item, "type", "")
    
    if event_type == "item.started":
        if item_type == "command_execution":
            command_preview = _console_block_preview(getattr(item, "command", ""))
            if "\n" in command_preview:
                _print_codex_line("    $")
                _print_codex_block(command_preview)
            else:
                _print_codex_line(f"    $ {command_preview}")
        elif item_type == "mcp_tool_call": _print_codex_line(f"    [mcp] {item.server}.{item.tool} started")
        elif item_type == "web_search": _print_codex_line(f"    [web] {_compact_line(getattr(item, 'query', ''), 220)}")
        elif item_type: _print_codex_line(f"    [{item_type}] started")
        return

    if event_type not in {"item.updated", "item.completed"}: return
    status = getattr(item, "status", "")
    done = event_type == "item.completed"

    if item_type == "reasoning":
        text = getattr(item, "text", "").strip()
        if text:
            _print_codex_line("    [reasoning summary]")
            _print_codex_block(text)
        elif done: _print_codex_line("    [reasoning] completed")
        return

    if item_type == "command_execution":
        if not done: return
        _print_codex_line(f"    [command {status}] exit_code={getattr(item, 'exit_code', None)}")
        output = getattr(item, "aggregated_output", "").rstrip()
        if output: _print_codex_block(output)
        return

    if item_type == "file_change":
        if not done: return
        changes = getattr(item, "changes", []) or []
        _print_codex_line(f"    [files {status}]")
        for change in changes: _print_codex_line(f"      {_format_change(change)}")
        return

    if item_type == "mcp_tool_call":
        if not done: return
        _print_codex_line(f"    [mcp {status}] {item.server}.{item.tool}")
        error = getattr(item, "error", None)
        if error: _print_codex_line(f"      error: {getattr(error, 'message', '')}")
        return

    if item_type == "todo_list":
        items = getattr(item, "items", []) or []
        _print_codex_line("    [todo]")
        for todo in items:
            mark = "x" if getattr(todo, "completed", False) else " "
            _print_codex_line(f"      [{mark}] {getattr(todo, 'text', '')}")
        return

    if item_type == "agent_message":
        if not done: return
        text = getattr(item, "text", "").strip()
        if text:
            _print_codex_line("    [codex message]")
            _print_codex_block(text)
        return

    if item_type == "error":
        _print_codex_line(f"    [error] {_compact_line(getattr(item, 'message', ''), 260)}")

codex_client = Codex(CODEX_OPTIONS)

def _budget_limited_final_response(
    worker_name: str,
    final_response: str,
    command_summaries: list[dict[str, Any]],
    file_summaries: list[str],
    tool_summaries: list[str],
    output_schema: dict[str, Any] | None,
) -> str:
    content = (final_response or "").strip()
    if "[explore]" not in content:
        evidence: list[str] = []
        for index, command in enumerate(command_summaries, start=1):
            evidence.append(
                f"- command[{index}] exit={command.get('exit_code')}: "
                f"{_compact_line(str(command.get('command', '')), 160)} | "
                f"{_compact_line(str(command.get('output_tail', '')), 260)}"
            )
        for file_change in file_summaries:
            evidence.append(f"- file: {file_change}")
        for tool in tool_summaries:
            evidence.append(f"- tool: {tool}")
        evidence_text = "\n".join(evidence) if evidence else "- 已达到探索预算，但没有可用证据摘要。"
        content = (
            "[explore]\n"
            "已确认:\n"
            f"{evidence_text}\n"
            "关键路径:\n"
            "- 见上述预算内命令证据。\n"
            "缺失/歧义:\n"
            "- 本轮探索达到工具预算后已停止；如果还需要更多上下文，应由主线发起下一轮只读探索。\n"
            "约束/风险:\n"
            f"- {worker_name} 达到本轮探索预算，未继续追加工具调用。\n"
            "计划信息充足性:\n"
            "- partial: 已收集预算内证据，但需要主线判断是否足够支撑计划。"
        )
    elif "达到本轮探索预算" not in content:
        content = (
            f"{content.rstrip()}\n\n"
            "预算说明:\n"
            f"- {worker_name} 达到本轮探索预算，CLI 已停止继续追加工具调用；"
            "如需更多上下文，应由主线发起下一轮只读探索。"
        )
    if output_schema is not None:
        return json.dumps({"content": content}, ensure_ascii=False)
    return content

async def run_codex_subtask(
    task_prompt: str,
    worker_name: str,
    thread_options: dict[str, object] | None = None,
    required_tool_types: set[str] | None = None,
    require_local_evidence: bool | None = None,
    output_schema: dict[str, Any] | None = None,
    max_tool_events: int | None = None,
) -> str:
    is_read_only_explore = _task_requests_read_only_explore(task_prompt)
    effective_prompt = _build_read_only_explore_task(task_prompt) if is_read_only_explore else task_prompt
    if is_read_only_explore:
        thread_opt = dict(CODEX_EXPLORE_THREAD_OPTIONS)
        output_schema = output_schema or EXPLORE_OUTPUT_SCHEMA
        max_tool_events = max_tool_events or EXPLORE_COMMAND_BUDGET
    else:
        thread_opt = dict(thread_options or CODEX_THREAD_OPTIONS)
    thread_opt["model"] = _codex_model_for_current_mode(CODEX_MODEL)
    codex_wire_api = os.getenv("CODEX_LOCAL_WIRE_API", "responses").strip().lower()
    if "ark.cn-beijing.volces.com" in CODEX_BASE_URL:
        thread_opt.pop("model_reasoning_effort", None)
    elif THINKING_ENABLED:
        effort = _normalize_reasoning_effort(CODEX_REASONING_EFFORT)
        thread_opt["model_reasoning_effort"] = "medium" if effort == "none" else effort
    thread = codex_client.start_thread(thread_opt)
    
    final_response = ""
    usage = None
    command_events = 0
    file_events = 0
    tool_events = 0
    seen_tool_types: set[str] = set()
    command_summaries: list[dict[str, Any]] = []
    file_summaries: list[str] = []
    tool_summaries: list[str] = []
    recorded = False
    budget_limited = False

    def record_execution(status: str, error: str | None = None) -> None:
        nonlocal recorded
        if recorded:
            return
        recorded = True
        _append_turn_execution_record({
            "kind": "codex",
            "worker": worker_name,
            "status": status,
            "task": task_prompt,
            "model": thread_opt.get("model"),
            "thinking_enabled": _model_has_thinking(str(thread_opt.get("model", ""))) or THINKING_ENABLED,
            "reasoning_effort": thread_opt.get("model_reasoning_effort"),
            "commands": command_summaries,
            "files": file_summaries,
            "tools": tool_summaries,
            "result": _truncate_text(final_response, 2000),
            "error": error,
        })
    
    analysis_only_rule = (
        ""
        if worker_name.startswith("CodexExplore")
        else "- If the subtask is explicitly analysis-only and needs no local action, say exactly 'NO_LOCAL_EXECUTION_REQUIRED: <reason>'.\n"
    )
    task_input = [
        {
            "type": "text",
            "text": (
                f"You are {worker_name}. Complete this subtask in isolation.\n"
                "Use the current workspace only.\n"
                f"The configured working directory is exactly: {TARGET_WORKSPACE}\n"
                "Treat every relative path in the subtask as relative to that directory. "
                "Do not create helper subdirectories unless the subtask explicitly asks for them.\n"
                "Evidence protocol:\n"
                "- For normal engineering subtasks, perform observable local work: run commands, inspect files, edit files, or verify outputs.\n"
                "- If local work is required, your very first turn should begin with a real tool action rather than prose.\n"
                "- Do not claim completion from prose alone. Future-tense statements like 'I will create' or 'I'll run' are not results.\n"
                "- A completion summary without any real tool item is considered failure and will be retried.\n"
                f"{analysis_only_rule}"
                f"{SUBAGENT_SYSTEM_RECALL_TEXT}\n"
                "- When finished, return a concise result with files changed, commands run, and final verification output.\n"
                f"{_build_target_file_guidance(task_prompt)}"
                f"{VENV_INJECT_PROMPT}\n\n"
                f"Subtask:\n{effective_prompt}"
            ),
        }
    ]
    progress = _AsyncStatusIndicator(enabled=SHOW_CODEX_STREAM, priority=50)
    progress.set(f"  ⏳ [{worker_name}] 等待 Codex 创建 turn")
    progress_task = asyncio.create_task(progress.run())
    abort_signal = asyncio.Event()
    try:
        stream_result = await thread.run_streamed(
            task_input,
            TurnOptions(idle_timeout_seconds=300, output_schema=output_schema, signal=abort_signal),
        )
        progress.set(f"  ⏳ [{worker_name}] 等待模型生成")

        async for event in stream_result.events:
            on_codex_stream(SimpleNamespace(event=event, thread=thread, tool_call=None))
            _update_codex_progress(progress, event, worker_name)
            
            # 授权拦截器
            if hasattr(event, "type") and event.type == "turn.approval_required":
                progress.clear()
                with patch_stdout():
                    print(f"\n\033[93m⚠️  [{worker_name} 申请特权]: {getattr(event, 'message', '网络或执行权限')}\033[0m")
                    ans = (await _prompt_inline("👉 授权? (y=同意 / n=拒绝 / 输入指令补充说明): ")).strip().lower()
                    if ans == 'y':
                        await thread.approve()
                        print("✅ 已授权，继续执行...")
                    elif ans == 'n':
                        await thread.reject()
                        print("❌ 已拒绝。")
                    else:
                        await thread.send_input(ans)
                progress.set(f"  ⏳ [{worker_name}] 等待模型继续")
                continue

            if isinstance(event, ItemCompletedEvent) and is_agent_message_item(event.item):
                final_response = event.item.text
            elif isinstance(event, ItemCompletedEvent):
                item_type = getattr(event.item, "type", "")
                if item_type: seen_tool_types.add(item_type)
                if item_type == "command_execution":
                    command_events += 1
                    tool_events += 1
                    command_summaries.append({
                        "command": getattr(event.item, "command", ""),
                        "exit_code": getattr(event.item, "exit_code", None),
                        "output_tail": _truncate_text(getattr(event.item, "aggregated_output", ""), 1000).rstrip(),
                    })
                elif item_type == "file_change":
                    file_events += 1
                    tool_events += 1
                    for change in getattr(event.item, "changes", []) or []:
                        file_summaries.append(_format_change(change))
                elif item_type in {"mcp_tool_call", "web_search"}:
                    tool_events += 1
                    if item_type == "web_search":
                        tool_summaries.append(f"web_search: {getattr(event.item, 'query', '')}")
                    else:
                        tool_summaries.append(f"mcp: {getattr(event.item, 'server', '')}.{getattr(event.item, 'tool', '')}")
                if max_tool_events is not None and tool_events >= max_tool_events:
                    budget_limited = True
                    final_response = _budget_limited_final_response(
                        worker_name,
                        final_response,
                        command_summaries,
                        file_summaries,
                        tool_summaries,
                        output_schema,
                    )
                    abort_signal.set()
                    _print_codex_line(f"    [budget] {worker_name} reached tool budget {max_tool_events}; closing this explore turn.")
                    break
            elif isinstance(event, TurnCompletedEvent):
                usage = getattr(event, "usage", None)
            elif isinstance(event, TurnFailedEvent):
                message = f"{worker_name} failed: {getattr(getattr(event, 'error', None), 'message', '')}"
                record_execution("failed", message)
                raise RuntimeError(message)
            elif isinstance(event, ThreadErrorEvent):
                message = f"{worker_name} stream error: {getattr(event, 'message', '')}"
                record_execution("failed", message)
                raise RuntimeError(message)
    finally:
        await progress.stop()
        await progress_task

    if not final_response.strip():
        fallback_summary = _fallback_final_response_from_evidence(
            worker_name,
            command_summaries,
            file_summaries,
            tool_summaries,
            output_schema,
        )
        if fallback_summary:
            final_response = fallback_summary
        else:
            usage_text = f" usage(in={usage.input_tokens}, out={usage.output_tokens})" if usage else ""
            message = f"{worker_name} returned an empty final response.{usage_text}"
            record_execution("failed", message)
            raise RuntimeError(message)
    
    if not _task_allows_text_only(task_prompt) and tool_events == 0:
        if _looks_like_claimed_completion(final_response):
            print(f"  ⚠️ [{worker_name}] 检测到“口头完成但无真实工具项”，将判定为失败并触发重试。")
        message = (
            f"{worker_name} returned text but produced no real tool events "
            f"(command={command_events}, file={file_events}, tool={tool_events})."
        )
        record_execution("failed", message)
        raise RuntimeError(message)
    
    req_local = require_local_evidence if require_local_evidence is not None else _task_requires_local_evidence(task_prompt)
    if not _task_allows_text_only(task_prompt) and req_local and (command_events + file_events == 0):
        message = (
            f"{worker_name} returned text but produced no local command/file evidence "
            f"(command={command_events}, file={file_events}, tool={tool_events})."
        )
        record_execution("failed", message)
        raise RuntimeError(message)
    
    if required_tool_types:
        missing_tool_types = sorted(required_tool_types - seen_tool_types)
        if missing_tool_types:
            message = f"{worker_name} did not produce required tool item types: {missing_tool_types}. Seen: {sorted(seen_tool_types)}."
            record_execution("failed", message)
            raise RuntimeError(message)

    if not _task_allows_external_artifacts(task_prompt):
        artifact_scan = "\n".join(
            [final_response]
            + [str(command.get("command", "")) for command in command_summaries]
            + [str(command.get("output_tail", "")) for command in command_summaries]
        )
        external_artifacts = _find_external_artifact_paths(artifact_scan)
        if external_artifacts:
            message = (
                f"{worker_name} wrote or reported durable artifacts outside the workspace: "
                f"{external_artifacts}. Use workspace-relative paths for scripts, databases, and data files."
            )
            record_execution("failed", message)
            raise RuntimeError(message)
            
    expected_written_paths = _extract_expected_written_paths(task_prompt)
    missing_written_paths = [path for path in expected_written_paths if not path.exists()]
    if missing_written_paths:
        missing_text = ", ".join(str(path) for path in missing_written_paths)
        message = f"{worker_name} did not create expected output file(s): {missing_text}."
        record_execution("failed", message)
        raise RuntimeError(message)

    record_execution("budget_limited" if budget_limited else "completed")
    return final_response

async def _run_single_subtask(i: int, task_prompt: str) -> str:
    worker_name = f"Worker-{i+1}"
    print(f"  👉 [{worker_name}] 正在处理: {task_prompt[:60]}...")
    try:
        response = await run_codex_subtask(task_prompt, worker_name)
    except Exception as first_error:
        print(f"  ⚠️ [{worker_name}] 首次失败，重试一次: {first_error}")
        retry_context = _build_retry_evidence_context(worker_name)
        retry_prompt = (
            "Previous attempt failed, returned no final response, or claimed completion without any real tool events. "
            f"Failure reason: {first_error}\n"
            "Failure-avoidance rules:\n"
            "- Reuse the validated evidence and target paths from the previous attempt before searching again.\n"
            "- If code edits are needed, prefer Codex-native file editing so the run emits file_change events.\n"
            "- Avoid fragile shell-only patching strategies unless the change is a trivial one-liner.\n"
            "- Do not restart from a guessed file like cli.py if previous evidence already identified another target file.\n"
            "Redo the subtask from scratch if needed. If local work is required, start with a real tool action in the first turn. "
            "Do not answer with prose-only completion; produce observable tool evidence, verify with commands, and return the final result.\n\n"
            f"{retry_context}\n\n"
            f"{task_prompt}"
        )
        try:
            response = await run_codex_subtask(retry_prompt, f"{worker_name}-retry")
        except Exception as retry_error:
            return f"--- [任务 {i+1} 失败] ---\n首次错误: {first_error}\n重试错误: {retry_error}"
    return f"--- [任务 {i+1} 结果] ---\n{response}"

def _extract_response_output_text(response_body: dict) -> str:
    parts: list[str] = []
    for item in response_body.get("output", []):
        if item.get("type") == "message":
            for content in item.get("content", []):
                if content.get("type") == "output_text":
                    text = content.get("text", "")
                    if isinstance(text, str) and text:
                        parts.append(text)
    return "\n\n".join(parts)

@function_tool
async def web_search(query: str) -> str:
    """Search the web for real-time information."""
    try:
        response = await custom_client.responses.create(
            model=DEFAULT_MODEL,
            input=f"Search the web for: {query}\nReturn concise findings with source links.",
            tools=[{"type": "web_search"}],
            reasoning=_reasoning_payload(),
            store=_response_store_enabled(),
            extra_body=_request_extra_body(),
        )
        output_text = _extract_response_output_text(response.model_dump())
        if output_text.strip():
            _append_turn_execution_record({
                "kind": "web_search",
                "worker": "web_search",
                "status": "completed",
                "task": query,
                "tools": ["provider web_search"],
                "result": _truncate_text(output_text, 2000),
            })
            return _tool_receipt("web_search", f"搜索实时信息：{query}", output_text)
    except Exception as provider_error:
        provider_failure = provider_error
    else:
        provider_failure = None

    try:
        from duckduckgo_search import DDGS
        with DDGS() as ddgs:
            results = [r for r in ddgs.text(query, max_results=3)]
        if not results:
            output = "未找到相关内容。"
        else:
            output = "\n\n".join([f"{r['title']}\n{r['body']}" for r in results])
        _append_turn_execution_record({
            "kind": "web_search",
            "worker": "web_search",
            "status": "completed",
            "task": query,
            "tools": ["duckduckgo_search fallback"],
            "result": _truncate_text(output, 2000),
            "error": str(provider_failure) if provider_failure else None,
        })
        return _tool_receipt("web_search", f"搜索实时信息：{query}", output)
    except Exception as e:
        output = f"搜索失败: provider={provider_failure}; fallback={e}"
        _append_turn_execution_record({
            "kind": "web_search",
            "worker": "web_search",
            "status": "failed",
            "task": query,
            "result": output,
            "error": output,
        })
        return _tool_receipt("web_search", f"搜索实时信息：{query}", output)

async def _run_checked_codex_tool(
    task: str,
    worker_name: str,
    thread_options: dict,
    required_tool_types: set | None = None,
    require_local_evidence: bool | None = None,
    output_schema: dict[str, Any] | None = None,
    max_tool_events: int | None = None,
) -> str:
    try:
        return await run_codex_subtask(
            task,
            worker_name,
            thread_options,
            required_tool_types,
            require_local_evidence,
            output_schema,
            max_tool_events,
        )
    except Exception as first_error:
        print(f"  ⚠️ [{worker_name}] 首次失败，重试一次: {first_error}")
        finish_instruction = (
            "Return a final response that satisfies the required output schema.\n"
            if output_schema
            else "Return files changed, commands/web searches run, and verification.\n"
        )
        retry_prompt = (
            "Previous attempt failed or produced no real tool evidence. "
            f"Failure reason: {first_error}\n"
            "Redo the task. Start with a real tool action in the first turn. "
            f"{finish_instruction}\n"
            f"{task}"
        )
        return await run_codex_subtask(
            retry_prompt,
            f"{worker_name}-retry",
            thread_options,
            required_tool_types,
            require_local_evidence,
            output_schema,
            max_tool_events,
        )

async def _run_direct_read_only_probe(user_input: str) -> str:
    probe_task = f"{READ_ONLY_EXPLORE_TAG}\n{user_input}"
    result = await _run_checked_codex_tool(
        probe_task,
        "CodexProbe",
        CODEX_THREAD_OPTIONS,
        require_local_evidence=True,
    )
    return _explore_content_from_response(result)

EXPLORE_COMMAND_BUDGET = max(1, _env_int("PYCLI_EXPLORE_COMMAND_BUDGET", 4))
READ_ONLY_EXPLORE_TAG = "[read-only-explore]"
READ_ONLY_PROBE_SIGNAL_MIN = max(2, _env_int("PYCLI_READ_ONLY_PROBE_SIGNAL_MIN", 3))

EXPLORE_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["content"],
    "properties": {
        "content": {
            "type": "string",
            "description": "Markdown exploration summary beginning with [explore].",
        },
    },
}

def _task_requests_read_only_explore(task_prompt: str) -> bool:
    lowered = (task_prompt or "").lower()
    if READ_ONLY_EXPLORE_TAG in lowered:
        return True
    signals = 0
    for pattern in (
        "只读探索",
        "不要修改文件",
        "不要下载新数据",
        "关键路径",
        "缺失或歧义",
        "主要风险",
        "信息是否足够",
        "判断当前工作区里的事实信息是否足够",
    ):
        if pattern in (task_prompt or ""):
            signals += 1
    return signals >= READ_ONLY_PROBE_SIGNAL_MIN

def _user_prompt_prefers_direct_probe(user_input: str) -> bool:
    text = (user_input or "").strip()
    if not text or len(text) > 1600:
        return False
    positive_signals = [
        "判断当前工作区",
        "事实信息是否足够",
        "请先判断",
        "只读探索",
        "不要修改文件",
        "不要下载新数据",
        "关键路径",
        "缺失或歧义",
        "主要风险",
    ]
    negative_signals = [
        "修复",
        "修改代码",
        "写一个",
        "实现",
        "重构",
        "运行真实回测",
        "下载真实数据",
        "执行",
        "生成文件",
    ]
    pos = sum(1 for pattern in positive_signals if pattern in text)
    neg = sum(1 for pattern in negative_signals if pattern in text)
    return pos >= READ_ONLY_PROBE_SIGNAL_MIN and neg == 0


def _user_prompt_prefers_direct_codex(user_input: str) -> bool:
    text = (user_input or "").strip()
    lowered = text.lower()
    if not text or len(text) > 2200:
        return False
    if _user_prompt_prefers_direct_probe(text):
        return False

    positive_signals = [
        "修复",
        "bug",
        "fix",
        "/model",
        "交互",
        "空回车",
        "回车",
        "输入链路",
        "控制台",
        "输出",
        "提示",
        "重试",
        "最小验证",
        "验证一下",
        "cli",
        "单文件",
    ]
    broad_signals = [
        "架构",
        "多个模块",
        "多文件",
        "大范围",
        "大改",
        "重构整个",
        "全局",
        "系统性设计",
        "方案对比",
    ]
    mentioned_files = _extract_candidate_file_hints(text)
    pos = sum(1 for pattern in positive_signals if pattern in lowered or pattern in text)
    broad = sum(1 for pattern in broad_signals if pattern in lowered or pattern in text)
    if broad > 0:
        return False
    if len(mentioned_files) >= 4:
        return False
    return pos >= 2

def _strip_read_only_explore_tag(task_prompt: str) -> str:
    pattern = re.compile(re.escape(READ_ONLY_EXPLORE_TAG), re.IGNORECASE)
    return pattern.sub("", task_prompt or "").strip()

def _build_read_only_explore_task(task: str) -> str:
    clean_task = _strip_read_only_explore_tag(task)
    return (
        "Read-only exploration fragment. Do not modify files, create files, install packages, change git state, or create temp scripts. "
        "This is a short explore-and-summarize pass, not a full audit. "
        "Collect enough directly relevant evidence to support the next plan or to prove that more information is needed.\n"
        f"Exploration budget: at most {EXPLORE_COMMAND_BUDGET} read-only command batches in this pass. "
        "Merge information needs before each tool call. Prefer one batch command that collects schemas, row counts, samples, key files, and progress clues. "
        "Prefer shell/sqlite/head/sed/rg commands over fragile multi-line Python. "
        "Do not generalize from samples: claims such as all rows, no rows, all values are zero, every fetch failed, or no data exists must be backed by a count or aggregate check. "
        "If evidence is sufficient, partial, or clearly insufficient, stop and summarize. "
        "Final answer MUST be a JSON object matching the required schema, with the full human-readable report stored in `content`.\n"
        "The content string MUST use this format:\n"
        "[explore]\n"
        "已确认:\n"
        "- <facts with evidence>\n"
        "关键路径:\n"
        "- <files, tables, commands, or modules that matter>\n"
        "缺失/歧义:\n"
        "- <unknowns or exact user-facing questions, if any>\n"
        "约束/风险:\n"
        "- <practical constraints, failure risks, or none>\n"
        "计划信息充足性:\n"
        "- <sufficient|partial|insufficient>: <brief reason>\n\n"
        f"{clean_task}"
    )

def _explore_content_from_response(response: str) -> str:
    text = (response or "").strip()
    if not text:
        return text
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()
    try:
        data = json.loads(text)
    except Exception:
        return response.strip()
    if isinstance(data, dict) and isinstance(data.get("content"), str):
        return data["content"].strip()
    return response.strip()

@function_tool
async def codex(task: str) -> str:
    """Run a checked Codex workspace task with command/file evidence required."""
    result = await _run_checked_codex_tool(task, "Codex", CODEX_THREAD_OPTIONS)
    result_text = _explore_content_from_response(result) if _task_requests_read_only_explore(task) else result
    intent = "在当前 workspace 执行只读短探索片段" if _task_requests_read_only_explore(task) else "在当前 workspace 执行本地任务"
    return _tool_receipt("codex", f"{intent}：{task}", result_text)

@function_tool
async def codex_web(task: str) -> str:
    """Run a checked Codex workspace task with native web_search enabled."""
    web_task = (
        "You must use the native web_search tool item for the web lookup. "
        "Do not run a shell command named web_search. After web_search completes, finish the requested task.\n\n"
        f"{task}"
    )
    result = await _run_checked_codex_tool(web_task, "CodexWeb", CODEX_WEB_THREAD_OPTIONS, {"web_search"}, False)
    return _tool_receipt("codex_web", f"先做原生网页搜索再执行任务：{task}", result)

@function_tool
async def execute_sub_tasks(tasks: list[str]) -> str:
    """Break complex tasks into isolated Codex sub-tasks and execute."""
    sys.stdout.write("\033[K")
    print(f"\n[🔄 分治] 架构师派发了 {len(tasks)} 个任务...")
    concurrency = max(1, min(len(tasks), SUBTASK_CONCURRENCY_LIMIT))
    if concurrency > 1: print(f"  ↔️ 子任务并发上限: {concurrency}")

    if concurrency == 1:
        results = [await _run_single_subtask(i, t) for i, t in enumerate(tasks)]
        output = "\n\n".join(results + ["--- [主线恢复要求] ---\n" + POST_SUBTASK_MAINLINE_PROTOCOL])
        return _tool_receipt("execute_sub_tasks", f"把复杂任务拆成 {len(tasks)} 个子任务并收集结果", output)

    semaphore = asyncio.Semaphore(concurrency)
    async def _bounded_run(i: int, t: str) -> str:
        async with semaphore: return await _run_single_subtask(i, t)
    
    results = await asyncio.gather(*[_bounded_run(i, t) for i, t in enumerate(tasks)])
    output = "\n\n".join(list(results) + ["--- [主线恢复要求] ---\n" + POST_SUBTASK_MAINLINE_PROTOCOL])
    return _tool_receipt("execute_sub_tasks", f"把复杂任务拆成 {len(tasks)} 个子任务并收集结果", output)

def make_lead_agent(
    current_model: str = DEFAULT_MODEL,
    thinking_enabled: bool | None = None,
    reasoning_effort: str | None = None,
) -> Agent:
    return Agent(
        name="Lead-Architect",
        model=make_model(current_model),
        model_settings=make_model_settings(thinking_enabled, reasoning_effort),
        instructions=(
            "你是一个高级架构师。对于复杂开发任务，必须调用 execute_sub_tasks 将任务分派给下属。\n"
            "涉及明确文件修改、命令执行或产物生成时，调用 codex 工具；需要实时网络资料时调用 web_search。\n"
            "只有当 Codex 子任务自身需要搜索实时网络资料时，才调用 codex_web。\n"
            "当信息不足时，可以继续调用 codex，但在 task 开头加入 [read-only-explore]，表示先执行一次短只读探索片段；"
            "这不是默认前置阶段，只在缺少事实时使用。该探索片段只负责收集事实、关键路径、风险和信息充足性，不负责选择下一步工具。\n"
            "调用 codex/codex_web 时，传入 {\"task\":\"任务说明\"}。\n"
            "如果是在延续既有工作，传给 codex/codex_web/execute_sub_tasks 的任务必须显式包含必要上下文，"
            "例如相关路径、已生成文件、数据库名、失败命令、错误原因、用户纠正点和下一步目标。\n"
            "对于跨轮、多步骤、依赖既有文件或强逻辑连续性的工作，必须先利用最近的[执行记录]、"
            "前情摘要和用户纠正点，明确已完成内容、失败点、产物路径和下一步，再继续推进；"
            "不要丢弃上下文后重启一个不相关方案。\n"
            f"{LEAD_ROUTING_PROTOCOL}\n"
            f"当前推理模式: {_thinking_status_label()}。\n"
            f"{SUBTASK_EVIDENCE_PROTOCOL}\n"
            f"{SUBTASK_ACTION_FIRST_PROTOCOL}\n"
            f"{POST_SUBTASK_MAINLINE_PROTOCOL}\n"
            f"{TOOL_CALLING_HINT}"
        ),
        tools=[codex, codex_web, web_search, execute_sub_tasks],
    )

# ==========================================
# 🧪 6. Composition Demo 定义 (完整保留12个)
# ==========================================
COMPOSITION_DEMO_ROUNDS: list[tuple[str, str]] = [
    ("01_codex_workspace_probe", "必须调用 codex 工具：检查当前 workspace 路径，列出前 8 个文件，并用一句话说明这个 workspace 是否可写。不要改文件。"),
    ("02_codex_write_and_verify", "必须调用 codex 工具：创建 composition_round_02.txt，内容包含 ROUND_02_OK；随后读取它并汇报验证结果。"),
    ("03_codex_command_and_file", "必须调用 codex 工具：第一步必须运行真实命令，建议命令为 printf '{\"round\":3,\"status\":\"ROUND_03_OK\"}\\n' > composition_round_03.json && python3 -m json.tool composition_round_03.json；随后读取文件确认 JSON 有效。"),
    ("04_outer_web_search", "必须调用外层 web_search 工具：搜索 OpenAI Agents SDK function_tool 和 WebSearchTool 的用法，用 3 条中文要点总结。"),
    ("05_codex_native_web_search", "必须调用 codex_web 工具，并要求 Codex 在内部使用 native web_search：搜索 Codex CLI web search / Responses web_search 的关系，只返回 3 条简短结论，不要写文件。"),
    ("06_subtask_fanout_files", "必须调用 execute_sub_tasks：派发两个子任务，分别创建 alpha_composition.txt 写入 ALPHA_COMPOSITION_OK、beta_composition.txt 写入 BETA_COMPOSITION_OK，并各自用命令读取验证。"),
    ("07_subtask_analysis_only", "必须调用 execute_sub_tasks：派发两个 [analysis-only] 子任务，分别从架构角度分析 codex_tool 与 execute_sub_tasks 的职责边界；不需要本地命令。"),
    ("08_search_then_codex_note", "先调用外层 web_search 搜索 OpenAI Responses API web_search tool，再调用 codex 工具把搜索摘要和来源标题写入 composition_round_08_research_note.md。"),
    ("09_subtasks_then_aggregate", "必须先调用 execute_sub_tasks 创建三个短文件 c1.txt、c2.txt、c3.txt，每个写入自己的编号；然后调用 codex 工具把三个文件合并成 composition_round_09_aggregate.md。"),
    ("10_codex_verify_artifacts", "必须调用 codex 工具：用命令检查 composition_round_02.txt、composition_round_03.json、composition_round_09_aggregate.md 是否存在，并输出清单。"),
    ("11_repair_or_report", "必须调用 codex 工具：在当前目录检查这些明确文件：composition_round_02.txt、composition_round_03.json、composition_round_08_research_note.md、composition_round_09_aggregate.md。第一步必须运行 pwd 和 ls/测试命令。无论是否缺失，都只在当前目录写 composition_round_11_status.md，列出每个文件 PRESENT/MISSING；不要创建子目录。"),
    ("12_final_summary", "必须只调用 codex 工具，不要调用 execute_sub_tasks。本回合不是复杂开发任务，只是单个文件写入验证。传给 codex 的 task 必须明确要求三步：1) 第一条命令运行 pwd && ls -1 composition_round_* composition_round_11_status.md；2) 运行 cat composition_round_11_status.md composition_round_02.txt composition_round_03.json composition_round_08_research_note.md composition_round_09_aggregate.md；3) 用真实 shell 写入 composition_demo_final_summary.md，内容必须包含这四个标签行：codex、codex_web、web_search、execute_sub_tasks。不要总结当前宿主 Codex Desktop 的工具名，例如 exec_command、spawn_agent、update_plan。不要读取或创建子目录。"),
]

COMPOSITION_DEMO_EXPECTED_TOOLS: dict[str, tuple[str, ...]] = {
    "01_codex_workspace_probe": ("codex",), "02_codex_write_and_verify": ("codex",), "03_codex_command_and_file": ("codex",),
    "04_outer_web_search": ("web_search",), "05_codex_native_web_search": ("codex_web",), "06_subtask_fanout_files": ("execute_sub_tasks",),
    "07_subtask_analysis_only": ("execute_sub_tasks",), "08_search_then_codex_note": ("web_search", "codex"),
    "09_subtasks_then_aggregate": ("execute_sub_tasks", "codex"), "10_codex_verify_artifacts": ("codex",),
    "11_repair_or_report": ("codex",), "12_final_summary": ("codex",),
}

COMPOSITION_DEMO_EXPECTED_FILES: dict[str, tuple[str, ...]] = {
    "02_codex_write_and_verify": ("composition_round_02.txt",), "03_codex_command_and_file": ("composition_round_03.json",),
    "06_subtask_fanout_files": ("alpha_composition.txt", "beta_composition.txt"), "08_search_then_codex_note": ("composition_round_08_research_note.md",),
    "09_subtasks_then_aggregate": ("composition_round_09_aggregate.md",), "11_repair_or_report": ("composition_round_11_status.md",),
    "12_final_summary": ("composition_demo_final_summary.md",),
}

COMPOSITION_DEMO_FILE_MUST_CONTAIN: dict[str, dict[str, tuple[str, ...]]] = {
    "02_codex_write_and_verify": {"composition_round_02.txt": ("ROUND_02_OK",)},
    "03_codex_command_and_file": {"composition_round_03.json": ("ROUND_03_OK",)},
    "06_subtask_fanout_files": {"alpha_composition.txt": ("ALPHA_COMPOSITION_OK",), "beta_composition.txt": ("BETA_COMPOSITION_OK",)},
    "09_subtasks_then_aggregate": {"composition_round_09_aggregate.md": ("1", "2", "3")},
    "11_repair_or_report": {"composition_round_11_status.md": ("composition_round_02.txt: PRESENT", "composition_round_03.json: PRESENT", "composition_round_08_research_note.md: PRESENT", "composition_round_09_aggregate.md: PRESENT")},
    "12_final_summary": {"composition_demo_final_summary.md": ("codex", "codex_web", "web_search", "execute_sub_tasks")}
}

COMPOSITION_DEMO_FILE_MUST_NOT_CONTAIN: dict[str, dict[str, tuple[str, ...]]] = {
    "12_final_summary": {"composition_demo_final_summary.md": ("exec_command", "spawn_agent", "update_plan", "request_user_input", "view_image")}
}

def _parse_demo_round_limit() -> int | None:
    for arg in sys.argv[1:]:
        if arg.startswith("--demo-rounds="):
            try: return max(1, min(len(COMPOSITION_DEMO_ROUNDS), int(arg.split("=", 1)[1].strip())))
            except ValueError: return None
    return None

def _parse_demo_only() -> str | None:
    for arg in sys.argv[1:]:
        if arg.startswith("--demo-only="): return arg.split("=", 1)[1].strip()
    return None

def _format_demo_transcript(transcript: list[tuple[str, str, str]]) -> str:
    chunks = ["# Composition Demo Transcript", ""]
    for round_id, prompt, output in transcript:
        chunks.extend([f"## {round_id}", "", "### Prompt", "", prompt, "", "### Output", "", output, ""])
    return "\n".join(chunks).rstrip() + "\n"

def _result_output_text(result) -> str:
    output = result.final_output
    text = output if isinstance(output, str) else str(output) if output else ""
    if text.strip(): return text
    try: return ItemHelpers.text_message_outputs(result.new_items)
    except Exception: return text

def _describe_run_result(result) -> str:
    item_types = [getattr(item, "type", type(item).__name__) for item in result.new_items]
    raw_output_types = [getattr(item, "type", type(item).__name__) for response in result.raw_responses[-2:] for item in response.output]
    return f"new_items={item_types or []}; raw_responses={len(result.raw_responses)}; raw_output_types={raw_output_types or []}"

def _run_result_tool_names(result) -> set[str]:
    tool_names = set()
    for item in result.new_items:
        if getattr(item, "type", "") != "tool_call_item": continue
        raw_item = getattr(item, "raw_item", None)
        name = raw_item.get("name") if isinstance(raw_item, dict) else getattr(raw_item, "name", None)
        if isinstance(name, str) and name: tool_names.add(name)
    return tool_names

def _is_low_signal_output(output: str) -> bool:
    stripped = (output or "").strip()
    if len(stripped) < 8: return True
    return " ".join(stripped.lower().split()) in {"reading", "reading reading", "done", "ok", "{}", "{"}

def _demo_file_content_issues(round_id: str) -> list[str]:
    issues = []
    must_contain = COMPOSITION_DEMO_FILE_MUST_CONTAIN.get(round_id, {})
    must_not_contain = COMPOSITION_DEMO_FILE_MUST_NOT_CONTAIN.get(round_id, {})
    file_names = sorted(set(must_contain) | set(must_not_contain))
    for file_name in file_names:
        path = Path(TARGET_WORKSPACE) / file_name
        if not path.exists(): continue
        lowered = path.read_text(encoding="utf-8", errors="replace").lower()
        missing = [n for n in must_contain.get(file_name, ()) if n.lower() not in lowered]
        forbidden = [n for n in must_not_contain.get(file_name, ()) if n.lower() in lowered]
        if missing: issues.append(f"{file_name} 缺少: {missing}")
        if forbidden: issues.append(f"{file_name} 违规包含: {forbidden}")
    return issues

async def _run_demo_round(round_id: str, lead_agent: Agent, chat_history: list[dict[str, str]], expected_tools: tuple, expected_files: tuple) -> tuple[str, str]:
    diagnostics = ""
    for attempt in range(3):
        result = await Runner.run(lead_agent, _build_lead_run_history(chat_history), run_config=RUN_CONFIG)
        output = _result_output_text(result)
        diagnostics = _describe_run_result(result)
        missing_tools = sorted(set(expected_tools) - _run_result_tool_names(result))
        missing_files = [f for f in expected_files if not (Path(TARGET_WORKSPACE) / f).exists()]
        content_issues = _demo_file_content_issues(round_id)
        low_signal = _is_low_signal_output(output)
        
        if output.strip() and not missing_tools and not missing_files and not content_issues and not low_signal:
            return output, diagnostics
        
        reason = "空输出" if not output.strip() else f"缺少工具 {missing_tools}" if missing_tools else f"缺少文件 {missing_files}" if missing_files else f"内容不合格 {content_issues}" if content_issues else "低质量汇总"
        print(f"  ⚠️ Lead {reason}，重试一次。诊断: {diagnostics}")
        chat_history.append({
            "role": "user",
            "content": (
                "上一轮未合格，但必须继续同一个原始任务，禁止改写成“子任务1描述/子任务2描述”等占位任务。\n"
                f"原始任务如下：\n{chat_history[-1]['content']}\n\n"
                f"需调用工具: {', '.join(expected_tools) or '无'}。\n"
                f"需产生文件: {', '.join(expected_files) or '无'}。\n"
                f"内容问题: {'; '.join(content_issues) or '无'}。\n"
                "完成后必须输出中文总结，并说明原计划是否完成、是否需要继续。"
            )
        })
    return "", diagnostics

async def run_composition_demo(round_limit: int | None = None, only_round: str | None = None):
    lead_agent = make_lead_agent()
    rounds = [r for r in COMPOSITION_DEMO_ROUNDS if r[0] == only_round] if only_round else COMPOSITION_DEMO_ROUNDS[: round_limit or len(COMPOSITION_DEMO_ROUNDS)]
    if only_round and not rounds: raise SystemExit(f"Unknown round. Available: {', '.join(r[0] for r in COMPOSITION_DEMO_ROUNDS)}")
    
    chat_history = []
    transcript = []

    print(f"🧪 Demo 启动 | rounds={len(rounds)} | Workspace: {TARGET_WORKSPACE}")
    print("=" * 65)

    for index, (round_id, prompt) in enumerate(rounds, start=1):
        if get_token_count(chat_history) > TOKEN_LIMIT: chat_history = await compress_history(chat_history)
        full_prompt = f"[composition-demo:{round_id}]\n第 {index}/{len(rounds)} 回合。\n\n{prompt}\n\n必须产生真实 tool_calls，第一轮必须使用实际命令。"
        print(f"\n🧩 Round {index:02d} / {len(rounds)}: {round_id}")
        chat_history.append({"role": "user", "content": full_prompt})

        output, diagnostics = await _run_demo_round(round_id, lead_agent, chat_history, COMPOSITION_DEMO_EXPECTED_TOOLS.get(round_id, ()), COMPOSITION_DEMO_EXPECTED_FILES.get(round_id, ()))
        sys.stdout.write("\033[K")
        print(f"🤖 Lead 输出:\n{output}")
        if not output.strip(): print(f"⚠️ 本回合为空，诊断: {diagnostics}")
        print("-" * 65)

        transcript.append((round_id, full_prompt, output))
        chat_history.append({"role": "assistant", "content": output})

    (Path(TARGET_WORKSPACE) / "composition_demo_transcript.md").write_text(_format_demo_transcript(transcript), encoding="utf-8")
    print("\n✅ Demo 完成，转录已写入文件")

async def _prompt_inline(
    prompt_text: str,
    *,
    multiline: bool = False,
    completer=None,
) -> str:
    prompt_session = PromptSession(
        completer=completer,
        complete_while_typing=bool(completer),
        multiline=multiline,
    )
    with patch_stdout():
        return await prompt_session.prompt_async(prompt_text, multiline=multiline)

# ==========================================
# 🚀 7. 增强版交互 CLI (主循环)
# ==========================================
async def interactive_cli():
    global DEFAULT_MODEL, CODEX_MODEL, THINKING_ENABLED, DEFAULT_REASONING_EFFORT
    current_model = DEFAULT_MODEL
    chat_history = []
    last_ctrl_c_time = 0
    
    # 快捷键配置
    kb = KeyBindings()
    
    @kb.add("enter")
    def _(event):
        event.current_buffer.validate_and_handle()

    @kb.add("escape", "enter")
    def _(event):
        event.current_buffer.insert_text('\n')

    @kb.add("escape")
    def _(event):
        b = event.current_buffer
        if b.complete_state:
            b.cancel_completion()

    # 高级级联菜单设置
    command_completer = NestedCompleter.from_nested_dict({
        '/resume': None,
        '/exit': None,
        '/clear': None,
        '/save': None,
        '/thinking': {
            'on': None,
            'off': None,
            'status': None,
            'medium': None,
            'high': None,
            'xhigh': None,
        },
        '/model': {
            'list': None,
            'deepseek': None,
            'deepseek-thinking': None,
            'doubao': None,
            'xiaomi': None,
            'xiaomi-base': None,
            'mimo': None,
            'mimo-pro': None,
            'mimo-2.5': None,
            'mimo-2.5-pro': None,
            'local': None,
            'xm': None,
            'db': None,
            'DeepSeekV4': None,
            'DeepSeekV4-thinking': None,
            'thinking': None,
            'doubao-seed-2-0-mini-260215': None,
            'xiaomi-mimo-v2-pro': None,
            'xiaomi-mimo-v2.5': None,
            'xiaomi-mimo-v2.5-pro': None,
        }
    })

    session = PromptSession(
        key_bindings=kb, 
        completer=command_completer,
        complete_while_typing=True
    )

    print(f"\n🚀 架构师 CLI v2.9 | Workspace: {TARGET_WORKSPACE}")
    print(f"💡 [Enter] 提交 | [Esc+Enter] 换行 | [Ctrl+C] 取消任务/退出 | [/resume] 恢复会话")
    print("-" * 65)

    while True:
        try:
            token_breakdown = _context_token_breakdown(chat_history)
            prompt_html = (
                f"<ansigray>[{current_model} {_thinking_status_label()}]</ansigray> "
                f"<ansigreen>in~{token_breakdown['input']/1000:.1f}k "
                f"out~{token_breakdown['output']/1000:.1f}k|User: </ansigreen>"
            )

            with patch_stdout():
                user_input = await session.prompt_async(HTML(prompt_html), multiline=True)
            
            user_input = user_input.strip()
            if not user_input: continue

            # --- 🎮 解析特殊指令 ---
            if user_input.startswith("/"):
                cmd = user_input.split()[0].lower()
                if cmd == "/exit": break
                if cmd == "/model":
                    parts = user_input.split()
                    selected = parts[1] if len(parts) > 1 else ""
                    if not selected or selected.lower() in {"list", "select", "menu", "?"}:
                        choices = _print_model_profiles(current_model)
                        choice = (await _prompt_inline("👉 选择编号/预设名/自定义模型名 (回车取消): ")).strip()
                        if not choice:
                            continue
                        if choice.isdigit() and 1 <= int(choice) <= len(choices):
                            selected = choices[int(choice) - 1][0]
                        else:
                            selected = choice

                    try:
                        profile = _apply_model_profile(selected)
                        current_model = DEFAULT_MODEL
                        print(
                            f"✅ 已切换至 {profile['label']}: lead={DEFAULT_MODEL} "
                            f"codex={CODEX_MODEL} ({_thinking_status_label()})"
                        )
                    except KeyError:
                        requested_model = selected
                        wants_thinking_variant = requested_model.lower() in {"thinking", "think"} or "--thinking" in parts[2:]
                        if requested_model.lower() in {"thinking", "think"}:
                            current_model = _thinking_variant_for_model(current_model)
                        elif wants_thinking_variant:
                            current_model = _thinking_variant_for_model(requested_model)
                        else:
                            current_model = requested_model
                        if wants_thinking_variant or _model_has_thinking(current_model):
                            THINKING_ENABLED = True
                        elif "--no-thinking" in parts[2:]:
                            THINKING_ENABLED = False
                        DEFAULT_MODEL = current_model
                        CODEX_MODEL = current_model
                        _refresh_provider_clients()
                        print(f"✅ 模型已切换至自定义名称: {current_model} ({_thinking_status_label()})")
                    continue
                if cmd in {"/thinking", "/think"}:
                    parts = user_input.split()
                    if len(parts) == 1 or parts[1].lower() == "status":
                        print(f"🧠 当前思考模式: {_thinking_status_label()} | model={current_model}")
                        print("   用法: /thinking on [medium|high|xhigh] 或 /thinking off")
                        continue
                    action = parts[1].lower()
                    if action in VALID_REASONING_EFFORTS:
                        DEFAULT_REASONING_EFFORT = _normalize_reasoning_effort(action)
                        THINKING_ENABLED = DEFAULT_REASONING_EFFORT != "none"
                    elif action in {"on", "true", "1"}:
                        THINKING_ENABLED = True
                        if len(parts) > 2:
                            DEFAULT_REASONING_EFFORT = _normalize_reasoning_effort(parts[2])
                    elif action in {"off", "false", "0"}:
                        THINKING_ENABLED = False
                    else:
                        print("⚠️ 用法: /thinking on [medium|high|xhigh] 或 /thinking off")
                        continue
                    print(f"✅ 思考模式已更新: {_thinking_status_label()} | provider 将收到 thinking_enabled={THINKING_ENABLED}")
                    continue
                if cmd == "/save":
                    sid = SessionManager.save(chat_history, current_model, THINKING_ENABLED, DEFAULT_REASONING_EFFORT)
                    print(f"💾 会话已自动持续保存，无需 /save。当前文件: {sid}")
                    continue
                if cmd == "/clear":
                    chat_history.clear()
                    SessionManager.current_file = None
                    print("🧹 对话已清空。")
                    continue
                if cmd == "/resume":
                    files = SessionManager.list_sessions()
                    if not files:
                        print("📭 没有找到历史会话。")
                        continue
                    print("\n📂 请选择要恢复的会话:")
                    for i, f in enumerate(files):
                        mtime = datetime.fromtimestamp(f.stat().st_mtime).strftime('%Y-%m-%d %H:%M')
                        print(f"  [{i}] {f.name} ({mtime})")
                    
                    choice = (await _prompt_inline("👉 输入编号 (或回车取消): ")).strip()
                    if choice.isdigit() and int(choice) < len(files):
                        data = SessionManager.load(files[int(choice)])
                        chat_history = data["history"]
                        current_model = data.get("model", DEFAULT_MODEL)
                        DEFAULT_MODEL = current_model
                        THINKING_ENABLED = data.get("thinking_enabled", _model_has_thinking(current_model))
                        DEFAULT_REASONING_EFFORT = _normalize_reasoning_effort(data.get("reasoning_effort", DEFAULT_REASONING_EFFORT))
                        print(f"✅ 已恢复会话，模型自动设为 {current_model} ({_thinking_status_label()})")
                    continue

            # --- 🤖 Agent 运行拦截与执行 ---
            if len(user_input) > 500:
                print(f"📎 [已安全接收长文本粘贴: {len(user_input)} 字节]")

            chat_history.append({"role": "user", "content": user_input})
            if get_token_count(chat_history) > TOKEN_LIMIT:
                chat_history = await compress_history(chat_history)
            SessionManager.save(chat_history, current_model, THINKING_ENABLED, DEFAULT_REASONING_EFFORT)
            TURN_EXECUTION_RECORDS.clear()
            run_task = None
            use_direct_probe = _user_prompt_prefers_direct_probe(user_input)
            use_direct_codex = _user_prompt_prefers_direct_codex(user_input)
            if use_direct_probe:
                print("🤖 Agent (Probe): 正在运行 (按 Ctrl+C 可强行中止当前任务)...")
                run_task = asyncio.create_task(_run_direct_read_only_probe(user_input))
            elif use_direct_codex:
                print("🤖 Agent (Codex): 正在运行 (按 Ctrl+C 可强行中止当前任务)...")
                run_task = asyncio.create_task(
                    _run_checked_codex_tool(
                        user_input,
                        "CodexDirect",
                        CODEX_THREAD_OPTIONS,
                        require_local_evidence=_task_requires_local_evidence(user_input),
                    )
                )
            else:
                lead_agent = make_lead_agent(current_model, THINKING_ENABLED, DEFAULT_REASONING_EFFORT)
                print("🤖 Agent (Lead): 正在运行 (按 Ctrl+C 可强行中止当前任务)...")
                run_task = asyncio.create_task(
                    Runner.run(lead_agent, _build_lead_run_history(chat_history), run_config=RUN_CONFIG)
                )

            try:
                if use_direct_probe:
                    wait_label = "🤖 Agent (Probe): 等待模型/工具返回"
                elif use_direct_codex:
                    wait_label = "🤖 Agent (Codex): 等待模型/工具返回"
                else:
                    wait_label = "🤖 Agent (Lead): 等待模型/工具返回"
                result = await _await_with_status(run_task, wait_label)
                if use_direct_probe:
                    final_output = str(result)
                elif use_direct_codex:
                    final_output = result
                else:
                    final_output = _result_output_text(result)
                    usage_summary = _collect_result_usage(result)
                    if usage_summary["total"]:
                        _add_session_usage(usage_summary)
                        print(
                            "📊 Lead usage: "
                            f"{_format_usage_summary(usage_summary)} | "
                            f"session={_format_usage_summary(SESSION_USAGE_TOTALS)}"
                        )
                sys.stdout.write("\033[K")
                role_name = "Probe" if use_direct_probe else "Codex" if use_direct_codex else "Lead"
                print(f"🤖 Agent ({role_name}):\n{final_output}")
                chat_history.append({"role": "assistant", "content": _history_content_with_execution_record(final_output)})
                SessionManager.save(chat_history, current_model, THINKING_ENABLED, DEFAULT_REASONING_EFFORT)
            
            except asyncio.CancelledError:
                await _cancel_running_task(run_task)
                print("\n🛑 [任务取消] 当前 Agent 的思考与执行流程已被强行掐断。")
                cancel_text = "[Task forcefully cancelled by user via Ctrl+C]"
                chat_history.append({"role": "assistant", "content": _history_content_with_execution_record(cancel_text)})
                SessionManager.save(chat_history, current_model, THINKING_ENABLED, DEFAULT_REASONING_EFFORT)
            except Exception as e:
                print(f"\n❌ [执行出错]: {e}")
                error_text = f"[执行出错]: {e}"
                chat_history.append({"role": "assistant", "content": _history_content_with_execution_record(error_text)})
                SessionManager.save(chat_history, current_model, THINKING_ENABLED, DEFAULT_REASONING_EFFORT)

        except KeyboardInterrupt:
            if 'run_task' in locals() and not run_task.done():
                await _cancel_running_task(run_task)
                print("\n🛑 [已取消] 当前任务已收到 Ctrl+C，中断执行。")
            else:
                now = time.time()
                if now - last_ctrl_c_time < 2.0:
                    print("\n👋 彻底退出程序。")
                    break
                else:
                    print("\n💡 [提示] 再次按下 Ctrl+C 将退出整个程序 (或输入 /exit)。")
                    last_ctrl_c_time = now
        except EOFError:
            break

if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    if "--demo-composition" in sys.argv:
        asyncio.run(run_composition_demo(_parse_demo_round_limit(), _parse_demo_only()))
    else:
        asyncio.run(interactive_cli())
