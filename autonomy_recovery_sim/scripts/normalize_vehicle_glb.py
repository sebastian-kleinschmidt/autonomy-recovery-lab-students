#!/usr/bin/env python3
"""Skaliert ein einfaches Fahrzeug-GLB auf AutonomyRecoverySim-Meter und setzt den Ursprung."""

from __future__ import annotations

import argparse
import json
import math
import struct
from pathlib import Path


JSON_CHUNK = 0x4E4F534A
BIN_CHUNK = 0x004E4942
FLOAT = 5126


def load_glb(path: Path) -> tuple[dict[str, object], bytearray]:
    data = path.read_bytes()
    magic, version, declared_length = struct.unpack_from("<4sII", data)
    if magic != b"glTF" or version != 2 or declared_length != len(data):
        raise ValueError(f"{path}: kein gueltiges glTF-2.0-Binary")
    chunks: dict[int, bytes] = {}
    offset = 12
    while offset < len(data):
        length, kind = struct.unpack_from("<II", data, offset)
        offset += 8
        chunks[kind] = data[offset : offset + length]
        offset += length
    if JSON_CHUNK not in chunks or BIN_CHUNK not in chunks:
        raise ValueError(f"{path}: JSON- oder BIN-Chunk fehlt")
    gltf = json.loads(chunks[JSON_CHUNK].decode("utf8").rstrip("\x00 "))
    return gltf, bytearray(chunks[BIN_CHUNK])


def accessor_layout(gltf: dict[str, object], accessor_index: int) -> tuple[int, int, int]:
    accessor = gltf["accessors"][accessor_index]  # type: ignore[index]
    if accessor["componentType"] != FLOAT or accessor["type"] != "VEC3":  # type: ignore[index]
        raise ValueError("POSITION und NORMAL muessen als Float-VEC3 vorliegen")
    view = gltf["bufferViews"][accessor["bufferView"]]  # type: ignore[index]
    start = int(view.get("byteOffset", 0)) + int(accessor.get("byteOffset", 0))
    stride = int(view.get("byteStride", 12))
    return start, stride, int(accessor["count"])


def read_vec3(gltf: dict[str, object], binary: bytearray, accessor_index: int) -> list[tuple[float, float, float]]:
    start, stride, count = accessor_layout(gltf, accessor_index)
    return [struct.unpack_from("<fff", binary, start + index * stride) for index in range(count)]


def write_vec3(
    gltf: dict[str, object],
    binary: bytearray,
    accessor_index: int,
    values: list[tuple[float, float, float]],
) -> None:
    start, stride, count = accessor_layout(gltf, accessor_index)
    if len(values) != count:
        raise ValueError("Accessor-Laenge hat sich beim Skalieren geaendert")
    for index, value in enumerate(values):
        struct.pack_into("<fff", binary, start + index * stride, *value)


def normalized(value: tuple[float, float, float]) -> tuple[float, float, float]:
    length = math.sqrt(sum(component * component for component in value))
    if length < 1e-12:
        return 0.0, 1.0, 0.0
    return tuple(component / length for component in value)  # type: ignore[return-value]


def normalize_vehicle(
    gltf: dict[str, object],
    binary: bytearray,
    *,
    length_m: float,
    width_m: float,
    height_m: float,
) -> dict[str, float]:
    nodes = gltf.get("nodes", [])
    mesh_nodes = [(index, node) for index, node in enumerate(nodes) if "mesh" in node]
    if len(mesh_nodes) != 1:
        raise ValueError("Der Normalisierer erwartet genau einen Mesh-Node")
    _, node = mesh_nodes[0]
    if "matrix" in node or node.get("rotation", [0, 0, 0, 1]) != [0, 0, 0, 1] or node.get("scale", [1, 1, 1]) != [1, 1, 1]:
        raise ValueError("Rotation oder Skalierung am Mesh-Node muss vor dem Import angewendet werden")
    translation = tuple(float(value) for value in node.get("translation", [0, 0, 0]))
    mesh = gltf["meshes"][node["mesh"]]  # type: ignore[index]
    primitives = mesh.get("primitives", [])
    position_accessors = sorted({primitive["attributes"]["POSITION"] for primitive in primitives})
    normal_accessors = sorted(
        {primitive["attributes"]["NORMAL"] for primitive in primitives if "NORMAL" in primitive["attributes"]}
    )
    if not position_accessors:
        raise ValueError("Das Fahrzeug enthaelt keine Positionsdaten")

    positions_by_accessor = {
        index: [
            (x + translation[0], y + translation[1], z + translation[2])
            for x, y, z in read_vec3(gltf, binary, index)
        ]
        for index in position_accessors
    }
    positions = [position for values in positions_by_accessor.values() for position in values]
    mins = [min(position[axis] for position in positions) for axis in range(3)]
    maxs = [max(position[axis] for position in positions) for axis in range(3)]
    current = [maxs[axis] - mins[axis] for axis in range(3)]
    if min(current) <= 0:
        raise ValueError("Das Fahrzeug hat eine leere Raumachse")

    scales = [width_m / current[0], height_m / current[1], length_m / current[2]]
    centers = [(mins[0] + maxs[0]) / 2, mins[1], (mins[2] + maxs[2]) / 2]
    for accessor_index, values in positions_by_accessor.items():
        transformed = [
            tuple((value[axis] - centers[axis]) * scales[axis] for axis in range(3))
            for value in values
        ]
        write_vec3(gltf, binary, accessor_index, transformed)  # type: ignore[arg-type]
        accessor = gltf["accessors"][accessor_index]  # type: ignore[index]
        accessor["min"] = [min(value[axis] for value in transformed) for axis in range(3)]
        accessor["max"] = [max(value[axis] for value in transformed) for axis in range(3)]

    for accessor_index in normal_accessors:
        transformed_normals = [
            normalized((normal[0] / scales[0], normal[1] / scales[1], normal[2] / scales[2]))
            for normal in read_vec3(gltf, binary, accessor_index)
        ]
        write_vec3(gltf, binary, accessor_index, transformed_normals)
        accessor = gltf["accessors"][accessor_index]  # type: ignore[index]
        accessor["min"] = [min(value[axis] for value in transformed_normals) for axis in range(3)]
        accessor["max"] = [max(value[axis] for value in transformed_normals) for axis in range(3)]

    node.pop("translation", None)
    node.pop("rotation", None)
    node.pop("scale", None)
    asset = gltf.setdefault("asset", {})
    extras = {
        key: value
        for key, value in asset.setdefault("extras", {}).items()
        if not key.endswith(("_dimensions_m", "_axes"))
    }
    asset["extras"] = extras
    extras["autonomy_recovery_sim_dimensions_m"] = {"length": length_m, "width": width_m, "height": height_m}
    extras["autonomy_recovery_sim_axes"] = "+X right, +Y up, +Z front"
    return {"length_m": length_m, "width_m": width_m, "height_m": height_m}


def write_glb(path: Path, gltf: dict[str, object], binary: bytearray) -> None:
    json_data = json.dumps(gltf, ensure_ascii=False, separators=(",", ":")).encode("utf8")
    json_data += b" " * (-len(json_data) % 4)
    binary += b"\x00" * (-len(binary) % 4)
    total = 12 + 8 + len(json_data) + 8 + len(binary)
    output = bytearray(struct.pack("<4sII", b"glTF", 2, total))
    output += struct.pack("<II", len(json_data), JSON_CHUNK) + json_data
    output += struct.pack("<II", len(binary), BIN_CHUNK) + binary
    path.write_bytes(output)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--length", type=float, default=4.71)
    parser.add_argument("--width", type=float, default=1.99)
    parser.add_argument("--height", type=float, default=1.94)
    args = parser.parse_args()
    gltf, binary = load_glb(args.input)
    dimensions = normalize_vehicle(
        gltf,
        binary,
        length_m=args.length,
        width_m=args.width,
        height_m=args.height,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_glb(args.output, gltf, binary)
    print(json.dumps(dimensions, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
