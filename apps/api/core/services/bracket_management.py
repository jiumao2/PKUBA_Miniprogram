from __future__ import annotations

import hashlib
import json
from uuid import UUID

from django.core.serializers.json import DjangoJSONEncoder
from django.db import IntegrityError, transaction

from core.models import Account, AdminAuditLog, Division, Game, GameWinnerFeed, Season

BRACKET_STAGES = (
    Game.Stage.KNOCKOUT,
    Game.Stage.SEMIFINAL,
    Game.Stage.FINAL,
)


class BracketManagementError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def game_winner(game: Game):
    if game.home_score is None or game.away_score is None:
        return None
    if game.home_score == game.away_score:
        return None
    return game.home_team if game.home_score > game.away_score else game.away_team


def _games(division: Division, *, lock: bool = False) -> list[Game]:
    query = (
        Game.objects.filter(division=division, stage__in=BRACKET_STAGES)
        .exclude(status=Game.Status.VOID)
        .select_related("home_team", "away_team", "home_slot", "away_slot", "period")
        .order_by("date", "start_time", "venue_name", "code")
    )
    if lock:
        query = query.select_for_update(of=("self",))
    return list(query)


def _legacy_suggestions(games: list[Game]) -> list[dict[str, object]]:
    by_stage = {stage: [game for game in games if game.stage == stage] for stage in BRACKET_STAGES}
    rows: list[dict[str, object]] = []
    previous: list[Game] = []
    for stage in BRACKET_STAGES:
        current = by_stage[stage]
        if previous and len(previous) == len(current) * 2:
            for index, target in enumerate(current):
                rows.extend(
                    [
                        {
                            "source_game_id": previous[index * 2].id,
                            "target_game_id": target.id,
                            "target_side": GameWinnerFeed.TargetSide.HOME,
                        },
                        {
                            "source_game_id": previous[index * 2 + 1].id,
                            "target_game_id": target.id,
                            "target_side": GameWinnerFeed.TargetSide.AWAY,
                        },
                    ]
                )
        previous = current
    return rows


def _feed_row(feed: GameWinnerFeed) -> dict[str, object]:
    return {
        "id": feed.id,
        "source_game_id": feed.source_game_id,
        "target_game_id": feed.target_game_id,
        "target_side": feed.target_side,
        "applied_winner_id": feed.applied_winner_id,
        "applied_winner_name": (
            feed.applied_winner.name if feed.applied_winner_id else None
        ),
        "applied_source_version": feed.applied_source_version,
        "version": feed.version,
    }


def serialize_bracket_management(division: Division) -> dict[str, object]:
    games = _games(division)
    feeds = list(
        GameWinnerFeed.objects.filter(source_game__division=division)
        .select_related("source_game", "target_game", "applied_winner")
        .order_by("target_game__date", "target_game__start_time", "target_side")
    )
    return {
        "season_id": division.season_id,
        "season_name": division.season.name,
        "season_status": division.season.status,
        "season_version": division.season.version,
        "division_id": division.id,
        "division_name": division.name,
        "division_status": division.operation_status,
        "division_version": division.version,
        "relation_mode": "AUTHORITATIVE" if feeds else "LEGACY_DERIVED",
        "read_only": division.season.status == Season.Status.ARCHIVED,
        "locked_reason": (
            "归档赛季的淘汰赛关系只读。"
            if division.season.status == Season.Status.ARCHIVED
            else ""
        ),
        "games": [
            {
                "id": game.id,
                "code": game.code,
                "stage": game.stage,
                "round_number": game.round_number,
                "date": game.date,
                "start_time": game.start_time.strftime("%H:%M"),
                "home_name": game.home_display,
                "away_name": game.away_display,
                "home_team_id": game.home_team_id,
                "away_team_id": game.away_team_id,
                "home_score": game.home_score,
                "away_score": game.away_score,
                "status": game.status,
                "version": game.version,
            }
            for game in games
        ],
        "feeds": [_feed_row(feed) for feed in feeds],
        "legacy_suggestions": _legacy_suggestions(games) if not feeds else [],
    }


def _normalize_rows(
    *,
    division: Division,
    rows: list[dict[str, object]],
    lock: bool,
) -> tuple[list[dict[str, object]], dict[UUID, Game]]:
    games = _games(division, lock=lock)
    games_by_id = {game.id: game for game in games}
    normalized: list[dict[str, object]] = []
    target_positions: set[tuple[UUID, str]] = set()
    for row in rows:
        try:
            source_id = UUID(str(row["source_game_id"]))
            target_id = UUID(str(row["target_game_id"]))
            target_side = str(row["target_side"])
        except (KeyError, TypeError, ValueError) as error:
            raise BracketManagementError(
                "RELATION_INVALID",
                "淘汰赛关系包含无效的比赛 ID。",
            ) from error
        if target_side not in GameWinnerFeed.TargetSide.values:
            raise BracketManagementError("TARGET_SIDE_INVALID", "目标位置无效。")
        source = games_by_id.get(source_id)
        target = games_by_id.get(target_id)
        if source is None or target is None:
            raise BracketManagementError(
                "GAME_NOT_FOUND",
                "胜者来源和目标比赛必须属于当前组别的淘汰赛。",
            )
        position = (target_id, target_side)
        if position in target_positions:
            raise BracketManagementError(
                "TARGET_SIDE_DUPLICATE",
                "同一目标比赛位置不能接收多个胜者来源。",
            )
        target_positions.add(position)
        probe = GameWinnerFeed(
            source_game=source,
            target_game=target,
            target_side=target_side,
            confirmed_by_id=UUID(int=0),
        )
        try:
            probe.clean()
        except Exception as error:
            raise BracketManagementError("RELATION_INVALID", str(error)) from error
        normalized.append(
            {
                "source_game_id": source_id,
                "target_game_id": target_id,
                "target_side": target_side,
            }
        )

    graph: dict[UUID, list[UUID]] = {}
    for row in normalized:
        graph.setdefault(row["source_game_id"], []).append(row["target_game_id"])
    visiting: set[UUID] = set()
    visited: set[UUID] = set()

    def visit(node: UUID) -> None:
        if node in visiting:
            raise BracketManagementError("RELATION_CYCLE", "淘汰赛关系不能形成循环。")
        if node in visited:
            return
        visiting.add(node)
        for target in graph.get(node, []):
            visit(target)
        visiting.remove(node)
        visited.add(node)

    for node in graph:
        visit(node)
    normalized.sort(
        key=lambda row: (
            games_by_id[row["target_game_id"]].date,
            games_by_id[row["target_game_id"]].start_time,
            row["target_side"],
            games_by_id[row["source_game_id"]].code,
        )
    )
    return normalized, games_by_id


def _analyze(
    *,
    season: Season,
    division: Division,
    expected_season_version: int,
    expected_division_version: int,
    rows: list[dict[str, object]],
    lock: bool,
) -> tuple[dict[str, object], dict[UUID, Game]]:
    if season.status == Season.Status.ARCHIVED:
        raise BracketManagementError("SEASON_ARCHIVED", "已归档赛季只读。")
    if season.version != expected_season_version or division.version != expected_division_version:
        raise BracketManagementError("VERSION_CONFLICT", "淘汰赛数据已变化，请刷新。")
    normalized, games_by_id = _normalize_rows(division=division, rows=rows, lock=lock)
    existing_query = GameWinnerFeed.objects.filter(source_game__division=division).select_related(
        "source_game", "target_game", "applied_winner"
    )
    if lock:
        existing_query = existing_query.select_for_update(of=("self",))
    existing = list(existing_query)
    before_rows = [
        {
            "source_game_id": feed.source_game_id,
            "target_game_id": feed.target_game_id,
            "target_side": feed.target_side,
        }
        for feed in existing
    ]
    before_set = {
        (row["source_game_id"], row["target_game_id"], row["target_side"])
        for row in before_rows
    }
    after_set = {
        (row["source_game_id"], row["target_game_id"], row["target_side"])
        for row in normalized
    }
    removed = before_set - after_set
    changed_applied = [
        feed
        for feed in existing
        if feed.applied_winner_id
        and (feed.source_game_id, feed.target_game_id, feed.target_side) in removed
    ]
    blockers: list[dict[str, object]] = []
    if changed_applied:
        blockers.append(
            {
                "code": "APPLIED_RELATION_REQUIRES_CORRECTION",
                "message": "已向下游应用胜者的关系必须先执行赛果影响纠错。",
                "count": len(changed_applied),
            }
        )
    canonical = {
        "season_id": str(season.id),
        "season_version": season.version,
        "division_id": str(division.id),
        "division_version": division.version,
        "before": before_rows,
        "after": normalized,
        "blockers": blockers,
    }
    impact_hash = hashlib.sha256(
        json.dumps(canonical, cls=DjangoJSONEncoder, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return (
        {
            "season_id": season.id,
            "season_version": season.version,
            "division_id": division.id,
            "division_version": division.version,
            "relation_mode_before": "AUTHORITATIVE" if existing else "LEGACY_DERIVED",
            "relation_mode_after": "AUTHORITATIVE" if normalized else "LEGACY_DERIVED",
            "added_count": len(after_set - before_set),
            "removed_count": len(removed),
            "unchanged_count": len(before_set & after_set),
            "blockers": blockers,
            "can_apply": not blockers,
            "impact_hash": impact_hash,
            "relations": normalized,
        },
        games_by_id,
    )


def preview_bracket_relations(
    *,
    season: Season,
    division: Division,
    expected_season_version: int,
    expected_division_version: int,
    rows: list[dict[str, object]],
) -> dict[str, object]:
    preview, _ = _analyze(
        season=season,
        division=division,
        expected_season_version=expected_season_version,
        expected_division_version=expected_division_version,
        rows=rows,
        lock=False,
    )
    return preview


@transaction.atomic
def apply_bracket_relations(
    *,
    actor: Account,
    season_id: UUID,
    division_id: UUID,
    expected_season_version: int,
    expected_division_version: int,
    rows: list[dict[str, object]],
    impact_hash: str,
) -> dict[str, object]:
    season = Season.objects.select_for_update().filter(id=season_id).first()
    if season is None:
        raise BracketManagementError("SEASON_NOT_FOUND", "赛季不存在。")
    division = (
        Division.objects.select_for_update()
        .select_related("season")
        .filter(id=division_id, season=season)
        .first()
    )
    if division is None:
        raise BracketManagementError("DIVISION_NOT_FOUND", "组别不存在。")
    preview, games_by_id = _analyze(
        season=season,
        division=division,
        expected_season_version=expected_season_version,
        expected_division_version=expected_division_version,
        rows=rows,
        lock=True,
    )
    if preview["impact_hash"] != impact_hash:
        raise BracketManagementError("IMPACT_HASH_MISMATCH", "关系影响已变化，请重新预览。")
    if preview["blockers"]:
        raise BracketManagementError("RELATION_CHANGE_BLOCKED", "关系变更存在阻塞项。")

    before = serialize_bracket_management(division)
    GameWinnerFeed.objects.filter(source_game__division=division).delete()
    for row in preview["relations"]:
        source = games_by_id[row["source_game_id"]]
        target = games_by_id[row["target_game_id"]]
        winner = game_winner(source)
        target_team_id = (
            target.home_team_id
            if row["target_side"] == GameWinnerFeed.TargetSide.HOME
            else target.away_team_id
        )
        feed = GameWinnerFeed(
            source_game=source,
            target_game=target,
            target_side=row["target_side"],
            applied_winner=winner if winner and target_team_id == winner.id else None,
            applied_source_version=(
                source.version if winner and target_team_id == winner.id else None
            ),
            confirmed_by=actor,
        )
        feed.full_clean()
        feed.save()
    division.version += 1
    division.save(update_fields=["version", "updated_at"])
    season.version += 1
    season.save(update_fields=["version", "updated_at"])
    after = serialize_bracket_management(division)
    AdminAuditLog.objects.create(
        actor=actor,
        action="BRACKET_RELATIONS_CONFIRMED",
        object_type="Division",
        object_id=division.id,
        before=json.loads(json.dumps(before, cls=DjangoJSONEncoder)),
        after=json.loads(json.dumps(after, cls=DjangoJSONEncoder)),
        metadata={"impact_hash": impact_hash},
    )
    return after


def relation_integrity_error(error: IntegrityError) -> BracketManagementError:
    return BracketManagementError(
        "RELATION_CONFLICT",
        "淘汰赛关系与并发数据冲突，请刷新后重试。",
    )
