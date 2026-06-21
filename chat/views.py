from __future__ import annotations

import json

from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_http_methods

from core import ai_guard
from fengcloud import prompts as prompt_library

from . import llm
from .dashboard import build_dashboard_payload

SESSION_MODEL_KEY = "aiot_selected_model"
SESSION_HISTORY_KEY = "aiot_chat_history"
MAX_HISTORY_MESSAGES = int(getattr(settings, "AI_GUARD_HISTORY_MAX_MESSAGES", 20))
MAX_HISTORY_CHARS = int(getattr(settings, "AI_GUARD_HISTORY_MAX_CHARS", 6000))


def _current_model_name(request, requested_model: str | None = None) -> str:
    candidate = requested_model or request.session.get(SESSION_MODEL_KEY)
    return llm.resolve_model(candidate).key


def _model_payload(model_name: str) -> dict[str, str]:
    model = llm.resolve_model(model_name)
    return {
        "key": model.key,
        "label": model.label,
        "description": model.description,
        "provider": model.provider,
    }


def _get_session_history(request) -> list[dict[str, str]]:
    history = request.session.get(SESSION_HISTORY_KEY, [])
    if not isinstance(history, list):
        return []

    sanitized_history: list[dict[str, str]] = []
    for item in history:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = item.get("content")
        if role not in {"user", "assistant"} or not isinstance(content, str):
            continue
        sanitized_history.append({"role": role, "content": content})
    return ai_guard.trim_history_messages(
        sanitized_history,
        max_messages=MAX_HISTORY_MESSAGES,
        max_chars=MAX_HISTORY_CHARS,
    )


def _save_session_history(request, history: list[dict[str, str]]) -> None:
    request.session[SESSION_HISTORY_KEY] = ai_guard.trim_history_messages(
        history,
        max_messages=MAX_HISTORY_MESSAGES,
        max_chars=MAX_HISTORY_CHARS,
    )
    request.session.modified = True


def _append_session_message(request, role: str, content: str) -> None:
    if role not in {"user", "assistant"} or not content:
        return

    history = _get_session_history(request)
    history.append({"role": role, "content": content})
    _save_session_history(request, history)


def _dashboard_payload() -> dict:
    return build_dashboard_payload(include_war_room=False)


def _aiot_assistant_system_prompt() -> str:
    metrics = _dashboard_payload().get("metrics", {})

    def _format_value(value, unit: str = "") -> str:
        if value is None:
            return "目前無資料"
        suffix = f" {unit}" if unit else ""
        return f"{float(value):.2f}{suffix}"

    return prompt_library.build_aiot_water_assistant_system_prompt(
        current_temp=_format_value(metrics.get("temperature_c"), "°C"),
        current_ph=_format_value(metrics.get("ph")),
        current_do=_format_value(metrics.get("dissolved_oxygen_mg_l"), "mg/L"),
    )


def _json_no_store(payload: dict[str, object], *, status: int = 200) -> JsonResponse:
    response = JsonResponse(payload, status=status)
    response["Cache-Control"] = "no-store, max-age=0"
    return response


def _guard_error_response(message: str, *, status: int, retry_after: int | None = None) -> JsonResponse:
    response = _json_no_store({"error": message}, status=status)
    if retry_after is not None:
        response["Retry-After"] = str(max(int(retry_after), 1))
    return response


def _validate_json_request_meta(request, *, body_limit: int) -> JsonResponse | None:
    content_type = str(request.content_type or "").lower()
    if "application/json" not in content_type:
        return _guard_error_response("Content-Type 必須為 application/json。", status=400)

    content_length = ai_guard.get_request_content_length(request)
    if content_length > body_limit:
        return _guard_error_response("Request body 過大。", status=413)
    return None


def chat_page(request):
    current_model = _current_model_name(request)
    context = {
        "model_options": llm.get_available_models(),
        "current_model": current_model,
        "current_model_meta": _model_payload(current_model),
        "dashboard": _dashboard_payload(),
        "chat_history": _get_session_history(request),
    }
    return render(request, "chat/chat.html", context)


@require_GET
def dashboard_api(request):
    return JsonResponse(_dashboard_payload())


@require_http_methods(["POST"])
def chat_api(request):
    content_length = ai_guard.get_request_content_length(request)
    meta_error = _validate_json_request_meta(
        request,
        body_limit=settings.AI_GUARD_MAX_JSON_BODY_BYTES,
    )
    if meta_error is not None:
        ai_guard.log_ai_abuse(
            request,
            endpoint_scope="aiot_chat",
            reason_code="invalid_request_meta",
            content_length=content_length,
        )
        return meta_error

    try:
        body = json.loads(request.body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        ai_guard.log_ai_abuse(
            request,
            endpoint_scope="aiot_chat",
            reason_code="invalid_json",
            content_length=content_length,
        )
        return _guard_error_response("Request body must be valid JSON.", status=400)

    if not isinstance(body, dict):
        ai_guard.log_ai_abuse(
            request,
            endpoint_scope="aiot_chat",
            reason_code="invalid_json_object",
            content_length=content_length,
        )
        return _guard_error_response("Request body must be a JSON object.", status=400)

    raw_message = str(body.get("message") or "")
    validation = ai_guard.validate_ai_text(raw_message, ai_guard.CHAT_ENDPOINT_TYPE)
    if not validation["ok"]:
        ai_guard.log_ai_abuse(
            request,
            endpoint_scope="aiot_chat",
            reason_code=validation["reason_code"],
            text=validation["normalized_text"],
            content_length=content_length,
        )
        return _guard_error_response(str(validation["message"]), status=400)
    message = validation["normalized_text"]

    rate_limit_error = ai_guard.apply_rate_limits(
        request,
        ["chat_minute", "chat_hour", "chat_day", "ai_global_day"],
        endpoint_scope="aiot_chat",
        content_length=content_length,
        text=message,
    )
    if rate_limit_error is not None:
        rate_limit_error["Cache-Control"] = "no-store, max-age=0"
        return rate_limit_error

    requested_model = (body.get("model") or "").strip() or None
    try:
        model_name = _current_model_name(request, requested_model)
    except ValueError as exc:
        return _guard_error_response(str(exc), status=400)

    request.session[SESSION_MODEL_KEY] = model_name
    history = _get_session_history(request)

    try:
        reply = llm.chat_with_system_prompt(
            message,
            history=history,
            model_name=model_name,
            system_prompt=_aiot_assistant_system_prompt(),
            usage_scope="aiot_chat",
        )
    except Exception as exc:  # noqa: BLE001
        return _guard_error_response(
            f"LLM request failed: {type(exc).__name__}: {exc}",
            status=500,
        )

    _append_session_message(request, "user", message)
    _append_session_message(request, "assistant", reply)
    return _json_no_store({"reply": reply, "model": _model_payload(model_name)})


@require_http_methods(["POST"])
def set_model_api(request):
    try:
        body = json.loads(request.body.decode("utf-8"))
    except json.JSONDecodeError:
        return JsonResponse({"error": "Request body must be valid JSON."}, status=400)

    requested_model = (body.get("model") or "").strip()
    if not requested_model:
        return JsonResponse({"error": "Model is required."}, status=400)

    try:
        model_name = llm.resolve_model(requested_model).key
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    request.session[SESSION_MODEL_KEY] = model_name
    return JsonResponse({"ok": True, "model": _model_payload(model_name)})


@require_http_methods(["POST"])
def clear_history_api(request):
    _save_session_history(request, [])
    return JsonResponse({"ok": True})
