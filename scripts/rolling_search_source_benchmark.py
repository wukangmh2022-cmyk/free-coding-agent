#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT_DIR = ROOT / "release-artifacts" / "search-source-benchmark"
DEFAULT_QUERY = "西南医科大学附属医院卫生学校 官网"
DEFAULT_SOURCES = ("sm", "so360", "sogou", "baidu")

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


@dataclass(frozen=True)
class SearchSource:
    key: str
    name: str
    base_url: str
    query_param: str


SOURCES: dict[str, SearchSource] = {
    "sm": SearchSource("sm", "sm.cn", "https://m.sm.cn/s", "q"),
    "so360": SearchSource("so360", "360 Search", "https://www.so.com/s", "q"),
    "sogou": SearchSource("sogou", "Sogou", "https://www.sogou.com/web", "query"),
    "baidu": SearchSource("baidu", "Baidu", "https://www.baidu.com/s", "wd"),
}

SECURITY_MARKERS = (
    "安全验证",
    "验证码",
    "showcaptcha",
    "wappass.baidu.com",
    "访问过于频繁",
    "Access Denied",
    "Forbidden",
)

TARGET_MARKERS = (
    "hlxy.swmu.edu.cn",
    "西南医科大学附属医院卫生学校",
    "西南医科大学护理学院",
)

try:
    import trafilatura  # type: ignore
except Exception:
    trafilatura = None

try:
    from bs4 import BeautifulSoup  # type: ignore
except Exception:
    BeautifulSoup = None


def build_opener() -> urllib.request.OpenerDirector:
    context = ssl.create_default_context()
    handlers: list[Any] = [
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPSHandler(context=context),
    ]
    return urllib.request.build_opener(*handlers)


def build_search_url(source: SearchSource, query: str) -> str:
    params = urllib.parse.urlencode({source.query_param: query})
    return f"{source.base_url}?{params}"


def count_markers(text: str, markers: tuple[str, ...]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for marker in markers:
        count = text.count(marker)
        if count:
            counts[marker] = count
    return counts


def classify_result(status: int, text: str, elapsed_s: float) -> str:
    if status != 200:
        return "http_error"
    if any(marker in text for marker in SECURITY_MARKERS):
        return "challenge"
    if any(marker in text for marker in TARGET_MARKERS):
        return "relevant"
    if elapsed_s >= 14.5:
        return "slow_unknown"
    return "unknown"


def clean_html_snapshot(html_text: str, url: str) -> dict[str, Any]:
    cleaned = ""
    method = "none"
    if trafilatura is not None:
        try:
            cleaned = trafilatura.extract(
                html_text,
                url=url,
                favor_recall=True,
                include_links=True,
            ) or ""
            if cleaned.strip():
                method = "trafilatura"
        except Exception:
            cleaned = ""
    if not cleaned.strip() and BeautifulSoup is not None:
        try:
            soup = BeautifulSoup(html_text, "html.parser")
            cleaned = soup.get_text("\n", strip=True)
            if cleaned.strip():
                method = "beautifulsoup"
        except Exception:
            cleaned = ""
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    excerpt = cleaned[:800]
    target_counts = count_markers(cleaned, TARGET_MARKERS)
    security_counts = count_markers(cleaned, SECURITY_MARKERS)
    return {
        "clean_method": method,
        "cleaned_excerpt": excerpt,
        "cleaned_chars": len(cleaned),
        "cleaned_target_counts": target_counts,
        "cleaned_security_counts": security_counts,
        "cleaned_has_target": bool(target_counts),
        "cleaned_has_security": bool(security_counts),
    }


def fetch_once(
    opener: urllib.request.OpenerDirector,
    source: SearchSource,
    query: str,
    timeout_s: float,
) -> dict[str, Any]:
    url = build_search_url(source, query)
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        },
        method="GET",
    )
    started = time.time()
    status = 0
    body = ""
    error = ""
    final_url = url
    try:
        with opener.open(request, timeout=timeout_s) as response:
            status = int(getattr(response, "status", 200) or 200)
            final_url = str(getattr(response, "url", "") or response.geturl() or url)
            raw = response.read()
            charset = response.headers.get_content_charset() or "utf-8"
            body = raw.decode(charset, "replace")
    except urllib.error.HTTPError as exc:
        status = int(exc.code or 0)
        final_url = str(exc.geturl() or url)
        body = exc.read().decode("utf-8", "replace")
        error = str(exc)
    except Exception as exc:
        error = str(exc)
    elapsed_s = time.time() - started
    target_counts = count_markers(body, TARGET_MARKERS)
    security_counts = count_markers(body, SECURITY_MARKERS)
    cleaned_info = clean_html_snapshot(body, final_url)
    return {
        "source": source.key,
        "source_name": source.name,
        "query": query,
        "request_url": url,
        "final_url": final_url,
        "status": status,
        "elapsed_s": round(elapsed_s, 3),
        "bytes": len(body.encode("utf-8", "ignore")),
        "classification": classify_result(status, body, elapsed_s),
        "target_counts": target_counts,
        "security_counts": security_counts,
        "has_target": bool(target_counts),
        "has_security": bool(security_counts),
        "error": error,
        "title_present": "<title" in body.lower(),
        "h3_count": body.lower().count("<h3"),
        **cleaned_info,
    }


def summarize(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    by_source: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_source.setdefault(str(row["source"]), []).append(row)
    for source in DEFAULT_SOURCES:
        source_rows = by_source.get(source, [])
        if not source_rows:
            continue
        elapsed_values = [float(item["elapsed_s"]) for item in source_rows]
        relevant = sum(1 for item in source_rows if item["classification"] == "relevant")
        challenge = sum(1 for item in source_rows if item["classification"] == "challenge")
        cleaned_relevant = sum(1 for item in source_rows if item.get("cleaned_has_target"))
        cleaned_challenge = sum(1 for item in source_rows if item.get("cleaned_has_security"))
        http_errors = sum(1 for item in source_rows if item["classification"] == "http_error")
        first_challenge_round = None
        for item in source_rows:
            if item["classification"] == "challenge":
                first_challenge_round = int(item["source_round"])
                break
        first_cleaned_challenge_round = None
        for item in source_rows:
            if item.get("cleaned_has_security"):
                first_cleaned_challenge_round = int(item["source_round"])
                break
        summaries.append(
            {
                "source": source,
                "rounds": len(source_rows),
                "relevant_rounds": relevant,
                "cleaned_relevant_rounds": cleaned_relevant,
                "challenge_rounds": challenge,
                "first_challenge_round": first_challenge_round,
                "cleaned_challenge_rounds": cleaned_challenge,
                "first_cleaned_challenge_round": first_cleaned_challenge_round,
                "http_error_rounds": http_errors,
                "avg_elapsed_s": round(sum(elapsed_values) / len(elapsed_values), 3),
                "min_elapsed_s": round(min(elapsed_values), 3),
                "max_elapsed_s": round(max(elapsed_values), 3),
            }
        )
    return summaries


def write_outputs(out_dir: Path, rows: list[dict[str, Any]], summaries: list[dict[str, Any]]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "results.json"
    csv_path = out_dir / "results.csv"
    summary_path = out_dir / "summary.json"

    json_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    summary_path.write_text(json.dumps(summaries, ensure_ascii=False, indent=2), encoding="utf-8")

    fieldnames = [
        "source",
        "source_name",
        "query",
        "status",
        "elapsed_s",
        "bytes",
        "classification",
        "has_target",
        "has_security",
        "clean_method",
        "cleaned_chars",
        "cleaned_has_target",
        "cleaned_has_security",
        "cleaned_excerpt",
        "title_present",
        "h3_count",
        "request_url",
        "final_url",
        "error",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Roll benchmark direct no-proxy search pages for multiple Chinese search sources."
    )
    parser.add_argument("--query", default=DEFAULT_QUERY)
    parser.add_argument("--rounds-per-source", type=int, default=15)
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument(
        "--interval",
        type=float,
        default=15.0,
        help="Target seconds per round. Sleeps after a fast round to keep cadence.",
    )
    parser.add_argument(
        "--sources",
        default=",".join(DEFAULT_SOURCES),
        help="Comma-separated source keys: sm,so360,sogou,baidu",
    )
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source_keys = [item.strip() for item in str(args.sources).split(",") if item.strip()]
    selected_sources = [SOURCES[key] for key in source_keys]
    if not selected_sources:
        raise SystemExit("No valid sources selected.")

    opener = build_opener()
    rows: list[dict[str, Any]] = []
    total_rounds = args.rounds_per_source * len(selected_sources)
    round_index = 0

    for attempt in range(args.rounds_per_source):
        for source in selected_sources:
            round_index += 1
            print(
                f"[{round_index}/{total_rounds}] source={source.key} round={attempt + 1}/{args.rounds_per_source} "
                f"query={args.query}"
            )
            started = time.time()
            row = fetch_once(opener, source, args.query, timeout_s=args.timeout)
            row["global_round"] = round_index
            row["source_round"] = attempt + 1
            rows.append(row)
            print(
                f"  -> status={row['status']} elapsed={row['elapsed_s']}s class={row['classification']} "
                f"target={row['has_target']} security={row['has_security']}"
            )
            remaining = float(args.interval) - (time.time() - started)
            if remaining > 0:
                time.sleep(remaining)

    summaries = summarize(rows)
    out_dir = Path(args.out_dir).expanduser() / time.strftime("%Y%m%d-%H%M%S")
    write_outputs(out_dir, rows, summaries)
    print(f"\nWrote outputs to: {out_dir}")
    print(json.dumps(summaries, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
