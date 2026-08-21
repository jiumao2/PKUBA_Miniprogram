from __future__ import annotations

import io
import json
import os
from pathlib import Path
from typing import Any

from django.conf import settings
from pypdf import PdfReader, PdfWriter
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas

ASSET_DIR = Path(settings.BASE_DIR) / "core" / "assets" / "scoresheet"
DEFAULT_TEMPLATE = ASSET_DIR / "scoresheet_template.pdf"
DEFAULT_DEFINITION = ASSET_DIR / "template_definition.json"


def _definition() -> dict[str, Any]:
    with DEFAULT_DEFINITION.open("r", encoding="utf-8") as source:
        return json.load(source)


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _draw_text(
    pdf: canvas.Canvas,
    *,
    page_height: float,
    x: float,
    baseline: float,
    value: Any,
    size: float = 7.5,
    width: float | None = None,
    anchor: str = "start",
) -> None:
    text = _text(value).strip()
    if not text:
        return
    pdf.setFont("STSong-Light", size)
    if anchor in {"middle", "center"} and width is not None:
        pdf.drawCentredString(x + width / 2, page_height - baseline, text)
    elif anchor == "end" and width is not None:
        pdf.drawRightString(x + width, page_height - baseline, text)
    else:
        pdf.drawString(x, page_height - baseline, text)


def _header(pdf: canvas.Canvas, document: dict[str, Any], definition: dict[str, Any]) -> None:
    page_height = float(definition["page"]["height"])
    game = document.get("game", {})
    teams = document.get("teams", {})
    values = {
        "team_a_name": teams.get("A", {}).get("name"),
        "team_b_name": teams.get("B", {}).get("name"),
        "competition": game.get("competition"),
        "date": game.get("date"),
        "scheduled_time": game.get("scheduled_time"),
        "crew_chief": game.get("crew_chief"),
        "game_number": game.get("game_number"),
        "venue": game.get("venue"),
        "umpire_1": game.get("umpire_1"),
        "umpire_2": game.get("umpire_2"),
    }
    for key, field in definition["header_fields"].items():
        _draw_text(
            pdf,
            page_height=page_height,
            x=float(field["x"]),
            baseline=float(field["baseline"]),
            value=values.get(key),
            size=float(field.get("font_size", 8)),
            width=float(field.get("width", 0)),
            anchor=str(field.get("anchor", "start")),
        )


def _teams(pdf: canvas.Canvas, document: dict[str, Any], definition: dict[str, Any]) -> None:
    height = float(definition["page"]["height"])
    columns = definition["player_columns"]
    for side in ("A", "B"):
        layout = definition["team_layouts"][side]
        team = document.get("teams", {}).get(side, {})
        field = layout["team_name"]
        _draw_text(
            pdf,
            page_height=height,
            x=float(field["x"]),
            baseline=float(field["baseline"]),
            value=team.get("name"),
            size=float(field.get("font_size", 9)),
            width=float(field["width"]),
            anchor="middle",
        )
        rows = layout["player_rows"]
        for index, player in enumerate(team.get("players", [])[: len(rows)]):
            baseline = float(rows[index]) + 9.0
            player_name = str(player.get("name") or "")
            if player.get("captain"):
                player_name += " (CAP)"
            _draw_text(
                pdf,
                page_height=height,
                x=float(columns["name"][0]) + 2,
                baseline=baseline,
                value=player_name,
                size=6.5,
                width=float(columns["name"][1] - columns["name"][0]) - 4,
                anchor="middle",
            )
            _draw_text(
                pdf,
                page_height=height,
                x=float(columns["jersey"][0]),
                baseline=baseline,
                value=player.get("jersey_number"),
                size=7,
                width=float(columns["jersey"][1] - columns["jersey"][0]),
                anchor="middle",
            )
            if player.get("appeared"):
                pdf.setLineWidth(0.9)
                y = height - baseline + 2
                x1, x2 = map(float, columns["participation"])
                pdf.line(x1 + 4, y - 3, x2 - 4, y + 4)
                pdf.line(x1 + 4, y + 4, x2 - 4, y - 3)
                if player.get("starter"):
                    pdf.circle((x1 + x2) / 2, y, 5.2, stroke=1, fill=0)
            for foul_index, foul in enumerate(player.get("fouls", [])[:5]):
                x1, x2 = map(float, columns["fouls"][foul_index])
                _draw_text(
                    pdf,
                    page_height=height,
                    x=x1,
                    baseline=baseline,
                    value=foul.get("code") if isinstance(foul, dict) else foul,
                    size=6.5,
                    width=x2 - x1,
                    anchor="middle",
                )

        for scope, timeout_layout in layout.get("timeouts", {}).items():
            entries = team.get("timeouts", {}).get(scope, [])
            if not isinstance(entries, list):
                continue
            for index, bounds in enumerate(timeout_layout.get("cells", [])):
                if index >= len(entries):
                    break
                entry = entries[index]
                value = entry.get("minute", "") if isinstance(entry, dict) else entry
                x1, y1, x2, y2 = map(float, bounds)
                _draw_text(
                    pdf,
                    page_height=height,
                    x=x1,
                    baseline=(y1 + y2) / 2 + 2.5,
                    value=value,
                    size=6.5,
                    width=x2 - x1,
                    anchor="middle",
                )

        for period, foul_layout in layout.get("team_fouls", {}).items():
            entries = team.get("team_fouls", {}).get(period, [])
            if not isinstance(entries, list):
                continue
            for index, bounds in enumerate(foul_layout.get("cells", [])):
                if index >= len(entries):
                    break
                x1, y1, x2, y2 = map(float, bounds)
                pdf.setLineWidth(0.9)
                pdf.line(x1 + 2, height - y1 - 1.5, x2 - 2, height - y2 + 1.5)
                pdf.line(x2 - 2, height - y1 - 1.5, x1 + 2, height - y2 + 1.5)

        for coach_key, row_key in (
            ("head_coach", "head"),
            ("assistant_coach", "assistant"),
        ):
            coach = team.get(coach_key, {})
            row_bounds = layout.get("coach_rows", {}).get(row_key, [])
            if not isinstance(coach, dict) or len(row_bounds) != 2:
                continue
            center = sum(map(float, row_bounds)) / 2
            _draw_text(
                pdf,
                page_height=height,
                x=float(columns["name"][0]) + 3,
                baseline=center + 2.5,
                value=coach.get("name"),
                size=6.8,
                width=float(columns["name"][1] - columns["name"][0]) - 6,
            )
            fouls = coach.get("fouls", [])
            if not isinstance(fouls, list):
                continue
            for index, foul in enumerate(fouls[: len(columns["coach_fouls"])]):
                x1, x2 = map(float, columns["coach_fouls"][index])
                _draw_text(
                    pdf,
                    page_height=height,
                    x=x1,
                    baseline=center + 2.5,
                    value=foul.get("code") if isinstance(foul, dict) else foul,
                    size=6.2,
                    width=x2 - x1,
                    anchor="middle",
                )


def _running_score(
    pdf: canvas.Canvas, document: dict[str, Any], definition: dict[str, Any]
) -> None:
    height = float(definition["page"]["height"])
    score_layout = definition["running_score"]
    boundaries = [float(value) for value in score_layout["group_boundaries"]]
    rows = [float(value) for value in score_layout["row_boundaries"]]
    offsets = score_layout["cell_offsets"]
    for event in document.get("running_score", []):
        if not isinstance(event, dict):
            continue
        try:
            cumulative = int(event.get("cumulative"))
            value = int(event.get("value"))
        except (TypeError, ValueError):
            continue
        if cumulative < 1 or cumulative > 160 or event.get("team") not in {"A", "B"}:
            continue
        block = (cumulative - 1) // 40
        row = (cumulative - 1) % 40
        left = boundaries[block]
        y_top = rows[row]
        y_bottom = rows[row + 1]
        y = height - (y_top + y_bottom) / 2
        side = event["team"]
        score_x = left + float(offsets["a_score" if side == "A" else "b_score"])
        player_x = left + float(offsets["a_player" if side == "A" else "b_player"])
        player_number = event.get("player_number", "")
        _draw_text(
            pdf,
            page_height=height,
            x=player_x - 6,
            baseline=(y_top + y_bottom) / 2 + 2.5,
            value=player_number,
            size=5.7,
            width=12,
            anchor="middle",
        )
        if value == 1:
            pdf.circle(score_x, y, 1.5, stroke=0, fill=1)
        else:
            pdf.setLineWidth(0.8)
            pdf.line(score_x - 4, y - 4, score_x + 4, y + 4)
            if value == 3:
                pdf.circle(score_x, y, 5.2, stroke=1, fill=0)
        boundary = event.get("boundary")
        if boundary in {"period", "game"}:
            pdf.setLineWidth(1.1)
            pdf.circle(score_x, y, 5.3, stroke=1, fill=0)
            if side == "A":
                line_start, line_end = left + 1.2, left + 27.0
            else:
                line_start, line_end = left + 29.4, left + 55.2
            pdf.line(line_start, y - 6, line_end, y - 6)
            if boundary == "game":
                pdf.line(line_start, y - 8.3, line_end, y - 8.3)


def _summary_and_officials(
    pdf: canvas.Canvas, document: dict[str, Any], definition: dict[str, Any]
) -> None:
    height = float(definition["page"]["height"])
    summary = document.get("summary", {})
    fields = definition["summary_fields"]
    period_scores = summary.get("period_scores", {})
    for index, period in enumerate(("1", "2", "3", "4", "OT")):
        baseline = float(fields["period_baselines"][index])
        for side, x_key in (("A", "period_a_x"), ("B", "period_b_x")):
            _draw_text(
                pdf,
                page_height=height,
                x=float(fields[x_key]) - 10,
                baseline=baseline,
                value=period_scores.get(period, {}).get(side),
                size=7,
                width=20,
                anchor="middle",
            )
    final = summary.get("final_score", {})
    for side, key in (("A", "final_a"), ("B", "final_b")):
        field = fields[key]
        _draw_text(
            pdf,
            page_height=height,
            x=float(field["x"]) - 10,
            baseline=float(field["baseline"]),
            value=final.get(side),
            size=8,
            width=20,
            anchor="middle",
        )
    winner = summary.get("winner_side")
    winner_name = document.get("teams", {}).get(winner, {}).get("name", "")
    for key, value in (("winner", winner_name), ("ended_at", summary.get("ended_at"))):
        field = fields[key]
        _draw_text(
            pdf,
            page_height=height,
            x=float(field["x"]),
            baseline=float(field["baseline"]),
            value=value,
            size=7,
            width=float(field["width"]),
            anchor=str(field.get("anchor", "middle")),
        )
    officials = document.get("officials", {})
    game = document.get("game", {})
    values = {
        **officials,
        "crew_chief": game.get("crew_chief"),
        "umpire_1": game.get("umpire_1"),
        "umpire_2": game.get("umpire_2"),
        "protest_captain": "有异议" if officials.get("captain_protest_signature") else "",
    }
    for key, field in definition["official_fields"].items():
        _draw_text(
            pdf,
            page_height=height,
            x=float(field["x"]),
            baseline=float(field["baseline"]),
            value=values.get(key),
            size=6.8,
            width=float(field["width"]),
            anchor=str(field.get("anchor", "middle")),
        )


def render_scoresheet_pdf(document: dict[str, Any], template_path: Path | None = None) -> bytes:
    definition = _definition()
    path = template_path or Path(os.getenv("SCORESHEET_TEMPLATE_PATH", DEFAULT_TEMPLATE))
    if not path.exists():
        raise FileNotFoundError(f"记录表 PDF 模板不存在：{path}")
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    page_size = (float(definition["page"]["width"]), float(definition["page"]["height"]))
    overlay_buffer = io.BytesIO()
    pdf = canvas.Canvas(overlay_buffer, pagesize=page_size, pageCompression=1)
    _header(pdf, document, definition)
    _teams(pdf, document, definition)
    _running_score(pdf, document, definition)
    _summary_and_officials(pdf, document, definition)
    pdf.showPage()
    pdf.save()
    overlay_buffer.seek(0)

    template = PdfReader(str(path))
    overlay = PdfReader(overlay_buffer)
    page = template.pages[0]
    page.merge_page(overlay.pages[0])
    writer = PdfWriter()
    writer.add_page(page)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()
