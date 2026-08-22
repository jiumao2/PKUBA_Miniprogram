from __future__ import annotations

import copy
import io
import json
from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings
from django.utils import timezone
from openpyxl import load_workbook
from PIL import Image
from pypdf import PdfReader

from core.models import (
    Account,
    AdminAuditLog,
    ApiIdempotencyRecord,
    Game,
    GameMediaAsset,
    GamePlayerStat,
    GameScoresheet,
    GameTeamStat,
    RosterPlayer,
    ScoresheetEditLease,
    ScoresheetPublication,
    ScoresheetRecognitionRun,
)
from core.scoresheet_v2.recognition import prepare_image
from core.services.game_media import replace_game_media, upload_game_media
from core.services.scoresheet_recognition import (
    RecognitionAttemptError,
    _renew_worker_lease,
    claim_next_run,
    execute_claim,
    run_once,
)
from core.services.scoresheet_renderer import render_scoresheet_pdf
from core.services.scoresheets import (
    ScoresheetError,
    acknowledge_warnings,
    acquire_edit_lease,
    force_takeover_edit_lease,
    publish_scoresheet,
    release_edit_lease,
    retry_recognition,
    review_region,
    save_draft_changes,
    sync_scoresheet,
    validate_scoresheet,
)
from core.services.wechat import issue_session
from core.tests.factories import reschedule_setup

pytestmark = pytest.mark.django_db(transaction=True)


def image_file(name: str = "scoresheet.jpg") -> SimpleUploadedFile:
    content = io.BytesIO()
    Image.new("RGB", (800, 1130), color=(246, 244, 239)).save(
        content, format="JPEG", quality=90
    )
    return SimpleUploadedFile(name, content.getvalue(), content_type="image/jpeg")


def create_scoresheet():
    setup = reschedule_setup()
    setup["ordinary_admin"] = setup["admin"]
    setup["admin"] = setup["superadmin"]
    game = setup["games"][0]
    player_rows = {}
    for side, team, prefix in (
        ("A", game.home_team, "甲队"),
        ("B", game.away_team, "乙队"),
    ):
        created = [
            RosterPlayer.objects.create(
                team=team,
                name=f"{prefix}{index}号",
                jersey_number=str(index + 3),
            )
            for index in range(1, 6)
        ]
        player_rows[side] = created[0]
    asset = upload_game_media(
        actor=setup["superadmin"],
        game=game,
        kind=GameMediaAsset.Kind.SCORESHEET,
        scoresheet_complete_confirmed=True,
        uploaded_file=image_file(),
    )
    return setup, game, player_rows, asset, GameScoresheet.objects.get(game=game)


def valid_document(scoresheet: GameScoresheet) -> dict[str, object]:
    document = copy.deepcopy(scoresheet.draft)
    player_a = document["teams"][0]["players"][0]
    player_b = document["teams"][1]["players"][0]
    for player in document["teams"][0]["players"][:5]:
        player["participation"] = "starter"
    for player in document["teams"][1]["players"][:5]:
        player["participation"] = "starter"
    document["score_events"] = [
        {
            "sequence": 1,
            "team": "A",
            "period": 1,
            "points": 2,
            "cumulative_score": 2,
            "scorer_jersey": player_a["jersey_number"],
            "mark": "diagonal",
            "scorer_circled": False,
            "boundary": "none",
            "ink_role": "q1_q3",
        },
        {
            "sequence": 2,
            "team": "B",
            "period": 1,
            "points": 1,
            "cumulative_score": 1,
            "scorer_jersey": player_b["jersey_number"],
            "mark": "filled_dot",
            "scorer_circled": False,
            "boundary": "game_end",
            "ink_role": "q1_q3",
        },
    ]
    document["stated_period_scores"] = [
        {"period": 1, "team_a": 2, "team_b": 1},
        {"period": 2, "team_a": 0, "team_b": 0},
        {"period": 3, "team_a": 0, "team_b": 0},
        {"period": 4, "team_a": 0, "team_b": 0},
    ]
    document["final_score"] = {
        "team_a": 2,
        "team_b": 1,
        "winner_name": document["teams"][0]["name"],
        "ended_at": "14:20",
    }
    for official in document["officials"]:
        if official["role"] == "scorer":
            official.update({"name": "记录员", "signature": "present"})
        elif official["role"] == "timer":
            official.update({"name": "计时员", "signature": "present"})
        elif official["role"] == "crew_chief":
            official["signature"] = "present"
    return document


def obtain_lease(scoresheet: GameScoresheet, actor: Account, client_id: str = "web-1") -> str:
    _, token, read_only, _reason = acquire_edit_lease(
        scoresheet_id=scoresheet.id,
        actor=actor,
        client_id=client_id,
        surface=ScoresheetEditLease.Surface.WEB,
    )
    assert read_only is False
    assert token
    return token


def make_ready(
    scoresheet: GameScoresheet,
    actor: Account,
    token: str,
    *,
    client_id: str = "web-1",
) -> GameScoresheet:
    save_draft_changes(
        scoresheet_id=scoresheet.id,
        actor=actor,
        expected_version=scoresheet.draft_version,
        lease_token=token,
        client_id=client_id,
        surface=ScoresheetEditLease.Surface.WEB,
        changes=[{"path": "/", "operation": "SET", "value": valid_document(scoresheet)}],
        explicit_save=True,
    )
    scoresheet.refresh_from_db()
    validate_scoresheet(
        scoresheet_id=scoresheet.id,
        actor=actor,
        expected_version=scoresheet.draft_version,
        lease_token=token,
        client_id=client_id,
        surface=ScoresheetEditLease.Surface.WEB,
    )
    scoresheet.refresh_from_db()
    warning_ids = [row["id"] for row in scoresheet.validation_report.get("warnings", [])]
    if warning_ids:
        acknowledge_warnings(
            scoresheet_id=scoresheet.id,
            actor=actor,
            expected_version=scoresheet.draft_version,
            lease_token=token,
            client_id=client_id,
            surface=ScoresheetEditLease.Surface.WEB,
            warning_ids=warning_ids,
        )
        scoresheet.refresh_from_db()
    assert scoresheet.validation_report["errors"] == []
    return scoresheet


def test_single_editor_lease_expiry_and_superadmin_takeover(tmp_path):
    with override_settings(MEDIA_ROOT=tmp_path):
        setup, _, _, _, scoresheet = create_scoresheet()
    second_admin = Account.objects.create_user(
        username="scoresheet-admin-2", password="password", role=Account.Role.SUPERADMIN
    )
    superadmin = Account.objects.create_user(
        username="scoresheet-root", password="password", role=Account.Role.SUPERADMIN
    )

    old_token = obtain_lease(scoresheet, setup["admin"], "web-owner")
    holder, other_token, read_only, reason = acquire_edit_lease(
        scoresheet_id=scoresheet.id,
        actor=second_admin,
        client_id="mini-reader",
        surface=ScoresheetEditLease.Surface.MINIAPP,
    )
    assert read_only is True
    assert other_token is None
    assert reason
    assert holder is not None
    assert holder.account_id == setup["admin"].id

    with pytest.raises(ScoresheetError, match="二次确认"):
        force_takeover_edit_lease(
            scoresheet_id=scoresheet.id,
            actor=superadmin,
            client_id="root-mini",
            surface=ScoresheetEditLease.Surface.MINIAPP,
            confirmed=False,
        )
    _, root_token = force_takeover_edit_lease(
        scoresheet_id=scoresheet.id,
        actor=superadmin,
        client_id="root-mini",
        surface=ScoresheetEditLease.Surface.MINIAPP,
        confirmed=True,
    )
    assert root_token
    with pytest.raises(ScoresheetError) as lost:
        save_draft_changes(
            scoresheet_id=scoresheet.id,
            actor=setup["admin"],
            expected_version=scoresheet.draft_version,
            lease_token=old_token,
            client_id="web-owner",
            surface=ScoresheetEditLease.Surface.WEB,
            changes=[{"path": "/header/game_number", "value": "失效客户端"}],
        )
    assert lost.value.code == "LEASE_LOST"

    lease = ScoresheetEditLease.objects.get(scoresheet=scoresheet)
    lease.expires_at = timezone.now() - timedelta(seconds=1)
    lease.save(update_fields=["expires_at"])
    new_holder, new_token, read_only, _reason = acquire_edit_lease(
        scoresheet_id=scoresheet.id,
        actor=second_admin,
        client_id="mini-reader",
        surface=ScoresheetEditLease.Surface.MINIAPP,
    )
    assert read_only is False
    assert new_token
    assert new_holder.account_id == second_admin.id
    assert AdminAuditLog.objects.filter(action="SCORESHEET_LEASE_FORCE_TAKEN").exists()


def test_ordinary_admin_is_read_only_during_ai_then_can_edit_after_failure(tmp_path):
    with override_settings(MEDIA_ROOT=tmp_path, QWEN_API_KEY="test-key"):
        setup, _, _, _, scoresheet = create_scoresheet()
    ordinary = setup["ordinary_admin"]

    holder, token, read_only, reason = acquire_edit_lease(
        scoresheet_id=scoresheet.id,
        actor=ordinary,
        client_id="ordinary-before-ai",
        surface=ScoresheetEditLease.Surface.WEB,
    )
    assert holder is None
    assert token is None
    assert read_only is True
    assert "识别" in reason

    ScoresheetRecognitionRun.objects.filter(
        scoresheet=scoresheet,
        source_version=scoresheet.source_version,
    ).update(
        status=ScoresheetRecognitionRun.Status.FAILED,
        last_error_code="CREDENTIALS_MISSING",
        finished_at=timezone.now(),
    )
    holder, token, read_only, _reason = acquire_edit_lease(
        scoresheet_id=scoresheet.id,
        actor=ordinary,
        client_id="ordinary-after-ai",
        surface=ScoresheetEditLease.Surface.WEB,
    )

    assert read_only is False
    assert token
    assert holder.account_id == ordinary.id


def test_field_change_invalidates_region_and_is_available_to_sync(tmp_path):
    with override_settings(MEDIA_ROOT=tmp_path):
        setup, _, _, _, scoresheet = create_scoresheet()
    token = obtain_lease(scoresheet, setup["admin"])
    review_region(
        scoresheet_id=scoresheet.id,
        actor=setup["admin"],
        expected_version=scoresheet.draft_version,
        lease_token=token,
        client_id="web-1",
        surface=ScoresheetEditLease.Surface.WEB,
        region="SOURCE_GAME",
        reviewed=True,
    )
    scoresheet.refresh_from_db()
    after_event = scoresheet.event_sequence
    old_version = scoresheet.draft_version
    save_draft_changes(
        scoresheet_id=scoresheet.id,
        actor=setup["admin"],
        expected_version=old_version,
        lease_token=token,
        client_id="web-1",
        surface=ScoresheetEditLease.Surface.WEB,
        changes=[{"path": "/header/game_number", "value": "TEST-UPDATED"}],
    )
    scoresheet.refresh_from_db()
    assert scoresheet.draft_version == old_version + 1
    assert "SOURCE_GAME" not in scoresheet.reviewed_regions
    events = sync_scoresheet(scoresheet, after_event)
    assert [event.event_type for event in events] == ["FIELD_EDIT"]
    assert events[0].changed_fields[0]["path"] == "/header/game_number"
    with pytest.raises(ScoresheetError) as stale:
        save_draft_changes(
            scoresheet_id=scoresheet.id,
            actor=setup["admin"],
            expected_version=old_version,
            lease_token=token,
            client_id="web-1",
            surface=ScoresheetEditLease.Surface.WEB,
            changes=[{"path": "/header/game_number", "value": "旧值覆盖"}],
        )
    assert stale.value.code == "VERSION_CONFLICT"


def test_publish_does_not_require_region_reviews_and_keeps_field_change_log(tmp_path):
    with override_settings(MEDIA_ROOT=tmp_path):
        setup, _, _, _, scoresheet = create_scoresheet()
    token = obtain_lease(scoresheet, setup["admin"])
    scoresheet = make_ready(scoresheet, setup["admin"], token)

    updated = copy.deepcopy(scoresheet.draft)
    updated["header"]["game_number"] = "只重核比赛信息"
    save_draft_changes(
        scoresheet_id=scoresheet.id,
        actor=setup["admin"],
        expected_version=scoresheet.draft_version,
        lease_token=token,
        client_id="web-1",
        surface=ScoresheetEditLease.Surface.WEB,
        changes=[{"path": "/", "operation": "SET", "value": updated}],
    )
    scoresheet.refresh_from_db()
    assert scoresheet.reviewed_regions == {}
    latest_change = scoresheet.change_logs.order_by("-event_sequence").first()
    assert latest_change is not None
    assert [row["path"] for row in latest_change.changed_fields] == ["/header/game_number"]

    validate_scoresheet(
        scoresheet_id=scoresheet.id,
        actor=setup["admin"],
        expected_version=scoresheet.draft_version,
        lease_token=token,
        client_id="web-1",
        surface=ScoresheetEditLease.Surface.WEB,
    )
    scoresheet.refresh_from_db()
    warning_ids = [row["id"] for row in scoresheet.validation_report.get("warnings", [])]
    if warning_ids:
        acknowledge_warnings(
            scoresheet_id=scoresheet.id,
            actor=setup["admin"],
            expected_version=scoresheet.draft_version,
            lease_token=token,
            client_id="web-1",
            surface=ScoresheetEditLease.Surface.WEB,
            warning_ids=warning_ids,
        )
    publication = publish_scoresheet(
        scoresheet_id=scoresheet.id,
        actor=setup["admin"],
        expected_version=scoresheet.draft_version,
        lease_token=token,
        client_id="web-1",
        surface=ScoresheetEditLease.Surface.WEB,
    )
    assert publication.publication_number == 1


def test_publish_endpoint_replays_same_idempotency_key(tmp_path):
    with override_settings(MEDIA_ROOT=tmp_path):
        setup, _, _, _, scoresheet = create_scoresheet()
    lease_token = obtain_lease(scoresheet, setup["admin"])
    scoresheet = make_ready(scoresheet, setup["admin"], lease_token)
    payload = {
        "expected_version": scoresheet.draft_version,
        "lease_token": lease_token,
        "client_id": "web-1",
        "surface": ScoresheetEditLease.Surface.WEB,
    }
    client = Client()
    client.force_login(setup["admin"])
    path = f"/api/v1/scoresheets/{scoresheet.id}/publish"

    first = client.post(
        path,
        data=json.dumps(payload),
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY="scoresheet-publish-test",
    )
    replayed = client.post(
        path,
        data=json.dumps(payload),
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY="scoresheet-publish-test",
    )
    conflicting = client.post(
        path,
        data=json.dumps({**payload, "expected_version": payload["expected_version"] + 1}),
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY="scoresheet-publish-test",
    )

    assert first.status_code == 200, first.content
    assert replayed.status_code == 200
    assert replayed.json()["publication"] == first.json()["publication"]
    assert conflicting.status_code == 409
    assert conflicting.json()["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert ScoresheetPublication.objects.filter(scoresheet=scoresheet).count() == 1
    assert ApiIdempotencyRecord.objects.filter(operation="scoresheet.publish").count() == 1


def test_web_edit_is_returned_by_miniapp_sync_endpoint(tmp_path):
    with override_settings(MEDIA_ROOT=tmp_path):
        setup, _, _, _, scoresheet = create_scoresheet()
    second_admin = Account.objects.create_user(
        username="mini-sync-admin", password="password", role=Account.Role.ADMIN
    )
    web = Client()
    web.force_login(setup["admin"])
    lease_response = web.post(
        f"/api/v1/scoresheets/{scoresheet.id}/lease",
        data=json.dumps({"client_id": "web-sync", "surface": "WEB"}),
        content_type="application/json",
    )
    assert lease_response.status_code == 200
    lease_token = lease_response.json()["lease_token"]
    start_event = scoresheet.event_sequence
    saved = web.patch(
        f"/api/v1/scoresheets/{scoresheet.id}/draft",
        data=json.dumps(
            {
                "expected_version": scoresheet.draft_version,
                "lease_token": lease_token,
                "client_id": "web-sync",
                "surface": "WEB",
                "changes": [{"path": "/header/game_number", "value": "跨端同步编号"}],
            }
        ),
        content_type="application/json",
    )
    assert saved.status_code == 200

    mini_token = issue_session(second_admin)
    synced = Client().get(
        f"/api/v1/scoresheets/{scoresheet.id}/sync",
        data={"after_version": scoresheet.draft_version, "after_event": start_event},
        HTTP_AUTHORIZATION=f"Bearer {mini_token}",
    )
    assert synced.status_code == 200
    assert synced.json()["current_version"] == scoresheet.draft_version + 1
    assert synced.json()["events"][-1]["changed_fields"][0]["after"] == "跨端同步编号"
    assert synced.json()["lease"]["surface"] == "WEB"


def test_miniapp_bearer_can_write_without_csrf_cookie(tmp_path):
    with override_settings(MEDIA_ROOT=tmp_path):
        setup, _, _, _, scoresheet = create_scoresheet()
    mini_token = issue_session(setup["admin"])
    mini = Client(enforce_csrf_checks=True)

    lease_response = mini.post(
        f"/api/v1/scoresheets/{scoresheet.id}/lease",
        data=json.dumps({"client_id": "mini-csrf", "surface": "MINIAPP"}),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {mini_token}",
    )

    assert lease_response.status_code == 200
    assert lease_response.json()["read_only"] is False
    lease_token = lease_response.json()["lease_token"]
    saved = mini.patch(
        f"/api/v1/scoresheets/{scoresheet.id}/draft",
        data=json.dumps(
            {
                "expected_version": scoresheet.draft_version,
                "lease_token": lease_token,
                "client_id": "mini-csrf",
                "surface": "MINIAPP",
                "changes": [{"path": "/header/game_number", "value": "MINI-NO-CSRF"}],
            }
        ),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {mini_token}",
    )

    assert saved.status_code == 200
    assert saved.json()["draft"]["header"]["game_number"] == "MINI-NO-CSRF"


def test_retryable_recognition_uses_initial_call_plus_three_retries(tmp_path, monkeypatch):
    from core.services import scoresheet_recognition as recognition

    with override_settings(MEDIA_ROOT=tmp_path, QWEN_API_KEY="test-key"):
        _, _, _, _, scoresheet = create_scoresheet()

        def fail(_run):
            raise RecognitionAttemptError(
                "QWEN_NETWORK_ERROR", "temporary", retryable=True
            )

        monkeypatch.setattr(recognition, "call_qwen", fail)
        expected_delays = [30, 30, 30]
        for attempt in range(4):
            before = timezone.now()
            outcome = run_once("test-worker")
            run = ScoresheetRecognitionRun.objects.get(scoresheet=scoresheet)
            assert run.attempt_count == attempt + 1
            if attempt < 3:
                assert outcome == "retry_wait"
                assert run.status == ScoresheetRecognitionRun.Status.RETRY_WAIT
                remaining = (run.next_attempt_at - before).total_seconds()
                assert expected_delays[attempt] - 2 <= remaining <= expected_delays[attempt] + 2
                run.next_attempt_at = timezone.now() - timedelta(seconds=1)
                run.save(update_fields=["next_attempt_at"])
            else:
                assert outcome == "failed"
                assert run.status == ScoresheetRecognitionRun.Status.FAILED
        scoresheet.refresh_from_db()
        assert scoresheet.status == GameScoresheet.Status.RECOGNITION_FAILED


def test_running_recognition_worker_lease_renews_during_long_provider_calls(tmp_path):
    with override_settings(MEDIA_ROOT=tmp_path, QWEN_API_KEY="test-key"):
        _, _, _, _, scoresheet = create_scoresheet()
        claim = claim_next_run("long-call-worker")
        assert claim is not None
        run = ScoresheetRecognitionRun.objects.get(scoresheet=scoresheet)
        run.worker_lease_expires_at = timezone.now() + timedelta(seconds=1)
        run.save(update_fields=["worker_lease_expires_at"])

        assert _renew_worker_lease(claim) is True

        run.refresh_from_db()
        assert run.worker_lease_expires_at >= timezone.now() + timedelta(minutes=4)


def test_qwen_prior_contains_only_team_and_player_names(tmp_path):
    from core.services.scoresheet_recognition import _prompt, _provider_prior

    with override_settings(MEDIA_ROOT=tmp_path):
        _, _, _, _, scoresheet = create_scoresheet()
    prior = _provider_prior(scoresheet)
    serialized = json.dumps(prior, ensure_ascii=False)
    prompt = _prompt(prior)
    assert set(prior) == {"teams"}
    assert set(prior["teams"]["A"]) == {"name", "players"}
    assert set(prior["teams"]["A"]["players"][0]) == {"name"}
    assert str(scoresheet.id) not in serialized
    assert scoresheet.game_prior_snapshot["date"] not in serialized
    assert scoresheet.game_prior_snapshot["venue"] not in serialized
    assert "甲队1号" in prompt and "乙队1号" in prompt


def test_late_success_is_retained_but_never_overwrites_human_edits(tmp_path, monkeypatch):
    from core.services import scoresheet_recognition as recognition

    with override_settings(MEDIA_ROOT=tmp_path):
        setup, _, _, _, scoresheet = create_scoresheet()
        token = obtain_lease(scoresheet, setup["admin"])
        save_draft_changes(
            scoresheet_id=scoresheet.id,
            actor=setup["admin"],
            expected_version=scoresheet.draft_version,
            lease_token=token,
            client_id="web-1",
            surface=ScoresheetEditLease.Surface.WEB,
            changes=[{"path": "/header/game_number", "value": "人工确认编号"}],
        )
        scoresheet.refresh_from_db()
        with override_settings(QWEN_API_KEY="test-key"):
            retry_recognition(
                scoresheet_id=scoresheet.id,
                actor=setup["admin"],
                expected_version=scoresheet.draft_version,
                lease_token=token,
                client_id="web-1",
                surface=ScoresheetEditLease.Surface.WEB,
            )
            claim = claim_next_run("race-worker")
        assert claim is not None
        monkeypatch.setattr(
            recognition,
            "call_qwen",
            lambda _run: ({"game": {"venue": "模型错误场地"}}, {"total_tokens": 12}),
        )
        assert execute_claim(claim) == "stored_not_applied"
    scoresheet.refresh_from_db()
    run = (
        ScoresheetRecognitionRun.objects.filter(scoresheet=scoresheet)
        .order_by("-cycle")
        .first()
    )
    assert run is not None
    assert scoresheet.draft["header"]["game_number"] == "人工确认编号"
    assert run.status == ScoresheetRecognitionRun.Status.SUCCEEDED
    assert run.provider_result["game"]["venue"] == "模型错误场地"
    assert scoresheet.change_logs.filter(
        event_type="RECOGNITION_STORED_NOT_APPLIED"
    ).exists()


def test_reupload_supersedes_old_claim_and_resets_attempt_budget(tmp_path, monkeypatch):
    from core.services import scoresheet_recognition as recognition

    with override_settings(MEDIA_ROOT=tmp_path, QWEN_API_KEY="test-key"):
        setup, _, _, old_asset, scoresheet = create_scoresheet()
        old_claim = claim_next_run("old-worker")
        assert old_claim is not None
        replacement = replace_game_media(
            actor=setup["admin"],
            asset_id=old_asset.id,
            expected_version=old_asset.version,
            scoresheet_complete_confirmed=True,
            uploaded_file=image_file("replacement.jpg"),
        )
        monkeypatch.setattr(
            recognition,
            "call_qwen",
            lambda _run: ({"game": {"venue": "迟到结果"}}, {}),
        )
        assert execute_claim(old_claim) == "superseded"
    scoresheet.refresh_from_db()
    runs = list(scoresheet.recognition_runs.order_by("source_version"))
    assert replacement.id == scoresheet.source_asset_id
    assert runs[0].status == ScoresheetRecognitionRun.Status.SUPERSEDED
    assert runs[1].status == ScoresheetRecognitionRun.Status.QUEUED
    assert runs[1].attempt_count == 0
    assert scoresheet.draft["header"]["venue"] != "迟到结果"


def test_publish_is_atomic_generates_stats_and_limits_leader_view(tmp_path, monkeypatch):
    with override_settings(MEDIA_ROOT=tmp_path):
        setup, game, _, asset, scoresheet = create_scoresheet()
        token = obtain_lease(scoresheet, setup["admin"])
        scoresheet = make_ready(scoresheet, setup["admin"], token)

        original_bulk_create = GameTeamStat.objects.bulk_create

        def fail_bulk_create(*_args, **_kwargs):
            raise RuntimeError("simulated stats failure")

        monkeypatch.setattr(GameTeamStat.objects, "bulk_create", fail_bulk_create)
        with pytest.raises(RuntimeError, match="simulated stats failure"):
            publish_scoresheet(
                scoresheet_id=scoresheet.id,
                actor=setup["admin"],
                expected_version=scoresheet.draft_version,
                lease_token=token,
                client_id="web-1",
                surface=ScoresheetEditLease.Surface.WEB,
            )
        assert ScoresheetPublication.objects.count() == 0
        game.refresh_from_db()
        asset.refresh_from_db()
        assert game.status == Game.Status.SCHEDULED
        assert asset.review_status == GameMediaAsset.ReviewStatus.PENDING
        assert ScoresheetEditLease.objects.filter(scoresheet=scoresheet).exists()

        monkeypatch.setattr(GameTeamStat.objects, "bulk_create", original_bulk_create)
        publication = publish_scoresheet(
            scoresheet_id=scoresheet.id,
            actor=setup["admin"],
            expected_version=scoresheet.draft_version,
            lease_token=token,
            client_id="web-1",
            surface=ScoresheetEditLease.Surface.WEB,
        )

    game.refresh_from_db()
    asset.refresh_from_db()
    scoresheet.refresh_from_db()
    assert publication.publication_number == 1
    assert game.status == Game.Status.COMPLETED
    assert [game.home_score, game.away_score] == [2, 1]
    assert asset.review_status == GameMediaAsset.ReviewStatus.APPROVED
    assert scoresheet.current_publication_id == publication.id
    assert GameTeamStat.objects.filter(publication=publication).count() == 2
    assert GamePlayerStat.objects.filter(publication=publication).count() == 10
    assert sum(
        GamePlayerStat.objects.filter(publication=publication).values_list("points", flat=True)
    ) == 3
    assert not ScoresheetEditLease.objects.filter(scoresheet=scoresheet).exists()

    leader_token = issue_session(setup["accounts"][0])
    leader_view = Client().get(
        f"/api/v1/game-media/games/{game.id}",
        HTTP_AUTHORIZATION=f"Bearer {leader_token}",
    )
    assert leader_view.status_code == 200
    assert [row["id"] for row in leader_view.json()["assets"]] == [str(asset.id)]
    public = Client().get(f"/api/v1/public/scoresheet-stats?game_id={game.id}")
    assert public.status_code == 200
    assert public.json()[0]["home_score"] == 2
    assert public.json()[0]["player_stats"][0].keys() >= {
        "points",
        "one_point_events",
        "two_point_events",
        "three_point_events",
        "personal_fouls",
    }

    with pytest.raises(ScoresheetError) as correction:
        publish_scoresheet(
            scoresheet_id=scoresheet.id,
            actor=setup["admin"],
            expected_version=scoresheet.draft_version,
            lease_token="released-token",
            client_id="web-1",
            surface=ScoresheetEditLease.Surface.WEB,
        )
    assert correction.value.code in {"LEASE_REQUIRED", "SUPERADMIN_REQUIRED"}


def test_published_source_correction_is_superadmin_only_and_old_publication_stays_live(
    tmp_path,
):
    with override_settings(MEDIA_ROOT=tmp_path):
        setup, game, _, asset, scoresheet = create_scoresheet()
        token = obtain_lease(scoresheet, setup["admin"])
        scoresheet = make_ready(scoresheet, setup["admin"], token)
        publication = publish_scoresheet(
            scoresheet_id=scoresheet.id,
            actor=setup["admin"],
            expected_version=scoresheet.draft_version,
            lease_token=token,
            client_id="web-1",
            surface=ScoresheetEditLease.Surface.WEB,
        )
        asset.refresh_from_db()

        admin_client = Client()
        admin_client.force_login(setup["ordinary_admin"])
        blocked_replace = admin_client.post(
            f"/api/v1/admin/game-media/{asset.id}/replace",
            data={
                "expected_version": asset.version,
                "scoresheet_complete_confirmed": "true",
                "image": image_file("admin-correction.jpg"),
            },
        )
        blocked_delete = admin_client.delete(
            f"/api/v1/admin/game-media/{asset.id}",
            data=json.dumps({"expected_version": asset.version}),
            content_type="application/json",
        )
        assert blocked_replace.status_code == 403
        assert blocked_replace.json()["code"] == "SUPERADMIN_REQUIRED"
        assert blocked_delete.status_code == 403
        assert blocked_delete.json()["code"] == "SUPERADMIN_REQUIRED"

        superadmin = Account.objects.create_user(
            username="scoresheet-correction-root",
            password="password",
            role=Account.Role.SUPERADMIN,
        )
        admin_client.force_login(superadmin)
        replacement = admin_client.post(
            f"/api/v1/admin/game-media/{asset.id}/replace",
            data={
                "expected_version": asset.version,
                "scoresheet_complete_confirmed": "true",
                "image": image_file("superadmin-correction.jpg"),
            },
        )
        assert replacement.status_code == 201

        scoresheet.refresh_from_db()
        asset.refresh_from_db()
        assert str(scoresheet.source_asset_id) == replacement.json()["id"]
        assert scoresheet.current_publication_id == publication.id
        assert scoresheet.recognition_runs.filter(
            source_version=scoresheet.source_version,
            status=ScoresheetRecognitionRun.Status.FAILED,
            last_error_code="CREDENTIALS_MISSING",
        ).exists()
        assert asset.deleted_at is not None

        leader_token = issue_session(setup["accounts"][0])
        leader_view = Client().get(
            f"/api/v1/game-media/games/{game.id}",
            HTTP_AUTHORIZATION=f"Bearer {leader_token}",
        )
        assert leader_view.status_code == 200
        assert [row["id"] for row in leader_view.json()["assets"]] == [str(asset.id)]
        old_source_url = leader_view.json()["assets"][0]["content_url"]
        assert Client().get(old_source_url).status_code == 200

        public = Client().get(f"/api/v1/public/scoresheet-stats?game_id={game.id}")
        assert public.status_code == 200
        assert public.json()[0]["publication_number"] == 1
        assert [public.json()[0]["home_score"], public.json()[0]["away_score"]] == [2, 1]

        admin_client.force_login(setup["ordinary_admin"])
        blocked_lease = admin_client.post(
            f"/api/v1/scoresheets/{scoresheet.id}/lease",
            data=json.dumps({"client_id": "normal-admin", "surface": "WEB"}),
            content_type="application/json",
        )
        assert blocked_lease.status_code == 200
        assert blocked_lease.json()["read_only"] is True
        assert "普通管理员" in blocked_lease.json()["read_only_reason"]


def test_publish_rejects_tampered_validation_and_stale_game_prior(tmp_path):
    with override_settings(MEDIA_ROOT=tmp_path):
        setup, game, _, _, scoresheet = create_scoresheet()
        token = obtain_lease(scoresheet, setup["admin"])
        scoresheet = make_ready(scoresheet, setup["admin"], token)

        report = copy.deepcopy(scoresheet.validation_report)
        report["draft_digest"] = "tampered"
        scoresheet.validation_report = report
        scoresheet.save(update_fields=["validation_report", "updated_at"])
        with pytest.raises(ScoresheetError) as tampered:
            publish_scoresheet(
                scoresheet_id=scoresheet.id,
                actor=setup["admin"],
                expected_version=scoresheet.draft_version,
                lease_token=token,
                client_id="web-1",
                surface=ScoresheetEditLease.Surface.WEB,
            )
        assert tampered.value.code == "VALIDATION_STALE"

        validate_scoresheet(
            scoresheet_id=scoresheet.id,
            actor=setup["admin"],
            expected_version=scoresheet.draft_version,
            lease_token=token,
            client_id="web-1",
            surface=ScoresheetEditLease.Surface.WEB,
        )
        game.version += 1
        game.save(update_fields=["version", "updated_at"])
        scoresheet.refresh_from_db()
        with pytest.raises(ScoresheetError) as stale_game:
            publish_scoresheet(
                scoresheet_id=scoresheet.id,
                actor=setup["admin"],
                expected_version=scoresheet.draft_version,
                lease_token=token,
                client_id="web-1",
                surface=ScoresheetEditLease.Surface.WEB,
            )
        assert stale_game.value.code == "GAME_PRIOR_STALE"


def test_publication_stats_are_immutable_and_pdf_is_single_page(tmp_path):
    with override_settings(MEDIA_ROOT=tmp_path):
        setup, _, _, _, scoresheet = create_scoresheet()
        document = valid_document(scoresheet)
        rendered = render_scoresheet_pdf(document)
        assert rendered.startswith(b"%PDF")
        assert len(PdfReader(io.BytesIO(rendered)).pages) == 1

        token = obtain_lease(scoresheet, setup["admin"])
        scoresheet = make_ready(scoresheet, setup["admin"], token)
        publication = publish_scoresheet(
            scoresheet_id=scoresheet.id,
            actor=setup["admin"],
            expected_version=scoresheet.draft_version,
            lease_token=token,
            client_id="web-1",
            surface=ScoresheetEditLease.Surface.WEB,
        )
        team_stat = publication.team_stats.first()
        player_stat = publication.player_stats.first()
        assert team_stat is not None and player_stat is not None
        team_stat.total_score = 99
        player_stat.points = 99
        with pytest.raises(ValidationError):
            team_stat.save()
        with pytest.raises(ValidationError):
            player_stat.save()
        with pytest.raises(ValidationError):
            publication.delete()


def test_published_scoresheet_export_endpoints(tmp_path):
    with override_settings(MEDIA_ROOT=tmp_path):
        setup, game, _, _, scoresheet = create_scoresheet()
        token = obtain_lease(scoresheet, setup["admin"])
        scoresheet = make_ready(scoresheet, setup["admin"], token)
        publish_scoresheet(
            scoresheet_id=scoresheet.id,
            actor=setup["admin"],
            expected_version=scoresheet.draft_version,
            lease_token=token,
            client_id="web-1",
            surface=ScoresheetEditLease.Surface.WEB,
        )

        client = Client()
        client.force_login(setup["admin"])
        pdf_response = client.get(f"/api/v1/scoresheets/{scoresheet.id}/exports/pdf")
        csv_response = client.get(f"/api/v1/scoresheets/{scoresheet.id}/exports/csv")
        xlsx_response = client.get(
            f"/api/v1/scoresheets/exports/seasons/{game.season_id}/xlsx"
        )

    assert pdf_response.status_code == 200
    assert pdf_response["Content-Type"] == "application/pdf"
    assert len(PdfReader(io.BytesIO(pdf_response.content)).pages) == 1

    assert csv_response.status_code == 200
    assert csv_response["Content-Type"].startswith("text/csv")
    csv_text = csv_response.content.decode("utf-8-sig")
    assert "甲队1号" in csv_text and "乙队1号" in csv_text

    assert xlsx_response.status_code == 200
    assert xlsx_response["Content-Type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    workbook = load_workbook(io.BytesIO(xlsx_response.content), read_only=True)
    assert workbook.sheetnames == ["球队单场统计", "球员单场统计"]
    assert workbook["球队单场统计"].max_row == 3
    assert workbook["球员单场统计"].max_row == 11


def test_superadmin_can_correct_and_republish_the_same_source(tmp_path):
    with override_settings(MEDIA_ROOT=tmp_path):
        setup, game, _, asset, scoresheet = create_scoresheet()
        token = obtain_lease(scoresheet, setup["admin"])
        scoresheet = make_ready(scoresheet, setup["admin"], token)
        first = publish_scoresheet(
            scoresheet_id=scoresheet.id,
            actor=setup["admin"],
            expected_version=scoresheet.draft_version,
            lease_token=token,
            client_id="web-1",
            surface=ScoresheetEditLease.Surface.WEB,
        )

        scoresheet.refresh_from_db()
        game.refresh_from_db()
        assert scoresheet.game_prior_snapshot["game_version"] == game.version

        superadmin = Account.objects.create_user(
            username="same-source-correction-root",
            password="password",
            role=Account.Role.SUPERADMIN,
        )
        root_token = obtain_lease(scoresheet, superadmin, "root-web")
        save_draft_changes(
            scoresheet_id=scoresheet.id,
            actor=superadmin,
            expected_version=scoresheet.draft_version,
            lease_token=root_token,
            client_id="root-web",
            surface=ScoresheetEditLease.Surface.WEB,
            changes=[{"path": "/officials/0/name", "value": "纠错记录员"}],
        )
        scoresheet.refresh_from_db()
        assert scoresheet.reviewed_regions == {}
        validate_scoresheet(
            scoresheet_id=scoresheet.id,
            actor=superadmin,
            expected_version=scoresheet.draft_version,
            lease_token=root_token,
            client_id="root-web",
            surface=ScoresheetEditLease.Surface.WEB,
        )
        scoresheet.refresh_from_db()
        warning_ids = [row["id"] for row in scoresheet.validation_report.get("warnings", [])]
        if warning_ids:
            acknowledge_warnings(
                scoresheet_id=scoresheet.id,
                actor=superadmin,
                expected_version=scoresheet.draft_version,
                lease_token=root_token,
                client_id="root-web",
                surface=ScoresheetEditLease.Surface.WEB,
                warning_ids=warning_ids,
            )
        second = publish_scoresheet(
            scoresheet_id=scoresheet.id,
            actor=superadmin,
            expected_version=scoresheet.draft_version,
            lease_token=root_token,
            client_id="root-web",
            surface=ScoresheetEditLease.Surface.WEB,
        )

        scoresheet.refresh_from_db()
        assert second.publication_number == 2
        assert second.supersedes_id == first.id
        assert second.source_asset_id == asset.id
        assert scoresheet.current_publication_id == second.id

        game.refresh_from_db()
        blocked_payload = {
            "expected_version": game.version,
            "date": game.date.isoformat(),
            "period_id": str(game.period_id),
            "start_time": game.start_time.strftime("%H:%M"),
            "standard_venue_id": str(setup["venues"][0].id),
            "venue_name": game.venue_name,
            "home_team_id": str(game.home_team_id),
            "away_team_id": str(game.away_team_id),
            "home_score": 88,
            "away_score": 77,
            "status": Game.Status.COMPLETED,
            "leader_adjustable": game.leader_adjustable,
            "override_rules": True,
            "confirmed": True,
        }
        blocked = Client().put(
            f"/api/v1/admin/mobile/games/{game.id}",
            data=json.dumps(blocked_payload),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {issue_session(superadmin)}",
        )
        assert blocked.status_code == 409
        assert blocked.json()["code"] == "SCORESHEET_REPUBLICATION_REQUIRED"


def test_public_stats_and_exports_use_the_immutable_publication_snapshot(tmp_path):
    with override_settings(MEDIA_ROOT=tmp_path):
        setup, game, _, _, scoresheet = create_scoresheet()
        published_home_name = game.home_team.name
        token = obtain_lease(scoresheet, setup["admin"])
        scoresheet = make_ready(scoresheet, setup["admin"], token)
        publish_scoresheet(
            scoresheet_id=scoresheet.id,
            actor=setup["admin"],
            expected_version=scoresheet.draft_version,
            lease_token=token,
            client_id="web-1",
            surface=ScoresheetEditLease.Surface.WEB,
        )

        game.home_score = 99
        game.away_score = 98
        game.save(update_fields=["home_score", "away_score", "updated_at"])
        game.home_team.name = "后来修改的队名"
        game.home_team.save(update_fields=["name", "updated_at"])

        public = Client().get(f"/api/v1/public/scoresheet-stats?game_id={game.id}")
        assert public.status_code == 200
        assert [public.json()[0]["home_score"], public.json()[0]["away_score"]] == [2, 1]
        assert public.json()[0]["home_name"] == published_home_name
        assert public.json()[0]["team_stats"][0]["team_name"] in {
            published_home_name,
            game.away_team.name,
        }

        client = Client()
        client.force_login(setup["admin"])
        csv_response = client.get(f"/api/v1/scoresheets/{scoresheet.id}/exports/csv")
        xlsx_response = client.get(
            f"/api/v1/scoresheets/exports/seasons/{game.season_id}/xlsx"
        )
        assert published_home_name in csv_response.content.decode("utf-8-sig")
        assert "后来修改的队名" not in csv_response.content.decode("utf-8-sig")
        workbook = load_workbook(io.BytesIO(xlsx_response.content), read_only=True)
        team_names = {
            row[2]
            for row in workbook["球队单场统计"].iter_rows(min_row=2, values_only=True)
        }
        assert published_home_name in team_names
        assert "后来修改的队名" not in team_names


def test_same_tab_resumes_exact_lease_token_without_log_noise(tmp_path):
    with override_settings(MEDIA_ROOT=tmp_path):
        setup, _, _, _, scoresheet = create_scoresheet()
    actor = setup["admin"]
    lease, token, read_only, reason = acquire_edit_lease(
        scoresheet_id=scoresheet.id,
        actor=actor,
        client_id="tab-a",
        surface=ScoresheetEditLease.Surface.WEB,
    )
    assert lease and token and read_only is False and reason == ""

    same_holder, missing_token, read_only, reason = acquire_edit_lease(
        scoresheet_id=scoresheet.id,
        actor=actor,
        client_id="tab-a",
        surface=ScoresheetEditLease.Surface.WEB,
    )
    assert same_holder and missing_token is None and read_only is True
    assert "凭据" in reason

    resumed, resumed_token, read_only, reason = acquire_edit_lease(
        scoresheet_id=scoresheet.id,
        actor=actor,
        client_id="tab-a",
        surface=ScoresheetEditLease.Surface.WEB,
        resume_token=token,
    )
    assert resumed and resumed.id == lease.id
    assert resumed_token == token
    assert read_only is False and reason == ""
    assert scoresheet.change_logs.filter(event_type="LEASE_ACQUIRED").count() == 1
    assert not scoresheet.change_logs.filter(event_type="LEASE_RESUMED").exists()

    for _ in range(2):
        release_edit_lease(
            scoresheet_id=scoresheet.id,
            actor=actor,
            lease_token=token,
            client_id="tab-a",
            surface=ScoresheetEditLease.Surface.WEB,
        )
    assert not ScoresheetEditLease.objects.filter(scoresheet=scoresheet).exists()


def test_changes_endpoint_only_returns_chinese_human_events_with_true_values(tmp_path):
    with override_settings(MEDIA_ROOT=tmp_path):
        setup, _, _, _, scoresheet = create_scoresheet()
    before = scoresheet.draft["header"]["game_number"]
    token = obtain_lease(scoresheet, setup["admin"], "human-log-tab")
    save_draft_changes(
        scoresheet_id=scoresheet.id,
        actor=setup["admin"],
        expected_version=scoresheet.draft_version,
        lease_token=token,
        client_id="human-log-tab",
        surface=ScoresheetEditLease.Surface.WEB,
        changes=[{"path": "/header/game_number", "value": "人工日志-42"}],
    )
    release_edit_lease(
        scoresheet_id=scoresheet.id,
        actor=setup["admin"],
        lease_token=token,
        client_id="human-log-tab",
        surface=ScoresheetEditLease.Surface.WEB,
    )

    client = Client()
    client.force_login(setup["admin"])
    response = client.get(f"/api/v1/scoresheets/{scoresheet.id}/changes")

    assert response.status_code == 200
    assert [row["action"] for row in response.json()["items"]] == ["human_edit"]
    entry = response.json()["items"][0]
    assert entry["summary"] == "人工编辑 · 1 项"
    assert entry["changes"] == [
        {
            "path": "/header/game_number",
            "before": before,
            "after": "人工日志-42",
        }
    ]


def test_recognition_capability_and_removed_stop_endpoint(tmp_path):
    with override_settings(MEDIA_ROOT=tmp_path, QWEN_API_KEY=""):
        setup, _, _, _, scoresheet = create_scoresheet()
        client = Client()
        client.force_login(setup["admin"])
        capability = client.get("/api/v1/scoresheets/recognition/capabilities")
        stopped = client.post(f"/api/v1/scoresheets/{scoresheet.id}/recognition/stop")

    assert capability.status_code == 200
    assert capability.json() == {
        "configured": False,
        "provider": "QWEN",
        "model": "qwen3.8-max",
        "prompt_version": "scoresheet-2026-08-20-v24-cn",
        "max_attempts": 4,
        "retry_delays_seconds": [30, 30, 30],
    }
    # No POST handler exists. Ninja's adjacent read-only dynamic route may
    # surface this as 405 instead of 404, but the mutation endpoint is gone.
    assert stopped.status_code in {404, 405}


def test_provider_retry_after_longer_than_default_is_honored(tmp_path, monkeypatch):
    from core.services import scoresheet_recognition as recognition

    with override_settings(MEDIA_ROOT=tmp_path, QWEN_API_KEY="test-key"):
        _, _, _, _, scoresheet = create_scoresheet()

        def fail(_run):
            raise RecognitionAttemptError(
                "QWEN_HTTP_429",
                "rate limited",
                retryable=True,
                retry_after_seconds=75,
            )

        monkeypatch.setattr(recognition, "call_qwen", fail)
        before = timezone.now()
        assert run_once("retry-after-worker") == "retry_wait"
        run = ScoresheetRecognitionRun.objects.get(scoresheet=scoresheet)
        remaining = (run.next_attempt_at - before).total_seconds()
        assert 73 <= remaining <= 77


def test_qwen_request_uses_strict_private_contract_and_audits_image(tmp_path, monkeypatch):
    import base64
    import hashlib
    import sys
    from types import SimpleNamespace

    from core.scoresheet_v2.recognition import PROMPT_VERSION, SYSTEM_PROMPT
    from core.services.scoresheet_recognition import call_qwen

    captured: dict[str, object] = {}

    class FakeCompletions:
        def create(self, **kwargs):
            captured.update(kwargs)
            return []

    class FakeOpenAI:
        def __init__(self, **kwargs):
            captured["client"] = kwargs
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=FakeOpenAI))
    with override_settings(
        MEDIA_ROOT=tmp_path,
        QWEN_API_KEY="private-test-key",
        QWEN_MODEL="qwen3.8-max",
        QWEN_REASONING_EFFORT="xhigh",
        SCORESHEET_RECOGNITION_MAX_PIXELS=10_000_000,
    ):
        _, _, _, _, scoresheet = create_scoresheet()
        run = ScoresheetRecognitionRun.objects.get(scoresheet=scoresheet)
        with pytest.raises(RecognitionAttemptError) as failure:
            call_qwen(run)

    assert failure.value.code == "PROVIDER_SCHEMA_INVALID"
    assert hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest() == (
        "ad8bd0f20d5b0d18496187555d3ad7d7559b734cac08cca1612a56e9e50b8586"
    )
    assert captured["model"] == "qwen3.8-max"
    assert captured["client"]["timeout"] is None
    assert captured["seed"] == 1234
    assert captured["stream"] is True
    assert captured["stream_options"] == {"include_usage": True}
    assert captured["extra_body"] == {
        "enable_thinking": True,
        "reasoning_effort": "xhigh",
        "vl_high_resolution_images": True,
        "preserve_thinking": False,
    }
    response_format = captured["response_format"]
    assert response_format["json_schema"]["strict"] is True
    schema_text = json.dumps(response_format["json_schema"]["schema"], ensure_ascii=False)
    messages = captured["messages"]
    user_content = messages[1]["content"]
    user_text = next(item["text"] for item in user_content if item["type"] == "text")
    image_url = next(
        item["image_url"]["url"] for item in user_content if item["type"] == "image_url"
    )
    assert scoresheet.draft["teams"][0]["players"][0]["name"] in schema_text
    assert scoresheet.draft["teams"][1]["players"][0]["name"] in schema_text
    assert scoresheet.draft["teams"][0]["name"] in user_text
    assert scoresheet.draft["teams"][1]["name"] in user_text
    forbidden_values = {
        str(scoresheet.id),
        str(scoresheet.game_id),
        scoresheet.game_prior_snapshot["competition"],
        scoresheet.game_prior_snapshot["date"],
        scoresheet.game_prior_snapshot["venue"],
    }
    assert all(value not in user_text + schema_text for value in forbidden_values if value)
    processed_image = base64.b64decode(image_url.split(",", 1)[1])
    run.refresh_from_db()
    assert run.model_name == "qwen3.8-max"
    assert run.prompt_version == PROMPT_VERSION
    assert run.image_sha256 == hashlib.sha256(processed_image).hexdigest()


def test_image_preprocessing_caps_output_at_ten_megapixels():
    source = io.BytesIO()
    Image.new("RGB", (4000, 3000), color=(246, 244, 239)).save(source, format="JPEG")

    processed, _, _ = prepare_image(source.getvalue(), 10_000_000)

    with Image.open(io.BytesIO(processed)) as image:
        assert image.width * image.height <= 10_000_000
        assert image.width * image.height >= 9_900_000


def test_admin_pdf_uses_current_correction_draft_not_old_publication(tmp_path, monkeypatch):
    from core import api_scoresheets

    captured: dict[str, object] = {}

    def fake_render(document):
        captured["document"] = copy.deepcopy(document)
        return b"%PDF-1.4\n%%EOF\n"

    with override_settings(MEDIA_ROOT=tmp_path):
        setup, _, _, _, scoresheet = create_scoresheet()
        token = obtain_lease(scoresheet, setup["admin"])
        scoresheet = make_ready(scoresheet, setup["admin"], token)
        publish_scoresheet(
            scoresheet_id=scoresheet.id,
            actor=setup["admin"],
            expected_version=scoresheet.draft_version,
            lease_token=token,
            client_id="web-1",
            surface=ScoresheetEditLease.Surface.WEB,
        )
        scoresheet.refresh_from_db()
        correction_token = obtain_lease(scoresheet, setup["admin"], "correction-tab")
        save_draft_changes(
            scoresheet_id=scoresheet.id,
            actor=setup["admin"],
            expected_version=scoresheet.draft_version,
            lease_token=correction_token,
            client_id="correction-tab",
            surface=ScoresheetEditLease.Surface.WEB,
            changes=[{"path": "/officials/0/name", "value": "纠错后的记录员"}],
        )
        monkeypatch.setattr(api_scoresheets, "render_scoresheet_pdf", fake_render)
        client = Client()
        client.force_login(setup["admin"])
        response = client.get(f"/api/v1/scoresheets/{scoresheet.id}/exports/pdf")

    assert response.status_code == 200
    assert captured["document"]["officials"][0]["name"] == "纠错后的记录员"
