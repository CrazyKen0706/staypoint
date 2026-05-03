from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from stay_detection import _dbscan_labels, project_xy_m
except ModuleNotFoundError:  # pragma: no cover - used when imported as scripts.aggregate_stay_points.
    from scripts.stay_detection import _dbscan_labels, project_xy_m


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Post-aggregate extracted stay points with time-aware DBSCAN.")
    parser.add_argument("stay_csv", nargs="+", help="One or more stay point CSV files.")
    parser.add_argument("--output-prefix", required=True, help="Output prefix, e.g. outputs/agg_oimachi_20211101_20211107.")
    parser.add_argument("--origin-lon", type=float, default=139.73494, help="Local projection origin longitude.")
    parser.add_argument("--origin-lat", type=float, default=35.60625, help="Local projection origin latitude.")
    parser.add_argument("--eps-m", type=float, default=150.0, help="DBSCAN Eps radius in meters.")
    parser.add_argument("--min-source-stays", type=int, default=2, help="DBSCAN MinPts for source staypoints.")
    parser.add_argument(
        "--time-window-sec",
        type=int,
        default=1800,
        help="Maximum gap between source staypoints before starting a new time window.",
    )
    return parser.parse_args()


def as_float(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def as_int(value: str | None) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except ValueError:
        return None


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value)


def read_stays(paths: list[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in paths:
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                lon = as_float(row.get("lon"))
                lat = as_float(row.get("lat"))
                point_count = as_int(row.get("point_count"))
                if lon is None or lat is None or point_count is None:
                    continue
                row["lon"] = lon
                row["lat"] = lat
                row["point_count"] = point_count
                row["duration_sec"] = as_float(row.get("duration_sec")) or 0.0
                row["mean_accuracy"] = as_float(row.get("mean_accuracy"))
                row["_start_dt"] = parse_time(str(row["start_time"]))
                row["_end_dt"] = parse_time(str(row["end_time"]))
                row["_source_file"] = path.name
                rows.append(row)
    return rows


def weighted_mean(rows: list[dict[str, Any]], key: str) -> float | None:
    valid = [(row[key], row["point_count"]) for row in rows if row.get(key) is not None and row.get(key) != ""]
    if not valid:
        return None
    total_weight = sum(weight for _, weight in valid)
    return sum(float(value) * weight for value, weight in valid) / total_weight


def split_by_time_window(stays: list[dict[str, Any]], time_window_sec: int) -> list[list[dict[str, Any]]]:
    if not stays:
        return []
    sorted_stays = sorted(stays, key=lambda row: (row["_start_dt"], row["_end_dt"]))
    windows: list[list[dict[str, Any]]] = []
    current = [sorted_stays[0]]
    for stay in sorted_stays[1:]:
        gap = (stay["_start_dt"] - current[-1]["_end_dt"]).total_seconds()
        if gap <= time_window_sec:
            current.append(stay)
        else:
            windows.append(current)
            current = [stay]
    windows.append(current)
    return windows


def emit_group(
    rows: list[dict[str, Any]],
    agg_id: str,
    min_source_stays: int,
    dbscan_label: int,
) -> dict[str, Any]:
    rows = sorted(rows, key=lambda row: (row["_start_dt"], row["_end_dt"]))
    total_points = sum(row["point_count"] for row in rows)
    start_time = min(row["_start_dt"] for row in rows)
    end_time = max(row["_end_dt"] for row in rows)
    duration_sec = (end_time - start_time).total_seconds()
    source_ids = [str(row.get("stay_id", "")) for row in rows]
    can_aggregate = len(rows) >= min_source_stays and dbscan_label >= 0
    action = "aggregated" if can_aggregate else "unchanged"

    return {
        "stay_id": agg_id,
        "userid": rows[0]["userid"],
        "date": rows[0]["date"],
        "start_time": start_time.isoformat(sep=" "),
        "end_time": end_time.isoformat(sep=" "),
        "duration_sec": round(duration_sec, 3),
        "point_count": total_points,
        "lon": round(weighted_mean(rows, "lon") or rows[0]["lon"], 8),
        "lat": round(weighted_mean(rows, "lat") or rows[0]["lat"], 8),
        "mean_accuracy": round(weighted_mean(rows, "mean_accuracy"), 3) if weighted_mean(rows, "mean_accuracy") is not None else "",
        "source_days": ",".join(sorted({str(row.get("source_days") or row["date"]) for row in rows})),
        "anonymous_removed_flag": rows[0].get("anonymous_removed_flag", ""),
        "source_stay_count": len(rows),
        "source_stay_ids": "|".join(source_ids),
        "aggregation_action": action,
        "dbscan_label": dbscan_label,
        "time_window_start": start_time.isoformat(sep=" "),
        "time_window_end": end_time.isoformat(sep=" "),
    }


def aggregate_stays(
    stays: list[dict[str, Any]],
    origin_lon: float,
    origin_lat: float,
    eps_m: float,
    min_source_stays: int,
    time_window_sec: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    by_user_date: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for stay in stays:
        by_user_date[(str(stay["userid"]), str(stay["date"]))].append(stay)

    output_rows: list[dict[str, Any]] = []
    decision_rows: list[dict[str, Any]] = []
    next_id = 1

    for (_, _), group_rows in sorted(by_user_date.items()):
        if len(group_rows) == 1:
            row = emit_group(group_rows, f"agg_{next_id:08d}", min_source_stays, -1)
            output_rows.append(row)
            decision_rows.append(row.copy())
            next_id += 1
            continue

        for time_window in split_by_time_window(group_rows, time_window_sec):
            if len(time_window) == 1:
                row = emit_group(time_window, f"agg_{next_id:08d}", min_source_stays, -1)
                output_rows.append(row)
                decision_rows.append(row.copy())
                next_id += 1
                continue

            xy = [project_xy_m(row["lon"], row["lat"], origin_lon, origin_lat) for row in time_window]
            labels = _dbscan_labels(xy, eps_m, min_source_stays)
            handled = set()
            for cluster_id in sorted({label for label in labels if label >= 0}):
                spatial_cluster = [row for row, label in zip(time_window, labels) if label == cluster_id]
                merged_row = emit_group(spatial_cluster, f"agg_{next_id:08d}", min_source_stays, cluster_id)
                output_rows.append(merged_row)
                decision_rows.append(merged_row.copy())
                next_id += 1
                handled.update(id(item) for item in spatial_cluster)

            for row_item, label in zip(time_window, labels):
                if label == -1 or id(row_item) not in handled:
                    row = emit_group([row_item], f"agg_{next_id:08d}", min_source_stays, -1)
                    output_rows.append(row)
                    decision_rows.append(row.copy())
                    next_id += 1

    return output_rows, decision_rows


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    args = parse_args()
    input_paths = [Path(path) for path in args.stay_csv]
    output_prefix = Path(args.output_prefix)
    stays = read_stays(input_paths)
    aggregated, decisions = aggregate_stays(
        stays=stays,
        origin_lon=args.origin_lon,
        origin_lat=args.origin_lat,
        eps_m=args.eps_m,
        min_source_stays=args.min_source_stays,
        time_window_sec=args.time_window_sec,
    )

    fields = [
        "stay_id",
        "userid",
        "date",
        "start_time",
        "end_time",
        "duration_sec",
        "point_count",
        "lon",
        "lat",
        "mean_accuracy",
        "source_days",
        "anonymous_removed_flag",
        "source_stay_count",
        "source_stay_ids",
        "aggregation_action",
        "dbscan_label",
        "time_window_start",
        "time_window_end",
    ]
    combined_csv = output_prefix.with_suffix(".csv")
    decisions_csv = output_prefix.with_name(output_prefix.name + "_aggregation_decisions").with_suffix(".csv")
    qa_csv = output_prefix.with_name(output_prefix.name + "_qa_by_date").with_suffix(".csv")
    write_csv(combined_csv, aggregated, fields)
    write_csv(decisions_csv, decisions, fields)

    qa_rows = []
    by_date = defaultdict(list)
    for row in aggregated:
        by_date[row["date"]].append(row)
    for date, rows in sorted(by_date.items()):
        action_counts = Counter(row["aggregation_action"] for row in rows)
        source_input_count = sum(int(row["source_stay_count"]) for row in rows)
        qa_rows.append(
            {
                "date": date,
                "input_staypoints": source_input_count,
                "output_staypoints": len(rows),
                "aggregated_output_staypoints": action_counts.get("aggregated", 0),
                "unchanged_output_staypoints": action_counts.get("unchanged", 0)
                + action_counts.get("unchanged_time_split", 0),
                "reduced_staypoints": source_input_count - len(rows),
            }
        )
        per_date_path = output_prefix.with_name(f"{output_prefix.name}_{date}").with_suffix(".csv")
        write_csv(per_date_path, rows, fields)

    write_csv(
        qa_csv,
        qa_rows,
        [
            "date",
            "input_staypoints",
            "output_staypoints",
            "aggregated_output_staypoints",
            "unchanged_output_staypoints",
            "reduced_staypoints",
        ],
    )

    print(f"Input staypoints: {len(stays)}")
    print(f"Output staypoints: {len(aggregated)}")
    print(f"Wrote aggregated CSV: {combined_csv}")
    print(f"Wrote decisions CSV: {decisions_csv}")
    print(f"Wrote QA CSV: {qa_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
