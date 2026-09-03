from __future__ import annotations

import json
from datetime import timedelta

import pytest
from django.test import Client
from django.utils import timezone

from core.models import Account, AdminAuditLog, Division, Season, SeasonLeaderBinding, Team
from core.services.leader_bindings import (
    LeaderBindingError,
    preview_leader_transfer,
    release_leader_binding,
    transfer_leader_binding,
)

pytestmark = pytest.mark.django_db(transaction=True)


def _setup():
    today = timezone.localdate()
    season = Season.objects.create(
        name="领队绑定纠错测试",
        competition_type=Season.CompetitionType.PKU_CUP,
        year=today.year,
        status=Season.Status.PUBLISHED,
        starts_on=today,
        ends_on=today + timedelta(days=60),
    )
    division = Division.objects.create(season=season, code="men-a", name="男甲")
    teams = [
        Team.objects.create(season=season, division=division, name=f"球队 {index}")
        for index in range(1, 4)
    ]
    accounts = [
        Account.objects.create_user(username=f"leader-{index}", password="test-password")
        for index in range(1, 4)
    ]
    actor = Account.objects.create_user(
        username="leader-root",
        password="test-password",
        role=Account.Role.SUPERADMIN,
    )
    return season, teams, accounts, actor


def test_transfer_releases_conflicts_preserves_history_and_release_is_versioned():
    season, teams, accounts, actor = _setup()
    first = SeasonLeaderBinding.objects.create(
        season=season,
        account=accounts[0],
        team=teams[0],
    )
    second = SeasonLeaderBinding.objects.create(
        season=season,
        account=accounts[1],
        team=teams[1],
    )
    preview, _ = preview_leader_transfer(
        actor=actor,
        season_id=season.id,
        expected_season_version=season.version,
        account_id=accounts[0].id,
        team_id=teams[1].id,
        reason="纠正错绑",
    )
    assert {row["id"] for row in preview["release_bindings"]} == {
        str(first.id),
        str(second.id),
    }

    binding = transfer_leader_binding(
        actor=actor,
        season_id=season.id,
        expected_season_version=season.version,
        account_id=accounts[0].id,
        team_id=teams[1].id,
        reason="纠正错绑",
        impact_hash=preview["impact_hash"],
        confirmed=True,
    )
    first.refresh_from_db()
    second.refresh_from_db()
    season.refresh_from_db()
    assert not first.active and not second.active
    assert first.released_by_id == second.released_by_id == actor.id
    assert SeasonLeaderBinding.objects.filter(season=season).count() == 3
    assert SeasonLeaderBinding.objects.filter(season=season, active=True).get().id == binding.id
    assert season.version == 2

    released = release_leader_binding(
        actor=actor,
        binding_id=binding.id,
        expected_version=binding.version,
        reason="赛季身份更正",
        confirmed=True,
    )
    assert released.active is False
    assert released.release_reason == "赛季身份更正"
    assert released.version == 2
    assert AdminAuditLog.objects.filter(action="LEADER_BINDING_TRANSFERRED").count() == 1
    assert AdminAuditLog.objects.filter(action="LEADER_BINDING_RELEASED").count() == 1


def test_transfer_rejects_cross_season_team_and_stale_preview():
    season, teams, accounts, actor = _setup()
    other = Season.objects.create(
        name="其他赛季",
        competition_type=Season.CompetitionType.PKU_CUP,
        year=season.year + 1,
        status=Season.Status.SETUP,
        starts_on=season.starts_on,
        ends_on=season.ends_on,
    )
    other_division = Division.objects.create(season=other, code="men-a", name="男甲")
    other_team = Team.objects.create(season=other, division=other_division, name="其他球队")
    with pytest.raises(LeaderBindingError) as crossed:
        preview_leader_transfer(
            actor=actor,
            season_id=season.id,
            expected_season_version=season.version,
            account_id=accounts[0].id,
            team_id=other_team.id,
        )
    assert crossed.value.code == "TEAM_NOT_AVAILABLE"

    preview, _ = preview_leader_transfer(
        actor=actor,
        season_id=season.id,
        expected_season_version=season.version,
        account_id=accounts[0].id,
        team_id=teams[0].id,
    )
    season.version += 1
    season.save(update_fields=["version", "updated_at"])
    with pytest.raises(LeaderBindingError) as stale:
        transfer_leader_binding(
            actor=actor,
            season_id=season.id,
            expected_season_version=1,
            account_id=accounts[0].id,
            team_id=teams[0].id,
            reason="",
            impact_hash=preview["impact_hash"],
            confirmed=True,
        )
    assert stale.value.code == "VERSION_CONFLICT"


def test_transfer_api_replays_same_idempotency_key():
    season, teams, accounts, actor = _setup()
    preview, _ = preview_leader_transfer(
        actor=actor,
        season_id=season.id,
        expected_season_version=season.version,
        account_id=accounts[0].id,
        team_id=teams[0].id,
        reason="API 幂等",
    )
    payload = {
        "expected_season_version": season.version,
        "account_id": str(accounts[0].id),
        "team_id": str(teams[0].id),
        "reason": "API 幂等",
        "impact_hash": preview["impact_hash"],
        "confirmed": True,
    }
    client = Client()
    client.force_login(actor)
    url = f"/api/v1/admin/seasons/{season.id}/leader-bindings/transfer"
    first = client.post(
        url,
        data=json.dumps(payload),
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY="leader-transfer-1",
    )
    replay = client.post(
        url,
        data=json.dumps(payload),
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY="leader-transfer-1",
    )
    assert first.status_code == replay.status_code == 200
    assert first.json()["id"] == replay.json()["id"]
    assert SeasonLeaderBinding.objects.filter(season=season, active=True).count() == 1
