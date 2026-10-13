#!/usr/bin/env python3
"""Baut aus OSM-Wegen (Map-API-XML) eine AutonomyRecoverySim-Route als GeoJSON-LineString.

Die Wege werden in der angegebenen Reihenfolge verkettet; jeder muss am Ende des
vorigen ansetzen (die Richtung wird bei Bedarf gedreht). Beispiel:

    python3 autonomy_recovery_sim/scripts/import_osm_route.py stadion.osm \
        autonomy_recovery_sim/data/hannover_robert_enke_strasse.geojson \
        --ways 27095856 1540868152 681074694 --name "Robert-Enke-Straße, Hannover"

Danach die Kontextkarte mit ``import_osm_context.py`` aus derselben XML-Datei erzeugen.
"""

from __future__ import annotations

import argparse
import json
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path


def way_coordinates(root: ET.Element, way_id: str) -> tuple[list[tuple[float, float]], dict[str, str]]:
    nodes = {
        node.attrib["id"]: (float(node.attrib["lon"]), float(node.attrib["lat"]))
        for node in root.findall("node")
    }
    for way in root.findall("way"):
        if way.attrib["id"] == way_id:
            tags = {tag.attrib["k"]: tag.attrib["v"] for tag in way.findall("tag")}
            return [nodes[nd.attrib["ref"]] for nd in way.findall("nd")], tags
    raise SystemExit(f"Way {way_id} steht nicht in der OSM-Datei")


def chain(pieces: list[list[tuple[float, float]]]) -> list[tuple[float, float]]:
    route = list(pieces[0])
    for index, piece in enumerate(pieces[1:], start=2):
        if piece[0] == route[-1]:
            route.extend(piece[1:])
        elif piece[-1] == route[-1]:
            route.extend(reversed(piece[:-1]))
        else:
            raise SystemExit(f"Way {index} setzt nicht am Ende des vorigen an; Reihenfolge pruefen")
    return route


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", type=Path, help="OSM-XML von der Map API")
    parser.add_argument("output", type=Path, help="Ziel-GeoJSON")
    parser.add_argument("--ways", nargs="+", required=True, help="OSM-Way-IDs in Fahrtrichtung")
    parser.add_argument("--name", required=True)
    parser.add_argument("--reverse", action="store_true", help="Fahrtrichtung umkehren")
    args = parser.parse_args()

    root = ET.parse(args.source).getroot()
    pieces, all_tags = [], []
    for way_id in args.ways:
        coordinates, tags = way_coordinates(root, way_id)
        pieces.append(coordinates)
        all_tags.append(tags)
    route = chain(pieces)
    if args.reverse:
        route.reverse()
    first = all_tags[0]
    properties = {
        "name": args.name,
        "osm_way_id": int(args.ways[0]),
        "osm_way_ids": [int(way_id) for way_id in args.ways],
        "highway": first.get("highway", ""),
        "maxspeed": first.get("maxspeed", ""),
        "lanes": first.get("lanes", ""),
        "source": f"OpenStreetMap contributors, downloaded {date.today().isoformat()} via Map API",
        "source_url": f"https://www.openstreetmap.org/way/{args.ways[0]}",
        "license": "ODbL-1.0",
    }
    feature = {
        "type": "Feature",
        "properties": properties,
        "geometry": {"type": "LineString", "coordinates": [[lon, lat] for lon, lat in route]},
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps({"type": "FeatureCollection", "features": [feature]}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf8",
    )
    print(f"{len(route)} Punkte, {len(args.ways)} Wege -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
