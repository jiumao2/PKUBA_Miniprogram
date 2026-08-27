from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event

import pytest
from django.db import IntegrityError, connections, transaction
from django.test import Client

from core.models import (
    AdminAuditLog,
    Game,
    GameMediaAsset,
    GamePlayerStat,
    GameScoresheet,
    GameTeamStat,
    RosterPlayer,
    ScoresheetPublication,
    Season,
    Team,
)
from core.services.roster_management import (
    RosterManagementError,
    create_team_with_roster,
    preview_team_change,
    save_team_roster,
)
from core.services.scoresheets import publish_scoresheet
from core.tests.test_admin_api import login_admin
from core.tests.test_roster_management import _setup
from core.tests.test_roster_preview_serialization import _post_preview, _save
from core.tests.test_scoresheets import create_scoresheet, make_ready, obtain_lease

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def isolated_runtime(settings, tmp_path):
    settings.SECURE_SSL_REDIRECT = False
    settings.MEDIA_ROOT = tmp_path
    settings.QWEN_API_KEY = ""


def _payload(team):
    return {
        "expected_team_version": team.version,
        "name": team.name,
        "active": team.active,
        "players": [
            {
                "id": str(player.id),
                "expected_version": player.version,
                "name": player.name,
                "jersey_number": player.jersey_number,
                "eligible": player.eligible,
                "active": player.active,
            }
            for player in team.roster.order_by("jersey_number")
        ],
    }


def _case():
    setup = _setup()
    team = create_team_with_roster(
        actor=setup["actor"],
        season=setup["season"],
        division_id=setup["divisions"][0].id,
        name="号码调整球队",
        players=[
            {"name": name, "jersey_number": number}
            for name, number in (("甲球员", "7"), ("乙球员", "8"), ("丙球员", "9"))
        ],
        expected_season_version=setup["season"].version,
    )
    Season.objects.filter(id=team.season_id).update(status=Season.Status.PUBLISHED)
    team.refresh_from_db()
    client = Client(enforce_csrf_checks=True)
    csrf = login_admin(client, setup["actor"])
    return setup["actor"], team, client, csrf, _payload(team)


def _snapshot(*models):
    selected = models or (
        Season,
        Team,
        RosterPlayer,
        AdminAuditLog,
        Game,
        GameScoresheet,
        ScoresheetPublication,
        GamePlayerStat,
        GameTeamStat,
        GameMediaAsset,
    )
    return {model.__name__: list(model.objects.order_by("id").values()) for model in selected}


def _preview_and_confirm(team, client, csrf, payload):
    before = _snapshot()
    preview = _post_preview(client, csrf, team, payload)
    assert preview.status_code == 200, preview.content
    assert _snapshot() == before
    return _save(
        client,
        csrf,
        team,
        {
            **payload,
            "maintenance_token": preview.json()["maintenance_token"],
        },
    )


@pytest.mark.parametrize("mode", ["swap", "cycle", "deactivate_reuse", "omit_reuse", "mixed"])
def test_final_roster_number_changes_preserve_ids_and_audit(mode):
    _actor, team, client, csrf, payload = _case()
    original = copy.deepcopy(payload["players"])
    rows = payload["players"]
    if mode == "swap":
        rows[0]["jersey_number"], rows[1]["jersey_number"] = "8", "7"
    elif mode == "cycle":
        for row, number in zip(rows, ("8", "9", "7"), strict=True):
            row["jersey_number"] = number
    else:
        if mode == "omit_reuse":
            rows.pop(0)
        elif mode == "deactivate_reuse":
            rows[0]["active"] = False
        else:
            rows[0]["jersey_number"] = "8"
            rows[1]["jersey_number"] = "10"
        rows.insert(0, {"name": "新球员", "jersey_number": "7", "active": True})
    response = _preview_and_confirm(team, client, csrf, payload)
    assert response.status_code == 200, response.content
    team.refresh_from_db()
    assert team.version == payload["expected_team_version"] + 1
    persisted = {str(player.id): player for player in team.roster.all()}
    assert {row["id"] for row in original} <= persisted.keys()
    targets = {row["id"]: row for row in rows if row.get("id")}
    for prior in original:
        saved = persisted[prior["id"]]
        target = targets.get(prior["id"], {**prior, "active": False})
        for field in ("name", "jersey_number", "eligible", "active"):
            assert getattr(saved, field) == target[field]
        changed = any(
            target[field] != prior[field]
            for field in ("name", "jersey_number", "eligible", "active")
        )
        assert saved.version == prior["expected_version"] + int(changed)
    assert len(persisted) == len(original) + int(
        mode in {"deactivate_reuse", "omit_reuse", "mixed"}
    )
    audits = AdminAuditLog.objects.filter(action="roster.team.save", object_id=team.id)
    assert audits.count() == 1
    audit = audits.get()
    assert {row["id"] for row in audit.before["players"]} == {row["id"] for row in original}
    previous = {row["id"]: row for row in audit.before["players"]}
    for row in original:
        assert previous[row["id"]]["active"] is row["active"]
        assert previous[row["id"]]["jersey_number"] == row["jersey_number"]
        assert previous[row["id"]]["version"] == row["expected_version"]
    after = {row["id"]: row for row in audit.after["players"]}
    assert {key: row["active"] for key, row in after.items()} == {
        key: row.active for key, row in persisted.items()
    }
    assert audit.metadata["maintenance_confirmed"] is True


@pytest.mark.parametrize("fault", ["duplicate", "team_version", "player_version", "changed_target"])
def test_invalid_final_roster_or_stale_preview_keeps_every_row_unchanged(fault):
    _actor, team, client, csrf, payload = _case()
    payload["players"][0]["jersey_number"], payload["players"][1]["jersey_number"] = "8", "7"
    preview = _post_preview(client, csrf, team, payload)
    assert preview.status_code == 200
    payload["maintenance_token"] = preview.json()["maintenance_token"]
    if fault == "duplicate":
        payload["players"][1]["jersey_number"] = "8"
    elif fault == "team_version":
        payload["expected_team_version"] += 1
    elif fault == "player_version":
        payload["players"][-1]["expected_version"] += 1
    else:
        payload["players"][-1]["name"] = "预览后改名"
    before = _snapshot()
    response = _save(client, csrf, team, payload)
    assert response.status_code == (400 if fault == "duplicate" else 409), response.content
    assert _snapshot() == before


@pytest.mark.parametrize("fail_at", ["second_player", "audit"])
def test_late_failure_rolls_back_released_numbers_versions_and_audit(monkeypatch, fail_at):
    actor, team, _client, _csrf, payload = _case()
    payload["players"][0]["jersey_number"], payload["players"][1]["jersey_number"] = "8", "7"
    preview = preview_team_change(actor=actor, team=team, payload=payload)
    before = _snapshot()
    saved = []
    original_save = RosterPlayer.save

    def player_save(player, *args, **kwargs):
        if fail_at == "second_player" and len(saved) == 1:
            raise IntegrityError("isolated later-row failure")
        result = original_save(player, *args, **kwargs)
        saved.append(player.id)
        return result

    original_audit_save = AdminAuditLog.save

    def audit_save(audit, *args, **kwargs):
        if audit.action == "roster.team.save" and fail_at == "audit":
            raise IntegrityError("isolated audit failure")
        return original_audit_save(audit, *args, **kwargs)

    monkeypatch.setattr(RosterPlayer, "save", player_save)
    monkeypatch.setattr(AdminAuditLog, "save", audit_save)
    with pytest.raises(RosterManagementError) as rejected:
        save_team_roster(
            actor=actor,
            team_id=team.id,
            payload=payload,
            maintenance_token=preview["maintenance_token"],
        )
    assert rejected.value.code == "ROSTER_INTEGRITY_CONFLICT"
    assert len(saved) == (1 if fail_at == "second_player" else 2)
    assert _snapshot() == before


def test_number_swap_keeps_published_statistics_and_source_identity():
    setup, game, _players, _source, sheet = create_scoresheet()
    actor = setup["superadmin"]
    token = obtain_lease(sheet, actor)
    sheet = make_ready(sheet, actor, token)
    publish_scoresheet(
        scoresheet_id=sheet.id,
        actor=actor,
        expected_version=sheet.draft_version,
        lease_token=token,
        client_id="web-1",
        surface="WEB",
    )
    team = game.home_team
    payload = _payload(team)
    first, second = payload["players"][:2]
    first["jersey_number"], second["jersey_number"] = (
        second["jersey_number"],
        first["jersey_number"],
    )
    before = _snapshot(
        Game, GameScoresheet, ScoresheetPublication, GamePlayerStat, GameTeamStat, GameMediaAsset
    )
    identities = set(team.roster.values_list("id", flat=True))
    client = Client(enforce_csrf_checks=True)
    csrf = login_admin(client, actor)
    response = _preview_and_confirm(team, client, csrf, payload)
    assert response.status_code == 200, response.content
    assert set(team.roster.values_list("id", flat=True)) == identities
    assert (
        _snapshot(
            Game,
            GameScoresheet,
            ScoresheetPublication,
            GamePlayerStat,
            GameTeamStat,
            GameMediaAsset,
        )
        == before
    )


def test_database_still_rejects_duplicate_active_numbers_bypassing_service():
    _actor, team, _client, _csrf, _payload_data = _case()
    before = _snapshot()
    with pytest.raises(IntegrityError), transaction.atomic():
        RosterPlayer.objects.filter(team=team, jersey_number="7").update(jersey_number="8")
    assert _snapshot() == before


def test_repeated_stable_player_id_is_rejected_before_any_write():
    _actor, team, client, csrf, payload = _case()
    payload["players"].append(
        {
            **payload["players"][0],
            "name": "重复身份不同姓名",
            "jersey_number": "20",
        }
    )
    before = _snapshot()
    for response in (
        _post_preview(client, csrf, team, payload),
        _save(client, csrf, team, payload),
    ):
        assert response.status_code == 400, response.content
        assert response.json()["code"] == "INVALID_PLAYER_ID"
        assert _snapshot() == before


def test_all_player_versions_are_checked_before_releasing_any_number(monkeypatch):
    from django.db.models.query import QuerySet

    actor, team, client, csrf, payload = _case()
    payload["players"][0]["jersey_number"], payload["players"][1]["jersey_number"] = "8", "7"
    payload["maintenance_token"] = preview_team_change(actor=actor, team=team, payload=payload)[
        "maintenance_token"
    ]
    RosterPlayer.objects.filter(id=payload["players"][-1]["id"]).update(version=2)
    before = _snapshot()
    original_update = QuerySet.update
    releases = []

    def observed_update(queryset, **kwargs):
        if queryset.model is RosterPlayer and kwargs.get("active") is False:
            releases.append(kwargs)
        return original_update(queryset, **kwargs)

    monkeypatch.setattr(QuerySet, "update", observed_update)
    response = _save(client, csrf, team, payload)
    assert response.status_code == 409, response.content
    assert response.json()["code"] == "VERSION_CONFLICT"
    assert releases == []
    assert _snapshot() == before


@pytest.mark.django_db(transaction=True)
def test_concurrent_number_changes_have_one_winner_and_one_version_conflict():
    actor, team, client, csrf, payload = _case()
    other_client = Client(enforce_csrf_checks=True)
    other_csrf = login_admin(other_client, actor)
    proposals = [copy.deepcopy(payload), copy.deepcopy(payload)]
    for proposed, numbers in zip(proposals, (("8", "7", "9"), ("9", "7", "8")), strict=True):
        for row, number in zip(proposed["players"], numbers, strict=True):
            row["jersey_number"] = number
        proposed["maintenance_token"] = preview_team_change(
            actor=actor, team=team, payload=proposed
        )["maintenance_token"]
    barrier = Barrier(2)

    def submit(index):
        connections.close_all()
        try:
            barrier.wait(timeout=15)
            response = _save(
                (client, other_client)[index], (csrf, other_csrf)[index], team, proposals[index]
            )
            return index, response.status_code, response.json()
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(submit, (0, 1)))
    assert sorted(status for _, status, _ in outcomes) == [200, 409], outcomes
    winner, _, _ = next(item for item in outcomes if item[1] == 200)
    assert next(body for _, status, body in outcomes if status == 409)["code"] == "VERSION_CONFLICT"
    saved = {str(player.id): player for player in team.roster.all()}
    for original, target in zip(payload["players"], proposals[winner]["players"], strict=True):
        player = saved[target["id"]]
        assert player.jersey_number == target["jersey_number"]
        assert player.active is True
        assert player.version == original["expected_version"] + int(
            target["jersey_number"] != original["jersey_number"]
        )
    team.refresh_from_db()
    assert team.version == payload["expected_team_version"] + 1
    assert AdminAuditLog.objects.filter(action="roster.team.save", object_id=team.id).count() == 1


@pytest.mark.django_db(transaction=True)
def test_other_connections_never_see_temporary_number_release(monkeypatch):
    actor, team, _client, _csrf, payload = _case()
    original = _snapshot(RosterPlayer)
    payload["players"][0]["jersey_number"], payload["players"][1]["jersey_number"] = "8", "7"
    token = preview_team_change(actor=actor, team=team, payload=payload)["maintenance_token"]
    reached, resume = Event(), Event()
    actual_save = RosterPlayer.save

    def paused_save(player, *args, **kwargs):
        if str(player.id) == payload["players"][0]["id"]:
            reached.set()
            assert resume.wait(timeout=15)
        return actual_save(player, *args, **kwargs)

    monkeypatch.setattr(RosterPlayer, "save", paused_save)

    def write():
        connections.close_all()
        try:
            return save_team_roster(
                actor=actor, team_id=team.id, payload=payload, maintenance_token=token
            )
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(write)
        try:
            assert reached.wait(timeout=15)
            assert _snapshot(RosterPlayer) == original
        finally:
            resume.set()
        assert future.result(timeout=15).id == team.id
    assert team.roster.filter(active=True).count() == 3
