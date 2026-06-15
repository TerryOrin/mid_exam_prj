from __future__ import annotations

import json

from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_http_methods

from water.models import Pond

from . import llm, tools

SESSION_MODEL_KEY = "aiot_selected_model"
SESSION_HISTORY_KEY = "aiot_chat_history"
MAX_HISTORY_MESSAGES = 12


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
    return sanitized_history[-MAX_HISTORY_MESSAGES:]


def _save_session_history(request, history: list[dict[str, str]]) -> None:
    request.session[SESSION_HISTORY_KEY] = history[-MAX_HISTORY_MESSAGES:]
    request.session.modified = True


def _append_session_message(request, role: str, content: str) -> None:
    if role not in {"user", "assistant"} or not content:
        return

    history = _get_session_history(request)
    history.append({"role": role, "content": content})
    _save_session_history(request, history)


def _dashboard_payload() -> dict:
    pond_summaries = []
    readings = []
    alert_count = 0

    for pond in Pond.objects.prefetch_related("readings").order_by("name"):
        latest = pond.readings.first()
        if latest is None:
            pond_summaries.append(
                {
                    "name": pond.name,
                    "species": pond.species,
                    "description": pond.description,
                    "status": "No data",
                    "has_measurements": False,
                }
            )
            continue

        readings.append(latest)
        threshold_result = tools.check_thresholds(pond.name)
        alerts = threshold_result.get("alerts", [])
        alert_count += len(alerts)
        pond_summaries.append(
            {
                "name": pond.name,
                "species": pond.species,
                "description": pond.description,
                "status": "Alert" if alerts else "Normal",
                "has_measurements": True,
                "measured_at": latest.measured_at.isoformat(),
                "temperature_c": latest.temperature,
                "ph": latest.ph,
                "dissolved_oxygen_mg_l": latest.dissolved_oxygen,
                "ammonia_mg_l": latest.ammonia,
                "nitrite_mg_l": latest.nitrite,
                "alerts": alerts,
            }
        )

    def _avg(attr: str) -> float | None:
        values = [getattr(reading, attr) for reading in readings if getattr(reading, attr) is not None]
        if not values:
            return None
        return round(sum(values) / len(values), 2)

    return {
        "metrics": {
            "temperature_c": _avg("temperature"),
            "ph": _avg("ph"),
            "dissolved_oxygen_mg_l": _avg("dissolved_oxygen"),
            "ammonia_mg_l": _avg("ammonia"),
            "nitrite_mg_l": _avg("nitrite"),
            "alert_count": alert_count,
        },
        "ponds": pond_summaries,
    }


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
    try:
        body = json.loads(request.body.decode("utf-8"))
    except json.JSONDecodeError:
        return JsonResponse({"error": "Request body must be valid JSON."}, status=400)

    message = (body.get("message") or "").strip()
    if not message:
        return JsonResponse({"error": "Message is required."}, status=400)

    requested_model = (body.get("model") or "").strip() or None
    try:
        model_name = _current_model_name(request, requested_model)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    request.session[SESSION_MODEL_KEY] = model_name
    history = _get_session_history(request)

    try:
        reply = llm.chat(message, history=history, model_name=model_name)
    except Exception as exc:  # noqa: BLE001
        return JsonResponse({"error": f"LLM request failed: {type(exc).__name__}: {exc}"}, status=500)

    _append_session_message(request, "user", message)
    _append_session_message(request, "assistant", reply)
    return JsonResponse({"reply": reply, "model": _model_payload(model_name)})


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
