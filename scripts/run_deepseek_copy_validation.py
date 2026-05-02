#!/usr/bin/env python3
from __future__ import annotations

import argparse
import concurrent.futures
import json
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = ROOT / "tests" / "fixtures" / "deepseek-copy-validation" / "parser-summary-100.json"
DEFAULT_OUT = ROOT / "release-artifacts" / "deepseek-web-validation"
DEFAULT_PROVIDER_URL = "http://127.0.0.1:8765/v1/chat/completions"
DEFAULT_SESSION_STATE = Path("/Users/pippo/.agent_qt/plugins/web_provider/profiles/deepseek-session.json")

UI_NOISE_LINES = {"复制", "下载", "深度思考", "智能搜索", "重新生成", "喜欢", "不喜欢", "分享"}
DISCLAIMER_MARKERS = ("本回答由 AI 生成", "本回答由AI生成", "内容由 AI 生成", "内容由AI生成")
BUSY_MARKERS = ("有消息正在生成，请稍后再试", "message is being generated", "please try again later")
SHELL_LANGS = {"bash", "sh", "shell", "zsh"}

PROMPT_PREAMBLE = """【批量验证任务】
请根据下面的 case 需求，生成一段 assistant 最终回复。
要求：
- 如果 case 要求终端动作，必须输出 Markdown fenced bash/sh/shell/zsh 终端命令块。
- 可以在命令块前后保留少量自然语言、推理摘要或后续非终端 fenced 代码块。
- 不要输出 JSON 工具协议，不要输出 XML。
- 不要复述本提示词。
- 不要添加网页免责声明或 UI 按钮文字。
"""

CASE_INSTRUCTIONS: dict[str, str] = {
    "terminal-basic": "生成一个符合 case 摘要的终端命令回复，保留命令语义，可以有简短说明。",
    "placeholder-multiblock": "生成一个用占位符协议写文件的回复：先给 bash 命令块，后给对应语言的文件内容 fenced 代码块。",
    "terminal-extension": "生成一个包含 Agent Qt 终端扩展指令的 bash 命令块，扩展指令要保留为真实命令行。",
    "noisy-or-mixed": "生成一个混合 Markdown 回复，但最终必须包含可执行终端命令块；不要包含网页按钮文字。",
}


@dataclass
class Case:
    id: str
    kind: str
    command_head: list[str]
    fenced_blocks: list[str]
    terminal_extensions: list[str]
    prompt: str = ""


def load_cases(path: Path) -> list[Case]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    cases: list[Case] = []
    for item in raw:
        case_id = str(item.get("id") or "")
        kind = str(item.get("kind") or item.get("category") or "")
        prompt = str(item.get("prompt") or "")
        cases.append(Case(
            id=case_id,
            kind=kind,
            command_head=[str(x) for x in item.get("command_head") or []],
            fenced_blocks=[str(x) for x in item.get("fenced_blocks") or []],
            terminal_extensions=[str(x) for x in item.get("terminal_extensions") or []],
            prompt=prompt,
        ))
    return cases


def prompt_for_case(case: Case) -> str:
    if case.prompt.strip():
        return (
            PROMPT_PREAMBLE
            + f"\nCase ID: {case.id}\n"
            + f"Case 类型: {case.kind}\n"
            + "真实用户需求：\n"
            + case.prompt.strip()
        )
    command_preview = "\n".join(case.command_head[:8])
    blocks = ", ".join(case.fenced_blocks) or "bash"
    extensions = "\n".join(case.terminal_extensions) or "无"
    return (
        PROMPT_PREAMBLE
        + f"\nCase ID: {case.id}\n"
        + f"Case 类型: {case.kind}\n"
        + f"目标 fenced 语言组合: {blocks}\n"
        + f"目标终端扩展指令:\n{extensions}\n"
        + f"命令语义预览:\n{command_preview}\n\n"
        + CASE_INSTRUCTIONS.get(case.kind, "生成符合摘要的回复。")
    )


def post_chat(provider_url: str, model: str, prompt: str, user: str, timeout_s: int) -> tuple[int, str, float]:
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "user": user,
        "output_protocol": "plain",
        "extra_body": {"output_protocol": "plain"},
    }
    req = urllib.request.Request(
        provider_url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return resp.status, resp.read().decode("utf-8", "replace"), time.time() - started
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace"), time.time() - started


def parse_content(raw: str) -> tuple[str, dict[str, Any]]:
    try:
        data = json.loads(raw)
        content = data["choices"][0]["message"].get("content") or ""
        return str(content), data
    except Exception:
        return "", {}


def read_chat_url(session_state: Path) -> str:
    try:
        data = json.loads(session_state.read_text(encoding="utf-8"))
        return str(data.get("chat_url") or "")
    except Exception:
        return ""


def fenced_langs(text: str) -> list[str]:
    langs: list[str] = []
    in_code = False
    fence_char = ""
    fence_len = 0
    for line in (text or "").splitlines():
        if in_code:
            pattern = rf"^\s{{0,3}}{re.escape(fence_char)}{{{fence_len},}}\s*$"
            if re.match(pattern, line):
                in_code = False
                fence_char = ""
                fence_len = 0
            continue
        match = re.match(r"^\s{0,3}([`~]{3,})([^\r\n]*)\s*$", line)
        if not match:
            continue
        fence = match.group(1)
        raw_info = (match.group(2) or "").strip()
        lang = raw_info.split(maxsplit=1)[0].strip().lower() if raw_info else ""
        langs.append(lang)
        fence_char = fence[0]
        fence_len = len(fence)
        in_code = True
    return langs


def judge(case: Case, status: int, content: str, raw: str) -> dict[str, Any]:
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    langs = fenced_langs(content)
    shell_blocks = [lang for lang in langs if lang in SHELL_LANGS]
    has_disclaimer = any(marker in content for marker in DISCLAIMER_MARKERS)
    has_ui_noise = any(line in UI_NOISE_LINES for line in lines)
    busy = any(marker in (content + raw).lower() for marker in BUSY_MARKERS)
    has_required_extension = True
    missing_extensions: list[str] = []
    for ext in case.terminal_extensions:
        if ext and ext not in content:
            has_required_extension = False
            missing_extensions.append(ext)
    ok = (
        status == 200
        and bool(content.strip())
        and not busy
        and not has_disclaimer
        and not has_ui_noise
        and bool(shell_blocks)
        and has_required_extension
    )
    reasons = []
    if status != 200:
        reasons.append(f"http_{status}")
    if not content.strip():
        reasons.append("empty_content")
    if busy:
        reasons.append("web_busy")
    if has_disclaimer:
        reasons.append("disclaimer")
    if has_ui_noise:
        reasons.append("ui_noise")
    if not shell_blocks:
        reasons.append("missing_shell_fence")
    if not has_required_extension:
        reasons.append("missing_extension:" + ";".join(missing_extensions[:3]))
    return {
        "ok": ok,
        "reasons": reasons,
        "fenced_langs": langs,
        "shell_blocks": shell_blocks,
        "has_disclaimer": has_disclaimer,
        "has_ui_noise": has_ui_noise,
        "busy": busy,
        "missing_extensions": missing_extensions,
    }


def run_case(case: Case, args: argparse.Namespace, url_lock: Lock) -> dict[str, Any]:
    prompt = prompt_for_case(case)
    status, raw, elapsed = post_chat(
        args.provider_url,
        args.model,
        prompt,
        f"deepseek-copy-validation-{case.id}",
        args.timeout,
    )
    content, data = parse_content(raw)
    # With one shared DeepSeek profile, the session URL is exact for concurrency=1.
    # For concurrency>1 it is still useful but can be last-writer-wins.
    with url_lock:
        chat_url = read_chat_url(args.session_state)
    verdict = judge(case, status, content, raw)
    return {
        "case_id": case.id,
        "kind": case.kind,
        "prompt": case.prompt,
        "status": status,
        "elapsed_s": round(elapsed, 3),
        "content_chars": len(content),
        "raw_chars": len(raw),
        "chat_url": chat_url,
        "content": content,
        "verdict": verdict,
        "response_ready_reason": data.get("response_ready_reason") if isinstance(data, dict) else None,
    }


def write_outputs(results: list[dict[str, Any]], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    jsonl = out_dir / "results.jsonl"
    combined = out_dir / "assistant-content-combined.txt"
    summary = out_dir / "summary.json"
    with jsonl.open("w", encoding="utf-8") as f:
        for item in results:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
    with combined.open("w", encoding="utf-8") as f:
        for item in results:
            f.write(f"===== {item['case_id']} | {item['kind']} | ok={item['verdict']['ok']} | elapsed={item['elapsed_s']}s =====\n")
            f.write(f"url: {item.get('chat_url') or ''}\n")
            if item["verdict"].get("reasons"):
                f.write("reasons: " + ", ".join(item["verdict"]["reasons"]) + "\n")
            f.write("assistant_content:\n")
            f.write((item.get("content") or "").rstrip() + "\n\n")
    ok_count = sum(1 for item in results if item["verdict"]["ok"])
    failed = [
        {
            "case_id": item["case_id"],
            "kind": item["kind"],
            "reasons": item["verdict"]["reasons"],
            "chat_url": item.get("chat_url") or "",
            "elapsed_s": item["elapsed_s"],
            "content_head": (item.get("content") or "")[:240],
        }
        for item in results
        if not item["verdict"]["ok"]
    ]
    payload = {
        "total": len(results),
        "ok": ok_count,
        "failed": len(results) - ok_count,
        "success_rate": round(ok_count / max(1, len(results)), 4),
        "elapsed_total_s": round(sum(float(item["elapsed_s"]) for item in results), 3),
        "avg_elapsed_s": round(sum(float(item["elapsed_s"]) for item in results) / max(1, len(results)), 3),
        "failed_cases": failed,
        "files": {
            "jsonl": str(jsonl),
            "combined": str(combined),
            "summary": str(summary),
        },
    }
    summary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run real DeepSeek Web copy-capture validation cases through the local provider.")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--provider-url", default=DEFAULT_PROVIDER_URL)
    parser.add_argument("--session-state", type=Path, default=DEFAULT_SESSION_STATE)
    parser.add_argument("--model", default="DeepSeekV4")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args()

    cases = load_cases(args.cases)
    selected = cases[args.offset : args.offset + args.limit if args.limit > 0 else None]
    args.out_dir.mkdir(parents=True, exist_ok=True)
    print(f"running cases={len(selected)} concurrency={args.concurrency} provider={args.provider_url}", flush=True)
    started = time.time()
    results: list[dict[str, Any]] = []
    url_lock = Lock()
    if args.concurrency <= 1:
        for index, case in enumerate(selected, 1):
            print(f"[{index}/{len(selected)}] {case.id} {case.kind} start", flush=True)
            item = run_case(case, args, url_lock)
            results.append(item)
            print(
                f"[{index}/{len(selected)}] {case.id} ok={item['verdict']['ok']} elapsed={item['elapsed_s']}s reasons={item['verdict']['reasons']} url={item['chat_url']}",
                flush=True,
            )
            write_outputs(results, args.out_dir)
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as executor:
            future_map = {executor.submit(run_case, case, args, url_lock): case for case in selected}
            for index, future in enumerate(concurrent.futures.as_completed(future_map), 1):
                case = future_map[future]
                try:
                    item = future.result()
                except Exception as exc:
                    item = {
                        "case_id": case.id,
                        "kind": case.kind,
                        "status": 0,
                        "elapsed_s": 0,
                        "content_chars": 0,
                        "raw_chars": 0,
                        "chat_url": "",
                        "content": "",
                        "verdict": {"ok": False, "reasons": [f"exception:{exc}"], "fenced_langs": [], "shell_blocks": []},
                    }
                results.append(item)
                print(
                    f"[{index}/{len(selected)}] {case.id} ok={item['verdict']['ok']} elapsed={item['elapsed_s']}s reasons={item['verdict']['reasons']} url={item.get('chat_url','')}",
                    flush=True,
                )
                write_outputs(results, args.out_dir)
    write_outputs(results, args.out_dir)
    ok_count = sum(1 for item in results if item["verdict"]["ok"])
    print(f"done total={len(results)} ok={ok_count} failed={len(results)-ok_count} wall_s={round(time.time()-started, 1)}", flush=True)
    print(f"summary={args.out_dir / 'summary.json'}", flush=True)
    print(f"combined={args.out_dir / 'assistant-content-combined.txt'}", flush=True)
    return 0 if ok_count == len(results) else 2


if __name__ == "__main__":
    raise SystemExit(main())
