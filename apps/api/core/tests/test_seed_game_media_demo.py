import pytest
from django.core.files.storage import default_storage
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

from core.models import AdminAuditLog, Game, GameMediaAsset
from core.tests.factories import reschedule_setup

pytestmark = pytest.mark.django_db(transaction=True)


def test_seed_game_media_demo_is_dry_run_by_default(tmp_path):
    setup = reschedule_setup()
    with override_settings(DEBUG=True, MEDIA_ROOT=tmp_path):
        call_command(
            "seed_game_media_demo",
            season_id=str(setup["season"].id),
        )
    assert GameMediaAsset.objects.filter(game__season=setup["season"]).count() == 0


def test_seed_game_media_demo_covers_every_game_and_is_idempotent(tmp_path):
    setup = reschedule_setup()
    season = setup["season"]
    expected_games = season.games.exclude(status=Game.Status.VOID).count()
    options = {
        "season_id": str(season.id),
        "actor": setup["superadmin"].username,
        "confirm_local_demo": True,
    }

    with override_settings(DEBUG=True, MEDIA_ROOT=tmp_path):
        call_command("seed_game_media_demo", **options)
        first_ids = set(
            GameMediaAsset.objects.filter(game__season=season).values_list(
                "id", flat=True
            )
        )
        call_command("seed_game_media_demo", **options)
        rows = list(GameMediaAsset.objects.filter(game__season=season))
        assert all(default_storage.exists(row.file_key) for row in rows)

    assert len(first_ids) == expected_games * 2
    assert {row.id for row in rows} == first_ids
    assert sum(row.kind == GameMediaAsset.Kind.GROUP_PHOTO for row in rows) == expected_games
    assert sum(row.kind == GameMediaAsset.Kind.SCORESHEET for row in rows) == expected_games
    assert all(row.review_status == GameMediaAsset.ReviewStatus.APPROVED for row in rows)
    assert AdminAuditLog.objects.filter(
        action="LOCAL_GAME_MEDIA_DEMO_SEEDED", object_id=season.id
    ).count() == 1
    with override_settings(DEBUG=True, MEDIA_ROOT=tmp_path):
        call_command("seed_game_media_demo", refresh_generated=True, **options)
    refreshed = GameMediaAsset.objects.filter(
        game__season=season, deleted_at__isnull=True
    )
    assert refreshed.count() == expected_games * 2
    assert not first_ids.intersection(refreshed.values_list("id", flat=True))
    assert GameMediaAsset.objects.filter(
        game__season=season, deleted_at__isnull=False
    ).count() == expected_games * 2
    with pytest.raises(CommandError, match="本地合成比赛图片"):
        call_command("check_no_synthetic_public_data")


def test_seed_game_media_demo_refuses_non_debug_environment(tmp_path):
    setup = reschedule_setup()
    with override_settings(DEBUG=False, MEDIA_ROOT=tmp_path):
        with pytest.raises(CommandError, match="DEBUG"):
            call_command(
                "seed_game_media_demo",
                season_id=str(setup["season"].id),
                actor=setup["superadmin"].username,
                confirm_local_demo=True,
            )
