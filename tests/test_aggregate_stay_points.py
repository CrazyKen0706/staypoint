from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from scripts.aggregate_stay_points import aggregate_stays


def stay(stay_id: str, start: datetime, lon: float = 139.73494, lat: float = 35.60625):
    return {
        "stay_id": stay_id,
        "userid": "u1",
        "date": "20211101",
        "start_time": start.isoformat(sep=" "),
        "end_time": (start + timedelta(minutes=2)).isoformat(sep=" "),
        "duration_sec": 120.0,
        "point_count": 3,
        "lon": lon,
        "lat": lat,
        "mean_accuracy": 10.0,
        "source_days": "20211101",
        "anonymous_removed_flag": "True",
        "_start_dt": start,
        "_end_dt": start + timedelta(minutes=2),
    }


class AggregateStayPointTests(unittest.TestCase):
    def test_same_place_morning_and_evening_are_not_merged(self) -> None:
        morning = datetime(2021, 11, 1, 8, 0)
        evening = datetime(2021, 11, 1, 20, 0)
        rows = [
            stay("s1", morning),
            stay("s2", morning + timedelta(minutes=5), lon=139.73500),
            stay("s3", evening),
            stay("s4", evening + timedelta(minutes=5), lon=139.73500),
        ]

        aggregated, _ = aggregate_stays(
            rows,
            origin_lon=139.73494,
            origin_lat=35.60625,
            eps_m=150,
            min_source_stays=2,
            time_window_sec=1800,
        )

        self.assertEqual(len(aggregated), 2)
        self.assertEqual([row["source_stay_count"] for row in aggregated], [2, 2])


if __name__ == "__main__":
    unittest.main()
