from __future__ import annotations

import math
import random
from datetime import datetime, timedelta

from django.db.models import OuterRef, Subquery
from django.utils import timezone

from water.models import Pond, SensorReading

IOT_DEFAULT_WINDOW_HOURS = 12
IOT_DEFAULT_INTERVAL_MINUTES = 5
IOT_MAX_WINDOW_HOURS = 24
IOT_POLL_SECONDS = 15
IOT_SITE_NAME = "IoT 智慧養殖戰情室"

IOT_METRIC_CONFIG = {
    "temperature_c": {
        "label": "水溫",
        "unit": "°C",
        "min": 20.0,
        "max": 30.0,
        "safe_low": 22.0,
        "safe_high": 28.0,
        "watch_low": 21.0,
        "watch_high": 29.0,
    },
    "ph": {
        "label": "pH 值",
        "unit": "",
        "min": 6.5,
        "max": 8.5,
        "safe_low": 6.8,
        "safe_high": 8.2,
        "watch_low": 6.7,
        "watch_high": 8.3,
    },
    "dissolved_oxygen_mg_l": {
        "label": "溶氧量",
        "unit": "mg/L",
        "min": 4.0,
        "max": 8.0,
        "safe_low": 5.5,
        "safe_high": 7.5,
        "watch_low": 5.0,
        "watch_high": 7.8,
    },
}

IOT_SUMMARY_CONFIG = {
    "temperature_c": {"label": "平均溫度", "unit": "C"},
    "ph": {"label": "平均 pH", "unit": ""},
    "dissolved_oxygen_mg_l": {"label": "平均溶氧", "unit": "mg/L"},
    "ammonia_mg_l": {"label": "Ammonia (NH3)", "unit": "mg/L"},
    "nitrite_mg_l": {"label": "Nitrite (NO2-)", "unit": "mg/L"},
}


def _clamp_float(value: float, lower: float, upper: float) -> float:
    return max(lower, min(value, upper))


def _latest_reading_subquery(field_name: str) -> Subquery:
    latest_reading = SensorReading.objects.filter(pond=OuterRef("pk")).order_by("-measured_at")
    return Subquery(latest_reading.values(field_name)[:1])


def _build_latest_pond_queryset():
    return Pond.objects.order_by("name").annotate(
        latest_measured_at=_latest_reading_subquery("measured_at"),
        latest_temperature=_latest_reading_subquery("temperature"),
        latest_ph=_latest_reading_subquery("ph"),
        latest_dissolved_oxygen=_latest_reading_subquery("dissolved_oxygen"),
        latest_ammonia=_latest_reading_subquery("ammonia"),
        latest_nitrite=_latest_reading_subquery("nitrite"),
    )


def _threshold_alerts(
    *,
    temperature: float | None,
    ph_value: float | None,
    dissolved_oxygen: float | None,
    ammonia: float | None,
    nitrite: float | None,
) -> list[str]:
    alerts: list[str] = []
    if dissolved_oxygen is not None and dissolved_oxygen < 4:
        alerts.append("Dissolved oxygen is below 4 mg/L.")
    if ph_value is not None and (ph_value < 7 or ph_value > 9):
        alerts.append("pH is outside the 7.0 to 9.0 range.")
    if temperature is not None and temperature > 32:
        alerts.append("Temperature is above 32C.")
    if ammonia is not None and ammonia > 0.2:
        alerts.append("Ammonia (NH3) is above 0.2 mg/L.")
    if nitrite is not None and nitrite > 0.5:
        alerts.append("Nitrite (NO2-) is above 0.5 mg/L.")
    return alerts


def _round_or_none(value) -> float | None:
    if value is None:
        return None
    return round(float(value), 2)


def build_dashboard_payload(*, include_war_room: bool = False) -> dict:
    pond_summaries = []
    readings: dict[str, list[float]] = {
        "temperature_c": [],
        "ph": [],
        "dissolved_oxygen_mg_l": [],
        "ammonia_mg_l": [],
        "nitrite_mg_l": [],
    }
    alert_count = 0

    for pond in _build_latest_pond_queryset():
        measured_at = pond.latest_measured_at
        if measured_at is None:
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

        temperature = _round_or_none(pond.latest_temperature)
        ph_value = _round_or_none(pond.latest_ph)
        dissolved_oxygen = _round_or_none(pond.latest_dissolved_oxygen)
        ammonia = _round_or_none(pond.latest_ammonia)
        nitrite = _round_or_none(pond.latest_nitrite)
        alerts = _threshold_alerts(
            temperature=temperature,
            ph_value=ph_value,
            dissolved_oxygen=dissolved_oxygen,
            ammonia=ammonia,
            nitrite=nitrite,
        )
        alert_count += len(alerts)

        metric_pairs = {
            "temperature_c": temperature,
            "ph": ph_value,
            "dissolved_oxygen_mg_l": dissolved_oxygen,
            "ammonia_mg_l": ammonia,
            "nitrite_mg_l": nitrite,
        }
        for key, value in metric_pairs.items():
            if value is not None:
                readings[key].append(value)

        pond_summaries.append(
            {
                "name": pond.name,
                "species": pond.species,
                "description": pond.description,
                "status": "Alert" if alerts else "Normal",
                "has_measurements": True,
                "measured_at": timezone.localtime(measured_at).isoformat(),
                "temperature_c": temperature,
                "ph": ph_value,
                "dissolved_oxygen_mg_l": dissolved_oxygen,
                "ammonia_mg_l": ammonia,
                "nitrite_mg_l": nitrite,
                "alerts": alerts,
            }
        )

    def _avg(metric_key: str) -> float | None:
        values = readings[metric_key]
        if not values:
            return None
        return round(sum(values) / len(values), 2)

    payload = {
        "metrics": {
            "temperature_c": _avg("temperature_c"),
            "ph": _avg("ph"),
            "dissolved_oxygen_mg_l": _avg("dissolved_oxygen_mg_l"),
            "ammonia_mg_l": _avg("ammonia_mg_l"),
            "nitrite_mg_l": _avg("nitrite_mg_l"),
            "alert_count": alert_count,
        },
        "ponds": pond_summaries,
    }

    if include_war_room:
        payload["war_room"] = build_iot_payload(dashboard_payload=payload)

    return payload


def _stable_noise(metric_key: str, dt: datetime, amplitude: float, *, bucket_seconds: int = 900) -> float:
    bucket = int(dt.timestamp() // bucket_seconds)
    rng = random.Random(f"{metric_key}:{bucket}")
    return rng.uniform(-amplitude, amplitude)


def _simulate_iot_snapshot(dt: datetime) -> dict[str, float]:
    local_dt = timezone.localtime(dt)
    ts = local_dt.timestamp()
    seconds_in_day = (
        local_dt.hour * 3600
        + local_dt.minute * 60
        + local_dt.second
        + (local_dt.microsecond / 1_000_000)
    )
    day_angle = (seconds_in_day / 86400.0) * math.tau
    hour_decimal = local_dt.hour + (local_dt.minute / 60.0)

    temperature = (
        25.1
        + 2.6 * math.sin(day_angle - 1.05)
        + 0.55 * math.sin((math.tau * ts / 7200.0) + 0.7)
        + _stable_noise("temperature", local_dt, 0.28, bucket_seconds=600)
    )
    temperature = round(_clamp_float(temperature, 20.0, 30.0), 2)

    ph_value = (
        7.35
        + 0.28 * math.sin(day_angle - 0.2)
        + 0.12 * math.sin((math.tau * ts / 14400.0) + 1.8)
        + _stable_noise("ph", local_dt, 0.08, bucket_seconds=900)
    )
    ph_value = round(_clamp_float(ph_value, 6.5, 8.5), 2)

    dawn_dip = math.exp(-(((hour_decimal - 5.1) / 1.5) ** 2))
    dissolved_oxygen = (
        6.2
        - 0.22 * (temperature - 25.0)
        + 0.72 * math.sin(day_angle + 1.9)
        + 0.36 * math.sin((math.tau * ts / 10800.0) - 0.45)
        - 0.95 * dawn_dip
        + _stable_noise("dissolved_oxygen", local_dt, 0.18, bucket_seconds=600)
    )
    dissolved_oxygen = round(_clamp_float(dissolved_oxygen, 4.0, 8.0), 2)

    return {
        "temperature_c": temperature,
        "ph": ph_value,
        "dissolved_oxygen_mg_l": dissolved_oxygen,
    }


def _status_for_metric(metric_key: str, value: float) -> dict[str, str]:
    config = IOT_METRIC_CONFIG[metric_key]
    if config["safe_low"] <= value <= config["safe_high"]:
        return {"level": "good", "label": "穩定", "detail": "位於建議運作區間。"}
    if config["watch_low"] <= value <= config["watch_high"]:
        return {"level": "watch", "label": "注意", "detail": "接近警戒邊界，建議持續觀察。"}
    return {"level": "alert", "label": "警示", "detail": "已偏離安全區間，建議立即巡檢。"}


def build_metric_payload(metric_key: str, value: float) -> dict[str, object]:
    config = IOT_METRIC_CONFIG[metric_key]
    status = _status_for_metric(metric_key, value)
    span = config["max"] - config["min"]
    progress = ((value - config["min"]) / span) * 100 if span else 0

    return {
        "label": config["label"],
        "value": round(value, 2),
        "unit": config["unit"],
        "display": f"{value:.2f} {config['unit']}".strip(),
        "range_min": config["min"],
        "range_max": config["max"],
        "safe_low": config["safe_low"],
        "safe_high": config["safe_high"],
        "progress_pct": round(_clamp_float(progress, 0, 100), 1),
        "status": status,
    }


def build_summary_metric_payload(metric_key: str, value: float | None) -> dict[str, object]:
    config = IOT_SUMMARY_CONFIG[metric_key]
    if value is None:
        return {
            "label": config["label"],
            "value": None,
            "unit": config["unit"],
            "display": "--",
        }

    rounded = round(float(value), 2)
    return {
        "label": config["label"],
        "value": rounded,
        "unit": config["unit"],
        "display": f"{rounded:.2f} {config['unit']}".strip(),
    }


def overview_from_snapshot(snapshot: dict[str, float], *, alert_count: int = 0) -> dict[str, object]:
    temperature_status = _status_for_metric("temperature_c", snapshot["temperature_c"])
    ph_status = _status_for_metric("ph", snapshot["ph"])
    do_status = _status_for_metric("dissolved_oxygen_mg_l", snapshot["dissolved_oxygen_mg_l"])
    levels = [temperature_status["level"], ph_status["level"], do_status["level"]]

    if "alert" in levels:
        return {
            "level": "alert",
            "label": "需立即處理",
            "message": "至少一項指標超出安全區間，建議立即巡檢魚塭與增氧設備。",
            "alert_count": alert_count or levels.count("alert"),
        }
    if "watch" in levels:
        return {
            "level": "watch",
            "label": "持續觀察",
            "message": "目前有指標接近警戒邊界，建議留意清晨與午後波動。",
            "alert_count": alert_count or levels.count("watch"),
        }
    return {
        "level": "good",
        "label": "狀態穩定",
        "message": "目前水質維持在建議區間，可持續例行巡檢。",
        "alert_count": alert_count,
    }


def _current_snapshot_from_dashboard(
    dashboard_payload: dict | None,
    *,
    now: datetime,
) -> tuple[dict[str, float], str, str]:
    metrics = (dashboard_payload or {}).get("metrics") or {}
    try:
        temperature = float(metrics["temperature_c"])
        ph_value = float(metrics["ph"])
        dissolved_oxygen = float(metrics["dissolved_oxygen_mg_l"])
    except (KeyError, TypeError, ValueError):
        return _simulate_iot_snapshot(now), "simulated-read-time", "即時計算模擬"

    snapshot = {
        "temperature_c": round(_clamp_float(temperature, 20.0, 30.0), 2),
        "ph": round(_clamp_float(ph_value, 6.5, 8.5), 2),
        "dissolved_oxygen_mg_l": round(_clamp_float(dissolved_oxygen, 4.0, 8.0), 2),
    }
    return snapshot, "dashboard-anchored-latest", "AIOT 即時數據對齊"


def _build_iot_history(
    now: datetime,
    *,
    hours: int,
    interval_minutes: int,
    current_snapshot: dict[str, float],
) -> list[dict[str, object]]:
    start = now - timedelta(hours=hours)
    cursor = start.replace(second=0, microsecond=0)
    step = timedelta(minutes=interval_minutes)
    history: list[dict[str, object]] = []

    simulated_now = _simulate_iot_snapshot(now)
    deltas = {
        metric_key: current_snapshot[metric_key] - simulated_now[metric_key]
        for metric_key in current_snapshot
    }

    while cursor < now:
        simulated_point = _simulate_iot_snapshot(cursor)
        point = {}
        for metric_key, config in IOT_METRIC_CONFIG.items():
            shifted_value = simulated_point[metric_key] + deltas[metric_key]
            point[metric_key] = round(
                _clamp_float(shifted_value, config["min"], config["max"]),
                2,
            )

        local_cursor = timezone.localtime(cursor)
        history.append(
            {
                "timestamp": local_cursor.isoformat(),
                "label": local_cursor.strftime("%H:%M"),
                **point,
            }
        )
        cursor += step

    local_now = timezone.localtime(now)
    history.append(
        {
            "timestamp": local_now.isoformat(),
            "label": local_now.strftime("%H:%M"),
            **current_snapshot,
        }
    )
    return history


def build_iot_payload(
    *,
    hours: int = IOT_DEFAULT_WINDOW_HOURS,
    interval_minutes: int = IOT_DEFAULT_INTERVAL_MINUTES,
    dashboard_payload: dict | None = None,
    now: datetime | None = None,
) -> dict[str, object]:
    current_time = timezone.localtime(now or timezone.now()).replace(microsecond=0)
    dashboard = dashboard_payload or build_dashboard_payload(include_war_room=False)
    current_snapshot, source, source_label = _current_snapshot_from_dashboard(
        dashboard,
        now=current_time,
    )
    history = _build_iot_history(
        current_time,
        hours=hours,
        interval_minutes=interval_minutes,
        current_snapshot=current_snapshot,
    )
    dashboard_metrics = dashboard.get("metrics") or {}
    alert_count = int((dashboard_metrics.get("alert_count")) or 0)

    current_payload = {
        "timestamp": timezone.localtime(current_time).isoformat(),
        "temperature_c": build_metric_payload("temperature_c", current_snapshot["temperature_c"]),
        "ph": build_metric_payload("ph", current_snapshot["ph"]),
        "dissolved_oxygen_mg_l": build_metric_payload(
            "dissolved_oxygen_mg_l",
            current_snapshot["dissolved_oxygen_mg_l"],
        ),
    }
    summary_payload = {
        key: build_summary_metric_payload(key, dashboard_metrics.get(key))
        for key in IOT_SUMMARY_CONFIG
    }
    summary_payload["alert_count"] = {
        "label": "警示數量",
        "value": alert_count,
        "unit": "",
        "display": str(alert_count),
    }

    return {
        "resource": "iot_data",
        "site": IOT_SITE_NAME,
        "source": source,
        "source_label": source_label,
        "generated_at": timezone.localtime(current_time).isoformat(),
        "window_hours": hours,
        "interval_minutes": interval_minutes,
        "refresh_after_seconds": IOT_POLL_SECONDS,
        "summary": summary_payload,
        "current": current_payload,
        "overview": overview_from_snapshot(current_snapshot, alert_count=alert_count),
        "history": history,
        "metric_ranges": IOT_METRIC_CONFIG,
    }
