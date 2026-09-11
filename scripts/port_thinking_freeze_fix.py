#!/usr/bin/env python3
"""Splice thinking-freeze fix from zip worktree into git worktree bridge."""
from __future__ import annotations

from pathlib import Path

ZIP = Path("/Users/pippo/github-repo/free-coding-agent-codex-feat-v0.99-stream-quick-release")
GIT = Path("/Users/pippo/github-repo/free-coding-agent")
REL = "plugins/web_provider/backend/packages/harness/deerflow/models/deepseek_web_bridge.py"


def extract_function(src: str, name: str) -> str:
    start = src.index(f"\ndef {name}(")
    if start < 0:
        raise SystemExit(f"function {name} not found")
    start += 1  # skip leading newline
    # next top-level def/class after start
    nxt = len(src)
    for marker in ("\ndef ", "\nclass "):
        pos = src.find(marker, start + 10)
        if pos != -1:
            nxt = min(nxt, pos + 1)
    return src[start:nxt].rstrip() + "\n"


def replace_function(src: str, name: str, new_body: str) -> str:
    start = src.index(f"\ndef {name}(") + 1
    nxt = len(src)
    for marker in ("\ndef ", "\nclass "):
        pos = src.find(marker, start + 10)
        if pos != -1:
            nxt = min(nxt, pos + 1)
    return src[:start] + new_body + "\n" + src[nxt:]


zip_src = (ZIP / REL).read_text(encoding="utf-8")
git_src = (GIT / REL).read_text(encoding="utf-8")

new_incomplete = extract_function(zip_src, "is_suspicious_incomplete_command_text")
git_src = replace_function(git_src, "is_suspicious_incomplete_command_text", new_incomplete)

# preview shrink: apply the same guard from zip by replacing the condition block
old = """            if (
                not done
                and not error
                and previous_text
                and len(text) < max(80, int(len(previous_text) * 0.75))
            ):
                return
"""
new = """            # Thinking can collapse / switch to a short answer: don't freeze preview on shrink.
            if (
                not done
                and not error
                and previous_text
                and len(text) < max(80, int(len(previous_text) * 0.75))
                and len(text) < 200
            ):
                return
"""
if old not in git_src:
    raise SystemExit("shrink guard not found in git file")
git_src = git_src.replace(old, new, 1)

old_trunc = '''                    logger.warning(
                        "DeepSeek wait_for_response postponing likely truncated plain text chars=%d copied_chars=%d",
                        len(current),
                        len(copied),
                    )
                    page.wait_for_timeout(min(self.stable_poll_interval_ms, 200))
                    continue
'''
new_trunc = '''                    # Generation complete + stable: if still misclassified as truncated, release after N rounds.
                    if plain_generation_complete and stable_seen >= max(self.stable_rounds * 3, 9) and len(current) >= 80:
                        if trace is not None:
                            trace.set("response_chars", len(current))
                            trace.set("response_ready_reason", "plain_truncated_stable_force")
                            trace.set("plain_truncated_force_stable_seen", stable_seen)
                            trace.mark("response_stable")
                        logger.warning(
                            "DeepSeek wait_for_response accepting stable plain text despite truncated heuristic chars=%d stable_seen=%d copied_chars=%d",
                            len(current),
                            stable_seen,
                            len(copied),
                        )
                        return current
                    logger.warning(
                        "DeepSeek wait_for_response postponing likely truncated plain text chars=%d copied_chars=%d",
                        len(current),
                        len(copied),
                    )
                    page.wait_for_timeout(min(self.stable_poll_interval_ms, 200))
                    continue
'''
if old_trunc not in git_src:
    raise SystemExit("truncated postpone not found in git file")
git_src = git_src.replace(old_trunc, new_trunc, 1)

(GIT / REL).write_text(git_src, encoding="utf-8")
print("patched", GIT / REL)
