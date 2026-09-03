from __future__ import annotations

from django.db.models import Max

from core.models import (
    Account,
    CompetitionCorrection,
    Game,
    GameResultRevision,
    ScoresheetPublication,
)


def game_result_snapshot(game: Game) -> dict[str, object]:
    return {
        "game_id": str(game.id),
        "game_version": game.version,
        "home_team_id": str(game.home_team_id) if game.home_team_id else None,
        "away_team_id": str(game.away_team_id) if game.away_team_id else None,
        "home_score": game.home_score,
        "away_score": game.away_score,
        "status": game.status,
        "current_result_revision_id": (
            str(game.current_result_revision_id) if game.current_result_revision_id else None
        ),
    }


def append_game_result_revision(
    *,
    game: Game,
    actor: Account | None,
    reason: str,
    publication: ScoresheetPublication | None = None,
    correction: CompetitionCorrection | None = None,
) -> GameResultRevision:
    """Append and select one immutable formal result revision.

    The caller must already hold the game row lock and remain inside the same
    transaction as any authoritative Game or publication changes.
    """

    current_number = (
        GameResultRevision.objects.filter(game=game).aggregate(
            maximum=Max("revision_number")
        )["maximum"]
        or 0
    )
    revision = GameResultRevision(
        game=game,
        revision_number=current_number + 1,
        status=game.status,
        home_team_id=game.home_team_id,
        away_team_id=game.away_team_id,
        home_score=game.home_score,
        away_score=game.away_score,
        publication=publication,
        supersedes_id=game.current_result_revision_id,
        correction=correction,
        reason=reason,
        created_by=actor,
    )
    revision.full_clean()
    revision.save()
    game.current_result_revision = revision
    game.save(update_fields=["current_result_revision", "updated_at"])
    return revision
