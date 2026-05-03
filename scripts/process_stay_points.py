from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from stay_detection import TrackPoint, detect_stays_for_user_day, distance_m


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract Oimachi stay points from Geo-People GPS pings.")
    parser.add_argument("--config", default="config/oimachi_stay_processing.json", help="UTF-8 JSON config path.")
    parser.add_argument("--start-date", default=None, help="Override config start date, YYYYMMDD.")
    parser.add_argument("--end-date", default=None, help="Override config end date, YYYYMMDD.")
    parser.add_argument("--file-glob", default=None, help="Override both visitor and trajectory gzip filename patterns.")
    parser.add_argument("--visitor-file-glob", default=None, help="Per-day gzip pattern for the 800 m visitor scan.")
    parser.add_argument("--trajectory-file-glob", default=None, help="Per-day gzip pattern for target users' daily tracks.")
    parser.add_argument("--max-files-per-day", type=int, default=None, help="Optional smoke-test file limit.")
    parser.add_argument("--output-prefix", default=None, help="Override output filename prefix.")
    parser.add_argument("--write-parquet", action="store_true", help="Also write Parquet if pandas has an engine.")
    return parser.parse_args()


def load_config(path: str) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def date_range(start: str, end: str) -> list[str]:
    current = datetime.strptime(start, "%Y%m%d")
    end_dt = datetime.strptime(end, "%Y%m%d")
    dates: list[str] = []
    while current <= end_dt:
        dates.append(current.strftime("%Y%m%d"))
        current += timedelta(days=1)
    return dates


def day_geo_files(geo_root: Path, date: str, file_glob: str, max_files: int | None) -> list[Path]:
    day_dir = geo_root / date
    pattern = file_glob.format(date=date)
    files = sorted(day_dir.rglob(pattern))
    if max_files is not None:
        files = files[:max_files]
    return files


def read_csv_chunks(file_path: Path, usecols: list[str], chunksize: int):
    return pd.read_csv(
        file_path,
        compression="gzip",
        usecols=usecols,
        chunksize=chunksize,
        dtype=str,
        encoding="utf-8",
        encoding_errors="replace",
        low_memory=False,
    )


def as_bool(value: str | None) -> bool:
    return str(value).strip().lower() == "true"


def as_float(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.strip())


def inside_radius(row: dict[str, str], columns: dict[str, str], lon0: float, lat0: float, radius_m: float) -> bool:
    lon = as_float(row.get(columns["lon"]))
    lat = as_float(row.get(columns["lat"]))
    if lon is None or lat is None:
        return False
    return distance_m(lon, lat, lon0, lat0) <= radius_m


def scan_visitors(
    files_by_date: dict[str, list[Path]],
    columns: dict[str, str],
    lon0: float,
    lat0: float,
    radius_m: float,
    chunksize: int,
) -> tuple[dict[str, set[str]], dict[str, Counter]]:
    visitors_by_date: dict[str, set[str]] = {}
    qa_by_date: dict[str, Counter] = {}

    for date, files in files_by_date.items():
        visitors: set[str] = set()
        qa: Counter = Counter(visitor_files=len(files))
        for file_path in files:
            usecols = [columns["user_id"], columns["lon"], columns["lat"]]
            for chunk in read_csv_chunks(file_path, usecols, chunksize):
                qa["raw_points"] += len(chunk)
                lon = pd.to_numeric(chunk[columns["lon"]], errors="coerce")
                lat = pd.to_numeric(chunk[columns["lat"]], errors="coerce")
                user_id = chunk[columns["user_id"]]
                valid = lon.notna() & lat.notna() & user_id.notna() & (user_id != "")
                qa["missing_user_id"] += int((~user_id.notna() | (user_id == "")).sum())
                if not valid.any():
                    continue

                meters_per_deg_lat = 111_320.0
                meters_per_deg_lon = meters_per_deg_lat * __import__("math").cos(__import__("math").radians(lat0))
                dx = (lon - lon0) * meters_per_deg_lon
                dy = (lat - lat0) * meters_per_deg_lat
                inside = valid & ((dx * dx + dy * dy) <= radius_m * radius_m)
                qa["points_inside_radius"] += int(inside.sum())
                if inside.any():
                    visitors.update(user_id[inside].astype(str).tolist())
        qa["visitor_users"] = len(visitors)
        visitors_by_date[date] = visitors
        qa_by_date[date] = qa
        print(f"[pass1] {date}: files={len(files)} raw={qa['raw_points']} visitors={len(visitors)}", flush=True)

    return visitors_by_date, qa_by_date


def collect_target_tracks(
    files: list[Path],
    date: str,
    target_users: set[str],
    columns: dict[str, str],
    chunksize: int,
) -> tuple[dict[str, list[TrackPoint]], Counter]:
    tracks: dict[str, list[TrackPoint]] = defaultdict(list)
    qa: Counter = Counter(trajectory_files=len(files), target_users=len(target_users))
    anonymous_users: set[str] = set()

    for file_path in files:
        usecols = [
            columns["user_id"],
            columns["timestamp"],
            columns["lon"],
            columns["lat"],
            columns["accuracy"],
            columns["anonymous"],
        ]
        for chunk in read_csv_chunks(file_path, usecols, chunksize):
            qa["raw_points_second_pass"] += len(chunk)
            target_mask = chunk[columns["user_id"]].isin(target_users)
            if not target_mask.any():
                continue

            target = chunk.loc[target_mask].copy()
            qa["target_user_points"] += len(target)
            anonymous_mask = target[columns["anonymous"]].astype(str).str.lower().eq("true")
            qa["anonymous_points_removed"] += int(anonymous_mask.sum())
            if anonymous_mask.any():
                anonymous_users.update(target.loc[anonymous_mask, columns["user_id"]].astype(str).tolist())

            target = target.loc[~anonymous_mask]
            if target.empty:
                continue

            target["_lon"] = pd.to_numeric(target[columns["lon"]], errors="coerce")
            target["_lat"] = pd.to_numeric(target[columns["lat"]], errors="coerce")
            target["_accuracy"] = pd.to_numeric(target[columns["accuracy"]], errors="coerce")
            target["_timestamp"] = pd.to_datetime(target[columns["timestamp"]], errors="coerce")

            invalid_coord = target["_lon"].isna() | target["_lat"].isna()
            missing_timestamp = target[columns["timestamp"]].isna() | target[columns["timestamp"]].eq("")
            invalid_timestamp = target["_timestamp"].isna() & ~missing_timestamp
            qa["invalid_coordinate_points"] += int(invalid_coord.sum())
            qa["missing_timestamp_points"] += int(missing_timestamp.sum())
            qa["invalid_timestamp_points"] += int(invalid_timestamp.sum())

            valid_accuracy = target.loc[target["_accuracy"].notna(), "_accuracy"]
            qa["accuracy_count"] += int(valid_accuracy.count())
            qa["accuracy_0_25m"] += int(((valid_accuracy >= 0) & (valid_accuracy < 25)).sum())
            qa["accuracy_25_50m"] += int(((valid_accuracy >= 25) & (valid_accuracy < 50)).sum())
            qa["accuracy_50_100m"] += int(((valid_accuracy >= 50) & (valid_accuracy < 100)).sum())
            qa["accuracy_100m_plus"] += int((valid_accuracy >= 100).sum())

            target = target.loc[~invalid_coord & ~target["_timestamp"].isna()]
            for row_dict in target.to_dict("records"):
                user_id = str(row_dict[columns["user_id"]])
                accuracy_value = row_dict["_accuracy"]
                tracks[user_id].append(
                    TrackPoint(
                        user_id=user_id,
                        timestamp=row_dict["_timestamp"].to_pydatetime(),
                        lon=float(row_dict["_lon"]),
                        lat=float(row_dict["_lat"]),
                        accuracy=None if pd.isna(accuracy_value) else float(accuracy_value),
                        source_date=date,
                    )
                )

    qa["target_users_with_nonanonymous_points"] = len(tracks)
    qa["anonymous_users_removed"] = len(anonymous_users)
    return tracks, qa


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def add_counter_values(target: Counter, source: Counter, skip_keys: set[str] | None = None) -> None:
    skip_keys = skip_keys or set()
    for key, value in source.items():
        if key in skip_keys:
            continue
        target[key] += value


def main() -> int:
    args = parse_args()
    config = load_config(args.config)
    site = config["site"]
    paths = config["paths"]
    columns = config["columns"]
    params = config["parameters"]

    start_date = args.start_date or config["date_range"]["start"]
    end_date = args.end_date or config["date_range"]["end"]
    dates = date_range(start_date, end_date)
    geo_root = Path(paths["geo_root"])
    output_dir = Path(paths["output_dir"])
    output_prefix = args.output_prefix or f"stay_points_{site['name']}_{dates[0]}_{dates[-1]}"

    visitor_file_glob = args.file_glob or args.visitor_file_glob or params.get("visitor_file_glob", "geolocation-{date}-*.csv.gz")
    trajectory_file_glob = args.file_glob or args.trajectory_file_glob or params.get(
        "trajectory_file_glob", "geolocation-{date}-*.csv.gz"
    )

    visitor_files_by_date = {
        date: day_geo_files(geo_root, date, visitor_file_glob, args.max_files_per_day)
        for date in dates
    }
    trajectory_files_by_date = {
        date: day_geo_files(geo_root, date, trajectory_file_glob, args.max_files_per_day)
        for date in dates
    }
    missing_dates = [date for date in dates if not visitor_files_by_date[date] or not trajectory_files_by_date[date]]
    if missing_dates:
        print(f"No gzip files found for dates: {', '.join(missing_dates)}", file=sys.stderr)
        return 2

    lon0 = float(site["center_lon"])
    lat0 = float(site["center_lat"])
    radius_m = float(site["radius_m"])

    chunksize = int(params.get("csv_chunksize", 200000))
    visitors_by_date, qa_by_date = scan_visitors(visitor_files_by_date, columns, lon0, lat0, radius_m, chunksize)

    stay_rows: list[dict[str, Any]] = []
    stay_id = 1

    for date in dates:
        target_users = visitors_by_date[date]
        if not target_users:
            qa_by_date[date].update(Counter(stay_points=0, stay_users=0))
            continue

        tracks, second_pass_qa = collect_target_tracks(trajectory_files_by_date[date], date, target_users, columns, chunksize)
        add_counter_values(qa_by_date[date], second_pass_qa)
        stay_users: set[str] = set()
        stay_count = 0

        for user_id, points in tracks.items():
            stays = detect_stays_for_user_day(
                user_id=user_id,
                points=points,
                origin_lon=lon0,
                origin_lat=lat0,
                sequence_max_gap_sec=int(params["sequence_max_gap_sec"]),
                stay_eps_m=float(params["stay_eps_m"]),
                stay_min_points=int(params["stay_min_points"]),
                max_duration_sec_per_point=int(params["max_duration_sec_per_point"]),
                merge_overlap_gap_sec=int(params["merge_overlap_gap_sec"]),
            )
            for stay in stays:
                if params.get("output_stays_within_radius_only", True):
                    if distance_m(stay.lon, stay.lat, lon0, lat0) > radius_m:
                        continue
                stay_rows.append(
                    {
                        "stay_id": f"{site['name']}_{stay_id:08d}",
                        "userid": stay.user_id,
                        "date": date,
                        "start_time": stay.start_time.isoformat(sep=" "),
                        "end_time": stay.end_time.isoformat(sep=" "),
                        "duration_sec": round(stay.duration_sec, 3),
                        "point_count": stay.point_count,
                        "lon": round(stay.lon, 8),
                        "lat": round(stay.lat, 8),
                        "mean_accuracy": round(stay.mean_accuracy, 3) if stay.mean_accuracy is not None else "",
                        "source_days": stay.source_days,
                        "anonymous_removed_flag": True,
                    }
                )
                stay_id += 1
                stay_count += 1
                stay_users.add(user_id)

        qa_by_date[date]["stay_points"] = stay_count
        qa_by_date[date]["stay_users"] = len(stay_users)
        print(
            f"[pass2] {date}: target_points={qa_by_date[date]['target_user_points']} "
            f"anon_removed={qa_by_date[date]['anonymous_points_removed']} stays={stay_count}",
            flush=True,
        )

    stay_fields = [
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
    ]
    stay_csv = output_dir / f"{output_prefix}.csv"
    write_csv(stay_csv, stay_rows, stay_fields)

    qa_rows: list[dict[str, Any]] = []
    qa_fields = [
        "date",
        "visitor_files",
        "trajectory_files",
        "raw_points",
        "points_inside_radius",
        "visitor_users",
        "raw_points_second_pass",
        "target_users",
        "target_user_points",
        "anonymous_points_removed",
        "anonymous_users_removed",
        "invalid_coordinate_points",
        "missing_timestamp_points",
        "invalid_timestamp_points",
        "target_users_with_nonanonymous_points",
        "accuracy_count",
        "accuracy_0_25m",
        "accuracy_25_50m",
        "accuracy_50_100m",
        "accuracy_100m_plus",
        "stay_points",
        "stay_users",
    ]
    for date in dates:
        qa = qa_by_date[date]
        qa_rows.append({"date": date, **{field: qa.get(field, 0) for field in qa_fields if field != "date"}})

    qa_csv = output_dir / f"{output_prefix}_qa_daily.csv"
    write_csv(qa_csv, qa_rows, qa_fields)

    if args.write_parquet:
        try:
            import pandas as pd

            pd.DataFrame(stay_rows).to_parquet(output_dir / f"{output_prefix}.parquet", index=False)
        except Exception as exc:  # pragma: no cover - depends on local optional engines.
            print(f"Parquet output skipped: {exc}", file=sys.stderr)

    print(f"Wrote {len(stay_rows)} stay rows: {stay_csv}")
    print(f"Wrote QA summary: {qa_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
