import base64
import json
import logging
import os
import re
import tempfile
from pathlib import Path

from PIL import Image as PILImage
from django.conf import settings
from django.http import JsonResponse
from django.core.paginator import Paginator
from django.db.models import Q, Case, When, Value, IntegerField
from django.shortcuts import render, get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST
import msgpack

from chat import llm as shared_llm

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
_AR_GUIDE_SYSTEM_PROMPT = (
    "你是雲林水井村的智慧養殖與漁村導覽專家。"
    "請用繁體中文、親切、口語化且精簡的語氣回答。"
    "字數盡量控制在 50 字以內適合語音播報。"
)


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


def _parse_ar_guide_payload(request) -> tuple[bool, str, str | None]:
    content_type = request.content_type or ""

    if "application/json" in content_type:
        try:
            body = json.loads(request.body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError("JSON 格式錯誤。") from exc

        if body.get("clear"):
            return True, "", None

        requested_model = str(body.get("model") or "").strip() or None
        return False, str(body.get("text") or "").strip(), requested_model

    clear_flag = str(request.POST.get("clear") or "").lower()
    if clear_flag in {"1", "true", "yes"}:
        return True, "", None

    requested_model = str(request.POST.get("model") or "").strip() or None
    return False, str(request.POST.get("text") or "").strip(), requested_model


def _extract_uploaded_audio(request) -> tuple[bytes, str]:
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
    return audio_bytes, suffix


def _azure_stt_sync(audio_path: str, speech_key: str, speech_region: str, language: str = "zh-TW") -> str:
    import azure.cognitiveservices.speech as speechsdk  # type: ignore

    speech_config = speechsdk.SpeechConfig(subscription=speech_key, region=speech_region)
    speech_config.speech_recognition_language = language
    audio_config = speechsdk.audio.AudioConfig(filename=audio_path)
    recognizer = speechsdk.SpeechRecognizer(
        speech_config=speech_config,
        audio_config=audio_config,
    )
    result = recognizer.recognize_once()

    if result.reason == speechsdk.ResultReason.RecognizedSpeech:
        transcript = (result.text or "").strip()
        transcript = re.sub(r"[。．.!！?？]+$", "", transcript)
        if transcript:
            return transcript
        raise RuntimeError("Azure STT 沒有回傳有效文字。")

    if result.reason == speechsdk.ResultReason.NoMatch:
        raise RuntimeError("Azure STT 無法辨識語音內容，請再說一次。")

    cancellation = speechsdk.CancellationDetails(result)
    error_details = cancellation.error_details or "未知錯誤"
    raise RuntimeError(f"Azure STT 失敗：{error_details}")


def _azure_stt(audio_bytes: bytes, suffix: str) -> str:
    try:
        import azure.cognitiveservices.speech as speechsdk  # noqa: F401
    except ImportError as exc:
        raise RuntimeError("缺少 Azure Speech SDK，請先安裝 azure-cognitiveservices-speech。") from exc

    speech_key = (os.environ.get("AZURE_SPEECH_KEY") or "").strip()
    speech_region = (os.environ.get("AZURE_SPEECH_REGION") or "eastasia").strip()
    if not speech_key:
        raise RuntimeError("未設定 AZURE_SPEECH_KEY，無法進行語音辨識。")

    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
            temp_file.write(audio_bytes)
            temp_path = temp_file.name

        return _azure_stt_sync(temp_path, speech_key, speech_region, language="zh-TW")
    finally:
        if temp_path:
            try:
                Path(temp_path).unlink(missing_ok=True)
            except OSError:
                logger.warning("Failed to delete temp audio file: %s", temp_path)


def _call_llm_for_ar(question: str, history: list[dict], model_name: str) -> str:
    return shared_llm.direct_chat(
        question,
        system_prompt=_AR_GUIDE_SYSTEM_PROMPT,
        history=history,
        model_name=model_name,
    )


def _azure_tts_sync(
    text: str,
    voice: str,
    speech_key: str,
    speech_region: str,
) -> bytes:
    import azure.cognitiveservices.speech as speechsdk  # type: ignore

    speech_config = speechsdk.SpeechConfig(subscription=speech_key, region=speech_region)
    speech_config.speech_synthesis_voice_name = voice
    speech_config.set_speech_synthesis_output_format(
        speechsdk.SpeechSynthesisOutputFormat.Riff16Khz16BitMonoPcm
    )
    synthesizer = speechsdk.SpeechSynthesizer(speech_config=speech_config, audio_config=None)
    result = synthesizer.speak_text_async(text).get()
    if result.reason != speechsdk.ResultReason.SynthesizingAudioCompleted:
        raise RuntimeError(f"Azure TTS 失敗：{result.reason}")
    return bytes(result.audio_data)


def _azure_tts_data_url(text: str) -> str:
    try:
        import azure.cognitiveservices.speech as speechsdk  # noqa: F401
    except ImportError:
        logger.warning("Azure Speech SDK not installed. Skip TTS.")
        return ""

    speech_key = (os.environ.get("AZURE_SPEECH_KEY") or "").strip()
    speech_region = (os.environ.get("AZURE_SPEECH_REGION") or "eastasia").strip()
    if not speech_key:
        logger.warning("AZURE_SPEECH_KEY missing. Skip TTS.")
        return ""

    try:
        audio_bytes = _azure_tts_sync(
            text=text,
            voice="zh-TW-HsiaoChenNeural",
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
        clear_requested, manual_text, requested_model = _parse_ar_guide_payload(request)
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
            audio_bytes, suffix = _extract_uploaded_audio(request)
            transcript = _azure_stt(audio_bytes, suffix)
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
        answer = _call_llm_for_ar(transcript, history, model_name)
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

