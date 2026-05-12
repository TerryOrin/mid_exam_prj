from django.shortcuts import render, get_object_or_404
from django.core.paginator import Paginator
from django.db.models import Q, Case, When, Value, IntegerField
from django.http import JsonResponse
from django.urls import reverse
from django.views.decorators.http import require_POST
from django.utils import timezone
import json
import logging
import re

from .models import HeroSlide, Event, StoryPost
from .forms import ContactForm

logger = logging.getLogger(__name__)
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
