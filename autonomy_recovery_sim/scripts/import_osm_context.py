#!/usr/bin/env python3
"""Konvertiert einen kleinen OSM-XML-Ausschnitt in die AutonomyRecoverySim-Kontextkarte."""

from __future__ import annotations

import argparse
import json
import math
import re
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path


NUMBER_WITH_UNIT = re.compile(r"^\s*([0-9]+(?:[.,][0-9]+)?)\s*(?:m|meter|metres?)?\s*$", re.IGNORECASE)
AREA_KEYS = ("landuse", "leisure", "natural", "tourism")


def osm_number(value: str | None) -> float | None:
    """Eine einzelne metrische OSM-Zahl lesen; Listen/unklare Werte bleiben unbekannt."""
    if value is None:
        return None
    match = NUMBER_WITH_UNIT.match(value)
    if match is None:
        return None
    return float(match.group(1).replace(",", "."))


def estimated_building_height(kind: str) -> float:
    if kind in {"roof", "garage", "garages", "shed", "hut", "service"}:
        return 3.0
    if kind in {"apartments", "dormitory"}:
        return 12.0
    if kind in {"university", "library", "office"}:
        return 10.5
    if kind in {"house", "detached", "residential"}:
        return 8.5
    return 9.0


def building_properties(tags: dict[str, str]) -> dict[str, object]:
    kind = tags.get("building:part", tags.get("building", "yes"))
    tagged_height = osm_number(tags.get("height"))
    levels = osm_number(tags.get("building:levels"))
    roof_height = osm_number(tags.get("roof:height"))
    roof_levels = osm_number(tags.get("roof:levels"))
    min_height = osm_number(tags.get("min_height"))
    min_level = osm_number(tags.get("building:min_level"))

    if tagged_height is not None:
        height = tagged_height
        height_source = "osm:height"
    elif levels is not None and levels > 0:
        inferred_roof = roof_height if roof_height is not None else (roof_levels or 0.0) * 1.5
        height = levels * 3.0 + inferred_roof
        height_source = "osm:building:levels"
    else:
        height = estimated_building_height(kind)
        height_source = "estimated:type"

    base_height = min_height if min_height is not None else (min_level or 0.0) * 3.0
    properties: dict[str, object] = {
        "kind": kind,
        "is_part": "building:part" in tags,
        "height_m": round(height, 2),
        "min_height_m": round(base_height, 2),
        "height_source": height_source,
    }
    if levels is not None:
        properties["levels"] = levels
    if roof_height is not None:
        properties["roof_height_m"] = roof_height
    if tags.get("roof:shape"):
        properties["roof_shape"] = tags["roof:shape"]
    return properties


def project(lon: float, lat: float, lon0: float, lat0: float, lat_ref: float) -> list[float]:
    return [
        round((lon - lon0) * 111_320.0 * math.cos(lat_ref), 2),
        round((lat - lat0) * 110_540.0, 2),
    ]


def area_properties(tags: dict[str, str]) -> dict[str, str]:
    key = next(key for key in AREA_KEYS if key in tags)
    properties = {"kind": tags[key], "category": key}
    if tags.get("name"):
        properties["name"] = tags["name"]
    return properties


def outer_rings(relation: ET.Element, way_nodes: dict[str, list[str]]) -> list[list[str]]:
    """Verkettet die aeusseren Ways einer einfachen OSM-Multipolygon-Relation."""
    pieces = [
        list(way_nodes[member.attrib["ref"]])
        for member in relation.findall("member")
        if member.attrib.get("type") == "way"
        and member.attrib.get("role") == "outer"
        and member.attrib.get("ref") in way_nodes
    ]
    rings: list[list[str]] = []
    while pieces:
        ring = pieces.pop(0)
        while ring[0] != ring[-1]:
            for index, piece in enumerate(pieces):
                if piece[0] == ring[-1]:
                    ring.extend(piece[1:])
                    pieces.pop(index)
                    break
                if piece[-1] == ring[-1]:
                    ring.extend(reversed(piece[:-1]))
                    pieces.pop(index)
                    break
            else:
                break
        if len(ring) >= 4 and ring[0] == ring[-1]:
            rings.append(ring)
    return rings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="OSM-XML von der Map API")
    parser.add_argument("route", type=Path, help="GeoJSON der simulierten Route")
    parser.add_argument("output", type=Path, help="Ziel-JSON")
    args = parser.parse_args()

    route = json.loads(args.route.read_text(encoding="utf8"))
    route_feature = route["features"][0]
    lon0, lat0 = route_feature["geometry"]["coordinates"][0]

    root = ET.parse(args.source).getroot()
    bounds = root.find("bounds")
    if bounds is None:
        raise ValueError("OSM-Datei enthaelt keine bounds")
    min_lon = float(bounds.attrib["minlon"])
    min_lat = float(bounds.attrib["minlat"])
    max_lon = float(bounds.attrib["maxlon"])
    max_lat = float(bounds.attrib["maxlat"])
    lat_ref = math.radians((min_lat + max_lat) / 2.0)

    nodes = {
        node.attrib["id"]: (float(node.attrib["lon"]), float(node.attrib["lat"]))
        for node in root.findall("node")
    }
    way_nodes = {
        way.attrib["id"]: [nd.attrib["ref"] for nd in way.findall("nd")]
        for way in root.findall("way")
    }
    result: dict[str, object] = {
        "source": "OpenStreetMap contributors",
        "source_url": "https://www.openstreetmap.org/copyright",
        "license": "ODbL-1.0",
        "downloaded": date.today().isoformat(),
        "bounds_xy": [
            project(min_lon, min_lat, lon0, lat0, lat_ref),
            project(max_lon, max_lat, lon0, lat0, lat_ref),
        ],
        "roads": [],
        "buildings": [],
        "areas": [],
        "waterways": [],
        "railways": [],
    }

    for way in root.findall("way"):
        tags = {tag.attrib["k"]: tag.attrib["v"] for tag in way.findall("tag")}
        coordinates = [nodes[nd.attrib["ref"]] for nd in way.findall("nd") if nd.attrib["ref"] in nodes]
        if len(coordinates) < 2:
            continue
        points = [project(lon, lat, lon0, lat0, lat_ref) for lon, lat in coordinates]
        common = {"id": int(way.attrib["id"]), "points_xy": points}

        if ("building" in tags or "building:part" in tags) and len(points) >= 4:
            result["buildings"].append({**common, **building_properties(tags)})  # type: ignore[union-attr]
        elif "highway" in tags:
            result["roads"].append({  # type: ignore[union-attr]
                **common,
                "kind": tags["highway"],
                "name": tags.get("name", ""),
            })
        elif "waterway" in tags:
            result["waterways"].append({**common, "kind": tags["waterway"]})  # type: ignore[union-attr]
        elif "railway" in tags:
            result["railways"].append({**common, "kind": tags["railway"]})  # type: ignore[union-attr]
        elif len(points) >= 4 and points[0] == points[-1] and any(key in tags for key in AREA_KEYS):
            result["areas"].append({**common, **area_properties(tags)})  # type: ignore[union-attr]

    # Bedeutende Flaechen wie Zoos sind in OSM oft als Multipolygon aus mehreren
    # Ways modelliert. Innere Ringe werden fuer die kompakte Kulissenkarte bewusst
    # nicht ausgeschnitten; fuer Navigation und Kollisionen bleibt die Route massgeblich.
    for relation in root.findall("relation"):
        tags = {tag.attrib["k"]: tag.attrib["v"] for tag in relation.findall("tag")}
        if tags.get("type") != "multipolygon" or not any(key in tags for key in AREA_KEYS):
            continue
        for ring_index, refs in enumerate(outer_rings(relation, way_nodes)):
            coordinates = [nodes[ref] for ref in refs if ref in nodes]
            if len(coordinates) != len(refs):
                continue
            result["areas"].append({  # type: ignore[union-attr]
                "id": int(relation.attrib["id"]),
                "ring_index": ring_index,
                "points_xy": [project(lon, lat, lon0, lat0, lat_ref) for lon, lat in coordinates],
                **area_properties(tags),
            })

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":")), encoding="utf8")
    counts = {key: len(result[key]) for key in ("roads", "buildings", "areas", "waterways", "railways")}
    print(json.dumps(counts, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
