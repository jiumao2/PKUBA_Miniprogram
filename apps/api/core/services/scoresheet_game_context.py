"""Current match context is not the immutable image/recognition evidence.

Never use Game.version as a semantic version: locks, policy edits and publishing
also advance it. Old priors without association evidence require one explicit
review; they are not silently filled from today's database.
"""

from __future__ import annotations

import copy
from typing import Any

from django.core import signing

from core.models import Game, GameScoresheet
from core.scoresheet_schema_v2 import (
    document_digest,
    game_prior_snapshot,
    roster_prior_snapshot,
    validate_document,
)

CONTEXT_SALT = "pkuba.scoresheet.game-context.v1"
CONTEXT_MAX_AGE = 600
CONTEXT_KEYS = (
    "game_id",
    "game_code",
    "competition",
    "division",
    "date",
    "scheduled_time",
    "venue",
    "team_a",
    "team_b",
    "season_id",
    "division_id",
    "period_id",
    "period_name",
    "stage",
    "round_number",
    "group_id",
)
ASSOCIATION_KEYS = (
    "season_id",
    "division_id",
    "period_id",
    "period_name",
    "stage",
    "round_number",
    "group_id",
)


def current_context(game: Game) -> dict[str, Any]:
    prior = game_prior_snapshot(game)
    return {
        "game": {key: prior.get(key) for key in CONTEXT_KEYS},
        "roster": roster_prior_snapshot(game),
    }


def current_context_for_scoresheet(sheet: GameScoresheet) -> dict[str, Any]:
    """Return the reviewed correction context without changing the public Game."""

    from core.services.competition_corrections import proposed_game_for_correction

    game = proposed_game_for_correction(sheet.game, sheet.pending_correction)
    return current_context(game)


def review_binding(sheet: GameScoresheet, context: dict[str, Any]) -> dict[str, Any]:
    return {
        "scoresheet_id": str(sheet.id),
        "source_asset_id": str(sheet.source_asset_id),
        "source_version": sheet.source_version,
        "draft_version": sheet.draft_version,
        "draft_digest": document_digest(sheet.draft),
        "prior_digest": document_digest(sheet.game_prior_snapshot),
        "roster_digest": document_digest(sheet.roster_snapshot),
        "current_context_digest": document_digest(context),
    }


def _roster_label(rows: list[dict[str, Any]]) -> str:
    return (
        "、".join(
            f"{row['display_name']}（{row.get('jersey_number') or '未填号码'}"
            f"{'，不可参赛' if not row.get('eligible', True) else ''}）"
            for row in rows
        )
        or "暂无启用球员"
    )


def resolve_player(prior: dict, roster: list[dict], side: str, player: dict) -> dict | None:
    """Use an explicitly reviewed identity, or a unique name/jersey match.

    An explicit row/name binding follows the stable player ID. The number saved
    with that binding is review evidence, not an identity key: match-local number
    edits must never fall back to a different same-name player. A removed or
    renamed bound player still fails resolution against the current roster.
    """
    name = str(player.get("name") or "").strip()
    number = str(player.get("jersey_number") or "")
    for mapping in prior.get("confirmed_player_bindings", []):
        if (mapping["side"], mapping["row"], mapping["name"]) == (
            side,
            player["row"],
            name,
        ):
            return next(
                (
                    row
                    for row in roster
                    if row["player_id"] == mapping["player_id"] and row["display_name"] == name
                ),
                None,
            )
    matches = [row for row in roster if row["display_name"] == name]
    if len(matches) > 1:
        matches = [row for row in matches if str(row.get("jersey_number") or "") == number]
    return matches[0] if len(matches) == 1 else None


def context_differences(sheet: GameScoresheet, context: dict[str, Any]) -> list[dict]:
    old, new = sheet.game_prior_snapshot, context["game"]
    differences = []

    def add(field, label, before, after):
        if before != after:
            differences.append(
                {
                    "field": field,
                    "label": label,
                    "before": str(before or "未填写"),
                    "after": str(after or "未填写"),
                }
            )

    for key, label in (
        ("competition", "赛季"),
        ("division", "组别"),
        ("date", "日期"),
        ("scheduled_time", "开赛时间"),
        ("venue", "场地"),
    ):
        add(key, label, old.get(key), new.get(key))
    for key, label in (("team_a", "A 队"), ("team_b", "B 队")):
        before, after = old.get(key, {}), new[key]
        if before != after:
            differences.append(
                {
                    "field": key,
                    "label": label,
                    "before": before.get("display_name") or "未落位",
                    "after": after["display_name"]
                    + ("（球队身份已变更）" if before.get("team_id") != after["team_id"] else ""),
                }
            )
    if any(key not in old for key in ASSOCIATION_KEYS):
        differences.append(
            {
                "field": "legacy_associations",
                "label": "历史关联依据",
                "before": "旧快照未完整记录组别、时段和阶段的关联依据",
                "after": f"{new['competition']} · {new['division']} · {new['period_name']} · "
                f"{Game.Stage(new['stage']).label} 第 {new['round_number']} 轮；请核对一次",
            }
        )
    else:
        if old["period_id"] != new["period_id"] or old["period_name"] != new["period_name"]:
            differences.append(
                {
                    "field": "period",
                    "label": "比赛时段",
                    "before": old["period_name"],
                    "after": new["period_name"] + "（当前时段）",
                }
            )
        for key, label in (
            ("season_id", "赛季归属"),
            ("division_id", "组别归属"),
            ("group_id", "小组归属"),
            ("stage", "赛事阶段"),
            ("round_number", "比赛轮次"),
        ):
            if old[key] != new[key]:
                before, after = "原先关联", "关联已变化，请核对比赛"
                if key == "stage":
                    before = Game.Stage(old[key]).label
                    after = Game.Stage(new[key]).label
                elif key == "round_number":
                    before, after = f"第 {old[key]} 轮", f"第 {new[key]} 轮"
                elif key == "season_id":
                    before, after = old["competition"], new["competition"]
                elif key == "division_id":
                    before, after = old["division"], new["division"]
                elif key == "group_id":
                    before = "原先小组" if old[key] else "未分小组"
                    after = "当前小组（归属已变化）" if new[key] else "未分小组"
                differences.append(
                    {
                        "field": key,
                        "label": label,
                        "before": before,
                        "after": after,
                    }
                )
    if old.get("game_id") != new["game_id"] or old.get("game_code") != new["game_code"]:
        differences.append(
            {
                "field": "game_identity",
                "label": "比赛标识",
                "before": "原先比赛",
                "after": "标识已变化，请核对比赛",
            }
        )
    for side in ("A", "B"):
        before, after = sheet.roster_snapshot.get(side, []), context["roster"].get(side, [])
        if before != after:
            differences.append(
                {
                    "field": f"roster_{side}",
                    "label": f"{side} 队名单",
                    "before": _roster_label(before),
                    "after": _roster_label(after),
                }
            )
    return differences


def player_conflicts(sheet: GameScoresheet, context: dict[str, Any]) -> list[dict]:
    """Changing team/player IDs must not silently reattribute existing points.

    Only an explicit row mapping or editing the row's name resolves this guard.
    Blank rows do not hold any player identity. Jersey numbers remain game-local.
    """
    previous = sheet.game_prior_snapshot
    persisted = previous.get("unresolved_player_bindings", [])
    result = []
    for team in sheet.draft.get("teams", []):
        side = team["side"]
        key = "team_a" if side == "A" else "team_b"
        old_roster = sheet.roster_snapshot.get(side, [])
        roster = context["roster"].get(side, [])
        for player in team.get("players", []):
            name = str(player.get("name") or "").strip()
            if not name:
                continue
            row = player["row"]
            old = resolve_player(previous, old_roster, side, player)
            now = resolve_player(previous, roster, side, player)
            retained = any(
                p["side"] == side and p["row"] == row and p["name"] == name for p in persisted
            )
            team_changed = previous.get(key, {}).get("team_id") != context["game"][key]["team_id"]
            identity_changed = old is not None and (
                now is None or old["player_id"] != now["player_id"]
            )
            ambiguous = now is None and sum(p["display_name"] == name for p in roster) > 1
            if retained or team_changed or identity_changed or ambiguous:
                result.append(
                    {
                        "side": side,
                        "row": row,
                        "name": name,
                        "choices": [
                            {
                                "id": p["player_id"],
                                "name": p["display_name"],
                                "label": (
                                    f"{p['display_name']} · 名单号码 "
                                    f"{p.get('jersey_number') or '未填'}"
                                ),
                            }
                            for p in roster
                            if p.get("eligible", True)
                        ],
                    }
                )
    return result


def context_review(sheet: GameScoresheet, context: dict[str, Any]) -> dict[str, Any]:
    differences = context_differences(sheet, context)
    conflicts = player_conflicts(sheet, context)
    required = bool(differences or conflicts)
    return {
        "required": required,
        "differences": differences,
        "player_conflicts": conflicts,
        "review_token": signing.dumps(review_binding(sheet, context), salt=CONTEXT_SALT)
        if required
        else None,
    }


def validation_with_context(sheet: GameScoresheet, context: dict[str, Any]) -> dict[str, Any]:
    # A jersey belongs to this match, not to the season roster. Associate points
    # through a uniquely matched authoritative player, never an old jersey alone.
    match_roster = {"A": [], "B": []}
    for team in sheet.draft.get("teams", []):
        for player in team.get("players", []):
            match = resolve_player(
                sheet.game_prior_snapshot,
                context["roster"].get(team["side"], []),
                team["side"],
                player,
            )
            if match is not None:
                match_roster[team["side"]].append(
                    {**match, "jersey_number": player.get("jersey_number")}
                )
    report = validate_document(sheet.draft, match_roster)
    review = context_review(sheet, context)
    if review["required"]:
        report["errors"].append(
            {
                "id": "game-context-review-required",
                "severity": "ERROR",
                "code": "GAME_CONTEXT_REVIEW_REQUIRED",
                "region": "SOURCE_GAME",
                "path": "/header",
                "paths": ["/header"],
                "message": "比赛信息或名单需要重新核对。原图和人工编辑已保留，请查看差异。",
            }
        )
    # Recognition/manual typing is never authority for roster membership.
    for team in sheet.draft.get("teams", []):
        side = team["side"]
        roster = context["roster"].get(side, [])
        used_ids = set()
        for index, player in enumerate(team.get("players", [])):
            name = str(player.get("name") or "").strip()
            if not name:
                continue
            match = resolve_player(sheet.game_prior_snapshot, roster, side, player)
            if match is None or not match.get("eligible", True) or match["player_id"] in used_ids:
                path = f"/teams/{0 if side == 'A' else 1}/players/{index}/name"
                report["errors"].append(
                    {
                        "id": f"current-roster-{side}-{player['row']}",
                        "severity": "ERROR",
                        "code": "CURRENT_ROSTER_MISMATCH",
                        "region": f"TEAM_{side}",
                        "path": path,
                        "paths": [path],
                        "message": (
                            f"{side} 队第 {player['row']} 行“{name}”"
                            "不能唯一匹配当前可参赛名单，请修正。"
                        ),
                    }
                )
            if match is not None:
                used_ids.add(match["player_id"])
    report["game_context"] = review
    report["current_context_digest"] = document_digest(context)
    return report


def reviewed_document(sheet: GameScoresheet, prior: dict, roster: dict) -> dict:
    # This is a trusted context update, not recognition/reupload. In particular,
    # do not roundtrip/canonicalize recognition evidence or replace manual rows.
    from core.scoresheet_schema_v2 import _source_prior

    draft = copy.deepcopy(sheet.draft)
    draft["game_prior"] = _source_prior(prior, roster)
    draft["header"].update(
        {
            "competition": prior["competition"],
            "game_number": prior["game_code"],
            "date": prior["date"],
            "scheduled_time": prior["scheduled_time"],
            "venue": prior["venue"],
        }
    )
    for team in draft["teams"]:
        key = "team_a" if team["side"] == "A" else "team_b"
        team["name"] = prior[key]["display_name"]
    final = draft.get("final_score", {})
    a, b = final.get("team_a"), final.get("team_b")
    if isinstance(a, int) and isinstance(b, int) and a != b:
        final["winner_name"] = prior["team_a" if a > b else "team_b"]["display_name"]
    return draft
