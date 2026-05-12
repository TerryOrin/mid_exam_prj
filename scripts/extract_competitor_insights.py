from __future__ import annotations

import html
import json
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin, urlparse

import requests


LEADERBOARD_API = "https://voteweb.pythonanywhere.com/api/post/leaderboard/"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

KEYWORDS = [
    "USR",
    "活動",
    "故事",
    "水井",
    "風雲客棧",
    "AIoT",
    "社區",
    "永續",
    "課程",
    "工作坊",
    "導覽",
    "計畫",
    "體驗",
    "論壇",
    "服務",
]

COMMON_PATHS = [
    "",
    "/events/",
    "/event/",
    "/stories/",
    "/story/",
    "/usr/",
    "/about/",
    "/contact/",
]

UI_PATTERNS = {
    "hero_section": [r"class=[\"'][^\"']*hero", r"hero-banner", r"hero-section"],
    "card_layout": [r"class=[\"'][^\"']*card", r"card-title", r"card-body"],
    "timeline_section": [r"timeline", r"milestone"],
    "search_ui": [r"type=[\"']search[\"']", r"name=[\"']q[\"']", r"bi-search"],
    "contact_form": [r"<form", r"name=[\"']email[\"']", r"聯絡", r"contact"],
    "chatbot_ui": [r"chatbot", r"api/chat", r"gemini", r"聊天"],
    "glass_effect": [r"glass", r"backdrop-filter"],
    "bootstrap_ui": [r"bootstrap", r"container-fluid", r"row", r"col-"],
    "django_admin": [r"/admin", r"csrfmiddlewaretoken"],
}


def fetch_json(url: str):
    response = requests.get(url, timeout=30, headers={"User-Agent": USER_AGENT})
    response.raise_for_status()
    return response.json()


def fetch_page(url: str):
    try:
        response = requests.get(
            url,
            timeout=20,
            headers={"User-Agent": USER_AGENT},
            allow_redirects=True,
        )
        content_type = (response.headers.get("content-type") or "").lower()
        return {
            "ok": True,
            "status": response.status_code,
            "url": response.url,
            "content_type": content_type,
            "text": response.text if "text/html" in content_type else "",
            "error": "",
        }
    except Exception as exc:  # pragma: no cover - network failures are runtime-only
        return {
            "ok": False,
            "status": None,
            "url": url,
            "content_type": "",
            "text": "",
            "error": str(exc),
        }


def clean_html_to_lines(html_text: str) -> list[str]:
    text = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", html_text)
    text = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", text)
    text = re.sub(r"(?is)<!--.*?-->", " ", text)
    text = re.sub(r"(?is)<[^>]+>", "\n", text)
    text = html.unescape(text)
    lines = []
    for raw in text.splitlines():
        line = re.sub(r"\s+", " ", raw).strip()
        if 6 <= len(line) <= 180:
            lines.append(line)
    return lines


def pick_keyword_lines(lines: Iterable[str], max_items: int = 16) -> list[str]:
    seen = set()
    picked = []
    for line in lines:
        if any(keyword in line for keyword in KEYWORDS):
            if line not in seen:
                seen.add(line)
                picked.append(line)
        if len(picked) >= max_items:
            break
    return picked


def detect_ui_signals(html_text: str) -> list[str]:
    signals = []
    for signal, patterns in UI_PATTERNS.items():
        if any(re.search(pattern, html_text, flags=re.I) for pattern in patterns):
            signals.append(signal)
    return signals


def is_pythonanywhere_site(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return host.endswith("pythonanywhere.com")


def build_site_candidates(site_url: str) -> list[str]:
    host = urlparse(site_url).netloc
    scheme = urlparse(site_url).scheme or "https"
    base = f"{scheme}://{host}/"
    return [urljoin(base, path) for path in COMMON_PATHS]


def extract_from_post(post: dict) -> dict:
    site_url = (post.get("link") or "").strip()
    candidates = build_site_candidates(site_url) if site_url else []
    crawled_pages = []
    snippets = []
    ui_signals = set()
    feature_sources = []
    best_root_status = None

    for idx, url in enumerate(candidates):
        page = fetch_page(url)
        if idx == 0:
            best_root_status = page["status"]
        crawled_pages.append(
            {
                "url": page["url"],
                "status": page["status"],
                "content_type": page["content_type"],
                "error": page["error"],
            }
        )
        if page["status"] != 200 or not page["text"]:
            continue
        lines = clean_html_to_lines(page["text"])
        selected = pick_keyword_lines(lines, max_items=8)
        for item in selected:
            snippets.append({"text": item, "source_url": page["url"]})
        signals = detect_ui_signals(page["text"])
        for signal in signals:
            ui_signals.add(signal)
        if selected or signals:
            feature_sources.append(page["url"])

    # Fallback: article description from leaderboard API
    content_text = (post.get("content") or "").strip()
    content_lines = [
        re.sub(r"\s+", " ", line).strip()
        for line in content_text.splitlines()
        if line.strip()
    ]
    content_selected = pick_keyword_lines(content_lines, max_items=8)
    if content_selected:
        for item in content_selected:
            snippets.append(
                {
                    "text": item,
                    "source_url": f"https://voteweb.pythonanywhere.com/articles_list/{post.get('slug')}/",
                }
            )

    # Deduplicate snippets
    dedup = []
    seen_text = set()
    for sn in snippets:
        text = sn["text"]
        if text not in seen_text:
            seen_text.add(text)
            dedup.append(sn)
    snippets = dedup[:20]

    return {
        "rank": None,  # filled later
        "slug": post.get("slug"),
        "article_title": post.get("title"),
        "student_id": (post.get("profile") or {}).get("studient_id") or post.get("studient_id"),
        "author": (post.get("profile") or {}).get("name"),
        "article_url": f"https://voteweb.pythonanywhere.com/articles_list/{post.get('slug')}/",
        "site_url": site_url,
        "site_host": urlparse(site_url).netloc if site_url else "",
        "is_pythonanywhere": bool(site_url and is_pythonanywhere_site(site_url)),
        "likes_count": post.get("likes_count", 0),
        "views": post.get("views", 0),
        "root_status": best_root_status,
        "ui_signals": sorted(ui_signals),
        "ui_signal_sources": feature_sources[:10],
        "usr_activity_snippets": snippets,
        "crawled_pages": crawled_pages,
    }


def summarize(results: list[dict]) -> dict:
    ui_counter = Counter()
    keyword_counter = Counter()
    useful_by_site = {}

    for row in results:
        ui_counter.update(row["ui_signals"])
        for sn in row["usr_activity_snippets"]:
            text = sn["text"]
            for kw in KEYWORDS:
                if kw in text:
                    keyword_counter[kw] += 1
        useful_by_site[row["site_url"]] = len(row["usr_activity_snippets"])

    top_sites = sorted(
        results,
        key=lambda r: (
            len(r["usr_activity_snippets"]),
            len(r["ui_signals"]),
            r.get("likes_count", 0),
            r.get("views", 0),
        ),
        reverse=True,
    )

    return {
        "site_count": len(results),
        "pythonanywhere_count": sum(1 for r in results if r["is_pythonanywhere"]),
        "available_site_count": sum(1 for r in results if r["root_status"] == 200),
        "ui_signal_counts": dict(ui_counter.most_common()),
        "keyword_counts": dict(keyword_counter.most_common()),
        "top_sites_by_extracted_content": [
            {
                "rank": row["rank"],
                "site_url": row["site_url"],
                "article_title": row["article_title"],
                "snippets": len(row["usr_activity_snippets"]),
                "ui_signals": row["ui_signals"],
            }
            for row in top_sites[:10]
        ],
    }


def write_markdown(path: Path, summary: dict, rows: list[dict]) -> None:
    ui_map = {
        "hero_section": "Hero 首屏",
        "card_layout": "卡片式資訊區塊",
        "timeline_section": "時間軸/里程碑",
        "search_ui": "搜尋介面",
        "contact_form": "聯絡表單",
        "chatbot_ui": "聊天機器人",
        "glass_effect": "玻璃擬態效果",
        "bootstrap_ui": "Bootstrap 響應式架構",
        "django_admin": "Django 後台整合",
    }
    lines = []
    lines.append(f"# 競品站點 USR/活動資訊擷取報告（{datetime.now().strftime('%Y-%m-%d %H:%M')}）")
    lines.append("")
    lines.append(f"- 來源站點數：{summary['site_count']}")
    lines.append(f"- PythonAnywhere 站點數：{summary['pythonanywhere_count']}")
    lines.append(f"- 可開啟站點（根網址 200）：{summary['available_site_count']}")
    lines.append("")
    lines.append("## UI/UX 常見優點（統計）")
    lines.append("")
    for key, count in summary["ui_signal_counts"].items():
        lines.append(f"- {ui_map.get(key, key)}：{count}")
    lines.append("")
    lines.append("## USR/活動關鍵詞出現次數")
    lines.append("")
    for kw, count in summary["keyword_counts"].items():
        lines.append(f"- {kw}：{count}")
    lines.append("")
    lines.append("## 各站擷取重點")
    lines.append("")
    for row in rows:
        lines.append(f"### #{row['rank']} {row['site_url']}")
        lines.append(f"- 文章標題：{row['article_title']}")
        lines.append(f"- 學號/作者：{row.get('student_id') or '-'} / {row.get('author') or '-'}")
        lines.append(f"- 根網址狀態：{row.get('root_status')}")
        if row["ui_signals"]:
            tags = "、".join(ui_map.get(x, x) for x in row["ui_signals"])
            lines.append(f"- UI 特徵：{tags}")
        else:
            lines.append("- UI 特徵：-")
        if row["usr_activity_snippets"]:
            lines.append("- USR/活動摘錄：")
            for sn in row["usr_activity_snippets"][:6]:
                lines.append(f"  - {sn['text']}（來源：{sn['source_url']}）")
        else:
            lines.append("- USR/活動摘錄：-")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def main():
    output_dir = Path("data/research")
    output_dir.mkdir(parents=True, exist_ok=True)

    posts = fetch_json(LEADERBOARD_API)
    rows = []
    for idx, post in enumerate(posts, start=1):
        row = extract_from_post(post)
        row["rank"] = idx
        rows.append(row)

    summary = summarize(rows)
    payload = {
        "generated_at": datetime.now().isoformat(),
        "source": LEADERBOARD_API,
        "summary": summary,
        "results": rows,
    }

    json_path = output_dir / "leaderboard_usr_activity_insights.json"
    md_path = output_dir / "leaderboard_usr_activity_insights.md"

    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    write_markdown(md_path, summary, rows)

    print(f"Saved JSON: {json_path}")
    print(f"Saved MD:   {md_path}")
    print(
        f"Sites={summary['site_count']}, PythonAnywhere={summary['pythonanywhere_count']}, "
        f"available={summary['available_site_count']}"
    )


if __name__ == "__main__":
    main()
