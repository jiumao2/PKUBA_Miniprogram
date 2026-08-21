from __future__ import annotations

import hashlib
import json
from uuid import UUID

from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction

from core.models import (
    Account,
    AdminAuditLog,
    Game,
    GameScoresheet,
    GameWinnerFeed,
    RescheduleRequest,
    ScoresheetEditLease,
    Season,
    SlotReservation,
)


class GameResultError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def winner_team(game: Game):
    if game.home_score is None or game.away_score is None:
        return None
    if game.home_score == game.away_score:
        raise GameResultError("TIED_RESULT", "正式比分不允许平局。")
    return game.home_team if game.home_score > game.away_score else game.away_team


def propagate_winner_locked(
    *,
    source_game: Game,
    actor: Account,
    visited: set[UUID] | None = None,
) -> list[dict[str, object]]:
    """Apply an authoritative winner feed inside the caller's transaction."""

    winner = winner_team(source_game)
    if winner is None:
        return []
    visited = visited or set()
    if source_game.id in visited:
        raise GameResultError("RELATION_CYCLE", "淘汰赛胜者关系形成循环。")
    visited.add(source_game.id)
    changes: list[dict[str, object]] = []
    feeds = list(
        GameWinnerFeed.objects.select_for_update(of=("self",))
        .filter(source_game=source_game)
        .select_related("target_game", "applied_winner")
        .order_by("target_game__date", "target_game__start_time", "target_side")
    )
    for feed in feeds:
        if feed.applied_winner_id and feed.applied_winner_id != winner.id:
            raise GameResultError(
                "DOWNSTREAM_CORRECTION_REQUIRED",
                "胜者发生变化，必须先预览并确认级联重置下游对阵。",
            )
        target = (
            Game.objects.select_for_update(of=("self",))
            .select_related("home_team", "away_team")
            .get(id=feed.target_game_id)
        )
        attribute = (
            "home_team"
            if feed.target_side == GameWinnerFeed.TargetSide.HOME
            else "away_team"
        )
        current_team_id = getattr(target, f"{attribute}_id")
        if current_team_id not in {None, winner.id, feed.applied_winner_id}:
            raise GameResultError(
                "TARGET_PARTICIPANT_CONFLICT",
                f"{target.code} 的目标位置已有其他球队，必须先预览并执行级联纠错。",
            )
        participant_changed = current_team_id != winner.id
        if participant_changed and (
            target.home_score is not None
            or target.away_score is not None
            or target.status in {Game.Status.COMPLETED, Game.Status.FORFEIT}
        ):
            raise GameResultError(
                "DOWNSTREAM_RESULT_EXISTS",
                f"{target.code} 已有赛果，必须先预览并执行级联纠错。",
            )
        before = {
            "target_game_id": str(target.id),
            "target_side": feed.target_side,
            "team_id": str(current_team_id) if current_team_id else None,
            "feed_version": feed.version,
            "target_game_version": target.version,
        }
        if participant_changed:
            setattr(target, attribute, winner)
            target.version += 1
            target.full_clean()
            target.save(update_fields=[attribute, "version", "updated_at"])
        feed_changed = (
            feed.applied_winner_id != winner.id
            or feed.applied_source_version != source_game.version
        )
        if feed_changed:
            feed.applied_winner = winner
            feed.applied_source_version = source_game.version
            feed.version += 1
            feed.save(
                update_fields=[
                    "applied_winner",
                    "applied_source_version",
                    "version",
                    "updated_at",
                ]
            )
        if participant_changed or feed_changed:
            after = {
                "target_game_id": str(target.id),
                "target_side": feed.target_side,
                "team_id": str(winner.id),
                "feed_version": feed.version,
                "target_game_version": target.version,
            }
            changes.append(after)
            AdminAuditLog.objects.create(
                actor=actor,
                action="GAME_WINNER_PROPAGATED",
                object_type="GameWinnerFeed",
                object_id=feed.id,
                before=before,
                after=after,
                metadata={
                    "source_game_id": str(source_game.id),
                    "source_game_version": source_game.version,
                },
            )
        if target.home_score is not None and target.away_score is not None:
            changes.extend(
                propagate_winner_locked(
                    source_game=target,
                    actor=actor,
                    visited=visited,
                )
            )
    visited.remove(source_game.id)
    return changes


@transaction.atomic
def propagate_existing_result(*, game_id: UUID, actor: Account) -> list[dict[str, object]]:
    game = (
        Game.objects.select_for_update(of=("self",))
        .select_related("home_team", "away_team")
        .get(id=game_id)
    )
    return propagate_winner_locked(source_game=game, actor=actor)


def _correction_analysis(
    *,
    source_game: Game,
    expected_game_version: int,
    lock: bool,
) -> tuple[dict[str, object], list[Game], list[GameWinnerFeed]]:
    if source_game.season.status == Season.Status.ARCHIVED:
        raise GameResultError("SEASON_ARCHIVED", "已归档赛季只读。")
    if source_game.version != expected_game_version:
        raise GameResultError("VERSION_CONFLICT", "比赛赛果已变化，请刷新后重试。")
    discovered = {source_game.id}
    frontier = {source_game.id}
    feeds: list[GameWinnerFeed] = []
    while frontier:
        query = GameWinnerFeed.objects.filter(source_game_id__in=frontier).select_related(
            "source_game", "target_game", "applied_winner"
        )
        if lock:
            query = query.select_for_update(of=("self",))
        next_frontier: set[UUID] = set()
        for feed in query:
            feeds.append(feed)
            if feed.target_game_id not in discovered:
                discovered.add(feed.target_game_id)
                next_frontier.add(feed.target_game_id)
        frontier = next_frontier
    descendant_ids = discovered - {source_game.id}
    game_query = (
        Game.objects.filter(id__in=descendant_ids)
        .select_related("home_team", "away_team", "home_slot", "away_slot")
        .order_by("date", "start_time", "code")
    )
    if lock:
        game_query = game_query.select_for_update(of=("self",))
    descendants = list(game_query)
    games_by_id = {game.id: game for game in descendants}
    blockers: list[dict[str, object]] = []
    missing_slots = 0
    for feed in feeds:
        target = games_by_id.get(feed.target_game_id)
        if target is None or feed.applied_winner_id is None:
            continue
        slot_id = (
            target.home_slot_id
            if feed.target_side == GameWinnerFeed.TargetSide.HOME
            else target.away_slot_id
        )
        if slot_id is None:
            missing_slots += 1
    if missing_slots:
        blockers.append(
            {
                "code": "TARGET_PLACEHOLDER_MISSING",
                "message": "部分下游位置没有可恢复的占位签位，需先人工修复赛程。",
                "count": missing_slots,
            }
        )
    affected_ids = descendant_ids | {source_game.id}
    terminal = list(RescheduleRequest.TERMINAL_STATUSES)
    active_requests = list(
        RescheduleRequest.objects.filter(game_id__in=affected_ids)
        .exclude(status__in=terminal)
        .values("id", "version", "game_id", "status")
    )
    reservations = SlotReservation.objects.filter(
        request__id__in=[row["id"] for row in active_requests],
        status=SlotReservation.Status.ACTIVE,
    ).count()
    scoresheets = {
        row["game_id"]: row
        for row in GameScoresheet.objects.filter(game_id__in=descendant_ids).values(
            "id",
            "game_id",
            "status",
            "current_publication_id",
            "draft_version",
        )
    }
    canonical = {
        "source_game_id": str(source_game.id),
        "source_game_version": source_game.version,
        "descendants": [
            {
                "id": str(game.id),
                "version": game.version,
                "home_team_id": str(game.home_team_id) if game.home_team_id else None,
                "away_team_id": str(game.away_team_id) if game.away_team_id else None,
                "home_score": game.home_score,
                "away_score": game.away_score,
                "status": game.status,
                "current_publication_id": (
                    str(scoresheets[game.id]["current_publication_id"])
                    if game.id in scoresheets and scoresheets[game.id]["current_publication_id"]
                    else None
                ),
            }
            for game in descendants
        ],
        "feeds": [
            {
                "id": str(feed.id),
                "version": feed.version,
                "source_game_id": str(feed.source_game_id),
                "target_game_id": str(feed.target_game_id),
                "target_side": feed.target_side,
                "applied_winner_id": (
                    str(feed.applied_winner_id) if feed.applied_winner_id else None
                ),
            }
            for feed in feeds
        ],
        "active_requests": [
            {
                **row,
                "id": str(row["id"]),
                "game_id": str(row["game_id"]),
            }
            for row in active_requests
        ],
        "blockers": blockers,
    }
    impact_hash = hashlib.sha256(
        json.dumps(canonical, cls=DjangoJSONEncoder, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return (
        {
            "source_game_id": source_game.id,
            "source_game_version": source_game.version,
            "affected_games": canonical["descendants"],
            "affected_game_count": len(descendants),
            "affected_feed_count": len(feeds),
            "active_request_count": len(active_requests),
            "active_reservation_count": reservations,
            "publication_count": sum(
                1 for row in scoresheets.values() if row["current_publication_id"]
            ),
            "blockers": blockers,
            "can_apply": not blockers,
            "impact_hash": impact_hash,
        },
        descendants,
        feeds,
    )


def preview_downstream_correction(
    *,
    game: Game,
    expected_game_version: int,
) -> dict[str, object]:
    preview, _games, _feeds = _correction_analysis(
        source_game=game,
        expected_game_version=expected_game_version,
        lock=False,
    )
    return preview


@transaction.atomic
def apply_downstream_correction(
    *,
    actor: Account,
    game_id: UUID,
    expected_game_version: int,
    impact_hash: str,
) -> dict[str, object]:
    source_game = (
        Game.objects.select_for_update(of=("self",))
        .select_related("season", "division")
        .filter(id=game_id)
        .first()
    )
    if source_game is None:
        raise GameResultError("GAME_NOT_FOUND", "比赛不存在。")
    preview, descendants, feeds = _correction_analysis(
        source_game=source_game,
        expected_game_version=expected_game_version,
        lock=True,
    )
    if preview["impact_hash"] != impact_hash:
        raise GameResultError("IMPACT_HASH_MISMATCH", "纠错影响已变化，请重新预览。")
    if preview["blockers"]:
        raise GameResultError("CORRECTION_BLOCKED", "下游纠错存在阻塞项。")

    from core.services.rescheduling import admin_cancel_request

    affected_ids = {source_game.id, *(game.id for game in descendants)}
    terminal = list(RescheduleRequest.TERMINAL_STATUSES)
    requests = list(
        RescheduleRequest.objects.select_for_update()
        .filter(game_id__in=affected_ids)
        .exclude(status__in=terminal)
    )
    for request_item in requests:
        admin_cancel_request(
            actor=actor,
            request_id=request_item.id,
            expected_version=request_item.version,
        )

    feeds_by_target: dict[UUID, list[GameWinnerFeed]] = {}
    for feed in feeds:
        feeds_by_target.setdefault(feed.target_game_id, []).append(feed)
    before = {
        "source_game_id": str(source_game.id),
        "games": preview["affected_games"],
        "feed_count": len(feeds),
        "cancelled_request_ids": [str(item.id) for item in requests],
    }
    for game in reversed(descendants):
        changed_fields: set[str] = set()
        for feed in feeds_by_target.get(game.id, []):
            if feed.applied_winner_id:
                if (
                    feed.target_side == GameWinnerFeed.TargetSide.HOME
                    and game.home_team_id == feed.applied_winner_id
                ):
                    game.home_team = None
                    changed_fields.add("home_team")
                elif (
                    feed.target_side == GameWinnerFeed.TargetSide.AWAY
                    and game.away_team_id == feed.applied_winner_id
                ):
                    game.away_team = None
                    changed_fields.add("away_team")
            feed.applied_winner = None
            feed.applied_source_version = None
            feed.version += 1
            feed.save(
                update_fields=[
                    "applied_winner",
                    "applied_source_version",
                    "version",
                    "updated_at",
                ]
            )
        if game.home_score is not None:
            game.home_score = None
            changed_fields.add("home_score")
        if game.away_score is not None:
            game.away_score = None
            changed_fields.add("away_score")
        if game.status != Game.Status.SCHEDULED:
            game.status = Game.Status.SCHEDULED
            changed_fields.add("status")
        if changed_fields:
            game.version += 1
            changed_fields.update({"version", "updated_at"})
            game.save(update_fields=list(changed_fields))

    scoresheets = list(
        GameScoresheet.objects.select_for_update().filter(game_id__in=[
            game.id for game in descendants
        ])
    )
    for scoresheet in scoresheets:
        if scoresheet.current_publication_id is None:
            continue
        scoresheet.current_publication = None
        scoresheet.status = GameScoresheet.Status.DRAFT
        scoresheet.validation_draft_version = None
        scoresheet.save(
            update_fields=[
                "current_publication",
                "status",
                "validation_draft_version",
                "updated_at",
            ]
        )
        ScoresheetEditLease.objects.filter(scoresheet=scoresheet).delete()

    after = {
        "reset_game_ids": [str(game.id) for game in descendants],
        "reset_feed_ids": [str(feed.id) for feed in feeds],
        "cancelled_request_ids": [str(item.id) for item in requests],
        "withdrawn_publication_count": preview["publication_count"],
    }
    AdminAuditLog.objects.create(
        actor=actor,
        action="GAME_RESULT_DOWNSTREAM_RESET",
        object_type="Game",
        object_id=source_game.id,
        before=before,
        after=after,
        metadata={"impact_hash": impact_hash},
    )
    return {**preview, **after}
