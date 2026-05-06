#!/usr/bin/env python3
"""Crawl SWMU nursing/school notices without relying on search-result limits.

This reproduces the useful part of the old 00agent2 run: use search only to
find the official site, then crawl the site's own paginated notice list and
visit detail pages directly.
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable
from urllib.parse import unquote, urljoin, urlparse

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError as exc:  # pragma: no cover - CLI dependency hint
    raise SystemExit("Missing dependencies. Install with: pip install requests beautifulsoup4") from exc

DEFAULT_LIST_URL = "https://hlxy.swmu.edu.cn/index/tzgg.htm"
DEFAULT_BASE_SITE = "https://hlxy.swmu.edu.cn"
DEFAULT_KEYWORDS = (
    "招标",
    "投标",
    "招投标",
    "市场调研",
    "调研",
    "询价",
    "比价",
    "采购",
    "竞价",
    "磋商",
    "比选",
    "成交",
    "流标",
    "结果公示",
)
ATTACHMENT_EXTENSIONS = (".doc", ".docx", ".pdf", ".xls", ".xlsx", ".zip", ".rar", ".7z")


@dataclass
class Notice:
    title: str
    date: str
    url: str
    page_url: str = ""
    attachments: list[tuple[str, str]] = field(default_factory=list)


def build_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
            "Connection": "keep-alive",
        }
    )
    return session


def fetch_text(session: requests.Session, url: str, *, timeout: int, retries: int, sleep_seconds: float) -> str:
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            response = session.get(url, timeout=timeout)
            response.raise_for_status()
            if not response.encoding or response.encoding.lower() == "iso-8859-1":
                response.encoding = response.apparent_encoding or "utf-8"
            return response.text
        except Exception as exc:  # pragma: no cover - network variability
            last_error = exc
            if attempt < retries:
                time.sleep(sleep_seconds)
    raise RuntimeError(f"failed to fetch {url}: {last_error}")


def infer_total_pages(html: str, items_per_page: int) -> int | None:
    patterns = (
        r"共\s*(\d+)\s*条",
        r"总共\s*(\d+)\s*条",
        r"total[^0-9]{0,12}(\d+)",
    )
    for pattern in patterns:
        match = re.search(pattern, html, re.I)
        if match:
            total_count = int(match.group(1))
            return max(1, (total_count + items_per_page - 1) // items_per_page)
    page_numbers = [int(value) for value in re.findall(r"/index/tzgg/(\d+)\.htm", html)]
    return max(page_numbers) if page_numbers else None


def page_url_for(list_url: str, base_site: str, page_num: int) -> str:
    if page_num <= 1:
        return list_url
    parsed = urlparse(list_url)
    prefix = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else base_site
    return f"{prefix}/index/tzgg/{page_num}.htm"


def normalize_date(raw: str) -> str:
    raw = re.sub(r"\s+", "", raw or "")
    raw = raw.strip("[]【】()（）")
    return raw


def parse_notices_from_page(html: str, page_url: str, base_site: str, keywords: tuple[str, ...]) -> list[Notice]:
    soup = BeautifulSoup(html, "html.parser")
    containers = soup.find_all("ul", class_=re.compile(r"(^|\s)mylist(\s|$)")) or [soup]
    notices: list[Notice] = []
    seen_urls: set[str] = set()
    for container in containers:
        for item in container.find_all("li"):
            link = item.find("a", href=True)
            if not link:
                continue
            title = link.get_text(" ", strip=True)
            if not title or not any(keyword in title for keyword in keywords):
                continue
            detail_url = urljoin(base_site, link["href"])
            if detail_url in seen_urls:
                continue
            seen_urls.add(detail_url)
            date_tag = item.find("span")
            date_text = normalize_date(date_tag.get_text(" ", strip=True) if date_tag else "")
            notices.append(Notice(title=title, date=date_text, url=detail_url, page_url=page_url))
    return notices


def safe_filename(value: str, fallback: str) -> str:
    value = unquote(value or "").strip() or fallback
    value = re.sub(r"^[^:：]{0,12}[:：]\s*", "", value)
    value = re.sub(r"[\\/:*?\"<>|\r\n]+", "_", value)
    value = re.sub(r"\s+", " ", value).strip(" .")
    return value or fallback


def attachment_filename(link_text: str, href: str, index: int, title: str) -> str:
    text = link_text.strip()
    for ext in ATTACHMENT_EXTENSIONS:
        if text.lower().endswith(ext):
            return safe_filename(text, f"attachment_{index}{ext}")
    path_name = os.path.basename(urlparse(href).path)
    if any(path_name.lower().endswith(ext) for ext in ATTACHMENT_EXTENSIONS):
        return safe_filename(path_name, f"attachment_{index}")
    short_title = safe_filename(title[:24], "notice")
    return f"{short_title}_attachment_{index}"


def parse_attachments(html: str, detail_url: str, title: str) -> list[tuple[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    attachments: list[tuple[str, str]] = []
    seen: set[str] = set()
    for link in soup.find_all("a", href=True):
        href = str(link.get("href") or "").strip()
        text = link.get_text(" ", strip=True)
        path = urlparse(href).path.lower()
        text_lower = text.lower()
        looks_like_file = any(path.endswith(ext) or text_lower.endswith(ext) for ext in ATTACHMENT_EXTENSIONS)
        looks_like_download = "download" in path or "附件" in text or "下载" in text
        if not (looks_like_file or looks_like_download):
            continue
        absolute = urljoin(detail_url, href)
        if absolute in seen:
            continue
        seen.add(absolute)
        attachments.append((absolute, attachment_filename(text, href, len(attachments) + 1, title)))
    return attachments


def crawl_notices(args: argparse.Namespace) -> list[Notice]:
    session = build_session()
    keywords = tuple(item.strip() for item in args.keywords.split(",") if item.strip()) or DEFAULT_KEYWORDS
    first_html = fetch_text(session, args.list_url, timeout=args.timeout, retries=args.retries, sleep_seconds=args.sleep)
    total_pages = args.pages or infer_total_pages(first_html, args.items_per_page) or 1
    total_pages = min(total_pages, args.max_pages)
    print(f"List pages to crawl: {total_pages}")

    notices_by_url: dict[str, Notice] = {}
    for page_num in range(1, total_pages + 1):
        url = page_url_for(args.list_url, args.base_site, page_num)
        try:
            html = first_html if page_num == 1 else fetch_text(
                session,
                url,
                timeout=args.timeout,
                retries=args.retries,
                sleep_seconds=args.sleep,
            )
        except Exception as exc:
            print(f"[{page_num}/{total_pages}] {url} -> skipped: {exc}", file=sys.stderr)
            if page_num > 1:
                break
            continue
        page_notices = parse_notices_from_page(html, url, args.base_site, keywords)
        print(f"[{page_num}/{total_pages}] {url} -> {len(page_notices)} matching notices")
        for notice in page_notices:
            notices_by_url.setdefault(notice.url, notice)
        if args.sleep:
            time.sleep(args.sleep)

    notices = list(notices_by_url.values())
    if args.with_attachments:
        for idx, notice in enumerate(notices, start=1):
            try:
                html = fetch_text(session, notice.url, timeout=args.timeout, retries=args.retries, sleep_seconds=args.sleep)
                notice.attachments = parse_attachments(html, notice.url, notice.title)
                print(f"detail [{idx}/{len(notices)}] {len(notice.attachments)} attachments: {notice.title}")
            except Exception as exc:
                print(f"detail [{idx}/{len(notices)}] failed: {notice.url}: {exc}", file=sys.stderr)
            if args.sleep:
                time.sleep(args.sleep)
    return notices


def write_outputs(notices: list[Notice], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    md_path = output_dir / "swmu_bidding_market_research.md"
    csv_path = output_dir / "swmu_bidding_market_research.csv"
    attachment_path = output_dir / "swmu_attachments_index.md"

    with md_path.open("w", encoding="utf-8") as handle:
        handle.write("# 西南医科大学招投标与市场调研公告列表\n\n")
        handle.write(f"共抓取相关公告：{len(notices)} 条\n\n")
        handle.write("| 序号 | 公告标题 | 发布时间 | 详情链接 |\n")
        handle.write("|------|----------|----------|----------|\n")
        for index, notice in enumerate(notices, start=1):
            handle.write(f"| {index} | {notice.title} | {notice.date} | {notice.url} |\n")

    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["index", "title", "date", "url", "page_url", "attachment_count"])
        writer.writeheader()
        for index, notice in enumerate(notices, start=1):
            writer.writerow(
                {
                    "index": index,
                    "title": notice.title,
                    "date": notice.date,
                    "url": notice.url,
                    "page_url": notice.page_url,
                    "attachment_count": len(notice.attachments),
                }
            )

    with attachment_path.open("w", encoding="utf-8") as handle:
        handle.write("# 西南医科大学公告附件索引\n\n")
        handle.write("| 序号 | 公告标题 | 附件名称 | 附件链接 |\n")
        handle.write("|------|----------|----------|----------|\n")
        row = 0
        for notice in notices:
            for url, filename in notice.attachments:
                row += 1
                handle.write(f"| {row} | {notice.title} | {filename} | {url} |\n")
        if row == 0:
            handle.write("| - | - | 未抓取附件或未发现附件 | - |\n")

    print(f"Wrote: {md_path}")
    print(f"Wrote: {csv_path}")
    print(f"Wrote: {attachment_path}")


def parse_args(argv: Iterable[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Crawl SWMU bidding/market-research notices from the official site.")
    parser.add_argument("--list-url", default=DEFAULT_LIST_URL)
    parser.add_argument("--base-site", default=DEFAULT_BASE_SITE)
    parser.add_argument("--output-dir", default="swmu_research_out")
    parser.add_argument("--keywords", default=",".join(DEFAULT_KEYWORDS), help="Comma-separated Chinese keywords.")
    parser.add_argument("--pages", type=int, default=0, help="Force number of list pages. 0 means infer from the first page.")
    parser.add_argument("--max-pages", type=int, default=200)
    parser.add_argument("--items-per-page", type=int, default=18)
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--sleep", type=float, default=0.2)
    parser.add_argument("--with-attachments", action="store_true", help="Also visit detail pages and index attachment links.")
    return parser.parse_args(list(argv))


def main(argv: Iterable[str] = sys.argv[1:]) -> int:
    args = parse_args(argv)
    notices = crawl_notices(args)
    write_outputs(notices, Path(args.output_dir))
    print(f"Done. Matching notices: {len(notices)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
