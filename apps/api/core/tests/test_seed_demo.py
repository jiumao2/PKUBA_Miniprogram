import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

from core.models import AdminAuditLog, Game, RescheduleRequest, Season, Team

pytestmark = pytest.mark.django_db


def test_demo_origin_is_idempotent_and_blocks_the_read_only_production_gate():
    from core.management.commands.seed_demo import AUDIT_ACTION

    call_command("seed_demo")
    call_command("seed_demo")
    marker = AdminAuditLog.objects.get(action=AUDIT_ACTION)
    season = Season.objects.get(id=marker.object_id)
    assert season.games.count() == 3
    assert season.teams.count() == 4
    before = list(AdminAuditLog.objects.values())
    with pytest.raises(CommandError, match="合成演示赛季数据"):
        call_command("check_no_synthetic_public_data")
    assert list(AdminAuditLog.objects.values()) == before


@pytest.mark.parametrize(
    "action", [None, "PUBLIC_LEADERBOARD_SYNTHETIC_SEEDED", "LOCAL_GAME_MEDIA_DEMO_SEEDED"]
)
def test_gate_retains_other_origin_checks_without_guessing_real_season_names(action):
    season = Season.objects.create(
        name="北大杯",
        year=2026,
        competition_type="PKU_CUP",
        status=Season.Status.PUBLISHED,
        starts_on="2026-08-01",
        ends_on="2026-09-01",
    )
    if action:
        AdminAuditLog.objects.create(action=action, object_type="Season", object_id=season.id)
        with pytest.raises(CommandError):
            call_command("check_no_synthetic_public_data")
    else:
        call_command("check_no_synthetic_public_data")
    assert Season.objects.count() == 1
    assert AdminAuditLog.objects.count() == int(action is not None)


def test_failure_to_record_demo_origin_rolls_back_the_seed(monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("synthetic origin failure")

    monkeypatch.setattr(AdminAuditLog.objects, "get_or_create", fail)
    with pytest.raises(RuntimeError, match="synthetic origin failure"):
        call_command("seed_demo")
    assert not Season.objects.exists()
    assert not Team.objects.exists()
    assert not Game.objects.exists()
    assert not AdminAuditLog.objects.exists()


def test_seed_demo_if_empty_preserves_existing_season():
    existing = Season.objects.create(
        name="Existing local season",
        competition_type=Season.CompetitionType.PKU_CUP,
        year=2026,
        status=Season.Status.SETUP,
        starts_on="2026-08-01",
        ends_on="2026-09-01",
    )

    call_command("seed_demo", if_empty=True)

    assert list(Season.objects.values_list("id", flat=True)) == [existing.id]


@override_settings(DEBUG=True)
def test_reschedule_demo_seed_is_visible_and_idempotent():
    call_command("seed_demo")

    call_command("seed_reschedule_demo")
    first_count = RescheduleRequest.objects.filter(game__code__startswith="DEMO-RS-").count()
    statuses = set(
        RescheduleRequest.objects.filter(game__code__startswith="DEMO-RS-").values_list(
            "status", flat=True
        )
    )
    call_command("seed_reschedule_demo")

    assert first_count == 9
    assert RescheduleRequest.objects.filter(game__code__startswith="DEMO-RS-").count() == 9
    assert statuses == {
        RescheduleRequest.Status.WAITING_OPPONENT,
        RescheduleRequest.Status.WAITING_ADMIN_DECISION,
        RescheduleRequest.Status.WAITING_SELECTED_TEAMS,
        RescheduleRequest.Status.WAITING_ADMIN_FINAL,
        RescheduleRequest.Status.APPROVED,
        RescheduleRequest.Status.REJECTED,
        RescheduleRequest.Status.WITHDRAWN,
        RescheduleRequest.Status.EXPIRED,
        RescheduleRequest.Status.ADMIN_CANCELLED,
    }
