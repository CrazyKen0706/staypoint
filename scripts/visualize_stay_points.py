from __future__ import annotations

import argparse
import csv
import html
import json
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create an interactive Leaflet map for extracted stay points.")
    parser.add_argument("stay_csv", help="Stay point CSV from scripts/process_stay_points.py.")
    parser.add_argument("--config", default="config/oimachi_stay_processing.json", help="UTF-8 JSON config path.")
    parser.add_argument("--output-html", default=None, help="Output HTML path. Defaults next to the CSV.")
    parser.add_argument("--output-geojson", default=None, help="Output GeoJSON path. Defaults next to the CSV.")
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


def read_stays(path: Path, max_points: int | None) -> list[dict[str, Any]]:
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
            stays.append(row)
            if max_points is not None and len(stays) >= max_points:
                break
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
    stay_payload = []
    for stay in stays:
        popup = (
            f"<b>{html.escape(str(stay.get('stay_id', '')))}</b><br>"
            f"points: {stay['point_count']}<br>"
            f"duration: {stay['duration_sec']:.1f} sec<br>"
            f"user: {html.escape(str(stay.get('userid', ''))[:12])}...<br>"
            f"start: {html.escape(str(stay.get('start_time', '')))}<br>"
            f"end: {html.escape(str(stay.get('end_time', '')))}<br>"
            f"mean accuracy: {'' if stay.get('mean_accuracy') is None else f'{stay['mean_accuracy']:.1f} m'}"
        )
        stay_payload.append(
            {
                "lat": stay["lat"],
                "lon": stay["lon"],
                "point_count": stay["point_count"],
                "radius": point_radius(stay["point_count"]),
                "color": point_color(stay["point_count"]),
                "popup": popup,
            }
        )

    payload_json = json.dumps(stay_payload, ensure_ascii=False)
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
  </style>
</head>
<body>
  <div id="map"></div>
  <div class="panel">
    <b>{html.escape(site["station_name"])}</b><br>
    Stay points: {len(stays)}<br>
    Buffer: {radius_m:.0f} m<br>
    {geojson_link}
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
    for (const stay of stays) {{
      L.circleMarker([stay.lat, stay.lon], {{
        radius: stay.radius,
        color: '#333',
        weight: 0.6,
        fillColor: stay.color,
        fillOpacity: 0.72
      }}).bindPopup(stay.popup).addTo(map);
    }}
  </script>
</body>
</html>
"""


def main() -> int:
    args = parse_args()
    stay_csv = Path(args.stay_csv)
    config = load_config(args.config)
    stays = read_stays(stay_csv, args.max_points)

    output_html = Path(args.output_html) if args.output_html else stay_csv.with_suffix(".html")
    output_geojson = Path(args.output_geojson) if args.output_geojson else stay_csv.with_suffix(".geojson")
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
