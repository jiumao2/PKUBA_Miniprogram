from __future__ import annotations

import html
import io
import os
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from django.conf import settings
from pypdf import PdfReader, PdfWriter
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from .models import (
    FoulEntry,
    FoulMarkStyle,
    ParticipationStatus,
    PostFoulMarker,
    ScoreBoundary,
    ScoreMark,
    ScoresheetDocument,
    TeamEntry,
    TeamSide,
)
from .template import load_template_definition

ASSET_DIR = Path(settings.BASE_DIR) / "core" / "assets" / "scoresheet"
DEFAULT_TEMPLATE = ASSET_DIR / "scoresheet_template.pdf"

Primitive = dict[str, Any]
INK = "#11110f"


def _text(
    x: float,
    y: float,
    value: str,
    size: float = 7.2,
    *,
    anchor: str = "start",
    field_id: str = "",
    weight: str = "normal",
    vertical: str = "baseline",
) -> Primitive:
    return {
        "type": "text",
        "x": x,
        "y": y,
        "value": value,
        "size": size,
        "anchor": anchor,
        "field_id": field_id,
        "weight": weight,
        "vertical": vertical,
    }


def _line(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    width: float = 1.0,
    *,
    field_id: str = "",
) -> Primitive:
    return {
        "type": "line",
        "x1": x1,
        "y1": y1,
        "x2": x2,
        "y2": y2,
        "width": width,
        "field_id": field_id,
    }


def _circle(
    cx: float,
    cy: float,
    radius: float,
    width: float = 1.0,
    *,
    field_id: str = "",
) -> Primitive:
    return {
        "type": "circle",
        "cx": cx,
        "cy": cy,
        "radius": radius,
        "width": width,
        "field_id": field_id,
    }


def _dot(cx: float, cy: float, radius: float = 1.55, *, field_id: str = "") -> Primitive:
    return {
        "type": "dot",
        "cx": cx,
        "cy": cy,
        "radius": radius,
        "field_id": field_id,
    }


def _row_center(boundaries: list[float], row: int) -> float:
    return (boundaries[row - 1] + boundaries[row]) / 2


def _box_center(bounds: list[float]) -> tuple[float, float]:
    x1, y1, x2, y2 = bounds
    return (x1 + x2) / 2, (y1 + y2) / 2


def _anchored_x(field: dict[str, Any]) -> tuple[float, str]:
    anchor = field.get("anchor", "start")
    return (field["x"] + field["width"] / 2 if anchor == "middle" else field["x"], anchor)


def _foul_primitives(
    foul: FoulEntry,
    x1: float,
    x2: float,
    center_y: float,
    field_id: str,
    scale: float = 1.0,
) -> list[Primitive]:
    center_x = (x1 + x2) / 2
    code = foul.code.value
    base_x = center_x - (0.7 * scale if foul.free_throws else 0.0)
    result = [
        _text(
            base_x,
            center_y + 2.5 * scale,
            code,
            7.2 * scale,
            anchor="middle",
            field_id=field_id,
        )
    ]
    if foul.mark_style == FoulMarkStyle.CIRCLED:
        result.append(
            _circle(
                base_x,
                center_y,
                (5.0 if len(code) == 1 else 6.0) * scale,
                max(0.45, 0.8 * scale),
                field_id=f"{field_id}.circle",
            )
        )
    if foul.free_throws:
        result.append(
            _text(
                center_x + 3.0 * scale,
                center_y + 4.2 * scale,
                str(foul.free_throws),
                4.1 * scale,
                field_id=field_id,
            )
        )
    if foul.cancelled:
        # FIBA calls for a small c "beside" the code. It shares the baseline: Pc, not P₂ᶜ.
        result[0] = _text(
            center_x,
            center_y + 2.5 * scale,
            f"{code}c",
            (6.9 if len(code) == 1 else 6.2) * scale,
            anchor="middle",
            field_id=field_id,
        )
    return result


def _post_foul_primitives(
    markers: list[PostFoulMarker],
    x1: float,
    x2: float,
    center_y: float,
    field_id: str,
) -> list[Primitive]:
    ordered = sorted(markers, key=lambda marker: marker.slot)[:2]
    if not ordered:
        return []
    width = x2 - x1
    centers = [(x1 + x2) / 2] if len(ordered) == 1 else [x1 + width * 0.29, x1 + width * 0.72]
    result: list[Primitive] = []
    for marker, marker_x in zip(ordered, centers, strict=True):
        span = 8.0 if len(ordered) == 1 else width * 0.48
        result.extend(
            _foul_primitives(
                marker,
                marker_x - span / 2,
                marker_x + span / 2,
                center_y,
                f"{field_id}.{marker.slot}",
                scale=0.82 if len(ordered) == 1 else 0.62,
            )
        )
    return result


def _team_primitives(
    team: TeamEntry, layout: dict[str, Any], columns: dict[str, Any]
) -> list[Primitive]:
    side = team.side.value
    result: list[Primitive] = []
    team_name = layout["team_name"]
    team_name_x, team_name_anchor = _anchored_x(team_name)
    result.append(
        _text(
            team_name_x,
            team_name["baseline"],
            team.name,
            team_name["font_size"],
            anchor=team_name_anchor,
            field_id=f"team.{side}.name",
        )
    )

    timeout_by_scope = {(entry.scope, entry.slot): entry for entry in team.timeouts}
    for scope, timeout_layout in layout["timeouts"].items():
        for index, bounds in enumerate(timeout_layout["cells"], start=1):
            x1, y1, x2, y2 = bounds
            center_x, center_y = _box_center(bounds)
            field_id = f"team.{side}.timeout.{scope}.{index}"
            entry = timeout_by_scope.get((scope, index))
            if entry:
                result.append(
                    _text(
                        center_x,
                        center_y,
                        str(entry.minute),
                        7.0,
                        anchor="middle",
                        field_id=field_id,
                        vertical="middle",
                    )
                )
            else:
                result.extend(
                    [
                        _line(
                            x1 + 2.0,
                            center_y - 1.15,
                            x2 - 2.0,
                            center_y - 1.15,
                            0.75,
                            field_id=field_id,
                        ),
                        _line(
                            x1 + 2.0,
                            center_y + 1.15,
                            x2 - 2.0,
                            center_y + 1.15,
                            0.75,
                            field_id=field_id,
                        ),
                    ]
                )

    foul_counts = {entry.period: entry.count for entry in team.team_fouls}
    for period_text, foul_layout in layout["team_fouls"].items():
        period = int(period_text)
        used_count = foul_counts.get(period, 0)
        for index, bounds in enumerate(foul_layout["cells"], start=1):
            x1, y1, x2, y2 = bounds
            center_x, center_y = _box_center(bounds)
            field_id = f"team.{side}.team_foul.{period}.{index}"
            if index <= used_count:
                result.extend(
                    [
                        _line(
                            x1 + 2.0,
                            y1 + 1.5,
                            x2 - 2.0,
                            y2 - 1.5,
                            1.0,
                            field_id=field_id,
                        ),
                        _line(
                            x2 - 2.0,
                            y1 + 1.5,
                            x1 + 2.0,
                            y2 - 1.5,
                            1.0,
                            field_id=field_id,
                        ),
                    ]
                )
            else:
                result.extend(
                    [
                        _line(
                            x1 + 2.0,
                            center_y - 1.15,
                            x2 - 2.0,
                            center_y - 1.15,
                            0.75,
                            field_id=field_id,
                        ),
                        _line(
                            x1 + 2.0,
                            center_y + 1.15,
                            x2 - 2.0,
                            center_y + 1.15,
                            0.75,
                            field_id=field_id,
                        ),
                    ]
                )

    row_boundaries = layout["player_rows"]
    players_by_row = {player.row: player for player in team.players}
    for row in range(1, 13):
        player = players_by_row.get(row)
        if not player:
            continue
        center_y = _row_center(row_boundaries, row)
        baseline = center_y + 2.5
        prefix = f"team.{side}.player.{row:02d}"
        result.append(
            _text(
                sum(columns["license"]) / 2,
                baseline,
                player.license_number,
                6.0,
                anchor="middle",
                field_id=f"{prefix}.license",
            )
        )
        name = f"{player.name}{' (CAP)' if player.captain else ''}"
        result.append(
            _text(columns["name"][0] + 3.0, baseline, name, 7.1, field_id=f"{prefix}.name")
        )
        result.append(
            _text(
                sum(columns["jersey"]) / 2,
                baseline,
                player.jersey_number,
                7.0,
                anchor="middle",
                field_id=f"{prefix}.jersey",
            )
        )

        participation_x = sum(columns["participation"]) / 2
        if player.participation != ParticipationStatus.NONE:
            result.extend(
                [
                    _line(
                        participation_x - 2.7,
                        center_y - 2.7,
                        participation_x + 2.7,
                        center_y + 2.7,
                        0.9,
                        field_id=f"{prefix}.participation",
                    ),
                    _line(
                        participation_x + 2.7,
                        center_y - 2.7,
                        participation_x - 2.7,
                        center_y + 2.7,
                        0.9,
                        field_id=f"{prefix}.participation",
                    ),
                ]
            )
            if player.participation == ParticipationStatus.STARTER:
                result.append(
                    _circle(participation_x, center_y, 5.1, 0.9, field_id=f"{prefix}.participation")
                )

        fouls_by_slot = {foul.slot: foul for foul in player.fouls}
        for slot, (x1, x2) in enumerate(columns["fouls"], start=1):
            foul_id = f"{prefix}.foul.{slot}"
            foul = fouls_by_slot.get(slot)
            if foul:
                result.extend(_foul_primitives(foul, x1, x2, center_y, foul_id))
            elif slot > len(player.fouls):
                result.append(_line(x1 + 1.2, center_y, x2 - 1.2, center_y, 0.85, field_id=foul_id))

        post_x1, post_x2 = columns["post_foul"]
        result.extend(
            _post_foul_primitives(
                player.post_foul_markers,
                post_x1,
                post_x2,
                center_y,
                f"{prefix}.post_foul",
            )
        )

    last_player_row = max(players_by_row, default=0)
    if 0 < last_player_row < 12:
        closure_row = last_player_row + 1
        closure_y = _row_center(row_boundaries, closure_row)
        bottom_y = row_boundaries[-1]
        result.append(
            _line(
                columns["license"][0],
                closure_y,
                columns["participation"][1],
                closure_y,
                1.1,
                field_id=f"team.{side}.roster_closure.horizontal",
            )
        )
        if last_player_row < 11:
            result.append(
                _line(
                    columns["participation"][1],
                    closure_y,
                    columns["fouls"][-1][1],
                    bottom_y,
                    1.1,
                    field_id=f"team.{side}.roster_closure.diagonal",
                )
            )

    head_center = sum(layout["coach_rows"]["head"]) / 2
    assistant_center = sum(layout["coach_rows"]["assistant"]) / 2
    result.append(
        _text(98.0, head_center + 2.4, team.head_coach, 7.0, field_id=f"team.{side}.head_coach")
    )
    result.append(
        _text(
            98.0,
            assistant_center + 2.4,
            team.assistant_coach,
            7.0,
            field_id=f"team.{side}.assistant_coach",
        )
    )
    coach_fouls = {foul.slot: foul for foul in team.coach_fouls}
    for slot, (x1, x2) in enumerate(columns["coach_fouls"], start=1):
        foul = coach_fouls.get(slot)
        if foul:
            result.extend(
                _foul_primitives(foul, x1, x2, head_center, f"team.{side}.coach_foul.{slot}")
            )
    last_coach_foul = max(coach_fouls, default=0)
    if last_coach_foul < len(columns["coach_fouls"]):
        unused_x1 = columns["coach_fouls"][last_coach_foul][0]
        unused_x2 = columns["coach_fouls"][-1][1]
        result.append(
            _line(
                unused_x1 + 1.2,
                head_center,
                unused_x2 - 1.2,
                head_center,
                0.85,
                field_id=f"team.{side}.coach_foul.unused",
            )
        )
    assistant_coach_fouls = {foul.slot: foul for foul in team.assistant_coach_fouls}
    for slot, (x1, x2) in enumerate(columns["coach_fouls"], start=1):
        foul = assistant_coach_fouls.get(slot)
        if foul:
            result.extend(
                _foul_primitives(
                    foul,
                    x1,
                    x2,
                    assistant_center,
                    f"team.{side}.assistant_coach_foul.{slot}",
                )
            )
    last_assistant_foul = max(assistant_coach_fouls, default=0)
    if last_assistant_foul < len(columns["coach_fouls"]):
        unused_x1 = columns["coach_fouls"][last_assistant_foul][0]
        unused_x2 = columns["coach_fouls"][-1][1]
        result.append(
            _line(
                unused_x1 + 1.2,
                assistant_center,
                unused_x2 - 1.2,
                assistant_center,
                0.85,
                field_id=f"team.{side}.assistant_coach_foul.unused",
            )
        )
    post_x1, post_x2 = columns["post_foul"]
    result.extend(
        _post_foul_primitives(
            team.coach_post_foul_markers,
            post_x1,
            post_x2,
            head_center,
            f"team.{side}.coach_post_foul",
        )
    )
    result.extend(
        _post_foul_primitives(
            team.assistant_coach_post_foul_markers,
            post_x1,
            post_x2,
            assistant_center,
            f"team.{side}.assistant_coach_post_foul",
        )
    )
    return result


def _automatic_game_end_sequences(document: ScoresheetDocument) -> set[int]:
    """Return the last scoring event for each team once both written final scores agree."""
    latest: dict[TeamSide, Any] = {}
    for side in (TeamSide.A, TeamSide.B):
        side_events = [event for event in document.score_events if event.team == side]
        if not side_events:
            return set()
        latest[side] = max(side_events, key=lambda event: event.cumulative_score)
    if latest[TeamSide.A].cumulative_score != document.final_score.team_a:
        return set()
    if latest[TeamSide.B].cumulative_score != document.final_score.team_b:
        return set()
    return {latest[TeamSide.A].sequence, latest[TeamSide.B].sequence}


def build_scene(document: ScoresheetDocument) -> list[Primitive]:
    definition = load_template_definition()
    result: list[Primitive] = []
    header_values = {
        "team_a_name": next(team.name for team in document.teams if team.side == TeamSide.A),
        "team_b_name": next(team.name for team in document.teams if team.side == TeamSide.B),
        "competition": document.header.competition,
        "date": document.header.date,
        "scheduled_time": document.header.scheduled_time,
        "crew_chief": document.header.crew_chief,
        "game_number": document.header.game_number,
        "venue": document.header.venue,
        "umpire_1": document.header.umpire_1,
        "umpire_2": document.header.umpire_2,
    }
    for field_id, value in header_values.items():
        field = definition["header_fields"][field_id]
        field_x, field_anchor = _anchored_x(field)
        result.append(
            _text(
                field_x,
                field["baseline"],
                value,
                field["font_size"],
                anchor=field_anchor,
                field_id=f"header.{field_id}",
            )
        )

    columns = definition["player_columns"]
    for team in document.teams:
        result.extend(_team_primitives(team, definition["team_layouts"][team.side.value], columns))

    running = definition["running_score"]
    group_boundaries = running["group_boundaries"]
    row_boundaries = running["row_boundaries"]
    offsets = running["cell_offsets"]
    automatic_game_end = _automatic_game_end_sequences(document)
    for event in document.score_events:
        group = (event.cumulative_score - 1) // 40
        row = (event.cumulative_score - 1) % 40 + 1
        group_x = group_boundaries[group]
        center_y = _row_center(row_boundaries, row)
        score_x = group_x + offsets["a_score" if event.team == TeamSide.A else "b_score"]
        player_x = group_x + offsets["a_player" if event.team == TeamSide.A else "b_player"]
        event_id = f"score.{event.team.value}.{event.cumulative_score:03d}"
        result.append(
            _text(
                player_x,
                center_y + 2.4,
                event.scorer_jersey,
                6.5,
                anchor="middle",
                field_id=f"{event_id}.scorer",
            )
        )
        if event.mark == ScoreMark.FILLED_DOT:
            result.append(_dot(score_x, center_y, field_id=f"{event_id}.mark"))
        elif event.mark == ScoreMark.DIAGONAL:
            result.append(
                _line(
                    score_x - 4.6,
                    center_y + 4.6,
                    score_x + 4.6,
                    center_y - 4.6,
                    1.2,
                    field_id=f"{event_id}.mark",
                )
            )
        if event.scorer_circled:
            result.append(_circle(player_x, center_y, 5.2, 1.0, field_id=f"{event_id}.three_point"))
        effective_boundary = (
            ScoreBoundary.GAME_END
            if event.sequence in automatic_game_end
            else ScoreBoundary.PERIOD_END
            if event.boundary in {ScoreBoundary.PERIOD_END, ScoreBoundary.GAME_END}
            else ScoreBoundary.NONE
        )
        if effective_boundary in {ScoreBoundary.PERIOD_END, ScoreBoundary.GAME_END}:
            result.append(_circle(score_x, center_y, 5.3, 1.35, field_id=f"{event_id}.boundary"))
            if event.team == TeamSide.A:
                line_start, line_end = group_x + 1.2, group_x + 27.0
            else:
                line_start, line_end = group_x + 29.4, group_x + 55.2
            result.append(
                _line(
                    line_start,
                    center_y + 6.0,
                    line_end,
                    center_y + 6.0,
                    1.3,
                    field_id=f"{event_id}.boundary",
                )
            )
            if effective_boundary == ScoreBoundary.GAME_END:
                result.append(
                    _line(
                        line_start,
                        center_y + 8.3,
                        line_end,
                        center_y + 8.3,
                        1.3,
                        field_id=f"{event_id}.boundary",
                    )
                )
                next_top = row_boundaries[row]
                group_bottom = row_boundaries[-1]
                if next_top < group_bottom:
                    result.append(
                        _line(
                            line_start + 1.0,
                            next_top + 1.0,
                            line_end - 1.0,
                            group_bottom - 1.0,
                            1.1,
                            field_id=f"{event_id}.closure",
                        )
                    )

    summary = definition["summary_fields"]
    stated_by_period = {score.period: score for score in document.stated_period_scores}
    for period, baseline in enumerate(summary["period_baselines"], start=1):
        score = stated_by_period.get(period)
        if score:
            result.append(
                _text(
                    summary["period_a_x"],
                    baseline,
                    str(score.team_a),
                    8.0,
                    anchor="middle",
                    field_id=f"summary.period.{period}.A",
                )
            )
            result.append(
                _text(
                    summary["period_b_x"],
                    baseline,
                    str(score.team_b),
                    8.0,
                    anchor="middle",
                    field_id=f"summary.period.{period}.B",
                )
            )
    result.extend(
        [
            _text(
                summary["final_a"]["x"],
                summary["final_a"]["baseline"],
                str(document.final_score.team_a),
                8.8,
                anchor="middle",
                field_id="summary.final.A",
            ),
            _text(
                summary["final_b"]["x"],
                summary["final_b"]["baseline"],
                str(document.final_score.team_b),
                8.8,
                anchor="middle",
                field_id="summary.final.B",
            ),
            _text(
                summary["winner"]["x"] + summary["winner"]["width"] / 2,
                summary["winner"]["baseline"],
                document.final_score.winner_name,
                8.0,
                anchor="middle",
                field_id="summary.winner",
            ),
            _text(
                summary["ended_at"]["x"] + summary["ended_at"]["width"] / 2,
                summary["ended_at"]["baseline"],
                document.final_score.ended_at,
                7.8,
                anchor="middle",
                field_id="summary.ended_at",
            ),
        ]
    )

    official_layout = definition["official_fields"]
    for official in document.officials:
        field = official_layout[official.role]
        field_x, field_anchor = _anchored_x(field)
        result.append(
            _text(
                field_x,
                field["baseline"],
                official.name,
                7.4,
                anchor=field_anchor,
                field_id=f"official.{official.role}.name",
            )
        )
    return result


def render_svg(document: ScoresheetDocument) -> str:
    definition = load_template_definition()
    width = definition["page"]["width"]
    height = definition["page"]["height"]
    elements: list[str] = []
    for primitive in build_scene(document):
        field = html.escape(primitive.get("field_id", ""), quote=True)
        if primitive["type"] == "text":
            value = html.escape(str(primitive["value"]))
            dominant_baseline = (
                ' dominant-baseline="central"' if primitive.get("vertical") == "middle" else ""
            )
            elements.append(
                f'<text data-field-id="{field}" x="{primitive["x"]}" y="{primitive["y"]}" '
                f'font-size="{primitive["size"]}" text-anchor="{primitive["anchor"]}" '
                f'font-family="Noto Sans SC, Microsoft YaHei, sans-serif" '
                f'font-weight="400" font-synthesis="none" text-rendering="geometricPrecision"'
                f'{dominant_baseline} fill="{INK}">{value}</text>'
            )
        elif primitive["type"] == "line":
            elements.append(
                f'<line data-field-id="{field}" x1="{primitive["x1"]}" y1="{primitive["y1"]}" '
                f'x2="{primitive["x2"]}" y2="{primitive["y2"]}" stroke="{INK}" '
                f'stroke-width="{primitive["width"]}" stroke-linecap="round" />'
            )
        elif primitive["type"] == "circle":
            elements.append(
                f'<circle data-field-id="{field}" cx="{primitive["cx"]}" cy="{primitive["cy"]}" '
                f'r="{primitive["radius"]}" fill="none" stroke="{INK}" '
                f'stroke-width="{primitive["width"]}" />'
            )
        elif primitive["type"] == "dot":
            elements.append(
                f'<circle data-field-id="{field}" cx="{primitive["cx"]}" cy="{primitive["cy"]}" '
                f'r="{primitive["radius"]}" fill="{INK}" />'
            )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}" role="img" aria-label="记录表语义覆盖层">'
        + "".join(elements)
        + "</svg>"
    )


def _register_font() -> str:
    candidates: Iterable[Path] = filter(
        None,
        [
            Path(settings.SCORESHEET_FONT_PATH)
            if getattr(settings, "SCORESHEET_FONT_PATH", "")
            else None,
            Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "msyh.ttf",
            Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "NotoSansSC-VF.ttf",
        ],
    )
    for path in candidates:
        if path.exists():
            try:
                pdfmetrics.registerFont(TTFont("ScoresheetFont", str(path)))
                return "ScoresheetFont"
            except Exception:
                continue
    try:
        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    except KeyError:
        pass
    return "STSong-Light"


def _draw_pdf_scene(pdf_canvas: canvas.Canvas, document: ScoresheetDocument) -> None:
    definition = load_template_definition()
    height = definition["page"]["height"]
    font_name = _register_font()
    pdf_canvas.setStrokeColorRGB(0.067, 0.067, 0.059)
    pdf_canvas.setFillColorRGB(0.067, 0.067, 0.059)
    for primitive in build_scene(document):
        if primitive["type"] == "text":
            pdf_canvas.setFont(font_name, primitive["size"])
            y = height - primitive["y"]
            if primitive.get("vertical") == "middle":
                ascent, descent = pdfmetrics.getAscentDescent(font_name, primitive["size"])
                y -= (ascent + descent) / 2
            value = str(primitive["value"])
            if primitive["anchor"] == "middle":
                pdf_canvas.drawCentredString(primitive["x"], y, value)
            elif primitive["anchor"] == "end":
                pdf_canvas.drawRightString(primitive["x"], y, value)
            else:
                pdf_canvas.drawString(primitive["x"], y, value)
        elif primitive["type"] == "line":
            pdf_canvas.setLineWidth(primitive["width"])
            pdf_canvas.line(
                primitive["x1"],
                height - primitive["y1"],
                primitive["x2"],
                height - primitive["y2"],
            )
        elif primitive["type"] == "circle":
            pdf_canvas.setLineWidth(primitive["width"])
            pdf_canvas.circle(
                primitive["cx"],
                height - primitive["cy"],
                primitive["radius"],
                stroke=1,
                fill=0,
            )
        elif primitive["type"] == "dot":
            pdf_canvas.circle(
                primitive["cx"],
                height - primitive["cy"],
                primitive["radius"],
                stroke=0,
                fill=1,
            )


def render_pdf(document: ScoresheetDocument, template_path: Path | None = None) -> bytes:
    definition = load_template_definition()
    page_size = (definition["page"]["width"], definition["page"]["height"])
    overlay_buffer = io.BytesIO()
    pdf_canvas = canvas.Canvas(overlay_buffer, pagesize=page_size, pageCompression=1)
    _draw_pdf_scene(pdf_canvas, document)
    pdf_canvas.showPage()
    pdf_canvas.save()
    overlay_buffer.seek(0)

    path = template_path or DEFAULT_TEMPLATE
    if not path.exists():
        raise FileNotFoundError(f"Template PDF not found at {path}. Set SCORESHEET_TEMPLATE_PATH.")
    template_reader = PdfReader(str(path))
    overlay_reader = PdfReader(overlay_buffer)
    page = template_reader.pages[0]
    page.merge_page(overlay_reader.pages[0])
    writer = PdfWriter()
    writer.add_page(page)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()
