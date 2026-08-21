from __future__ import annotations

import copy
import hashlib
import json
import re
from collections import defaultdict
from typing import Any

from core.models import Game, RosterPlayer

RULE_PROFILE = "fiba_2024"
TEMPLATE_ID = "pku-basketball-2019-v1"

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


def _new_player(player: dict[str, Any]) -> dict[str, Any]:
    return {
        "player_id": player["player_id"],
        "name": player["display_name"],
        "jersey_number": player.get("jersey_number", ""),
        "appeared": False,
        "starter": False,
        "captain": False,
        "fouls": [],
    }


def new_document(
    prior: dict[str, Any], roster: dict[str, list[dict[str, Any]]]
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "template_id": TEMPLATE_ID,
        "rule_profile": RULE_PROFILE,
        "game": {
            "competition": prior["competition"],
            "division": prior["division"],
            "date": prior["date"],
            "scheduled_time": prior["scheduled_time"],
            "game_number": prior["game_code"],
            "venue": prior["venue"],
            "crew_chief": "",
            "umpire_1": "",
            "umpire_2": "",
        },
        "teams": {
            "A": {
                "team_id": prior["team_a"]["team_id"],
                "name": prior["team_a"]["display_name"],
                "players": [_new_player(player) for player in roster.get("A", [])],
                "timeouts": {"H1": [], "H2": [], "OT": []},
                "team_fouls": {"1": [], "2": [], "3": [], "4": []},
                "head_coach": {"name": "", "fouls": []},
                "assistant_coach": {"name": "", "fouls": []},
            },
            "B": {
                "team_id": prior["team_b"]["team_id"],
                "name": prior["team_b"]["display_name"],
                "players": [_new_player(player) for player in roster.get("B", [])],
                "timeouts": {"H1": [], "H2": [], "OT": []},
                "team_fouls": {"1": [], "2": [], "3": [], "4": []},
                "head_coach": {"name": "", "fouls": []},
                "assistant_coach": {"name": "", "fouls": []},
            },
        },
        "running_score": [],
        "summary": {
            "period_scores": {
                "1": {"A": None, "B": None},
                "2": {"A": None, "B": None},
                "3": {"A": None, "B": None},
                "4": {"A": None, "B": None},
                "OT": {"A": None, "B": None},
            },
            "final_score": {"A": None, "B": None},
            "winner_side": "",
            "ended_at": "",
        },
        "officials": {
            "scorer": "",
            "assistant_scorer": "",
            "timer": "",
            "shot_clock_operator": "",
            "crew_chief_signature": False,
            "umpire_1_signature": False,
            "umpire_2_signature": False,
            "captain_protest_signature": False,
        },
        "source_alignment": {"corners": [], "rotation": 0},
    }


def region_for_path(path: str) -> str:
    if path in {"", "/"}:
        return "ALL"
    if path.startswith("/teams/A"):
        return "TEAM_A"
    if path.startswith("/teams/B"):
        return "TEAM_B"
    if path.startswith("/running_score"):
        return "RUNNING_SCORE"
    if path.startswith("/summary"):
        return "SUMMARY"
    if path.startswith("/officials"):
        return "OFFICIALS"
    if path == "/teams":
        return "ALL"
    if path.startswith("/game") or path.startswith("/source_alignment") or path in {
        "/schema_version",
        "/template_id",
        "/rule_profile",
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
            required = {
                "schema_version",
                "template_id",
                "rule_profile",
                "game",
                "teams",
                "running_score",
                "summary",
                "officials",
                "source_alignment",
            }
            if set(value) != required:
                raise ScoresheetDocumentError(
                    "DRAFT_SCHEMA_INVALID", "整表字段必须与当前记录表 Schema 完全一致。"
                )
            updated = value
            root_changes = _json_changes(document, value)
            for root_change in root_changes:
                changed_regions.add(region_for_path(root_change["path"]))
            normalized_changes.extend(root_changes)
            continue
        parts = [_decode_pointer_part(part) for part in path.lstrip("/").split("/")]
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
    if not isinstance(updated, dict):
        raise ScoresheetDocumentError("DRAFT_SCHEMA_INVALID", "记录表草稿必须是 JSON 对象。")
    return updated, sorted(changed_regions), normalized_changes


def _issue(
    severity: str,
    code: str,
    region: str,
    path: str,
    message: str,
    **context: Any,
) -> dict[str, Any]:
    fingerprint = hashlib.sha256(f"{code}|{path}|{message}".encode()).hexdigest()[:16]
    return {
        "id": fingerprint,
        "severity": severity,
        "code": code,
        "region": region,
        "path": path,
        "message": message,
        "context": context,
    }


def _integer(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def validate_document(
    document: dict[str, Any], roster_snapshot: dict[str, Any]
) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    teams = document.get("teams") if isinstance(document.get("teams"), dict) else {}
    running = document.get("running_score")
    summary = document.get("summary") if isinstance(document.get("summary"), dict) else {}
    officials = document.get("officials") if isinstance(document.get("officials"), dict) else {}
    game = document.get("game") if isinstance(document.get("game"), dict) else {}

    if (
        document.get("schema_version") != 1
        or document.get("rule_profile") != RULE_PROFILE
        or document.get("template_id") != TEMPLATE_ID
    ):
        errors.append(
            _issue(
                "ERROR",
                "SCHEMA_VERSION_INVALID",
                "SOURCE_GAME",
                "/schema_version",
                "草稿不是当前 FIBA 2024 记录表格式。",
            )
        )
    for field in ("competition", "date", "scheduled_time", "game_number", "venue"):
        if not str(game.get(field) or "").strip():
            errors.append(
                _issue(
                    "ERROR",
                    "GAME_FIELD_MISSING",
                    "SOURCE_GAME",
                    f"/game/{field}",
                    "比赛信息中的必填字段尚未填写。",
                    field=field,
                )
            )
    alignment = document.get("source_alignment")
    corners = alignment.get("corners") if isinstance(alignment, dict) else None
    rotation = alignment.get("rotation") if isinstance(alignment, dict) else None
    valid_corners = isinstance(corners, list) and len(corners) in {0, 4}
    if valid_corners:
        valid_corners = all(
            isinstance(point, dict)
            and isinstance(point.get("x"), (int, float))
            and not isinstance(point.get("x"), bool)
            and 0 <= point["x"] <= 1
            and isinstance(point.get("y"), (int, float))
            and not isinstance(point.get("y"), bool)
            and 0 <= point["y"] <= 1
            for point in corners
        )
    if not valid_corners or rotation not in {0, 90, 180, 270}:
        errors.append(
            _issue(
                "ERROR",
                "SOURCE_ALIGNMENT_INVALID",
                "SOURCE_GAME",
                "/source_alignment",
                "原图对齐必须为空或包含四个合法归一化角点，旋转角度必须为 90° 的倍数。",
            )
        )

    player_side: dict[str, str] = {}
    player_data: dict[str, dict[str, Any]] = {}
    for side in ("A", "B"):
        team = teams.get(side) if isinstance(teams.get(side), dict) else {}
        region = f"TEAM_{side}"
        players = team.get("players") if isinstance(team.get("players"), list) else []
        expected_ids = {str(row.get("player_id")) for row in roster_snapshot.get(side, [])}
        seen_ids: set[str] = set()
        starter_count = 0
        captain_count = 0
        for index, player in enumerate(players):
            if not isinstance(player, dict):
                errors.append(
                    _issue(
                        "ERROR",
                        "PLAYER_INVALID",
                        region,
                        f"/teams/{side}/players/{index}",
                        "球员行格式无效。",
                    )
                )
                continue
            player_id = str(player.get("player_id") or "")
            if not player_id or player_id not in expected_ids:
                errors.append(
                    _issue(
                        "ERROR",
                        "PLAYER_NOT_IN_FROZEN_ROSTER",
                        region,
                        f"/teams/{side}/players/{index}/player_id",
                        "球员不在上传时冻结的名单中。",
                    )
                )
            elif player_id in seen_ids:
                errors.append(
                    _issue(
                        "ERROR",
                        "PLAYER_DUPLICATED",
                        region,
                        f"/teams/{side}/players/{index}/player_id",
                        "同一球员在记录表中重复出现。",
                    )
                )
            else:
                seen_ids.add(player_id)
                player_side[player_id] = side
                player_data[player_id] = player
            appeared = bool(player.get("appeared"))
            starter = bool(player.get("starter"))
            captain = bool(player.get("captain"))
            starter_count += int(starter)
            captain_count += int(captain)
            if starter and not appeared:
                errors.append(
                    _issue(
                        "ERROR",
                        "STARTER_NOT_APPEARED",
                        region,
                        f"/teams/{side}/players/{index}/starter",
                        "首发球员必须同时标记为出场。",
                    )
                )
            fouls = player.get("fouls")
            if not isinstance(fouls, list) or len(fouls) > 5:
                errors.append(
                    _issue(
                        "ERROR",
                        "PLAYER_FOULS_INVALID",
                        region,
                        f"/teams/{side}/players/{index}/fouls",
                        "个人犯规必须为至多 5 个标记。",
                    )
                )
            elif any(
                not re.fullmatch(
                    r"(?:P|T|U|D)(?:1|2|3|c)?|F",
                    str(foul.get("code") if isinstance(foul, dict) else foul),
                )
                for foul in fouls
            ):
                errors.append(
                    _issue(
                        "ERROR",
                        "PLAYER_FOUL_CODE_INVALID",
                        region,
                        f"/teams/{side}/players/{index}/fouls",
                        "个人犯规标记不符合 FIBA 2024 规则。",
                    )
                )
        if starter_count > 5:
            errors.append(
                _issue(
                    "ERROR",
                    "TOO_MANY_STARTERS",
                    region,
                    f"/teams/{side}/players",
                    "每队首发不能超过 5 人。",
                )
            )
        elif starter_count == 0:
            warnings.append(
                _issue(
                    "WARNING",
                    "STARTERS_MISSING",
                    region,
                    f"/teams/{side}/players",
                    "尚未标记首发球员，请核对纸面。",
                )
            )
        if captain_count > 1:
            errors.append(
                _issue(
                    "ERROR",
                    "MULTIPLE_CAPTAINS",
                    region,
                    f"/teams/{side}/players",
                    "每队最多标记一名场上队长。",
                )
            )
        missing_ids = expected_ids - seen_ids
        if missing_ids:
            errors.append(
                _issue(
                    "ERROR",
                    "ROSTER_PLAYERS_MISSING",
                    region,
                    f"/teams/{side}/players",
                    "草稿缺少上传时冻结名单中的球员行。",
                    missing_count=len(missing_ids),
                )
            )
        timeouts = team.get("timeouts")
        timeout_limits = {"H1": 2, "H2": 3, "OT": 3}
        if not isinstance(timeouts, dict) or any(
            not isinstance(timeouts.get(scope), list)
            or len(timeouts.get(scope, [])) > limit
            for scope, limit in timeout_limits.items()
        ):
            errors.append(
                _issue(
                    "ERROR",
                    "TIMEOUTS_INVALID",
                    region,
                    f"/teams/{side}/timeouts",
                    "暂停格必须按上半场 2 次、下半场 3 次、加时 3 次以内填写。",
                )
            )
        team_fouls = team.get("team_fouls")
        if not isinstance(team_fouls, dict) or any(
            not isinstance(team_fouls.get(period), list)
            or len(team_fouls.get(period, [])) > 4
            for period in ("1", "2", "3", "4")
        ):
            errors.append(
                _issue(
                    "ERROR",
                    "TEAM_FOULS_INVALID",
                    region,
                    f"/teams/{side}/team_fouls",
                    "每节全队犯规最多填写四个格。",
                )
            )
        for coach_key in ("head_coach", "assistant_coach"):
            coach = team.get(coach_key)
            coach_fouls = coach.get("fouls") if isinstance(coach, dict) else None
            if not isinstance(coach, dict) or not isinstance(coach_fouls, list):
                errors.append(
                    _issue(
                        "ERROR",
                        "COACH_INVALID",
                        region,
                        f"/teams/{side}/{coach_key}",
                        "教练姓名和犯规格格式无效。",
                    )
                )
            elif len(coach_fouls) > 3 or any(
                not re.fullmatch(
                    r"(?:C|B|D)(?:1|2|3|c)?|F",
                    str(foul.get("code") if isinstance(foul, dict) else foul),
                )
                for foul in coach_fouls
            ):
                errors.append(
                    _issue(
                        "ERROR",
                        "COACH_FOULS_INVALID",
                        region,
                        f"/teams/{side}/{coach_key}/fouls",
                        "教练犯规必须为至多三个合法 FIBA 2024 标记。",
                    )
                )
        if not expected_ids:
            errors.append(
                _issue(
                    "ERROR",
                    "ROSTER_SNAPSHOT_EMPTY",
                    region,
                    f"/teams/{side}/players",
                    "上传时该队没有可用名单，不能发布。",
                )
            )

    computed_periods: dict[str, dict[str, int]] = defaultdict(lambda: {"A": 0, "B": 0})
    computed_totals = {"A": 0, "B": 0}
    player_points: dict[str, dict[int, int]] = defaultdict(lambda: {1: 0, 2: 0, 3: 0})
    period_rank = {"1": 1, "2": 2, "3": 3, "4": 4, "OT": 5}
    last_period_rank = 0
    seen_event_ids: set[str] = set()
    game_boundary_seen = False
    if not isinstance(running, list):
        errors.append(
            _issue(
                "ERROR",
                "RUNNING_SCORE_INVALID",
                "RUNNING_SCORE",
                "/running_score",
                "逐次得分必须为事件列表。",
            )
        )
        running = []
    for index, event in enumerate(running):
        path = f"/running_score/{index}"
        if not isinstance(event, dict):
            errors.append(
                _issue("ERROR", "SCORE_EVENT_INVALID", "RUNNING_SCORE", path, "得分事件格式无效。")
            )
            continue
        side = event.get("team")
        value = _integer(event.get("value"))
        period = str(event.get("period") or "")
        player_id = str(event.get("player_id") or "")
        event_id = str(event.get("id") or "")
        sequence = _integer(event.get("sequence"))
        boundary = str(event.get("boundary") or "none")
        if not event_id or event_id in seen_event_ids:
            errors.append(
                _issue(
                    "ERROR",
                    "SCORE_EVENT_ID_INVALID",
                    "RUNNING_SCORE",
                    f"{path}/id",
                    "逐次得分事件 ID 不能为空或重复。",
                )
            )
        else:
            seen_event_ids.add(event_id)
        if sequence != index + 1:
            errors.append(
                _issue(
                    "ERROR",
                    "SCORE_EVENT_SEQUENCE_INVALID",
                    "RUNNING_SCORE",
                    f"{path}/sequence",
                    "逐次得分序号必须连续且与纸面顺序一致。",
                )
            )
        if side not in {"A", "B"} or value not in {1, 2, 3} or period not in {
            "1",
            "2",
            "3",
            "4",
            "OT",
        }:
            errors.append(
                _issue(
                    "ERROR",
                    "SCORE_EVENT_VALUE_INVALID",
                    "RUNNING_SCORE",
                    path,
                    "得分事件必须包含球队、1/2/3 分和合法节次。",
                )
            )
            continue
        if period_rank[period] < last_period_rank:
            errors.append(
                _issue(
                    "ERROR",
                    "SCORE_PERIOD_REGRESSION",
                    "RUNNING_SCORE",
                    f"{path}/period",
                    "逐次得分节次不能倒退。",
                )
            )
        last_period_rank = max(last_period_rank, period_rank[period])
        if game_boundary_seen:
            errors.append(
                _issue(
                    "ERROR",
                    "SCORE_AFTER_GAME_END",
                    "RUNNING_SCORE",
                    path,
                    "终场标记之后不能再有得分事件。",
                )
            )
        if boundary not in {"none", "period", "game"}:
            errors.append(
                _issue(
                    "ERROR",
                    "SCORE_BOUNDARY_INVALID",
                    "RUNNING_SCORE",
                    f"{path}/boundary",
                    "节末/终场标记无效。",
                )
            )
        elif boundary == "game":
            game_boundary_seen = True
        computed_totals[side] += value
        computed_periods[period][side] += value
        paper_cumulative = _integer(event.get("cumulative"))
        if paper_cumulative is None:
            errors.append(
                _issue(
                    "ERROR",
                    "RUNNING_SCORE_CUMULATIVE_MISSING",
                    "RUNNING_SCORE",
                    f"{path}/cumulative",
                    "逐次得分事件缺少纸面累计分。",
                )
            )
        elif paper_cumulative > 160:
            errors.append(
                _issue(
                    "ERROR",
                    "RUNNING_SCORE_LIMIT_EXCEEDED",
                    "RUNNING_SCORE",
                    f"{path}/cumulative",
                    "单队纸面累计分不能超过 160 分。",
                )
            )
        elif paper_cumulative != computed_totals[side]:
            errors.append(
                _issue(
                    "ERROR",
                    "RUNNING_SCORE_CUMULATIVE_MISMATCH",
                    "RUNNING_SCORE",
                    f"{path}/cumulative",
                    f"纸面累计分 {paper_cumulative} 与事件累计 {computed_totals[side]} 不一致。",
                    expected=computed_totals[side],
                    actual=paper_cumulative,
                )
            )
        expected_mark = {1: "dot", 2: "slash", 3: "circle"}[value]
        if event.get("mark") not in {None, "", expected_mark}:
            warnings.append(
                _issue(
                    "WARNING",
                    "SCORE_MARK_MISMATCH",
                    "RUNNING_SCORE",
                    f"{path}/mark",
                    "得分符号与 1/2/3 分值不一致，请核对纸面。",
                )
            )
        if not player_id or player_side.get(player_id) != side:
            warnings.append(
                _issue(
                    "WARNING",
                    "SCORER_UNRESOLVED",
                    "RUNNING_SCORE",
                    f"{path}/player_id",
                    "该得分尚未关联本队冻结名单中的球员。",
                )
            )
        else:
            player_points[player_id][value] += 1
            if not bool(player_data[player_id].get("appeared")):
                warnings.append(
                    _issue(
                        "WARNING",
                        "SCORER_NOT_APPEARED",
                        "RUNNING_SCORE",
                        f"{path}/player_id",
                        "得分球员尚未标记出场。",
                    )
                )

    if running and not game_boundary_seen:
        warnings.append(
            _issue(
                "WARNING",
                "GAME_BOUNDARY_MISSING",
                "RUNNING_SCORE",
                "/running_score",
                "逐次得分尚未标记终场位置，请核对纸面。",
            )
        )

    paper_periods = summary.get("period_scores")
    if not isinstance(paper_periods, dict):
        paper_periods = {}
    for period in ("1", "2", "3", "4", "OT"):
        row = paper_periods.get(period)
        if not isinstance(row, dict):
            row = {}
        for side in ("A", "B"):
            paper = _integer(row.get(side))
            computed = computed_periods[period][side]
            if paper is None:
                warnings.append(
                    _issue(
                        "WARNING",
                        "PERIOD_SCORE_MISSING",
                        "SUMMARY",
                        f"/summary/period_scores/{period}/{side}",
                        "纸面节比分尚未填写。",
                    )
                )
            elif paper != computed:
                errors.append(
                    _issue(
                        "ERROR",
                        "PERIOD_SCORE_MISMATCH",
                        "SUMMARY",
                        f"/summary/period_scores/{period}/{side}",
                        f"纸面节比分 {paper} 与逐次得分合计 {computed} 不一致。",
                        expected=computed,
                        actual=paper,
                    )
                )

    final_score = summary.get("final_score")
    if not isinstance(final_score, dict):
        final_score = {}
    final_values: dict[str, int | None] = {
        side: _integer(final_score.get(side)) for side in ("A", "B")
    }
    for side in ("A", "B"):
        paper = final_values[side]
        if paper is None:
            errors.append(
                _issue(
                    "ERROR",
                    "FINAL_SCORE_MISSING",
                    "SUMMARY",
                    f"/summary/final_score/{side}",
                    "正式比分不能为空。",
                )
            )
        elif paper != computed_totals[side]:
            errors.append(
                _issue(
                    "ERROR",
                    "FINAL_SCORE_MISMATCH",
                    "SUMMARY",
                    f"/summary/final_score/{side}",
                    f"纸面最终比分 {paper} 与逐次得分合计 {computed_totals[side]} 不一致。",
                    expected=computed_totals[side],
                    actual=paper,
                )
            )
    if final_values["A"] is not None and final_values["A"] == final_values["B"]:
        errors.append(
            _issue(
                "ERROR",
                "FINAL_SCORE_TIED",
                "SUMMARY",
                "/summary/final_score",
                "正式比分不允许平局。",
            )
        )
    winner_side = summary.get("winner_side")
    expected_winner = (
        "A"
        if final_values["A"] is not None
        and final_values["B"] is not None
        and final_values["A"] > final_values["B"]
        else "B"
        if final_values["A"] is not None
        and final_values["B"] is not None
        and final_values["B"] > final_values["A"]
        else ""
    )
    if winner_side not in {"", expected_winner}:
        errors.append(
            _issue(
                "ERROR",
                "WINNER_SIDE_MISMATCH",
                "SUMMARY",
                "/summary/winner_side",
                "纸面胜队与最终比分不一致。",
            )
        )

    for field in ("scorer", "timer"):
        if not str(officials.get(field) or "").strip():
            warnings.append(
                _issue(
                    "WARNING",
                    "OFFICIAL_MISSING",
                    "OFFICIALS",
                    f"/officials/{field}",
                    "工作人员姓名尚未填写。",
                    field=field,
                )
            )
    if not officials.get("crew_chief_signature"):
        warnings.append(
            _issue(
                "WARNING",
                "CREW_CHIEF_SIGNATURE_MISSING",
                "OFFICIALS",
                "/officials/crew_chief_signature",
                "尚未确认主裁判签名。",
            )
        )

    return {
        "errors": errors,
        "warnings": warnings,
        "computed": {
            "period_scores": {
                period: computed_periods[period]
                for period in ("1", "2", "3", "4", "OT")
            },
            "final_score": computed_totals,
            "player_points": {
                player_id: {
                    "one_point_events": values[1],
                    "two_point_events": values[2],
                    "three_point_events": values[3],
                    "points": values[1] + 2 * values[2] + 3 * values[3],
                }
                for player_id, values in player_points.items()
            },
        },
    }


def merge_recognition_result(
    document: dict[str, Any], result: dict[str, Any], roster_snapshot: dict[str, Any]
) -> dict[str, Any]:
    """Merge provider output while restoring stable player IDs from frozen names."""

    merged = copy.deepcopy(document)
    for key in ("game", "running_score", "summary", "officials", "source_alignment"):
        if key in result:
            merged[key] = copy.deepcopy(result[key])
    recognized_teams = result.get("teams")
    if isinstance(recognized_teams, dict):
        for side in ("A", "B"):
            target = merged["teams"][side]
            source = recognized_teams.get(side)
            if not isinstance(source, dict):
                continue
            for key in ("timeouts", "team_fouls", "head_coach", "assistant_coach"):
                if key in source:
                    target[key] = copy.deepcopy(source[key])
            by_name = {
                str(row.get("display_name", "")).strip(): row
                for row in roster_snapshot.get(side, [])
            }
            by_number = {
                str(row.get("jersey_number", "")).strip(): row
                for row in roster_snapshot.get(side, [])
                if str(row.get("jersey_number", "")).strip()
            }
            recognized_players = source.get("players")
            if isinstance(recognized_players, list):
                existing = {row["player_id"]: row for row in target.get("players", [])}
                for player in recognized_players:
                    if not isinstance(player, dict):
                        continue
                    match = by_name.get(str(player.get("name", "")).strip()) or by_number.get(
                        str(player.get("jersey_number", "")).strip()
                    )
                    if not match:
                        continue
                    row = existing.get(match["player_id"], _new_player(match))
                    for key in ("appeared", "starter", "captain", "fouls"):
                        if key in player:
                            row[key] = copy.deepcopy(player[key])
                    existing[match["player_id"]] = row
                target["players"] = list(existing.values())

    names_to_ids: dict[tuple[str, str], str] = {}
    numbers_to_ids: dict[tuple[str, str], str] = {}
    for side in ("A", "B"):
        for player in roster_snapshot.get(side, []):
            names_to_ids[(side, str(player.get("display_name", "")).strip())] = player["player_id"]
            number = str(player.get("jersey_number", "")).strip()
            if number:
                numbers_to_ids[(side, number)] = player["player_id"]
    if isinstance(merged.get("running_score"), list):
        for index, event in enumerate(merged["running_score"]):
            if not isinstance(event, dict):
                continue
            event.setdefault("id", f"recognized-{index + 1}")
            event.setdefault("sequence", index + 1)
            side = str(event.get("team") or "")
            if not event.get("player_id"):
                event["player_id"] = names_to_ids.get(
                    (side, str(event.get("player_name", "")).strip())
                ) or numbers_to_ids.get((side, str(event.get("player_number", "")).strip()), "")
    return merged


def document_digest(document: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def region_digest(document: dict[str, Any], region: str) -> str:
    if region == "SOURCE_GAME":
        value: Any = {
            "schema_version": document.get("schema_version"),
            "template_id": document.get("template_id"),
            "rule_profile": document.get("rule_profile"),
            "game": document.get("game"),
            "source_alignment": document.get("source_alignment"),
        }
    elif region in {"TEAM_A", "TEAM_B"}:
        value = (document.get("teams") or {}).get(region[-1])
    elif region == "RUNNING_SCORE":
        value = document.get("running_score")
    elif region == "SUMMARY":
        value = document.get("summary")
    elif region == "OFFICIALS":
        value = document.get("officials")
    else:
        raise ScoresheetDocumentError("REGION_INVALID", "记录表区域不合法。")
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
