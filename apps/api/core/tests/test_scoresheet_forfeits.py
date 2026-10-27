"""Forfeit evidence and status transitions; no live model or production data."""

import copy
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
from django.db import close_old_connections, transaction
from django.test import Client
from django.utils import timezone

from core.models import (
    Game,
    GameMediaAsset,
    GameScoresheet,
    ScoresheetEditLease,
    ScoresheetRecognitionRun,
    Season,
)
from core.services import scoresheet_recognition as worker
from core.services.competition_corrections import apply_correction
from core.services.game_media import replace_game_media, upload_game_media
from core.services.scoresheet_workflow import reconcile_scoresheet_game_status
from core.services.scoresheets import (
    ScoresheetError,
    acquire_edit_lease,
    publish_scoresheet,
    recognition_diff,
    save_draft_changes,
)
from core.tests.factories import reschedule_setup
from core.tests.test_competition_corrections import _change, _freeze
from core.tests.test_scoresheet_recognition_retry import provider_document
from core.tests.test_scoresheets import create_scoresheet, image_file, make_ready, obtain_lease

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def isolated(settings, tmp_path, monkeypatch):
    settings.MEDIA_ROOT = tmp_path
    settings.QWEN_API_KEY = ""
    monkeypatch.setattr(worker, "call_qwen", lambda *_: pytest.fail("LIVE_MODEL_FORBIDDEN"))


def transition(setup, game, status):
    game.refresh_from_db()
    _, correction = _freeze(
        setup["superadmin"],
        game.season,
        [
            _change(
                game,
                status=status,
                home_score=20 if status == "FORFEIT" else None,
                away_score=0 if status == "FORFEIT" else None,
            )
        ],
    )
    apply_correction(
        actor=setup["superadmin"],
        correction_id=correction.id,
        expected_version=correction.version,
        impact_hash=correction.impact_hash,
        confirmed=True,
    )
    game.refresh_from_db()


def queue_run(sheet):
    run = sheet.recognition_runs.latest("created_at")
    run.status = "QUEUED"
    run.next_attempt_at = timezone.now()
    run.save()
    return run


def test_forfeit_without_roster_or_source_is_complete_and_upload_never_queues(settings):
    setup = reschedule_setup()
    game = setup["games"][0]
    transition(setup, game, "FORFEIT")
    client = Client()
    client.force_login(setup["superadmin"])
    url = f"/api/v1/scoresheets/?game_id={game.id}"
    for query in (
        "scope=ACTION_REQUIRED",
        "scope=IN_PROGRESS",
        "processing=UPLOAD",
        "processing=SCORESHEET_REVIEW",
    ):
        assert client.get(f"{url}&{query}").json()["items"] == []
    for query in ("scope=PUBLISHED", "processing=COMPLETE"):
        row = client.get(f"{url}&{query}").json()["items"][0]
        assert row["workflow_mode"] == "FORFEIT_ATTACHMENT"
        assert row["game_status"] == "FORFEIT"
        assert row["scoresheet_id"] is None
    settings.QWEN_API_KEY = "isolated-never-call"
    upload_game_media(
        actor=setup["superadmin"],
        game=game,
        kind="SCORESHEET",
        scoresheet_complete_confirmed=True,
        uploaded_file=image_file(),
    )
    sheet = GameScoresheet.objects.get(game=game)
    assert sheet.status == "DRAFT" and sheet.current_publication_id is None
    assert not ScoresheetRecognitionRun.objects.exists()
    assert worker.claim_next_run("forfeit-test") is None
    assert not sheet.publications.exists()
    detail = client.get(f"/api/v1/scoresheets/{sheet.id}").json()
    assert detail["workflow_mode"] == "FORFEIT_ATTACHMENT"
    game.refresh_from_db()
    assert (game.status, game.home_score, game.away_score) == ("FORFEIT", 20, 0)


@pytest.mark.parametrize("state", ["QUEUED", "RETRY_WAIT", "RUNNING"])
def test_transition_stops_unstarted_work_and_retains_running_result_after_return(state):
    setup, game, _, _, sheet = create_scoresheet()
    run = queue_run(sheet)
    claim = worker.claim_next_run("forfeit-test") if state == "RUNNING" else None
    if state == "RETRY_WAIT":
        run.status = state
        run.next_attempt_at = timezone.now()
        run.save()
    transition(setup, game, "FORFEIT")
    run.refresh_from_db()
    assert run.result_only and not run.auto_apply_allowed
    assert run.status == ("RUNNING" if claim else "STOPPED")
    transition(setup, game, "SCHEDULED")
    sheet.refresh_from_db()
    draft, version = copy.deepcopy(sheet.draft), sheet.draft_version
    if claim:
        result = provider_document(sheet, run.id)
        assert worker._complete_success(claim, result, {"total_tokens": 17}) == "stored_only"
        run.refresh_from_db()
        assert run.provider_result == result and run.provider_usage == {"total_tokens": 17}
        assert run.applied_draft_version is None
        sheet.refresh_from_db()
        assert recognition_diff(sheet, run)
    assert worker.claim_next_run("forfeit-test") is None
    sheet.refresh_from_db()
    assert sheet.draft == draft and sheet.draft_version == version
    assert not sheet.current_publication_id
    assert sheet.status == "DRAFT"


@pytest.mark.parametrize("completion", ["failure", "expired", "not_dispatched"])
def test_result_only_never_retries_or_dispatches(completion):
    setup, game, _, _, sheet = create_scoresheet()
    run = queue_run(sheet)
    claim = worker.claim_next_run("forfeit-test")
    assert claim
    transition(setup, game, "FORFEIT")
    if completion == "failure":
        assert (
            worker._complete_failure(
                claim, worker.RecognitionAttemptError("TIMEOUT", "mock timeout", retryable=True)
            )
            == "stored_only"
        )
    elif completion == "expired":
        run.refresh_from_db()
        run.attempt_count = 4
        run.worker_lease_expires_at = timezone.now() - timedelta(seconds=1)
        run.save()
        assert worker.claim_next_run("replacement-worker") is None
    else:
        assert worker.execute_claim(claim) == "stored_only"
    run.refresh_from_db()
    assert run.status == "FAILED" and run.next_attempt_at is None
    assert worker.claim_next_run("forfeit-test") is None
    assert run.result_only and run.applied_draft_version is None


def test_forfeit_revokes_lease_and_rejects_stale_editor_without_losing_draft():
    setup, game, _, _, sheet = create_scoresheet()
    token = obtain_lease(sheet, setup["admin"])
    draft = copy.deepcopy(sheet.draft)
    transition(setup, game, "FORFEIT")
    sheet.refresh_from_db()
    with pytest.raises(ScoresheetError) as error:
        save_draft_changes(
            scoresheet_id=sheet.id,
            actor=setup["admin"],
            expected_version=sheet.draft_version,
            lease_token=token,
            client_id="web-1",
            surface="WEB",
            changes=[{"path": "/", "value": draft}],
        )
    assert error.value.code == "SCORESHEET_NOT_REQUIRED"
    _, token, readonly, reason = acquire_edit_lease(
        scoresheet_id=sheet.id, actor=setup["admin"], client_id="new", surface="WEB"
    )
    assert readonly and token is None and "弃权" in reason
    sheet.refresh_from_db()
    assert (
        sheet.draft == draft and not ScoresheetEditLease.objects.filter(scoresheet=sheet).exists()
    )


def test_published_forfeit_withdrawal_preserves_immutable_publication_and_can_return():
    setup, game, _, asset, sheet = create_scoresheet()
    token = obtain_lease(sheet, setup["admin"])
    sheet = make_ready(sheet, setup["admin"], token)
    publication = publish_scoresheet(
        scoresheet_id=sheet.id,
        actor=setup["admin"],
        expected_version=sheet.draft_version,
        lease_token=token,
        client_id="web-1",
        surface="WEB",
    )
    original = type(publication).objects.values().get(id=publication.id)
    transition(setup, game, "FORFEIT")
    sheet.refresh_from_db()
    assert sheet.current_publication_id is None
    assert game.current_result_revision.publication_id is None
    public = Client().get(f"/api/v1/public/scoresheet-stats?game_id={game.id}")
    assert public.status_code == 200 and public.json() == []
    publication.refresh_from_db()
    after = type(publication).objects.values().get(id=publication.id)
    assert after == original
    assert GameMediaAsset.objects.filter(id=asset.id).exists()
    transition(setup, game, "SCHEDULED")
    sheet.refresh_from_db()
    assert sheet.current_publication_id is None and sheet.status == "DRAFT"
    assert (game.home_score, game.away_score) == (None, None)


def test_status_and_workflow_rollback_together():
    setup, game, _, _, sheet = create_scoresheet()
    run = queue_run(sheet)
    with pytest.raises(RuntimeError), transaction.atomic():
        locked = Game.objects.select_for_update().get(id=game.id)
        previous = locked.status
        locked.status, locked.home_score, locked.away_score = "FORFEIT", 20, 0
        locked.save()
        reconcile_scoresheet_game_status(
            game=locked, actor=setup["superadmin"], previous_status=previous
        )
        raise RuntimeError("rollback")
    run.refresh_from_db()
    game.refresh_from_db()
    assert game.status == "SCHEDULED" and run.status == "QUEUED" and not run.result_only


def test_schedule_endpoint_can_set_forfeit_while_recognition_runs():
    setup, game, _, _, sheet = create_scoresheet()
    run = queue_run(sheet)
    claim = worker.claim_next_run("schedule-test")
    client = Client()
    client.force_login(setup["superadmin"])
    response = client.put(
        f"/api/v1/admin/schedule/games/{game.id}",
        data=json.dumps({**_change(game, status="FORFEIT", home_score=0, away_score=20),
                         "confirmed": True, "override_rules": True}),
        content_type="application/json",
    )
    assert response.status_code == 200, response.content
    run.refresh_from_db()
    assert run.status == "RUNNING" and run.result_only
    assert worker._complete_success(claim, provider_document(sheet, run.id), {}) == "stored_only"


def test_retained_result_cannot_apply_after_roster_identity_changes():
    setup, game, players, _, sheet = create_scoresheet()
    run = queue_run(sheet)
    claim = worker.claim_next_run("retained-test")
    transition(setup, game, "FORFEIT")
    transition(setup, game, "SCHEDULED")
    worker._complete_success(claim, provider_document(sheet, run.id), {})
    player = players["A"]
    player.eligible = False
    player.save()
    run.refresh_from_db()
    sheet.refresh_from_db()
    with pytest.raises(ScoresheetError) as error:
        recognition_diff(sheet, run)
    assert error.value.code == "RECOGNITION_CONTEXT_CHANGED"


def test_replaced_forfeit_source_retains_old_response_without_touching_new_draft():
    setup, game, _, asset, sheet = create_scoresheet()
    run = queue_run(sheet)
    claim = worker.claim_next_run("replacement-test")
    result = provider_document(sheet, run.id)
    transition(setup, game, "FORFEIT")
    replace_game_media(actor=setup["superadmin"], asset_id=asset.id,
                       expected_version=asset.version, scoresheet_complete_confirmed=True,
                       uploaded_file=image_file("replacement.jpg"))
    sheet.refresh_from_db()
    draft, version, source = copy.deepcopy(sheet.draft), sheet.draft_version, sheet.source_asset_id
    assert worker._complete_success(claim, result, {}) == "stored_only"
    run.refresh_from_db()
    sheet.refresh_from_db()
    assert (sheet.draft, sheet.draft_version, sheet.source_asset_id) == (draft, version, source)
    assert run.provider_result == result and run.applied_draft_version is None
    assert sheet.recognition_runs.count() == 1


@pytest.mark.django_db(transaction=True)
def test_forfeit_transition_serializes_against_recognition_completion():
    setup, game, _, _, sheet = create_scoresheet()
    run = queue_run(sheet)
    claim = worker.claim_next_run("race-test")
    result, original = provider_document(sheet, run.id), copy.deepcopy(sheet.draft)
    barrier = Barrier(2)

    def change_status():
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            with transaction.atomic():
                Season.objects.select_for_update().get(id=game.season_id)
                locked = Game.objects.select_for_update().get(id=game.id)
                previous = locked.status
                locked.status, locked.home_score, locked.away_score = "FORFEIT", 20, 0
                locked.save()
                reconcile_scoresheet_game_status(
                    game=locked, actor=setup["superadmin"], previous_status=previous,
                )
        finally:
            close_old_connections()

    def complete():
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            return worker._complete_success(claim, result, {})
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        changed, completed = pool.submit(change_status), pool.submit(complete)
        changed.result(timeout=20)
        completed.result(timeout=20)
    game.refresh_from_db()
    sheet.refresh_from_db()
    run.refresh_from_db()
    assert (game.status, game.home_score, game.away_score) == ("FORFEIT", 20, 0)
    assert run.status == "SUCCEEDED" and sheet.current_publication_id is None
    if run.result_only:
        assert sheet.draft == original and run.applied_draft_version is None
