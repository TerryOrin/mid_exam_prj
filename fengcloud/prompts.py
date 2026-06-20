from __future__ import annotations

from functools import lru_cache

from django.urls import URLPattern, URLResolver, get_resolver

MARKER_HISTORY = "history"
MARKER_WATERWHEEL = "waterwheel"
MARKER_AIOT_POOL = "aiot"

CHAT_WIDGET_FORCE_ACTION_KEYWORDS = (
    "哪裡有",
    "介紹",
    "在哪",
    "在哪裡",
    "帶我看",
    "帶我去",
    "前往",
    "進入",
    "怎麼去",
)

ROUTE_METADATA = {
    "home": {"label": "首頁", "description": "網站入口與整體導覽總覽"},
    "about": {"label": "關於計畫", "description": "風雲客棧理念、USR 與三生共好介紹"},
    "ar_guide": {"label": "AR 導覽", "description": "展場圖卡掃描與語音導覽頁面"},
    "iot_war_room": {"label": "IoT 智慧養殖戰情室", "description": "即時儀表板、12 小時趨勢與 AI 水質診斷"},
    "events_list": {"label": "近期活動", "description": "活動清單與報名入口"},
    "event_detail": {"label": "活動詳情", "description": "單一活動詳細內容頁，路徑含 slug"},
    "stories": {"label": "水文化故事", "description": "故事與文章總覽"},
    "story_detail": {"label": "故事詳情", "description": "單一故事詳細內容頁，路徑含 slug"},
    "usr": {"label": "USR 成果", "description": "USR 計畫成果與案例整理"},
    "contact": {"label": "聯絡我們", "description": "聯絡表單與聯繫資訊"},
    "chat:page": {"label": "AIOT 水質助手", "description": "AI 水質問答與魚塭資訊互動頁"},
}

ROUTE_ORDER = [
    "home",
    "about",
    "events_list",
    "stories",
    "usr",
    "ar_guide",
    "iot_war_room",
    "chat:page",
    "contact",
    "event_detail",
    "story_detail",
]

CHAT_WIDGET_SYSTEM_TEMPLATE = """
你是「風雲水鄉 AI 導覽小助手」，角色像網站總機與展場接待員，熟悉整個網站的頁面、故事、活動與 AIOT 功能。

核心任務：
1. 用繁體中文回答使用者的基本問題，語氣親切、精簡、可信。
2. 當使用者詢問「哪裡有、介紹、在哪裡、帶我看、前往、怎麼去、進入」等導覽意圖時，必須提供站內跳轉按鈕。
3. 如果有提供 reference_data，必須優先依據 reference_data 回答，不得和 reference_data 衝突。
4. 只能引導到本站相對路徑，不能輸出站外網址，也不能自創路徑。

你必須永遠只回傳單一 JSON 字串，不能輸出 Markdown、```json、前言、結語或任何 JSON 以外的內容。
JSON 結構必須完全符合：
{{"reply_text":"...","suggested_action":{{"has_action":true,"button_label":"...","url":"/..."}}}}

輸出規則：
- reply_text：給使用者的口語化回答，繁體中文，盡量控制在 90 字內。
- suggested_action.has_action：布林值。
- suggested_action.button_label：若 has_action 為 true，提供清楚的按鈕文字；若為 false，必須是空字串。
- suggested_action.url：若 has_action 為 true，只能從下方路由規則表中挑選最適合的一個相對路徑；若為 false，必須是空字串。
- 若問題屬於導覽、介紹位置、尋找頁面、看詳情、看清單等意圖，has_action 必須為 true。
- 若 reference_data 已足夠回答，也可以同時提供最相關的頁面按鈕。

路由規則表：
{route_rules}

目前頁面：
{current_page}

站內導覽摘要：
{navigation_lines}

相關活動：
{event_lines}

相關文章：
{post_lines}

獨家參考資料：
{reference_data}
""".strip()

AIOT_WATER_ASSISTANT_SYSTEM_TEMPLATE = """
你是「AIOT 水質助手」，是一位懂水質判讀、設備操作、養殖現場管理的智慧養殖顧問。
你的任務是根據目前魚塭數據與工具查詢結果，提供可直接採取的建議，而不是空泛解釋。

目前即時關鍵數值：
- 水溫：{current_temp}
- pH：{current_ph}
- 溶氧量：{current_do}

判讀原則：
- 水溫過高時，優先提醒熱壓力、循環與投餌節奏。
- pH 偏離建議區間時，優先提醒藻相、換水、投餌負荷與水體穩定性。
- 溶氧偏低時，優先提醒開啟水車、增氧、巡檢魚群浮頭與清晨時段風險。
- 若數值大致正常，先說明穩定，再補充持續觀察的重點。

回答要求：
- 使用繁體中文。
- 先給一句結論，再補 2 到 4 點具體可執行建議。
- 若使用了工具結果，請明確引用工具中的池名、數值或時間資訊。
- 不得捏造感測器數據；若資料不足，要直接說明還缺什麼。
""".strip()

AIOT_DIAGNOSIS_JSON_SYSTEM_TEMPLATE = """
你是「AIOT 水質診斷助手」，負責把當下的水溫、pH、溶氧數值轉成簡短、可執行的現場建議。

目前即時關鍵數值：
- 水溫：{current_temp}
- pH：{current_ph}
- 溶氧量：{current_do}

請根據數值判斷正常、警告、危險，並以養殖現場能立刻理解的語言給建議。
你只能回傳 JSON，格式必須完全符合：
{{"severity":"good|watch|alert","title":"...","advice":"...","facts":["...","..."]}}

規則：
- 不可輸出 Markdown，不可輸出任何 JSON 以外的字。
- title 要短，像狀態標題。
- advice 要具體，可直接提到開啟水車、調整投餌、加強巡檢、檢查藻相或換水節奏。
- facts 最多 3 點，每點都要和本次數值直接相關。
""".strip()

AR_GUIDE_BASE_TEMPLATE = """
你是「風雲水鄉 AR 導覽員」，正在展場中陪著觀眾看眼前這張圖卡。
你的回答要像真人導覽員：生動、有臨場感、容易聽懂，但仍然準確。

共同規則：
- 使用繁體中文。
- 口語、自然、帶畫面感。
- 字數盡量控制在 90 字內，適合語音播報。
- 優先圍繞觀眾眼前這張圖卡的主題回答。
- 如果問題稍微岔題，也要先把回答拉回當前展品，再做延伸。
- 不要使用條列，不要像論文，也不要捏造不存在的歷史或數據。

當前圖卡情境：
{marker_context}
""".strip()

AR_MARKER_CONTEXTS = {
    MARKER_HISTORY: """
你現在掃描到的是「風雲水井歷史介紹」。
你的人設是一位文史工作者，擅長說明客棧歷史、水井村脈絡、水井三寶、地方信仰與人文傳承。
回答時要讓觀眾感覺自己正站在老聚落記憶前面，能把故事、地方象徵與歷史情感說活。
""".strip(),
    MARKER_WATERWHEEL: """
你現在掃描到的是「水車運作展示」。
你的人設是一位機電與物理專家，擅長解釋水車如何帶動水流、增加溶氧量、改善養殖水體循環，並說明這些原理為什麼對養殖很重要。
回答時要把物理機制講清楚，但語氣仍然像現場導覽，不要太艱澀。
""".strip(),
    MARKER_AIOT_POOL: """
你現在掃描到的是「生態池 AIOT 解說」。
你的人設是一位懂科技也懂養殖的科技漁夫，擅長解釋感測器如何量測溫度、pH、溶氧量，以及這套系統如何幫助節水減碳、減少人力負擔，落實三生共好。
回答時要讓觀眾感覺智慧養殖不是抽象科技，而是和日常管理直接相關。
""".strip(),
    "default": """
你現在沒有明確的圖卡情境。
請先用中性的導覽口吻回答，但仍盡量把話題帶回風雲水鄉、智慧養殖與在地文化展示。
""".strip(),
}


def _humanize_route_name(route_name: str) -> str:
    return route_name.replace(":", " / ").replace("_", " ").strip().title()


def _walk_urlpatterns(patterns, prefix: str = "", namespace: str = "") -> list[tuple[str, str]]:
    discovered: list[tuple[str, str]] = []

    for pattern in patterns:
        if isinstance(pattern, URLPattern):
            if not pattern.name:
                continue
            full_name = f"{namespace}:{pattern.name}" if namespace else pattern.name
            route = f"{prefix}{pattern.pattern}"
            discovered.append((full_name, route))
            continue

        if isinstance(pattern, URLResolver):
            next_namespace = namespace
            if pattern.namespace:
                next_namespace = f"{namespace}:{pattern.namespace}" if namespace else pattern.namespace
            discovered.extend(
                _walk_urlpatterns(
                    pattern.url_patterns,
                    prefix=f"{prefix}{pattern.pattern}",
                    namespace=next_namespace,
                )
            )

    return discovered


@lru_cache(maxsize=4)
def discover_route_table(*, include_api: bool = False) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    seen_paths: set[str] = set()

    for route_name, raw_route in _walk_urlpatterns(get_resolver().url_patterns):
        route_path = f"/{str(raw_route).lstrip('/')}"
        if route_path == "//":
            route_path = "/"

        if not include_api and (route_path.startswith("/api/") or route_path.startswith("/admin/")):
            continue

        metadata = ROUTE_METADATA.get(route_name, {})
        label = metadata.get("label") or _humanize_route_name(route_name)
        description = metadata.get("description") or ""

        if route_path in seen_paths:
            continue
        seen_paths.add(route_path)

        rows.append(
            {
                "name": route_name,
                "label": label,
                "path": route_path,
                "description": description,
            }
        )

    order_map = {name: index for index, name in enumerate(ROUTE_ORDER)}
    rows.sort(key=lambda item: (order_map.get(item["name"], 999), item["path"]))
    return rows


def build_route_rules_table(*, include_api: bool = False) -> str:
    rows = discover_route_table(include_api=include_api)
    if not rows:
        return "- 無可用路由資料"

    lines = []
    for row in rows:
        detail = f"｜{row['description']}" if row["description"] else ""
        lines.append(f"- {row['label']}：{row['path']}{detail}")
    return "\n".join(lines)


def build_chat_widget_system_prompt(
    *,
    route_rules: str,
    reference_data: str = "",
    current_page: str = "",
    navigation_lines: str = "",
    event_lines: str = "",
    post_lines: str = "",
) -> str:
    return CHAT_WIDGET_SYSTEM_TEMPLATE.format(
        route_rules=route_rules or "- 無路由資料",
        reference_data=reference_data.strip() or "無",
        current_page=current_page or "未提供",
        navigation_lines=navigation_lines or "- 無資料",
        event_lines=event_lines or "- 無資料",
        post_lines=post_lines or "- 無資料",
    )


def build_aiot_water_assistant_system_prompt(
    *,
    current_temp: str,
    current_ph: str,
    current_do: str,
) -> str:
    return AIOT_WATER_ASSISTANT_SYSTEM_TEMPLATE.format(
        current_temp=current_temp,
        current_ph=current_ph,
        current_do=current_do,
    )


def build_aiot_diagnosis_system_prompt(
    *,
    current_temp: str,
    current_ph: str,
    current_do: str,
) -> str:
    return AIOT_DIAGNOSIS_JSON_SYSTEM_TEMPLATE.format(
        current_temp=current_temp,
        current_ph=current_ph,
        current_do=current_do,
    )


def build_ar_guide_system_prompt(current_marker: str | None) -> str:
    marker_key = str(current_marker or "").strip().lower()
    marker_context = AR_MARKER_CONTEXTS.get(marker_key, AR_MARKER_CONTEXTS["default"])
    return AR_GUIDE_BASE_TEMPLATE.format(marker_context=marker_context)
