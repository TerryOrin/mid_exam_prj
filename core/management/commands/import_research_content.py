import json
import re
from datetime import datetime, timedelta
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from core.models import Event, StoryPost


SIMPLIFIED_MAP = str.maketrans(
    {
        "这": "這",
        "为": "為",
        "与": "與",
        "来": "來",
        "里": "裡",
        "点": "點",
        "应": "應",
        "实": "實",
        "网": "網",
        "观": "觀",
        "览": "覽",
        "台": "臺",
        "后": "後",
        "广": "廣",
        "见": "見",
        "计": "計",
        "划": "劃",
        "产": "產",
        "态": "態",
        "讯": "訊",
        "达": "達",
        "证": "證",
        "发": "發",
        "众": "眾",
        "结": "結",
        "协": "協",
        "开": "開",
        "显": "顯",
        "钟": "鐘",
        "营": "營",
        "导": "導",
        "验": "驗",
        "摄": "攝",
        "档": "檔",
        "乡": "鄉",
        "劳": "勞",
    }
)


EVENT_KEYWORDS = [
    "活動",
    "成果展",
    "工作坊",
    "博覽會",
    "啟動儀式",
    "市集",
    "導覽",
    "體驗",
    "課程",
    "參訪",
    "參與",
]


SKIP_PATTERNS = [
    r"^查看.*",
    r"^點擊.*",
    r"^深入了解.*",
    r"^立即.*",
    r"^歡迎.*",
    r"^首頁.*",
    r"^關於.*",
    r"^聯絡.*",
    r"^全部.*",
]


def _sanitize_text(value):
    text = (value or "").translate(SIMPLIFIED_MAP)
    text = re.sub(r"\s+", " ", text).strip()
    text = text.replace("→", "").replace("·", "．")
    return text


def _is_skip_text(text):
    if len(text) < 8:
        return True
    if re.fullmatch(r"[\W_]+", text):
        return True
    return any(re.match(pattern, text) for pattern in SKIP_PATTERNS)


def _classify_story_category(text):
    lowered = text.lower()
    if "aiot" in lowered or any(
        token in text for token in ["智慧", "辨識", "感測", "監測", "數位"]
    ):
        return "aiot"
    if any(token in lowered for token in ["usr"]) or any(
        token in text for token in ["社會責任", "永續", "共融", "地方創生", "計畫"]
    ):
        return "usr"
    if any(token in text for token in ["體驗", "導覽", "課程", "參訪"]):
        return "experience"
    return "water_story"


def _is_event_text(text):
    return any(keyword in text for keyword in EVENT_KEYWORDS) and len(text) >= 10


class Command(BaseCommand):
    help = "匯入排行榜擷取的 USR/活動/故事內容到資料庫。"

    def add_arguments(self, parser):
        parser.add_argument(
            "--json-path",
            default="data/research/leaderboard_usr_activity_insights.json",
            help="擷取結果 JSON 路徑",
        )
        parser.add_argument("--max-sites", type=int, default=16, help="最多處理站點數")
        parser.add_argument("--max-stories", type=int, default=48, help="最多匯入故事數")
        parser.add_argument("--max-events", type=int, default=20, help="最多匯入活動數")
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="只顯示摘要，不寫入資料庫",
        )

    def handle(self, *args, **options):
        json_path = Path(options["json_path"])
        if not json_path.exists():
            raise CommandError(f"找不到檔案：{json_path}")

        try:
            payload = json.loads(json_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise CommandError(f"JSON 解析失敗：{exc}") from exc

        results = payload.get("results", [])
        if not results:
            raise CommandError("JSON 內沒有可匯入的 results。")

        generated_at_raw = payload.get("generated_at")
        if generated_at_raw:
            imported_time = datetime.fromisoformat(generated_at_raw)
            if timezone.is_naive(imported_time):
                imported_time = timezone.make_aware(
                    imported_time, timezone.get_current_timezone()
                )
        else:
            imported_time = timezone.now()

        available_results = [item for item in results if item.get("root_status") == 200]
        selected_results = available_results[: options["max_sites"]]

        story_candidates = []
        event_candidates = []
        seen_text = set()
        for item in selected_results:
            snippets = item.get("usr_activity_snippets", [])
            for snippet in snippets:
                text = _sanitize_text(snippet.get("text"))
                source_url = snippet.get("source_url", item.get("site_url", ""))
                if _is_skip_text(text):
                    continue
                normalized = text.lower()
                if normalized in seen_text:
                    continue
                seen_text.add(normalized)

                candidate = {
                    "text": text,
                    "source_url": source_url,
                    "site_url": item.get("site_url", ""),
                    "site_host": item.get("site_host", ""),
                    "rank": item.get("rank"),
                    "student_id": item.get("student_id", ""),
                    "author": _sanitize_text(item.get("author", "")),
                    "article_title": _sanitize_text(item.get("article_title", "")),
                    "slug": item.get("slug", ""),
                }

                story_candidates.append(candidate)
                if _is_event_text(text):
                    event_candidates.append(candidate)

        story_candidates = story_candidates[: options["max_stories"]]
        event_candidates = event_candidates[: options["max_events"]]

        if options["dry_run"]:
            self.stdout.write(
                self.style.WARNING(
                    f"Dry-run: 可匯入故事 {len(story_candidates)} 筆，活動 {len(event_candidates)} 筆。"
                )
            )
            return

        created_story = 0
        updated_story = 0
        created_event = 0
        updated_event = 0

        for idx, item in enumerate(story_candidates, start=1):
            category = _classify_story_category(item["text"])
            title_seed = item["text"][:26]
            title = f"站點觀察#{item['rank']}｜{title_seed}"
            summary = item["text"][:120]
            content = (
                f"{item['text']}\n\n"
                f"來源網址：{item['source_url']}\n"
                f"站點：{item['site_url']}\n"
                f"排行榜：第 {item['rank']} 名，"
                f"{item['author']}（{item['student_id']}）\n"
                f"原文標題：{item['article_title']}"
            )
            slug = f"insight-story-r{item['rank']}-n{idx}"

            obj, created = StoryPost.objects.update_or_create(
                slug=slug,
                defaults={
                    "title": title,
                    "summary": summary,
                    "content": content,
                    "category": category,
                    "is_featured": idx <= 6,
                },
            )
            if created:
                created_story += 1
            else:
                updated_story += 1

        for idx, item in enumerate(event_candidates, start=1):
            title = item["text"][:80]
            short_description = item["text"][:120]
            description = (
                f"{item['text']}\n\n"
                f"此活動資訊整理自排行榜作品站點。\n"
                f"來源網址：{item['source_url']}\n"
                f"站點：{item['site_url']}\n"
                f"排行榜：第 {item['rank']} 名，"
                f"{item['author']}（{item['student_id']}）\n"
                f"原文標題：{item['article_title']}"
            )
            date_value = imported_time + timedelta(hours=idx)
            location = f"線上資訊（{item['site_host']}）"
            slug = f"insight-event-r{item['rank']}-n{idx}"

            _, created = Event.objects.update_or_create(
                slug=slug,
                defaults={
                    "title": title,
                    "short_description": short_description,
                    "description": description,
                    "date": date_value,
                    "location": location,
                    "is_featured": idx <= 6,
                },
            )
            if created:
                created_event += 1
            else:
                updated_event += 1

        self.stdout.write(
            self.style.SUCCESS(
                "匯入完成："
                f"故事 新增 {created_story} / 更新 {updated_story}；"
                f"活動 新增 {created_event} / 更新 {updated_event}。"
            )
        )
