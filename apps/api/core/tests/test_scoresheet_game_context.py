from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from django.db import close_old_connections
from django.test import Client

from core.models import (
    Account,
    AdminAuditLog,
    Game,
    GameMediaAsset,
    GamePlayerStat,
    GameScoresheet,
    GameTeamStat,
    Period,
    RosterPlayer,
    ScoresheetChangeLog,
    ScoresheetEditLease,
    ScoresheetPublication,
    ScoresheetRecognitionRun,
    ScoresheetRevision,
    Season,
)
from core.services.scoresheets import (
    ScoresheetError,
    acknowledge_warnings,
    acquire_edit_lease,
    publish_scoresheet,
    review_scoresheet_game_context,
    save_draft_changes,
    validate_scoresheet,
)
from core.services.wechat import issue_session
from core.tests.test_scoresheets import create_scoresheet, make_ready, obtain_lease

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def ready_sheet(settings, tmp_path):
    settings.QWEN_API_KEY = ""
    settings.MEDIA_ROOT = tmp_path
    setup, game, players, source, sheet = create_scoresheet()
    token = obtain_lease(sheet, setup["admin"])
    sheet = make_ready(sheet, setup["admin"], token)
    return setup, game, players, source, sheet, token


def mutation(fixture, **overrides):
    setup, _, _, _, sheet, token = fixture
    sheet.refresh_from_db()
    return {
        "scoresheet_id": sheet.id,
        "actor": setup["admin"],
        "expected_version": sheet.draft_version,
        "lease_token": token,
        "client_id": "web-1",
        "surface": "WEB",
        **overrides,
    }


def validate(fixture):
    return validate_scoresheet(**mutation(fixture))


def review(fixture, **overrides):
    sheet = fixture[4]
    sheet.refresh_from_db()
    return review_scoresheet_game_context(
        **mutation(fixture),
        confirmed=True,
        review_token=sheet.validation_report["game_context"]["review_token"],
        **overrides,
    )


def acknowledge(fixture):
    sheet = fixture[4]
    sheet.refresh_from_db()
    acknowledge_warnings(
        **mutation(fixture), warning_ids=[w["id"] for w in sheet.validation_report["warnings"]]
    )


def snapshot():
    return {
        model.__name__: list(model.objects.order_by("id").values())
        for model in (
            Game,
            GameScoresheet,
            GameMediaAsset,
            ScoresheetRecognitionRun,
            ScoresheetEditLease,
            ScoresheetPublication,
            ScoresheetRevision,
            ScoresheetChangeLog,
            AdminAuditLog,
            GamePlayerStat,
            GameTeamStat,
        )
    }


@pytest.mark.parametrize("change", ["counter", "noop_save", "policy", "lock_cycle"])
def test_internal_changes_revalidate_and_publish_without_touching_manual_or_source(
    ready_sheet, change
):
    _, game, _, source, sheet, _ = ready_sheet
    old_draft = copy.deepcopy(sheet.draft)
    old_runs = list(sheet.recognition_runs.values())
    old_source = source.file_sha256
    if change == "policy":
        game.leader_adjustable = not game.leader_adjustable
    # A request opening/closing advances this counter twice without rescheduling.
    game.version += 2 if change == "lock_cycle" else 1
    game.save()
    report = validate(ready_sheet).validation_report
    assert not report["game_context"]["required"]
    assert not report["errors"]
    acknowledge(ready_sheet)
    publication = publish_scoresheet(**mutation(ready_sheet))
    sheet.refresh_from_db()
    source.refresh_from_db()
    assert sheet.draft == old_draft
    assert publication.source_asset_id == source.id
    assert source.file_sha256 == old_source
    assert list(sheet.recognition_runs.values()) == old_runs
    assert (
        publication.snapshot["final_score"]["team_a"],
        publication.snapshot["final_score"]["team_b"],
    ) == (2, 1)


@pytest.mark.parametrize("field", ["date", "start_time", "venue_name", "period", "home_team"])
def test_real_semantic_changes_are_visible_and_unreviewed_publish_is_zero_write(ready_sheet, field):
    setup, game, _, _, _, _ = ready_sheet
    if field == "date":
        game.date += timedelta(days=1)
    elif field == "start_time":
        game.start_time = "14:05"
    elif field == "venue_name":
        game.venue_name = "新安排的场地"
    elif field == "period":
        game.period = Period.objects.create(
            season=game.season, code="def105", name="同时间不同场次时段", start_time=game.start_time
        )
    else:
        game.home_team = setup["teams"][2]
    game.save()
    sheet = validate(ready_sheet)
    assert sheet.validation_report["game_context"]["required"]
    assert sheet.validation_report["game_context"]["differences"]
    before = snapshot()
    with pytest.raises(ScoresheetError) as failed:
        publish_scoresheet(**mutation(ready_sheet))
    assert failed.value.code == "GAME_CONTEXT_REVIEW_REQUIRED"
    assert snapshot() == before


def test_review_preserves_source_provenance_and_manual_values_then_publishes(ready_sheet):
    _, game, _, _, sheet, _ = ready_sheet
    # Seed non-empty historical recognition evidence directly, without a paid call.
    sheet.draft["recognition"] = {
        "run_id": str(sheet.recognition_runs.first().id),
        "notes": "原始识别说明，不能被上下文复核改写",
        "problem_paths": ["/teams/0/players/0/name"],
        "table_personnel": ["人工保留人员"],
        "applied_at": "2026-08-20T03:04:05.123456Z",
    }
    sheet.save(update_fields=["draft"])
    old_draft = copy.deepcopy(sheet.draft)
    old_source = list(GameMediaAsset.objects.values())
    old_runs = list(ScoresheetRecognitionRun.objects.values())
    old_revisions = list(ScoresheetRevision.objects.values())
    game.date += timedelta(days=1)
    game.venue_name = "复核后的场地"
    game.save()
    validate(ready_sheet)
    reviewed = review(ready_sheet)
    for key in (
        "source",
        "recognition",
        "score_events",
        "teams",
        "officials",
        "stated_period_scores",
    ):
        assert reviewed.draft[key] == old_draft[key]
    assert reviewed.draft["header"]["venue"] == "复核后的场地"
    assert list(GameMediaAsset.objects.values()) == old_source
    assert list(ScoresheetRecognitionRun.objects.values()) == old_runs
    for old in old_revisions:
        assert ScoresheetRevision.objects.filter(id=old["id"]).values().get() == old
    assert AdminAuditLog.objects.filter(action="SCORESHEET_GAME_CONTEXT_REVIEWED").count() == 1
    report = validate(ready_sheet).validation_report
    assert not report["game_context"]["required"]
    assert not report["errors"]
    acknowledge(ready_sheet)
    publish_scoresheet(**mutation(ready_sheet))


def test_legacy_missing_evidence_requires_one_review_and_gets_remain_readonly(ready_sheet):
    setup, game, _, _, sheet, _ = ready_sheet
    for key in (
        "context_schema",
        "season_id",
        "division_id",
        "period_id",
        "period_name",
        "stage",
        "round_number",
        "group_id",
    ):
        sheet.game_prior_snapshot.pop(key)
    sheet.save(update_fields=["game_prior_snapshot"])
    game.version += 3
    game.save(update_fields=["version"])
    client = Client()
    client.force_login(setup["admin"])
    before = snapshot()
    assert client.get(f"/api/v1/scoresheets/{sheet.id}").status_code == 200
    assert snapshot() == before
    assert validate(ready_sheet).validation_report["game_context"]["required"]
    review(ready_sheet)
    assert not validate(ready_sheet).validation_report["game_context"]["required"]
    before = snapshot()
    assert client.get(f"/api/v1/scoresheets/{sheet.id}").status_code == 200
    assert snapshot() == before
    acknowledge(ready_sheet)
    publish_scoresheet(**mutation(ready_sheet))


@pytest.mark.parametrize("change", ["draft", "source_version", "source_asset", "period", "record"])
def test_review_token_rejects_changed_scope_and_has_zero_partial_writes(ready_sheet, change):
    _, game, _, _, sheet, _ = ready_sheet
    game.venue_name = "需要复核的场地"
    game.save()
    sheet = validate(ready_sheet)
    token = sheet.validation_report["game_context"]["review_token"]
    if change == "draft":
        save_draft_changes(
            **mutation(ready_sheet), changes=[{"path": "/officials/0/name", "value": "人工新编辑"}]
        )
    elif change in {"source_version", "source_asset"}:
        if change == "source_version":
            sheet.source_version += 1
        else:
            sheet.source_asset = None
        sheet.save()
    elif change == "period":
        game.period = Period.objects.create(
            season=game.season, code="other", name="另一个时段", start_time=game.start_time
        )
        game.save()
    else:
        from django.core import signing

        from core.services.scoresheet_game_context import CONTEXT_SALT

        payload = signing.loads(token, salt=CONTEXT_SALT)
        payload["scoresheet_id"] = str(uuid4())
        token = signing.dumps(payload, salt=CONTEXT_SALT)
    before = snapshot()
    with pytest.raises(ScoresheetError) as failed:
        review_scoresheet_game_context(**mutation(ready_sheet), review_token=token, confirmed=True)
    assert failed.value.status == 409
    assert snapshot() == before


def test_replaced_player_identity_needs_explicit_mapping_not_same_name(ready_sheet):
    _, _, players, _, sheet, _ = ready_sheet
    player = players["A"]
    player.active = False
    player.save()
    replacement = RosterPlayer.objects.create(
        team=player.team, name=player.name, jersey_number=player.jersey_number
    )
    report = validate(ready_sheet).validation_report
    assert report["game_context"]["player_conflicts"][0]["name"] == player.name
    review(ready_sheet)  # A generic context confirmation cannot transfer these points.
    assert validate(ready_sheet).validation_report["game_context"]["required"]
    review(ready_sheet, player_mappings=[{"side": "A", "row": 1, "player_id": str(replacement.id)}])
    assert not validate(ready_sheet).validation_report["errors"]
    acknowledge(ready_sheet)
    publication = publish_scoresheet(**mutation(ready_sheet))
    assert (
        GamePlayerStat.objects.get(publication=publication, roster_player=replacement).points == 2
    )
    assert not GamePlayerStat.objects.filter(publication=publication, roster_player=player).exists()


def test_current_roster_eligibility_blocks_after_review(ready_sheet):
    _, _, players, _, _, _ = ready_sheet
    player = players["A"]
    player.eligible = False
    player.save()
    validate(ready_sheet)
    review(ready_sheet)
    errors = validate(ready_sheet).validation_report["errors"]
    assert any(error["code"] == "CURRENT_ROSTER_MISMATCH" for error in errors)
    before = snapshot()
    with pytest.raises(ScoresheetError):
        publish_scoresheet(**mutation(ready_sheet))
    assert snapshot() == before


def test_same_name_players_require_unambiguous_identity_and_keep_distinct_stats(ready_sheet):
    _, _, players, _, sheet, _ = ready_sheet
    original = players["A"]
    other = RosterPlayer.objects.filter(team=original.team).exclude(id=original.id).first()
    other.name = original.name
    other.save()
    # Existing distinct jersey numbers still disambiguate legitimate same-name players.
    for player in sheet.draft["teams"][0]["players"]:
        if player["jersey_number"] == other.jersey_number:
            player["name"] = original.name
            renamed_row = player["row"]
    sheet.save(update_fields=["draft"])
    validate(ready_sheet)
    # Renaming to another existing name is a real identity ambiguity. Confirm the
    # changed row explicitly; matching the unchanged row must still remain valid.
    review(ready_sheet, player_mappings=[{"side": "A", "row": renamed_row,
                                        "player_id": str(other.id)}])
    assert not validate(ready_sheet).validation_report["errors"]
    acknowledge(ready_sheet)
    publication = publish_scoresheet(**mutation(ready_sheet))
    assert GamePlayerStat.objects.get(publication=publication, roster_player=original).points == 2
    assert GamePlayerStat.objects.get(publication=publication, roster_player=other).points == 0


def test_real_reschedule_submission_and_withdrawal_does_not_require_new_source(ready_sheet):
    from core.services.rescheduling import submit_reschedule, withdraw_request
    from core.tests.test_rescheduling import valid_submission_time

    setup, game, _, _, sheet, _ = ready_sheet
    before_draft = copy.deepcopy(sheet.draft)
    before_source = sheet.source_asset_id
    now = valid_submission_time(game.date, setup["target_date"])
    request = submit_reschedule(
        actor=setup["accounts"][0],
        game_id=game.id,
        expected_game_version=game.version,
        target_date=setup["target_date"],
        target_period_id=setup["period"].id,
        now=now,
    )
    withdraw_request(
        actor=setup["accounts"][0],
        request_id=request.id,
        expected_version=request.version,
        now=now + timedelta(minutes=1),
    )
    game.refresh_from_db()
    assert game.active_reschedule_request_id is None
    assert game.version > sheet.game_prior_snapshot["game_version"]
    assert not validate(ready_sheet).validation_report["game_context"]["required"]
    acknowledge(ready_sheet)
    publication = publish_scoresheet(**mutation(ready_sheet))
    assert publication.source_asset_id == before_source
    assert publication.snapshot == before_draft


def test_context_confirmation_expires_and_requires_explicit_consent(ready_sheet, monkeypatch):
    from django.core import signing

    _, game, _, _, _, _ = ready_sheet
    game.venue_name = "期限测试"
    game.save()
    sheet = validate(ready_sheet)
    params = {
        **mutation(ready_sheet),
        "review_token": sheet.validation_report["game_context"]["review_token"],
    }
    before = snapshot()
    with pytest.raises(ScoresheetError) as error:
        review_scoresheet_game_context(**params, confirmed=False)
    assert error.value.code == "CONFIRMATION_REQUIRED"
    now = signing.time.time()
    monkeypatch.setattr(signing.time, "time", lambda: now + 601)
    with pytest.raises(ScoresheetError) as error:
        review_scoresheet_game_context(**params, confirmed=True)
    assert error.value.code == "GAME_CONTEXT_STALE"
    assert snapshot() == before


def test_stage_and_round_changes_show_readable_values_not_enums_or_ids(ready_sheet):
    _, game, _, _, _, _ = ready_sheet
    game.stage = Game.Stage.SEMIFINAL
    game.round_number += 1
    game.save()
    differences = validate(ready_sheet).validation_report["game_context"]["differences"]
    by_field = {difference["field"]: difference for difference in differences}
    assert by_field["stage"]["before"] == "小组赛"
    assert by_field["stage"]["after"] == "半决赛"
    assert by_field["round_number"]["after"] == f"第 {game.round_number} 轮"


def test_changed_context_after_successful_review_still_blocks_publication(ready_sheet):
    _, game, _, _, _, _ = ready_sheet
    game.venue_name = "第一次变化"
    game.save()
    validate(ready_sheet)
    review(ready_sheet)
    validate(ready_sheet)
    acknowledge(ready_sheet)
    game.venue_name = "第二次变化"
    game.save()
    before = snapshot()
    with pytest.raises(ScoresheetError) as error:
        publish_scoresheet(**mutation(ready_sheet))
    assert error.value.code == "GAME_CONTEXT_REVIEW_REQUIRED"
    assert snapshot() == before


def test_wrong_player_mapping_is_atomic_and_current_actor_is_rechecked(ready_sheet):
    setup, game, _, _, _, _ = ready_sheet
    game.venue_name = "重新核对"
    game.save()
    validate(ready_sheet)
    before = snapshot()
    with pytest.raises(ScoresheetError) as error:
        review(ready_sheet, player_mappings=[{"side": "A", "row": 1, "player_id": str(uuid4())}])
    assert error.value.code == "PLAYER_MAPPING_INVALID"
    assert snapshot() == before
    Account.objects.filter(id=setup["admin"].id).update(is_active=False)
    before = snapshot()
    with pytest.raises(ScoresheetError) as error:
        review(ready_sheet)
    assert error.value.code == "ADMIN_REQUIRED"
    assert snapshot() == before


def test_concurrent_review_only_one_revision_and_audit(ready_sheet):
    setup, game, _, _, _, _ = ready_sheet
    game.venue_name = "并发复核场地"
    game.save()
    sheet = validate(ready_sheet)
    params = mutation(ready_sheet)
    token = sheet.validation_report["game_context"]["review_token"]
    barrier = Barrier(2)

    def run():
        close_old_connections()
        try:
            actor = Account.objects.get(id=setup["admin"].id)
            barrier.wait(timeout=10)
            try:
                review_scoresheet_game_context(
                    **{**params, "actor": actor}, review_token=token, confirmed=True
                )
                return "ok"
            except ScoresheetError as error:
                return error.code
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: run(), range(2)))
    assert sorted(results) == ["VERSION_CONFLICT", "ok"]
    assert AdminAuditLog.objects.filter(action="SCORESHEET_GAME_CONTEXT_REVIEWED").count() == 1


@pytest.mark.parametrize("surface", ["WEB", "MINIAPP"])
def test_http_review_roundtrip_and_idempotent_unknown_result_retry(ready_sheet, surface):
    setup, game, _, _, sheet, token = ready_sheet
    if surface == "MINIAPP":
        ScoresheetEditLease.objects.all().delete()
        _, token, _, _ = acquire_edit_lease(
            scoresheet_id=sheet.id, actor=setup["admin"], client_id="mini-1", surface="MINIAPP"
        )
        session = issue_session(setup["admin"])
        client = Client(enforce_csrf_checks=True, HTTP_AUTHORIZATION=f"Bearer {session}")
    else:
        client = Client()
        client.force_login(setup["admin"])
    game.venue_name = "API 复核场地"
    game.save()
    context = {
        "expected_version": sheet.draft_version,
        "lease_token": token,
        "client_id": "web-1" if surface == "WEB" else "mini-1",
        "surface": surface,
    }
    result = client.post(
        f"/api/v1/scoresheets/{sheet.id}/validate", context, content_type="application/json"
    )
    assert result.status_code == 200, result.content
    payload = {
        **context,
        "confirmed": True,
        "review_token": result.json()["validation_report"]["game_context"]["review_token"],
    }
    path = f"/api/v1/scoresheets/{sheet.id}/game-context/review"
    result = client.post(
        path, payload, content_type="application/json", HTTP_IDEMPOTENCY_KEY="def105-review"
    )
    assert result.status_code == 200, result.content
    before = snapshot()
    replay = client.post(
        path, payload, content_type="application/json", HTTP_IDEMPOTENCY_KEY="def105-review"
    )
    assert replay.status_code == 200, replay.content
    assert replay.json()["draft_version"] == result.json()["draft_version"]
    assert snapshot() == before


@pytest.mark.parametrize(
    "role,surface,archived,published,allowed",
    [
        ("ADMIN", "WEB", False, False, True),
        ("ADMIN", "MINIAPP", False, False, True),
        ("ADMIN", "WEB", False, True, False),
        ("ADMIN", "MINIAPP", False, True, False),
        ("SUPERADMIN", "WEB", True, True, True),
        ("SUPERADMIN", "MINIAPP", True, True, False),
        ("SUPERADMIN", "WEB", True, False, False),
        ("ADMIN", "WEB", True, True, False),
    ],
)
def test_review_keeps_publication_and_archive_permissions(
    ready_sheet, role, surface, archived, published, allowed
):
    setup, game, _, _, sheet, _ = ready_sheet
    if published:
        publish_scoresheet(**mutation(ready_sheet))
    if archived:
        Season.objects.filter(id=game.season_id).update(status=Season.Status.ARCHIVED)
    actor = setup["admin"] if role == "SUPERADMIN" else setup["ordinary_admin"]
    # Create the proposed lease directly to exercise service authorization even
    # when a bad/stale client somehow presents a previously acquired lease.
    import hashlib

    from django.utils import timezone

    ScoresheetEditLease.objects.all().delete()
    ScoresheetEditLease.objects.create(
        scoresheet=sheet,
        account=actor,
        client_id="review-client",
        surface=surface,
        token_hash=hashlib.sha256(b"fixture-token").hexdigest(),
        archived_correction=archived,
        expires_at=timezone.now() + timedelta(seconds=60),
        last_heartbeat_at=timezone.now(),
    )
    params = mutation(
        ready_sheet,
        actor=actor,
        lease_token="fixture-token",
        surface=surface,
        client_id="review-client",
    )
    game.venue_name = "权限复核场地"
    game.save(update_fields=["venue_name"])
    before = snapshot()
    if not allowed:
        with pytest.raises(ScoresheetError) as denied:
            validate_scoresheet(**params)
        assert denied.value.status in {403, 409}
        assert snapshot() == before
        # A token is a snapshot proof, not authorization. Even a correctly
        # signed token from an earlier authorized validation cannot grant a
        # downgraded/otherwise forbidden actor access to the direct endpoint.
        from django.core import signing

        from core.services.scoresheet_game_context import (
            CONTEXT_SALT,
            current_context,
            review_binding,
        )

        sheet.refresh_from_db()
        signed_token = signing.dumps(
            review_binding(sheet, current_context(game)), salt=CONTEXT_SALT
        )
        with pytest.raises(ScoresheetError) as denied:
            review_scoresheet_game_context(
                **params, confirmed=True, review_token=signed_token
            )
        assert denied.value.status in {403, 409}
        assert snapshot() == before
    else:
        validated = validate_scoresheet(**params)
        review_scoresheet_game_context(
            **params,
            confirmed=True,
            review_token=validated.validation_report["game_context"]["review_token"],
        )
