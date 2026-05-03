from __future__ import annotations

import argparse
import csv
import html
import json
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create an interactive Leaflet map for extracted stay points.")
    parser.add_argument("stay_csv", nargs="+", help="One or more stay point CSV files from scripts/process_stay_points.py.")
    parser.add_argument("--config", default="config/oimachi_stay_processing.json", help="UTF-8 JSON config path.")
    parser.add_argument("--comparison-csv", nargs="*", default=None, help="Optional aggregated/comparison stay CSV files.")
    parser.add_argument("--base-label", default="Before aggregation", help="Layer label for positional stay_csv inputs.")
    parser.add_argument("--comparison-label", default="After aggregation", help="Layer label for --comparison-csv inputs.")
    parser.add_argument("--output-html", default=None, help="Output HTML path. Defaults next to the first CSV.")
    parser.add_argument("--output-geojson", default=None, help="Output GeoJSON path. Defaults next to the first CSV.")
    parser.add_argument("--max-points", type=int, default=None, help="Optional cap for quick map previews.")
    return parser.parse_args()


def load_config(path: str) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


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


def read_stays(path: Path, max_points: int | None, dataset: str) -> list[dict[str, Any]]:
    stays: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            lon = as_float(row.get("lon"))
            lat = as_float(row.get("lat"))
            if lon is None or lat is None:
                continue
            row["lon"] = lon
            row["lat"] = lat
            row["point_count"] = as_int(row.get("point_count")) or 0
            row["duration_sec"] = as_float(row.get("duration_sec")) or 0.0
            row["mean_accuracy"] = as_float(row.get("mean_accuracy"))
            row["source_file"] = path.name
            row["dataset"] = dataset
            stays.append(row)
            if max_points is not None and len(stays) >= max_points:
                break
    return stays


def read_many_stays(paths: list[Path], max_points: int | None, dataset: str) -> list[dict[str, Any]]:
    stays: list[dict[str, Any]] = []
    for path in paths:
        remaining = None if max_points is None else max_points - len(stays)
        if remaining is not None and remaining <= 0:
            break
        stays.extend(read_stays(path, remaining, dataset))
    return stays


def point_color(point_count: int) -> str:
    if point_count >= 20:
        return "#b10026"
    if point_count >= 10:
        return "#e31a1c"
    if point_count >= 6:
        return "#fd8d3c"
    if point_count >= 4:
        return "#feb24c"
    return "#2b8cbe"


def point_radius(point_count: int) -> int:
    return max(4, min(18, 3 + point_count))


def to_geojson(stays: list[dict[str, Any]]) -> dict[str, Any]:
    features = []
    for stay in stays:
        properties = {
            key: value
            for key, value in stay.items()
            if key not in {"lon", "lat"}
        }
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [stay["lon"], stay["lat"]]},
                "properties": properties,
            }
        )
    return {"type": "FeatureCollection", "features": features}


def render_html(stays: list[dict[str, Any]], config: dict[str, Any], geojson_name: str | None) -> str:
    site = config["site"]
    center_lat = float(site["center_lat"])
    center_lon = float(site["center_lon"])
    radius_m = float(site["radius_m"])
    dates = sorted({str(stay.get("date", "")) for stay in stays if stay.get("date")})
    datasets = list(dict.fromkeys(str(stay.get("dataset", "Stay points")) for stay in stays))
    stay_payload = []
    for stay in stays:
        mean_accuracy = stay.get("mean_accuracy")
        mean_accuracy_text = "" if mean_accuracy is None else f"{mean_accuracy:.1f} m"
        popup = (
            f"<b>{html.escape(str(stay.get('stay_id', '')))}</b><br>"
            f"date: {html.escape(str(stay.get('date', '')))}<br>"
            f"points: {stay['point_count']}<br>"
            f"duration: {stay['duration_sec']:.1f} sec<br>"
            f"user: {html.escape(str(stay.get('userid', ''))[:12])}...<br>"
            f"start: {html.escape(str(stay.get('start_time', '')))}<br>"
            f"end: {html.escape(str(stay.get('end_time', '')))}<br>"
            f"mean accuracy: {mean_accuracy_text}"
        )
        stay_payload.append(
            {
                "date": str(stay.get("date", "")),
                "dataset": str(stay.get("dataset", "Stay points")),
                "lat": stay["lat"],
                "lon": stay["lon"],
                "point_count": stay["point_count"],
                "radius": point_radius(stay["point_count"]),
                "color": point_color(stay["point_count"]),
                "popup": popup,
            }
        )

    payload_json = json.dumps(stay_payload, ensure_ascii=False)
    dates_json = json.dumps(dates, ensure_ascii=False)
    datasets_json = json.dumps(datasets, ensure_ascii=False)
    title = f"{site['name']} stay points"
    geojson_link = f'<a href="{html.escape(geojson_name)}">GeoJSON</a>' if geojson_name else ""

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
  <style>
    html, body, #map {{ height: 100%; margin: 0; }}
    .panel {{
      position: absolute;
      z-index: 500;
      top: 12px;
      right: 12px;
      background: white;
      border: 1px solid #bbb;
      border-radius: 6px;
      padding: 10px 12px;
      font: 13px/1.4 Arial, sans-serif;
      box-shadow: 0 1px 8px rgba(0,0,0,0.2);
      max-width: 280px;
    }}
    .legend span {{
      display: inline-block;
      width: 10px;
      height: 10px;
      border-radius: 50%;
      margin-right: 6px;
    }}
    .date-controls {{
      margin-top: 8px;
      display: grid;
      gap: 4px;
    }}
    .filter-controls {{
      margin-top: 8px;
      display: grid;
      gap: 4px;
    }}
    .filter-controls label {{
      display: flex;
      align-items: center;
      gap: 6px;
      white-space: nowrap;
    }}
    .panel button {{
      margin: 6px 6px 0 0;
      padding: 3px 7px;
      border: 1px solid #aaa;
      border-radius: 4px;
      background: #f7f7f7;
      cursor: pointer;
    }}
  </style>
</head>
<body>
  <div id="map"></div>
  <div class="panel">
    <b>{html.escape(site["station_name"])}</b><br>
    Stay points: {len(stays)}<br>
    Buffer: {radius_m:.0f} m<br>
    Visible: <span id="visible-count">{len(stays)}</span><br>
    {geojson_link}
    <hr>
    <b>Date filter</b>
    <div>
      <button type="button" id="select-all">All</button>
      <button type="button" id="select-none">None</button>
    </div>
    <div id="date-controls" class="filter-controls"></div>
    <hr>
    <b>Layer filter</b>
    <div id="dataset-controls" class="filter-controls"></div>
    <hr>
    <div class="legend">
      <div><span style="background:#2b8cbe"></span>3 points</div>
      <div><span style="background:#feb24c"></span>4-5 points</div>
      <div><span style="background:#fd8d3c"></span>6-9 points</div>
      <div><span style="background:#e31a1c"></span>10-19 points</div>
      <div><span style="background:#b10026"></span>20+ points</div>
    </div>
  </div>
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <script>
    const stays = {payload_json};
    const dates = {dates_json};
    const datasets = {datasets_json};
    const map = L.map('map').setView([{center_lat}, {center_lon}], 15);
    L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
      maxZoom: 19,
      attribution: '&copy; OpenStreetMap contributors'
    }}).addTo(map);
    L.circle([{center_lat}, {center_lon}], {{
      radius: {radius_m},
      color: '#333',
      weight: 1,
      fillOpacity: 0
    }}).addTo(map);

    const markerLayer = L.layerGroup().addTo(map);
    const markers = stays.map((stay) => {{
      const isComparison = stay.dataset === '{html.escape(datasets[-1] if datasets else "")}';
      const marker = L.circleMarker([stay.lat, stay.lon], {{
          radius: isComparison ? stay.radius + 1 : stay.radius,
          color: isComparison ? '#176b35' : '#333',
          weight: isComparison ? 1.4 : 0.6,
          fillColor: stay.color,
          fillOpacity: isComparison ? 0.58 : 0.34
        }}).bindPopup(stay.popup);
      return {{ date: stay.date, dataset: stay.dataset, marker }};
    }});

    const dateControls = document.getElementById('date-controls');
    const datasetControls = document.getElementById('dataset-controls');
    for (const date of dates) {{
      const label = document.createElement('label');
      const input = document.createElement('input');
      input.type = 'checkbox';
      input.value = date;
      input.checked = true;
      input.addEventListener('change', renderMarkers);
      label.appendChild(input);
      label.appendChild(document.createTextNode(date));
      dateControls.appendChild(label);
    }}
    for (const dataset of datasets) {{
      const label = document.createElement('label');
      const input = document.createElement('input');
      input.type = 'checkbox';
      input.value = dataset;
      input.checked = true;
      input.addEventListener('change', renderMarkers);
      label.appendChild(input);
      label.appendChild(document.createTextNode(dataset));
      datasetControls.appendChild(label);
    }}

    function selectedDates() {{
      return new Set(Array.from(dateControls.querySelectorAll('input:checked')).map((input) => input.value));
    }}

    function selectedDatasets() {{
      return new Set(Array.from(datasetControls.querySelectorAll('input:checked')).map((input) => input.value));
    }}

    function renderMarkers() {{
      const selected = selectedDates();
      const selectedLayers = selectedDatasets();
      markerLayer.clearLayers();
      let visible = 0;
      for (const item of markers) {{
        if (selected.has(item.date) && selectedLayers.has(item.dataset)) {{
          item.marker.addTo(markerLayer);
          visible += 1;
        }}
      }}
      document.getElementById('visible-count').textContent = visible;
    }}

    document.getElementById('select-all').addEventListener('click', () => {{
      dateControls.querySelectorAll('input').forEach((input) => input.checked = true);
      datasetControls.querySelectorAll('input').forEach((input) => input.checked = true);
      renderMarkers();
    }});

    document.getElementById('select-none').addEventListener('click', () => {{
      dateControls.querySelectorAll('input').forEach((input) => input.checked = false);
      datasetControls.querySelectorAll('input').forEach((input) => input.checked = false);
      renderMarkers();
    }});

    renderMarkers();
  </script>
</body>
</html>
"""


def main() -> int:
    args = parse_args()
    stay_csvs = [Path(path) for path in args.stay_csv]
    config = load_config(args.config)
    stays = read_many_stays(stay_csvs, args.max_points, args.base_label)
    if args.comparison_csv:
        remaining = None if args.max_points is None else max(args.max_points - len(stays), 0)
        stays.extend(read_many_stays([Path(path) for path in args.comparison_csv], remaining, args.comparison_label))

    output_html = Path(args.output_html) if args.output_html else stay_csvs[0].with_suffix(".html")
    output_geojson = Path(args.output_geojson) if args.output_geojson else stay_csvs[0].with_suffix(".geojson")
    output_html.parent.mkdir(parents=True, exist_ok=True)
    output_geojson.parent.mkdir(parents=True, exist_ok=True)

    geojson = to_geojson(stays)
    output_geojson.write_text(json.dumps(geojson, ensure_ascii=False), encoding="utf-8")
    html_text = render_html(stays, config, output_geojson.name)
    output_html.write_text(html_text, encoding="utf-8")

    print(f"Wrote map: {output_html}")
    print(f"Wrote GeoJSON: {output_geojson}")
    print(f"Mapped stay points: {len(stays)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
