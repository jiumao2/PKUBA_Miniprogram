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
from core.scoresheet_schema import REGIONS
from core.services.game_media import replace_game_media, upload_game_media
from core.services.scoresheet_recognition import (
    RecognitionAttemptError,
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
    game = setup["games"][0]
    players = {
        "A": RosterPlayer.objects.create(
            team=game.home_team, name="甲队一号", jersey_number="4"
        ),
        "B": RosterPlayer.objects.create(
            team=game.away_team, name="乙队一号", jersey_number="5"
        ),
    }
    asset = upload_game_media(
        actor=setup["admin"],
        game=game,
        kind=GameMediaAsset.Kind.SCORESHEET,
        scoresheet_complete_confirmed=True,
        uploaded_file=image_file(),
    )
    return setup, game, players, asset, GameScoresheet.objects.get(game=game)


def valid_document(scoresheet: GameScoresheet) -> dict[str, object]:
    document = copy.deepcopy(scoresheet.draft)
    player_a = document["teams"]["A"]["players"][0]
    player_b = document["teams"]["B"]["players"][0]
    player_a.update({"appeared": True, "starter": True})
    player_b.update({"appeared": True, "starter": True})
    document["running_score"] = [
        {
            "id": "score-a-1",
            "sequence": 1,
            "team": "A",
            "player_id": player_a["player_id"],
            "player_name": player_a["name"],
            "player_number": player_a["jersey_number"],
            "value": 2,
            "period": "1",
            "cumulative": 2,
            "boundary": "none",
        },
        {
            "id": "score-b-1",
            "sequence": 2,
            "team": "B",
            "player_id": player_b["player_id"],
            "player_name": player_b["name"],
            "player_number": player_b["jersey_number"],
            "value": 1,
            "period": "1",
            "cumulative": 1,
            "boundary": "game",
        },
    ]
    document["summary"] = {
        "period_scores": {
            "1": {"A": 2, "B": 1},
            "2": {"A": 0, "B": 0},
            "3": {"A": 0, "B": 0},
            "4": {"A": 0, "B": 0},
            "OT": {"A": 0, "B": 0},
        },
        "final_score": {"A": 2, "B": 1},
        "winner_side": "A",
        "ended_at": "14:20",
    }
    document["officials"].update(
        {
            "scorer": "记录员",
            "timer": "计时员",
            "crew_chief_signature": True,
        }
    )
    return document


def obtain_lease(scoresheet: GameScoresheet, actor: Account, client_id: str = "web-1") -> str:
    _, token, read_only = acquire_edit_lease(
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
    for region in REGIONS:
        review_region(
            scoresheet_id=scoresheet.id,
            actor=actor,
            expected_version=scoresheet.draft_version,
            lease_token=token,
            client_id=client_id,
            surface=ScoresheetEditLease.Surface.WEB,
            region=region,
            reviewed=True,
        )
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
        username="scoresheet-admin-2", password="password", role=Account.Role.ADMIN
    )
    superadmin = Account.objects.create_user(
        username="scoresheet-root", password="password", role=Account.Role.SUPERADMIN
    )

    old_token = obtain_lease(scoresheet, setup["admin"], "web-owner")
    holder, other_token, read_only = acquire_edit_lease(
        scoresheet_id=scoresheet.id,
        actor=second_admin,
        client_id="mini-reader",
        surface=ScoresheetEditLease.Surface.MINIAPP,
    )
    assert read_only is True
    assert other_token is None
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
            changes=[{"path": "/game/venue", "value": "失效客户端"}],
        )
    assert lost.value.code == "LEASE_LOST"

    lease = ScoresheetEditLease.objects.get(scoresheet=scoresheet)
    lease.expires_at = timezone.now() - timedelta(seconds=1)
    lease.save(update_fields=["expires_at"])
    new_holder, new_token, read_only = acquire_edit_lease(
        scoresheet_id=scoresheet.id,
        actor=second_admin,
        client_id="mini-reader",
        surface=ScoresheetEditLease.Surface.MINIAPP,
    )
    assert read_only is False
    assert new_token
    assert new_holder.account_id == second_admin.id
    assert AdminAuditLog.objects.filter(action="SCORESHEET_LEASE_FORCE_TAKEN").exists()


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
        changes=[{"path": "/game/venue", "value": "五四西场"}],
    )
    scoresheet.refresh_from_db()
    assert scoresheet.draft_version == old_version + 1
    assert "SOURCE_GAME" not in scoresheet.reviewed_regions
    events = sync_scoresheet(scoresheet, after_event)
    assert [event.event_type for event in events] == ["FIELD_EDIT"]
    assert events[0].changed_fields[0]["path"] == "/game/venue"
    with pytest.raises(ScoresheetError) as stale:
        save_draft_changes(
            scoresheet_id=scoresheet.id,
            actor=setup["admin"],
            expected_version=old_version,
            lease_token=token,
            client_id="web-1",
            surface=ScoresheetEditLease.Surface.WEB,
            changes=[{"path": "/game/venue", "value": "旧值覆盖"}],
        )
    assert stale.value.code == "VERSION_CONFLICT"


def test_unrelated_region_reviews_survive_a_field_change(tmp_path):
    with override_settings(MEDIA_ROOT=tmp_path):
        setup, _, _, _, scoresheet = create_scoresheet()
    token = obtain_lease(scoresheet, setup["admin"])
    scoresheet = make_ready(scoresheet, setup["admin"], token)

    updated = copy.deepcopy(scoresheet.draft)
    updated["game"]["venue"] = "只重核比赛信息"
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
    assert "SOURCE_GAME" not in scoresheet.reviewed_regions
    assert all(
        scoresheet.reviewed_regions[region]["draft_version"]
        == scoresheet.draft_version
        for region in REGIONS
        if region != "SOURCE_GAME"
    )
    latest_change = scoresheet.change_logs.order_by("-event_sequence").first()
    assert latest_change is not None
    assert [row["path"] for row in latest_change.changed_fields] == ["/game/venue"]

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
                "changes": [{"path": "/game/venue", "value": "跨端同步场地"}],
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
    assert synced.json()["events"][-1]["changed_fields"][0]["value"] == "跨端同步场地"
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
                "changes": [{"path": "/game/venue", "value": "小程序无 CSRF 保存"}],
            }
        ),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {mini_token}",
    )

    assert saved.status_code == 200
    assert saved.json()["draft"]["game"]["venue"] == "小程序无 CSRF 保存"


def test_retryable_recognition_uses_initial_call_plus_three_retries(tmp_path, monkeypatch):
    from core.services import scoresheet_recognition as recognition

    with override_settings(MEDIA_ROOT=tmp_path):
        _, _, _, _, scoresheet = create_scoresheet()

        def fail(_run):
            raise RecognitionAttemptError(
                "QWEN_NETWORK_ERROR", "temporary", retryable=True
            )

        monkeypatch.setattr(recognition, "call_qwen", fail)
        expected_delays = [30, 120, 600]
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
    assert "甲队一号" in prompt and "乙队一号" in prompt


def test_late_success_is_retained_but_never_overwrites_human_edits(tmp_path, monkeypatch):
    from core.services import scoresheet_recognition as recognition

    with override_settings(MEDIA_ROOT=tmp_path):
        setup, _, _, _, scoresheet = create_scoresheet()
        claim = claim_next_run("race-worker")
        assert claim is not None
        token = obtain_lease(scoresheet, setup["admin"])
        save_draft_changes(
            scoresheet_id=scoresheet.id,
            actor=setup["admin"],
            expected_version=scoresheet.draft_version,
            lease_token=token,
            client_id="web-1",
            surface=ScoresheetEditLease.Surface.WEB,
            changes=[{"path": "/game/venue", "value": "人工确认场地"}],
        )
        monkeypatch.setattr(
            recognition,
            "call_qwen",
            lambda _run: ({"game": {"venue": "模型错误场地"}}, {"total_tokens": 12}),
        )
        assert execute_claim(claim) == "stored_not_applied"
    scoresheet.refresh_from_db()
    run = ScoresheetRecognitionRun.objects.get(scoresheet=scoresheet)
    assert scoresheet.draft["game"]["venue"] == "人工确认场地"
    assert run.status == ScoresheetRecognitionRun.Status.SUCCEEDED
    assert run.provider_result["game"]["venue"] == "模型错误场地"
    assert scoresheet.change_logs.filter(
        event_type="RECOGNITION_STORED_NOT_APPLIED"
    ).exists()


def test_reupload_supersedes_old_claim_and_resets_attempt_budget(tmp_path, monkeypatch):
    from core.services import scoresheet_recognition as recognition

    with override_settings(MEDIA_ROOT=tmp_path):
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
    assert scoresheet.draft["game"]["venue"] != "迟到结果"


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
    assert GamePlayerStat.objects.filter(publication=publication).count() == 2
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
        admin_client.force_login(setup["admin"])
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
            status=ScoresheetRecognitionRun.Status.QUEUED,
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

        admin_client.force_login(setup["admin"])
        blocked_lease = admin_client.post(
            f"/api/v1/scoresheets/{scoresheet.id}/lease",
            data=json.dumps({"client_id": "normal-admin", "surface": "WEB"}),
            content_type="application/json",
        )
        assert blocked_lease.status_code == 403
        assert blocked_lease.json()["code"] == "SUPERADMIN_REQUIRED"


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
    assert "甲队一号" in csv_text and "乙队一号" in csv_text

    assert xlsx_response.status_code == 200
    assert xlsx_response["Content-Type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    workbook = load_workbook(io.BytesIO(xlsx_response.content), read_only=True)
    assert workbook.sheetnames == ["球队单场统计", "球员单场统计"]
    assert workbook["球队单场统计"].max_row == 3
    assert workbook["球员单场统计"].max_row == 3


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
            changes=[{"path": "/officials/scorer", "value": "纠错记录员"}],
        )
        scoresheet.refresh_from_db()
        assert "OFFICIALS" not in scoresheet.reviewed_regions
        assert all(
            region in scoresheet.reviewed_regions
            for region in REGIONS
            if region != "OFFICIALS"
        )
        review_region(
            scoresheet_id=scoresheet.id,
            actor=superadmin,
            expected_version=scoresheet.draft_version,
            lease_token=root_token,
            client_id="root-web",
            surface=ScoresheetEditLease.Surface.WEB,
            region="OFFICIALS",
            reviewed=True,
        )
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
