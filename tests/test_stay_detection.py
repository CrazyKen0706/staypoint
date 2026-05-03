from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from scripts.stay_detection import TrackPoint, detect_stays_for_user_day, distance_m


class StayDetectionTests(unittest.TestCase):
    def test_detects_three_close_temporally_continuous_points(self) -> None:
        origin_lon = 139.73494
        origin_lat = 35.60625
        start = datetime(2021, 11, 1, 10, 0, 0)
        points = [
            TrackPoint("u1", start, origin_lon, origin_lat, 10, "20211101"),
            TrackPoint("u1", start + timedelta(seconds=60), origin_lon + 0.00005, origin_lat, 20, "20211101"),
            TrackPoint("u1", start + timedelta(seconds=120), origin_lon, origin_lat + 0.00005, 30, "20211101"),
        ]

        stays = detect_stays_for_user_day("u1", points, origin_lon, origin_lat)

        self.assertEqual(len(stays), 1)
        self.assertEqual(stays[0].point_count, 3)
        self.assertEqual(stays[0].duration_sec, 120)
        self.assertAlmostEqual(stays[0].mean_accuracy or 0, 20)

    def test_breaks_on_time_gap(self) -> None:
        origin_lon = 139.73494
        origin_lat = 35.60625
        start = datetime(2021, 11, 1, 10, 0, 0)
        points = [
            TrackPoint("u1", start, origin_lon, origin_lat, None, "20211101"),
            TrackPoint("u1", start + timedelta(seconds=60), origin_lon, origin_lat, None, "20211101"),
            TrackPoint("u1", start + timedelta(seconds=300), origin_lon, origin_lat, None, "20211101"),
        ]

        stays = detect_stays_for_user_day("u1", points, origin_lon, origin_lat)

        self.assertEqual(stays, [])

    def test_distance_is_reasonable_for_oimachi_scale(self) -> None:
        self.assertLess(distance_m(139.73494, 35.60625, 139.73504, 35.60625), 12)


if __name__ == "__main__":
    unittest.main()
