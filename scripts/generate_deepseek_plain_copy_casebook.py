#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "plugins/web_provider/backend/packages/harness"))

from deerflow.models.deepseek_web_bridge import clean_plain_visible_assistant_text  # type: ignore  # noqa: E402

OUT_DIR = ROOT / "tests" / "fixtures" / "deepseek-copy-validation"
CASEBOOK = OUT_DIR / "assistant-content-100.txt"
SUMMARY = OUT_DIR / "parser-summary-100.json"

BASH_SNIPPETS = [
    "echo hello",
    "pwd && ls -la",
    "wc -l /Users/pippo/Desktop/my-project/match-crush-game.html && head -3 /Users/pippo/Desktop/my-project/match-crush-game.html && tail -3 /Users/pippo/Desktop/my-project/match-crush-game.html",
    "wx send_file /Users/pippo/Desktop/my-project/monopoly-game.html",
    "schedule list",
    "schedule update {\"target\":\"每5分钟查比特币价格\",\"enabled\":true}",
    "printf '%s\\n' alpha beta",
    "cat > /Users/pippo/Desktop/my-project/hello.py <<'PYEOF'\n<!-- Python block 1 -->\nPYEOF\n/Users/pippo/.agent_qt/runtime/python/bin/python /Users/pippo/Desktop/my-project/hello.py",
    "/Users/pippo/.agent_qt/runtime/python/bin/python <<'PYEOF'\nprint('ok')\nPYEOF",
    "if [ -f /Users/pippo/Desktop/my-project/a.txt ]; then\necho yes\nelse\necho no\nfi",
]

FILE_BLOCKS = {
    "python": "# <desc test python>\nprint('hello')",
    "html": "<!-- <desc page> -->\n<!doctype html><html><body>Hello</body></html>",
    "svg": "<!-- <desc icon> -->\n<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"10\" height=\"10\"></svg>",
    "json": "{\"ok\": true, \"items\": [1, 2, 3]}",
    "javascript": "// <desc js>\nconsole.log('hi')",
}

PREFIXES = [
    "",
    "先执行下面这一步。\n\n",
    "我会先验证，再给结论。\n\n",
    "思路：先读真实文件，再运行命令。\n\n",
    "下面是当前批次：\n\n",
]

SUFFIXES = [
    "",
    "\n\n执行后我再看结果。",
    "\n\n```plaintext\nTODO: 下一轮继续处理剩余内容。\n```",
    "\n\n本回答由 AI 生成，内容仅供参考，请仔细甄别。",
    "\n\n内容由 AI 生成，请仔细甄别",
]

LANG_VARIANTS = ["bash", "sh", "shell", "zsh"]


def fenced(lang: str, body: str) -> str:
    return f"```{lang}\n{body}\n```"


def build_cases() -> list[dict[str, str]]:
    cases: list[dict[str, str]] = []
    idx = 1
    for snippet in BASH_SNIPPETS:
        for lang in LANG_VARIANTS:
            prefix = PREFIXES[(idx - 1) % len(PREFIXES)]
            suffix = SUFFIXES[(idx + 1) % len(SUFFIXES)]
            cases.append({
                "id": f"case-{idx:03d}",
                "kind": "terminal-basic",
                "content": prefix + fenced(lang, snippet) + suffix,
            })
            idx += 1
    placeholder_templates = [
        ("python", "cat > /Users/pippo/Desktop/my-project/hello.py <<'PYEOF'\n<!-- Python block 1 -->\nPYEOF\npython /Users/pippo/Desktop/my-project/hello.py"),
        ("html", "cat > /Users/pippo/Desktop/my-project/index.html <<'HTMLEOF'\n<!-- HTML block 1 -->\nHTMLEOF"),
        ("svg", "cat > /Users/pippo/Desktop/my-project/icon.svg <<'SVGEO'\n<!-- SVG block 1 -->\nSVGEO"),
        ("json", "cat > /Users/pippo/Desktop/my-project/data.json <<'JSONEOF'\n<!-- JSON block 1 -->\nJSONEOF"),
        ("javascript", "cat > /Users/pippo/Desktop/my-project/app.js <<'JSEOF'\n<!-- JavaScript block 1 -->\nJSEOF\nnode /Users/pippo/Desktop/my-project/app.js"),
    ]
    for block_lang, command in placeholder_templates:
        for variant in range(6):
            cases.append({
                "id": f"case-{idx:03d}",
                "kind": "placeholder-multiblock",
                "content": PREFIXES[variant % len(PREFIXES)] + fenced("bash", command) + "\n\n" + fenced(block_lang, FILE_BLOCKS[block_lang]) + SUFFIXES[variant % len(SUFFIXES)],
            })
            idx += 1
    extension_cases = [
        "wx send_file /Users/pippo/Desktop/my-project/a.html",
        "schedule create {\"title\":\"测试\",\"prompt\":\"echo ok\",\"trigger\":{\"run_at\":\"2026-05-02 23:00:00\"}}",
        "echo before\nwx send_file /Users/pippo/Desktop/my-project/a.html\necho after",
        "schedule list\nwx send_file /Users/pippo/Desktop/my-project/report.html",
        "bash wx send_file /Users/pippo/Desktop/my-project/a.html",
        "wx schedule list",
        "AGENT_WECHAT_SEND_FILE: /Users/pippo/Desktop/my-project/a.html",
        "AGENT_WECHAT_CREATE_SCHEDULE: {\"title\":\"x\",\"prompt\":\"y\",\"trigger\":{\"run_at\":\"2026-05-02 23:00:00\"}}",
        "schedule update {\"target\":\"x\",\"enabled\":false}",
        "schedule delete x",
    ]
    for command in extension_cases:
        for variant in range(2):
            cases.append({
                "id": f"case-{idx:03d}",
                "kind": "terminal-extension",
                "content": PREFIXES[(idx + variant) % len(PREFIXES)] + fenced("bash", command) + SUFFIXES[(idx + 2) % len(SUFFIXES)],
            })
            idx += 1
    noisy_cases = [
        "bash\n复制\n下载\n" + BASH_SNIPPETS[8] + "\n本回答由 AI 生成，内容仅供参考，请仔细甄别。\n深度思考\n智能搜索\n内容由 AI 生成，请仔细甄别\n复制",
        "```bash\necho ok\n```\n复制\n重新生成\n喜欢\n不喜欢\n分享",
        "```python\nprint('not terminal')\n```\n\n```bash\necho terminal\n```",
        "```html\n<div>展示代码</div>\n```\n\n需要执行再看下一块：\n```bash\necho after html\n```",
        "解释文字\n```bash\necho one\n```\n\n更多解释\n```json\n{\"a\":1}\n```",
        "```bash\nif [ -n \"$HOME\" ]; then\necho ok\nfi\n```",
        "```bash\ncat <<'EOF'\nhello\nEOF\n```",
        "```bash\nprintf '%s\\n' \"中文\"\n```\n\n```plaintext\nTODO: done\n```",
        "```bash\n# <desc 检查>\nls /Users/pippo/Desktop/my-project\n```",
        "```bash\necho final-check\n```",
    ]
    while len(cases) < 100:
        base = noisy_cases[(len(cases) - 70) % len(noisy_cases)]
        cases.append({
            "id": f"case-{idx:03d}",
            "kind": "noisy-or-mixed",
            "content": base,
        })
        idx += 1
    return cases[:100]


def extract_fenced_blocks(text: str) -> list[tuple[str, str]]:
    return [(m.group(1).strip().lower(), m.group(2)) for m in re.finditer(r"(?ms)^```([A-Za-z0-9_+-]*)\s*\n(.*?)\n```", text)]


def first_terminal_block(blocks: list[tuple[str, str]]) -> tuple[str, str]:
    for lang in ("bash", "sh", "shell", "zsh"):
        for block_lang, body in blocks:
            if block_lang == lang:
                return body, block_lang
    return "", ""


def extract_terminal_extensions(text: str) -> list[str]:
    out: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if re.match(r"^(?:wx\s+send_file|schedule\s+(?:create|list|delete|remove|del|update)|AGENT_WECHAT_)", stripped, re.I):
            out.append(stripped)
    return out


def summarize_case(case: dict[str, str]) -> dict[str, object]:
    content = case["content"]
    cleaned = clean_plain_visible_assistant_text(content)
    blocks = extract_fenced_blocks(cleaned)
    command, command_lang = first_terminal_block(blocks)
    terminal_ext = extract_terminal_extensions(cleaned)
    return {
        "id": case["id"],
        "kind": case["kind"],
        "chars": len(content),
        "cleaned_chars": len(cleaned),
        "fenced_blocks": [lang for lang, _body in blocks],
        "command_lang": command_lang,
        "command_head": command.strip().splitlines()[:3],
        "has_command": bool(command.strip()),
        "terminal_extensions": terminal_ext,
        "contains_disclaimer": any(x in cleaned for x in ("本回答由 AI 生成", "内容由 AI 生成")),
        "contains_ui_noise": any(x in cleaned.splitlines() for x in ("复制", "下载", "深度思考", "智能搜索")),
    }


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    cases = build_cases()
    summaries = [summarize_case(case) for case in cases]
    with CASEBOOK.open("w", encoding="utf-8") as f:
        f.write("# DeepSeek plain-copy assistant content casebook (100 cases)\n\n")
        for case, summary in zip(cases, summaries):
            f.write(f"===== {case['id']} | {case['kind']} =====\n")
            f.write("assistant_content:\n")
            f.write(case["content"].rstrip() + "\n")
            f.write("parser_summary:\n")
            f.write(json.dumps(summary, ensure_ascii=False, indent=2) + "\n\n")
    SUMMARY.write_text(json.dumps(summaries, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {CASEBOOK}")
    print(f"wrote {SUMMARY}")
    print(f"cases={len(cases)}")


if __name__ == "__main__":
    main()
