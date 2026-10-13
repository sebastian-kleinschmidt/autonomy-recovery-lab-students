from __future__ import annotations

import html
import math
from pathlib import Path

from .simulation import SimulationResult


def _points(values: list[tuple[float, float]], transform) -> str:
    return " ".join(f"{transform(x, y)[0]:.1f},{transform(x, y)[1]:.1f}" for x, y in values)


def write_svg(result: SimulationResult, path: Path) -> None:
    road = result.scenario.road
    samples = [road.to_xy(i * road.length_m / 180.0)[:2] for i in range(181)]
    ego_path = [road.to_xy(p["s_m"], p["d_m"])[:2] for p in result.trajectory]
    actor_points = [road.to_xy(a.s_m, a.d_m)[:2] for a in result.scenario.actors]
    all_points = samples + ego_path + actor_points
    min_x = min(p[0] for p in all_points) - 12
    max_x = max(p[0] for p in all_points) + 12
    min_y = min(p[1] for p in all_points) - 12
    max_y = max(p[1] for p in all_points) + 12
    width, height, pad = 980, 720, 50
    scale = min((width - 2 * pad) / max(max_x - min_x, 1), (height - 2 * pad) / max(max_y - min_y, 1))

    def t(x: float, y: float) -> tuple[float, float]:
        return pad + (x - min_x) * scale, height - pad - (y - min_y) * scale

    road_width = road.lane_width_m * 2 * scale
    dash = "stroke-dasharray='12 10'" if road.center_marking == "dashed" else ""
    actor_svg = []
    colors = {"blocker": "#E55D42", "oncoming": "#725AC1"}
    for actor, point in zip(result.scenario.actors, actor_points):
        x, y = t(*point)
        actor_svg.append(
            f"<circle cx='{x:.1f}' cy='{y:.1f}' r='7' fill='{colors.get(actor.actor_id, '#E55D42')}'/>"
            f"<text x='{x + 10:.1f}' y='{y - 8:.1f}'>{html.escape(actor.actor_id)}</text>"
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"""<svg xmlns='http://www.w3.org/2000/svg' width='{width}' height='{height}' viewBox='0 0 {width} {height}'>
<rect width='100%' height='100%' fill='#F3F0E8'/>
<style>text{{font:14px system-ui,sans-serif;fill:#17212B}} .small{{font-size:12px;fill:#53606D}}</style>
<polyline points='{_points(samples, t)}' fill='none' stroke='#B7BCC1' stroke-width='{road_width:.1f}' stroke-linecap='round' stroke-linejoin='round'/>
<polyline points='{_points(samples, t)}' fill='none' stroke='#FFFFFF' stroke-width='2' {dash}/>
<polyline points='{_points(ego_path, t)}' fill='none' stroke='#087E8B' stroke-width='4' stroke-linecap='round'/>
{''.join(actor_svg)}
<rect x='24' y='20' width='430' height='112' rx='10' fill='#FFFFFF' opacity='.94'/>
<text x='42' y='48' font-size='20' font-weight='700'>{html.escape(result.scenario_id)}</text>
<text x='42' y='75'>Befehl: {html.escape(result.command)} · korrekt: {'ja' if result.command_correct else 'nein'}</text>
<text x='42' y='98'>Befreit: {'ja' if result.liberated else 'nein'} · Kollision: {'ja' if result.collision else 'nein'}</text>
<text x='42' y='121' class='small'>Türkis: Ego-Trajektorie · Rot: Blockade · Violett: Gegenverkehr</text>
<text x='24' y='{height - 18}' class='small'>Kartendaten © OpenStreetMap-Mitwirkende · ODbL · {html.escape(road.source_url)}</text>
</svg>""",
        encoding="utf8",
    )
