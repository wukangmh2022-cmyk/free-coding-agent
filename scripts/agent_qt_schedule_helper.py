import json
import os
import re
import sys
from datetime import datetime
from typing import Dict, List, Optional

HISTORY_DIR_NAME = ".agent_qt"
SCHEDULES_FILE_NAME = "schedules.json"
HISTORY_VERSION = 1


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
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def format_schedule_datetime(value: object) -> str:
    dt = parse_schedule_datetime(value)
    if not dt:
        return str(value or "").strip()
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def normalize_repeat_seconds(value: object) -> int:
    try:
        seconds = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return max(0, seconds)


def normalize_schedule_spec(raw: Dict[str, object]) -> Optional[Dict[str, object]]:
    if not isinstance(raw, dict):
        return None
    run_at = format_schedule_datetime(raw.get("run_at"))
    if not run_at:
        hour = raw.get("hour")
        minute = raw.get("minute")
        if hour is not None and minute is not None:
            try:
                hour_i = int(hour)
                minute_i = int(minute)
                run_at = datetime.now().replace(hour=hour_i, minute=minute_i, second=0, microsecond=0).strftime("%Y-%m-%d %H:%M:%S")
            except (TypeError, ValueError):
                run_at = ""
    if not run_at:
        return None
    normalized: Dict[str, object] = {"run_at": run_at}
    repeat_seconds = normalize_repeat_seconds(raw.get("repeat_every_seconds"))
    if repeat_seconds:
        normalized["repeat_every_seconds"] = repeat_seconds
    until_at = format_schedule_datetime(raw.get("until_at"))
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
    normalized = [item for item in (normalize_schedule(schedule) for schedule in schedules) if item]
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
    normalized_spec = normalize_schedule_spec(schedule)
    if not normalized_spec:
        raise RuntimeError("计划触发器不完整。")
    base = safe_schedule_id(title or "schedule")
    existing = {str(item.get("id") or "") for item in schedules}
    schedule_id = base
    suffix = 2
    while schedule_id in existing:
        schedule_id = f"{base}-{suffix}"
        suffix += 1
    item = {
        "id": schedule_id,
        "title": (title.strip() or "定时计划")[:80],
        "prompt": prompt.strip(),
        "enabled": bool(enabled),
        "schedule": normalized_spec,
    }
    normalized = normalize_schedule(item)
    if not normalized:
        raise RuntimeError("计划内容不完整。")
    if not save_workspace_schedules(root, [*schedules, normalized]):
        raise RuntimeError("保存计划失败。")
    return normalized


def resolve_schedule_target(schedules: List[Dict[str, object]], target: str) -> str:
    raw = str(target or "").strip()
    if not raw:
        return ""
    if raw.isdigit():
        index = int(raw) - 1
        if 0 <= index < len(schedules):
            return str(schedules[index].get("id") or "")
    safe_target = schedule_lookup_key(raw)
    for item in schedules:
        schedule_id = str(item.get("id") or "")
        title = str(item.get("title") or "")
        if raw == schedule_id or (safe_target and safe_target == schedule_id) or raw == title:
            return schedule_id
    for item in schedules:
        schedule_id = str(item.get("id") or "")
        title = str(item.get("title") or "")
        if raw in title or raw in schedule_id:
            return schedule_id
    return ""


def update_workspace_schedule(root: str, schedule_id: str, patch: Dict[str, object]) -> bool:
    schedules = load_workspace_schedules(root)
    safe_id = safe_schedule_id(schedule_id)
    updated: List[Dict[str, object]] = []
    found = False
    for item in schedules:
        if str(item.get("id") or "") == safe_id:
            merged = dict(item)
            merged.update(patch)
            merged["updated_at"] = datetime.now().isoformat(timespec="seconds")
            item = merged
            found = True
        updated.append(item)
    return found and save_workspace_schedules(root, updated)


def delete_workspace_schedule(root: str, target: str) -> bool:
    schedules = load_workspace_schedules(root)
    resolved = resolve_schedule_target(schedules, target)
    if not resolved:
        return False
    remaining = [item for item in schedules if str(item.get("id") or "") != resolved]
    return len(remaining) != len(schedules) and save_workspace_schedules(root, remaining)


def update_workspace_schedule_from_action(root: str, payload: Dict[str, object]) -> Dict[str, object]:
    schedules = load_workspace_schedules(root)
    target = str(payload.get("target") or payload.get("id") or payload.get("title") or "").strip()
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
        patch["enabled"] = bool(payload.get("enabled"))
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
    if not patch:
        raise RuntimeError("没有可更新的计划字段。")
    if not update_workspace_schedule(root, resolved, patch):
        raise RuntimeError(f"修改计划失败：{target}")
    return next((item for item in load_workspace_schedules(root) if str(item.get("id") or "") == resolved), current)


def format_schedule_time(schedule_item: Dict[str, object]) -> str:
    text = str(schedule_item.get("schedule_text") or "").strip()
    if text:
        return text
    return format_schedule_spec(dict(schedule_item.get("schedule") or {}))


def schedules_summary_text(schedules: List[Dict[str, object]]) -> str:
    if not schedules:
        return "当前没有计划。"
    lines = ["当前计划："]
    for index, item in enumerate(schedules, start=1):
        status = "启用" if bool(item.get("enabled", True)) else "暂停"
        lines.append(f"{index}. {item.get('title')}（{status}，{format_schedule_time(item)}）")
    return "\n".join(lines)


def handle_create(root: str, payload: Dict[str, object]) -> str:
    title = str(payload.get("title") or payload.get("name") or "定时计划").strip()[:80] or "定时计划"
    prompt = str(payload.get("prompt") or payload.get("content") or "").strip()
    trigger = payload.get("trigger") or payload.get("schedule")
    if not isinstance(trigger, dict):
        raise RuntimeError("计划缺少 trigger。")
    if not prompt:
        raise RuntimeError("计划缺少 prompt。")
    item = create_workspace_schedule_from_spec(root, title, prompt, trigger, bool(payload.get("enabled", True)))
    return "已创建计划：" + f"{item.get('title')}（{format_schedule_time(item)}）"


def main(argv: List[str]) -> int:
    if len(argv) < 3:
        print("usage: agent_qt_schedule_helper.py <project_root> <action> [payload]", file=sys.stderr)
        return 2
    root = os.path.abspath(os.path.expanduser(argv[1]))
    action = str(argv[2] or "").strip().lower()
    payload_text = " ".join(argv[3:]).strip()
    try:
        if action == "list":
            print(schedules_summary_text(load_workspace_schedules(root)))
            return 0
        if action == "create":
            payload = json.loads(payload_text)
            if not isinstance(payload, dict):
                raise RuntimeError("schedule create payload 必须是 JSON 对象。")
            print(handle_create(root, payload))
            return 0
        if action in {"delete", "remove", "del"}:
            if not payload_text:
                raise RuntimeError("schedule delete 缺少目标。")
            if not delete_workspace_schedule(root, payload_text):
                raise RuntimeError(f"未找到计划：{payload_text}")
            print(f"已删除计划：{payload_text}")
            return 0
        if action == "update":
            payload = json.loads(payload_text)
            if not isinstance(payload, dict):
                raise RuntimeError("schedule update payload 必须是 JSON 对象。")
            updated = update_workspace_schedule_from_action(root, payload)
            print(f"已修改计划：{updated.get('title')}（{format_schedule_time(updated)}）")
            return 0
        raise RuntimeError(f"schedule: unsupported action: {action}")
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
