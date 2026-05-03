from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from math import cos, radians, sqrt
from typing import Iterable, Sequence


@dataclass(slots=True)
class TrackPoint:
    user_id: str
    timestamp: datetime
    lon: float
    lat: float
    accuracy: float | None = None
    source_date: str | None = None


@dataclass(slots=True)
class StayPoint:
    user_id: str
    start_time: datetime
    end_time: datetime
    lon: float
    lat: float
    duration_sec: float
    point_count: int
    mean_accuracy: float | None
    source_days: str


def project_xy_m(lon: float, lat: float, origin_lon: float, origin_lat: float) -> tuple[float, float]:
    """Project lon/lat to a local meter plane around the study site."""
    meters_per_deg_lat = 111_320.0
    meters_per_deg_lon = meters_per_deg_lat * cos(radians(origin_lat))
    return (lon - origin_lon) * meters_per_deg_lon, (lat - origin_lat) * meters_per_deg_lat


def distance_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    origin_lat = (lat1 + lat2) / 2.0
    x1, y1 = project_xy_m(lon1, lat1, lon1, origin_lat)
    x2, y2 = project_xy_m(lon2, lat2, lon1, origin_lat)
    return sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)


def split_temporally_continuous(
    points: Sequence[TrackPoint],
    max_gap_sec: int,
) -> list[list[TrackPoint]]:
    if not points:
        return []

    sorted_points = sorted(points, key=lambda p: p.timestamp)
    sequences: list[list[TrackPoint]] = []
    current = [sorted_points[0]]

    for point in sorted_points[1:]:
        gap = (point.timestamp - current[-1].timestamp).total_seconds()
        if gap <= max_gap_sec:
            current.append(point)
        else:
            sequences.append(current)
            current = [point]

    sequences.append(current)
    return sequences


def _build_stay_from_points(
    user_id: str,
    cluster_points: Sequence[TrackPoint],
    stay_min_points: int,
    max_duration_sec_per_point: int,
) -> StayPoint | None:
    point_count = len(cluster_points)
    if point_count < stay_min_points:
        return None

    sorted_points = sorted(cluster_points, key=lambda p: p.timestamp)
    start_time = sorted_points[0].timestamp
    end_time = sorted_points[-1].timestamp
    duration_sec = (end_time - start_time).total_seconds()
    if duration_sec > point_count * max_duration_sec_per_point:
        return None

    mean_lon = sum(point.lon for point in sorted_points) / point_count
    mean_lat = sum(point.lat for point in sorted_points) / point_count
    accuracies = [point.accuracy for point in sorted_points if point.accuracy is not None]
    mean_accuracy = sum(accuracies) / len(accuracies) if accuracies else None
    source_days = ",".join(sorted({point.source_date or start_time.strftime("%Y%m%d") for point in sorted_points}))

    return StayPoint(
        user_id=user_id,
        start_time=start_time,
        end_time=end_time,
        lon=mean_lon,
        lat=mean_lat,
        duration_sec=duration_sec,
        point_count=point_count,
        mean_accuracy=mean_accuracy,
        source_days=source_days,
    )


def _dbscan_labels(points_xy: Sequence[tuple[float, float]], eps_m: float, min_points: int) -> list[int]:
    labels = [-99] * len(points_xy)
    cluster_id = 0

    def region_query(index: int) -> list[int]:
        x1, y1 = points_xy[index]
        neighbours: list[int] = []
        for other_index, (x2, y2) in enumerate(points_xy):
            if sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2) <= eps_m:
                neighbours.append(other_index)
        return neighbours

    for index in range(len(points_xy)):
        if labels[index] != -99:
            continue

        neighbours = region_query(index)
        if len(neighbours) < min_points:
            labels[index] = -1
            continue

        labels[index] = cluster_id
        seeds = [n for n in neighbours if n != index]
        seed_pos = 0
        while seed_pos < len(seeds):
            seed = seeds[seed_pos]
            if labels[seed] == -1:
                labels[seed] = cluster_id
            if labels[seed] != -99:
                seed_pos += 1
                continue

            labels[seed] = cluster_id
            seed_neighbours = region_query(seed)
            if len(seed_neighbours) >= min_points:
                known_seeds = set(seeds)
                for neighbour in seed_neighbours:
                    if neighbour not in known_seeds:
                        seeds.append(neighbour)
                        known_seeds.add(neighbour)
            seed_pos += 1

        cluster_id += 1

    return labels


def detect_stays_for_user_day(
    user_id: str,
    points: Sequence[TrackPoint],
    origin_lon: float,
    origin_lat: float,
    sequence_max_gap_sec: int = 120,
    stay_eps_m: float = 50,
    stay_min_points: int = 3,
    max_duration_sec_per_point: int = 120,
    merge_overlap_gap_sec: int = 0,
) -> list[StayPoint]:
    sequences = split_temporally_continuous(points, sequence_max_gap_sec)
    stays: list[StayPoint] = []

    for sequence in sequences:
        if len(sequence) < stay_min_points:
            continue

        xy = [project_xy_m(point.lon, point.lat, origin_lon, origin_lat) for point in sequence]
        labels = _dbscan_labels(xy, stay_eps_m, stay_min_points)
        cluster_ids = sorted({label for label in labels if label >= 0})

        for cluster_id in cluster_ids:
            cluster_points = [point for point, label in zip(sequence, labels) if label == cluster_id]
            temporal_cluster_parts = split_temporally_continuous(cluster_points, sequence_max_gap_sec)
            for temporal_cluster_points in temporal_cluster_parts:
                stay = _build_stay_from_points(
                    user_id=user_id,
                    cluster_points=temporal_cluster_points,
                    stay_min_points=stay_min_points,
                    max_duration_sec_per_point=max_duration_sec_per_point,
                )
                if stay is not None:
                    stays.append(stay)

    return merge_overlapping_stays(stays, merge_overlap_gap_sec)


def merge_overlapping_stays(stays: Iterable[StayPoint], merge_gap_sec: int = 0) -> list[StayPoint]:
    sorted_stays = sorted(stays, key=lambda s: (s.start_time, s.end_time))
    if not sorted_stays:
        return []

    merged = [sorted_stays[0]]
    gap = timedelta(seconds=merge_gap_sec)

    for stay in sorted_stays[1:]:
        current = merged[-1]
        if stay.start_time <= current.end_time + gap:
            total_points = current.point_count + stay.point_count
            weighted_lon = (current.lon * current.point_count + stay.lon * stay.point_count) / total_points
            weighted_lat = (current.lat * current.point_count + stay.lat * stay.point_count) / total_points
            accuracies = [
                (current.mean_accuracy, current.point_count),
                (stay.mean_accuracy, stay.point_count),
            ]
            valid = [(value, weight) for value, weight in accuracies if value is not None]
            mean_accuracy = (
                sum(value * weight for value, weight in valid) / sum(weight for _, weight in valid)
                if valid
                else None
            )
            start_time = min(current.start_time, stay.start_time)
            end_time = max(current.end_time, stay.end_time)
            source_days = ",".join(sorted(set(current.source_days.split(",")) | set(stay.source_days.split(","))))
            merged[-1] = StayPoint(
                user_id=current.user_id,
                start_time=start_time,
                end_time=end_time,
                lon=weighted_lon,
                lat=weighted_lat,
                duration_sec=(end_time - start_time).total_seconds(),
                point_count=total_points,
                mean_accuracy=mean_accuracy,
                source_days=source_days,
            )
        else:
            merged.append(stay)

    return merged
