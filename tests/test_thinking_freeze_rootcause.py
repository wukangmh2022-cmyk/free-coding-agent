#!/usr/bin/env python3
"""回归：thinking 正文奇数撇号曾永久判定 shell 未闭合，导致 wait_for_response 假死。"""
from __future__ import annotations

import re


def has_unclosed_shell_quote(text: str) -> bool:
    single_open = False
    double_open = False
    escaped = False
    for ch in text:
        if escaped:
            escaped = False
            continue
        if ch == "\\" and not single_open:
            escaped = True
            continue
        if ch == "'" and not double_open:
            single_open = not single_open
            continue
        if ch == '"' and not single_open:
            double_open = not double_open
    return single_open or double_open


def old_incomplete(text: str) -> bool:
    """修复前：无 bash 围栏时把整段正文当 shell。"""
    stripped = text.strip()
    if not stripped:
        return False
    fenced = re.findall(r"(?ms)^```([A-Za-z0-9_+-]*)\s*\n(.*?)\n```", stripped)
    candidates = [
        block.strip()
        for lang, block in fenced
        if lang.lower().strip() in {"bash", "sh", "shell", "zsh"}
    ] or [stripped]
    for shell_text in candidates:
        if has_unclosed_shell_quote(shell_text):
            return True
    if stripped.count("'''") % 2 == 1 or stripped.count('"' * 3) % 2 == 1:
        return True
    return False


def new_incomplete(text: str) -> bool:
    """修复后：只检查真实 bash/sh 围栏。"""
    stripped = text.strip()
    if not stripped:
        return False
    fenced = re.findall(r"(?ms)^```([A-Za-z0-9_+-]*)\s*\n(.*?)\n```", stripped)
    candidates = [
        block.strip()
        for lang, block in fenced
        if lang.lower().strip() in {"bash", "sh", "shell", "zsh"}
    ]
    if candidates:
        for shell_text in candidates:
            if has_unclosed_shell_quote(shell_text):
                return True
    return False


def main() -> int:
    thinking = (
        "I need to create a HTML file. It's going to contain SVG. "
        "Pelican's beak and wing. Don't forget the wheels. "
        "The bird's leg pedals the bike."
    ) * 40 + " Don't stop."
    answer = "```html\n<!DOCTYPE html><html><body>hi</body></html>\n```"
    blob = thinking + "\n\n" + answer
    print("thinking_len", len(thinking))
    print("old_incomplete(thinking)", old_incomplete(thinking))
    print("new_incomplete(thinking)", new_incomplete(thinking))
    print("old_incomplete(blob)", old_incomplete(blob))
    print("new_incomplete(blob)", new_incomplete(blob))
    print("new_incomplete(unclosed bash)", new_incomplete('```bash\necho "unclosed\n```'))
    assert old_incomplete(thinking) is True
    assert new_incomplete(thinking) is False
    assert new_incomplete(blob) is False
    assert new_incomplete('```bash\necho "unclosed\n```') is True
    print("OK: freeze root-cause regression passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
