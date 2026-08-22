from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from django.conf import settings

ASSET_DIR = Path(settings.BASE_DIR) / "core" / "assets" / "scoresheet"
TEMPLATE_DEFINITION_PATH = ASSET_DIR / "template_definition.json"


def _cell(
    field_id: str,
    x: float,
    y: float,
    width: float,
    height: float,
    editor: str,
    data_path: str,
) -> dict[str, Any]:
    return {
        "id": field_id,
        "rect": {"x": x, "y": y, "width": width, "height": height},
        "editor": editor,
        "data_path": data_path,
        "ink_style": "semantic_black",
    }


def _build_cells(definition: dict[str, Any]) -> list[dict[str, Any]]:
    """Expand compact layout rules into addressable, stable editor cells."""
    cells: list[dict[str, Any]] = []
    header_editors = {
        "date": "date",
        "scheduled_time": "time",
    }
    for key, rect in definition["header_fields"].items():
        data_path = (
            "/teams/0/name"
            if key == "team_a_name"
            else "/teams/1/name"
            if key == "team_b_name"
            else f"/header/{key}"
        )
        cells.append(
            _cell(
                f"header.{key}",
                rect["x"],
                rect["y"],
                rect["width"],
                rect["height"],
                header_editors.get(key, "text"),
                data_path,
            )
        )

    columns = definition["player_columns"]
    for team_index, side in enumerate(("A", "B")):
        layout = definition["team_layouts"][side]
        name_rect = layout["team_name"]
        cells.append(
            _cell(
                f"team.{side}.name",
                name_rect["x"],
                name_rect["y"],
                name_rect["width"],
                name_rect["height"],
                "text",
                f"/teams/{team_index}/name",
            )
        )
        for scope, timeout_layout in layout["timeouts"].items():
            for slot, (x1, y1, x2, y2) in enumerate(timeout_layout["cells"], start=1):
                cells.append(
                    _cell(
                        f"team.{side}.timeout.{scope}.{slot}",
                        x1,
                        y1,
                        x2 - x1,
                        y2 - y1,
                        "timeout",
                        f"/teams/{team_index}/timeouts/{scope}/{slot}",
                    )
                )
        for period_text, foul_layout in layout["team_fouls"].items():
            for slot, (x1, y1, x2, y2) in enumerate(foul_layout["cells"], start=1):
                cells.append(
                    _cell(
                        f"team.{side}.team_foul.{period_text}.{slot}",
                        x1,
                        y1,
                        x2 - x1,
                        y2 - y1,
                        "team_foul",
                        f"/teams/{team_index}/team_fouls/{period_text}/{slot}",
                    )
                )

        player_fields = (
            ("license", "license_number", "text"),
            ("name", "name", "text"),
            ("jersey", "jersey_number", "jersey"),
            ("participation", "participation", "participation"),
        )
        for row in range(1, 13):
            top = layout["player_rows"][row - 1]
            height = layout["player_rows"][row] - top
            prefix = f"team.{side}.player.{row:02d}"
            for field_name, property_name, editor in player_fields:
                x1, x2 = columns[field_name]
                cells.append(
                    _cell(
                        f"{prefix}.{field_name}",
                        x1,
                        top,
                        x2 - x1,
                        height,
                        editor,
                        f"/teams/{team_index}/players/{row - 1}/{property_name}",
                    )
                )
            for slot, (x1, x2) in enumerate(columns["fouls"], start=1):
                cells.append(
                    _cell(
                        f"{prefix}.foul.{slot}",
                        x1,
                        top,
                        x2 - x1,
                        height,
                        "foul",
                        f"/teams/{team_index}/players/{row - 1}/fouls/{slot}",
                    )
                )
            x1, x2 = columns["post_foul"]
            cells.append(
                _cell(
                    f"{prefix}.post_foul",
                    x1,
                    top,
                    x2 - x1,
                    height,
                    "post_foul",
                    f"/teams/{team_index}/players/{row - 1}/post_foul_markers",
                )
            )

        for role, bounds in layout["coach_rows"].items():
            cells.append(
                _cell(
                    f"team.{side}.{role}_coach",
                    columns["name"][0],
                    bounds[0],
                    columns["participation"][1] - columns["name"][0],
                    bounds[1] - bounds[0],
                    "text",
                    f"/teams/{team_index}/{role}_coach",
                )
            )
        for slot, (x1, x2) in enumerate(columns["coach_fouls"], start=1):
            bounds = layout["coach_rows"]["head"]
            cells.append(
                _cell(
                    f"team.{side}.coach_foul.{slot}",
                    x1,
                    bounds[0],
                    x2 - x1,
                    bounds[1] - bounds[0],
                    "foul",
                    f"/teams/{team_index}/coach_fouls/{slot}",
                )
            )
            assistant_bounds = layout["coach_rows"]["assistant"]
            cells.append(
                _cell(
                    f"team.{side}.assistant_coach_foul.{slot}",
                    x1,
                    assistant_bounds[0],
                    x2 - x1,
                    assistant_bounds[1] - assistant_bounds[0],
                    "assistant_coach_foul",
                    f"/teams/{team_index}/assistant_coach_fouls/{slot}",
                )
            )
        bounds = layout["coach_rows"]["head"]
        x1, x2 = columns["post_foul"]
        cells.append(
            _cell(
                f"team.{side}.coach_post_foul",
                x1,
                bounds[0],
                x2 - x1,
                bounds[1] - bounds[0],
                "post_foul",
                f"/teams/{team_index}/coach_post_foul_markers",
            )
        )
        assistant_bounds = layout["coach_rows"]["assistant"]
        cells.append(
            _cell(
                f"team.{side}.assistant_coach_post_foul",
                x1,
                assistant_bounds[0],
                x2 - x1,
                assistant_bounds[1] - assistant_bounds[0],
                "post_foul",
                f"/teams/{team_index}/assistant_coach_post_foul_markers",
            )
        )

    running = definition["running_score"]
    for score in range(1, 161):
        group = (score - 1) // 40
        row = (score - 1) % 40
        top = running["row_boundaries"][row]
        height = running["row_boundaries"][row + 1] - top
        group_x = running["group_boundaries"][group]
        for side, x in (("A", group_x), ("B", group_x + 28.2)):
            cells.append(
                _cell(
                    f"score.{side}.{score:03d}",
                    x,
                    top,
                    28.2,
                    height,
                    "score_event",
                    f"/score_events?team={side}&cumulative_score={score}",
                )
            )

    summary = definition["summary_fields"]
    for period, baseline in enumerate(summary["period_baselines"], start=1):
        for side, x in (("A", summary["period_a_x"]), ("B", summary["period_b_x"])):
            cells.append(
                _cell(
                    f"summary.period.{period}.{side}",
                    x - 15.0,
                    baseline - 10.0,
                    30.0,
                    13.2,
                    "period_score",
                    f"/stated_period_scores/{period - 1}/team_{side.lower()}",
                )
            )
    for side in ("A", "B"):
        rect = summary[f"final_{side.lower()}"]
        cells.append(
            _cell(
                f"summary.final.{side}",
                rect["x"] - 20.0,
                rect["baseline"] - 11.0,
                40.0,
                14.0,
                "final_score",
                f"/final_score/team_{side.lower()}",
            )
        )
    for key, editor in (("winner", "winner"), ("ended_at", "time")):
        rect = summary[key]
        cells.append(
            _cell(
                f"summary.{key}",
                rect["x"],
                rect["baseline"] - 10.0,
                rect["width"],
                13.2,
                editor,
                f"/final_score/{'winner_name' if key == 'winner' else key}",
            )
        )

    for role, rect in definition["official_fields"].items():
        cells.append(
            _cell(
                f"official.{role}.name",
                rect["x"],
                rect["baseline"] - 10.0,
                rect["width"],
                13.2,
                "official",
                f"/officials/{role}",
            )
        )
    return cells


@lru_cache(maxsize=1)
def load_template_definition() -> dict[str, Any]:
    definition = json.loads(TEMPLATE_DEFINITION_PATH.read_text(encoding="utf-8"))
    definition["coordinate_system"] = "pdf_points_top_left"
    definition["ink_styles"] = {
        "semantic_black": {"default": "#11110f", "q1_q3": "#11110f", "q2_q4_ot": "#11110f"},
        "fiba_red_blue_future": {"q1_q3": "#b42318", "q2_q4_ot": "#175cd3"},
    }
    definition["cells"] = _build_cells(definition)
    return definition
