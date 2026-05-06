from __future__ import annotations

import atexit
import asyncio
import base64
import concurrent.futures
import html as html_lib
import hashlib
import inspect
import json
import logging
import os
import queue
import re
import shutil
import threading
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlencode, urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator
from starlette.responses import StreamingResponse
from jsonschema import ValidationError as JsonSchemaValidationError
from jsonschema import validate as jsonschema_validate

if "DEEPSEEK_WEB_DISABLE_PROXY" not in os.environ:
    _raw_provider_proxy = os.environ.get(
        "DEEPSEEK_PROVIDER_USE_SYSTEM_PROXY",
        os.environ.get("AGENT_QT_USE_SYSTEM_PROXY", "0"),
    )
    _provider_proxy_enabled = str(_raw_provider_proxy or "").strip().lower() not in {"0", "false", "off", "no", ""}
    os.environ["DEEPSEEK_WEB_DISABLE_PROXY"] = "0" if _provider_proxy_enabled else "1"

import deerflow.models.deepseek_web_bridge as deepseek_web_bridge_module
from deerflow.models.deepseek_web_bridge import DeepSeekWebBridge

logger = logging.getLogger(__name__)


def _looks_like_prompt_replay_text(text: str) -> bool:
    content = str(text or "").strip()
    if not content:
        return False
    lowered = content.lower()
    markers = (
        "you are acting as the backend llm for a local openai-compatible",
        "you are acting as the backend llm for a local bash-only coding runner",
        "continue the existing deerflow session already initialized in this chat",
        "continue the existing plain bash agent session already initialized in this chat",
        "return exactly one json object and nothing else",
        "plain bash agent 模式",
        "you are opencode, an interactive cli tool",
        "opencode request completed.",
    )
    if any(marker in lowered for marker in markers):
        return True
    hints = (
        "no tools are available for this request",
        "conversation:\n\n[user]",
        "new conversation events since the previous request",
        "runner 会执行你返回的 fenced bash 终端命令块",
        "available tools (openai tools schema)",
        "[system]\nrole:",
        "[assistant]\nrole:",
    )
    return sum(1 for hint in hints if hint in lowered) >= 2


def _sanitize_retry_preview_text(text: str) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    lowered = raw.lower()
    toxic_markers = (
        "you are opencode, an interactive cli tool",
        "opencode request completed.",
        "[system]",
        "[assistant]",
        "[user]",
        'role: "system"',
        'role: "assistant"',
        'role: "user"',
        "conversation:",
        "new conversation events since the previous request:",
    )
    if any(marker in lowered for marker in toxic_markers):
        return "（上一轮输出包含对话转储或系统回显，已省略具体内容。请重新执行上一条请求，并严格只输出合法 YAML。）"

    kept_lines: list[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            if kept_lines and kept_lines[-1]:
                kept_lines.append("")
            continue
        lowered_line = stripped.lower()
        if (
            stripped in {"[SYSTEM]", "[USER]", "[ASSISTANT]"}
            or lowered_line.startswith("role:")
            or lowered_line.startswith("content:")
            or "opencode request completed." in lowered_line
        ):
            continue
        kept_lines.append(stripped)
        if len("\n".join(kept_lines)) >= 600:
            break

    cleaned = "\n".join(kept_lines).strip()
    return cleaned[:600] if cleaned else "（上一轮输出预览已省略；请重新执行上一条请求，并严格只输出合法 YAML。）"


def _looks_like_web_busy_text(text: str) -> bool:
    raw_content = str(text or "").strip()
    content = raw_content.lower()
    if not content:
        return False
    if len(raw_content) > 100:
        return False
    markers = (
        "有消息正在生成，请稍后再试",
        "message is being generated",
        "response is being generated",
        "please try again later",
    )
    return any(marker in content for marker in markers)


def raise_if_web_busy_payload(payload: dict[str, Any], request_id: str, route: str) -> None:
    text = "\n".join(
        str(payload.get(key) or "")
        for key in ("content", "raw_text")
        if isinstance(payload.get(key), str)
    )
    if _looks_like_web_busy_text(text):
        logger.warning("provider[%s] %s DeepSeek web session busy; returning HTTP 429.", request_id, route)
        raise HTTPException(
            status_code=429,
            detail={
                "error": {
                    "type": "rate_limit_error",
                    "code": "web_session_busy",
                    "message": "DeepSeek Web 当前会话仍有消息正在生成，请稍后再试。",
                }
            },
        )


DEFAULT_URL = os.environ.get("DEEPSEEK_WEB_URL", "https://chat.deepseek.com/")
DEFAULT_HEADLESS = os.environ.get("DEEPSEEK_WEB_HEADLESS", "0") == "1"
DEFAULT_FORCE_NEW_CHAT = os.environ.get("DEEPSEEK_WEB_FORCE_NEW_CHAT", "0") == "1"
DEFAULT_BROWSER_CHANNEL = os.environ.get("DEEPSEEK_WEB_BROWSER_CHANNEL", "").strip() or None
DEFAULT_MODEL_ID = os.environ.get("DEEPSEEK_LOCAL_MODEL", "DeepSeekV4")
INTERFACE_MODE = os.environ.get("DEEPSEEK_LOCAL_INTERFACE_MODE", "both").strip().lower()


def _int_env(name: str, default: int, *, minimum: int = 1, maximum: int = 8) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning("Invalid integer env %s=%r; using %d", name, raw, default)
        return default
    return max(minimum, min(maximum, value))


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "off", "no", ""}


def _proxy_env_snapshot() -> dict[str, str]:
    return {
        key: value
        for key in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY")
        if (value := os.environ.get(key))
    }


PROVIDER_PROXY_ENV_KEYS = ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY")
PROVIDER_USE_SYSTEM_PROXY = _bool_env(
    "DEEPSEEK_PROVIDER_USE_SYSTEM_PROXY",
    _bool_env("AGENT_QT_USE_SYSTEM_PROXY", False),
)
if _bool_env("DEEPSEEK_WEB_DISABLE_PROXY", False):
    PROVIDER_USE_SYSTEM_PROXY = False
if not PROVIDER_USE_SYSTEM_PROXY:
    for _proxy_key in PROVIDER_PROXY_ENV_KEYS:
        os.environ.pop(_proxy_key, None)
os.environ.setdefault("NO_PROXY", "localhost,127.0.0.1,::1")
os.environ.setdefault("no_proxy", os.environ["NO_PROXY"])


def _selector_tuple_env(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = os.environ.get(name)
    if raw is None:
        return default
    selectors = tuple(selector.strip() for selector in raw.split("||") if selector.strip())
    return selectors or default


def _label_selector_tuple(label: str) -> tuple[str, ...]:
    return (
        f'text="{label}"',
        f'div:has-text("{label}")',
        f'span:has-text("{label}")',
        f'button:has-text("{label}")',
    )


DEEPSEEK_WEB_POOL_SIZE = _int_env("DEEPSEEK_WEB_POOL_SIZE", 1, minimum=1, maximum=6)
DEEPSEEK_WEB_POOL_PROFILE_ROOT = os.environ.get(
    "DEEPSEEK_WEB_POOL_PROFILE_ROOT",
    "~/.deerflow/deepseek-web-profile-pool",
)
DEEPSEEK_WEB_POOL_QUEUE_LIMIT = _int_env(
    "DEEPSEEK_WEB_POOL_QUEUE_LIMIT",
    max(4, DEEPSEEK_WEB_POOL_SIZE * 2),
    minimum=0,
    maximum=64,
)
DEEPSEEK_WEB_POOL_ACQUIRE_TIMEOUT_S = _int_env(
    "DEEPSEEK_WEB_POOL_ACQUIRE_TIMEOUT_S",
    600,
    minimum=1,
    maximum=3600,
)
DEEPSEEK_WEB_PREWARM_POOL = os.environ.get("DEEPSEEK_WEB_PREWARM_POOL", "1").strip().lower() not in {
    "0",
    "false",
    "off",
    "no",
    "",
}
DEEPSEEK_WEB_PREWARM_SLOTS = _int_env(
    "DEEPSEEK_WEB_PREWARM_SLOTS",
    1,
    minimum=0,
    maximum=6,
)
DEEPSEEK_WEB_PROTOCOL_RETRIES = _int_env(
    "DEEPSEEK_WEB_PROTOCOL_RETRIES",
    1,
    minimum=0,
    maximum=3,
)
PROVIDER_WEB_SEARCH_MAX_RESULTS = _int_env(
    "DEEPSEEK_PROVIDER_WEB_SEARCH_MAX_RESULTS",
    10,
    minimum=1,
    maximum=25,
)
PROVIDER_WEB_SEARCH_MAX_STEPS = _int_env(
    "DEEPSEEK_PROVIDER_WEB_SEARCH_MAX_STEPS",
    3,
    minimum=1,
    maximum=6,
)
PROVIDER_WEB_SEARCH_ATTEMPTS = _int_env(
    "DEEPSEEK_PROVIDER_WEB_SEARCH_ATTEMPTS",
    3,
    minimum=1,
    maximum=10,
)
PROVIDER_WEB_SEARCH_RETRY_DELAY_S = _int_env(
    "DEEPSEEK_PROVIDER_WEB_SEARCH_RETRY_DELAY_S",
    1,
    minimum=0,
    maximum=60,
)
PROVIDER_WEB_SEARCH_DEFAULT_BACKENDS = (
    "so360,sogou,sm,duckduckgo,bing,google,brave"
    if PROVIDER_USE_SYSTEM_PROXY
    else "so360,sogou,sm,duckduckgo"
)
PROVIDER_WEB_SEARCH_BACKENDS = tuple(
    backend.strip()
    for backend in os.environ.get(
        "DEEPSEEK_PROVIDER_WEB_SEARCH_BACKENDS",
        PROVIDER_WEB_SEARCH_DEFAULT_BACKENDS,
    ).split(",")
    if backend.strip()
) or ("auto",)
PROVIDER_WEB_SEARCH_DIRECT_TIMEOUT_S = _int_env(
    "DEEPSEEK_PROVIDER_WEB_SEARCH_DIRECT_TIMEOUT_S",
    8,
    minimum=2,
    maximum=60,
)
PROVIDER_WEB_SEARCH_BROWSER_ENABLED = os.environ.get("DEEPSEEK_PROVIDER_WEB_SEARCH_BROWSER_ENABLED", "0").strip().lower() not in {
    "0",
    "false",
    "off",
    "no",
    "",
}
PROVIDER_WEB_SEARCH_BROWSER_ENGINE = os.environ.get("DEEPSEEK_PROVIDER_WEB_SEARCH_BROWSER_ENGINE", "baidu").strip().lower() or "baidu"
PROVIDER_WEB_SEARCH_BROWSER_TIMEOUT_S = _int_env(
    "DEEPSEEK_PROVIDER_WEB_SEARCH_BROWSER_TIMEOUT_S",
    45,
    minimum=5,
    maximum=120,
)
PROVIDER_WEB_SEARCH_BROWSER_GRACE_S = _int_env(
    "DEEPSEEK_PROVIDER_WEB_SEARCH_BROWSER_GRACE_S",
    15,
    minimum=0,
    maximum=30,
)
PROVIDER_WEB_SEARCH_EAGER = os.environ.get("DEEPSEEK_PROVIDER_WEB_SEARCH_EAGER", "1").strip().lower() not in {
    "0",
    "false",
    "off",
    "no",
    "",
}
PROVIDER_WEB_FETCH_MAX_BYTES = _int_env(
    "DEEPSEEK_PROVIDER_WEB_FETCH_MAX_BYTES",
    800_000,
    minimum=50_000,
    maximum=5_000_000,
)
DEFAULT_RESPONSES_STORE = os.environ.get("DEEPSEEK_LOCAL_RESPONSES_STORE", "1").strip().lower() not in {
    "0",
    "false",
    "off",
    "no",
    "",
}
DEFAULT_EXPERT_MODE_ENABLED = os.environ.get("DEEPSEEK_LOCAL_EXPERT_MODE", "1").strip().lower() not in {
    "0",
    "false",
    "off",
    "no",
    "",
}


@dataclass(frozen=True)
class ModelSpec:
    model_id: str
    profile_dir: str
    url: str = DEFAULT_URL
    headless: bool = DEFAULT_HEADLESS
    force_new_chat: bool = DEFAULT_FORCE_NEW_CHAT
    sticky_marker: str | None = None
    sticky_reanchor_messages: int | None = 24
    session_state_path: str | None = None
    browser_channel: str | None = DEFAULT_BROWSER_CHANNEL
    reuse_persisted_chat: bool = False
    forced_thinking_enabled: bool | None = None
    forced_expert_mode_enabled: bool | None = None
    input_selectors: tuple[str, ...] | None = None
    send_selectors: tuple[str, ...] | None = None
    new_chat_selectors: tuple[str, ...] | None = None
    assistant_selectors: tuple[str, ...] | None = None
    preferred_model_label: str | None = None
    model_menu_selectors: tuple[str, ...] | None = None
    model_option_selectors: tuple[str, ...] | None = None
    page_load_timeout_ms: int | None = None
    response_timeout_ms: int | None = None
    stable_poll_interval_ms: int | None = None
    stable_rounds: int | None = None
    copy_probe_max_ms: int | None = None
    copy_candidate_max_distance: int | None = None
    fast_new_chat: bool = False


DEERFLOW_PROFILE_DIR = os.environ.get("DEEPSEEK_WEB_PROFILE_DEERFLOW", "~/.deerflow/profile-deerflow")
DEERFLOW_SESSION_STATE_PATH = os.environ.get(
    "DEEPSEEK_WEB_SESSION_STATE_DEERFLOW",
    "~/.deerflow/deepseek-web-deerflow-session.json",
)
DEERFLOW_FORCE_NEW_CHAT = os.environ.get("DEEPSEEK_WEB_FORCE_NEW_CHAT_DEERFLOW", "1") == "1"
DEERFLOW_FAST_NEW_CHAT = os.environ.get("DEEPSEEK_WEB_FAST_NEW_CHAT_DEERFLOW", "0").strip().lower() not in {
    "0",
    "false",
    "off",
    "no",
    "",
}
DEERFLOW_STABLE_POLL_INTERVAL_MS = _int_env(
    "DEEPSEEK_WEB_STABLE_POLL_INTERVAL_MS_DEERFLOW",
    1200,
    minimum=100,
    maximum=3_000,
)
DEERFLOW_STICKY_MARKER = os.environ.get("DEEPSEEK_WEB_STICKY_MARKER_DEERFLOW", "flowflow__system_prompt_v2")
DEERFLOW_STICKY_REANCHOR_MESSAGES = int(os.environ.get("DEEPSEEK_WEB_STICKY_REANCHOR_MESSAGES_DEERFLOW", "24"))

XIAOMI_MIMO_URL = os.environ.get("XIAOMI_MIMO_WEB_URL", "https://aistudio.xiaomimimo.com/#/c")
XIAOMI_MIMO_HEADLESS = os.environ.get(
    "XIAOMI_MIMO_WEB_HEADLESS",
    os.environ.get("DEEPSEEK_WEB_HEADLESS", "1"),
) == "1"
XIAOMI_MIMO_PROFILE_DIR = os.environ.get("XIAOMI_MIMO_WEB_PROFILE", "~/.deerflow/profile-xiaomi-mimo")
XIAOMI_MIMO_SESSION_STATE_PATH = os.environ.get(
    "XIAOMI_MIMO_WEB_SESSION_STATE",
    "~/.deerflow/xiaomi-mimo-session.json",
)
XIAOMI_MIMO_BROWSER_CHANNEL = (
    os.environ.get("XIAOMI_MIMO_WEB_BROWSER_CHANNEL", "").strip() or DEFAULT_BROWSER_CHANNEL
)
XIAOMI_MIMO_FORCE_NEW_CHAT = os.environ.get("XIAOMI_MIMO_FORCE_NEW_CHAT", "1") == "1"
XIAOMI_MIMO_STICKY_MARKER = os.environ.get("XIAOMI_MIMO_STICKY_MARKER", "mimo__system_prompt_v2")
XIAOMI_MIMO_STICKY_REANCHOR_MESSAGES = int(os.environ.get("XIAOMI_MIMO_STICKY_REANCHOR_MESSAGES", "24"))
XIAOMI_MIMO_MODEL_LABEL = os.environ.get("XIAOMI_MIMO_WEB_MODEL_LABEL", "MiMo-V2.5-Pro")
XIAOMI_MIMO_BASE_MODEL_LABEL = os.environ.get("XIAOMI_MIMO_WEB_MODEL_LABEL_BASE", "MiMo-V2.5")
XIAOMI_MIMO_RESPONSE_TIMEOUT_MS = _int_env(
    "XIAOMI_MIMO_RESPONSE_TIMEOUT_MS",
    90_000,
    minimum=10_000,
    maximum=300_000,
)
XIAOMI_MIMO_STABLE_POLL_INTERVAL_MS = _int_env(
    "XIAOMI_MIMO_STABLE_POLL_INTERVAL_MS",
    800,
    minimum=100,
    maximum=3_000,
)
XIAOMI_MIMO_STABLE_ROUNDS = _int_env(
    "XIAOMI_MIMO_STABLE_ROUNDS",
    2,
    minimum=1,
    maximum=8,
)
XIAOMI_MIMO_COPY_PROBE_MAX_MS = _int_env(
    "XIAOMI_MIMO_COPY_PROBE_MAX_MS",
    350,
    minimum=0,
    maximum=3_000,
)
XIAOMI_MIMO_COPY_CANDIDATE_MAX_DISTANCE = _int_env(
    "XIAOMI_MIMO_COPY_CANDIDATE_MAX_DISTANCE",
    180,
    minimum=40,
    maximum=1_000,
)
XIAOMI_MIMO_FAST_NEW_CHAT = os.environ.get("XIAOMI_MIMO_FAST_NEW_CHAT", "0").strip().lower() not in {
    "0",
    "false",
    "off",
    "no",
    "",
}
XIAOMI_MIMO_INPUT_SELECTORS = _selector_tuple_env(
    "XIAOMI_MIMO_INPUT_SELECTORS",
    (
        'textarea[placeholder*="Sign in to continue chatting"]',
        'textarea[placeholder*="Message"]',
        'textarea[placeholder*="Ask"]',
        'textarea[placeholder*="发送"]',
        'textarea[placeholder*="输入"]',
        "textarea",
        '[contenteditable="true"]',
    ),
)
XIAOMI_MIMO_SEND_SELECTORS = _selector_tuple_env(
    "XIAOMI_MIMO_SEND_SELECTORS",
    (
        "button.rounded-full.h-7.w-7:not([disabled])",
        'button[aria-label*="Send" i]:not([disabled])',
        'button[aria-label*="发送"]:not([disabled])',
    ),
)
XIAOMI_MIMO_NEW_CHAT_SELECTORS = _selector_tuple_env(
    "XIAOMI_MIMO_NEW_CHAT_SELECTORS",
    (
        'button[aria-label="New conversation"]',
        'button:has-text("New conversation")',
        'button:has-text("新建对话")',
        'button:has-text("新对话")',
    ),
)
XIAOMI_MIMO_ASSISTANT_SELECTORS = _selector_tuple_env(
    "XIAOMI_MIMO_ASSISTANT_SELECTORS",
    (
        "#message-list .markdown-prose",
        '#message-list [class*="Markdown_markdown"]',
        '[data-message-author-role="assistant"]',
        '[data-role="assistant"]',
    ),
)
XIAOMI_MIMO_MODEL_MENU_SELECTORS = _selector_tuple_env(
    "XIAOMI_MIMO_MODEL_MENU_SELECTORS",
    (
        'nav div[class*="cursor-pointer"]:has-text("MiMo-V2.5")',
        'nav div[class*="cursor-pointer"]:has-text("MiMo-V2")',
        'div[class*="cursor-pointer"]:has-text("MiMo-V2.5")',
        'div[class*="cursor-pointer"]:has-text("MiMo-V2")',
        "text=/MiMo-V2(?:\\.5)?(?:-(Flash|Pro|Omni|TTS))?/",
    ),
)
XIAOMI_MIMO_MODEL_OPTION_SELECTORS = _selector_tuple_env(
    "XIAOMI_MIMO_MODEL_OPTION_SELECTORS",
    _label_selector_tuple(XIAOMI_MIMO_MODEL_LABEL),
)
XIAOMI_MIMO_BASE_MODEL_OPTION_SELECTORS = _selector_tuple_env(
    "XIAOMI_MIMO_MODEL_OPTION_SELECTORS_BASE",
    _label_selector_tuple(XIAOMI_MIMO_BASE_MODEL_LABEL),
)

MODEL_SPECS: dict[str, ModelSpec] = {
    "deepseek-web-deerflow": ModelSpec(
        model_id="deepseek-web-deerflow",
        profile_dir=DEERFLOW_PROFILE_DIR,
        force_new_chat=DEERFLOW_FORCE_NEW_CHAT,
        sticky_marker=DEERFLOW_STICKY_MARKER,
        sticky_reanchor_messages=DEERFLOW_STICKY_REANCHOR_MESSAGES,
        session_state_path=DEERFLOW_SESSION_STATE_PATH,
        stable_poll_interval_ms=DEERFLOW_STABLE_POLL_INTERVAL_MS,
        fast_new_chat=DEERFLOW_FAST_NEW_CHAT,
        forced_expert_mode_enabled=DEFAULT_EXPERT_MODE_ENABLED,
    ),
    "deepseek-web-deerflow-sticky": ModelSpec(
        model_id="deepseek-web-deerflow-sticky",
        profile_dir=DEERFLOW_PROFILE_DIR,
        force_new_chat=False,
        sticky_marker=DEERFLOW_STICKY_MARKER,
        sticky_reanchor_messages=DEERFLOW_STICKY_REANCHOR_MESSAGES,
        session_state_path=DEERFLOW_SESSION_STATE_PATH,
        reuse_persisted_chat=True,
        stable_poll_interval_ms=DEERFLOW_STABLE_POLL_INTERVAL_MS,
        forced_expert_mode_enabled=DEFAULT_EXPERT_MODE_ENABLED,
    ),
    "DeepSeekV4": ModelSpec(
        model_id="DeepSeekV4",
        profile_dir=DEERFLOW_PROFILE_DIR,
        force_new_chat=DEERFLOW_FORCE_NEW_CHAT,
        sticky_marker=DEERFLOW_STICKY_MARKER,
        sticky_reanchor_messages=DEERFLOW_STICKY_REANCHOR_MESSAGES,
        session_state_path=DEERFLOW_SESSION_STATE_PATH,
        reuse_persisted_chat=False,
        forced_thinking_enabled=False,
        forced_expert_mode_enabled=DEFAULT_EXPERT_MODE_ENABLED,
        stable_poll_interval_ms=DEERFLOW_STABLE_POLL_INTERVAL_MS,
        fast_new_chat=DEERFLOW_FAST_NEW_CHAT,
    ),
    "DeepSeekV4-simple": ModelSpec(
        model_id="DeepSeekV4-simple",
        profile_dir=DEERFLOW_PROFILE_DIR,
        force_new_chat=DEERFLOW_FORCE_NEW_CHAT,
        sticky_marker=DEERFLOW_STICKY_MARKER,
        sticky_reanchor_messages=DEERFLOW_STICKY_REANCHOR_MESSAGES,
        session_state_path=DEERFLOW_SESSION_STATE_PATH,
        reuse_persisted_chat=False,
        forced_thinking_enabled=False,
        forced_expert_mode_enabled=False,
        stable_poll_interval_ms=DEERFLOW_STABLE_POLL_INTERVAL_MS,
        fast_new_chat=DEERFLOW_FAST_NEW_CHAT,
    ),
    "DeepSeekV4-thinking": ModelSpec(
        model_id="DeepSeekV4-thinking",
        profile_dir=DEERFLOW_PROFILE_DIR,
        force_new_chat=DEERFLOW_FORCE_NEW_CHAT,
        sticky_marker=DEERFLOW_STICKY_MARKER,
        sticky_reanchor_messages=DEERFLOW_STICKY_REANCHOR_MESSAGES,
        session_state_path=DEERFLOW_SESSION_STATE_PATH,
        reuse_persisted_chat=False,
        forced_thinking_enabled=True,
        forced_expert_mode_enabled=DEFAULT_EXPERT_MODE_ENABLED,
        stable_poll_interval_ms=DEERFLOW_STABLE_POLL_INTERVAL_MS,
        fast_new_chat=DEERFLOW_FAST_NEW_CHAT,
    ),
    "DeepSeekV4-simple-thinking": ModelSpec(
        model_id="DeepSeekV4-simple-thinking",
        profile_dir=DEERFLOW_PROFILE_DIR,
        force_new_chat=DEERFLOW_FORCE_NEW_CHAT,
        sticky_marker=DEERFLOW_STICKY_MARKER,
        sticky_reanchor_messages=DEERFLOW_STICKY_REANCHOR_MESSAGES,
        session_state_path=DEERFLOW_SESSION_STATE_PATH,
        reuse_persisted_chat=False,
        forced_thinking_enabled=True,
        forced_expert_mode_enabled=False,
        stable_poll_interval_ms=DEERFLOW_STABLE_POLL_INTERVAL_MS,
        fast_new_chat=DEERFLOW_FAST_NEW_CHAT,
    ),
    "xiaomi-mimo-v2.5-pro": ModelSpec(
        model_id="xiaomi-mimo-v2.5-pro",
        profile_dir=XIAOMI_MIMO_PROFILE_DIR,
        url=XIAOMI_MIMO_URL,
        headless=XIAOMI_MIMO_HEADLESS,
        browser_channel=XIAOMI_MIMO_BROWSER_CHANNEL,
        force_new_chat=XIAOMI_MIMO_FORCE_NEW_CHAT,
        sticky_marker=XIAOMI_MIMO_STICKY_MARKER,
        sticky_reanchor_messages=XIAOMI_MIMO_STICKY_REANCHOR_MESSAGES,
        session_state_path=XIAOMI_MIMO_SESSION_STATE_PATH,
        reuse_persisted_chat=False,
        input_selectors=XIAOMI_MIMO_INPUT_SELECTORS,
        send_selectors=XIAOMI_MIMO_SEND_SELECTORS,
        new_chat_selectors=XIAOMI_MIMO_NEW_CHAT_SELECTORS,
        assistant_selectors=XIAOMI_MIMO_ASSISTANT_SELECTORS,
        preferred_model_label=XIAOMI_MIMO_MODEL_LABEL,
        model_menu_selectors=XIAOMI_MIMO_MODEL_MENU_SELECTORS,
        model_option_selectors=XIAOMI_MIMO_MODEL_OPTION_SELECTORS,
        response_timeout_ms=XIAOMI_MIMO_RESPONSE_TIMEOUT_MS,
        stable_poll_interval_ms=XIAOMI_MIMO_STABLE_POLL_INTERVAL_MS,
        stable_rounds=XIAOMI_MIMO_STABLE_ROUNDS,
        copy_probe_max_ms=XIAOMI_MIMO_COPY_PROBE_MAX_MS,
        copy_candidate_max_distance=XIAOMI_MIMO_COPY_CANDIDATE_MAX_DISTANCE,
        fast_new_chat=XIAOMI_MIMO_FAST_NEW_CHAT,
    ),
    "xiaomi-mimo-v2.5": ModelSpec(
        model_id="xiaomi-mimo-v2.5",
        profile_dir=XIAOMI_MIMO_PROFILE_DIR,
        url=XIAOMI_MIMO_URL,
        headless=XIAOMI_MIMO_HEADLESS,
        browser_channel=XIAOMI_MIMO_BROWSER_CHANNEL,
        force_new_chat=XIAOMI_MIMO_FORCE_NEW_CHAT,
        sticky_marker=XIAOMI_MIMO_STICKY_MARKER,
        sticky_reanchor_messages=XIAOMI_MIMO_STICKY_REANCHOR_MESSAGES,
        session_state_path=XIAOMI_MIMO_SESSION_STATE_PATH,
        reuse_persisted_chat=False,
        input_selectors=XIAOMI_MIMO_INPUT_SELECTORS,
        send_selectors=XIAOMI_MIMO_SEND_SELECTORS,
        new_chat_selectors=XIAOMI_MIMO_NEW_CHAT_SELECTORS,
        assistant_selectors=XIAOMI_MIMO_ASSISTANT_SELECTORS,
        preferred_model_label=XIAOMI_MIMO_BASE_MODEL_LABEL,
        model_menu_selectors=XIAOMI_MIMO_MODEL_MENU_SELECTORS,
        model_option_selectors=XIAOMI_MIMO_BASE_MODEL_OPTION_SELECTORS,
        response_timeout_ms=XIAOMI_MIMO_RESPONSE_TIMEOUT_MS,
        stable_poll_interval_ms=XIAOMI_MIMO_STABLE_POLL_INTERVAL_MS,
        stable_rounds=XIAOMI_MIMO_STABLE_ROUNDS,
        copy_probe_max_ms=XIAOMI_MIMO_COPY_PROBE_MAX_MS,
        copy_candidate_max_distance=XIAOMI_MIMO_COPY_CANDIDATE_MAX_DISTANCE,
        fast_new_chat=XIAOMI_MIMO_FAST_NEW_CHAT,
    ),
}

# Optional legacy alias for older configs.
MODEL_ALIASES = {
    "deepseek-web": "DeepSeekV4",
    "DeepSeek V4": "DeepSeekV4",
    "DeepSeek V4 Simple": "DeepSeekV4-simple",
    "DeepSeekV4-simple": "DeepSeekV4-simple",
    "DeepSeek V4-thinking": "DeepSeekV4-thinking",
    "DeepSeekV3": "DeepSeekV4",
    "DeepSeekV3-thinking": "DeepSeekV4-thinking",
    "mimo": "xiaomi-mimo-v2.5-pro",
    "mimo-pro": "xiaomi-mimo-v2.5-pro",
    "mimo-2.5-pro": "xiaomi-mimo-v2.5-pro",
    "mimo-v2.5-pro": "xiaomi-mimo-v2.5-pro",
    "mimo2.5pro": "xiaomi-mimo-v2.5-pro",
    "mimo-v2-pro": "xiaomi-mimo-v2.5-pro",
    "mimo-2.5": "xiaomi-mimo-v2.5",
    "mimo-v2.5": "xiaomi-mimo-v2.5",
    "mimo2.5": "xiaomi-mimo-v2.5",
    "MiMo-V2.5-Pro": "xiaomi-mimo-v2.5-pro",
    "MIMO V2.5 PRO": "xiaomi-mimo-v2.5-pro",
    "MiMo-V2.5": "xiaomi-mimo-v2.5",
    "MIMO V2.5": "xiaomi-mimo-v2.5",
    "MiMo-V2-Pro": "xiaomi-mimo-v2.5-pro",
    "MIMO V2 PRO": "xiaomi-mimo-v2.5-pro",
    "xiaomi": "xiaomi-mimo-v2.5-pro",
    "xiaomi-mimo": "xiaomi-mimo-v2.5-pro",
    "xiaomi-mimo-v2-pro": "xiaomi-mimo-v2.5-pro",
    "xiaomi-mimo-v2.5-pro": "xiaomi-mimo-v2.5-pro",
    "xiaomi-mimo-v2.5": "xiaomi-mimo-v2.5",
    "Xiaomi MiMo-V2.5-Pro": "xiaomi-mimo-v2.5-pro",
    "Xiaomi MiMo-V2.5": "xiaomi-mimo-v2.5",
    "Xiaomi MiMo-V2-Pro": "xiaomi-mimo-v2.5-pro",
}


def is_interface_enabled(name: str) -> bool:
    mode = INTERFACE_MODE or "both"
    if mode not in {"openai", "anthropic", "both"}:
        mode = "both"
    return mode == "both" or mode == name

_bridge_pools: dict[str, "BridgePool"] = {}
_SESSION_KEY_SANITIZE_RE = re.compile(r"[^A-Za-z0-9._-]+")
TOOL_NAME_ALIASES: dict[str, str] = {
    "ls": "ls",
    "listdir": "ls",
    "list_dir": "ls",
    "list-dir": "ls",
    "cat": "read_file",
    "readfile": "read_file",
    "read_file": "read_file",
    "read-file": "read_file",
    "writefile": "write_file",
    "write_file": "write_file",
    "write-file": "write_file",
    "shell": "Bash",
    "bash": "Bash",
}
WINDOWS_COMPAT_ENV = "DEEPSEEK_LOCAL_WINDOWS_COMPAT"
FORCE_WINDOWS_PATH_ENV = "DEEPSEEK_LOCAL_FORCE_WINDOWS_PATHS"
WINDOWS_PATH_ARG_KEYS = {"path", "file_path", "cwd", "workdir"}


def summarize_tool_calls(tool_calls: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    summary: list[dict[str, Any]] = []
    for index, tool_call in enumerate(tool_calls or []):
        arguments = tool_call.get("arguments", {})
        if isinstance(arguments, str):
            argument_chars = len(arguments)
        else:
            argument_chars = len(json.dumps(arguments, ensure_ascii=False))
        summary.append(
            {
                "index": index,
                "id": tool_call.get("id"),
                "name": tool_call.get("name"),
                "argument_chars": argument_chars,
            }
        )
    return summary


def get_model_spec(model_name: str) -> ModelSpec:
    normalized_name = model_name.strip()
    resolved_name = MODEL_ALIASES.get(normalized_name)
    if resolved_name is None:
        resolved_name = MODEL_ALIASES.get(normalized_name.lower(), normalized_name)
    spec = MODEL_SPECS.get(resolved_name)
    if spec is None:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown model '{model_name}'. Available models: {', '.join(MODEL_SPECS)}",
        )
    return spec


def _normalize_session_key(value: str | None) -> str | None:
    if not value:
        return None
    raw = value.strip()
    if not raw:
        return None

    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    normalized = _SESSION_KEY_SANITIZE_RE.sub("-", raw).strip("-.")
    if normalized:
        normalized = normalized[:48].rstrip("-.")
        return f"{normalized}-{digest}"
    return f"session-{digest}"


def _sessionize_state_path(base_path: str | None, session_key: str | None) -> str | None:
    if not base_path or not session_key:
        return base_path
    path = Path(base_path).expanduser()
    suffix = path.suffix or ".json"
    stem = path.stem if path.suffix else path.name
    sessionized = path.with_name(f"{stem}--{session_key}{suffix}")
    return str(sessionized)


def resolve_request_spec(model_name: str, request_user: str | None = None) -> ModelSpec:
    spec = get_model_spec(model_name)
    if not spec.reuse_persisted_chat:
        return spec

    session_key = _normalize_session_key(request_user)
    if session_key is None:
        # Without a per-thread key, sticky mode can leak prior webpage context.
        return replace(
            spec,
            force_new_chat=True,
            reuse_persisted_chat=False,
            sticky_marker=None,
            session_state_path=None,
        )

    sticky_marker = f"{spec.sticky_marker}::{session_key}" if spec.sticky_marker else session_key
    session_state_path = _sessionize_state_path(spec.session_state_path, session_key)
    return replace(
        spec,
        sticky_marker=sticky_marker,
        session_state_path=session_state_path,
    )


def _bridge_cache_key(spec: ModelSpec) -> str:
    if (
        spec.url == DEFAULT_URL
        and spec.profile_dir == DEERFLOW_PROFILE_DIR
        and spec.session_state_path == DEERFLOW_SESSION_STATE_PATH
    ):
        return "deepseek-web-deerflow-shared"
    return f"{spec.model_id}:{spec.url}:{spec.profile_dir}:{spec.session_state_path or ''}"


def _safe_pool_name(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-.")
    return normalized or "default"


def _pooled_profile_dir(base_profile_dir: str, *, cache_key: str, slot_index: int, pool_size: int) -> str:
    base_path = Path(base_profile_dir).expanduser()
    if pool_size <= 1 or slot_index == 0:
        return str(base_path)

    pool_root = Path(DEEPSEEK_WEB_POOL_PROFILE_ROOT).expanduser()
    slot_path = pool_root / _safe_pool_name(cache_key) / f"profile-{slot_index}"
    if slot_path.exists():
        return str(slot_path)

    slot_path.parent.mkdir(parents=True, exist_ok=True)
    if base_path.exists():
        logger.warning(
            "Creating DeepSeek web profile clone slot=%d source=%s target=%s",
            slot_index,
            base_path,
            slot_path,
        )
        shutil.copytree(
            base_path,
            slot_path,
            symlinks=True,
            ignore=shutil.ignore_patterns(
                "Singleton*",
                "LOCK",
                "lockfile",
                "Crashpad",
                "GPUCache",
                "GrShaderCache",
                "ShaderCache",
                "Code Cache",
                "Cache",
            ),
        )
    else:
        slot_path.mkdir(parents=True, exist_ok=True)
    return str(slot_path)


def _pooled_session_state_path(base_state_path: str | None, *, slot_index: int, pool_size: int) -> str | None:
    if not base_state_path or pool_size <= 1:
        return base_state_path
    path = Path(base_state_path).expanduser()
    suffix = path.suffix or ".json"
    stem = path.stem if path.suffix else path.name
    return str(path.with_name(f"{stem}--pool-{slot_index}{suffix}"))


def _effective_pool_size(base_spec: ModelSpec) -> int:
    if base_spec.reuse_persisted_chat:
        return 1
    return DEEPSEEK_WEB_POOL_SIZE


def _make_bridge(base_spec: ModelSpec, *, cache_key: str, slot_index: int, pool_size: int) -> DeepSeekWebBridge:
    bridge_kwargs: dict[str, Any] = {
        "url": base_spec.url,
        "user_data_dir": _pooled_profile_dir(
            base_spec.profile_dir,
            cache_key=cache_key,
            slot_index=slot_index,
            pool_size=pool_size,
        ),
        "headless": base_spec.headless,
        "force_new_chat": base_spec.force_new_chat,
        "sticky_marker": base_spec.sticky_marker,
        "sticky_reanchor_messages": base_spec.sticky_reanchor_messages,
        "session_state_path": _pooled_session_state_path(
            base_spec.session_state_path,
            slot_index=slot_index,
            pool_size=pool_size,
        ),
        "browser_channel": base_spec.browser_channel,
        "reuse_persisted_chat": base_spec.reuse_persisted_chat,
        "fast_new_chat": base_spec.fast_new_chat,
    }
    for attr_name in (
        "page_load_timeout_ms",
        "response_timeout_ms",
        "stable_poll_interval_ms",
        "stable_rounds",
        "copy_probe_max_ms",
        "copy_candidate_max_distance",
    ):
        attr_value = getattr(base_spec, attr_name)
        if attr_value is not None:
            bridge_kwargs[attr_name] = attr_value
    for attr_name in (
        "input_selectors",
        "send_selectors",
        "new_chat_selectors",
        "assistant_selectors",
        "preferred_model_label",
        "model_menu_selectors",
        "model_option_selectors",
    ):
        attr_value = getattr(base_spec, attr_name)
        if attr_value is not None:
            bridge_kwargs[attr_name] = attr_value
    return DeepSeekWebBridge(**bridge_kwargs)


class BridgePoolBusy(RuntimeError):
    pass


class BridgeSlot:
    def __init__(self, *, cache_key: str, base_spec: ModelSpec, slot_index: int, pool_size: int) -> None:
        self.index = slot_index
        self.pool_size = pool_size
        self.bridge = _make_bridge(
            base_spec,
            cache_key=cache_key,
            slot_index=slot_index,
            pool_size=pool_size,
        )
        self.executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix=f"deepseek-web-{_safe_pool_name(cache_key)}-{slot_index}",
        )
        self._prewarm_lock = threading.Lock()
        self._prewarm_future: concurrent.futures.Future | None = None

    def prewarm(self, spec: ModelSpec) -> None:
        with self._prewarm_lock:
            if self._prewarm_future is not None and not self._prewarm_future.done():
                return

            def operation() -> dict[str, Any]:
                started = time.perf_counter()
                try:
                    result = run_bridge_with_spec(
                        self.bridge,
                        spec=spec,
                        operation=lambda: self.bridge.prepare_for_next_request(
                            thinking_enabled=spec.forced_thinking_enabled,
                            expert_mode_enabled=spec.forced_expert_mode_enabled,
                        ),
                    )
                    logger.warning(
                        "DeepSeek bridge slot prewarmed slot=%d prepared=%s already=%s elapsed_ms=%d total_ms=%d url=%s",
                        self.index,
                        result.get("prepared"),
                        result.get("already_prepared"),
                        result.get("elapsed_ms"),
                        int((time.perf_counter() - started) * 1000),
                        result.get("url"),
                    )
                    return result
                except Exception:
                    logger.warning("DeepSeek bridge slot prewarm failed slot=%d", self.index, exc_info=True)
                    raise

            self._prewarm_future = self.executor.submit(lambda: _run_in_playwright_worker(operation))

    def close(self) -> None:
        try:
            future = self.executor.submit(lambda: _run_in_playwright_worker(self.bridge.close))
            future.result(timeout=30)
        except Exception:
            logger.debug("Failed to close DeepSeek bridge slot %d cleanly.", self.index, exc_info=True)
        self.executor.shutdown(wait=False, cancel_futures=True)

    def request_cancel_generation(self) -> bool:
        cancel = getattr(self.bridge, "request_cancel_generation", None)
        if not callable(cancel):
            return False
        cancel()
        return True


class BridgePool:
    def __init__(self, *, cache_key: str, base_spec: ModelSpec, size: int) -> None:
        self.cache_key = cache_key
        self.size = max(1, size)
        self.queue_limit = max(0, DEEPSEEK_WEB_POOL_QUEUE_LIMIT)
        self._all: list[BridgeSlot] = [
            BridgeSlot(cache_key=cache_key, base_spec=base_spec, slot_index=index, pool_size=self.size)
            for index in range(self.size)
        ]
        self._available: queue.LifoQueue[BridgeSlot] = queue.LifoQueue()
        self._admission = threading.BoundedSemaphore(self.size + self.queue_limit)
        for slot in reversed(self._all):
            self._available.put(slot)

    def prewarm_available(
        self,
        spec: ModelSpec,
        *,
        limit: int | None = None,
        exclude_slot_indexes: set[int] | None = None,
    ) -> None:
        if not DEEPSEEK_WEB_PREWARM_POOL:
            return
        limit = self.size if limit is None else max(0, min(limit, self.size))
        if limit <= 0:
            return
        excluded = set(exclude_slot_indexes or ())
        available_slots = list(getattr(self._available, "queue", []))
        scheduled = 0
        for slot in available_slots:
            if slot.index in excluded:
                continue
            slot.prewarm(_spec_for_bridge_slot(spec, slot))
            scheduled += 1
            if scheduled >= limit:
                break

    def acquire(self) -> BridgeSlot:
        admitted = self._admission.acquire(blocking=False)
        if not admitted:
            raise BridgePoolBusy(
                f"DeepSeek web bridge pool is busy: size={self.size} queue_limit={self.queue_limit}"
            )
        try:
            return self._available.get(timeout=DEEPSEEK_WEB_POOL_ACQUIRE_TIMEOUT_S)
        except queue.Empty as exc:
            self._admission.release()
            raise BridgePoolBusy(
                f"Timed out waiting for DeepSeek web bridge slot after {DEEPSEEK_WEB_POOL_ACQUIRE_TIMEOUT_S}s"
            ) from exc

    def release(self, slot: BridgeSlot) -> None:
        self._available.put(slot)
        self._admission.release()

    def first_bridge(self) -> DeepSeekWebBridge:
        return self._all[0].bridge

    def response_preview(self) -> dict[str, Any]:
        previews = [slot.bridge.response_preview() for slot in self._all]
        previews.sort(key=lambda item: float(item.get("updated_at") or 0), reverse=True)
        for preview in previews:
            if preview.get("active") or preview.get("text") or preview.get("error"):
                return preview
        return previews[0] if previews else {
            "active": False,
            "done": False,
            "error": "",
            "source": "",
            "text": "",
            "chars": 0,
            "updated_at": 0.0,
        }

    def request_cancel_generations(self) -> int:
        return sum(1 for slot in self._all if slot.request_cancel_generation())

    def close(self) -> None:
        for slot in self._all:
            slot.close()


def get_bridge_pool(model_name: str, request_user: str | None = None) -> tuple[ModelSpec, BridgePool]:
    base_spec = get_model_spec(model_name)
    spec = resolve_request_spec(model_name, request_user)
    cache_key = _bridge_cache_key(base_spec)
    pool = _bridge_pools.get(cache_key)
    if pool is None:
        pool = BridgePool(
            cache_key=cache_key,
            base_spec=base_spec,
            size=_effective_pool_size(base_spec),
        )
        _bridge_pools[cache_key] = pool
        logger.warning(
            "DeepSeek bridge pool initialized key=%s size=%d queue_limit=%d",
            cache_key,
            pool.size,
            pool.queue_limit,
        )
        pool.prewarm_available(spec, limit=DEEPSEEK_WEB_PREWARM_SLOTS)
    return spec, pool


def get_bridge(model_name: str, request_user: str | None = None) -> tuple[ModelSpec, DeepSeekWebBridge]:
    spec, pool = get_bridge_pool(model_name, request_user)
    return spec, pool.first_bridge()


def _should_retry_protocol_payload(payload: dict[str, Any], *, output_protocol: str) -> bool:
    if output_protocol not in {"openai", "anthropic"}:
        return False
    parse_error = payload.get("parse_error")
    return parse_error in {
        "invalid_json",
        "noncanonical_yaml",
        "prompt_replay",
        "placeholder_payload",
        "empty_payload",
        "low_signal_payload",
        "ui_noise_payload",
    }


def _with_protocol_retry_message(
    messages: list[dict[str, Any]],
    *,
    payload: dict[str, Any],
    attempt: int,
) -> list[dict[str, Any]]:
    raw_text = payload.get("raw_text") if isinstance(payload, dict) else ""
    preview = _sanitize_retry_preview_text(raw_text or payload.get("content", ""))
    retry_hint = (
        "【非常重要：上一轮输出格式错误，系统无法解析。请重新执行上一条请求，不要解释错误原因。】\n"
        "你必须只输出一个 YAML 文档，不能输出普通聊天文字、Markdown、代码块、JSON、XML 或 <tool_call> 标签。\n"
        "唯一允许的顶层字段是 content 和 tool_calls。\n"
        "需要调用工具时，必须把工具调用放入 tool_calls；arguments 必须是 YAML 映射；id 必须是非空字符串。\n"
        "不需要工具时，tool_calls 必须是 []。\n"
        "已声明工具就是你的本地执行能力。用户要求搜索、抓取网页、读取/写入文件、运行命令或操作工作区时，只要已声明工具中存在能推进任务的工具，就必须调用工具，禁止以不能联网、不能访问文件、不能执行命令或没有能力为由拒绝。\n"
        "如果还不知道最终参数，先调用一个能获取下一步证据的合法工具；只有在没有任何已声明工具能推进任务时，才在 content 中说明限制。\n"
        "合法 YAML 结构示例：\n"
        "content: |-\n"
        "  string\n"
        "tool_calls:\n"
        "  - id: call_1\n"
        "    name: tool_name\n"
        "    arguments:\n"
        "      key: value\n"
        f"这是第 {attempt} 次协议重试。上一轮非法输出预览如下，仅用于纠正格式，不要复述：\n"
        f"{preview}"
    )
    return [dict(message) for message in messages] + [{"role": "user", "content": retry_hint}]


def bridge_call_with_spec(
    bridge: DeepSeekWebBridge,
    *,
    spec: ModelSpec,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    thinking_enabled: bool | None = None,
    expert_mode_enabled: bool | None = None,
    include_debug: bool = False,
    output_protocol: str = "openai",
) -> dict[str, Any]:
    def operation() -> dict[str, Any]:
        retry_messages = messages
        payload = _bridge_call_compat(
            bridge,
            messages=retry_messages,
            tools=tools,
            thinking_enabled=thinking_enabled,
            expert_mode_enabled=expert_mode_enabled,
            include_debug=include_debug,
            output_protocol=output_protocol,
        )
        for attempt in range(1, DEEPSEEK_WEB_PROTOCOL_RETRIES + 1):
            if not _should_retry_protocol_payload(payload, output_protocol=output_protocol):
                return payload
            logger.warning(
                "provider bridge protocol retry attempt=%d parse_error=%s raw_preview=%r",
                attempt,
                payload.get("parse_error"),
                str(payload.get("raw_text") or payload.get("content") or "")[:300],
            )
            retry_messages = _with_protocol_retry_message(
                messages,
                payload=payload,
                attempt=attempt,
            )
            payload = _bridge_call_compat(
                bridge,
                messages=retry_messages,
                tools=tools,
                thinking_enabled=thinking_enabled,
                expert_mode_enabled=expert_mode_enabled,
                include_debug=include_debug,
                output_protocol=output_protocol,
            )
            payload["protocol_retry_count"] = attempt
        return payload

    return run_bridge_with_spec(
        bridge,
        spec=spec,
        operation=operation,
    )


def _bridge_call_compat(
    bridge: DeepSeekWebBridge,
    *,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    thinking_enabled: bool | None,
    expert_mode_enabled: bool | None,
    include_debug: bool,
    output_protocol: str,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "messages": messages,
        "tools": tools,
        "thinking_enabled": thinking_enabled,
        "include_debug": include_debug,
    }
    try:
        parameters = inspect.signature(bridge.call).parameters
    except (TypeError, ValueError):
        parameters = {}
    if not parameters or "expert_mode_enabled" in parameters:
        kwargs["expert_mode_enabled"] = expert_mode_enabled
    if not parameters or "output_protocol" in parameters:
        kwargs["output_protocol"] = output_protocol
    return bridge.call(**kwargs)


def _run_in_playwright_worker(operation):
    # Some callers can leave an event loop bound to the worker thread.
    # Playwright Sync API rejects that environment, so detach it explicitly.
    try:
        asyncio.set_event_loop(None)
    except Exception:
        pass
    return operation()


def _spec_for_bridge_slot(spec: ModelSpec, slot: BridgeSlot) -> ModelSpec:
    return replace(
        spec,
        profile_dir=slot.bridge.user_data_dir,
        session_state_path=_pooled_session_state_path(
            spec.session_state_path,
            slot_index=slot.index,
            pool_size=slot.pool_size,
        ),
    )


async def run_on_bridge_slot(
    pool: BridgePool,
    *,
    spec: ModelSpec,
    request_id: str,
    route: str,
    operation,
    prewarm_after: bool = True,
):
    started_at = time.perf_counter()
    try:
        slot = await asyncio.to_thread(pool.acquire)
    except BridgePoolBusy as exc:
        logger.warning("provider[%s] %s bridge pool busy: %s", request_id, route, exc)
        raise HTTPException(status_code=429, detail=str(exc)) from exc

    acquired_at = time.perf_counter()
    slot_spec = _spec_for_bridge_slot(spec, slot)
    logger.warning(
        "provider[%s] %s acquired bridge slot=%d pool=%s available=%d slot_acquire_ms=%d",
        request_id,
        route,
        slot.index,
        pool.cache_key,
        pool._available.qsize(),
        int((acquired_at - started_at) * 1000),
    )
    try:
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            slot.executor,
            lambda: _run_in_playwright_worker(lambda: operation(slot.bridge, slot_spec)),
        )
        finished_at = time.perf_counter()
        return result, {
            "slot_acquire_ms": int((acquired_at - started_at) * 1000),
            "bridge_exec_ms": int((finished_at - acquired_at) * 1000),
            "bridge_total_ms": int((finished_at - started_at) * 1000),
        }
    except Exception:
        logger.warning(
            "provider[%s] %s resetting failed bridge slot=%d pool=%s",
            request_id,
            route,
            slot.index,
            pool.cache_key,
            exc_info=True,
        )
        try:
            await loop.run_in_executor(
                slot.executor,
                lambda: _run_in_playwright_worker(slot.bridge.close),
            )
        except Exception:
            logger.debug(
                "provider[%s] %s failed to reset bridge slot=%d",
                request_id,
                route,
                slot.index,
                exc_info=True,
            )
        raise
    finally:
        released_at = time.perf_counter()
        pool.release(slot)
        if prewarm_after:
            pool.prewarm_available(
                spec,
                limit=DEEPSEEK_WEB_PREWARM_SLOTS,
                exclude_slot_indexes={slot.index},
            )
        logger.warning(
            "provider[%s] %s released bridge slot=%d pool=%s available=%d lifetime_ms=%d",
            request_id,
            route,
            slot.index,
            pool.cache_key,
            pool._available.qsize(),
            int((released_at - acquired_at) * 1000),
        )


def close_bridges() -> None:
    for pool in list(_bridge_pools.values()):
        pool.close()
    _bridge_pools.clear()


atexit.register(close_bridges)

app = FastAPI(title="DeepSeek Localhost Provider", version="0.2.0")


@app.on_event("startup")
async def prewarm_default_bridge_pool() -> None:
    if not DEEPSEEK_WEB_PREWARM_POOL or DEEPSEEK_WEB_PREWARM_SLOTS <= 0:
        return
    try:
        spec, pool = get_bridge_pool(DEFAULT_MODEL_ID)
        pool.prewarm_available(spec, limit=DEEPSEEK_WEB_PREWARM_SLOTS)
        logger.warning(
            "DeepSeek bridge pool startup prewarm scheduled model=%s slots=%d pool=%s",
            DEFAULT_MODEL_ID,
            min(DEEPSEEK_WEB_PREWARM_SLOTS, pool.size),
            pool.cache_key,
        )
    except Exception:
        logger.warning("DeepSeek bridge pool startup prewarm scheduling failed", exc_info=True)


@app.on_event("shutdown")
async def shutdown_bridge_pools() -> None:
    logger.warning("DeepSeek provider shutting down; closing bridge pools.")
    close_bridges()


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    role: str
    content: Any = ""
    tool_call_id: str | None = None
    tool_calls: list[dict[str, Any]] | None = None


class StreamOptions(BaseModel):
    model_config = ConfigDict(extra="ignore")

    include_usage: bool = False


class ChatCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    model: str = Field(default=DEFAULT_MODEL_ID)
    messages: list[ChatMessage]
    tools: list[dict[str, Any]] | None = None
    stream: bool = False
    user: str | None = None
    thinking_enabled: bool | None = None
    expert_mode_enabled: bool | None = None
    output_protocol: str | None = None
    extra_body: dict[str, Any] | None = None
    stream_options: StreamOptions | None = None

    @model_validator(mode="before")
    @classmethod
    def normalize_undefined_values(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data

        normalized = dict(data)
        undefined_like = {"[undefined]", "undefined", ""}
        for key, value in list(normalized.items()):
            if isinstance(value, str) and value in undefined_like:
                normalized[key] = None

        tools = normalized.get("tools")
        if isinstance(tools, str) and tools in undefined_like:
            normalized["tools"] = None

        stream_options = normalized.get("stream_options")
        if isinstance(stream_options, str) and stream_options in undefined_like:
            normalized["stream_options"] = None

        extra_body = normalized.get("extra_body")
        if isinstance(extra_body, str) and extra_body in undefined_like:
            normalized["extra_body"] = None

        return normalized


class ResponsesRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    model: str = Field(default=DEFAULT_MODEL_ID)
    input: Any = ""
    instructions: str | None = None
    include: list[str] | None = None
    tools: list[dict[str, Any]] | None = None
    stream: bool = False
    user: str | None = None
    thinking_enabled: bool | None = None
    expert_mode_enabled: bool | None = None
    reasoning: dict[str, Any] | None = None
    store: bool | None = None
    prompt_cache_retention: str | None = None


class DirectWebSearchRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    query: str
    max_results: int = PROVIDER_WEB_SEARCH_MAX_RESULTS
    allowed_domains: list[str] | None = None


class DirectWebFetchRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    url: str
    include_html: bool = False
    max_bytes: int = PROVIDER_WEB_FETCH_MAX_BYTES


class ProviderProxyModeRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    use_system_proxy: bool = False
    proxy_env: dict[str, str] | None = None


class DebugTraceRequest(ChatCompletionRequest):
    include_payload: bool = False


class ThinkingModeRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    model: str = Field(default=DEFAULT_MODEL_ID)
    user: str | None = None
    thinking_enabled: bool | None = None
    visible: bool = False


class ExpertModeRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    model: str = Field(default=DEFAULT_MODEL_ID)
    user: str | None = None
    expert_mode_enabled: bool | None = None
    visible: bool = False


class AnthropicMessageRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    model: str = Field(default=DEFAULT_MODEL_ID)
    messages: Any = Field(default_factory=list)
    system: Any = None
    tools: Any = None
    stream: bool = False
    user: str | None = None
    thinking_enabled: bool | None = None
    expert_mode_enabled: bool | None = None
    extra_body: dict[str, Any] | None = None

    @model_validator(mode="before")
    @classmethod
    def normalize_undefined_values(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        normalized = dict(data)
        undefined_like = {"[undefined]", "undefined", ""}
        for key, value in list(normalized.items()):
            if isinstance(value, str) and value in undefined_like:
                normalized[key] = None
        messages = normalized.get("messages")
        if messages is None:
            normalized["messages"] = []
        elif not isinstance(messages, list):
            normalized["messages"] = [messages] if isinstance(messages, dict | str) else []

        tools = normalized.get("tools")
        if tools is None:
            normalized["tools"] = None
        elif isinstance(tools, list):
            pass
        elif isinstance(tools, dict):
            normalized["tools"] = [tools]
        else:
            normalized["tools"] = None
        return normalized


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc: RequestValidationError):
    logger.warning(
        "request validation error path=%s errors=%s body=%s",
        request.url.path,
        exc.errors(),
        exc.body,
    )
    return JSONResponse(status_code=400, content={"detail": exc.errors()})


class AnthropicCountTokensRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    model: str = Field(default=DEFAULT_MODEL_ID)
    messages: list[dict[str, Any]]
    system: Any = None


def resolve_request_thinking_enabled(request: ChatCompletionRequest) -> bool | None:
    if isinstance(request.thinking_enabled, bool):
        return request.thinking_enabled
    extra_body = request.extra_body or {}
    candidate = extra_body.get("thinking_enabled")
    if isinstance(candidate, bool):
        return candidate
    return None


def resolve_request_expert_mode_enabled(request: ChatCompletionRequest) -> bool | None:
    if isinstance(getattr(request, "expert_mode_enabled", None), bool):
        return request.expert_mode_enabled
    extra_body = request.extra_body or {}
    for key in ("expert_mode_enabled", "expert_mode"):
        candidate = extra_body.get(key)
        if isinstance(candidate, bool):
            return candidate
    return None


def resolve_request_output_protocol(request: ChatCompletionRequest) -> str:
    direct = (getattr(request, "output_protocol", None) or "").strip().lower()
    extra_body = request.extra_body or {}
    nested = str(extra_body.get("output_protocol") or "").strip().lower()
    if direct or nested:
        protocol = direct or nested
        return "plain" if protocol == "plain" else "openai"
    for message in request.messages:
        try:
            content = message.content if isinstance(message.content, str) else json.dumps(message.content, ensure_ascii=False)
        except Exception:
            content = str(message.content)
        if (
            "本地 Agent 执行引擎" in content
            or "占位符 + 代码块" in content
            or "AGENT_QT_DONE" in content
            or "<!-- agent_qt_user_prompt:" in content
        ):
            return "plain"
    protocol = "openai"
    return "plain" if protocol == "plain" else "openai"


def resolve_effective_thinking_enabled(
    requested_thinking_enabled: bool | None,
    *,
    spec: ModelSpec,
) -> bool | None:
    if isinstance(spec.forced_thinking_enabled, bool):
        return spec.forced_thinking_enabled
    return requested_thinking_enabled


def resolve_effective_expert_mode_enabled(
    requested_expert_mode_enabled: bool | None,
    *,
    spec: ModelSpec,
) -> bool | None:
    if isinstance(spec.forced_expert_mode_enabled, bool):
        return spec.forced_expert_mode_enabled
    return requested_expert_mode_enabled


def normalize_anthropic_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
                    continue
            if isinstance(block, dict):
                nested = block.get("content")
                if nested is not None:
                    parts.append(normalize_anthropic_text(nested))
            elif block is not None:
                parts.append(str(block))
        return "\n".join(part for part in parts if part)
    if isinstance(content, dict):
        if "text" in content:
            return normalize_anthropic_text(content.get("text"))
        if "content" in content:
            return normalize_anthropic_text(content.get("content"))
        return json.dumps(content, ensure_ascii=False)
    return "" if content is None else str(content)


def anthropic_messages_to_bridge_payload(request: AnthropicMessageRequest) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []

    if request.system:
        payload.append({"role": "system", "content": normalize_anthropic_text(request.system)})

    for message in request.messages:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "user")
        content = message.get("content", "")

        if role == "assistant":
            assistant_content = ""
            tool_calls: list[dict[str, Any]] = []
            if isinstance(content, list):
                text_parts: list[str] = []
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    block_type = block.get("type")
                    if block_type == "text":
                        text = block.get("text")
                        if isinstance(text, str):
                            text_parts.append(text)
                    elif block_type == "tool_use":
                        name = block.get("name")
                        if isinstance(name, str) and name:
                            tool_calls.append(
                                {
                                    "id": block.get("id") or f"toolu_{uuid.uuid4().hex[:12]}",
                                    "name": name,
                                    "arguments": block.get("input", {}) if isinstance(block.get("input"), dict) else {},
                                }
                            )
                assistant_content = "\n".join(part for part in text_parts if part)
            else:
                assistant_content = normalize_anthropic_text(content)

            item: dict[str, Any] = {"role": "assistant", "content": assistant_content}
            if tool_calls:
                item["tool_calls"] = tool_calls
            payload.append(item)
            continue

        if role == "user":
            text_parts: list[str] = []
            if isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    block_type = block.get("type")
                    if block_type == "tool_result":
                        payload.append(
                            {
                                "role": "tool",
                                "tool_call_id": block.get("tool_use_id"),
                                "content": normalize_anthropic_text(block.get("content", "")),
                            }
                        )
                    elif block_type == "text":
                        text = block.get("text")
                        if isinstance(text, str):
                            text_parts.append(text)
                user_text = "\n".join(part for part in text_parts if part)
                if user_text:
                    payload.append({"role": "user", "content": user_text})
            else:
                payload.append({"role": "user", "content": normalize_anthropic_text(content)})
            continue

        if role == "tool":
            payload.append(
                {
                    "role": "tool",
                    "tool_call_id": message.get("tool_call_id"),
                    "content": normalize_anthropic_text(content),
                }
            )
            continue

        payload.append({"role": "user", "content": normalize_anthropic_text(content)})

    return payload


def anthropic_tools_to_openai_tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for tool in tools or []:
        if not isinstance(tool, dict):
            continue
        name = tool.get("name")
        if not isinstance(name, str) or not name:
            continue
        out.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": tool.get("description", ""),
                    "parameters": tool.get("input_schema", {"type": "object", "properties": {}}),
                },
            }
        )
    return out


def build_openai_assistant_message(payload: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
    assistant_content = payload.get("content", "")
    if _looks_like_prompt_replay_text(str(assistant_content or "")):
        logger.warning("Suppressing prompt-replay content before building assistant message.")
        assistant_content = ""
        payload = {**payload, "content": "", "parse_error": payload.get("parse_error") or "prompt_replay"}
    message: dict[str, Any] = {
        "role": "assistant",
        "content": assistant_content,
        "refusal": None,
    }
    tool_calls = payload.get("tool_calls") or []
    normalized_tool_calls: list[dict[str, Any]] = []
    if tool_calls:
        seen_ids: set[str] = set()
        message_tool_calls: list[dict[str, Any]] = []
        for tool_call in tool_calls:
            if not isinstance(tool_call, dict):
                continue
            name = tool_call.get("name")
            arguments = tool_call.get("arguments")
            if not isinstance(name, str) or not name:
                continue
            if isinstance(arguments, str):
                arguments_json = arguments
                arguments_obj: dict[str, Any] = {}
                try:
                    parsed = json.loads(arguments)
                    if isinstance(parsed, dict):
                        arguments_obj = parsed
                except Exception:
                    pass
            else:
                arguments_obj = arguments if isinstance(arguments, dict) else {}
                arguments_json = json.dumps(arguments_obj, ensure_ascii=False)

            candidate_id = tool_call.get("id")
            normalized_id = candidate_id if isinstance(candidate_id, str) and candidate_id else f"call_{uuid.uuid4().hex}"
            if normalized_id in seen_ids:
                normalized_id = f"call_{uuid.uuid4().hex}"
            seen_ids.add(normalized_id)

            normalized_tool_calls.append(
                {
                    "id": normalized_id,
                    "name": name,
                    "arguments": arguments_obj,
                }
            )
            message_tool_calls.append(
                {
                    "id": normalized_id,
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": arguments_json,
                    },
                }
            )

        message["tool_calls"] = message_tool_calls
        if not str(assistant_content or "").strip():
            message["content"] = None

    finish_reason = "tool_calls" if normalized_tool_calls else "stop"
    return message, normalized_tool_calls, finish_reason


RESPONSES_TOOL_CALLING_HINT = (
    "Responses compatibility rule: when you need to call any tool, emit a real function_call/tool_call "
    "with a concrete non-empty call_id/id such as call_1. Do not return prose or empty JSON instead of "
    "the tool call."
)
RESPONSES_WEB_SEARCH_TOOL_TYPES = {"web_search", "web_search_preview"}
PROVIDER_WEB_SEARCH_TOOL_NAME = "web_search"
PROVIDER_WEB_FETCH_TOOL_NAME = "web_fetch"
PROVIDER_WEB_TOOL_NAMES = {PROVIDER_WEB_SEARCH_TOOL_NAME, PROVIDER_WEB_FETCH_TOOL_NAME}
PROVIDER_WEB_SEARCH_TOOL_HINT = (
    "When the user needs current or external information, call the function tool "
    f'"{PROVIDER_WEB_SEARCH_TOOL_NAME}" with a concrete query instead of answering from memory. '
    f"After search results provide a relevant URL, call {PROVIDER_WEB_FETCH_TOOL_NAME} to fetch and clean that page before summarizing, crawling, or saving web content. "
    "After tool results arrive, use them and cite the listed sources."
)
def normalize_responses_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            text = normalize_responses_text(item)
            if text:
                parts.append(text)
        return "\n".join(parts)
    if isinstance(value, dict):
        block_type = value.get("type")
        if block_type in {"input_text", "output_text", "text"}:
            text = value.get("text")
            return text if isinstance(text, str) else ""
        if block_type == "input_image":
            return "[image]"
        if "content" in value:
            return normalize_responses_text(value.get("content"))
        if "output" in value:
            return normalize_responses_text(value.get("output"))
        if "text" in value:
            return normalize_responses_text(value.get("text"))
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return str(value)


def responses_tools_to_openai_tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for tool in tools or []:
        if not isinstance(tool, dict):
            continue
        if tool.get("type") != "function":
            continue
        function = tool.get("function")
        if isinstance(function, dict):
            name = function.get("name")
            if isinstance(name, str) and name:
                out.append(tool)
            continue
        name = tool.get("name")
        if not isinstance(name, str) or not name:
            continue
        out.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": tool.get("description", ""),
                    "parameters": tool.get("parameters", {"type": "object", "properties": {}}),
                },
            }
        )
    return out


def split_responses_tools(
    tools: list[dict[str, Any]] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    function_tools: list[dict[str, Any]] = []
    web_search_tools: list[dict[str, Any]] = []
    for tool in tools or []:
        if not isinstance(tool, dict):
            continue
        tool_type = tool.get("type")
        if tool_type == "function":
            function_tools.extend(responses_tools_to_openai_tools([tool]))
        elif tool_type in RESPONSES_WEB_SEARCH_TOOL_TYPES:
            web_search_tools.append(tool)
    return function_tools, web_search_tools


def _append_system_hint(messages: list[dict[str, Any]], hint: str) -> list[dict[str, Any]]:
    if not hint:
        return messages
    if messages and messages[0].get("role") == "system":
        updated = dict(messages[0])
        existing = str(updated.get("content", "") or "").strip()
        updated["content"] = f"{existing}\n\n{hint}" if existing else hint
        return [updated, *messages[1:]]
    return [{"role": "system", "content": hint}, *messages]


def _response_include_has(request: ResponsesRequest, value: str) -> bool:
    include = request.include or []
    return any(isinstance(item, str) and item == value for item in include)


def _extract_allowed_domains(web_search_tools: list[dict[str, Any]]) -> list[str]:
    domains: list[str] = []
    for tool in web_search_tools:
        filters = tool.get("filters")
        candidates: Any = None
        if isinstance(filters, dict):
            candidates = filters.get("allowed_domains") or filters.get("domains")
        if candidates is None:
            candidates = tool.get("allowed_domains") or tool.get("domains")
        if not isinstance(candidates, list):
            continue
        for candidate in candidates:
            if not isinstance(candidate, str):
                continue
            normalized = candidate.strip().lower()
            if normalized:
                domains.append(normalized)
    return list(dict.fromkeys(domains))


def _extract_max_results(web_search_tools: list[dict[str, Any]]) -> int:
    for tool in web_search_tools:
        for key in ("max_results", "limit"):
            value = tool.get(key)
            if isinstance(value, int) and value > 0:
                return min(10, value)
    return PROVIDER_WEB_SEARCH_MAX_RESULTS


def build_provider_web_search_tool(web_search_tools: list[dict[str, Any]]) -> dict[str, Any]:
    allowed_domains = _extract_allowed_domains(web_search_tools)
    domain_hint = ""
    if allowed_domains:
        domain_hint = f" Restrict results to these domains when possible: {', '.join(allowed_domains)}."
    return {
        "type": "function",
        "function": {
            "name": PROVIDER_WEB_SEARCH_TOOL_NAME,
            "description": (
                "Search the public web for up-to-date information and return JSON results with titles, URLs, and snippets."
                f"{domain_hint}"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The exact web search query to run.",
                    }
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    }


def build_provider_web_fetch_tool() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": PROVIDER_WEB_FETCH_TOOL_NAME,
            "description": (
                "Fetch a specific web page URL and return cleaned readable text extracted from the page. "
                "Use this after web_search when the user asks to crawl, inspect, summarize, or save original web pages."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The absolute http(s) URL to fetch.",
                    },
                    "include_html": {
                        "type": "boolean",
                        "description": "Whether to include the raw fetched HTML in the result. Defaults to false.",
                    },
                },
                "required": ["url"],
                "additionalProperties": False,
            },
        },
    }


def _normalize_tool_call_arguments(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _latest_user_text(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
    return ""


def _unwrap_subtask_text(text: str) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    match = re.search(r"\bSubtask:\s*(.+)$", raw, flags=re.IGNORECASE | re.DOTALL)
    if match:
        return match.group(1).strip()
    return raw


def _extract_explicit_search_query(text: str) -> str:
    raw = _unwrap_subtask_text(text)
    if not raw:
        return ""
    patterns = (
        r"""web_search[^"'“”\n]{0,80}["“](.{2,120}?)["”]""",
        r"""搜索[^"'“”\n]{0,40}["“](.{2,120}?)["”]""",
        r"""search[^"'“”\n]{0,40}["“](.{2,120}?)["”]""",
    )
    for source in (raw, str(text or "").strip()):
        for pattern in patterns:
            match = re.search(pattern, source, flags=re.IGNORECASE | re.DOTALL)
            if not match:
                continue
            candidate = " ".join(match.group(1).split())
            if candidate and len(candidate) <= 120:
                return candidate
    return ""


def _resolve_eager_web_search_query(messages: list[dict[str, Any]]) -> str:
    latest = _latest_user_text(messages)
    if not latest:
        return ""
    explicit = _extract_explicit_search_query(latest)
    if explicit:
        return explicit
    unwrapped = _unwrap_subtask_text(latest)
    lowered = unwrapped.lower()
    wrapper_markers = (
        "you are codexweb.",
        "complete this subtask in isolation.",
        "use the current workspace only.",
        "the configured working directory is exactly:",
        "evidence protocol:",
    )
    if any(marker in lowered for marker in wrapper_markers):
        return ""
    if len(unwrapped) > 500:
        return ""
    return unwrapped

def _domain_allowed(url: str, allowed_domains: list[str]) -> bool:
    if not allowed_domains:
        return True
    hostname = (urlparse(url).hostname or "").lower().strip(".")
    if not hostname:
        return False
    return any(hostname == domain or hostname.endswith(f".{domain}") for domain in allowed_domains)


def _plain_text_from_html(value: str) -> str:
    return html_lib.unescape(re.sub(r"<[^>]+>", " ", value or "")).strip()


def _query_keywords(query: str) -> list[str]:
    raw = str(query or "").strip()
    if not raw:
        return []
    keywords: list[str] = []
    for part in re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9_.-]{3,}", raw):
        normalized = part.strip().lower()
        if normalized:
            keywords.append(normalized)
            if re.fullmatch(r"[\u4e00-\u9fff]{6,}", normalized):
                for size in (4, 6):
                    for index in range(0, max(0, len(normalized) - size + 1)):
                        keywords.append(normalized[index : index + size])
    return list(dict.fromkeys(keywords))


def _search_result_relevance_score(query: str, *, title: str, url: str, content: str) -> int:
    haystack = " ".join([str(title or ""), str(url or ""), str(content or "")]).lower()
    score = 0
    for keyword in _query_keywords(query):
        if keyword in haystack:
            score += max(1, min(len(keyword), 8))
    if any(token in haystack for token in ("youtube", "google support", "maps.google", "learn more", "privacy", "terms")):
        score -= 8
    return score


def _browser_search_queries(query: str) -> list[str]:
    normalized_query = str(query or "").strip()
    if not normalized_query:
        return []
    keywords = _query_keywords(normalized_query)
    queries = [normalized_query]
    primary = ""
    for keyword in keywords:
        if re.fullmatch(r"[\u4e00-\u9fff]{4,}|[A-Za-z0-9_.-]{6,}", keyword):
            primary = keyword
            break
    if primary:
        queries.append(f'"{primary}"')
        extras = [keyword for keyword in keywords if keyword != primary][:2]
        if extras:
            queries.append(" ".join([f'"{primary}"', *extras]))
    return list(dict.fromkeys(item for item in queries if item.strip()))


def _expand_provider_web_search_backends(backends: tuple[str, ...]) -> list[tuple[str, str]]:
    expanded: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for raw_backend in backends:
        backend = raw_backend.strip().lower()
        if not backend:
            continue
        candidates: list[tuple[str, str]]
        if backend == "bing":
            candidates = [("ddgs", "duckduckgo"), ("ddgs", "yahoo")]
        elif backend in {"sm", "smcn", "shenma"}:
            candidates = [("html", "sm")]
        elif backend in {"so360", "360", "haosou"}:
            candidates = [("html", "so360")]
        elif backend in {"sogou"}:
            candidates = [("html", "sogou")]
        elif backend in {"baidu", "baidu_html"}:
            candidates = [("baidu", "baidu")]
        else:
            candidates = [("ddgs", backend)]
        for candidate in candidates:
            if candidate in seen:
                continue
            seen.add(candidate)
            expanded.append(candidate)
    return expanded or [("ddgs", "auto")]


def _normalize_provider_search_results(
    raw_results: list[dict[str, Any]],
    *,
    query: str = "",
    max_results: int,
    allowed_domains: list[str],
) -> list[dict[str, Any]]:
    normalized_results: list[dict[str, Any]] = []
    for item in raw_results or []:
        raw_url = str(item.get("href") or item.get("link") or item.get("url") or "").strip()
        if not raw_url:
            continue
        title = str(item.get("title") or "").strip()
        content = str(item.get("body") or item.get("snippet") or item.get("content") or "").strip()
        resolved_url = _extract_direct_url_from_search_result(raw_url, content)
        effective_url = resolved_url or raw_url
        if not _domain_allowed(effective_url, allowed_domains):
            continue
        if query and _search_result_relevance_score(query, title=title, url=effective_url, content=content) <= 0:
            continue
        result_item = {
            "title": title,
            "url": effective_url,
            "content": content,
        }
        if resolved_url and resolved_url != raw_url:
            result_item["source_url"] = raw_url
        normalized_results.append(result_item)
        if len(normalized_results) >= max_results:
            break
    return normalized_results


def _extract_direct_url_from_search_result(raw_url: str, content: str) -> str:
    candidate = str(raw_url or "").strip()
    if not candidate:
        return ""
    direct_from_content = ""
    direct_match = re.search(r"Direct URL:\s*(https?://\S+)", str(content or ""), re.I)
    if direct_match:
        direct_from_content = direct_match.group(1).strip().rstrip(".,;)]}")
    if direct_from_content.startswith(("http://", "https://")):
        return direct_from_content
    if not _looks_like_search_engine_redirect_url(candidate):
        return candidate
    resolved = _resolve_redirect_url(candidate, timeout_s=6).strip()
    if resolved.startswith(("http://", "https://")) and not _looks_like_search_engine_redirect_url(resolved):
        return resolved
    return candidate


def _looks_like_search_engine_redirect_url(url: str) -> bool:
    parsed = urlparse(str(url or "").strip())
    host = (parsed.hostname or "").lower()
    path = parsed.path or ""
    if host.endswith("so.com") and path.startswith("/link"):
        return True
    if host.endswith("sogou.com") and path.startswith("/link"):
        return True
    if host.endswith("baidu.com") and path.startswith("/link"):
        return True
    if host.endswith("bing.com") and path.startswith("/ck/"):
        return True
    return False


def _decode_bing_result_url(url: str) -> str:
    raw_url = str(url or "").strip()
    parsed = urlparse(raw_url)
    if "bing.com" not in (parsed.netloc or "").lower() or not parsed.path.startswith("/ck/"):
        return raw_url
    encoded = (parse_qs(parsed.query).get("u") or [""])[0]
    if not encoded:
        return raw_url
    if encoded.startswith("a1"):
        encoded = encoded[2:]
    try:
        padded = encoded + "=" * (-len(encoded) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8", "ignore")
        return unquote(decoded).strip() or raw_url
    except Exception:
        return raw_url


def _ascii_safe_url_for_request(raw_url: str) -> str:
    parsed = urlparse(str(raw_url or "").strip())
    if not parsed.scheme or not parsed.netloc:
        return str(raw_url or "").strip()
    netloc = parsed.netloc
    try:
        netloc.encode("ascii")
    except UnicodeEncodeError:
        if parsed.hostname:
            host = parsed.hostname.encode("idna").decode("ascii")
            if parsed.port:
                host = f"{host}:{parsed.port}"
            if parsed.username:
                userinfo = quote(unquote(parsed.username), safe="")
                if parsed.password:
                    userinfo += ":" + quote(unquote(parsed.password), safe="")
                host = f"{userinfo}@{host}"
            netloc = host
    path = quote(parsed.path or "/", safe="/%:@!$&'()*+,;=")
    query = quote(parsed.query or "", safe="=&%:/?+,-._~;")
    fragment = quote(parsed.fragment or "", safe="%:/?+,-._~")
    return parsed._replace(netloc=netloc, path=path, query=query, fragment=fragment).geturl()


class _AsciiSafeRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return super().redirect_request(req, fp, code, msg, headers, _ascii_safe_url_for_request(newurl))


def _ascii_safe_opener():
    return build_opener(_AsciiSafeRedirectHandler)


def _resolve_redirect_url(url: str, *, timeout_s: int = 15) -> str:
    raw_url = str(url or "").strip()
    if not raw_url.startswith(("http://", "https://")):
        return raw_url
    request = Request(
        _ascii_safe_url_for_request(raw_url),
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
            ),
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        },
    )
    try:
        with _ascii_safe_opener().open(request, timeout=timeout_s) as response:
            final_url = str(getattr(response, "url", "") or response.geturl() or "").strip()
            if final_url.startswith(("http://", "https://")) and not _looks_like_search_engine_redirect_url(final_url):
                return final_url
            content_type = str(getattr(response, "headers", {}).get("Content-Type", "") or "")
            charset = getattr(response.headers, "get_content_charset", lambda default=None: None)("utf-8") or "utf-8"
            try:
                html_bytes = response.read()
            except Exception:
                html_bytes = b""
            if html_bytes:
                try:
                    html_text = html_bytes.decode(charset, "ignore")
                except Exception:
                    html_text = html_bytes.decode("utf-8", "ignore")
                redirected = _extract_js_redirect_url(html_text, final_url or raw_url)
                if redirected.startswith(("http://", "https://")):
                    return redirected
                if "html" in content_type.lower():
                    meta_match = re.search(
                        r'<meta[^>]+http-equiv=["\']?refresh["\']?[^>]+content=["\'][^"\']*url=([^"\']+)["\']',
                        html_text or "",
                        re.I,
                    )
                    if meta_match:
                        candidate = urljoin(final_url or raw_url, html_lib.unescape(meta_match.group(1).strip()))
                        if candidate.startswith(("http://", "https://")):
                            return candidate
            if final_url.startswith(("http://", "https://")):
                return final_url
    except Exception:
        return raw_url
    return raw_url


def _extract_js_redirect_url(html_text: str, base_url: str) -> str:
    patterns = (
        r'window\.location(?:\.replace)?\(\s*["\']([^"\']+)["\']\s*\)',
        r'location\.href\s*=\s*["\']([^"\']+)["\']',
        r'location\.replace\(\s*["\']([^"\']+)["\']\s*\)',
    )
    for pattern in patterns:
        match = re.search(pattern, html_text or "", re.I)
        if match:
            candidate = urljoin(base_url, html_lib.unescape(match.group(1).strip()))
            if candidate.startswith(("http://", "https://")):
                return candidate
    return ""


def _is_search_engine_noise_url(url: str) -> bool:
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower()
    if hostname.endswith("bing.com"):
        return True
    if hostname.endswith("microsoft.com"):
        return True
    return False


def perform_bing_browser_web_search(query: str, *, max_results: int) -> list[dict[str, Any]]:
    from playwright.sync_api import sync_playwright

    search_url = "https://www.bing.com/search?q=" + quote(query)
    timeout_ms = PROVIDER_WEB_SEARCH_BROWSER_TIMEOUT_S * 1000
    results: list[dict[str, Any]] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
                )
            )
            page.goto(search_url, wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_timeout(800)
            for item in page.locator("li.b_algo").all()[: max_results * 3]:
                try:
                    link = item.locator("h2 a").first
                    title = (link.inner_text(timeout=1000) or "").strip()
                    href = _decode_bing_result_url((link.get_attribute("href") or "").strip())
                    snippet = ""
                    snippet_locator = item.locator(".b_caption p, p").first
                    if snippet_locator.count():
                        snippet = (snippet_locator.inner_text(timeout=1000) or "").strip()
                except Exception:
                    continue
                if not title or not href.startswith(("http://", "https://")) or _is_search_engine_noise_url(href):
                    continue
                results.append({"title": title, "href": href, "body": snippet})
                if len(results) >= max_results:
                    break
            if not results:
                for link in page.locator("#b_results h2 a, main h2 a").all()[: max_results * 3]:
                    try:
                        title = (link.inner_text(timeout=1000) or "").strip()
                        href = _decode_bing_result_url((link.get_attribute("href") or "").strip())
                    except Exception:
                        continue
                    if not title or not href.startswith(("http://", "https://")) or _is_search_engine_noise_url(href):
                        continue
                    results.append({"title": title, "href": href, "body": ""})
                    if len(results) >= max_results:
                        break
        finally:
            browser.close()
    return results


def perform_baidu_browser_web_search(query: str, *, max_results: int) -> list[dict[str, Any]]:
    from playwright.sync_api import sync_playwright

    search_url = "https://www.baidu.com/s?wd=" + quote(query)
    timeout_ms = PROVIDER_WEB_SEARCH_BROWSER_TIMEOUT_S * 1000
    results: list[dict[str, Any]] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
                )
            )
            page.goto(search_url, wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_timeout(800)
            html_text = page.content()
            if "百度安全验证" in html_text or "wappass.baidu.com" in html_text:
                raise RuntimeError("Baidu browser search returned security verification page.")
            for item in page.locator("#content_left .result, #content_left .c-container").all()[: max_results * 3]:
                try:
                    link = item.locator("h3 a").first
                    title = (link.inner_text(timeout=1000) or "").strip()
                    href = (link.get_attribute("href") or "").strip()
                    snippet = ""
                    snippet_locator = item.locator(".c-abstract, .content-right_8Zs40, .c-span-last p, .c-line-clamp1, .c-line-clamp2").first
                    if snippet_locator.count():
                        snippet = (snippet_locator.inner_text(timeout=1000) or "").strip()
                except Exception:
                    continue
                if not title or not href.startswith(("http://", "https://")):
                    continue
                if _is_search_engine_noise_url(href):
                    continue
                results.append({"title": title, "href": href, "body": snippet})
                if len(results) >= max_results:
                    break
        finally:
            browser.close()
    return results


def perform_baidu_web_search(query: str, *, max_results: int) -> list[dict[str, Any]]:
    url = "https://www.baidu.com/s?wd=" + quote(query)
    request = Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
            ),
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        },
    )
    with urlopen(request, timeout=15) as response:
        html_text = response.read(300_000).decode("utf-8", "ignore")
    if "百度安全验证" in html_text or "wappass.baidu.com" in html_text:
        raise RuntimeError("Baidu returned security verification page.")

    results: list[dict[str, Any]] = []
    for match in re.finditer(r"<h3\b[^>]*>(.*?)</h3>", html_text, re.I | re.S):
        block = match.group(1)
        href_match = re.search(r'href=["\']([^"\']+)["\']', block, re.I)
        title = _plain_text_from_html(block)
        if not href_match or not title:
            continue
        href = urljoin("https://www.baidu.com/", html_lib.unescape(href_match.group(1)))
        tail = html_text[match.end() : match.end() + 1200]
        snippet = _plain_text_from_html(tail)
        results.append({"title": title, "href": href, "body": snippet[:300]})
        if len(results) >= max_results:
            break
    return results


def _fetch_html_text(url: str, *, timeout_s: int = 15) -> str:
    request = Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
            ),
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        },
    )
    with urlopen(request, timeout=timeout_s) as response:
        content_type = str(response.headers.get("Content-Type") or "")
        charset_match = re.search(r"charset=([\w.-]+)", content_type, re.I)
        charset = charset_match.group(1) if charset_match else "utf-8"
        data = response.read(800_000)
    try:
        return data.decode(charset, "ignore")
    except LookupError:
        return data.decode("utf-8", "ignore")


def perform_sm_web_search(query: str, *, max_results: int) -> list[dict[str, Any]]:
    from bs4 import BeautifulSoup

    html_text = _fetch_html_text("https://m.sm.cn/s?" + urlencode({"q": query}), timeout_s=15)
    soup = BeautifulSoup(html_text, "html.parser")
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for link in soup.select("a[href]"):
        href = str(link.get("href") or "").strip()
        title = link.get_text(" ", strip=True)
        if not href.startswith(("http://", "https://")):
            continue
        if not title or _is_search_engine_noise_url(href):
            continue
        if href in seen:
            continue
        seen.add(href)
        results.append({"title": title, "href": href, "body": ""})
        if len(results) >= max_results:
            break
    return results


def perform_so360_web_search(query: str, *, max_results: int) -> list[dict[str, Any]]:
    from bs4 import BeautifulSoup

    search_url = "https://www.so.com/s?" + urlencode({"q": query})
    html_text = _fetch_html_text(search_url, timeout_s=15)
    soup = BeautifulSoup(html_text, "html.parser")
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for heading in soup.select("h3 a[href]"):
        title = heading.get_text(" ", strip=True)
        href = str(heading.get("href") or "").strip()
        if not href:
            continue
        absolute = urljoin(search_url, href)
        if not absolute.startswith(("http://", "https://")) or _is_search_engine_noise_url(absolute):
            continue
        if not title or absolute in seen:
            continue
        seen.add(absolute)
        snippet = ""
        parent = heading.parent.parent if heading.parent is not None else None
        if parent is not None:
            snippet = parent.get_text(" ", strip=True)[:300]
        results.append({"title": title, "href": absolute, "body": snippet})
        if len(results) >= max_results:
            break
    return results


def perform_sogou_web_search(query: str, *, max_results: int) -> list[dict[str, Any]]:
    from bs4 import BeautifulSoup

    search_url = "https://www.sogou.com/web?" + urlencode({"query": query})
    html_text = _fetch_html_text(search_url, timeout_s=15)
    soup = BeautifulSoup(html_text, "html.parser")
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for heading in soup.select("h3 a[href]"):
        title = heading.get_text(" ", strip=True)
        href = str(heading.get("href") or "").strip()
        if not href or not title:
            continue
        container = heading.find_parent()
        direct_href = ""
        if container is not None:
            cite = container.find_next("a", class_="citeLinkClass", href=True)
            if cite is not None:
                direct_href = str(cite.get("href") or "").strip()
        absolute = urljoin(search_url, href)
        if not absolute.startswith(("http://", "https://")) or _is_search_engine_noise_url(absolute):
            continue
        if absolute in seen:
            continue
        seen.add(absolute)
        snippet = ""
        if container is not None:
            snippet = container.get_text(" ", strip=True)[:300]
        if direct_href and direct_href.startswith(("http://", "https://")):
            snippet = f"{snippet}\nDirect URL: {direct_href}".strip()
        results.append({"title": title, "href": absolute, "body": snippet})
        if len(results) >= max_results:
            break
    return results


def perform_direct_provider_web_search(
    query: str,
    *,
    max_results: int,
    allowed_domains: list[str],
) -> dict[str, Any]:
    try:
        from ddgs import DDGS
    except ImportError as exc:
        raise RuntimeError("ddgs is not installed in the provider environment.") from exc

    normalized_query = (query or "").strip()
    if not normalized_query:
        return {"query": "", "total_results": 0, "results": []}

    errors: list[str] = []
    saw_raw_results = False
    for backend_kind, backend in _expand_provider_web_search_backends(PROVIDER_WEB_SEARCH_BACKENDS):
        try:
            if backend_kind == "baidu":
                raw_results = perform_baidu_web_search(
                    normalized_query,
                    max_results=max_results * 3 if allowed_domains else max_results,
                )
            elif backend_kind == "html":
                if backend == "sm":
                    raw_results = perform_sm_web_search(
                        normalized_query,
                        max_results=max_results * 3 if allowed_domains else max_results,
                    )
                elif backend == "so360":
                    raw_results = perform_so360_web_search(
                        normalized_query,
                        max_results=max_results * 3 if allowed_domains else max_results,
                    )
                elif backend == "sogou":
                    raw_results = perform_sogou_web_search(
                        normalized_query,
                        max_results=max_results * 3 if allowed_domains else max_results,
                    )
                else:
                    raise RuntimeError(f"Unsupported html search backend: {backend}")
            else:
                ddgs = DDGS(timeout=PROVIDER_WEB_SEARCH_DIRECT_TIMEOUT_S)
                raw_results = ddgs.text(
                    normalized_query,
                    region="wt-wt",
                    safesearch="moderate",
                    backend=backend,
                    max_results=max_results * 3 if allowed_domains else max_results,
                )
        except Exception as exc:
            errors.append(f"{backend}: {str(exc)[:300]}")
            logger.info("provider web_search backend failed backend=%s query=%r error=%s", backend, normalized_query[:160], str(exc)[:300])
            continue
        saw_raw_results = saw_raw_results or bool(raw_results)
        normalized_results = _normalize_provider_search_results(
            raw_results,
            query=normalized_query,
            max_results=max_results,
            allowed_domains=allowed_domains,
        )
        if raw_results and not normalized_results:
            errors.append(f"{backend}: returned {len(raw_results)} raw results but 0 matched filters")
            continue
        if raw_results:
            logger.warning(
                "provider web_search backend succeeded backend=%s query=%r results=%d",
                backend,
                normalized_query[:160],
                len(normalized_results),
            )
            return {
                "query": normalized_query,
                "total_results": len(normalized_results),
                "results": normalized_results,
                "backend": backend,
            }
    if not saw_raw_results:
        raise RuntimeError("; ".join(errors) or "No results found.")
    return {
        "query": normalized_query,
        "total_results": 0,
        "results": [],
        "error": "; ".join(errors),
    }


def perform_browser_provider_web_search(
    query: str,
    *,
    max_results: int,
    allowed_domains: list[str],
) -> dict[str, Any]:
    normalized_query = (query or "").strip()
    if not normalized_query:
        return {"query": "", "total_results": 0, "results": []}
    browser_search_fn = {
        "bing": perform_bing_browser_web_search,
        "baidu": perform_baidu_browser_web_search,
    }.get(PROVIDER_WEB_SEARCH_BROWSER_ENGINE)
    if browser_search_fn is None:
        raise RuntimeError(f"Unsupported browser search engine: {PROVIDER_WEB_SEARCH_BROWSER_ENGINE}")
    errors: list[str] = []
    for browser_query in _browser_search_queries(normalized_query):
        try:
            raw_results = browser_search_fn(
                browser_query,
                max_results=max_results * 3 if allowed_domains else max_results,
            )
            normalized_results = _normalize_provider_search_results(
                raw_results,
                query=normalized_query,
                max_results=max_results,
                allowed_domains=allowed_domains,
            )
        except Exception as exc:
            errors.append(f"{browser_query[:80]}: {str(exc)[:200]}")
            continue
        if not normalized_results:
            errors.append(f"{browser_query[:80]}: no usable results")
            continue
        logger.warning(
            "provider web_search browser succeeded engine=%s query=%r browser_query=%r results=%d",
            PROVIDER_WEB_SEARCH_BROWSER_ENGINE,
            normalized_query[:160],
            browser_query[:160],
            len(normalized_results),
        )
        return {
            "query": normalized_query,
            "total_results": len(normalized_results),
            "results": normalized_results,
            "backend": f"browser:{PROVIDER_WEB_SEARCH_BROWSER_ENGINE}",
        }
    raise RuntimeError(f"Browser {PROVIDER_WEB_SEARCH_BROWSER_ENGINE} returned no usable results. " + "; ".join(errors[:3]))


def perform_provider_web_search(
    query: str,
    *,
    max_results: int,
    allowed_domains: list[str],
) -> dict[str, Any]:
    if not PROVIDER_WEB_SEARCH_BROWSER_ENABLED:
        return perform_direct_provider_web_search(
            query,
            max_results=max_results,
            allowed_domains=allowed_domains,
        )

    errors: list[str] = []
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="ProviderWebSearch")
    futures: dict[concurrent.futures.Future[dict[str, Any]], str] = {
        executor.submit(
            perform_browser_provider_web_search,
            query,
            max_results=max_results,
            allowed_domains=allowed_domains,
        ): "browser:bing",
        executor.submit(
            perform_direct_provider_web_search,
            query,
            max_results=max_results,
            allowed_domains=allowed_domains,
        ): "direct",
    }
    try:
        pending = set(futures)
        fallback_result: dict[str, Any] | None = None
        while pending:
            done, pending = concurrent.futures.wait(
                pending,
                timeout=PROVIDER_WEB_SEARCH_BROWSER_TIMEOUT_S,
                return_when=concurrent.futures.FIRST_COMPLETED,
            )
            if not done:
                break
            for future in done:
                source = futures[future]
                try:
                    result = future.result()
                except Exception as exc:
                    errors.append(f"{source}: {str(exc)[:300]}")
                    logger.info("provider web_search parallel source failed source=%s query=%r error=%s", source, str(query or "")[:160], str(exc)[:300])
                    continue
                if isinstance(result, dict) and result.get("results"):
                    if source == "browser:bing":
                        for leftover in pending:
                            leftover.cancel()
                        return result
                    fallback_result = result
                    if pending and PROVIDER_WEB_SEARCH_BROWSER_GRACE_S > 0:
                        grace_done, pending = concurrent.futures.wait(
                            pending,
                            timeout=PROVIDER_WEB_SEARCH_BROWSER_GRACE_S,
                            return_when=concurrent.futures.FIRST_COMPLETED,
                        )
                        for grace_future in grace_done:
                            grace_source = futures[grace_future]
                            try:
                                grace_result = grace_future.result()
                            except Exception as exc:
                                errors.append(f"{grace_source}: {str(exc)[:300]}")
                                logger.info("provider web_search parallel source failed source=%s query=%r error=%s", grace_source, str(query or "")[:160], str(exc)[:300])
                                continue
                            if isinstance(grace_result, dict) and grace_result.get("results"):
                                if grace_source == "browser:bing":
                                    for leftover in pending:
                                        leftover.cancel()
                                    return grace_result
                                fallback_result = grace_result
                    for leftover in pending:
                        leftover.cancel()
                    return fallback_result
                errors.append(f"{source}: no usable results")
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    raise RuntimeError("; ".join(errors) or "No results found.")


def perform_provider_web_fetch(
    url: str,
    *,
    include_html: bool = False,
    max_bytes: int = PROVIDER_WEB_FETCH_MAX_BYTES,
) -> dict[str, Any]:
    return _perform_provider_web_fetch(
        url,
        include_html=include_html,
        max_bytes=max_bytes,
        _redirect_depth=0,
    )


def _perform_provider_web_fetch(
    url: str,
    *,
    include_html: bool = False,
    max_bytes: int = PROVIDER_WEB_FETCH_MAX_BYTES,
    _redirect_depth: int = 0,
) -> dict[str, Any]:
    normalized_url = str(url or "").strip()
    parsed = urlparse(normalized_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RuntimeError(f"Invalid URL for web_fetch: {normalized_url!r}")

    capped_bytes = max(50_000, min(int(max_bytes or PROVIDER_WEB_FETCH_MAX_BYTES), PROVIDER_WEB_FETCH_MAX_BYTES))
    request = Request(
        _ascii_safe_url_for_request(normalized_url),
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        },
    )
    with _ascii_safe_opener().open(request, timeout=30) as response:
        body = response.read(capped_bytes)
        content_type = str(response.headers.get("Content-Type") or "")
        status = int(getattr(response, "status", 200) or 200)

    charset_match = re.search(r"charset=([\w.-]+)", content_type, re.I)
    charset = charset_match.group(1) if charset_match else "utf-8"
    try:
        html_text = body.decode(charset, "ignore")
    except LookupError:
        html_text = body.decode("utf-8", "ignore")

    if _redirect_depth < 2:
        js_redirect_url = _extract_js_redirect_url(html_text, normalized_url)
        if js_redirect_url and js_redirect_url != normalized_url:
            return _perform_provider_web_fetch(
                js_redirect_url,
                include_html=include_html,
                max_bytes=max_bytes,
                _redirect_depth=_redirect_depth + 1,
            )

    title_match = re.search(r"<title\b[^>]*>(.*?)</title>", html_text, re.I | re.S)
    title = _plain_text_from_html(title_match.group(1)) if title_match else ""
    captcha_hit = bool(
        re.search(r"(请输入验证码|验证码下载附件|附件下载|createimage\.jsp|codeValue|changeCodeImg)", html_text, re.I)
        and re.search(r"(验证码|codeValue|createimage\.jsp)", html_text, re.I)
    )
    if captcha_hit:
        img_match = re.search(r"<img[^>]+(?:id=['\"]codeimg['\"][^>]*src|src)=['\"]([^'\"]*createimage\.jsp[^'\"]*)['\"]", html_text, re.I)
        captcha_img_url = urljoin(normalized_url, img_match.group(1)) if img_match else ""
        content = "下载受阻：该页面要求输入验证码，未获取到真实附件。"
        if captcha_img_url:
            content += f"\n验证码图片：{captcha_img_url}"
        content += "\n建议：保留该附件 URL 清单，由用户在浏览器中输入验证码下载，或接入浏览器自动化人工确认流程。"
        return {
            "url": normalized_url,
            "status": status,
            "content_type": content_type,
            "title": title or "附件下载需要验证码",
            "content": content,
            "bytes": len(body),
            "captcha_required": True,
            "captcha_image_url": captcha_img_url,
        }
    cleaned_text = ""
    try:
        import trafilatura

        cleaned_text = trafilatura.extract(
            html_text,
            url=normalized_url,
            favor_recall=True,
            include_links=True,
        ) or ""
    except Exception as exc:
        logger.info("provider web_fetch trafilatura failed url=%r error=%s", normalized_url[:240], str(exc)[:300])
    if not cleaned_text:
        cleaned_text = re.sub(r"\s+", " ", _plain_text_from_html(html_text)).strip()
    supplemental_links = _extract_representative_page_links(html_text, normalized_url, cleaned_text, limit=14)
    if supplemental_links:
        links_block = "页面关键链接：\n" + "\n".join(f"- {item}" for item in supplemental_links)
        cleaned_text = (cleaned_text.strip() + "\n\n" + links_block).strip() if cleaned_text.strip() else links_block

    result: dict[str, Any] = {
        "url": normalized_url,
        "status": status,
        "content_type": content_type,
        "title": title,
        "content": cleaned_text,
        "bytes": len(body),
    }
    if include_html:
        result["html"] = html_text
    return result


def _extract_representative_page_links(
    html_text: str,
    base_url: str,
    cleaned_text: str,
    *,
    limit: int = 14,
) -> list[str]:
    try:
        from bs4 import BeautifulSoup
    except Exception:
        return []

    soup = BeautifulSoup(html_text, "html.parser")
    base_host = str(urlparse(base_url).netloc or "").lower()
    existing_urls = set(re.findall(r"https?://[^\s)\]]+", str(cleaned_text or "")))
    seen_urls: set[str] = set()
    scored: list[tuple[int, str]] = []
    keyword_re = re.compile(r"(通知|公告|新闻|招标|采购|调研|公示|学校|学院|列表|详情)", re.I)
    attachment_url_re = re.compile(
        r"(\.(?:docx?|xlsx?|pptx?|pdf|zip|rar|7z)(?:$|[\s?&#）)]|%[0-9a-f]{2})|"
        r"(?:^|[/?&=_-])(?:download|attach(?:ment)?|file|files|doc|docs)(?:$|[/?&=_-])|"
        r"(?:^|[?&])(?:fileid|wbfileid|attachid|attachmentid|filename|filepath|file|path)=)",
        re.I,
    )

    for anchor in soup.find_all("a", href=True):
        href = str(anchor.get("href") or "").strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        absolute = urljoin(base_url, html_lib.unescape(href))
        parsed = urlparse(absolute)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            continue
        url_like_download = bool(attachment_url_re.search(f"{absolute} {parsed.path}")) or anchor.has_attr("download")
        text = _plain_text_from_html(anchor.get_text(" ", strip=True))
        if not text:
            text = _plain_text_from_html(str(anchor.get("title") or "")).strip()
        if not text and url_like_download:
            text = unquote(str(parsed.path or "").rsplit("/", 1)[-1] or "下载链接") or "下载链接"
        if not text:
            continue
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) < 2:
            continue
        absolute = absolute.strip()
        if absolute in seen_urls or absolute in existing_urls:
            continue
        seen_urls.add(absolute)

        score = 0
        host = str(parsed.netloc or "").lower()
        path = str(parsed.path or "")
        if host == base_host:
            score += 40
        elif host.endswith("." + base_host):
            score += 30
        else:
            score += 5
        is_attachment = url_like_download
        if is_attachment:
            score += 90
        if path.endswith((".htm", ".html", ".shtml")):
            score += 18
        if "/info/" in path or "/index/" in path:
            score += 12
        if keyword_re.search(text) or keyword_re.search(path):
            score += 20
        if 2 <= len(text) <= 18:
            score += 12
        elif len(text) <= 36:
            score += 6
        if re.search(r"\d{4}[-/]\d{2}[-/]\d{2}", text):
            score += 4

        label = f"下载链接：{text}" if is_attachment and not re.search(r"^(附件|下载|download)", text, re.I) else text
        scored.append((score, f"{label} -> {absolute}"))

    scored.sort(key=lambda item: (-item[0], item[1]))
    return [item for _, item in scored[: max(1, int(limit or 14))]]


async def perform_provider_web_search_with_retries(
    query: str,
    *,
    max_results: int,
    allowed_domains: list[str],
    request_id: str,
    context: str,
) -> dict[str, Any]:
    last_exc: Exception | None = None
    for attempt in range(1, PROVIDER_WEB_SEARCH_ATTEMPTS + 1):
        try:
            result = await asyncio.to_thread(
                perform_provider_web_search,
                query,
                max_results=max_results,
                allowed_domains=allowed_domains,
            )
            if attempt > 1:
                logger.warning(
                    "provider[%s] web_search recovered context=%s attempt=%d results=%d",
                    request_id,
                    context,
                    attempt,
                    len(result.get("results", []) if isinstance(result, dict) else []),
                )
            return result
        except Exception as exc:
            last_exc = exc
            if attempt >= PROVIDER_WEB_SEARCH_ATTEMPTS:
                break
            logger.warning(
                "provider[%s] web_search failed context=%s attempt=%d/%d: %s",
                request_id,
                context,
                attempt,
                PROVIDER_WEB_SEARCH_ATTEMPTS,
                str(exc)[:300],
            )
            if PROVIDER_WEB_SEARCH_RETRY_DELAY_S > 0:
                await asyncio.sleep(PROVIDER_WEB_SEARCH_RETRY_DELAY_S)
    assert last_exc is not None
    raise last_exc


def build_provider_web_search_item(
    search_result: dict[str, Any],
    *,
    include_sources: bool,
) -> dict[str, Any]:
    action: dict[str, Any] = {
        "type": "search",
        "query": search_result.get("query", ""),
        "queries": [search_result.get("query", "")],
    }
    if include_sources:
        action["sources"] = [
            {
                "type": "url",
                "title": result.get("title", ""),
                "url": result.get("url", ""),
                "snippet": result.get("content", ""),
            }
            for result in search_result.get("results", [])
        ]
    return {
        "id": f"ws_{uuid.uuid4().hex}",
        "type": "web_search_call",
        "status": "completed",
        "action": action,
    }


def build_provider_web_search_context(search_results: list[dict[str, Any]]) -> str:
    compact_results: list[dict[str, Any]] = []
    for search_result in search_results:
        if not isinstance(search_result, dict):
            continue
        compact_results.append(
            {
                "query": search_result.get("query", ""),
                "error": search_result.get("error", ""),
                "results": [
                    {
                        "title": item.get("title", ""),
                        "url": item.get("url", ""),
                        "snippet": item.get("content", ""),
                    }
                    for item in search_result.get("results", [])
                    if isinstance(item, dict)
                ],
            }
        )
    return (
        "Provider-side web search has already been executed locally using DDGS. "
        "Use the following search results as current external evidence, cite URLs when relevant, "
        "and do not call a web_search tool for the same query again.\n"
        f"{json.dumps(compact_results, ensure_ascii=False, indent=2)}"
    )


def _dedupe_search_results(search_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for search_result in search_results:
        for item in search_result.get("results", []):
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "").strip()
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            deduped.append(item)
    return deduped


def append_provider_search_citations(
    content: str,
    search_results: list[dict[str, Any]],
) -> tuple[str, list[dict[str, Any]]]:
    deduped = _dedupe_search_results(search_results)
    if not deduped:
        return content, []
    base_content = content.rstrip()
    separator = "\n\n" if base_content else ""
    prefix = "Sources:\n"
    lines: list[str] = []
    annotations: list[dict[str, Any]] = []
    current_offset = len(base_content) + len(separator) + len(prefix)
    for index, item in enumerate(deduped, start=1):
        title = str(item.get("title") or item.get("url") or f"Result {index}")
        url = str(item.get("url") or "").strip()
        line = f"[{index}] {title} - {url}"
        title_start = current_offset + len(f"[{index}] ")
        title_end = title_start + len(title)
        annotations.append(
            {
                "type": "url_citation",
                "start_index": title_start,
                "end_index": title_end,
                "title": title,
                "url": url,
            }
        )
        lines.append(line)
        current_offset += len(line) + 1
    return f"{base_content}{separator}{prefix}" + "\n".join(lines), annotations


def _filter_internal_web_search_calls(payload: dict[str, Any]) -> dict[str, Any]:
    tool_calls = payload.get("tool_calls")
    if not isinstance(tool_calls, list):
        return payload
    filtered = [
        tool_call
        for tool_call in tool_calls
        if not (isinstance(tool_call, dict) and tool_call.get("name") in PROVIDER_WEB_TOOL_NAMES)
    ]
    if len(filtered) == len(tool_calls):
        return payload
    updated = dict(payload)
    updated["tool_calls"] = filtered
    return updated


def responses_input_to_chat_messages(input_value: Any, instructions: str | None, has_tools: bool) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    system_parts = [part for part in (instructions, RESPONSES_TOOL_CALLING_HINT if has_tools else None) if part]
    if system_parts:
        messages.append({"role": "system", "content": "\n\n".join(system_parts)})

    if isinstance(input_value, str):
        if input_value:
            messages.append({"role": "user", "content": input_value})
        return messages

    if isinstance(input_value, dict):
        items = [input_value]
    elif isinstance(input_value, list):
        items = input_value
    else:
        text = normalize_responses_text(input_value)
        if text:
            messages.append({"role": "user", "content": text})
        return messages

    for item in items:
        if isinstance(item, str):
            if item:
                messages.append({"role": "user", "content": item})
            continue
        if not isinstance(item, dict):
            text = normalize_responses_text(item)
            if text:
                messages.append({"role": "user", "content": text})
            continue

        item_type = item.get("type")
        if item_type == "function_call":
            name = item.get("name")
            if not isinstance(name, str) or not name:
                continue
            arguments = item.get("arguments", "{}")
            if not isinstance(arguments, str):
                arguments = json.dumps(arguments if isinstance(arguments, dict) else {}, ensure_ascii=False)
            call_id = item.get("call_id") or item.get("id") or f"call_{uuid.uuid4().hex}"
            messages.append(
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": str(call_id),
                            "type": "function",
                            "function": {
                                "name": name,
                                "arguments": arguments,
                            },
                        }
                    ],
                }
            )
            continue

        if item_type == "function_call_output":
            call_id = item.get("call_id") or item.get("id")
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": str(call_id) if call_id else "",
                    "content": normalize_responses_text(item.get("output", "")),
                }
            )
            continue

        if item_type == "message" or "role" in item:
            role = item.get("role")
            if role not in {"system", "user", "assistant", "tool"}:
                role = "user"
            message: dict[str, Any] = {
                "role": role,
                "content": normalize_responses_text(item.get("content", "")),
            }
            if role == "tool":
                call_id = item.get("tool_call_id") or item.get("call_id")
                if call_id:
                    message["tool_call_id"] = str(call_id)
            tool_calls = item.get("tool_calls")
            if isinstance(tool_calls, list):
                message["tool_calls"] = tool_calls
            messages.append(message)
            continue

        text = normalize_responses_text(item.get("content", item.get("text", "")))
        if text:
            messages.append({"role": "user", "content": text})

    return messages


def resolve_responses_thinking_enabled(request: ResponsesRequest) -> bool | None:
    if request.thinking_enabled is not None:
        return request.thinking_enabled
    reasoning = request.reasoning if isinstance(request.reasoning, dict) else {}
    effort = reasoning.get("effort")
    if effort == "none":
        return False
    return None


def resolve_responses_expert_mode_enabled(request: ResponsesRequest) -> bool | None:
    if isinstance(request.expert_mode_enabled, bool):
        return request.expert_mode_enabled
    return None


async def resolve_provider_web_search(
    *,
    payload: dict[str, Any],
    pool: BridgePool,
    spec: ModelSpec,
    request_id: str,
    request_messages: list[dict[str, Any]],
    bridge_tools: list[dict[str, Any]],
    request_thinking_enabled: bool | None,
    request_expert_mode_enabled: bool | None,
    web_search_tools: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    if not web_search_tools:
        return payload, [], []

    request_messages = list(request_messages)
    max_results = _extract_max_results(web_search_tools)
    allowed_domains = _extract_allowed_domains(web_search_tools)
    include_sources = True
    web_search_items: list[dict[str, Any]] = []
    search_results: list[dict[str, Any]] = []

    for step in range(PROVIDER_WEB_SEARCH_MAX_STEPS):
        current_payload = apply_text_tool_call_fallback(payload, bridge_tools)
        current_payload = validate_tool_calls_against_schemas(current_payload, bridge_tools)
        raw_tool_calls = current_payload.get("tool_calls") or []
        provider_calls = [
            tool_call
            for tool_call in raw_tool_calls
            if isinstance(tool_call, dict) and tool_call.get("name") in PROVIDER_WEB_TOOL_NAMES
        ]
        non_provider_tool_calls = [
            tool_call
            for tool_call in raw_tool_calls
            if isinstance(tool_call, dict) and tool_call.get("name") not in PROVIDER_WEB_TOOL_NAMES
        ]
        synthesized_provider_call = False

        if not provider_calls and step == 0 and not non_provider_tool_calls:
            fallback_query = _latest_user_text(request_messages)
            if fallback_query:
                provider_calls = [
                    {
                        "id": f"call_{uuid.uuid4().hex}",
                        "name": PROVIDER_WEB_SEARCH_TOOL_NAME,
                        "arguments": {"query": fallback_query},
                    }
                ]
                synthesized_provider_call = True

        if not provider_calls:
            return _filter_internal_web_search_calls(current_payload), web_search_items, search_results

        assistant_tool_calls: list[dict[str, Any]] = []
        tool_messages: list[dict[str, Any]] = []
        for tool_call in provider_calls:
            tool_call_id = str(tool_call.get("id") or f"call_{uuid.uuid4().hex}")
            tool_name = str(tool_call.get("name") or PROVIDER_WEB_SEARCH_TOOL_NAME)
            arguments = _normalize_tool_call_arguments(tool_call.get("arguments"))
            if tool_name == PROVIDER_WEB_FETCH_TOOL_NAME:
                url = str(arguments.get("url") or "").strip()
                if not url:
                    continue
                fetch_result = await asyncio.to_thread(
                    perform_provider_web_fetch,
                    url,
                    include_html=bool(arguments.get("include_html")),
                )
                tool_result: dict[str, Any] = {
                    "type": "web_fetch",
                    **fetch_result,
                }
            else:
                query = str(arguments.get("query") or "").strip() or _latest_user_text(request_messages)
                if not query:
                    continue
                search_result = await perform_provider_web_search_with_retries(
                    query,
                    max_results=max_results,
                    allowed_domains=allowed_domains,
                    request_id=request_id,
                    context="responses-tool-call",
                )
                search_results.append(search_result)
                web_search_items.append(
                    build_provider_web_search_item(search_result, include_sources=include_sources)
                )
                tool_result = search_result
            assistant_tool_calls.append(
                {
                    "id": tool_call_id,
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "arguments": json.dumps(arguments, ensure_ascii=False),
                    },
                }
            )
            tool_messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": json.dumps(tool_result, ensure_ascii=False),
                }
            )

        if not assistant_tool_calls or not tool_messages:
            return _filter_internal_web_search_calls(current_payload), web_search_items, search_results

        request_messages.extend(
            [
                {
                    "role": "assistant",
                    "content": "" if synthesized_provider_call else (current_payload.get("content", "") or ""),
                    "tool_calls": assistant_tool_calls,
                },
                *tool_messages,
            ]
        )

        payload, _slot_timing = await run_on_bridge_slot(
            pool,
            spec=spec,
            request_id=request_id,
            route="/v1/responses/web-search",
            operation=lambda bridge, slot_spec: bridge_call_with_spec(
                bridge,
                spec=slot_spec,
                messages=request_messages,
                tools=bridge_tools,
                thinking_enabled=request_thinking_enabled,
                expert_mode_enabled=request_expert_mode_enabled,
                output_protocol="openai",
            ),
        )

    return _filter_internal_web_search_calls(payload), web_search_items, search_results


async def resolve_provider_web_search_eager(
    *,
    request_messages: list[dict[str, Any]],
    web_search_tools: list[dict[str, Any]],
    request_id: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    if not web_search_tools:
        return request_messages, [], []

    query = _latest_user_text(request_messages)
    if not query:
        return request_messages, [], []

    max_results = _extract_max_results(web_search_tools)
    allowed_domains = _extract_allowed_domains(web_search_tools)
    try:
        search_result = await perform_provider_web_search_with_retries(
            query,
            max_results=max_results,
            allowed_domains=allowed_domains,
            request_id=request_id,
            context="responses-eager",
        )
    except Exception as exc:
        logger.exception("provider[%s] eager web_search failed query=%r", request_id, query)
        search_result = {
            "query": query,
            "total_results": 0,
            "results": [],
            "error": str(exc),
        }

    web_search_items = [build_provider_web_search_item(search_result, include_sources=True)]
    search_results = [search_result]
    updated_messages = _append_system_hint(
        request_messages,
        build_provider_web_search_context(search_results),
    )
    logger.warning(
        "provider[%s] eager web_search completed query=%r results=%d allowed_domains=%s",
        request_id,
        query[:240],
        len(search_result.get("results", []) if isinstance(search_result, dict) else []),
        allowed_domains,
    )
    return updated_messages, web_search_items, search_results


def build_responses_body(
    *,
    model: str,
    content: str,
    tool_calls: list[dict[str, Any]],
    request: ResponsesRequest,
    extra_output_items: list[dict[str, Any]] | None = None,
    annotations: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    output: list[dict[str, Any]] = list(extra_output_items or [])
    if content.strip():
        output.append(
            {
                "id": f"msg_{uuid.uuid4().hex}",
                "type": "message",
                "status": "completed",
                "role": "assistant",
                "content": [
                    {
                        "type": "output_text",
                        "text": content,
                        "annotations": annotations or [],
                    }
                ],
            }
        )

    for tool_call in tool_calls:
        arguments = tool_call.get("arguments", {})
        if not isinstance(arguments, str):
            arguments = json.dumps(arguments if isinstance(arguments, dict) else {}, ensure_ascii=False)
        output.append(
            {
                "id": f"fc_{uuid.uuid4().hex}",
                "type": "function_call",
                "status": "completed",
                "call_id": tool_call.get("id") or f"call_{uuid.uuid4().hex}",
                "name": tool_call.get("name", ""),
                "arguments": arguments,
            }
        )

    if not output:
        output.append(
            {
                "id": f"msg_{uuid.uuid4().hex}",
                "type": "message",
                "status": "completed",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "", "annotations": annotations or []}],
            }
        )

    usage = {
        "input_tokens": 0,
        "input_tokens_details": {"cached_tokens": 0},
        "output_tokens": 0,
        "output_tokens_details": {"reasoning_tokens": 0},
        "total_tokens": 0,
    }
    return {
        "id": f"resp_{uuid.uuid4().hex}",
        "object": "response",
        "created_at": int(time.time()),
        "status": "completed",
        "background": False,
        "error": None,
        "incomplete_details": None,
        "instructions": request.instructions,
        "max_output_tokens": None,
        "model": model,
        "output": output,
        "parallel_tool_calls": True,
        "previous_response_id": None,
        "reasoning": request.reasoning or {"effort": "none"},
        "store": bool(request.store) if request.store is not None else DEFAULT_RESPONSES_STORE,
        "temperature": None,
        "text": {"format": {"type": "text"}},
        "tool_choice": "auto",
        "tools": request.tools or [],
        "top_p": None,
        "truncation": "disabled",
        "usage": usage,
        "user": request.user,
    }


def encode_response_sse(event_type: str, payload: dict[str, Any]) -> str:
    data = json.dumps(payload, ensure_ascii=False)
    return f"event: {event_type}\ndata: {data}\n\n"


async def stream_responses_events(response_body: dict[str, Any]) -> AsyncIterator[str]:
    created_response = dict(response_body)
    created_response["status"] = "in_progress"
    created_response["output"] = []
    yield encode_response_sse(
        "response.created",
        {"type": "response.created", "response": created_response},
    )
    for index, item in enumerate(response_body.get("output", [])):
        yield encode_response_sse(
            "response.output_item.added",
            {"type": "response.output_item.added", "output_index": index, "item": item},
        )
        yield encode_response_sse(
            "response.output_item.done",
            {"type": "response.output_item.done", "output_index": index, "item": item},
        )
    yield encode_response_sse(
        "response.completed",
        {"type": "response.completed", "response": response_body},
    )
    yield "data: [DONE]\n\n"


def _extract_openai_tool_names(tools: list[dict[str, Any]] | None) -> list[str]:
    names: list[str] = []
    for tool in tools or []:
        if not isinstance(tool, dict):
            continue
        function = tool.get("function")
        if not isinstance(function, dict):
            continue
        name = function.get("name")
        if isinstance(name, str) and name:
            names.append(name)
    return names


def _pick_shell_tool_name(tool_names: list[str]) -> str | None:
    for preferred in ("Bash", "bash", "shell", "terminal"):
        for name in tool_names:
            if name == preferred:
                return name
    for name in tool_names:
        if name.lower() in {"bash", "shell", "terminal"}:
            return name
    return None


def _extract_shell_command_from_text(content: str) -> str | None:
    if not isinstance(content, str):
        return None
    stripped = content.strip()
    if not stripped:
        return None

    fenced = re.search(r"```(?:bash|sh|shell)\s*\n(.*?)(?:```|\Z)", stripped, re.IGNORECASE | re.DOTALL)
    if fenced:
        command = fenced.group(1).strip()
        if command:
            return command

    lines = [line.strip() for line in stripped.splitlines() if line.strip()]
    if not lines:
        return None

    normalized = [line for line in lines if line.lower() not in {"copy", "download"}]
    if not normalized:
        return None

    marker_indexes = [idx for idx, line in enumerate(normalized) if line.lower() in {"bash", "sh", "shell"}]
    if marker_indexes:
        start_idx = marker_indexes[-1] + 1
        if start_idx < len(normalized):
            command = "\n".join(normalized[start_idx:]).strip()
            return command or None

    return None


def apply_text_tool_call_fallback(payload: dict[str, Any], tools: list[dict[str, Any]] | None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return payload
    if payload.get("tool_calls"):
        return payload

    shell_tool_name = _pick_shell_tool_name(_extract_openai_tool_names(tools))
    if not shell_tool_name:
        return payload

    command = _extract_shell_command_from_text(payload.get("content", ""))
    if not command:
        return payload

    updated = dict(payload)
    updated["content"] = ""
    updated["tool_calls"] = [
        {
            "id": f"call_{uuid.uuid4().hex[:12]}",
            "name": shell_tool_name,
            "arguments": {
                "command": command,
                "description": "Execute shell command requested by assistant",
            },
        }
    ]
    logger.warning(
        "provider text->tool fallback activated tool=%s command_preview=%r",
        shell_tool_name,
        command[:160],
    )
    return updated


def _normalize_anthropic_tool_use_id(value: Any) -> str:
    raw = value.strip() if isinstance(value, str) else ""
    if raw.startswith("toolu_") and len(raw) > 6:
        return raw
    return f"toolu_{uuid.uuid4().hex}"


def _type_matches_json_schema(value: Any, expected_type: str) -> bool:
    if expected_type == "string":
        return isinstance(value, str)
    if expected_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected_type == "boolean":
        return isinstance(value, bool)
    if expected_type == "object":
        return isinstance(value, dict)
    if expected_type == "array":
        return isinstance(value, list)
    return True


def _coerce_string_for_json_schema_type(value: Any, expected_type: str) -> tuple[Any, bool]:
    if not isinstance(value, str):
        return value, False
    raw = value.strip()
    if expected_type == "integer" and re.fullmatch(r"[+-]?\d+", raw):
        try:
            return int(raw), True
        except ValueError:
            return value, False
    if expected_type == "number" and re.fullmatch(r"[+-]?(?:(?:\d+\.\d*)|(?:\.\d+)|(?:\d+))(?:[eE][+-]?\d+)?", raw):
        try:
            return (float(raw) if re.search(r"[.eE]", raw) else int(raw)), True
        except ValueError:
            return value, False
    if expected_type == "boolean":
        lowered = raw.lower()
        if lowered == "true":
            return True, True
        if lowered == "false":
            return False, True
    return value, False


def _coerce_arguments_for_schema_scalar_types(value: Any, schema: Any) -> tuple[Any, bool]:
    if not isinstance(schema, dict):
        return value, False

    schema_type = schema.get("type")
    if schema_type == "object" and isinstance(value, dict):
        properties = schema.get("properties")
        if not isinstance(properties, dict):
            return value, False
        updated = dict(value)
        rewritten = False
        for key, prop_schema in properties.items():
            if key not in updated or not isinstance(prop_schema, dict):
                continue
            prop_type = prop_schema.get("type")
            prop_types = prop_type if isinstance(prop_type, list) else [prop_type]
            coerced = updated[key]
            coerced_rewritten = False
            for expected_type in prop_types:
                if not isinstance(expected_type, str):
                    continue
                candidate, candidate_rewritten = _coerce_string_for_json_schema_type(coerced, expected_type)
                if candidate_rewritten and _type_matches_json_schema(candidate, expected_type):
                    coerced = candidate
                    coerced_rewritten = True
                    break
            if coerced_rewritten:
                updated[key] = coerced
                rewritten = True
                continue
            nested_value, nested_rewritten = _coerce_arguments_for_schema_scalar_types(updated[key], prop_schema)
            if nested_rewritten:
                updated[key] = nested_value
                rewritten = True
        return updated, rewritten

    if schema_type == "array" and isinstance(value, list):
        item_schema = schema.get("items")
        if not isinstance(item_schema, dict):
            return value, False
        updated_items = []
        rewritten = False
        for item in value:
            nested_value, nested_rewritten = _coerce_arguments_for_schema_scalar_types(item, item_schema)
            updated_items.append(nested_value)
            rewritten = rewritten or nested_rewritten
        return updated_items, rewritten

    for key in ("anyOf", "oneOf"):
        variants = schema.get(key)
        if not isinstance(variants, list):
            continue
        for variant in variants:
            nested_value, nested_rewritten = _coerce_arguments_for_schema_scalar_types(value, variant)
            if nested_rewritten:
                return nested_value, True

    return value, False


def _schema_allows_null(schema: Any) -> bool:
    if not isinstance(schema, dict):
        return False
    schema_type = schema.get("type")
    if schema_type == "null":
        return True
    if isinstance(schema_type, list) and "null" in schema_type:
        return True
    for key in ("anyOf", "oneOf"):
        variants = schema.get(key)
        if isinstance(variants, list) and any(_schema_allows_null(variant) for variant in variants):
            return True
    return False


def _fill_nullable_required_defaults(value: Any, schema: Any) -> tuple[Any, bool]:
    if not isinstance(schema, dict):
        return value, False

    schema_type = schema.get("type")
    if schema_type == "object" and isinstance(value, dict):
        properties = schema.get("properties")
        if not isinstance(properties, dict):
            return value, False
        updated = dict(value)
        rewritten = False
        required = schema.get("required")
        if isinstance(required, list):
            for key in required:
                prop_schema = properties.get(key)
                if key not in updated and _schema_allows_null(prop_schema):
                    updated[key] = None
                    rewritten = True
        for key, prop_schema in properties.items():
            if key not in updated:
                continue
            nested_value, nested_rewritten = _fill_nullable_required_defaults(updated[key], prop_schema)
            if nested_rewritten:
                updated[key] = nested_value
                rewritten = True
        return updated, rewritten

    if schema_type == "array" and isinstance(value, list):
        item_schema = schema.get("items")
        if not isinstance(item_schema, dict):
            return value, False
        updated_items = []
        rewritten = False
        for item in value:
            nested_value, nested_rewritten = _fill_nullable_required_defaults(item, item_schema)
            updated_items.append(nested_value)
            rewritten = rewritten or nested_rewritten
        return updated_items, rewritten

    for key in ("anyOf", "oneOf"):
        variants = schema.get(key)
        if not isinstance(variants, list):
            continue
        for variant in variants:
            nested_value, nested_rewritten = _fill_nullable_required_defaults(value, variant)
            if nested_rewritten:
                return nested_value, True

    return value, False


def _extract_openai_tool_schema_map(tools: list[dict[str, Any]] | None) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for tool in tools or []:
        if not isinstance(tool, dict):
            continue
        function = tool.get("function")
        if not isinstance(function, dict):
            continue
        name = function.get("name")
        if not isinstance(name, str) or not name:
            continue
        params = function.get("parameters")
        if isinstance(params, dict):
            out[name] = params
    return out


def _resolve_declared_tool_name(name: str, schema_map: dict[str, dict[str, Any]]) -> str | None:
    if name in schema_map:
        return name
    alias = TOOL_NAME_ALIASES.get(name.lower())
    if alias and alias in schema_map:
        return alias
    lowered = name.lower()
    by_lower = [declared for declared in schema_map if declared.lower() == lowered]
    if len(by_lower) == 1:
        return by_lower[0]
    compact = re.sub(r"[-_\\s]+", "", lowered)
    alias_compact = TOOL_NAME_ALIASES.get(compact)
    if alias_compact and alias_compact in schema_map:
        return alias_compact
    by_compact = [declared for declared in schema_map if re.sub(r"[-_\\s]+", "", declared.lower()) == compact]
    if len(by_compact) == 1:
        return by_compact[0]
    return None


def _is_windows_compat_enabled() -> bool:
    return os.name == "nt" or os.environ.get(WINDOWS_COMPAT_ENV, "0").strip() == "1"


def _should_force_windows_path_style() -> bool:
    return os.name == "nt" or os.environ.get(FORCE_WINDOWS_PATH_ENV, "0").strip() == "1"


def _normalize_tool_name_key(name: str) -> str:
    return re.sub(r"[-_\s]+", "", name.lower())


def _pick_first_non_empty(mapping: dict[str, Any], candidate_keys: tuple[str, ...]) -> Any:
    for key in candidate_keys:
        value = mapping.get(key)
        if isinstance(value, str):
            if value.strip():
                return value
            continue
        if value is not None:
            return value
    return None


def _normalize_windows_path_value(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    raw = value.strip()
    if not raw:
        return value
    # Keep URL-like values intact.
    if "://" in raw:
        return value
    # Normalize slash style for obvious Windows absolute/UNC paths.
    if re.match(r"^[A-Za-z]:[\\/]", raw) or raw.startswith("\\\\"):
        return raw.replace("/", "\\")
    return value


def _coerce_tool_arguments_for_compatibility(
    declared_tool_name: str,
    arguments: dict[str, Any],
    schema: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    updated = dict(arguments)
    rewritten = False
    key = _normalize_tool_name_key(declared_tool_name)

    if key in {"bash"}:
        if "command" not in updated:
            candidate = _pick_first_non_empty(updated, ("cmd", "script", "bash_command", "shell_command"))
            if isinstance(candidate, str) and candidate.strip():
                updated["command"] = candidate
                rewritten = True

    if key in {"execcommand"}:
        if "cmd" not in updated:
            candidate = _pick_first_non_empty(updated, ("command", "script", "shell_command"))
            if isinstance(candidate, str) and candidate.strip():
                updated["cmd"] = candidate
                rewritten = True

    if key in {"ls", "listdir", "listdirs"}:
        if "path" not in updated:
            candidate = _pick_first_non_empty(updated, ("directory", "dir", "target", "cwd", "workdir"))
            if isinstance(candidate, str) and candidate.strip():
                updated["path"] = candidate
                rewritten = True

    if key in {"readfile", "writefile"}:
        if "path" not in updated:
            candidate = _pick_first_non_empty(updated, ("file_path", "filepath", "file", "filename", "target"))
            if isinstance(candidate, str) and candidate.strip():
                updated["path"] = candidate
                rewritten = True
        if key == "writefile" and "content" not in updated:
            candidate = _pick_first_non_empty(updated, ("text", "contents", "body", "data"))
            if isinstance(candidate, str):
                updated["content"] = candidate
                rewritten = True

    if _should_force_windows_path_style():
        properties = schema.get("properties")
        if isinstance(properties, dict):
            for prop_key in WINDOWS_PATH_ARG_KEYS:
                if prop_key in updated and prop_key in properties:
                    normalized = _normalize_windows_path_value(updated[prop_key])
                    if normalized != updated[prop_key]:
                        updated[prop_key] = normalized
                        rewritten = True

    return updated, rewritten


def validate_tool_calls_against_schemas(payload: dict[str, Any], tools: list[dict[str, Any]] | None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return payload

    raw_tool_calls = payload.get("tool_calls")
    if not isinstance(raw_tool_calls, list) or not raw_tool_calls:
        return payload

    schema_map = _extract_openai_tool_schema_map(tools)
    if not schema_map:
        logger.warning("provider dropped tool_calls because request declared no tools")
        updated = dict(payload)
        updated["tool_calls"] = []
        return updated

    valid_calls: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    rewritten = False

    for tool_call in raw_tool_calls:
        if not isinstance(tool_call, dict):
            continue
        name = tool_call.get("name")
        arguments = tool_call.get("arguments")
        if not isinstance(name, str) or not name or not isinstance(arguments, dict):
            dropped.append({"name": name, "reason": "invalid_name_or_arguments_type"})
            continue

        resolved_name = _resolve_declared_tool_name(name, schema_map)
        schema = schema_map.get(resolved_name or "")
        if not isinstance(schema, dict):
            dropped.append({"name": name, "reason": "tool_name_not_declared"})
            continue

        required = schema.get("required")
        properties = schema.get("properties")

        if _is_windows_compat_enabled():
            normalized_arguments, compat_rewritten = _coerce_tool_arguments_for_compatibility(
                resolved_name or name,
                arguments,
                schema,
            )
            if compat_rewritten:
                tool_call = dict(tool_call)
                tool_call["arguments"] = normalized_arguments
                arguments = normalized_arguments
                rewritten = True

        if isinstance(properties, dict):
            sanitized_arguments = {key: value for key, value in arguments.items() if key in properties}
            if sanitized_arguments.keys() != arguments.keys():
                tool_call = dict(tool_call)
                tool_call["arguments"] = sanitized_arguments
                arguments = sanitized_arguments
                rewritten = True

        normalized_arguments, nullable_rewritten = _fill_nullable_required_defaults(arguments, schema)
        if nullable_rewritten and isinstance(normalized_arguments, dict):
            tool_call = dict(tool_call)
            tool_call["arguments"] = normalized_arguments
            arguments = normalized_arguments
            rewritten = True

        typed_arguments, typed_rewritten = _coerce_arguments_for_schema_scalar_types(arguments, schema)
        if typed_rewritten and isinstance(typed_arguments, dict):
            tool_call = dict(tool_call)
            tool_call["arguments"] = typed_arguments
            arguments = typed_arguments
            rewritten = True

        if isinstance(required, list):
            missing = [key for key in required if key not in arguments]
            if missing:
                dropped.append({"name": name, "reason": "missing_required", "missing": missing})
                continue

        if isinstance(properties, dict):
            type_mismatches: list[str] = []
            for key, prop_schema in properties.items():
                if key not in arguments or not isinstance(prop_schema, dict):
                    continue
                expected_type = prop_schema.get("type")
                if isinstance(expected_type, str) and not _type_matches_json_schema(arguments.get(key), expected_type):
                    type_mismatches.append(f"{key}:{expected_type}")
            if type_mismatches:
                dropped.append({"name": name, "reason": "type_mismatch", "fields": type_mismatches})
                continue

        try:
            jsonschema_validate(instance=arguments, schema=schema)
        except JsonSchemaValidationError as exc:
            dropped.append(
                {
                    "name": name,
                    "resolved_name": resolved_name,
                    "reason": "jsonschema_validation_error",
                    "message": str(exc).split("\n")[0][:300],
                }
            )
            continue

        if resolved_name and resolved_name != name:
            tool_call = dict(tool_call)
            tool_call["name"] = resolved_name
            rewritten = True
        valid_calls.append(tool_call)

    if not dropped and not rewritten:
        return payload

    if dropped:
        logger.warning("provider dropped invalid tool_calls=%s", dropped)
    updated = dict(payload)
    updated["tool_calls"] = valid_calls
    return updated


def run_bridge_with_spec(
    bridge: DeepSeekWebBridge,
    *,
    spec: ModelSpec,
    operation,
):
    original_force_new_chat = bridge.force_new_chat
    original_sticky_marker = bridge.sticky_marker
    original_sticky_reanchor_messages = bridge.sticky_reanchor_messages
    original_session_state_path = bridge.session_state_path
    original_reuse_persisted_chat = bridge.reuse_persisted_chat
    original_fast_new_chat = bridge.fast_new_chat
    try:
        bridge.force_new_chat = spec.force_new_chat
        bridge.sticky_marker = spec.sticky_marker
        bridge.sticky_reanchor_messages = spec.sticky_reanchor_messages
        bridge.session_state_path = spec.session_state_path
        bridge.reuse_persisted_chat = spec.reuse_persisted_chat
        bridge.fast_new_chat = spec.fast_new_chat
        return operation()
    finally:
        bridge.force_new_chat = original_force_new_chat
        bridge.sticky_marker = original_sticky_marker
        bridge.sticky_reanchor_messages = original_sticky_reanchor_messages
        bridge.session_state_path = original_session_state_path
        bridge.reuse_persisted_chat = original_reuse_persisted_chat
        bridge.fast_new_chat = original_fast_new_chat


def provider_capabilities_payload() -> dict[str, Any]:
    return {
        "interfaces": {
            "openai_chat_completions": {
                "path": "/v1/chat/completions",
                "method": "POST",
                "output_protocols": ["plain", "openai"],
                "default_output_protocol": "openai",
                "plain_output_protocol": {
                    "output_protocol": "plain",
                    "extra_body": {"output_protocol": "plain"},
                },
            },
            "openai_responses": {
                "path": "/v1/responses",
                "method": "POST",
                "output_protocol": "openai",
                "tools": ["web_search", "web_search_preview", "web_fetch"],
            },
            "direct_web_search": {
                "path": "/v1/web-search",
                "method": "POST",
                "description": "Provider-side direct web search. Does not call the web LLM or use any output protocol.",
            },
            "direct_web_fetch": {
                "path": "/v1/web-fetch",
                "method": "POST",
                "description": "Provider-side direct URL fetch and trafilatura cleanup. Does not call the web LLM or use any output protocol.",
            },
            "anthropic_messages": {
                "path": "/v1/messages",
                "method": "POST",
                "output_protocol": "anthropic",
            },
        },
        "output_protocols": {
            "plain": {
                "description": "Plain bash-agent output for Agent Qt local runner. Use /v1/chat/completions with output_protocol=plain.",
                "endpoint": "/v1/chat/completions",
            },
            "openai": {
                "description": "OpenAI-compatible JSON/tool_calls bridge output. Used by /v1/chat/completions and /v1/responses.",
                "endpoints": ["/v1/chat/completions", "/v1/responses"],
            },
            "anthropic": {
                "description": "Anthropic-compatible tool_use bridge output. Used by /v1/messages.",
                "endpoint": "/v1/messages",
            },
        },
        "defaults": {
            "chat_completions_output_protocol": "openai",
            "responses_output_protocol": "openai",
            "messages_output_protocol": "anthropic",
            "app_internal_web_research_path": "/v1/web-search",
            "web_ui_search_enabled": False,
            "use_system_proxy": PROVIDER_USE_SYSTEM_PROXY,
            "web_search_backends": list(PROVIDER_WEB_SEARCH_BACKENDS),
        },
    }


def apply_provider_proxy_mode(use_system_proxy: bool, proxy_env: dict[str, str] | None = None) -> dict[str, Any]:
    global PROVIDER_USE_SYSTEM_PROXY, PROVIDER_WEB_SEARCH_BACKENDS

    PROVIDER_USE_SYSTEM_PROXY = bool(use_system_proxy)
    os.environ["DEEPSEEK_PROVIDER_USE_SYSTEM_PROXY"] = "1" if PROVIDER_USE_SYSTEM_PROXY else "0"
    os.environ["AGENT_QT_USE_SYSTEM_PROXY"] = "1" if PROVIDER_USE_SYSTEM_PROXY else "0"
    os.environ["DEEPSEEK_WEB_DISABLE_PROXY"] = "0" if PROVIDER_USE_SYSTEM_PROXY else "1"
    deepseek_web_bridge_module.DISABLE_SYSTEM_PROXY = not PROVIDER_USE_SYSTEM_PROXY
    if PROVIDER_USE_SYSTEM_PROXY:
        for key, value in (proxy_env or {}).items():
            if key in PROVIDER_PROXY_ENV_KEYS and str(value or "").strip():
                os.environ[key] = str(value).strip()
    else:
        for key in PROVIDER_PROXY_ENV_KEYS:
            os.environ.pop(key, None)
    os.environ.setdefault("NO_PROXY", "localhost,127.0.0.1,::1")
    os.environ.setdefault("no_proxy", os.environ["NO_PROXY"])
    if not os.environ.get("DEEPSEEK_PROVIDER_WEB_SEARCH_BACKENDS", "").strip():
        backends_raw = (
            "so360,sogou,sm,duckduckgo,bing,google,brave"
            if PROVIDER_USE_SYSTEM_PROXY
            else "so360,sogou,sm,duckduckgo"
        )
        PROVIDER_WEB_SEARCH_BACKENDS = tuple(item.strip() for item in backends_raw.split(",") if item.strip())
    close_bridges()
    logger.warning(
        "Provider proxy mode updated use_system_proxy=%s search_backends=%s proxy_env_keys=%s",
        PROVIDER_USE_SYSTEM_PROXY,
        ",".join(PROVIDER_WEB_SEARCH_BACKENDS),
        ",".join(sorted(_proxy_env_snapshot())),
    )
    return {
        "use_system_proxy": PROVIDER_USE_SYSTEM_PROXY,
        "web_search_backends": list(PROVIDER_WEB_SEARCH_BACKENDS),
        "proxy_env_keys": sorted(_proxy_env_snapshot()),
    }


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"status": "ok", "capabilities": provider_capabilities_payload()}


@app.get("/status")
async def provider_status() -> dict[str, Any]:
    return {
        "status": "ok",
        "default_model": DEFAULT_MODEL_ID,
        "models": list(MODEL_SPECS.keys()),
        "capabilities": provider_capabilities_payload(),
        "proxy": {
            "use_system_proxy": PROVIDER_USE_SYSTEM_PROXY,
            "web_search_backends": list(PROVIDER_WEB_SEARCH_BACKENDS),
            "proxy_env_keys": sorted(_proxy_env_snapshot()),
        },
    }


@app.post("/debug/proxy-mode")
async def debug_proxy_mode(request: ProviderProxyModeRequest) -> dict[str, Any]:
    return {"status": "ok", **apply_provider_proxy_mode(request.use_system_proxy, request.proxy_env)}


@app.post("/debug/open-login")
async def open_login(model: str = DEFAULT_MODEL_ID) -> dict[str, Any]:
    spec, pool = get_bridge_pool(model)
    request_id = uuid.uuid4().hex[:8]
    result, _slot_timing = await run_on_bridge_slot(
        pool,
        spec=spec,
        request_id=request_id,
        route="/debug/open-login",
        operation=lambda bridge, _slot_spec: bridge.open_login_page(),
        prewarm_after=False,
    )
    return {"model": spec.model_id, **result}


@app.get("/debug/response-preview")
async def response_preview(model: str = DEFAULT_MODEL_ID, user: str | None = None) -> dict[str, Any]:
    spec, pool = get_bridge_pool(model, user)
    preview = pool.response_preview()
    return {"model": spec.model_id, **preview}


@app.post("/debug/cancel-generations")
async def cancel_generations(model: str | None = None, user: str | None = None) -> dict[str, Any]:
    cancelled_slots = 0
    if model:
        spec, pool = get_bridge_pool(model, user)
        cancelled_slots = pool.request_cancel_generations()
        return {"model": spec.model_id, "cancelled_slots": cancelled_slots}
    for pool in _bridge_pools.values():
        cancelled_slots += pool.request_cancel_generations()
    return {"model": None, "cancelled_slots": cancelled_slots}


@app.get("/v1/models")
async def list_models() -> dict[str, Any]:
    if not is_interface_enabled("openai"):
        raise HTTPException(status_code=404, detail="OpenAI-compatible endpoints are disabled by interface mode.")
    now = int(time.time())
    return {
        "object": "list",
        "data": [
            {
                "id": spec.model_id,
                "object": "model",
                "created": now,
                "owned_by": "local",
            }
            for spec in MODEL_SPECS.values()
        ],
    }


@app.post("/v1/web-search", response_model=None)
async def direct_web_search(request: DirectWebSearchRequest) -> dict[str, Any]:
    if not is_interface_enabled("openai"):
        raise HTTPException(status_code=404, detail="OpenAI-compatible endpoints are disabled by interface mode.")
    max_results = max(1, min(int(request.max_results or PROVIDER_WEB_SEARCH_MAX_RESULTS), PROVIDER_WEB_SEARCH_MAX_RESULTS))
    allowed_domains = [
        str(domain or "").strip().lower().strip(".")
        for domain in (request.allowed_domains or [])
        if str(domain or "").strip()
    ]
    try:
        result = await perform_provider_web_search_with_retries(
            request.query,
            max_results=max_results,
            allowed_domains=allowed_domains,
            request_id=uuid.uuid4().hex[:8],
            context="direct-web-search",
        )
    except Exception as exc:
        logger.exception("provider direct web-search failed query=%r", request.query)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {
        "object": "web_search.result",
        "status": "completed",
        **result,
    }


@app.post("/v1/web-fetch", response_model=None)
async def direct_web_fetch(request: DirectWebFetchRequest) -> dict[str, Any]:
    if not is_interface_enabled("openai"):
        raise HTTPException(status_code=404, detail="OpenAI-compatible endpoints are disabled by interface mode.")
    try:
        result = await asyncio.to_thread(
            perform_provider_web_fetch,
            request.url,
            include_html=bool(request.include_html),
            max_bytes=max(50_000, min(int(request.max_bytes or PROVIDER_WEB_FETCH_MAX_BYTES), PROVIDER_WEB_FETCH_MAX_BYTES)),
        )
    except Exception as exc:
        logger.exception("provider direct web-fetch failed url=%r", request.url)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {
        "object": "web_fetch.result",
        "status": "completed",
        **result,
    }


@app.post("/v1/responses", response_model=None)
async def responses(request: ResponsesRequest):
    if not is_interface_enabled("openai"):
        raise HTTPException(status_code=404, detail="OpenAI-compatible endpoints are disabled by interface mode.")

    spec, pool = get_bridge_pool(request.model, request.user)
    resolved_model = spec.model_id
    request_id = uuid.uuid4().hex[:8]
    request_tools, request_web_search_tools = split_responses_tools(request.tools)
    eager_web_search = bool(request_web_search_tools and PROVIDER_WEB_SEARCH_EAGER)
    bridge_tools = list(request_tools)
    if request_web_search_tools and not eager_web_search:
        bridge_tools.append(build_provider_web_search_tool(request_web_search_tools))
        bridge_tools.append(build_provider_web_fetch_tool())
    request_messages = responses_input_to_chat_messages(
        request.input,
        request.instructions,
        has_tools=bool(bridge_tools),
    )
    web_search_items: list[dict[str, Any]] = []
    search_results: list[dict[str, Any]] = []
    if request_web_search_tools and eager_web_search:
        request_messages, web_search_items, search_results = await resolve_provider_web_search_eager(
            request_messages=request_messages,
            web_search_tools=request_web_search_tools,
            request_id=request_id,
        )
    elif request_web_search_tools:
        request_messages = _append_system_hint(request_messages, PROVIDER_WEB_SEARCH_TOOL_HINT)
    if not request_messages:
        request_messages = [{"role": "user", "content": ""}]
    request_thinking_enabled = resolve_effective_thinking_enabled(
        resolve_responses_thinking_enabled(request),
        spec=spec,
    )
    request_expert_mode_enabled = resolve_effective_expert_mode_enabled(
        resolve_responses_expert_mode_enabled(request),
        spec=spec,
    )
    logger.warning(
        "provider[%s] /v1/responses start model=%s stream=%s messages=%d tools=%d thinking_enabled=%s web_search_tools=%d eager_web_search=%s",
        request_id,
        resolved_model,
        request.stream,
        len(request_messages),
        len(bridge_tools),
        request_thinking_enabled,
        len(request_web_search_tools),
        eager_web_search,
    )

    try:
        payload, _slot_timing = await run_on_bridge_slot(
            pool,
            spec=spec,
            request_id=request_id,
            route="/v1/responses",
            operation=lambda bridge, slot_spec: bridge_call_with_spec(
                bridge,
                spec=slot_spec,
                messages=request_messages,
                tools=bridge_tools,
                thinking_enabled=request_thinking_enabled,
                expert_mode_enabled=request_expert_mode_enabled,
                output_protocol="openai",
            ),
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("provider[%s] /v1/responses bridge.call failed", request_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    raw_text = payload.get("raw_text", "")
    logger.warning(
        "provider[%s] /v1/responses bridge.call done content_chars=%d raw_text_chars=%d tool_calls=%s parse_error=%s retries=%s",
        request_id,
        len(payload.get("content", "") or ""),
        len(raw_text) if isinstance(raw_text, str) else 0,
        summarize_tool_calls(payload.get("tool_calls")),
        payload.get("parse_error"),
        payload.get("protocol_retry_count", 0),
    )

    raise_if_web_busy_payload(payload, request_id, "/v1/responses")
    if request_web_search_tools and not eager_web_search:
        payload, web_search_items, search_results = await resolve_provider_web_search(
            payload=payload,
            pool=pool,
            spec=spec,
            request_id=request_id,
            request_messages=request_messages,
            bridge_tools=bridge_tools,
            request_thinking_enabled=request_thinking_enabled,
            request_expert_mode_enabled=request_expert_mode_enabled,
            web_search_tools=request_web_search_tools,
        )
    else:
        payload = _filter_internal_web_search_calls(payload)
    payload = apply_text_tool_call_fallback(payload, request_tools)
    payload = validate_tool_calls_against_schemas(payload, request_tools)
    message, tool_calls, _ = build_openai_assistant_message(payload)
    response_text, response_annotations = append_provider_search_citations(
        message.get("content") or "",
        search_results,
    )
    response_body = build_responses_body(
        model=resolved_model,
        content=response_text,
        tool_calls=tool_calls,
        request=request,
        extra_output_items=web_search_items,
        annotations=response_annotations,
    )
    logger.warning(
        "provider[%s] /v1/responses return ready output_items=%d response_chars=%d",
        request_id,
        len(response_body.get("output", [])),
        len(json.dumps(response_body, ensure_ascii=False)),
    )

    if request.stream:
        return StreamingResponse(
            stream_responses_events(response_body),
            media_type="text/event-stream",
        )

    return response_body


@app.post("/v1/chat/completions", response_model=None)
async def chat_completions(request: ChatCompletionRequest):
    if not is_interface_enabled("openai"):
        raise HTTPException(status_code=404, detail="OpenAI-compatible endpoints are disabled by interface mode.")

    spec, pool = get_bridge_pool(request.model, request.user)
    resolved_model = spec.model_id
    request_id = uuid.uuid4().hex[:8]
    request_messages = [message.model_dump(exclude_none=True) for message in request.messages]
    request_tools = request.tools or []
    request_thinking_enabled = resolve_effective_thinking_enabled(
        resolve_request_thinking_enabled(request),
        spec=spec,
    )
    request_expert_mode_enabled = resolve_effective_expert_mode_enabled(
        resolve_request_expert_mode_enabled(request),
        spec=spec,
    )
    request_output_protocol = resolve_request_output_protocol(request)
    logger.warning(
        "provider[%s] /v1 start model=%s stream=%s messages=%d tools=%d thinking_enabled=%s output_protocol=%s",
        request_id,
        resolved_model,
        request.stream,
        len(request_messages),
        len(request_tools),
        request_thinking_enabled,
        request_output_protocol,
    )

    try:
        payload, _slot_timing = await run_on_bridge_slot(
            pool,
            spec=spec,
            request_id=request_id,
            route="/v1/chat/completions",
            operation=lambda bridge, slot_spec: bridge_call_with_spec(
                bridge,
                spec=slot_spec,
                messages=request_messages,
                tools=request_tools,
                thinking_enabled=request_thinking_enabled,
                expert_mode_enabled=request_expert_mode_enabled,
                output_protocol=request_output_protocol,
            ),
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("provider[%s] /v1 bridge.call failed", request_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    raw_text = payload.get("raw_text", "")
    logger.warning(
        "provider[%s] /v1 bridge.call done content_chars=%d raw_text_chars=%d tool_calls=%s parse_error=%s retries=%s",
        request_id,
        len(payload.get("content", "") or ""),
        len(raw_text) if isinstance(raw_text, str) else 0,
        summarize_tool_calls(payload.get("tool_calls")),
        payload.get("parse_error"),
        payload.get("protocol_retry_count", 0),
    )

    raise_if_web_busy_payload(payload, request_id, "/v1/chat/completions")
    payload = apply_text_tool_call_fallback(payload, request_tools)
    payload = validate_tool_calls_against_schemas(payload, request_tools)
    if request_output_protocol == "plain" and _looks_like_prompt_replay_text(str(payload.get("content") or "")):
        logger.warning("provider[%s] /v1 suppressed plain prompt replay response.", request_id)
        payload = {**payload, "content": "", "tool_calls": [], "parse_error": "prompt_replay"}
    message, tool_calls, finish_reason = build_openai_assistant_message(payload)
    if tool_calls:
        logger.warning(
            "provider[%s] /v1 tool_calls encoded summaries=%s",
            request_id,
            [
                {
                    "index": index,
                    "id": tool_call["id"],
                    "name": tool_call["function"]["name"],
                    "argument_chars": len(tool_call["function"]["arguments"]),
                }
                for index, tool_call in enumerate(message["tool_calls"])
            ],
        )

    if request.stream:
        logger.warning("provider[%s] /v1 returning stream finish_reason=%s", request_id, finish_reason)
        return StreamingResponse(
            stream_chat_completion_chunks(
                model=resolved_model,
                content=message.get("content", ""),
                tool_calls=message.get("tool_calls"),
                finish_reason=finish_reason,
                include_usage=bool(request.stream_options and request.stream_options.include_usage),
            ),
            media_type="text/event-stream",
        )

    response_body = {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": resolved_model,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": finish_reason,
                "logprobs": None,
            }
        ],
        "system_fingerprint": None,
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        },
    }
    logger.warning(
        "provider[%s] /v1 return ready finish_reason=%s response_chars=%d",
        request_id,
        finish_reason,
        len(json.dumps(response_body, ensure_ascii=False)),
    )
    return response_body


@app.post("/v1/messages", response_model=None)
async def anthropic_messages(request: AnthropicMessageRequest):
    if not is_interface_enabled("anthropic"):
        raise HTTPException(status_code=404, detail="Anthropic-compatible endpoints are disabled by interface mode.")

    spec, pool = get_bridge_pool(request.model, request.user)
    resolved_model = spec.model_id
    request_id = uuid.uuid4().hex[:8]
    bridge_messages = anthropic_messages_to_bridge_payload(request)
    request_tools = anthropic_tools_to_openai_tools(request.tools)
    request_thinking_enabled = resolve_effective_thinking_enabled(
        request.thinking_enabled,
        spec=spec,
    )
    request_expert_mode_enabled = resolve_effective_expert_mode_enabled(
        request.expert_mode_enabled,
        spec=spec,
    )

    logger.warning(
        "provider[%s] /v1/messages start model=%s stream=%s messages=%d tools=%d thinking_enabled=%s",
        request_id,
        resolved_model,
        request.stream,
        len(bridge_messages),
        len(request_tools),
        request_thinking_enabled,
    )

    try:
        payload, _slot_timing = await run_on_bridge_slot(
            pool,
            spec=spec,
            request_id=request_id,
            route="/v1/messages",
            operation=lambda bridge, slot_spec: bridge_call_with_spec(
                bridge,
                spec=slot_spec,
                messages=bridge_messages,
                tools=request_tools,
                thinking_enabled=request_thinking_enabled,
                expert_mode_enabled=request_expert_mode_enabled,
                output_protocol="anthropic",
            ),
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("provider[%s] /v1/messages bridge.call failed", request_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    raise_if_web_busy_payload(payload, request_id, "/v1/messages")
    payload = apply_text_tool_call_fallback(payload, request_tools)
    payload = validate_tool_calls_against_schemas(payload, request_tools)
    message, tool_calls, _ = build_openai_assistant_message(payload)
    if len(tool_calls) > 1:
        logger.warning(
            "provider[%s] /v1/messages reducing parallel tool_calls from %d to 1 for compatibility",
            request_id,
            len(tool_calls),
        )
        tool_calls = [tool_calls[0]]
    if tool_calls:
        logger.warning(
            "provider[%s] /v1/messages tool_calls accepted summaries=%s",
            request_id,
            summarize_tool_calls(tool_calls),
        )

    anthropic_content: list[dict[str, Any]] = []
    message_text = message.get("content")
    if isinstance(message_text, str) and message_text.strip():
        anthropic_content.append({"type": "text", "text": message_text})

    seen_tool_use_ids: set[str] = set()
    for tool_call in tool_calls:
        arguments = tool_call.get("arguments", {})
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except Exception:
                arguments = {}
        if not isinstance(arguments, dict):
            arguments = {}
        normalized_tool_use_id = _normalize_anthropic_tool_use_id(tool_call.get("id"))
        while normalized_tool_use_id in seen_tool_use_ids:
            normalized_tool_use_id = f"toolu_{uuid.uuid4().hex}"
        seen_tool_use_ids.add(normalized_tool_use_id)
        anthropic_content.append(
            {
                "type": "tool_use",
                "id": normalized_tool_use_id,
                "name": tool_call.get("name"),
                "input": arguments,
            }
        )

    if not anthropic_content:
        anthropic_content = [{"type": "text", "text": ""}]

    stop_reason = "tool_use" if tool_calls else "end_turn"
    response_body = {
        "id": f"msg_{uuid.uuid4().hex}",
        "type": "message",
        "role": "assistant",
        "model": resolved_model,
        "content": anthropic_content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 0, "output_tokens": 0},
    }

    if request.stream:
        return StreamingResponse(
            stream_anthropic_message_events(response_body),
            media_type="text/event-stream",
        )

    return response_body


@app.post("/v1/messages/count_tokens", response_model=None)
async def anthropic_count_tokens(request: AnthropicCountTokensRequest):
    if not is_interface_enabled("anthropic"):
        raise HTTPException(status_code=404, detail="Anthropic-compatible endpoints are disabled by interface mode.")
    _ = request
    return {"input_tokens": 0}


@app.post("/debug/chat-timings")
async def debug_chat_timings(request: DebugTraceRequest) -> dict[str, Any]:
    spec, pool = get_bridge_pool(request.model)
    resolved_model = spec.model_id
    request_id = uuid.uuid4().hex[:8]
    request_messages = [message.model_dump(exclude_none=True) for message in request.messages]
    request_tools = request.tools or []
    request_thinking_enabled = resolve_effective_thinking_enabled(
        resolve_request_thinking_enabled(request),
        spec=spec,
    )
    request_expert_mode_enabled = resolve_effective_expert_mode_enabled(
        resolve_request_expert_mode_enabled(request),
        spec=spec,
    )
    logger.warning(
        "provider[%s] /debug start model=%s messages=%d tools=%d include_payload=%s thinking_enabled=%s",
        request_id,
        resolved_model,
        len(request_messages),
        len(request_tools),
        request.include_payload,
        request_thinking_enabled,
    )

    try:
        request_started_at = time.perf_counter()
        payload, slot_timing = await run_on_bridge_slot(
            pool,
            spec=spec,
            request_id=request_id,
            route="/debug/chat-timings",
            operation=lambda bridge, slot_spec: bridge_call_with_spec(
                bridge,
                spec=slot_spec,
                messages=request_messages,
                tools=request_tools,
                thinking_enabled=request_thinking_enabled,
                expert_mode_enabled=request_expert_mode_enabled,
                include_debug=True,
                output_protocol="openai",
            ),
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("provider[%s] /debug bridge.call failed", request_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    logger.warning(
        "provider[%s] /debug bridge.call done content_chars=%d raw_text_chars=%d tool_calls=%s",
        request_id,
        len(payload.get("content", "") or ""),
        len(payload.get("raw_text", "") or ""),
        summarize_tool_calls(payload.get("tool_calls")),
    )

    timing = (payload.get("debug") or {}).get("timing", {})
    internal_total_ms = timing.get("total_ms", 0) if isinstance(timing, dict) else 0
    route_total_ms = int((time.perf_counter() - request_started_at) * 1000)
    provider_overhead_ms = slot_timing["bridge_exec_ms"] - int(internal_total_ms or 0)
    response: dict[str, Any] = {
        "model": resolved_model,
        "timing": timing,
        "route_timing": {
            **slot_timing,
            "route_total_ms": route_total_ms,
            "provider_overhead_ms": max(0, provider_overhead_ms),
        },
    }
    if request.include_payload:
        response["payload"] = {
            "content": payload.get("content", ""),
            "tool_calls": payload.get("tool_calls", []),
            "raw_text": payload.get("raw_text", ""),
        }
    logger.warning(
        "provider[%s] /debug return ready response_chars=%d include_payload=%s",
        request_id,
        len(json.dumps(response, ensure_ascii=False)),
        request.include_payload,
    )
    return response


@app.post("/debug/thinking-mode")
async def debug_thinking_mode(request: ThinkingModeRequest) -> dict[str, Any]:
    spec, pool = get_bridge_pool(request.model, request.user)
    resolved_model = spec.model_id
    request_id = uuid.uuid4().hex[:8]
    requested = request.thinking_enabled
    effective_requested = resolve_effective_thinking_enabled(requested, spec=spec)
    logger.warning(
        "provider[%s] /debug/thinking-mode start model=%s requested=%s visible=%s",
        request_id,
        resolved_model,
        effective_requested,
        request.visible,
    )

    try:
        result, _slot_timing = await run_on_bridge_slot(
            pool,
            spec=spec,
            request_id=request_id,
            route="/debug/thinking-mode",
            operation=lambda bridge, slot_spec: run_bridge_with_spec(
                bridge,
                spec=slot_spec,
                operation=lambda: bridge.debug_sync_thinking_mode(
                    effective_requested,
                    visible=request.visible,
                ),
            ),
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("provider[%s] /debug/thinking-mode failed", request_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    response = {
        "model": resolved_model,
        **result,
    }
    logger.warning(
        "provider[%s] /debug/thinking-mode done applied=%s current=%s changed=%s",
        request_id,
        effective_requested,
        result.get("after", {}).get("thinking_enabled"),
        result.get("changed"),
    )
    return response


@app.post("/debug/expert-mode")
async def debug_expert_mode(request: ExpertModeRequest) -> dict[str, Any]:
    spec, pool = get_bridge_pool(request.model, request.user)
    resolved_model = spec.model_id
    request_id = uuid.uuid4().hex[:8]
    requested = request.expert_mode_enabled
    effective_requested = resolve_effective_expert_mode_enabled(requested, spec=spec)
    logger.warning(
        "provider[%s] /debug/expert-mode start model=%s requested=%s visible=%s",
        request_id,
        resolved_model,
        effective_requested,
        request.visible,
    )

    try:
        result, _slot_timing = await run_on_bridge_slot(
            pool,
            spec=spec,
            request_id=request_id,
            route="/debug/expert-mode",
            operation=lambda bridge, slot_spec: run_bridge_with_spec(
                bridge,
                spec=slot_spec,
                operation=lambda: bridge.debug_sync_expert_mode(
                    effective_requested,
                    visible=request.visible,
                ),
            ),
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("provider[%s] /debug/expert-mode failed", request_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    response = {
        "model": resolved_model,
        **result,
    }
    logger.warning(
        "provider[%s] /debug/expert-mode done applied=%s current=%s changed=%s",
        request_id,
        effective_requested,
        result.get("after", {}).get("expert_mode_enabled"),
        result.get("changed"),
    )
    return response


def stream_chat_completion_chunks(
    *,
    model: str,
    content: str,
    tool_calls: list[dict[str, Any]] | None,
    finish_reason: str,
    include_usage: bool = False,
) -> Iterator[str]:
    chunk_id = f"chatcmpl-{uuid.uuid4().hex}"
    created = int(time.time())

    initial_chunk = {
        "id": chunk_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": {"role": "assistant"},
                "finish_reason": None,
                "logprobs": None,
            }
        ],
        "system_fingerprint": None,
    }
    yield f"data: {json.dumps(initial_chunk, ensure_ascii=False)}\n\n"

    if tool_calls:
        for index, tool_call in enumerate(tool_calls):
            tool_chunk = {
                "id": chunk_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": index,
                                    "id": tool_call.get("id"),
                                    "type": "function",
                                    "function": {
                                        "name": (tool_call.get("function") or {}).get("name"),
                                        "arguments": (tool_call.get("function") or {}).get("arguments", ""),
                                    },
                                }
                            ]
                        },
                        "finish_reason": None,
                        "logprobs": None,
                    }
                ],
                "system_fingerprint": None,
            }
            yield f"data: {json.dumps(tool_chunk, ensure_ascii=False)}\n\n"
    elif content:
        content_chunk = {
            "id": chunk_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": content},
                    "finish_reason": None,
                    "logprobs": None,
                }
            ],
            "system_fingerprint": None,
        }
        yield f"data: {json.dumps(content_chunk, ensure_ascii=False)}\n\n"

    final_chunk = {
        "id": chunk_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": {},
                "finish_reason": finish_reason,
                "logprobs": None,
            }
        ],
        "system_fingerprint": None,
    }
    yield f"data: {json.dumps(final_chunk, ensure_ascii=False)}\n\n"

    if include_usage:
        usage_chunk = {
            "id": chunk_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [],
            "usage": {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
            },
            "system_fingerprint": None,
        }
        yield f"data: {json.dumps(usage_chunk, ensure_ascii=False)}\n\n"

    yield "data: [DONE]\n\n"


def stream_anthropic_message_events(response_body: dict[str, Any]) -> Iterator[str]:
    def emit(event: str, data: dict[str, Any]) -> str:
        return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

    content_blocks = response_body.get("content") or []
    message_stub = {
        "id": response_body.get("id"),
        "type": "message",
        "role": "assistant",
        "model": response_body.get("model"),
        "content": [],
        "stop_reason": None,
        "stop_sequence": None,
        "usage": {"input_tokens": 0, "output_tokens": 0},
    }
    yield emit("message_start", {"type": "message_start", "message": message_stub})

    for index, block in enumerate(content_blocks):
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "text":
            text = block.get("text", "")
            yield emit(
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": index,
                    "content_block": {"type": "text", "text": ""},
                },
            )
            if text:
                yield emit(
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": index,
                        "delta": {"type": "text_delta", "text": text},
                    },
                )
            yield emit("content_block_stop", {"type": "content_block_stop", "index": index})
            continue

        if block_type == "tool_use":
            yield emit(
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": index,
                    "content_block": {
                        "type": "tool_use",
                        "id": block.get("id"),
                        "name": block.get("name"),
                        "input": block.get("input", {}),
                    },
                },
            )
            yield emit("content_block_stop", {"type": "content_block_stop", "index": index})

    yield emit(
        "message_delta",
        {
            "type": "message_delta",
            "delta": {
                "stop_reason": response_body.get("stop_reason"),
                "stop_sequence": response_body.get("stop_sequence"),
            },
            "usage": response_body.get("usage", {"output_tokens": 0}),
        },
    )
    yield emit("message_stop", {"type": "message_stop"})


def iter_simulated_stream_pieces(content: str, target_chunk_size: int = 12) -> Iterator[str]:
    content = content or ""
    if not content:
        return

    current = ""
    for char in content:
        current += char
        boundary = char.isspace() or char in ",.!?;:，。！？；：)】]}>、\n"
        if len(current) >= target_chunk_size or boundary:
            yield current
            current = ""

    if current:
        yield current
