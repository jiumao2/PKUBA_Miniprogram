from __future__ import annotations

import copy
import hashlib
import json
import re
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from core.models import Game, RosterPlayer
from core.scoresheet_v2.models import ScoresheetDocument
from core.scoresheet_v2.validation import validate_document as validate_v2_document

RULE_PROFILE = "fiba_2024"
TEMPLATE_ID = "pku-basketball-2019-v1"
SCHEMA_VERSION = "1.4.0"

REGIONS = (
    "SOURCE_GAME",
    "TEAM_A",
    "TEAM_B",
    "RUNNING_SCORE",
    "SUMMARY",
    "OFFICIALS",
)

REGION_LABELS = {
    "SOURCE_GAME": "原图与比赛信息",
    "TEAM_A": "A 队",
    "TEAM_B": "B 队",
    "RUNNING_SCORE": "逐次得分",
    "SUMMARY": "节比分与最终结果",
    "OFFICIALS": "工作人员与签名",
}


class ScoresheetDocumentError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def game_prior_snapshot(game: Game) -> dict[str, Any]:
    if not game.home_team_id or not game.away_team_id:
        raise ScoresheetDocumentError("PARTICIPANTS_MISSING", "比赛双方尚未落位。")
    return {
        "game_id": str(game.id),
        "game_code": game.code,
        "competition": game.season.name,
        "division": game.division.name,
        "date": game.date.isoformat(),
        "scheduled_time": game.start_time.strftime("%H:%M"),
        "venue": game.venue_name,
        "team_a": {"team_id": str(game.home_team_id), "display_name": game.home_display},
        "team_b": {"team_id": str(game.away_team_id), "display_name": game.away_display},
        "game_version": game.version,
    }


def roster_prior_snapshot(game: Game) -> dict[str, list[dict[str, Any]]]:
    team_ids = [team_id for team_id in (game.home_team_id, game.away_team_id) if team_id]
    rows = RosterPlayer.objects.filter(team_id__in=team_ids, active=True).order_by(
        "team_id", "jersey_number", "name"
    )
    by_team: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for player in rows:
        by_team[str(player.team_id)].append(
            {
                "player_id": str(player.id),
                "display_name": player.name,
                "jersey_number": player.jersey_number,
            }
        )
    return {
        "A": by_team.get(str(game.home_team_id), []),
        "B": by_team.get(str(game.away_team_id), []),
    }


def _source_prior(prior: dict[str, Any], roster: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    source_hash = hashlib.sha256(
        json.dumps({"game": prior, "roster": roster}, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
    return {
        "game_id": prior["game_id"],
        "competition": prior["competition"],
        "division": prior["division"],
        "date": prior["date"],
        "scheduled_time": prior["scheduled_time"],
        "venue": prior["venue"],
        "team_a": {
            "team_id": prior["team_a"]["team_id"],
            "name": prior["team_a"]["display_name"],
            "player_names": [str(row.get("display_name") or "") for row in roster.get("A", [])],
        },
        "team_b": {
            "team_id": prior["team_b"]["team_id"],
            "name": prior["team_b"]["display_name"],
            "player_names": [str(row.get("display_name") or "") for row in roster.get("B", [])],
        },
        "source_hash": source_hash,
        "locked_paths": [
            "/header/game_number",
            "/header/competition",
            "/header/date",
            "/header/scheduled_time",
            "/header/venue",
            "/teams/0/name",
            "/teams/1/name",
        ],
    }


def _player_from_roster(row: int, player: dict[str, Any]) -> dict[str, Any]:
    return {
        "row": row,
        "license_number": "",
        "name": str(player.get("display_name") or ""),
        "jersey_number": str(player.get("jersey_number") or ""),
        "captain": False,
        "participation": "none",
        "fouls": [],
        "post_foul_markers": [],
    }


def _blank_team(side: str, name: str, roster: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "side": side,
        "name": name,
        "players": [_player_from_roster(index, row) for index, row in enumerate(roster[:12], 1)],
        "timeouts": [],
        "team_fouls": [],
        "coach_fouls": [],
        "coach_post_foul_markers": [],
        "assistant_coach_fouls": [],
        "assistant_coach_post_foul_markers": [],
        "head_coach": "",
        "assistant_coach": "",
    }


def _blank_officials() -> list[dict[str, Any]]:
    return [
        {"role": role, "name": "", "signature": "absent"}
        for role in (
            "scorer",
            "assistant_scorer",
            "timer",
            "shot_clock_operator",
            "crew_chief",
            "umpire_1",
            "umpire_2",
            "protest_captain",
        )
    ]


def new_document(
    prior: dict[str, Any],
    roster: dict[str, list[dict[str, Any]]],
    *,
    document_id: str = "pending",
    source: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = _utc_now()
    document = {
        "schema_version": SCHEMA_VERSION,
        "rules_profile": RULE_PROFILE,
        "id": document_id,
        "revision": 0,
        "template_id": TEMPLATE_ID,
        "status": "draft",
        "created_at": now,
        "updated_at": now,
        "source": {
            "original_filename": "",
            "original_url": "",
            "aligned_url": "",
            "version": 0,
            "content_sha256": "",
            "width": 0,
            "height": 0,
            "rotation": 0,
            "corners": None,
            **(source or {}),
        },
        "game_prior": _source_prior(prior, roster),
        "recognition": None,
        "header": {
            "competition": prior["competition"],
            "game_number": prior["game_code"],
            "date": prior["date"],
            "scheduled_time": prior["scheduled_time"],
            "venue": prior["venue"],
            "crew_chief": "",
            "umpire_1": "",
            "umpire_2": "",
        },
        "teams": [
            _blank_team("A", prior["team_a"]["display_name"], roster.get("A", [])),
            _blank_team("B", prior["team_b"]["display_name"], roster.get("B", [])),
        ],
        "score_events": [],
        "stated_period_scores": [],
        "final_score": {"team_a": 0, "team_b": 0, "winner_name": "", "ended_at": ""},
        "officials": _blank_officials(),
        "acknowledged_warnings": [],
    }
    return ScoresheetDocument.model_validate(document).model_dump(mode="json")


def _old_foul(entry: Any, slot: int) -> dict[str, Any] | None:
    raw = entry.get("code") if isinstance(entry, dict) else entry
    token = str(raw or "").strip()
    match = re.fullmatch(r"\(?([A-Z]+)\)?([123]|c)?", token)
    if not match:
        return None
    return {
        "slot": slot,
        "code": match.group(1),
        "catalog_id": None,
        "mark_style": "circled" if token.startswith("(") else "plain",
        "free_throws": int(match.group(2)) if (match.group(2) or "").isdigit() else None,
        "cancelled": match.group(2) == "c",
        "period": None,
    }


def _convert_old_document(
    document: dict[str, Any],
    prior: dict[str, Any],
    roster: dict[str, list[dict[str, Any]]],
    document_id: str,
) -> dict[str, Any]:
    converted = new_document(prior, roster, document_id=document_id)
    game = document.get("game") if isinstance(document.get("game"), dict) else {}
    for key in converted["header"]:
        if key in game:
            converted["header"][key] = str(game.get(key) or "")
    old_teams = document.get("teams") if isinstance(document.get("teams"), dict) else {}
    for team_index, side in enumerate(("A", "B")):
        old_team = old_teams.get(side) if isinstance(old_teams.get(side), dict) else {}
        target = converted["teams"][team_index]
        players = old_team.get("players") if isinstance(old_team.get("players"), list) else []
        target["players"] = []
        for index, player in enumerate(players[:12], 1):
            if not isinstance(player, dict):
                continue
            fouls = [
                parsed
                for slot, value in enumerate(player.get("fouls") or [], 1)
                if (parsed := _old_foul(value, slot)) is not None
            ][:5]
            target["players"].append(
                {
                    "row": index,
                    "license_number": "",
                    "name": str(player.get("name") or ""),
                    "jersey_number": str(player.get("jersey_number") or ""),
                    "captain": bool(player.get("captain")),
                    "participation": (
                        "starter"
                        if player.get("starter")
                        else "substitute"
                        if player.get("appeared")
                        else "none"
                    ),
                    "fouls": fouls,
                    "post_foul_markers": [],
                }
            )
        for scope, values in (old_team.get("timeouts") or {}).items():
            if scope not in {"H1", "H2", "OT"} or not isinstance(values, list):
                continue
            for slot, value in enumerate(values[:3], 1):
                minute = value.get("minute") if isinstance(value, dict) else value
                if isinstance(minute, int) and 0 <= minute <= 10:
                    target["timeouts"].append({"scope": scope, "slot": slot, "minute": minute})
        for period, values in (old_team.get("team_fouls") or {}).items():
            if str(period).isdigit() and isinstance(values, list):
                target["team_fouls"].append({"period": int(period), "count": min(4, len(values))})
        for role in ("head_coach", "assistant_coach"):
            value = old_team.get(role)
            target[role] = (
                str(value.get("name") or "") if isinstance(value, dict) else str(value or "")
            )

    for index, event in enumerate(document.get("running_score") or [], 1):
        if not isinstance(event, dict) or event.get("team") not in {"A", "B"}:
            continue
        points = event.get("value")
        cumulative = event.get("cumulative")
        if points not in {1, 2, 3} or not isinstance(cumulative, int):
            continue
        period = 5 if str(event.get("period")) == "OT" else int(event.get("period") or 1)
        converted["score_events"].append(
            {
                "sequence": index,
                "team": event["team"],
                "period": period,
                "points": points,
                "cumulative_score": cumulative,
                "scorer_jersey": str(event.get("player_number") or ""),
                "mark": "filled_dot" if points == 1 else "diagonal",
                "scorer_circled": points == 3,
                "boundary": {
                    "period": "period_end",
                    "game": "game_end",
                }.get(str(event.get("boundary")), "none"),
                "ink_role": "q1_q3" if period in {1, 3} else "q2_q4_ot",
            }
        )
    summary = document.get("summary") if isinstance(document.get("summary"), dict) else {}
    period_scores = (
        summary.get("period_scores") if isinstance(summary.get("period_scores"), dict) else {}
    )
    for label, period in (("1", 1), ("2", 2), ("3", 3), ("4", 4), ("OT", 5)):
        row = period_scores.get(label) if isinstance(period_scores.get(label), dict) else {}
        if isinstance(row.get("A"), int) and isinstance(row.get("B"), int):
            converted["stated_period_scores"].append(
                {"period": period, "team_a": row["A"], "team_b": row["B"]}
            )
    final = summary.get("final_score") if isinstance(summary.get("final_score"), dict) else {}
    converted["final_score"] = {
        "team_a": int(final.get("A") or 0),
        "team_b": int(final.get("B") or 0),
        "winner_name": (
            converted["teams"][0]["name"]
            if summary.get("winner_side") == "A"
            else converted["teams"][1]["name"]
            if summary.get("winner_side") == "B"
            else ""
        ),
        "ended_at": str(summary.get("ended_at") or ""),
    }
    old_officials = document.get("officials") if isinstance(document.get("officials"), dict) else {}
    for official in converted["officials"]:
        role = official["role"]
        if role in old_officials:
            official["name"] = str(old_officials.get(role) or "")
        signature_key = f"{role}_signature"
        if signature_key in old_officials:
            official["signature"] = "present" if old_officials[signature_key] else "absent"
    alignment = document.get("source_alignment")
    if isinstance(alignment, dict):
        converted["source"]["rotation"] = int(alignment.get("rotation") or 0)
        corners = alignment.get("corners")
        if isinstance(corners, list) and len(corners) == 4:
            converted["source"]["corners"] = [
                [float(point.get("x", 0)), float(point.get("y", 0))]
                if isinstance(point, dict)
                else point
                for point in corners
            ]
    return ScoresheetDocument.model_validate(converted).model_dump(mode="json")


def ensure_v2_document(
    document: dict[str, Any],
    prior: dict[str, Any] | None = None,
    roster: dict[str, list[dict[str, Any]]] | None = None,
    *,
    document_id: str | None = None,
) -> dict[str, Any]:
    prior = prior or {}
    roster = roster or {"A": [], "B": []}
    if isinstance(document, dict) and isinstance(document.get("schema_version"), str):
        payload = copy.deepcopy(document)
        if document_id:
            payload["id"] = document_id
        return ScoresheetDocument.model_validate(payload).model_dump(mode="json")
    if not prior:
        raise ScoresheetDocumentError("DRAFT_SCHEMA_INVALID", "旧版记录表缺少比赛先验，无法迁移。")
    return _convert_old_document(document, prior, roster, document_id or "pending")


def region_for_path(path: str) -> str:
    if path in {"", "/"}:
        return "ALL"
    if path.startswith("/teams/0"):
        return "TEAM_A"
    if path.startswith("/teams/1"):
        return "TEAM_B"
    if path.startswith("/score_events"):
        return "RUNNING_SCORE"
    if path.startswith("/stated_period_scores") or path.startswith("/final_score"):
        return "SUMMARY"
    if path.startswith("/officials") or path.startswith("/recognition/table_personnel"):
        return "OFFICIALS"
    if path.startswith(("/header", "/source", "/game_prior")) or path in {
        "/schema_version",
        "/template_id",
        "/rules_profile",
        "/id",
        "/revision",
        "/status",
        "/created_at",
        "/updated_at",
        "/recognition",
        "/acknowledged_warnings",
    }:
        return "SOURCE_GAME"
    raise ScoresheetDocumentError("DRAFT_PATH_INVALID", f"不能修改未知字段：{path}")


def _decode_pointer_part(value: str) -> str:
    return value.replace("~1", "/").replace("~0", "~")


def _encode_pointer_part(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _json_changes(
    before: Any,
    after: Any,
    path: str = "",
    changes: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    result = changes if changes is not None else []
    if type(before) is type(after) and before == after:
        return result
    if isinstance(before, dict) and isinstance(after, dict):
        for key in sorted(set(before) | set(after)):
            child_path = f"{path}/{_encode_pointer_part(str(key))}"
            if key not in after:
                result.append({"path": child_path, "operation": "DELETE", "value": None})
            elif key not in before:
                result.append(
                    {"path": child_path, "operation": "SET", "value": copy.deepcopy(after[key])}
                )
            else:
                _json_changes(before[key], after[key], child_path, result)
        return result
    if isinstance(before, list) and isinstance(after, list) and len(before) == len(after):
        for index, (left, right) in enumerate(zip(before, after, strict=True)):
            _json_changes(left, right, f"{path}/{index}", result)
        return result
    result.append({"path": path or "/", "operation": "SET", "value": copy.deepcopy(after)})
    return result


def _preserve_server_fields(current: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    prepared = copy.deepcopy(incoming)
    for field in (
        "id",
        "revision",
        "template_id",
        "rules_profile",
        "created_at",
        "updated_at",
        "game_prior",
    ):
        prepared[field] = copy.deepcopy(current.get(field))
    current_source = copy.deepcopy(current.get("source") or {})
    incoming_source = incoming.get("source") if isinstance(incoming.get("source"), dict) else {}
    current_source["rotation"] = incoming_source.get("rotation", current_source.get("rotation", 0))
    current_source["corners"] = incoming_source.get("corners", current_source.get("corners"))
    current_source["original_url"] = ""
    current_source["aligned_url"] = ""
    prepared["source"] = current_source
    current_recognition = current.get("recognition")
    incoming_recognition = incoming.get("recognition")
    if current_recognition is None:
        prepared["recognition"] = None
    elif isinstance(incoming_recognition, dict):
        prepared["recognition"] = copy.deepcopy(current_recognition)
        for key in ("table_personnel", "problem_paths", "issues"):
            if key in incoming_recognition:
                prepared["recognition"][key] = copy.deepcopy(incoming_recognition[key])
    else:
        prepared["recognition"] = copy.deepcopy(current_recognition)
    current_teams = {
        row.get("side"): row for row in current.get("teams", []) if isinstance(row, dict)
    }
    if current.get("game_prior"):
        for key in ("game_number", "competition", "date", "scheduled_time", "venue"):
            prepared["header"][key] = current["header"][key]
        for team in prepared.get("teams", []):
            if isinstance(team, dict) and team.get("side") in current_teams:
                team["name"] = current_teams[team["side"]]["name"]
    prepared["acknowledged_warnings"] = []
    prepared["status"] = "needs_review" if prepared.get("recognition") else "draft"
    return prepared


def _derive_final_score(document: dict[str, Any]) -> None:
    """Keep the canonical result tied to the paper's stated period scores."""
    scores = document.get("stated_period_scores") or []
    team_a = sum(int(row.get("team_a") or 0) for row in scores if isinstance(row, dict))
    team_b = sum(int(row.get("team_b") or 0) for row in scores if isinstance(row, dict))
    teams = {
        row.get("side"): str(row.get("name") or "")
        for row in document.get("teams", [])
        if isinstance(row, dict)
    }
    final = document.setdefault("final_score", {})
    final["team_a"] = team_a
    final["team_b"] = team_b
    final["winner_name"] = (
        teams.get("A", "")
        if team_a > team_b
        else teams.get("B", "")
        if team_b > team_a
        else ""
    )


def _assert_editable_path(document: dict[str, Any], path: str) -> None:
    locked_paths = set((document.get("game_prior") or {}).get("locked_paths") or [])
    if path in locked_paths:
        raise ScoresheetDocumentError(
            "SCORESHEET_FIELD_LOCKED",
            "该比赛资料由赛程和名单确定，不能在记录表中修改。",
        )
    if path in {"/final_score/team_a", "/final_score/team_b", "/final_score/winner_name"}:
        raise ScoresheetDocumentError(
            "SCORESHEET_FIELD_LOCKED",
            "最终比分和胜队由各节比分自动计算，不能直接修改。",
        )


def apply_changes(
    document: dict[str, Any], changes: list[dict[str, Any]]
) -> tuple[dict[str, Any], list[str], list[dict[str, Any]]]:
    if not changes or len(changes) > 200:
        raise ScoresheetDocumentError("DRAFT_CHANGES_INVALID", "每次应提交 1–200 个字段修改。")
    updated = copy.deepcopy(document)
    changed_regions: set[str] = set()
    normalized_changes: list[dict[str, Any]] = []
    for change in changes:
        path = str(change.get("path", ""))
        region = region_for_path(path)
        value = copy.deepcopy(change.get("value"))
        operation = str(change.get("operation", "SET")).upper()
        if path in {"", "/"}:
            if operation != "SET" or not isinstance(value, dict):
                raise ScoresheetDocumentError("DRAFT_ROOT_INVALID", "整表替换必须提交 JSON 对象。")
            candidate = _preserve_server_fields(document, value)
            _derive_final_score(candidate)
            try:
                updated = ScoresheetDocument.model_validate(candidate).model_dump(mode="json")
            except ValidationError as error:
                raise ScoresheetDocumentError("DRAFT_SCHEMA_INVALID", str(error)) from error
            root_changes = [
                change
                for change in _json_changes(document, updated)
                if change["path"]
                not in {"/revision", "/status", "/updated_at", "/acknowledged_warnings"}
            ]
            for root_change in root_changes:
                changed_regions.add(region_for_path(root_change["path"]))
            normalized_changes.extend(root_changes)
            continue
        parts = [_decode_pointer_part(part) for part in path.lstrip("/").split("/")]
        _assert_editable_path(document, path)
        cursor: Any = updated
        for part in parts[:-1]:
            if isinstance(cursor, list):
                try:
                    cursor = cursor[int(part)]
                except (IndexError, ValueError) as error:
                    raise ScoresheetDocumentError(
                        "DRAFT_PATH_INVALID", f"数组字段不存在：{path}"
                    ) from error
            elif isinstance(cursor, dict) and part in cursor:
                cursor = cursor[part]
            else:
                raise ScoresheetDocumentError("DRAFT_PATH_INVALID", f"字段不存在：{path}")
        leaf = parts[-1]
        if isinstance(cursor, list):
            if leaf == "-" and operation == "SET":
                cursor.append(value)
            else:
                try:
                    index = int(leaf)
                    if operation == "DELETE":
                        cursor.pop(index)
                    else:
                        cursor[index] = value
                except (IndexError, ValueError) as error:
                    raise ScoresheetDocumentError(
                        "DRAFT_PATH_INVALID", f"数组字段不存在：{path}"
                    ) from error
        elif isinstance(cursor, dict):
            if operation == "DELETE":
                if leaf not in cursor:
                    raise ScoresheetDocumentError("DRAFT_PATH_INVALID", f"字段不存在：{path}")
                del cursor[leaf]
            elif operation == "SET":
                cursor[leaf] = value
            else:
                raise ScoresheetDocumentError("DRAFT_OPERATION_INVALID", "仅支持 SET 或 DELETE。")
        else:
            raise ScoresheetDocumentError("DRAFT_PATH_INVALID", f"字段不存在：{path}")
        changed_regions.add(region)
        normalized_changes.append({"path": path, "operation": operation, "value": value})
    before_derived = copy.deepcopy(updated)
    _derive_final_score(updated)
    for derived_change in _json_changes(before_derived, updated):
        changed_regions.add(region_for_path(derived_change["path"]))
        normalized_changes.append(derived_change)
    try:
        updated = ScoresheetDocument.model_validate(updated).model_dump(mode="json")
    except ValidationError as error:
        raise ScoresheetDocumentError("DRAFT_SCHEMA_INVALID", str(error)) from error
    return updated, sorted(changed_regions), normalized_changes


def _target_issue(issue: Any) -> dict[str, Any]:
    paths = list(issue.paths)
    path = paths[0] if paths else "/"
    severity = str(issue.severity.value).upper()
    fingerprint = hashlib.sha256(f"{issue.code}|{path}|{issue.message}".encode()).hexdigest()[:16]
    return {
        "id": fingerprint,
        "severity": severity,
        "code": issue.code,
        "region": region_for_path(path),
        "path": path,
        "paths": paths,
        "message": issue.message,
        "context": {"observed": issue.observed, "expected": issue.expected},
    }


def _computed(document: ScoresheetDocument, roster_snapshot: dict[str, Any]) -> dict[str, Any]:
    player_points: dict[str, dict[str, int]] = {}
    by_side_jersey: dict[tuple[str, str], dict[str, Any]] = {}
    for side in ("A", "B"):
        for row in roster_snapshot.get(side, []):
            jersey = str(row.get("jersey_number") or "")
            if jersey:
                by_side_jersey[(side, jersey)] = row
    totals = {"A": 0, "B": 0}
    periods: dict[str, dict[str, int]] = defaultdict(lambda: {"A": 0, "B": 0})
    for event in document.score_events:
        points = int(event.points or 0)
        if points not in {1, 2, 3}:
            continue
        side = event.team.value
        totals[side] += points
        periods[str(event.period)][side] += points
        roster = by_side_jersey.get((side, event.scorer_jersey))
        key = str(roster.get("player_id")) if roster else f"{side}:{event.scorer_jersey}"
        row = player_points.setdefault(
            key,
            {"points": 0, "one_point_events": 0, "two_point_events": 0, "three_point_events": 0},
        )
        row["points"] += points
        row[{1: "one_point_events", 2: "two_point_events", 3: "three_point_events"}[points]] += 1
    return {"team_totals": totals, "period_totals": dict(periods), "player_points": player_points}


def validate_document(document: dict[str, Any], roster_snapshot: dict[str, Any]) -> dict[str, Any]:
    from django.conf import settings

    try:
        model = ScoresheetDocument.model_validate(document)
    except ValidationError as error:
        issue = {
            "id": hashlib.sha256(str(error).encode()).hexdigest()[:16],
            "severity": "ERROR",
            "code": "DOCUMENT_SCHEMA_INVALID",
            "region": "SOURCE_GAME",
            "path": "/",
            "paths": ["/"],
            "message": "记录表草稿结构不完整或字段格式不合法。",
            "context": {"detail": str(error)},
        }
        return {"errors": [issue], "warnings": [], "computed": {}}
    rule_path = settings.BASE_DIR / "core" / "assets" / "scoresheet" / "rule_profiles.json"
    report = validate_v2_document(model, rule_path)
    rows = [_target_issue(issue) for issue in report.issues]
    return {
        "errors": [row for row in rows if row["severity"] == "ERROR"],
        "warnings": [row for row in rows if row["severity"] == "WARNING"],
        "computed": _computed(model, roster_snapshot),
    }


def _canonical_foul(value: Any, slot: int) -> dict[str, Any] | None:
    if isinstance(value, dict) and value.get("code"):
        payload = copy.deepcopy(value)
        payload.setdefault("slot", slot)
        payload.setdefault("catalog_id", None)
        payload.setdefault("mark_style", "plain")
        payload.setdefault("free_throws", None)
        payload.setdefault("cancelled", False)
        payload.setdefault("period", None)
        return payload
    return _old_foul(value, slot)


def _merge_recognized_team(
    base: dict[str, Any], recognized: dict[str, Any], allowed_names: set[str]
) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key in (
        "timeouts",
        "team_fouls",
        "coach_fouls",
        "coach_post_foul_markers",
        "assistant_coach_fouls",
        "assistant_coach_post_foul_markers",
        "head_coach",
        "assistant_coach",
    ):
        if key in recognized:
            merged[key] = copy.deepcopy(recognized[key])
    existing_by_name = {row.get("name"): row for row in merged.get("players", [])}
    existing_by_row = {row.get("row"): row for row in merged.get("players", [])}
    for index, player in enumerate(recognized.get("players") or [], 1):
        if not isinstance(player, dict):
            continue
        name = str(player.get("name") or "")
        if name and name not in allowed_names:
            continue
        row_number = int(player.get("row") or index)
        target = existing_by_name.get(name) or existing_by_row.get(row_number)
        if target is None:
            target = _player_from_roster(row_number, {"display_name": name, "jersey_number": ""})
            merged.setdefault("players", []).append(target)
        for key in ("license_number", "jersey_number", "captain", "participation"):
            if key in player and player[key] is not None:
                target[key] = copy.deepcopy(player[key])
        if "appeared" in player or "starter" in player:
            target["participation"] = (
                "starter"
                if player.get("starter")
                else "substitute"
                if player.get("appeared")
                else "none"
            )
        if "fouls" in player:
            target["fouls"] = [
                foul
                for slot, value in enumerate(player.get("fouls") or [], 1)
                if (foul := _canonical_foul(value, slot)) is not None
            ][:5]
        if "post_foul_markers" in player:
            target["post_foul_markers"] = copy.deepcopy(player["post_foul_markers"])
    merged["players"] = sorted(merged.get("players", []), key=lambda row: row.get("row", 99))[:12]
    return merged


def merge_recognition_result(
    document: dict[str, Any],
    recognition: dict[str, Any],
    roster_snapshot: dict[str, Any],
    *,
    run_id: str = "",
) -> dict[str, Any]:
    result = copy.deepcopy(document)
    recognized = copy.deepcopy(recognition)
    if (
        isinstance(recognized.get("schema_version"), str)
        and isinstance(recognized.get("recognition"), dict)
        and isinstance(recognized.get("teams"), list)
    ):
        # ScoresheetReader's strict recognition contract maps the provider
        # payload into a complete semantic document before it is persisted.
        # Keep all server-owned identity/source/prior fields authoritative while
        # applying that deterministic reconstruction verbatim.
        for field in (
            "id",
            "revision",
            "template_id",
            "rules_profile",
            "created_at",
            "updated_at",
            "source",
            "game_prior",
        ):
            recognized[field] = copy.deepcopy(document.get(field))
        recognized["status"] = "needs_review"
        recognized["acknowledged_warnings"] = []
        try:
            return ScoresheetDocument.model_validate(recognized).model_dump(mode="json")
        except ValidationError as error:
            raise ScoresheetDocumentError("RECOGNITION_SCHEMA_INVALID", str(error)) from error
    if "running_score" in recognized and "score_events" not in recognized:
        recognized["score_events"] = []
        for index, event in enumerate(recognized.get("running_score") or [], 1):
            if not isinstance(event, dict) or event.get("team") not in {"A", "B"}:
                continue
            points = event.get("value")
            cumulative = event.get("cumulative")
            if points not in {1, 2, 3} or not isinstance(cumulative, int):
                continue
            period = 5 if str(event.get("period")) == "OT" else int(event.get("period") or 1)
            recognized["score_events"].append(
                {
                    "sequence": index,
                    "team": event["team"],
                    "period": period,
                    "points": points,
                    "cumulative_score": cumulative,
                    "scorer_jersey": str(event.get("player_number") or ""),
                    "mark": "filled_dot" if points == 1 else "diagonal",
                    "scorer_circled": points == 3,
                    "boundary": {"period": "period_end", "game": "game_end"}.get(
                        str(event.get("boundary")), "none"
                    ),
                    "ink_role": "q1_q3" if period in {1, 3} else "q2_q4_ot",
                }
            )
    if isinstance(recognized.get("header"), dict):
        for key, value in recognized["header"].items():
            if key in result["header"] and value is not None:
                result["header"][key] = str(value)
    raw_teams = recognized.get("teams")
    if isinstance(raw_teams, dict):
        recognized_teams = [dict(raw_teams.get(side) or {}, side=side) for side in ("A", "B")]
    elif isinstance(raw_teams, list):
        recognized_teams = raw_teams
    else:
        recognized_teams = []
    by_side = {row.get("side"): row for row in result["teams"]}
    for team in recognized_teams:
        if not isinstance(team, dict) or team.get("side") not in {"A", "B"}:
            continue
        side = team["side"]
        allowed_names = {
            str(row.get("display_name") or "") for row in roster_snapshot.get(side, [])
        }
        by_side[side] = _merge_recognized_team(by_side[side], team, allowed_names)
    result["teams"] = [by_side["A"], by_side["B"]]
    for key in ("score_events", "stated_period_scores", "final_score", "officials"):
        if key in recognized:
            result[key] = copy.deepcopy(recognized[key])
    result["recognition"] = {
        "run_id": run_id or str(recognized.get("run_id") or "unknown"),
        "notes": str(recognized.get("recognition_notes") or recognized.get("notes") or ""),
        "table_personnel": list(recognized.get("table_personnel") or []),
        "problem_paths": list(recognized.get("problem_paths") or []),
        "issues": list(recognized.get("issues") or []),
        "applied_at": _utc_now(),
    }
    result["status"] = "needs_review"
    result["acknowledged_warnings"] = []
    result = _preserve_server_fields(document, result)
    try:
        return ScoresheetDocument.model_validate(result).model_dump(mode="json")
    except ValidationError as error:
        raise ScoresheetDocumentError("RECOGNITION_SCHEMA_INVALID", str(error)) from error


def document_digest(document: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def region_digest(document: dict[str, Any], region: str) -> str:
    if region == "SOURCE_GAME":
        payload = {
            "header": document.get("header"),
            "source": document.get("source"),
            "game_prior": document.get("game_prior"),
        }
    elif region == "TEAM_A":
        payload = (document.get("teams") or [{}, {}])[0]
    elif region == "TEAM_B":
        payload = (document.get("teams") or [{}, {}])[1]
    elif region == "RUNNING_SCORE":
        payload = document.get("score_events")
    elif region == "SUMMARY":
        payload = {
            "stated_period_scores": document.get("stated_period_scores"),
            "final_score": document.get("final_score"),
        }
    elif region == "OFFICIALS":
        payload = {
            "officials": document.get("officials"),
            "table_personnel": (document.get("recognition") or {}).get("table_personnel"),
        }
    else:
        raise ScoresheetDocumentError("REGION_INVALID", "记录表区域不合法。")
    return document_digest(payload)
