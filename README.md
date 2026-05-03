# GPSStay

This repository contains the first reproducible module for replicating the stay-count workflow in the Oimachi/Kichijoji sidewalk study.

## Module 1: Oimachi Stay Point Extraction

The processor reads Geo-People gzip point tracks for `20211101` through `20211107`, finds users who entered an 800 m buffer around Oimachi Station, excludes `isanonymous=True` points, and extracts stay points from ordinary GPS pings.

Stay detection is performed per `userid`, not across all users. For each user-day, the algorithm applies 50 m DBSCAN only inside temporally continuous GPS sequences, then splits each spatial cluster again by time continuity. A stay must contain at least 3 points and its duration must be within `point_count * 2 minutes`, so morning and evening visits to the same place by the same user are treated as separate stays.

Run a focused smoke test on the Oimachi standard mesh. This uses only `533935` for both visitor detection and trajectory processing:

```powershell
python scripts/process_stay_points.py --start-date 20211101 --end-date 20211101 --file-glob "geolocation-{date}-533935.csv.gz" --output-prefix smoke_oimachi_20211101_533935
```

Run the full configured week. By default, visitor detection uses the Oimachi mesh `533935`, then target-user trajectories are read from all daily gzip files:

```powershell
python scripts/process_stay_points.py
```

Outputs are written to `outputs/`:

- `stay_points_oimachi_20211101_20211107.csv`
- `stay_points_oimachi_20211101_20211107_qa_daily.csv`

Each stay point row includes `point_count`, the number of original GPS pings aggregated into that stay.

Create an interactive map and a GeoJSON file:

```powershell
python scripts/visualize_stay_points.py outputs\stay_points_oimachi_20211101_20211107.csv
```

For the smoke-test output:

```powershell
python scripts/visualize_stay_points.py outputs\smoke_oimachi_20211101_533935.csv
```

The map colors and marker sizes are based on `point_count`, and clicking a marker shows the stay ID, point count, time range, duration, user prefix, and mean accuracy.

Multiple stay CSVs can be combined into one map with date checkboxes:

```powershell
python scripts/visualize_stay_points.py outputs\smoke_oimachi_20211101_533935.csv outputs\stay_points_oimachi_20211102_20211107_mesh533935.csv --output-html outputs\stay_points_oimachi_20211101_20211107_mesh533935.html --output-geojson outputs\stay_points_oimachi_20211101_20211107_mesh533935.geojson
```

Post-aggregate existing staypoints with the same per-user spatial and temporal logic:

```powershell
python scripts/aggregate_stay_points.py outputs\smoke_oimachi_20211101_533935_timefix.csv outputs\stay_points_oimachi_20211102_20211107_mesh533935.csv --output-prefix outputs\aggregated_stay_points_oimachi_20211101_20211107_mesh533935
```

Create a comparison map with date filters and before/after aggregation layers:

```powershell
python scripts/visualize_stay_points.py outputs\smoke_oimachi_20211101_533935_timefix.csv outputs\stay_points_oimachi_20211102_20211107_mesh533935.csv --comparison-csv outputs\aggregated_stay_points_oimachi_20211101_20211107_mesh533935.csv --output-html outputs\stay_points_oimachi_20211101_20211107_mesh533935_comparison.html --output-geojson outputs\stay_points_oimachi_20211101_20211107_mesh533935_comparison.geojson
```

The config at `config/oimachi_stay_processing.json` stores paths, dates, Oimachi coordinates, and stay-detection parameters.
