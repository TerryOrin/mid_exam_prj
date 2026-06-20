import base64
import io
import json
import logging
import os
import re
import secrets
import wave
from pathlib import Path
from urllib.parse import urlparse
from xml.sax.saxutils import escape as xml_escape

from PIL import Image as PILImage
from django.conf import settings
from django.http import JsonResponse
from django.core.paginator import Paginator
from django.db.models import Q, Case, When, Value, IntegerField
from django.shortcuts import render, get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.utils.crypto import constant_time_compare
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST, require_http_methods
import msgpack
import requests
from pydub import AudioSegment

from chat.dashboard import (
    IOT_DEFAULT_INTERVAL_MINUTES as DASHBOARD_IOT_DEFAULT_INTERVAL_MINUTES,
    IOT_DEFAULT_WINDOW_HOURS as DASHBOARD_IOT_DEFAULT_WINDOW_HOURS,
    IOT_MAX_WINDOW_HOURS as DASHBOARD_IOT_MAX_WINDOW_HOURS,
    IOT_POLL_SECONDS as DASHBOARD_IOT_POLL_SECONDS,
    build_iot_payload as build_aligned_iot_payload,
)
from chat import llm as shared_llm
from fengcloud import prompts as prompt_library

from .models import HeroSlide, Event, StoryPost
from .forms import ContactForm

logger = logging.getLogger(__name__)
AR_GUIDE_MODEL_SESSION_KEY = "aiot_selected_model"
AR_GUIDE_MAX_AUDIO_BYTES = 10 * 1024 * 1024
NAV_INTENT_KEYWORDS = [
    "打開",
    "點開",
    "前往",
    "進入",
    "帶我去",
    "跳轉",
    "連結",
    "網址",
    "頁面",
    "詳情",
    "詳細",
    "看看",
    "看",
]
AI_GUIDE_CHAT_SESSION_KEY = "ai_guide_chat_count"
AI_GUIDE_CHAT_MODEL = "deepseek-v4-flash"
AI_GUIDE_CHAT_TIMEOUT_SECONDS = 20.0
IOT_API_BROWSER_TOKEN_SESSION_KEY = "iot_browser_api_token"
LOCAL_STORIES = [
    {
        "keywords": ["風雲客棧", "理念", "三生", "虎科大", "數位聚落", "USR"],
        "content": "「風雲客棧數位平台」由國立虎尾科技大學電機資訊學院師生打造。以大學社會責任（USR）為核心，透過 AIoT 智慧養殖、STEAM 科技教育與在地文化保存，攜手水井村村民描繪「三生共好」永續藍圖。",
    },
    {
        "keywords": ["故事", "水井三寶", "姻緣花", "白馬", "烏龜", "歷史"],
        "content": "紀錄水井村歷史脈絡與沿海濕地生態，深入挖掘「水井三寶——姻緣花、白馬、烏龜」等珍貴文化象徵，讓百年記憶在數位空間傳承。",
    },
    {
        "keywords": ["活動", "報名", "Qrobt", "地圖", "導覽"],
        "content": "整合社區發展協會與學生工作坊活動資訊，提供線上報名系統。結合學生發想的「水井村 Qrobt 趣味地圖」，引領訪客實地探索百年漁村。",
    },
    {
        "keywords": ["AIoT", "水質", "養殖", "溫度", "pH", "溶氧量", "投餌"],
        "content": "團隊研發智慧養殖監控系統，漁民用手機便能即時掌握魚塭溫度、pH 值與溶氧量。搭配自動投餌與節水循環，降低人力負擔。",
    },
    {
        "keywords": ["STEAM", "教育", "機器人", "OTTO", "Matrix", "青銀共學"],
        "content": "大學生帶著 Matrix 機器人與互動程式課程走進偏鄉校園。透過青銀共學活動，讓阿公阿嬤與孫子輩產生新奇對話與笑聲。",
    },
    {
        "keywords": ["計畫人員", "主持團隊", "團隊名單", "計畫成員", "聯絡窗口"],
        "content": "本計畫團隊包含：計畫主持人許永和；共同主持人林正敏、張耀南；協同主持人莊文河、郭永明、吳添全、陳鳳雀；計畫聯絡人陳靜美。若需要窗口資訊，也可以直接詢問主持人、共同主持人、協同主持人或聯絡人。",
    },
    {
        "keywords": ["計畫主持人", "許永和", "資訊工程系", "特聘教授", "院長"],
        "content": "計畫主持人為許永和，單位是資訊工程系，職稱是特聘教授兼電機資訊學院院長，電話 0928471855，電子信箱 yhsheu@nfu.edu.tw。",
    },
    {
        "keywords": ["共同主持人", "林正敏", "張耀南", "永續發展暨社會責任處", "生物科技系"],
        "content": "共同主持人共有兩位：林正敏，單位是電機資訊學院，職稱為教授兼永續發展暨社會責任處執行長，電話 05-6313079，電子信箱 lcm@nfu.edu.tw；張耀南，單位是生物科技系，職稱為教授，電話 05-6315504，電子信箱 nelson@nfu.edu.tw。",
    },
    {
        "keywords": ["協同主持人", "莊文河", "郭永明", "吳添全", "陳鳳雀", "電子工程系", "通識教育中心"],
        "content": "協同主持人共有四位：莊文河，單位資訊工程系，職稱副教授，電話 05-6315584，電子信箱 riverjuang@nfu.edu.tw；郭永明，單位電子工程系，職稱助理教授，電話 05-631-5560，電子信箱 ymkuo@nfu.edu.tw；吳添全，單位電子工程系，職稱助理教授，電話 05-6315515，電子信箱 eetcwu@nfu.edu.tw；陳鳳雀，單位通識教育中心，職稱助理教授，電話 05-6315866，電子信箱 fanny@nfu.edu.tw。",
    },
    {
        "keywords": ["計畫聯絡人", "陳靜美", "電資學院", "研究副管理師", "聯絡人"],
        "content": "計畫聯絡人是陳靜美，單位為電資學院，職稱是研究副管理師，電話 05-6315602，電子信箱 g00441@nfu.edu.tw。",
    },
]


def _events_with_image_first(queryset):
    return queryset.annotate(
        has_image=Case(
            When(cover_image="", then=Value(0)),
            default=Value(1),
            output_field=IntegerField(),
        )
    ).order_by("-has_image", "-date")


def _stories_with_image_first(queryset):
    return queryset.annotate(
        has_image=Case(
            When(image="", then=Value(0)),
            default=Value(1),
            output_field=IntegerField(),
        )
    ).order_by("-has_image", "-created_at")


def home_view(request):
    slides = HeroSlide.objects.filter(is_active=True)
    featured_events = list(
        Event.objects.filter(is_featured=True).exclude(cover_image="").order_by("-date")[:3]
    )
    if len(featured_events) < 3:
        fallback_events = _events_with_image_first(Event.objects.filter(is_featured=True))
        featured_events = list(fallback_events[:3])

    water_stories = list(
        _stories_with_image_first(StoryPost.objects.filter(category="water_story"))[:3]
    )
    usr_posts = list(_stories_with_image_first(StoryPost.objects.filter(category="usr"))[:3])
    aiot_posts = list(_stories_with_image_first(StoryPost.objects.filter(category="aiot"))[:4])
    return render(
        request,
        "core/home.html",
        {
            "slides": slides,
            "featured_events": featured_events,
            "water_stories": water_stories,
            "usr_posts": usr_posts,
            "aiot_posts": aiot_posts,
        },
    )


def about_view(request):
    return render(request, "core/about.html")


def ar_guide_view(request):
    current_model = _current_ar_guide_model_name(request)
    raw_stops = [
        {
            "eyebrow": "AR Stop 01",
            "title": "風雲水井歷史介紹",
            "description": "辨識風雲水井的圖卡後，影片會直接貼合在圖片表面播放，呈現古井故事與場域背景。",
            "image": "img/about/about-fengyun.jpg",
            "badge": "風雲水井",
            "mission": "將鏡頭對準風雲水井圖卡，確認影片能穩定覆蓋在原圖位置上。",
            "video": "video/ar_video1.mp4",
            "target_index": 0,
            "marker_key": prompt_library.MARKER_HISTORY,
        },
        {
            "eyebrow": "AR Stop 02",
            "title": "水車運作展示",
            "description": "辨識水車圖卡後，影片會依照圖片比例覆蓋，示範灌溉與引水運作方式。",
            "image": "img/about/about-waterwheel.jpg",
            "badge": "水車設施",
            "mission": "移動鏡頭時確認影片仍鎖定在水車圖卡上，不要漂浮或錯位。",
            "video": "video/ar_video2.mp4",
            "target_index": 1,
            "marker_key": prompt_library.MARKER_WATERWHEEL,
        },
        {
            "eyebrow": "AR Stop 03",
            "title": "生態池 AIOT 解說",
            "description": "辨識生態池圖卡後，影片會固定在圖片上，補充 AIOT 水質監測與養殖應用。",
            "image": "img/about/about-pond.jpg",
            "badge": "AIOT 水質",
            "mission": "對準生態池圖卡後檢查影片是否完整覆蓋，並保持追蹤穩定。",
            "video": "video/ar_video3.mp4",
            "target_index": 2,
            "marker_key": prompt_library.MARKER_AIOT_POOL,
        },
    ]

    ar_stops = []
    for stop in raw_stops:
        image_path = Path(settings.BASE_DIR, "static", stop["image"])
        aspect_ratio = 0.72
        video_height_is_fallback = True
        if image_path.exists():
            with PILImage.open(image_path) as image:
                aspect_ratio = round(image.height / image.width, 4)
            video_height_is_fallback = False
        ar_stops.append(
            {
                **stop,
                "video_height": aspect_ratio,
                "video_height_is_fallback": video_height_is_fallback,
                "video_filename": Path(stop["video"]).name,
            }
        )

    mind_file_relpath = "ar/targets/shuijing_targets.mind"
    mind_file_path = Path(settings.BASE_DIR, "static", mind_file_relpath)
    mind_file_exists = mind_file_path.exists()
    mind_file_valid = False
    mind_file_error = ""
    mind_file_targets = []

    if mind_file_exists:
        try:
            mind_data = msgpack.unpackb(mind_file_path.read_bytes(), raw=False, strict_map_key=False)
            data_list = mind_data.get("dataList") or []
            mind_file_targets = [
                {
                    "index": index,
                    "width": item.get("targetImage", {}).get("width"),
                    "height": item.get("targetImage", {}).get("height"),
                }
                for index, item in enumerate(data_list)
            ]
            if mind_data.get("v") != 2:
                mind_file_error = "圖片辨識檔版本不符合 MindAR 1.2.5，請重新編譯。"
            elif len(data_list) < 3:
                mind_file_error = "圖片辨識檔內的 target 數量不足 3 張，請依入口、水車、魚塭順序重新編譯。"
            else:
                mind_file_valid = True
        except Exception as exc:
            logger.warning("Invalid MindAR target file: %s", exc)
            mind_file_error = (
                "圖片辨識檔 shuijing_targets.mind 可能損毀或編譯不完整，"
                "請重新用 MindAR Image Targets Compiler 產生 .mind 檔。"
            )

    context = {
        "ar_stops": ar_stops,
        "mind_file_relpath": mind_file_relpath,
        "mind_file_exists": mind_file_exists,
        "mind_file_valid": mind_file_valid,
        "mind_file_ready": mind_file_exists and mind_file_valid,
        "mind_file_error": mind_file_error,
        "mind_file_targets": mind_file_targets,
        "model_options": shared_llm.get_available_models(),
        "current_model": current_model,
        "current_model_meta": _ar_model_payload(current_model),
        "iot_api_token": _ensure_iot_browser_token(request),
    }
    return render(request, "core/ar_guide.html", context)


def events_list_view(request):
    query = request.GET.get("q", "")
    events = Event.objects.all()
    if query:
        events = events.filter(
            Q(title__icontains=query)
            | Q(short_description__icontains=query)
            | Q(description__icontains=query)
        )
    events = _events_with_image_first(events)
    paginator = Paginator(events, 6)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)
    return render(
        request,
        "core/events_list.html",
        {
            "page_obj": page_obj,
            "query": query,
        },
    )


def event_detail_view(request, slug):
    event = get_object_or_404(Event, slug=slug)
    return render(request, "core/event_detail.html", {"event": event})


def story_detail_view(request, slug):
    post = get_object_or_404(StoryPost, slug=slug)
    if post.category == "water_story":
        back_url = reverse("stories")
    elif post.category in ["usr", "experience", "aiot"]:
        back_url = f"{reverse('usr')}?category={post.category}"
    else:
        back_url = reverse("usr")
    return render(
        request,
        "core/story_detail.html",
        {
            "post": post,
            "back_url": back_url,
        },
    )


def stories_view(request):
    stories = _stories_with_image_first(StoryPost.objects.filter(category="water_story"))
    return render(request, "core/stories.html", {"stories": stories})


def usr_view(request):
    valid_categories = ["usr", "experience", "aiot"]
    selected_category = request.GET.get("category", "")
    posts = StoryPost.objects.filter(category__in=valid_categories)
    if selected_category in valid_categories:
        posts = posts.filter(category=selected_category)
    posts = _stories_with_image_first(posts)
    return render(
        request,
        "core/usr.html",
        {
            "posts": posts,
            "selected_category": selected_category,
        },
    )


def contact_view(request):
    form = ContactForm()
    submitted = False
    if request.method == "POST":
        form = ContactForm(request.POST)
        if form.is_valid():
            submitted = True
            form = ContactForm()
    return render(
        request,
        "core/contact.html",
        {
            "form": form,
            "submitted": submitted,
        },
    )


def _parse_bounded_int(raw_value, *, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, value))


def _json_no_store(payload: dict[str, object], *, status: int = 200):
    response = JsonResponse(payload, status=status)
    response["Cache-Control"] = "no-store, max-age=0"
    return response


def _iot_api_master_token() -> str:
    return (os.environ.get("IOT_API_MASTER_TOKEN") or "").strip()


def _ensure_iot_browser_token(request) -> str:
    token = request.session.get(IOT_API_BROWSER_TOKEN_SESSION_KEY)
    if isinstance(token, str) and token.strip():
        return token

    token = secrets.token_urlsafe(24)
    request.session[IOT_API_BROWSER_TOKEN_SESSION_KEY] = token
    request.session.modified = True
    return token


def _extract_iot_api_token(request) -> str:
    auth_header = str(request.headers.get("Authorization") or "").strip()
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()
    return str(request.headers.get("X-IoT-Token") or "").strip()


def _has_valid_iot_api_token(request) -> bool:
    provided = _extract_iot_api_token(request)
    if not provided:
        return False

    master_token = _iot_api_master_token()
    if master_token and constant_time_compare(provided, master_token):
        return True

    browser_token = request.session.get(IOT_API_BROWSER_TOKEN_SESSION_KEY)
    if isinstance(browser_token, str) and browser_token and constant_time_compare(
        provided,
        browser_token,
    ):
        return True

    return False


def _require_iot_api_token(request):
    if _has_valid_iot_api_token(request):
        return None
    return _json_no_store(
        {
            "error": "Missing or invalid IoT API token.",
            "detail": "Send X-IoT-Token: <token> or Authorization: Bearer <token>.",
        },
        status=403,
    )


def iot_war_room_view(request):
    initial_payload = build_aligned_iot_payload()
    current_model = shared_llm.resolve_model(request.session.get(AR_GUIDE_MODEL_SESSION_KEY)).key
    return render(
        request,
        "core/iot_war_room.html",
        {
            "initial_payload": initial_payload,
            "poll_seconds": DASHBOARD_IOT_POLL_SECONDS,
            "model_options": shared_llm.get_available_models(),
            "current_model": current_model,
            "current_model_meta": _ar_model_payload(current_model),
            "iot_api_token": _ensure_iot_browser_token(request),
        },
    )


@csrf_exempt
@require_http_methods(["GET", "POST"])
def iot_data_api(request):
    auth_error = _require_iot_api_token(request)
    if auth_error is not None:
        return auth_error

    if request.method == "POST":
        # Future hardware ingest hook:
        # When ESP32 or other microcontrollers start POSTing real sensor readings,
        # validate JSON fields here, verify a device credential such as
        # `X-Device-Token`, and replace this stub with
        # SensorReading.objects.create(...) or a queue/cache write path.
        # Example expected payload keys:
        # {"pond": "示範池", "measured_at": "...", "temperature_c": 25.1, "ph": 7.4, "dissolved_oxygen_mg_l": 6.2}
        return _json_no_store(
            {
                "resource": "iot_data",
                "detail": "Real sensor ingestion stub is ready. Persist validated POST payload here when ESP32 upload is enabled.",
            },
            status=501,
        )

    hours = _parse_bounded_int(
        request.GET.get("hours"),
        default=DASHBOARD_IOT_DEFAULT_WINDOW_HOURS,
        minimum=1,
        maximum=DASHBOARD_IOT_MAX_WINDOW_HOURS,
    )
    interval_minutes = _parse_bounded_int(
        request.GET.get("interval_minutes"),
        default=DASHBOARD_IOT_DEFAULT_INTERVAL_MINUTES,
        minimum=3,
        maximum=15,
    )
    return _json_no_store(
        build_aligned_iot_payload(hours=hours, interval_minutes=interval_minutes)
    )


def _normalise_current_metrics(payload: dict[str, object]) -> dict[str, float]:
    try:
        temperature = float(payload["temperature_c"])
        ph_value = float(payload["ph"])
        dissolved_oxygen = float(payload["dissolved_oxygen_mg_l"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("current payload must include temperature_c, ph, and dissolved_oxygen_mg_l.") from exc

    return {
        "temperature_c": round(temperature, 2),
        "ph": round(ph_value, 2),
        "dissolved_oxygen_mg_l": round(dissolved_oxygen, 2),
    }


def _heuristic_ai_diagnosis(current: dict[str, float]) -> dict[str, object]:
    facts: list[str] = []
    advice: list[str] = []
    severity = "good"

    dissolved_oxygen = current["dissolved_oxygen_mg_l"]
    temperature = current["temperature_c"]
    ph_value = current["ph"]

    if dissolved_oxygen < 5.0:
        severity = "alert"
        facts.append(f"溶氧 {dissolved_oxygen:.2f} mg/L，低於 5.0 mg/L 警戒值。")
        advice.append("目前溶氧量偏低，建議優先開啟水車或增氧設備。")
    elif dissolved_oxygen < 5.5:
        severity = "watch"
        facts.append(f"溶氧 {dissolved_oxygen:.2f} mg/L，接近清晨風險區。")
        advice.append("溶氧量接近警戒值，建議持續觀察清晨變化並預作增氧準備。")

    if temperature > 28.5:
        severity = "alert" if severity == "alert" else "watch"
        facts.append(f"水溫 {temperature:.2f} °C，午後熱壓力偏高。")
        advice.append("水溫偏高，建議加強循環、避免午後過量投餌。")
    elif temperature < 21.5:
        severity = "alert" if severity == "alert" else "watch"
        facts.append(f"水溫 {temperature:.2f} °C，低於常態投餌甜蜜區。")
        advice.append("水溫偏低，建議放慢投餌節奏並觀察魚群活動力。")

    if ph_value < 6.8 or ph_value > 8.2:
        severity = "alert" if severity == "alert" else "watch"
        facts.append(f"pH {ph_value:.2f}，已偏離建議區間 6.8–8.2。")
        advice.append("pH 有偏移，建議檢查換水節奏、藻相與投餌負荷。")

    if not advice:
        facts.append("三項核心指標都仍在建議區間內。")
        advice.append("目前水質大致穩定，建議維持例行巡檢並持續觀察日夜波動。")

    title_map = {
        "alert": "需立即處理",
        "watch": "建議持續觀察",
        "good": "狀態穩定",
    }
    return {
        "severity": severity,
        "title": title_map[severity],
        "advice": " ".join(advice[:2]),
        "facts": facts[:3],
    }


def _extract_json_object(text: str) -> str:
    fenced_match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, flags=re.DOTALL)
    if fenced_match:
        return fenced_match.group(1)

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("Model reply did not contain a JSON object.")
    return text[start : end + 1]


def _llm_ai_diagnosis(current: dict[str, float], model_name: str) -> dict[str, object]:
    system_prompt = prompt_library.build_aiot_diagnosis_system_prompt(
        current_temp=f"{current['temperature_c']:.2f} °C",
        current_ph=f"{current['ph']:.2f}",
        current_do=f"{current['dissolved_oxygen_mg_l']:.2f} mg/L",
    )
    reply = shared_llm.direct_chat(
        "請輸出本次水質診斷 JSON。",
        system_prompt=system_prompt,
        model_name=model_name,
    )
    payload = json.loads(_extract_json_object(reply))

    severity = str(payload.get("severity") or "").strip().lower()
    if severity not in {"good", "watch", "alert"}:
        raise ValueError("Model reply contained an unsupported severity.")

    title = str(payload.get("title") or "").strip()
    advice = str(payload.get("advice") or "").strip()
    raw_facts = payload.get("facts") or []
    if isinstance(raw_facts, str):
        raw_facts = [raw_facts]
    if not isinstance(raw_facts, list):
        raw_facts = []
    facts = [str(item).strip() for item in raw_facts if str(item).strip()][:3]

    if not title or not advice:
        raise ValueError("Model reply is missing title or advice.")

    return {
        "severity": severity,
        "title": title,
        "advice": advice,
        "facts": facts or ["模型未提供額外觀察重點。"],
    }


@csrf_exempt
@require_http_methods(["POST"])
def ai_diagnose_api(request):
    auth_error = _require_iot_api_token(request)
    if auth_error is not None:
        return auth_error

    try:
        body = json.loads(request.body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return _json_no_store({"error": "Request body must be valid JSON."}, status=400)

    raw_current = body.get("current") if isinstance(body, dict) else None
    if not isinstance(raw_current, dict):
        return _json_no_store({"error": "current payload is required."}, status=400)

    try:
        current = _normalise_current_metrics(raw_current)
    except ValueError as exc:
        return _json_no_store({"error": str(exc)}, status=400)

    model_name = ""
    if isinstance(body.get("model"), str):
        model_name = body["model"].strip()
    if model_name:
        try:
            model_name = shared_llm.resolve_model(model_name).key
        except ValueError as exc:
            return _json_no_store({"error": str(exc)}, status=400)
        request.session[AR_GUIDE_MODEL_SESSION_KEY] = model_name

    diagnosis = _heuristic_ai_diagnosis(current)
    source = "heuristic-fallback"
    if model_name:
        try:
            diagnosis = _llm_ai_diagnosis(current, model_name)
            source = "selected-llm"
        except Exception as exc:  # noqa: BLE001
            logger.warning("AI diagnose LLM fallback triggered: %s", exc)

    return _json_no_store(
        {
            "resource": "ai_diagnose",
            "generated_at": timezone.localtime().isoformat(),
            "source": source,
            "model": _ar_model_payload(model_name) if model_name else None,
            **diagnosis,
        }
    )


MAX_CHAT_PER_SESSION = 30


def _tokenize_for_search(text):
    """Tokenize mixed Chinese/English text for lightweight local search."""
    lowered = (text or "").lower()
    en_tokens = re.findall(r"[a-z0-9_]+", lowered)
    zh_chars = "".join(re.findall(r"[\u4e00-\u9fff]", lowered))
    if len(zh_chars) >= 2:
        zh_tokens = [zh_chars[i : i + 2] for i in range(len(zh_chars) - 1)]
    else:
        zh_tokens = list(zh_chars)
    return set(en_tokens + zh_tokens)


def _rank_local_content_scored(user_message, max_events=20, max_posts=30):
    """Rank events/posts by token overlap with user query."""
    query_tokens = _tokenize_for_search(user_message)

    now = timezone.localtime()
    candidate_events = list(Event.objects.filter(date__gte=now).order_by("date")[:max_events])
    if not candidate_events:
        candidate_events = list(Event.objects.order_by("-date")[:max_events])

    candidate_posts = list(
        StoryPost.objects.filter(category__in=["water_story", "usr", "experience", "aiot"])
        .order_by("-updated_at")[:max_posts]
    )

    scored_events = []
    for event in candidate_events:
        text_blob = f"{event.title} {event.short_description} {event.description} {event.location}"
        score = len(query_tokens & _tokenize_for_search(text_blob))
        scored_events.append((score, event))
    scored_events.sort(key=lambda item: (item[0], item[1].date), reverse=True)

    scored_posts = []
    for post in candidate_posts:
        text_blob = f"{post.title} {post.summary} {post.content[:320]} {post.get_category_display()}"
        score = len(query_tokens & _tokenize_for_search(text_blob))
        scored_posts.append((score, post))
    scored_posts.sort(key=lambda item: (item[0], item[1].updated_at), reverse=True)
    return scored_events, scored_posts


def _rank_local_content(user_message, max_events=20, max_posts=30):
    scored_events, scored_posts = _rank_local_content_scored(
        user_message, max_events=max_events, max_posts=max_posts
    )

    top_events = [item[1] for item in scored_events if item[0] > 0][:4]
    top_posts = [item[1] for item in scored_posts if item[0] > 0][:5]

    if not top_events:
        top_events = [item[1] for item in scored_events[:3]]
    if not top_posts:
        top_posts = [item[1] for item in scored_posts[:4]]

    return top_events, top_posts


def _resolve_redirect_target(request, user_message, page_path=""):
    """Resolve best redirect target for chatbot query."""
    text = (user_message or "").strip()
    lowered = text.lower()
    nav_intent = any(keyword in text or keyword in lowered for keyword in NAV_INTENT_KEYWORDS)
    asks_event = "活動" in text or "event" in lowered
    asks_story = "故事" in text or "story" in lowered
    asks_usr = "usr" in lowered or "成果" in text or "aiot" in lowered or "體驗" in text

    scored_events, scored_posts = _rank_local_content_scored(user_message)
    top_event_score, top_event = scored_events[0] if scored_events else (0, None)
    top_post_score, top_post = scored_posts[0] if scored_posts else (0, None)

    prefer_story = asks_story or asks_usr

    if prefer_story:
        if top_post and top_post_score >= 2 and (
            nav_intent or asks_story or asks_usr or top_post.title in text
        ):
            target_path = top_post.get_absolute_url()
            if not page_path.startswith(target_path):
                return _absolute_link(request, target_path)
        if top_event and top_event_score >= 2 and (nav_intent or top_event.title in text):
            target_path = top_event.get_absolute_url()
            if not page_path.startswith(target_path):
                return _absolute_link(request, target_path)
    else:
        if top_event and top_event_score >= 2 and (nav_intent or asks_event or top_event.title in text):
            target_path = top_event.get_absolute_url()
            if not page_path.startswith(target_path):
                return _absolute_link(request, target_path)
        if top_post and top_post_score >= 2 and (nav_intent or top_post.title in text):
            target_path = top_post.get_absolute_url()
            if not page_path.startswith(target_path):
                return _absolute_link(request, target_path)

    # Category-level redirects for broad navigation requests.
    if nav_intent or asks_event or asks_story or asks_usr:
        if asks_event:
            target_path = reverse("events_list")
        elif asks_story:
            target_path = reverse("stories")
        elif "aiot" in lowered or "智慧" in text:
            target_path = f"{reverse('usr')}?category=aiot"
        elif "體驗" in text:
            target_path = f"{reverse('usr')}?category=experience"
        elif asks_usr:
            target_path = f"{reverse('usr')}?category=usr"
        else:
            target_path = ""

        if target_path and not page_path.startswith(target_path.split("?")[0]):
            return _absolute_link(request, target_path)

    return ""


def _build_local_fallback_reply(user_message, request=None, page_path="", page_title=""):
    """Return a useful answer from local database when Gemini is unavailable."""
    matched_events, matched_stories = _rank_local_content(user_message)
    matched_events = matched_events[:2]
    matched_stories = matched_stories[:2]

    lines = ["目前 AI 雲端服務忙碌，先提供站內可查到的資訊："]
    if request:
        current_url = _absolute_link(request, page_path or "/")
        lines.append(f"目前頁面：{page_title or '未提供'}（{current_url}）")

    if matched_events:
        lines.append("近期活動：")
        for event in matched_events:
            event_time = timezone.localtime(event.date).strftime("%Y/%m/%d %H:%M")
            if request:
                event_url = _absolute_link(request, event.get_absolute_url())
                lines.append(f"- {event.title}（{event_time}，{event.location}）\n  連結：{event_url}")
            else:
                lines.append(f"- {event.title}（{event_time}，{event.location}）")

    if matched_stories:
        lines.append("相關文章：")
        for story in matched_stories:
            if request:
                lines.append(
                    f"- [{story.get_category_display()}] {story.title}：{story.summary}\n"
                    f"  連結：{_absolute_link(request, story.get_absolute_url())}"
                )
            else:
                lines.append(f"- [{story.get_category_display()}] {story.title}：{story.summary}")

    if request:
        lines.append(
            "快速入口："
            f"{_absolute_link(request, reverse('events_list'))}、"
            f"{_absolute_link(request, reverse('stories'))}、"
            f"{_absolute_link(request, reverse('usr'))}"
        )
    lines.append("你也可以再提供更具體關鍵字，我會再幫你精準整理。")
    return "\n".join(lines)


def _build_chat_context_payload(request, user_message="", page_path="", page_title=""):
    """Collect concise website context for Gemini prompt grounding."""
    navigation = {
        "首頁": _absolute_link(request, reverse("home")),
        "關於客棧": _absolute_link(request, reverse("about")),
        "活動資訊": _absolute_link(request, reverse("events_list")),
        "水井故事": _absolute_link(request, reverse("stories")),
        "USR成果": _absolute_link(request, reverse("usr")),
        "AR導覽": _absolute_link(request, reverse("ar_guide")),
        "聯絡我們": _absolute_link(request, reverse("contact")),
    }

    ranked_events, ranked_posts = _rank_local_content(user_message)
    upcoming_events = ranked_events[:4]
    latest_posts = ranked_posts[:6]

    event_lines = []
    for event in upcoming_events:
        event_link = _absolute_link(request, event.get_absolute_url())
        event_time = timezone.localtime(event.date).strftime("%Y/%m/%d %H:%M")
        event_lines.append(f"- {event.title}｜{event_time}｜{event.location}｜{event_link}")

    post_lines = []
    for post in latest_posts:
        category_label = post.get_category_display()
        post_lines.append(
            f"- [{category_label}] {post.title}：{post.summary[:80]}（詳見：{_absolute_link(request, post.get_absolute_url())}）"
        )

    nav_lines = [f"- {name}：{url}" for name, url in navigation.items()]
    current_page = (
        f"路徑：{page_path or '/'}；標題：{page_title or '未提供'}；網址：{_absolute_link(request, page_path or '/')}"
    )

    return {
        "current_page": current_page,
        "navigation_lines": "\n".join(nav_lines),
        "event_lines": "\n".join(event_lines) if event_lines else "- 目前沒有活動資料",
        "post_lines": "\n".join(post_lines) if post_lines else "- 目前沒有文章資料",
    }


def _absolute_link(request, path):
    """Build absolute URL from route path."""
    if path.startswith("http://") or path.startswith("https://"):
        return path
    return request.build_absolute_uri(path)


def _relative_internal_url(url: str) -> str:
    raw_url = str(url or "").strip()
    if not raw_url:
        return ""

    parsed = urlparse(raw_url)
    if parsed.scheme or parsed.netloc:
        if not parsed.path.startswith("/"):
            return ""
        query = f"?{parsed.query}" if parsed.query else ""
        return f"{parsed.path}{query}"

    if not raw_url.startswith("/"):
        return ""
    return raw_url


def _llm_message_text(message) -> str:
    content = getattr(message, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
                continue
            text = getattr(item, "text", None)
            if isinstance(text, str):
                parts.append(text)
        return "".join(parts)
    return ""


def _build_ai_guide_action_options(request, user_message: str = "") -> list[dict[str, str]]:
    options = [
        {"label": "首頁", "url": reverse("home")},
        {"label": "關於計畫", "url": reverse("about")},
        {"label": "近期活動", "url": reverse("events_list")},
        {"label": "水文化故事", "url": reverse("stories")},
        {"label": "USR 成果", "url": reverse("usr")},
        {"label": "AR 導覽", "url": reverse("ar_guide")},
        {"label": "AIOT 水質助手", "url": reverse("chat:page")},
        {"label": "IoT 戰情室", "url": reverse("iot_war_room")},
        {"label": "聯絡我們", "url": reverse("contact")},
    ]

    matched_events, matched_posts = _rank_local_content(user_message)
    for event in matched_events[:2]:
        options.append({"label": f"活動｜{event.title}", "url": event.get_absolute_url()})
    for post in matched_posts[:2]:
        options.append(
            {
                "label": f"{post.get_category_display()}｜{post.title}",
                "url": post.get_absolute_url(),
            }
        )

    deduped: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    for item in options:
        url = item["url"]
        if url in seen_urls:
            continue
        seen_urls.add(url)
        deduped.append(item)
    return deduped


def _retrieve_local_stories(user_message: str) -> list[dict[str, object]]:
    text = str(user_message or "").strip()
    lowered = text.lower()
    matched: list[dict[str, object]] = []

    for story in LOCAL_STORIES:
        keywords = [str(keyword).strip() for keyword in story.get("keywords", []) if str(keyword).strip()]
        matched_keywords = [
            keyword
            for keyword in keywords
            if keyword in text or keyword.lower() in lowered
        ]
        if not matched_keywords:
            continue
        matched.append(
            {
                "keywords": keywords,
                "matched_keywords": matched_keywords,
                "content": str(story.get("content") or "").strip(),
            }
        )

    matched.sort(key=lambda item: len(item["matched_keywords"]), reverse=True)
    return matched


def _build_ai_guide_story_reference(user_message: str) -> str:
    matched_stories = _retrieve_local_stories(user_message)
    if not matched_stories:
        return ""

    story_lines = []
    for index, story in enumerate(matched_stories[:3], start=1):
        keywords = "、".join(story["matched_keywords"])
        story_lines.append(f"{index}. 命中關鍵字：{keywords}\n內容：{story['content']}")
    return "\n\n".join(story_lines)


def _default_ai_guide_button_label(url: str) -> str:
    if url.startswith(reverse("iot_war_room")):
        return "前往 IoT 戰情室"
    if url.startswith(reverse("chat:page")):
        return "前往 AIOT 水質助手"
    if url.startswith(reverse("events_list")):
        return "查看近期活動"
    if url.startswith(reverse("stories")):
        return "查看水文化故事"
    if url.startswith(reverse("usr")):
        return "查看 USR 成果"
    if url.startswith(reverse("ar_guide")):
        return "開啟 AR 導覽"
    if url.startswith(reverse("contact")):
        return "前往聯絡頁面"
    if url.startswith(reverse("about")):
        return "查看計畫介紹"
    if url.startswith("/events/"):
        return "查看活動詳情"
    if url.startswith("/stories/"):
        return "查看內容詳情"
    return "前往相關頁面"


def _should_force_ai_guide_action(user_message: str) -> bool:
    text = str(user_message or "").strip()
    lowered = text.lower()
    for keyword in prompt_library.CHAT_WIDGET_FORCE_ACTION_KEYWORDS:
        normalized_keyword = keyword.lower()
        if keyword in text or normalized_keyword in lowered:
            return True
    return False


def _suggest_ai_guide_action_url(request, user_message: str, page_path: str = "") -> str:
    text = (user_message or "").strip()
    lowered = text.lower()
    current_path = _relative_internal_url(page_path or "/") or "/"

    manual_target = ""
    if "戰情室" in text or "war room" in lowered:
        manual_target = reverse("iot_war_room")
    elif "aiot" in lowered or "水質助手" in text:
        manual_target = reverse("chat:page")
    elif "近期活動" in text or "活動" in text or "event" in lowered:
        manual_target = reverse("events_list")
    elif "故事" in text or "story" in lowered:
        manual_target = reverse("stories")
    elif "usr" in lowered or "成果" in text:
        manual_target = reverse("usr")
    elif "ar 導覽" in lowered or ("ar" in lowered and "導覽" in text) or "掃描導覽" in text:
        manual_target = reverse("ar_guide")
    elif "聯絡" in text or "contact" in lowered:
        manual_target = reverse("contact")
    elif "關於計畫" in text or "計畫介紹" in text or "about" in lowered:
        manual_target = reverse("about")

    if manual_target:
        return "" if current_path.split("?")[0] == manual_target.split("?")[0] else manual_target

    legacy_target = _relative_internal_url(
        _resolve_redirect_target(request, user_message, page_path=page_path)
    )
    if legacy_target and current_path.split("?")[0] != legacy_target.split("?")[0]:
        return legacy_target
    return ""


def _build_ai_guide_local_reply(user_message: str) -> str:
    matched_stories = _retrieve_local_stories(user_message)
    if matched_stories:
        return str(matched_stories[0]["content"])

    matched_events, matched_posts = _rank_local_content(user_message)
    lowered = (user_message or "").lower()

    if matched_events and ("活動" in user_message or "event" in lowered):
        return f"我先幫你對到近期活動，從「{matched_events[0].title}」開始看會最快。"
    if matched_posts and ("故事" in user_message or "usr" in lowered or "成果" in user_message):
        return f"我找到一則相近內容：「{matched_posts[0].title}」，你可以先從這裡看。"
    if matched_posts and ("aiot" in lowered or "水質助手" in user_message):
        return f"如果你想看即時水質互動，先從「{matched_posts[0].title}」相關內容延伸最合適。"
    if matched_events:
        return f"我先整理到和你問題最接近的活動是「{matched_events[0].title}」。"
    if matched_posts:
        return f"我找到一則相近內容：「{matched_posts[0].title}」。"
    return "我可以幫你找近期活動、USR 成果、AIOT 水質助手或 IoT 戰情室。"


def _build_ai_guide_payload(
    reply_text: str,
    *,
    has_action: bool = False,
    button_label: str = "",
    url: str = "",
) -> dict[str, object]:
    cleaned_url = _relative_internal_url(url)
    show_action = bool(has_action and cleaned_url)
    return {
        "reply_text": str(reply_text or "").strip() or "我先幫你整理站內資訊。",
        "suggested_action": {
            "has_action": show_action,
            "button_label": button_label.strip() if show_action else "",
            "url": cleaned_url if show_action else "",
        },
    }


def _build_ai_guide_fallback_payload(
    request,
    user_message: str,
    page_path: str = "",
    *,
    raw_reply: str = "",
) -> dict[str, object]:
    fallback_url = _suggest_ai_guide_action_url(request, user_message, page_path=page_path)
    force_action = _should_force_ai_guide_action(user_message)

    if raw_reply.strip():
        return _build_ai_guide_payload(
            raw_reply.strip(),
            has_action=bool(force_action and fallback_url),
            button_label=_default_ai_guide_button_label(fallback_url) if force_action else "",
            url=fallback_url if force_action else "",
        )

    return _build_ai_guide_payload(
        _build_ai_guide_local_reply(user_message),
        has_action=bool(fallback_url),
        button_label=_default_ai_guide_button_label(fallback_url),
        url=fallback_url,
    )


def _coerce_ai_guide_payload(
    request,
    user_message: str,
    page_path: str,
    payload: dict[str, object],
) -> dict[str, object]:
    if not isinstance(payload, dict):
        return _build_ai_guide_fallback_payload(request, user_message, page_path=page_path)

    current_path = _relative_internal_url(page_path or "/") or "/"
    action_options = _build_ai_guide_action_options(request, user_message)
    allowed_urls = {item["url"] for item in action_options}
    fallback_url = _suggest_ai_guide_action_url(request, user_message, page_path=page_path)
    force_action = _should_force_ai_guide_action(user_message)

    reply_text = str(payload.get("reply_text") or "").strip() or _build_ai_guide_local_reply(
        user_message
    )
    raw_action = payload.get("suggested_action")
    if not isinstance(raw_action, dict):
        raw_action = {}

    action_url = _relative_internal_url(str(raw_action.get("url") or ""))
    if action_url and current_path.split("?")[0] == action_url.split("?")[0]:
        action_url = ""
    if action_url not in allowed_urls:
        action_url = ""
    if not action_url and fallback_url in allowed_urls:
        action_url = fallback_url

    should_show_action = bool(action_url) and (
        bool(raw_action.get("has_action")) or bool(fallback_url) or force_action
    )
    button_label = str(raw_action.get("button_label") or "").strip()
    if should_show_action and not button_label:
        button_label = _default_ai_guide_button_label(action_url)

    return _build_ai_guide_payload(
        reply_text,
        has_action=should_show_action,
        button_label=button_label,
        url=action_url,
    )


def _build_ai_guide_system_prompt(
    request,
    user_message: str,
    page_path: str = "",
    page_title: str = "",
) -> str:
    site_context = _build_chat_context_payload(
        request=request,
        user_message=user_message,
        page_path=page_path,
        page_title=page_title,
    )
    return prompt_library.build_chat_widget_system_prompt(
        route_rules=prompt_library.build_route_rules_table(include_api=False),
        reference_data=_build_ai_guide_story_reference(user_message) or "無",
        current_page=site_context["current_page"],
        navigation_lines=site_context["navigation_lines"],
        event_lines=site_context["event_lines"],
        post_lines=site_context["post_lines"],
    )


def _call_deepseek_ai_guide(request, user_message: str, page_path: str = "", page_title: str = "") -> str:
    api_key = ((os.environ.get("DS_API_KEY") or os.environ.get("DEEPSEEK_API_KEY") or "")).strip()
    if not api_key:
        raise RuntimeError("Missing DS_API_KEY or DEEPSEEK_API_KEY.")

    system_prompt = _build_ai_guide_system_prompt(
        request=request,
        user_message=user_message,
        page_path=page_path,
        page_title=page_title,
    )
    api_url = f"{shared_llm.DEEPSEEK_OPENAI_BASE_URL.rstrip('/')}/chat/completions"
    request_payload = {
        "model": AI_GUIDE_CHAT_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": (
                    f"目前頁面標題：{page_title or '未提供'}\n"
                    f"目前頁面路徑：{page_path or '/'}\n"
                    f"使用者訊息：{user_message}"
                ),
            },
        ],
        "temperature": 0.2,
        "max_tokens": 260,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    try:
        response = requests.post(
            api_url,
            headers=headers,
            json=request_payload,
            timeout=AI_GUIDE_CHAT_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise RuntimeError(f"DeepSeek API request failed: {exc}") from exc

    try:
        payload = response.json()
    except ValueError:
        payload = None

    if response.status_code != 200:
        detail = ""
        if isinstance(payload, dict):
            raw_error = payload.get("error")
            if isinstance(raw_error, dict):
                detail = str(raw_error.get("message") or "").strip()
            elif raw_error:
                detail = str(raw_error).strip()
            if not detail:
                detail = str(payload.get("message") or "").strip()
        if not detail:
            detail = re.sub(r"\s+", " ", (response.text or "").strip())[:220] or "Unknown API error."
        raise RuntimeError(f"DeepSeek API request failed: HTTP {response.status_code} {detail}")

    if not isinstance(payload, dict):
        raise RuntimeError("DeepSeek returned a non-JSON response body.")

    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise RuntimeError("DeepSeek returned no choices.")

    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, list):
        reply_text = "".join(
            str(item.get("text") or "")
            for item in content
            if isinstance(item, dict)
        ).strip()
    else:
        reply_text = str(content or "").strip()

    if not reply_text:
        raise RuntimeError("DeepSeek returned an empty reply.")
    return reply_text


@require_POST
def ai_guide_chat(request):
    count = request.session.get(AI_GUIDE_CHAT_SESSION_KEY, 0)
    if count >= MAX_CHAT_PER_SESSION:
        return _json_no_store(
            _build_ai_guide_payload(
                "今天已聊到 30 則，先讓系統稍微休息一下，晚點再來找我就可以。",
                has_action=False,
            )
        )

    try:
        body = json.loads(request.body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return _json_no_store({"error": "Request body must be valid JSON."}, status=400)
    if not isinstance(body, dict):
        return _json_no_store({"error": "Request body must be a JSON object."}, status=400)

    user_message = str(body.get("user_message") or "").strip()
    page_path = str(body.get("page_path") or "").strip()
    page_title = str(body.get("page_title") or "").strip()

    if not user_message or len(user_message) > 500:
        return _json_no_store({"error": "user_message is empty or too long."}, status=400)
    if page_path and not page_path.startswith("/"):
        page_path = "/"
    if len(page_title) > 200:
        page_title = page_title[:200]

    try:
        raw_reply = _call_deepseek_ai_guide(
            request,
            user_message=user_message,
            page_path=page_path,
            page_title=page_title,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("AI guide chat fallback triggered: %s", exc)
        payload = _build_ai_guide_fallback_payload(
            request,
            user_message,
            page_path=page_path,
        )
    else:
        try:
            parsed_reply = json.loads(raw_reply)
        except json.JSONDecodeError:
            payload = _build_ai_guide_fallback_payload(
                request,
                user_message,
                page_path=page_path,
                raw_reply=raw_reply,
            )
        else:
            payload = _coerce_ai_guide_payload(
                request,
                user_message,
                page_path,
                parsed_reply,
            )

    request.session[AI_GUIDE_CHAT_SESSION_KEY] = count + 1
    request.session.modified = True
    return _json_no_store(payload)


@require_POST
def chatbot_api(request):
    """水井小管家 chatbot endpoint."""
    from django.conf import settings

    count = request.session.get("chat_count", 0)
    if count >= MAX_CHAT_PER_SESSION:
        return JsonResponse(
            {
                "reply": "你今天問了好多問題呢！歡迎明天再來聊，或直接透過「聯絡我們」頁面與我們聯繫 😊"
            },
            status=200,
        )

    try:
        body = json.loads(request.body)
        user_message = body.get("message", "").strip()
        page_path = (body.get("page_path") or "").strip()
        page_title = (body.get("page_title") or "").strip()
    except (json.JSONDecodeError, AttributeError):
        return JsonResponse({"error": "Invalid request"}, status=400)

    if not user_message or len(user_message) > 500:
        return JsonResponse({"error": "Message is empty or too long"}, status=400)
    if page_path and not page_path.startswith("/"):
        page_path = "/"
    if len(page_title) > 200:
        page_title = page_title[:200]
    redirect_url = _resolve_redirect_target(request, user_message, page_path=page_path)

    api_key = settings.GEMINI_API_KEY
    if not api_key:
        reply = "AI 小管家目前尚未啟用雲端模型，先改用站內資料回覆。\n" + _build_local_fallback_reply(
            user_message,
            request=request,
            page_path=page_path,
            page_title=page_title,
        )
        request.session["chat_count"] = count + 1
        return JsonResponse({"reply": reply, "redirect_url": redirect_url}, status=200)

    ranked_events, ranked_posts = _rank_local_content(user_message)
    event_context_lines = []
    for event in ranked_events[:4]:
        event_time = timezone.localtime(event.date).strftime("%Y/%m/%d %H:%M")
        event_context_lines.append(
            f"- {event.title}｜{event_time}｜{event.location}｜{_absolute_link(request, event.get_absolute_url())}\n"
            f"  摘要：{event.short_description[:120]}"
        )
    story_context_lines = []
    for post in ranked_posts[:6]:
        story_context_lines.append(
            f"- [{post.get_category_display()}] {post.title}｜{_absolute_link(request, post.get_absolute_url())}\n"
            f"  摘要：{post.summary[:120]}"
        )

    event_context = "\n".join(event_context_lines) if event_context_lines else "- 無對應活動資料"
    story_context = "\n".join(story_context_lines) if story_context_lines else "- 無對應文章資料"
    site_context = _build_chat_context_payload(
        request=request, user_message=user_message, page_path=page_path, page_title=page_title
    )

    system_prompt = (
        "你是「AI 客棧助理」，只能回答本網站內容。\n"
        "請使用繁體中文，不得使用簡體字，不得捏造資料。\n"
        "先判斷使用者意圖，再輸出以下格式：\n"
        "1) 一句重點結論\n"
        "2) 最多三點條列（活動/故事/成果）\n"
        "3) 最後一行提供 1-3 個站內連結（完整 URL）\n"
        "若資料不足，明確說「站內目前沒有對應資料」，並提供可查詢頁面連結。\n"
        "避免空泛寒暄，回答精簡、可執行。\n\n"
        f"目前頁面：\n{site_context['current_page']}\n\n"
        f"站內導覽：\n{site_context['navigation_lines']}\n\n"
        f"活動索引：\n{site_context['event_lines']}\n\n"
        f"文章索引：\n{site_context['post_lines']}\n\n"
        f"與本次提問最相關的活動：\n{event_context}\n\n"
        f"與本次提問最相關的文章：\n{story_context}"
    )

    try:
        from google import genai

        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=getattr(settings, "GEMINI_MODEL", "gemini-2.0-flash"),
            contents=f"{system_prompt}\n\n使用者提問：{user_message}",
        )
        reply = (response.text or "").strip()
        if not reply:
            reply = _build_local_fallback_reply(
                user_message, request=request, page_path=page_path, page_title=page_title
            )
    except Exception as exc:
        logger.exception("Gemini API call failed: %s", exc)
        reply = _build_local_fallback_reply(
            user_message, request=request, page_path=page_path, page_title=page_title
        )

    request.session["chat_count"] = count + 1
    return JsonResponse({"reply": reply, "redirect_url": redirect_url})


# ─── AR + AI 語音導覽 API ────────────────────────────────────────────────── #

_AR_GUIDE_SESSION_KEY = "chat_history"
_AR_GUIDE_MAX_ROUNDS = 5
_AR_GUIDE_AZURE_STT_LANGUAGE = "zh-TW"
_AR_GUIDE_AZURE_TTS_VOICE = "zh-TW-HsiaoChenNeural"
_AR_GUIDE_AZURE_STT_MAX_SECONDS = 60
_AR_GUIDE_AZURE_STT_TIMEOUT = (10, 75)
_AR_GUIDE_AZURE_TTS_TIMEOUT = (10, 40)


def _current_ar_guide_model_name(request, requested_model: str | None = None) -> str:
    candidate = requested_model or request.session.get(AR_GUIDE_MODEL_SESSION_KEY)
    return shared_llm.resolve_model(candidate).key


def _ar_model_payload(model_name: str) -> dict[str, str]:
    model = shared_llm.resolve_model(model_name)
    return {
        "key": model.key,
        "label": model.label,
        "description": model.description,
        "provider": model.provider,
    }


def _trim_chat_history(history: list[dict]) -> list[dict]:
    return history[-(_AR_GUIDE_MAX_ROUNDS * 2):]


def _get_ar_guide_history(request) -> list[dict]:
    history = request.session.get(_AR_GUIDE_SESSION_KEY, [])
    if not isinstance(history, list):
        return []

    sanitized: list[dict] = []
    for item in history:
        if not isinstance(item, dict):
            continue

        role = item.get("role")
        content = item.get("content")
        if role in {"user", "assistant"} and isinstance(content, str) and content.strip():
            sanitized.append({"role": role, "content": content.strip()})

    return _trim_chat_history(sanitized)


def _save_ar_guide_history(request, history: list[dict]) -> None:
    request.session[_AR_GUIDE_SESSION_KEY] = _trim_chat_history(history)
    request.session.modified = True


def _normalize_ar_marker(current_marker: str | None) -> str:
    marker_key = str(current_marker or "").strip().lower()
    if marker_key in {
        prompt_library.MARKER_HISTORY,
        prompt_library.MARKER_WATERWHEEL,
        prompt_library.MARKER_AIOT_POOL,
    }:
        return marker_key
    return ""


def _parse_ar_guide_payload(request) -> tuple[bool, str, str | None, str]:
    content_type = request.content_type or ""

    if "application/json" in content_type:
        try:
            body = json.loads(request.body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError("JSON 格式錯誤。") from exc

        if body.get("clear"):
            return True, "", None, ""

        requested_model = str(body.get("model") or "").strip() or None
        current_marker = _normalize_ar_marker(body.get("current_marker"))
        return False, str(body.get("text") or "").strip(), requested_model, current_marker

    clear_flag = str(request.POST.get("clear") or "").lower()
    if clear_flag in {"1", "true", "yes"}:
        return True, "", None, ""

    requested_model = str(request.POST.get("model") or "").strip() or None
    current_marker = _normalize_ar_marker(request.POST.get("current_marker"))
    return False, str(request.POST.get("text") or "").strip(), requested_model, current_marker


def _extract_uploaded_audio(request) -> tuple[bytes, str, str]:
    upload = request.FILES.get("audio")
    if not upload:
        raise ValueError("缺少音訊檔案。")

    if upload.size and upload.size > AR_GUIDE_MAX_AUDIO_BYTES:
        raise ValueError("音訊檔案過大，請控制在 10 MB 以內。")

    audio_bytes = upload.read()
    if not audio_bytes:
        raise ValueError("音訊檔案內容為空。")
    if len(audio_bytes) > AR_GUIDE_MAX_AUDIO_BYTES:
        raise ValueError("音訊檔案過大，請控制在 10 MB 以內。")

    suffix = Path(upload.name or "speech.wav").suffix.lower() or ".wav"
    mime_type = (
        str(request.POST.get("audio_mime_type") or "").strip()
        or str(getattr(upload, "content_type", "") or "").strip()
    )
    return audio_bytes, suffix, mime_type


def _azure_speech_credentials(required: bool = True) -> tuple[str, str]:
    speech_key = (os.environ.get("AZURE_SPEECH_KEY") or "").strip()
    speech_region = (os.environ.get("AZURE_SPEECH_REGION") or "eastasia").strip()
    if required and not speech_key:
        raise RuntimeError("未設定 AZURE_SPEECH_KEY，無法使用 Azure 語音服務。")
    return speech_key, speech_region


def _azure_speech_url(speech_region: str, service: str) -> str:
    explicit_endpoint = (os.environ.get("AZURE_SPEECH_ENDPOINT") or "").strip().rstrip("/")
    if explicit_endpoint:
        if service == "stt":
            return (
                f"{explicit_endpoint}/stt/speech/recognition/conversation/"
                "cognitiveservices/v1"
            )
        return f"{explicit_endpoint}/cognitiveservices/v1"

    if service == "stt":
        return (
            f"https://{speech_region}.stt.speech.microsoft.com/"
            "speech/recognition/conversation/cognitiveservices/v1"
        )
    return f"https://{speech_region}.tts.speech.microsoft.com/cognitiveservices/v1"


def _inspect_wav_audio(audio_bytes: bytes) -> dict[str, float | int]:
    try:
        with wave.open(io.BytesIO(audio_bytes), "rb") as wav_file:
            channels = wav_file.getnchannels()
            sample_rate = wav_file.getframerate()
            sample_width = wav_file.getsampwidth()
            frame_count = wav_file.getnframes()
    except (wave.Error, EOFError) as exc:
        raise RuntimeError("Azure REST 語音辨識目前需要 WAV/PCM 音檔。") from exc

    duration_seconds = frame_count / sample_rate if sample_rate else 0
    return {
        "channels": channels,
        "sample_rate": sample_rate,
        "sample_width": sample_width,
        "frame_count": frame_count,
        "duration_seconds": duration_seconds,
    }


def _infer_audio_format(suffix: str, mime_type: str) -> str | None:
    suffix_map = {
        ".wav": "wav",
        ".wave": "wav",
        ".webm": "webm",
        ".ogg": "ogg",
        ".oga": "ogg",
        ".mp3": "mp3",
        ".m4a": "mp4",
        ".mp4": "mp4",
        ".aac": "aac",
    }
    mime_map = {
        "audio/wav": "wav",
        "audio/x-wav": "wav",
        "audio/wave": "wav",
        "audio/webm": "webm",
        "audio/ogg": "ogg",
        "audio/mp4": "mp4",
        "audio/x-m4a": "mp4",
        "audio/aac": "aac",
        "audio/mpeg": "mp3",
    }

    normalized_mime = mime_type.split(";", 1)[0].strip().lower()
    if normalized_mime in mime_map:
        return mime_map[normalized_mime]
    return suffix_map.get((suffix or "").lower())


def _normalize_audio_for_azure_stt(audio_bytes: bytes, suffix: str, mime_type: str) -> bytes:
    audio_format = _infer_audio_format(suffix, mime_type)
    source_stream = io.BytesIO(audio_bytes)

    try:
        if audio_format:
            audio = AudioSegment.from_file(source_stream, format=audio_format)
        else:
            audio = AudioSegment.from_file(source_stream)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "音訊轉檔失敗，請重新錄音後再試，或改用 Chrome / Safari 重新授權麥克風。"
        ) from exc

    normalized_audio = audio.set_frame_rate(16000).set_channels(1).set_sample_width(2)
    wav_stream = io.BytesIO()

    try:
        normalized_audio.export(wav_stream, format="wav", codec="pcm_s16le")
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("音訊轉換為 Azure 所需 WAV 格式時失敗。") from exc

    wav_bytes = wav_stream.getvalue()
    wav_info = _inspect_wav_audio(wav_bytes)
    if wav_info["duration_seconds"] > _AR_GUIDE_AZURE_STT_MAX_SECONDS:
        raise RuntimeError(
            f"Azure REST 語音辨識目前支援 {_AR_GUIDE_AZURE_STT_MAX_SECONDS} 秒內的短音檔，請縮短錄音時間。"
        )
    return wav_bytes


def _summarize_azure_error(response: requests.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        payload = None

    if isinstance(payload, dict):
        for key in ("error", "message", "RecognitionStatus", "DisplayText"):
            value = payload.get(key)
            if value:
                return str(value)

    raw_text = (response.text or "").strip()
    if raw_text:
        return re.sub(r"\s+", " ", raw_text)[:220]

    return "未知錯誤"


def _azure_stt(audio_bytes: bytes, suffix: str, mime_type: str = "") -> str:
    speech_key, speech_region = _azure_speech_credentials(required=True)
    wav_bytes = _normalize_audio_for_azure_stt(audio_bytes, suffix, mime_type)
    wav_info = _inspect_wav_audio(wav_bytes)

    if wav_info["channels"] != 1 or wav_info["sample_width"] != 2:
        raise RuntimeError("Azure REST 語音辨識需要 16-bit 單聲道 WAV 音檔。")
    if wav_info["sample_rate"] != 16000:
        raise RuntimeError("Azure REST 語音辨識需要 16 kHz 單聲道 WAV 音檔。")

    url = _azure_speech_url(speech_region, "stt")
    headers = {
        "Ocp-Apim-Subscription-Key": speech_key,
        "Content-Type": "audio/wav; codecs=audio/pcm; samplerate=16000",
        "Accept": "application/json",
    }

    try:
        response = requests.post(
            url,
            params={"language": _AR_GUIDE_AZURE_STT_LANGUAGE, "format": "simple"},
            headers=headers,
            data=wav_bytes,
            timeout=_AR_GUIDE_AZURE_STT_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise RuntimeError(f"Azure STT HTTP 請求失敗：{exc}") from exc

    if response.status_code != 200:
        raise RuntimeError(
            f"Azure STT 失敗：HTTP {response.status_code}，{_summarize_azure_error(response)}"
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError("Azure STT 回傳格式無法解析。") from exc

    recognition_status = str(payload.get("RecognitionStatus") or "").strip()
    if recognition_status == "Success":
        transcript = (payload.get("DisplayText") or "").strip()
        transcript = re.sub(r"[。．.!！?？]+$", "", transcript)
        if transcript:
            return transcript
        raise RuntimeError("Azure STT 沒有回傳有效文字。")

    if recognition_status == "NoMatch":
        raise RuntimeError("Azure STT 無法辨識語音內容，請再說一次。")

    raise RuntimeError(f"Azure STT 失敗：{recognition_status or '未知錯誤'}")


def _call_llm_for_ar(
    question: str,
    history: list[dict],
    model_name: str,
    current_marker: str = "",
) -> str:
    return shared_llm.direct_chat(
        question,
        system_prompt=prompt_library.build_ar_guide_system_prompt(current_marker),
        history=history,
        model_name=model_name,
    )


def _azure_tts_sync(
    text: str,
    voice: str,
    speech_key: str,
    speech_region: str,
) -> bytes:
    url = _azure_speech_url(speech_region, "tts")
    ssml = (
        "<speak version='1.0' xml:lang='zh-TW'>"
        f"<voice name='{voice}'>{xml_escape(text)}</voice>"
        "</speak>"
    )
    headers = {
        "Ocp-Apim-Subscription-Key": speech_key,
        "Content-Type": "application/ssml+xml",
        "X-Microsoft-OutputFormat": "riff-24khz-16bit-mono-pcm",
        "User-Agent": "mid_exam_prj/ar-guide",
    }

    try:
        response = requests.post(
            url,
            headers=headers,
            data=ssml.encode("utf-8"),
            timeout=_AR_GUIDE_AZURE_TTS_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise RuntimeError(f"Azure TTS HTTP 請求失敗：{exc}") from exc

    if response.status_code != 200:
        raise RuntimeError(
            f"Azure TTS 失敗：HTTP {response.status_code}，{_summarize_azure_error(response)}"
        )

    return response.content


def _azure_tts_data_url(text: str) -> str:
    speech_key, speech_region = _azure_speech_credentials(required=False)
    if not speech_key:
        logger.warning("AZURE_SPEECH_KEY missing. Skip TTS.")
        return ""

    try:
        audio_bytes = _azure_tts_sync(
            text=text,
            voice=_AR_GUIDE_AZURE_TTS_VOICE,
            speech_key=speech_key,
            speech_region=speech_region,
        )
        audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")
        return f"data:audio/wav;base64,{audio_b64}"
    except Exception as exc:
        logger.warning("Azure TTS failed: %s", exc)
        return ""


@require_POST
def ar_ai_guide_api(request):
    try:
        clear_requested, manual_text, requested_model, current_marker = _parse_ar_guide_payload(request)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    if clear_requested:
        _save_ar_guide_history(request, [])
        return JsonResponse({"ok": True})

    try:
        model_name = _current_ar_guide_model_name(request, requested_model)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    transcript = ""
    if request.FILES.get("audio"):
        try:
            audio_bytes, suffix, mime_type = _extract_uploaded_audio(request)
            transcript = _azure_stt(audio_bytes, suffix, mime_type)
        except ValueError as exc:
            return JsonResponse({"error": str(exc)}, status=400)
        except RuntimeError as exc:
            logger.warning("AR guide STT failed: %s", exc)
            return JsonResponse({"error": str(exc)}, status=500)
    else:
        transcript = manual_text

    if not transcript:
        return JsonResponse({"error": "缺少可辨識的語音或文字內容。"}, status=400)

    request.session[AR_GUIDE_MODEL_SESSION_KEY] = model_name
    history = _get_ar_guide_history(request)

    try:
        answer = _call_llm_for_ar(
            transcript,
            history,
            model_name,
            current_marker=current_marker,
        )
    except Exception as exc:
        logger.exception("AR guide LLM call failed: %s", exc)
        return JsonResponse({"error": f"AI 模型呼叫失敗：{exc}"}, status=500)

    history.append({"role": "user", "content": transcript})
    history.append({"role": "assistant", "content": answer})
    _save_ar_guide_history(request, history)

    audio_url = _azure_tts_data_url(answer)
    return JsonResponse(
        {
            "transcript": transcript,
            "text": answer,
            "audio_url": audio_url,
            "model": _ar_model_payload(model_name),
        }
    )

