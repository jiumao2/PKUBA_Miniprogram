from __future__ import annotations

import json
from datetime import timedelta

import pytest
from django.test import Client

from core.models import Account, AdminAuditLog, Season
from core.services.rescheduling import admin_cancel_request, submit_reschedule
from core.services.season_lifecycle import (
    SeasonLifecycleError,
    apply_season_lifecycle,
    preview_season_lifecycle,
)
from core.tests.factories import reschedule_setup
from core.tests.test_rescheduling import valid_submission_time

pytestmark = pytest.mark.django_db


def _superadmin() -> Account:
    return Account.objects.create_user(
        username="lifecycle-superadmin",
        password="test-password",
        role=Account.Role.SUPERADMIN,
    )


def _set_setup(setup: dict[str, object]) -> Season:
    season = setup["season"]
    season.status = Season.Status.SETUP
    season.save(update_fields=["status", "updated_at"])
    return season


def _apply(*, actor: Account, season: Season, target_status: str):
    season.refresh_from_db()
    preview = preview_season_lifecycle(
        season=season,
        expected_season_version=season.version,
        target_status=target_status,
    )
    assert preview["can_apply"] is True
    return apply_season_lifecycle(
        actor=actor,
        season_id=season.id,
        expected_season_version=season.version,
        target_status=target_status,
        impact_hash=preview["impact_hash"],
    )


def test_setup_season_can_publish_without_draw_mapping_or_division_activation():
    setup = reschedule_setup()
    season = _set_setup(setup)

    result = _apply(
        actor=_superadmin(),
        season=season,
        target_status=Season.Status.PUBLISHED,
    )

    season.refresh_from_db()
    assert result["after_season_status"] == Season.Status.PUBLISHED
    assert season.status == Season.Status.PUBLISHED
    assert AdminAuditLog.objects.filter(action="SEASON_LIFECYCLE_APPLIED").exists()


def test_publish_is_blocked_while_another_season_is_published():
    setup = reschedule_setup()
    season = _set_setup(setup)
    Season.objects.create(
        name="当前公开赛季",
        competition_type=Season.CompetitionType.PKU_CUP,
        year=season.year + 1,
        status=Season.Status.PUBLISHED,
        starts_on=season.starts_on,
        ends_on=season.ends_on,
    )

    preview = preview_season_lifecycle(
        season=season,
        expected_season_version=season.version,
        target_status=Season.Status.PUBLISHED,
    )

    assert preview["can_apply"] is False
    assert preview["blockers"][0]["code"] == "PUBLIC_SEASON_EXISTS"


def test_archive_keeps_terminal_state_and_requires_active_flows_to_close():
    setup = reschedule_setup()
    actor = _superadmin()
    game = setup["games"][0]
    now = valid_submission_time(game.date, setup["target_date"])
    request_item = submit_reschedule(
        actor=setup["accounts"][0],
        game_id=game.id,
        expected_game_version=game.version,
        target_date=setup["target_date"],
        target_period_id=setup["period"].id,
        now=now,
    )
    season = setup["season"]
    season.refresh_from_db()
    blocked = preview_season_lifecycle(
        season=season,
        expected_season_version=season.version,
        target_status=Season.Status.ARCHIVED,
        now=now,
    )
    assert blocked["can_apply"] is False
    assert blocked["references"]["reschedule_requests"] == 1
    assert blocked["references"]["reservations"] == 1

    admin_cancel_request(
        actor=actor,
        request_id=request_item.id,
        expected_version=request_item.version,
        now=now + timedelta(minutes=1),
    )
    result = _apply(actor=actor, season=season, target_status=Season.Status.ARCHIVED)
    season.refresh_from_db()
    assert result["after_season_status"] == Season.Status.ARCHIVED
    assert season.status == Season.Status.ARCHIVED

    with pytest.raises(SeasonLifecycleError) as error:
        preview_season_lifecycle(
            season=season,
            expected_season_version=season.version,
            target_status=Season.Status.PUBLISHED,
        )
    assert error.value.code == "SEASON_ARCHIVED"


def test_lifecycle_api_requires_preview_hash_and_is_idempotent():
    setup = reschedule_setup()
    season = _set_setup(setup)
    client = Client()
    client.force_login(_superadmin())
    path = f"/api/v1/admin/seasons/{season.id}/lifecycle"
    command = {
        "expected_season_version": season.version,
        "target_status": Season.Status.PUBLISHED,
    }
    preview = client.post(
        f"{path}/preview",
        data=json.dumps(command),
        content_type="application/json",
    )
    assert preview.status_code == 200, preview.content
    payload = {**command, "impact_hash": preview.json()["impact_hash"]}
    applied = client.post(
        f"{path}/apply",
        data=json.dumps(payload),
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY="lifecycle-apply-test",
    )
    assert applied.status_code == 200, applied.content
    replayed = client.post(
        f"{path}/apply",
        data=json.dumps(payload),
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY="lifecycle-apply-test",
    )
    assert replayed.status_code == 200
    assert replayed.json() == applied.json()
