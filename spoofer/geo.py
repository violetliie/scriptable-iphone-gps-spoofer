"""Geodesy helpers for route interpolation and joystick movement."""
from __future__ import annotations

import math
from typing import Optional

EARTH_RADIUS_M = 6_371_008.8


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(a)))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial bearing from point 1 to point 2, in degrees clockwise from north."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def destination(lat: float, lon: float, bearing: float, distance_m: float) -> tuple[float, float]:
    """Point reached by travelling `distance_m` along `bearing` from (lat, lon)."""
    d = distance_m / EARTH_RADIUS_M
    b = math.radians(bearing)
    p1, l1 = math.radians(lat), math.radians(lon)
    p2 = math.asin(math.sin(p1) * math.cos(d) + math.cos(p1) * math.sin(d) * math.cos(b))
    l2 = l1 + math.atan2(
        math.sin(b) * math.sin(d) * math.cos(p1),
        math.cos(d) - math.sin(p1) * math.sin(p2),
    )
    return math.degrees(p2), (math.degrees(l2) + 540.0) % 360.0 - 180.0


def clamp_lat(lat: float) -> float:
    return max(-90.0, min(90.0, lat))


def wrap_lon(lon: float) -> float:
    return (lon + 540.0) % 360.0 - 180.0


class Route:
    """A polyline walked at a constant ground speed, optionally looping.

    Holds a cursor as (segment index, metres travelled into that segment) so
    `advance` can cross any number of waypoints in a single tick.
    """

    def __init__(self, points: list[tuple[float, float]], loop: bool = False, ping_pong: bool = False) -> None:
        if len(points) < 2:
            raise ValueError("a route needs at least two points")
        self.points = points
        self.loop = loop
        self.ping_pong = ping_pong
        self.direction = 1
        self.segment = 0
        self.offset_m = 0.0
        self.finished = False

    @property
    def total_m(self) -> float:
        return sum(
            haversine_m(*self.points[i], *self.points[i + 1]) for i in range(len(self.points) - 1)
        )

    def _segment_endpoints(self) -> tuple[tuple[float, float], tuple[float, float]]:
        if self.direction == 1:
            return self.points[self.segment], self.points[self.segment + 1]
        return self.points[self.segment + 1], self.points[self.segment]

    def position(self) -> tuple[float, float, float]:
        """Current (lat, lon, heading) along the route."""
        a, b = self._segment_endpoints()
        head = bearing_deg(*a, *b)
        lat, lon = destination(*a, head, self.offset_m)
        return lat, lon, head

    def advance(self, distance_m: float) -> None:
        """Move the cursor forward, spilling over into later segments as needed."""
        remaining = distance_m
        # Bounded so a huge speed on a short route can't spin forever.
        for _ in range(10_000):
            if self.finished or remaining <= 0:
                return
            a, b = self._segment_endpoints()
            seg_len = haversine_m(*a, *b)
            if self.offset_m + remaining < seg_len:
                self.offset_m += remaining
                return
            remaining -= max(0.0, seg_len - self.offset_m)
            self.offset_m = 0.0
            self._next_segment()

    def _next_segment(self) -> None:
        last = len(self.points) - 2
        if self.direction == 1:
            if self.segment < last:
                self.segment += 1
                return
            if self.ping_pong:
                self.direction = -1
                return
            if self.loop:
                self.segment = 0
                return
            self.finished = True
        else:
            if self.segment > 0:
                self.segment -= 1
                return
            if self.loop or self.ping_pong:
                self.direction = 1
                return
            self.finished = True


def parse_gpx(xml_text: str) -> list[tuple[float, float]]:
    """Pull an ordered point list out of a GPX file (track points, else route points, else waypoints)."""
    import xml.etree.ElementTree as ET

    # GPX has no legitimate use for a DTD, and the expat bundled with older Pythons has no
    # default cap on entity expansion, so a declaration here is either broken or a billion-laughs.
    head = xml_text[:8192].upper()
    if "<!DOCTYPE" in head or "<!ENTITY" in head:
        raise ValueError("GPX files with a DTD or entity declarations are rejected")

    root = ET.fromstring(xml_text)

    def collect(tag: str) -> list[tuple[float, float]]:
        found = []
        for el in root.iter():
            if el.tag.rsplit("}", 1)[-1] != tag:
                continue
            lat, lon = el.get("lat"), el.get("lon")
            if lat is not None and lon is not None:
                found.append((float(lat), float(lon)))
        return found

    for tag in ("trkpt", "rtept", "wpt"):
        pts = collect(tag)
        if len(pts) >= 2:
            return pts
    raise ValueError("no usable <trkpt>/<rtept>/<wpt> pairs found in GPX")


def jitter(lat: float, lon: float, radius_m: float, rng) -> tuple[float, float]:
    """Nudge a fix by a random offset inside `radius_m`, mimicking real GPS noise."""
    if radius_m <= 0:
        return lat, lon
    r = radius_m * math.sqrt(rng.random())
    return destination(lat, lon, rng.uniform(0.0, 360.0), r)


def looks_like_coord(lat: Optional[float], lon: Optional[float]) -> bool:
    return (
        lat is not None
        and lon is not None
        and -90.0 <= lat <= 90.0
        and -180.0 <= lon <= 180.0
    )
