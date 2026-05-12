from datetime import datetime
from pathlib import Path
import hashlib
import html
import re
import ssl
from urllib.parse import quote, urljoin, urlparse
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.utils.text import slugify

from core.models import Event, HeroSlide, StoryPost


class Command(BaseCommand):
    help = "建立/更新首頁輪播、活動與故事種子資料（優先使用官方真實資料）"

    OFFICIAL_BASE_URL = "https://ossr.nfu.edu.tw"
    OFFICIAL_VIDEO_LIST_URL = f"{OFFICIAL_BASE_URL}/zh_tw/practices/getVideoList"

    def _fetch_url_bytes(self, url: str) -> bytes:
        req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urlopen(req, timeout=30) as response:
                return response.read()
        except Exception:
            insecure_context = ssl._create_unverified_context()
            with urlopen(req, timeout=30, context=insecure_context) as response:
                return response.read()

    def _fetch_url_text(self, url: str) -> str:
        raw = self._fetch_url_bytes(url)
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return raw.decode("utf-8", errors="ignore")

    def _download_image(self, image_url: str, image_name: str, subdir: str) -> str:
        media_root = Path(settings.MEDIA_ROOT)
        target_dir = media_root / subdir
        target_dir.mkdir(parents=True, exist_ok=True)

        suffix = Path(urlparse(image_url).path).suffix.lower()
        if suffix not in {".jpg", ".jpeg", ".png", ".webp"}:
            suffix = ".jpg"

        safe_name = slugify(image_name) or f"{subdir}-image"
        file_name = f"{safe_name}{suffix}"
        abs_path = target_dir / file_name

        if not abs_path.exists():
            abs_path.write_bytes(self._fetch_url_bytes(image_url))

        return f"{subdir}/{file_name}"

    def _download_story_image(self, image_url: str, image_name: str) -> str:
        return self._download_image(image_url, image_name, "stories")

    def _download_event_image(self, image_url: str, image_name: str) -> str:
        return self._download_image(image_url, image_name, "events")

    def _parse_video_list_events(self, limit: int = 12) -> list[dict]:
        page = self._fetch_url_text(self.OFFICIAL_VIDEO_LIST_URL)
        blocks = re.findall(r'<li class="video_data[^>]*>.*?</li>', page, flags=re.S)

        events: list[dict] = []
        for idx, block in enumerate(blocks):
            title_match = re.search(r'title="([^"]+)"', block)
            if not title_match:
                title_match = re.search(r"title='([^']+)'", block)
            if title_match:
                title = title_match.group(1)
            else:
                h5_match = re.search(r"<h5>(.*?)</h5>", block, flags=re.S)
                title = h5_match.group(1) if h5_match else ""
            title = html.unescape(re.sub(r"<[^>]+>", "", title)).strip()
            title = re.sub(r"\s+", " ", title)
            if not title:
                continue

            date_match = re.search(
                r"<div class=\"video_time\">\s*<span[^>]*>\s*([0-9]{4}/[0-9]{2}/[0-9]{2})\s*</span>",
                block,
                flags=re.S,
            )
            if not date_match:
                continue
            date_text = date_match.group(1)
            date_obj = datetime.strptime(date_text, "%Y/%m/%d")
            event_date = timezone.make_aware(date_obj.replace(hour=10, minute=0))

            image_match = re.search(
                r'<img[^>]*class="video_snapshot"[^>]*src="([^"]+)"', block
            )
            image_url = ""
            if image_match:
                image_url = urljoin(self.OFFICIAL_BASE_URL, image_match.group(1).strip())

            api_match = re.search(r'data-api-url="([^"]+)"', block)
            if not api_match:
                api_match = re.search(r"data-api-url='([^']+)'", block)
            api_url = api_match.group(1).strip() if api_match else ""
            full_api_url = (
                urljoin(self.OFFICIAL_BASE_URL, quote(api_url, safe="/:?=&-%"))
                if api_url
                else ""
            )

            yt_match = re.search(r'data-video-url="([^"]+)"', block)
            video_url = yt_match.group(1).strip() if yt_match else ""
            if video_url.startswith("//"):
                video_url = f"https:{video_url}"

            digest = hashlib.md5(
                f"{date_text}|{title}|{api_url}".encode("utf-8")
            ).hexdigest()[:8]
            slug = f"ossr-{date_obj.strftime('%Y%m%d')}-{digest}"

            description_lines = [
                "此活動資訊整理自國立虎尾科技大學永續發展處（OSSR）官方網站。",
                f"活動名稱：{title}",
                f"活動日期：{date_text}",
                "活動地點：官方影音列表未提供，請以來源頁面為準。",
                f"來源列表：{self.OFFICIAL_VIDEO_LIST_URL}",
            ]
            if full_api_url:
                description_lines.append(f"活動資料頁：{full_api_url}")
            if video_url:
                description_lines.append(f"影片連結：{video_url}")

            events.append(
                {
                    "title": title,
                    "slug": slug,
                    "short_description": f"OSSR 官方活動影音紀錄：{title}",
                    "description": "\n".join(description_lines),
                    "date": event_date,
                    "location": "請見官方來源頁（OSSR）",
                    "is_featured": idx < 3,
                    "image_url": image_url,
                }
            )

            if len(events) >= limit:
                break

        if not events:
            raise ValueError("OSSR 官方活動列表解析失敗，未取得任何活動。")
        return events

    def _fallback_real_events(self) -> list[dict]:
        seed = [
            {
                "title": "深耕計畫目標二｜2026 兒童館無人機足球",
                "date": "2026/03/04",
                "image_url": "https://ossr.nfu.edu.tw/uploads/video_data_image/file/69a7d21a4eae35070cbf0199/0.jpg",
                "video_url": "https://www.youtube.com/embed/BNSiAJfvotk",
                "api_url": "/xhr/video_pro/show_api/深耕計畫目標二｜2026-兒童館無人機足球-78758628",
            },
            {
                "title": "深耕計畫目標二｜2026 不要科學怪人-樹藝森林精靈機器人工作坊",
                "date": "2026/02/24",
                "image_url": "https://ossr.nfu.edu.tw/uploads/video_data_image/file/699d5b974eae355e0b75d533/0.jpg",
                "video_url": "https://www.youtube.com/embed/n_6NbJkXCvc",
                "api_url": "/xhr/video_pro/show_api/深耕計畫目標二｜2026-不要科學怪人-樹藝森林精靈機器人工作坊-82982146",
            },
            {
                "title": "深耕計畫目標二｜2026尚虎雲Q Robot AI 教學",
                "date": "2026/02/10",
                "image_url": "https://ossr.nfu.edu.tw/uploads/video_data_image/file/698ae4494eae35d341aae1e4/0.jpg",
                "video_url": "https://www.youtube.com/embed/QnD0DEtjDlU",
                "api_url": "/xhr/video_pro/show_api/深耕計畫目標二｜2026尚虎雲Q-Robot-AI-教學-92585832",
            },
            {
                "title": "深耕計畫目標二｜2026 Q-ROBOT AI X白陽光祥童軍團",
                "date": "2026/02/05",
                "image_url": "https://ossr.nfu.edu.tw/uploads/video_data_image/file/69814e854eae35e66c548f35/0.jpg",
                "video_url": "https://www.youtube.com/embed/7uBmXc8n6bc",
                "api_url": "/xhr/video_pro/show_api/深耕計畫目標二｜2026-Q-ROBOT-AI-X白陽光祥童軍團-5894767",
            },
            {
                "title": "2025海洋科學節",
                "date": "2026/01/21",
                "image_url": "https://ossr.nfu.edu.tw/uploads/video_data_image/file/69814e714eae35e66c548f2d/0.jpg",
                "video_url": "https://www.youtube.com/embed/JVKk8f8ONu4",
                "api_url": "/xhr/video_pro/show_api/2025海洋科學節-87325268",
            },
            {
                "title": "深耕計畫目標二｜2025他里霧舊聖你沒來市集",
                "date": "2025/12/25",
                "image_url": "https://ossr.nfu.edu.tw/uploads/video_data_image/file/697192414eae3513317cf63c/0.jpg",
                "video_url": "https://www.youtube.com/embed/NBsC8GCmjfo",
                "api_url": "/xhr/video_pro/show_api/深耕計畫目標二｜2025他里霧舊聖你沒來市集-62342913",
            },
        ]

        rows: list[dict] = []
        for idx, item in enumerate(seed):
            date_obj = datetime.strptime(item["date"], "%Y/%m/%d")
            digest = hashlib.md5(
                f"{item['date']}|{item['title']}|{item['api_url']}".encode("utf-8")
            ).hexdigest()[:8]
            rows.append(
                {
                    "title": item["title"],
                    "slug": f"ossr-{date_obj.strftime('%Y%m%d')}-{digest}",
                    "short_description": f"OSSR 官方活動影音紀錄：{item['title']}",
                    "description": "\n".join(
                        [
                            "此活動資訊整理自國立虎尾科技大學永續發展處（OSSR）官方網站。",
                            f"活動名稱：{item['title']}",
                            f"活動日期：{item['date']}",
                            "活動地點：官方影音列表未提供，請以來源頁面為準。",
                            f"來源列表：{self.OFFICIAL_VIDEO_LIST_URL}",
                            f"活動資料頁：{urljoin(self.OFFICIAL_BASE_URL, quote(item['api_url'], safe='/:?=&-%'))}",
                            f"影片連結：{item['video_url']}",
                        ]
                    ),
                    "date": timezone.make_aware(date_obj.replace(hour=10, minute=0)),
                    "location": "請見官方來源頁（OSSR）",
                    "is_featured": idx < 3,
                    "image_url": item["image_url"],
                }
            )
        return rows

    def handle(self, *args, **options):
        self.stdout.write("開始建立種子資料...")

        slides_data = [
            {
                "title": "水井村在地故事與行動",
                "subtitle": "整合社區故事、USR 實踐與 AIoT 活動紀錄",
                "order": 1,
                "is_active": True,
            },
            {
                "title": "真實活動資訊",
                "subtitle": "活動清單優先使用 OSSR 官方來源並附上連結",
                "order": 2,
                "is_active": True,
            },
            {
                "title": "影像與故事並行",
                "subtitle": "每則內容盡量補上可追溯圖片與來源",
                "order": 3,
                "is_active": True,
            },
        ]
        for row in slides_data:
            HeroSlide.objects.update_or_create(order=row["order"], defaults=row)
        self.stdout.write(self.style.SUCCESS(f"  HeroSlide: {HeroSlide.objects.count()} 筆"))

        try:
            events_data = self._parse_video_list_events(limit=12)
            self.stdout.write(
                self.style.SUCCESS(
                    f"  Event source: OSSR 官方影音列表（{len(events_data)} 筆）"
                )
            )
        except Exception as exc:
            self.stdout.write(
                self.style.WARNING(
                    f"  官方活動抓取失敗，改用備援真實資料。原因：{exc}"
                )
            )
            events_data = self._fallback_real_events()

        old_fake_slugs = {
            "water-village-culture-walk",
            "fengcloud-opening-market",
            "aiot-smart-agriculture-workshop",
            "picture-book-story-sharing",
            "local-food-cooking-class",
            "nfu-community-development-forum",
        }

        new_slugs = {row["slug"] for row in events_data}
        Event.objects.filter(slug__in=old_fake_slugs).exclude(slug__in=new_slugs).delete()
        Event.objects.filter(slug__startswith="ossr-").exclude(slug__in=new_slugs).delete()

        for row in events_data:
            payload = row.copy()
            image_url = payload.pop("image_url", "")
            if image_url:
                try:
                    payload["cover_image"] = self._download_event_image(image_url, payload["slug"])
                except Exception as exc:
                    self.stdout.write(
                        self.style.WARNING(
                            f"  Event image download failed ({payload['slug']}): {exc}"
                        )
                    )
            Event.objects.update_or_create(slug=payload["slug"], defaults=payload)
        self.stdout.write(self.style.SUCCESS(f"  Event: {Event.objects.count()} 筆"))

        stories_data = [
            {
                "title": "深耕計畫目標二｜2026 兒童館無人機足球",
                "slug": "story-drone-football-2026",
                "summary": "OSSR 官方活動影音紀錄，含活動日期與影片來源。",
                "content": "來源：OSSR 官方影音列表\n活動日期：2026/03/04\n影片：https://www.youtube.com/embed/BNSiAJfvotk\n活動頁：https://ossr.nfu.edu.tw/zh_tw/practices/getVideoList",
                "category": "aiot",
                "is_featured": True,
                "image_url": "https://ossr.nfu.edu.tw/uploads/video_data_image/file/69a7d21a4eae35070cbf0199/0.jpg",
            },
            {
                "title": "深耕計畫目標二｜2026 不要科學怪人-樹藝森林精靈機器人工作坊",
                "slug": "story-forest-spirit-robot-2026",
                "summary": "OSSR 官方活動影音紀錄，含活動日期與影片來源。",
                "content": "來源：OSSR 官方影音列表\n活動日期：2026/02/24\n影片：https://www.youtube.com/embed/n_6NbJkXCvc\n活動頁：https://ossr.nfu.edu.tw/zh_tw/practices/getVideoList",
                "category": "experience",
                "is_featured": True,
                "image_url": "https://ossr.nfu.edu.tw/uploads/video_data_image/file/699d5b974eae355e0b75d533/0.jpg",
            },
            {
                "title": "深耕計畫目標二｜2026尚虎雲Q Robot AI 教學",
                "slug": "story-qrobot-ai-2026",
                "summary": "OSSR 官方活動影音紀錄，含活動日期與影片來源。",
                "content": "來源：OSSR 官方影音列表\n活動日期：2026/02/10\n影片：https://www.youtube.com/embed/QnD0DEtjDlU\n活動頁：https://ossr.nfu.edu.tw/zh_tw/practices/getVideoList",
                "category": "aiot",
                "is_featured": False,
                "image_url": "https://ossr.nfu.edu.tw/uploads/video_data_image/file/698ae4494eae35d341aae1e4/0.jpg",
            },
            {
                "title": "深耕計畫目標二｜2026 Q-ROBOT AI X白陽光祥童軍團",
                "slug": "story-qrobot-scout-2026",
                "summary": "OSSR 官方活動影音紀錄，含活動日期與影片來源。",
                "content": "來源：OSSR 官方影音列表\n活動日期：2026/02/05\n影片：https://www.youtube.com/embed/7uBmXc8n6bc\n活動頁：https://ossr.nfu.edu.tw/zh_tw/practices/getVideoList",
                "category": "aiot",
                "is_featured": False,
                "image_url": "https://ossr.nfu.edu.tw/uploads/video_data_image/file/69814e854eae35e66c548f35/0.jpg",
            },
            {
                "title": "2025海洋科學節",
                "slug": "story-ocean-science-2025",
                "summary": "OSSR 官方活動影音紀錄，含活動日期與影片來源。",
                "content": "來源：OSSR 官方影音列表\n活動日期：2026/01/21\n影片：https://www.youtube.com/embed/JVKk8f8ONu4\n活動頁：https://ossr.nfu.edu.tw/zh_tw/practices/getVideoList",
                "category": "experience",
                "is_featured": False,
                "image_url": "https://ossr.nfu.edu.tw/uploads/video_data_image/file/69814e714eae35e66c548f2d/0.jpg",
            },
            {
                "title": "深耕計畫目標二｜2025他里霧舊聖你沒來市集",
                "slug": "story-daliwu-market-2025",
                "summary": "OSSR 官方活動影音紀錄，含活動日期與影片來源。",
                "content": "來源：OSSR 官方影音列表\n活動日期：2025/12/25\n影片：https://www.youtube.com/embed/NBsC8GCmjfo\n活動頁：https://ossr.nfu.edu.tw/zh_tw/practices/getVideoList",
                "category": "water_story",
                "is_featured": True,
                "image_url": "https://ossr.nfu.edu.tw/uploads/video_data_image/file/697192414eae3513317cf63c/0.jpg",
            },
            {
                "title": "114年深耕計畫目標二成果影片",
                "slug": "story-usr-result-114",
                "summary": "OSSR 官方活動影音紀錄，含活動日期與影片來源。",
                "content": "來源：OSSR 官方影音列表\n活動日期：2025/11/26\n影片：https://www.youtube.com/embed/yaCiEvS0Tyg\n活動頁：https://ossr.nfu.edu.tw/zh_tw/practices/getVideoList",
                "category": "usr",
                "is_featured": True,
                "image_url": "https://ossr.nfu.edu.tw/uploads/video_data_image/file/696458f44eae350970def3ec/0.jpg",
            },
            {
                "title": "深耕計畫目標二｜尚虎雲在竹山小鎮社會實踐",
                "slug": "story-zhushan-practice-114",
                "summary": "OSSR 官方活動影音紀錄，含活動日期與影片來源。",
                "content": "來源：OSSR 官方影音列表\n活動日期：2025/10/25\n影片：https://www.youtube.com/embed/AO6kxiQ_3Sw\n活動頁：https://ossr.nfu.edu.tw/zh_tw/practices/getVideoList",
                "category": "water_story",
                "is_featured": False,
                "image_url": "https://ossr.nfu.edu.tw/uploads/video_data_image/file/69036aea0857777d1503b6fc/0.jpg",
            },
            {
                "title": "114年虎豐星星暑期教育營隊",
                "slug": "story-hufeng-summer-camp-114",
                "summary": "OSSR 官方活動影音紀錄，含活動日期與影片來源。",
                "content": "來源：OSSR 官方影音列表\n活動日期：2025/09/08\n影片：https://www.youtube.com/embed/qj0pXQCJ8Ng\n活動頁：https://ossr.nfu.edu.tw/zh_tw/practices/getVideoList",
                "category": "usr",
                "is_featured": False,
                "image_url": "https://ossr.nfu.edu.tw/uploads/video_data_image/file/69036a8f0857777d1503b6f2/0.jpg",
            },
            {
                "title": "114年第四屆亞太永續博覽會＆USR EXPO",
                "slug": "story-usr-expo-114",
                "summary": "OSSR 官方活動影音紀錄，含活動日期與影片來源。",
                "content": "來源：OSSR 官方影音列表\n活動日期：2025/07/13\n影片：https://www.youtube.com/embed/O0U4fD4slcA\n活動頁：https://ossr.nfu.edu.tw/zh_tw/practices/getVideoList",
                "category": "usr",
                "is_featured": False,
                "image_url": "https://ossr.nfu.edu.tw/uploads/video_data_image/file/69036a480857777d1503b6e8/0.jpg",
            },
            {
                "title": "114年 3-6月偏鄉職能造夢計畫",
                "slug": "story-career-dream-plan-114",
                "summary": "OSSR 官方活動影音紀錄，含活動日期與影片來源。",
                "content": "來源：OSSR 官方影音列表\n活動日期：2025/06/16\n影片：https://www.youtube.com/embed/vSnQjR9fHcY\n活動頁：https://ossr.nfu.edu.tw/zh_tw/practices/getVideoList",
                "category": "experience",
                "is_featured": False,
                "image_url": "https://ossr.nfu.edu.tw/uploads/video_data_image/file/69036a070857777d1503b6de/0.jpg",
            },
            {
                "title": "114年微藻固碳系統啟動儀式暨永續週成果展",
                "slug": "story-algae-carbon-114",
                "summary": "OSSR 官方活動影音紀錄，含活動日期與影片來源。",
                "content": "來源：OSSR 官方影音列表\n活動日期：2025/06/16\n影片：https://www.youtube.com/embed/PVAWtwl_1jQ\n活動頁：https://ossr.nfu.edu.tw/zh_tw/practices/getVideoList",
                "category": "aiot",
                "is_featured": False,
                "image_url": "https://ossr.nfu.edu.tw/uploads/video_data_image/file/690369cf0857777d1503b6d4/0.jpg",
            },
            {
                "title": "水井姻緣花手作工作坊",
                "slug": "story-waterwell-flower-workshop",
                "summary": "OSSR 官方新聞活動頁整理，含活動來源連結。",
                "content": "來源：OSSR 官方活動頁\n活動頁：https://ossr.nfu.edu.tw/zh_tw/newstrends/getNewsList/events/-%E6%B0%B8%E7%BA%8C%E8%99%95%E6%B7%B1%E8%80%95%E7%9B%AE%E6%A8%99%E4%BA%8C-%E5%B0%9A%E8%99%8E%E9%9B%B2%E5%AD%B8%E7%94%9F%E5%9C%98%E9%9A%8A-%E9%81%8B%E7%94%A8%E7%A7%91%E6%8A%80%E7%82%BA%E5%82%B3%E7%B5%B1%E5%B7%A5%E8%97%9D%E8%88%87%E8%BE%B2%E5%BB%A2%E5%86%8D%E7%94%9F%E5%B7%A5%E8%97%9D%E5%B8%B6%E4%BE%86%E6%96%B0%E7%94%9F%E5%91%BD-52459484",
                "category": "water_story",
                "is_featured": False,
                "image_url": "https://ossr.nfu.edu.tw/uploads/video_data_image/file/696458f44eae350970def3ec/0.jpg",
            },
        ]

        old_story_slugs = {
            "hundred-year-old-well-legend",
            "fengcloud-inn-past-and-present",
            "grandma-signature-dishes",
            "nfu-first-step-into-water-village",
            "from-classroom-to-field-usr",
            "aiot-monitoring-in-water-village",
            "waterwell-aiot-carbon-water-practice",
            "smart-aquaculture-with-community-craft",
            "q-robot-ai-teaching-record",
            "waterwell-yinyuan-flower-workshop",
            "otto-robot-course-record",
            "forest-spirit-robot-workshop",
        }
        new_story_slugs = {row["slug"] for row in stories_data}
        StoryPost.objects.filter(slug__in=old_story_slugs).exclude(
            slug__in=new_story_slugs
        ).delete()

        for row in stories_data:
            payload = row.copy()
            image_url = payload.pop("image_url", "")
            if image_url:
                try:
                    payload["image"] = self._download_story_image(image_url, payload["slug"])
                except Exception as exc:
                    self.stdout.write(
                        self.style.WARNING(
                            f"  Story image download failed ({payload['slug']}): {exc}"
                        )
                    )
            StoryPost.objects.update_or_create(slug=payload["slug"], defaults=payload)
        self.stdout.write(self.style.SUCCESS(f"  StoryPost: {StoryPost.objects.count()} 筆"))

        self.stdout.write(self.style.SUCCESS("\n種子資料建立完成。"))
