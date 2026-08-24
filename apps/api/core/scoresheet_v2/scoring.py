from __future__ import annotations

from .models import (
    InkRole,
    ScoreBoundary,
    ScoreMark,
    ScoresheetDocument,
    TeamSide,
)


def period_checkpoints(
    document: ScoresheetDocument,
    side: TeamSide,
) -> list[tuple[int, int]]:
    """Return written cumulative checkpoints for Q1-Q4 and optional combined OT."""
    by_period = {}
    for score in document.stated_period_scores:
        by_period.setdefault(score.period, score)
    periods = [1, 2, 3, 4]
    if 5 in by_period:
        periods.append(5)
    total = 0
    checkpoints: list[tuple[int, int]] = []
    for period in periods:
        score = by_period.get(period)
        if score is not None:
            total += score.team_a if side == TeamSide.A else score.team_b
        checkpoints.append((period, total))
    return checkpoints


def _period_for_score(cumulative_score: int, checkpoints: list[tuple[int, int]]) -> int:
    for period, checkpoint in checkpoints:
        if cumulative_score <= checkpoint:
            return period
    if checkpoints and checkpoints[-1][1] > 0:
        return checkpoints[-1][0]
    return 1


def _semantic_mark(points: int | None) -> tuple[ScoreMark | None, bool]:
    if points == 1:
        return ScoreMark.FILLED_DOT, False
    if points == 2:
        return ScoreMark.DIAGONAL, False
    if points == 3:
        return ScoreMark.DIAGONAL, True
    return None, False


def derive_score_events(document: ScoresheetDocument) -> ScoresheetDocument:
    """Canonicalize all non-editable score-event fields from fixed score cells."""
    events_by_side = {
        side: sorted(
            (event for event in document.score_events if event.team == side),
            key=lambda event: (event.cumulative_score, event.sequence),
        )
        for side in (TeamSide.A, TeamSide.B)
    }

    for side, events in events_by_side.items():
        checkpoints = period_checkpoints(document, side)
        previous = 0
        for event in events:
            delta = event.cumulative_score - previous
            event.points = delta if delta >= 1 else None
            event.mark, event.scorer_circled = _semantic_mark(event.points)
            event.period = _period_for_score(event.cumulative_score, checkpoints)
            event.ink_role = (
                InkRole.Q1_Q3 if event.period in {1, 3} else InkRole.Q2_Q4_OT
            )
            event.boundary = ScoreBoundary.NONE
            previous = event.cumulative_score

        events_by_score = {event.cumulative_score: event for event in events}
        for _, checkpoint in checkpoints:
            if checkpoint > 0 and checkpoint in events_by_score:
                events_by_score[checkpoint].boundary = ScoreBoundary.PERIOD_END

    latest = {side: events[-1] if events else None for side, events in events_by_side.items()}
    if (
        latest[TeamSide.A] is not None
        and latest[TeamSide.B] is not None
        and latest[TeamSide.A].cumulative_score == document.final_score.team_a
        and latest[TeamSide.B].cumulative_score == document.final_score.team_b
    ):
        latest[TeamSide.A].boundary = ScoreBoundary.GAME_END
        latest[TeamSide.B].boundary = ScoreBoundary.GAME_END

    document.score_events.sort(
        key=lambda event: (
            event.period,
            event.team.value,
            event.cumulative_score,
            event.sequence,
        )
    )
    for sequence, event in enumerate(document.score_events, start=1):
        event.sequence = sequence
    return document
