"""Named locations, persisted so they survive restarts and folder moves."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("spoofer.favorites")

STORE = Path.home() / ".iphone-location-simulator" / "favorites.json"

# Seeded on first run so the list is never an empty box with no explanation.
SEED = [
    {"name": "Eiffel Tower", "lat": 48.8584, "lon": 2.2945},
    {"name": "Shibuya Crossing", "lat": 35.6595, "lon": 139.7005},
    {"name": "Times Square", "lat": 40.7580, "lon": -73.9855},
]


def _read() -> dict[str, Any]:
    try:
        data = json.loads(STORE.read_text())
    except FileNotFoundError:
        return {"favorites": list(SEED), "last": None}
    except (OSError, ValueError):
        log.warning("favorites file is unreadable; starting fresh")
        return {"favorites": list(SEED), "last": None}
    if not isinstance(data, dict):
        return {"favorites": list(SEED), "last": None}
    data.setdefault("favorites", [])
    data.setdefault("last", None)
    return data


def _write(data: dict[str, Any]) -> None:
    try:
        STORE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STORE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        tmp.replace(STORE)
    except OSError as exc:
        log.warning("could not save favorites: %s", exc)


def load() -> dict[str, Any]:
    return _read()


def add(name: str, lat: float, lon: float) -> dict[str, Any]:
    data = _read()
    name = (name or "").strip()[:60] or f"{lat:.4f}, {lon:.4f}"
    # Re-saving under an existing name overwrites rather than duplicating.
    data["favorites"] = [f for f in data["favorites"] if f.get("name") != name]
    data["favorites"].insert(0, {"name": name, "lat": lat, "lon": lon})
    data["favorites"] = data["favorites"][:50]
    _write(data)
    return data


def remove(name: str) -> dict[str, Any]:
    data = _read()
    data["favorites"] = [f for f in data["favorites"] if f.get("name") != name]
    _write(data)
    return data


def remember_last(lat: float, lon: float) -> None:
    """Record where the phone was last put, so it can be offered again next launch."""
    data = _read()
    last = data.get("last")
    if last and abs(last.get("lat", 0) - lat) < 1e-6 and abs(last.get("lon", 0) - lon) < 1e-6:
        return
    data["last"] = {"lat": lat, "lon": lon}
    _write(data)


def last() -> Optional[dict[str, float]]:
    return _read().get("last")
