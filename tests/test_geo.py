"""Tests for the geodesy and route engine. No device required."""
import math
import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from spoofer import geo  # noqa: E402

SF = (37.7749, -122.4194)


def test_haversine_known_distance():
    # SF to NYC, roughly 4130 km.
    d = geo.haversine_m(37.7749, -122.4194, 40.7128, -74.0060)
    assert 4_120_000 < d < 4_140_000


def test_destination_round_trips_through_haversine():
    for bearing in (0, 45, 90, 180, 270, 359):
        lat, lon = geo.destination(*SF, bearing, 1000.0)
        assert geo.haversine_m(*SF, lat, lon) == pytest.approx(1000.0, abs=0.5)


def test_destination_bearing_is_recoverable():
    for bearing in (0, 37, 90, 181, 270):
        lat, lon = geo.destination(*SF, bearing, 500.0)
        assert geo.bearing_deg(*SF, lat, lon) == pytest.approx(bearing, abs=0.5)


def test_wrap_lon_and_clamp_lat():
    assert geo.wrap_lon(181) == -179
    assert geo.wrap_lon(-181) == 179
    assert geo.wrap_lon(0) == 0
    assert geo.clamp_lat(91) == 90
    assert geo.clamp_lat(-91) == -90


def test_antimeridian_crossing_stays_in_range():
    lat, lon = geo.destination(0.0, 179.99, 90, 5000.0)
    assert -180.0 <= lon <= 180.0
    assert lon < 0  # wrapped to the western hemisphere


class TestRoute:
    points = [(37.7749, -122.4194), (37.7749, -122.4094), (37.7849, -122.4094)]

    def test_needs_two_points(self):
        with pytest.raises(ValueError):
            geo.Route([(0, 0)])

    def test_starts_at_first_point(self):
        lat, lon, _ = geo.Route(self.points).position()
        assert (lat, lon) == pytest.approx(self.points[0])

    def test_advance_covers_expected_ground(self):
        r = geo.Route(self.points)
        r.advance(100.0)
        lat, lon, _ = r.position()
        assert geo.haversine_m(*self.points[0], lat, lon) == pytest.approx(100.0, abs=1.0)

    def test_advance_spills_across_segments(self):
        r = geo.Route(self.points)
        first_leg = geo.haversine_m(*self.points[0], *self.points[1])
        r.advance(first_leg + 50.0)
        assert r.segment == 1
        lat, lon, _ = r.position()
        assert geo.haversine_m(*self.points[1], lat, lon) == pytest.approx(50.0, abs=1.0)

    def test_one_shot_route_finishes(self):
        r = geo.Route(self.points)
        r.advance(r.total_m * 2)
        assert r.finished

    def test_loop_never_finishes(self):
        r = geo.Route(self.points, loop=True)
        r.advance(r.total_m * 5)
        assert not r.finished

    def test_ping_pong_reverses_and_never_finishes(self):
        r = geo.Route(self.points, ping_pong=True)
        r.advance(r.total_m * 1.25)
        assert r.direction == -1
        assert not r.finished

    def test_huge_advance_terminates(self):
        # Guards the bounded loop in advance(): a vast step on a short route must not hang.
        r = geo.Route([(0.0, 0.0), (0.0, 0.0001)])
        r.advance(1e9)
        assert r.finished


class TestGpx:
    def test_parses_track_points(self):
        xml = (
            '<?xml version="1.0"?><gpx><trk><trkseg>'
            '<trkpt lat="51.5074" lon="-0.1278"/><trkpt lat="51.5080" lon="-0.1290"/>'
            "</trkseg></trk></gpx>"
        )
        assert geo.parse_gpx(xml) == [(51.5074, -0.1278), (51.5080, -0.1290)]

    def test_falls_back_to_waypoints(self):
        xml = '<?xml version="1.0"?><gpx><wpt lat="1" lon="2"/><wpt lat="3" lon="4"/></gpx>'
        assert geo.parse_gpx(xml) == [(1.0, 2.0), (3.0, 4.0)]

    def test_rejects_dtd_entity_expansion(self):
        # Billion laughs. The expat bundled with older Pythons has no default cap.
        xml = (
            '<?xml version="1.0"?><!DOCTYPE gpx [<!ENTITY a "AA">]>'
            '<gpx><trk><trkseg><trkpt lat="1" lon="2"/><trkpt lat="3" lon="4"/>'
            "</trkseg></trk></gpx>"
        )
        with pytest.raises(ValueError, match="DTD or entity"):
            geo.parse_gpx(xml)

    def test_rejects_file_without_enough_points(self):
        with pytest.raises(ValueError):
            geo.parse_gpx('<?xml version="1.0"?><gpx><wpt lat="1" lon="2"/></gpx>')


class TestJitter:
    def test_stays_within_radius(self):
        rng = random.Random(1)
        for _ in range(500):
            lat, lon = geo.jitter(*SF, 10.0, rng)
            assert geo.haversine_m(*SF, lat, lon) <= 10.0 + 1e-6

    def test_zero_radius_is_identity(self):
        assert geo.jitter(*SF, 0.0, random.Random(1)) == SF

    def test_distribution_is_area_uniform(self):
        # sqrt() sampling means mean radius should sit near 2/3 R, not R/2.
        rng = random.Random(7)
        radii = [geo.haversine_m(*SF, *geo.jitter(*SF, 10.0, rng)) for _ in range(4000)]
        assert sum(radii) / len(radii) == pytest.approx(10.0 * 2 / 3, rel=0.06)


def test_looks_like_coord():
    assert geo.looks_like_coord(0, 0)
    assert geo.looks_like_coord(-90, 180)
    assert not geo.looks_like_coord(91, 0)
    assert not geo.looks_like_coord(0, 181)
    assert not geo.looks_like_coord(None, 0)
