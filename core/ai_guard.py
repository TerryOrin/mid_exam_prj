from __future__ import annotations

import hashlib
import logging
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from django.conf import settings
from django.core.cache import caches
from django.http import JsonResponse
from django.utils import timezone

logger = logging.getLogger("ai_abuse")

_MEANINGFUL_CHAR_RE = re.compile(r"[A-Za-z0-9\u4e00-\u9fff]")
_SYMBOL_LINE_RE = re.compile(r"^[\W_]+$")
_REPEATED_FRAGMENT_RE = re.compile(r"(.{1,8}?)\1{3,}")
_MEANINGLESS_SYMBOL_SPAM_RE = re.compile(r"([!@#$%^&*~\\/=_+\-|?><.,])\1{7,}")
_CODE_FENCE_RE = re.compile(r"```")
_HTML_PAYLOAD_RE = re.compile(r"<!DOCTYPE|<script\b|</script>|<html\b|</html>", re.IGNORECASE)
_TECH_KEYWORD_RE = re.compile(
    r"\b(import|from|def|class|function|const|let|var|SELECT|INSERT|UPDATE|DELETE|CREATE\s+TABLE|curl|npm\s+install|pip\s+install|python\s+-m|console\.log)\b",
    re.IGNORECASE,
)
_MULTILINE_CODE_RE = re.compile(r"^\s{0,8}(def |class |function |\{|\}|<\w+|import |from )", re.MULTILINE)
_REPEATED_LINE_SPLIT_RE = re.compile(r"[\r\n]+")
_WHITESPACE_RE = re.compile(r"\s+")

CHAT_ENDPOINT_TYPE = "chat"
AR_VOICE_ENDPOINT_TYPE = "ar_voice"


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    remaining: int
    retry_after: int
    limit: int
    window_seconds: int


def _rate_limit_config() -> dict[str, dict[str, int]]:
    return getattr(
        settings,
        "AI_GUARD_RATE_LIMITS",
        {
            "chat_minute": {"limit": 6, "window_seconds": 60},
            "chat_hour": {"limit": 25, "window_seconds": 3600},
            "chat_day": {"limit": 60, "window_seconds": 86400},
            "ar_voice_minute": {"limit": 3, "window_seconds": 60},
            "ar_voice_hour": {"limit": 12, "window_seconds": 3600},
            "ar_voice_day": {"limit": 25, "window_seconds": 86400},
            "ai_global_day": {"limit": 200, "window_seconds": 86400},
        },
    )


def get_request_content_length(request) -> int:
    try:
        raw_value = request.META.get("CONTENT_LENGTH") or 0
        return max(int(raw_value), 0)
    except (TypeError, ValueError):
        return 0


def ensure_session_key(request) -> str:
    if not request.session.session_key:
        request.session.save()
    return str(request.session.session_key or "")


def _request_ip(request) -> str:
    forwarded_for = str(request.META.get("HTTP_X_FORWARDED_FOR") or "").strip()
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return str(request.META.get("REMOTE_ADDR") or "").strip()


def get_anonymous_client_hash(request) -> str:
    session_key = ensure_session_key(request)
    raw_identity = f"{session_key}:{_request_ip(request)}"
    secret = str(settings.SECRET_KEY or "")
    return hashlib.sha256(f"{secret}:{raw_identity}".encode("utf-8")).hexdigest()


def build_client_cache_key(request, scope: str, window_seconds: int) -> str:
    client_hash = get_anonymous_client_hash(request)
    return f"ai:{scope}:{client_hash}:{window_seconds}"


def normalise_ai_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(text or ""))
    normalized = normalized.strip()
    normalized = _WHITESPACE_RE.sub(" ", normalized)
    return normalized


def _meaningful_char_count(text: str) -> int:
    return len(_MEANINGFUL_CHAR_RE.findall(text))


def _lines(text: str) -> list[str]:
    return [line.strip() for line in _REPEATED_LINE_SPLIT_RE.split(text) if line.strip()]


def _is_effectively_empty(text: str) -> bool:
    if not text:
        return True
    meaningful_count = _meaningful_char_count(text)
    if meaningful_count < 2:
        return True
    if len(text) == 1:
        return True
    if _SYMBOL_LINE_RE.fullmatch(text):
        return True
    return False


def _looks_like_code_payload(text: str) -> bool:
    if not text:
        return False

    length = len(text)
    score = 0
    if _CODE_FENCE_RE.search(text):
        score += 4
    if _HTML_PAYLOAD_RE.search(text):
        score += 4

    tech_matches = _TECH_KEYWORD_RE.findall(text)
    if tech_matches:
        score += min(len(tech_matches), 4)

    brace_like_chars = sum(text.count(char) for char in "{}[]();\\")
    if brace_like_chars >= 18:
        score += 2
    elif brace_like_chars >= 10:
        score += 1

    lines = _lines(text)
    if len(lines) >= 6 and _MULTILINE_CODE_RE.search(text):
        score += 2

    non_text_ratio = 1.0 - (_meaningful_char_count(text) / max(len(text), 1))
    if non_text_ratio >= 0.42:
        score += 2
    elif non_text_ratio >= 0.32:
        score += 1

    if length > 200 and score >= 4:
        return True
    if length > 350 and score >= 3:
        return True
    if score >= 5 and length > 80:
        return True
    return False


def _looks_like_repetition_spam(text: str) -> bool:
    if not text:
        return False

    if re.search(r"(.)\1{15,}", text):
        return True
    if _REPEATED_FRAGMENT_RE.search(text):
        return True
    if _MEANINGLESS_SYMBOL_SPAM_RE.search(text):
        return True

    lines = _lines(text)
    if lines:
        counts: dict[str, int] = {}
        for line in lines:
            counts[line] = counts.get(line, 0) + 1
        if max(counts.values()) >= 4:
            return True

    compact = re.sub(r"\s+", "", text)
    if len(compact) >= 24:
        n = 3 if len(compact) < 48 else 4
        total = max(len(compact) - n + 1, 0)
        if total > 0:
            ngram_counts: dict[str, int] = {}
            for index in range(total):
                token = compact[index : index + n]
                ngram_counts[token] = ngram_counts.get(token, 0) + 1
            highest_ratio = max(ngram_counts.values()) / total
            if highest_ratio >= 0.38:
                return True

    return False


def validate_ai_text(text: str, endpoint_type: str) -> dict[str, Any]:
    normalized_text = normalise_ai_text(text)
    if endpoint_type == AR_VOICE_ENDPOINT_TYPE:
        max_chars = int(getattr(settings, "AI_GUARD_AR_TEXT_MAX_CHARS", 350))
    else:
        max_chars = int(getattr(settings, "AI_GUARD_CHAT_MAX_CHARS", 600))

    if _is_effectively_empty(normalized_text):
        return {
            "ok": False,
            "reason_code": "empty_or_too_short",
            "message": "請輸入與水井村、USR、活動、AIoT 或 AR 導覽相關的簡短問題。",
            "normalized_text": normalized_text,
        }

    if len(normalized_text) > max_chars:
        return {
            "ok": False,
            "reason_code": "text_too_long",
            "message": f"輸入內容過長，請控制在 {max_chars} 字元以內。",
            "normalized_text": normalized_text,
        }

    if _looks_like_code_payload(normalized_text):
        return {
            "ok": False,
            "reason_code": "code_payload_detected",
            "message": "目前不接受大量程式碼或技術 payload，請改成簡短描述你的問題。",
            "normalized_text": normalized_text,
        }

    if _looks_like_repetition_spam(normalized_text):
        return {
            "ok": False,
            "reason_code": "repetition_or_spam",
            "message": "請輸入與水井村、USR、活動、AIoT 或 AR 導覽相關的簡短問題。",
            "normalized_text": normalized_text,
        }

    return {
        "ok": True,
        "reason_code": "ok",
        "message": "",
        "normalized_text": normalized_text,
    }


def _get_rate_limit_cache():
    alias = getattr(settings, "AI_RATE_LIMIT_CACHE_ALIAS", "default")
    try:
        return caches[alias]
    except Exception:  # noqa: BLE001
        return caches["default"]


def consume_rate_limit(request, scope: str, limit: int, window_seconds: int) -> RateLimitResult:
    cache = _get_rate_limit_cache()
    key = build_client_cache_key(request, scope, window_seconds)

    try:
        record = cache.get(key)
        now_ts = int(timezone.now().timestamp())
        if not isinstance(record, dict) or int(record.get("reset_at", 0)) <= now_ts:
            record = {"count": 0, "reset_at": now_ts + window_seconds}

        count = int(record.get("count", 0))
        reset_at = int(record.get("reset_at", now_ts + window_seconds))
        if count >= limit:
            retry_after = max(reset_at - now_ts, 1)
            return RateLimitResult(
                allowed=False,
                remaining=0,
                retry_after=retry_after,
                limit=limit,
                window_seconds=window_seconds,
            )

        count += 1
        cache.set(key, {"count": count, "reset_at": reset_at}, timeout=max(reset_at - now_ts, 1))
        remaining = max(limit - count, 0)
        return RateLimitResult(
            allowed=True,
            remaining=remaining,
            retry_after=max(reset_at - now_ts, 1),
            limit=limit,
            window_seconds=window_seconds,
        )
    except Exception:  # noqa: BLE001
        fallback_cache = caches["default"]
        if fallback_cache is cache:
            return RateLimitResult(
                allowed=True,
                remaining=max(limit - 1, 0),
                retry_after=window_seconds,
                limit=limit,
                window_seconds=window_seconds,
            )
        try:
            record = fallback_cache.get(key) or {"count": 0, "reset_at": int(timezone.now().timestamp()) + window_seconds}
            now_ts = int(timezone.now().timestamp())
            count = int(record.get("count", 0))
            reset_at = int(record.get("reset_at", now_ts + window_seconds))
            if count >= limit:
                return RateLimitResult(False, 0, max(reset_at - now_ts, 1), limit, window_seconds)
            count += 1
            fallback_cache.set(key, {"count": count, "reset_at": reset_at}, timeout=max(reset_at - now_ts, 1))
            return RateLimitResult(True, max(limit - count, 0), max(reset_at - now_ts, 1), limit, window_seconds)
        except Exception:  # noqa: BLE001
            return RateLimitResult(True, max(limit - 1, 0), window_seconds, limit, window_seconds)


def rate_limit_from_settings(request, scope: str) -> RateLimitResult:
    config = _rate_limit_config()[scope]
    return consume_rate_limit(
        request,
        scope=scope,
        limit=int(config["limit"]),
        window_seconds=int(config["window_seconds"]),
    )


def log_ai_abuse(
    request,
    *,
    endpoint_scope: str,
    reason_code: str,
    text: str = "",
    content_length: int = 0,
) -> None:
    logger.warning(
        "AI guard rejected request",
        extra={
            "timestamp": timezone.now().isoformat(),
            "endpoint_scope": endpoint_scope,
            "reason_code": reason_code,
            "client_hash": get_anonymous_client_hash(request),
            "input_char_count": len(text or ""),
            "request_content_length": int(content_length or 0),
        },
    )


def json_error_response(message: str, *, status: int, retry_after: int | None = None) -> JsonResponse:
    response = JsonResponse({"error": message}, status=status)
    if retry_after is not None:
        response["Retry-After"] = str(max(int(retry_after), 1))
    return response


def apply_rate_limits(request, scopes: list[str], *, endpoint_scope: str, content_length: int, text: str = ""):
    for scope in scopes:
        result = rate_limit_from_settings(request, scope)
        if result.allowed:
            continue

        if scope == "ai_global_day":
            message = "今日 AI 導覽服務已達暫定使用上限，請明日再試。"
        else:
            message = f"操作過於頻繁，請在 {result.retry_after} 秒後再試。"
        log_ai_abuse(
            request,
            endpoint_scope=endpoint_scope,
            reason_code=f"rate_limit:{scope}",
            text=text,
            content_length=content_length,
        )
        return json_error_response(message, status=429, retry_after=result.retry_after)
    return None


def trim_history_messages(
    history: list[dict[str, str]],
    *,
    max_messages: int = 20,
    max_chars: int = 6000,
) -> list[dict[str, str]]:
    trimmed = history[-max_messages:]
    total_chars = sum(len(item.get("content") or "") for item in trimmed)
    while trimmed and total_chars > max_chars:
        removed = trimmed.pop(0)
        total_chars -= len(removed.get("content") or "")
    return trimmed
