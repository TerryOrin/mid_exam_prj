from __future__ import annotations

from datetime import timedelta

from django.db.models import Avg
from django.utils import timezone

from water.models import Pond, SensorReading


def _pond_or_error(pond_name: str) -> Pond | None:
    try:
        return Pond.objects.get(name=pond_name)
    except Pond.DoesNotExist:
        return None


def get_latest_water_quality(pond_name: str) -> dict:
    pond = _pond_or_error(pond_name)
    if pond is None:
        return {"error": f"Unknown pond: {pond_name}"}

    latest = pond.readings.first()
    if latest is None:
        return {"error": f"No readings found for pond: {pond_name}"}

    return {
        "pond": pond.name,
        "species": pond.species,
        "measured_at": latest.measured_at.isoformat(),
        "temperature_c": latest.temperature,
        "ph": latest.ph,
        "dissolved_oxygen_mg_l": latest.dissolved_oxygen,
        "salinity_ppt": latest.salinity,
    }


def get_average_do(pond_name: str, days: int = 7) -> dict:
    pond = _pond_or_error(pond_name)
    if pond is None:
        return {"error": f"Unknown pond: {pond_name}"}

    since = timezone.now() - timedelta(days=max(days, 1))
    avg = pond.readings.filter(measured_at__gte=since).aggregate(avg_do=Avg("dissolved_oxygen"))
    return {
        "pond": pond.name,
        "days": days,
        "average_dissolved_oxygen_mg_l": round(avg["avg_do"] or 0, 2),
    }


def get_water_quality_history(pond_name: str, days: int = 7) -> dict:
    pond = _pond_or_error(pond_name)
    if pond is None:
        return {"error": f"Unknown pond: {pond_name}"}

    since = timezone.now() - timedelta(days=max(days, 1))
    readings = SensorReading.objects.filter(pond=pond, measured_at__gte=since).order_by("-measured_at")
    return {
        "pond": pond.name,
        "species": pond.species,
        "days": days,
        "readings": [
            {
                "measured_at": reading.measured_at.isoformat(),
                "temperature_c": reading.temperature,
                "ph": reading.ph,
                "dissolved_oxygen_mg_l": reading.dissolved_oxygen,
                "salinity_ppt": reading.salinity,
            }
            for reading in readings
        ],
    }


def check_thresholds(pond_name: str) -> dict:
    pond = _pond_or_error(pond_name)
    if pond is None:
        return {"error": f"Unknown pond: {pond_name}"}

    latest = pond.readings.first()
    if latest is None:
        return {"error": f"No readings found for pond: {pond_name}"}

    alerts: list[str] = []
    if latest.dissolved_oxygen < 4:
        alerts.append("Dissolved oxygen is below 4 mg/L.")
    if latest.ph < 7 or latest.ph > 9:
        alerts.append("pH is outside the 7.0 to 9.0 range.")
    if latest.temperature > 32:
        alerts.append("Temperature is above 32C.")

    return {"ok": not alerts, "alerts": alerts}


def list_ponds() -> dict:
    return {
        "ponds": list(Pond.objects.values("name", "species", "description").order_by("name"))
    }


TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_latest_water_quality",
            "description": "Get the latest water quality reading for a pond.",
            "parameters": {
                "type": "object",
                "properties": {"pond_name": {"type": "string", "description": "The pond name."}},
                "required": ["pond_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_average_do",
            "description": "Get the average dissolved oxygen for a pond over the last N days.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pond_name": {"type": "string", "description": "The pond name."},
                    "days": {"type": "integer", "description": "Number of days to average."},
                },
                "required": ["pond_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_water_quality_history",
            "description": "Get recent water quality history for a pond.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pond_name": {"type": "string", "description": "The pond name."},
                    "days": {"type": "integer", "description": "Number of days to retrieve."},
                },
                "required": ["pond_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_thresholds",
            "description": "Check whether the latest pond reading triggers threshold alerts.",
            "parameters": {
                "type": "object",
                "properties": {"pond_name": {"type": "string", "description": "The pond name."}},
                "required": ["pond_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_ponds",
            "description": "List all ponds available in the system.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


_TOOL_REGISTRY = {
    "get_latest_water_quality": get_latest_water_quality,
    "get_average_do": get_average_do,
    "get_water_quality_history": get_water_quality_history,
    "check_thresholds": check_thresholds,
    "list_ponds": list_ponds,
}


def dispatch(name: str, arguments: dict) -> dict:
    fn = _TOOL_REGISTRY.get(name)
    if fn is None:
        return {"error": f"Unknown tool: {name}"}
    try:
        return fn(**arguments)
    except TypeError as exc:
        return {"error": f"Invalid arguments: {exc}"}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"Tool execution failed: {type(exc).__name__}: {exc}"}
