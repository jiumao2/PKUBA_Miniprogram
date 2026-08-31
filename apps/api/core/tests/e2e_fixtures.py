"""Explicit test-only targets for the official browser suite.

The fixture is imported only by the isolated browser harness.  It deliberately
uses the same model and scoresheet services as the application and is never
exposed as a management command or startup seed.
"""

from datetime import timedelta

from core.models import Game, GameMediaAsset, GameScoresheet, ScoresheetEditLease
from core.services.game_media import upload_game_media
from core.services.scoresheets import acquire_edit_lease, release_edit_lease
from core.tests.test_scoresheets import create_scoresheet, image_file, make_ready


def _prepare_sheet(scoresheet: GameScoresheet, actor, *, client_id: str) -> GameScoresheet:
    _lease, token, read_only, reason = acquire_edit_lease(
        scoresheet_id=scoresheet.id,
        actor=actor,
        client_id=client_id,
        surface=ScoresheetEditLease.Surface.WEB,
    )
    if read_only or not token:
        raise RuntimeError(f"Browser fixture could not acquire its edit lease: {reason}")
    try:
        return make_ready(scoresheet, actor, token, client_id=client_id)
    finally:
        release_edit_lease(
            scoresheet_id=scoresheet.id,
            actor=actor,
            lease_token=token,
            client_id=client_id,
            surface=ScoresheetEditLease.Surface.WEB,
        )


def seed_browser_targets(actor):
    """Build deterministic targets in a fresh isolated test DB and media store."""
    setup, base, _players, _asset, editor = create_scoresheet()
    del setup
    editor = _prepare_sheet(editor, actor, client_id="official-browser-edit")
    targets = {"edit": str(editor.id)}
    for offset, purpose in enumerate(("publication", "private"), start=1):
        game = Game.objects.create(
            season=base.season,
            division=base.division,
            group=base.group,
            code=f"SCORESHEET-E2E-{purpose.upper()}",
            stage=base.stage,
            round_number=base.round_number + offset,
            date=base.date + timedelta(days=offset),
            period=base.period,
            start_time=base.start_time,
            venue_name=base.venue_name,
            home_team=base.home_team,
            away_team=base.away_team,
            home_slot=base.home_slot,
            away_slot=base.away_slot,
        )
        upload_game_media(
            actor=actor,
            game=game,
            kind=GameMediaAsset.Kind.SCORESHEET,
            scoresheet_complete_confirmed=True,
            uploaded_file=image_file(f"{purpose}.jpg"),
        )
        sheet = _prepare_sheet(
            GameScoresheet.objects.get(game=game),
            actor,
            client_id=f"official-browser-{purpose}",
        )
        targets[purpose] = str(sheet.id)
    targets["game_pattern"] = (
        f"{base.date.isoformat()}.*{base.home_team.name}.*{base.away_team.name}"
    )
    return targets
