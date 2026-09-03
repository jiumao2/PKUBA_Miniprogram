from __future__ import annotations

import copy
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from django.db import close_old_connections
from django.test import Client
from django.utils import timezone

from core.models import (
    ApiIdempotencyRecord,
    EmailOutbox,
    GameMediaAsset,
    GameScoresheet,
    InboxItem,
    ScoresheetEditLease,
    ScoresheetPublication,
    ScoresheetRecognitionRun,
    Season,
)
from core.scoresheet_schema_v2 import document_digest, new_document
from core.services import scoresheet_recognition as worker
from core.services.scoresheets import (
    ScoresheetError,
    acquire_edit_lease,
    publish_scoresheet,
    retry_recognition,
    save_draft_changes,
)
from core.services.wechat import issue_session
from core.tests.test_scoresheet_game_context import snapshot
from core.tests.test_scoresheets import create_scoresheet, make_ready, obtain_lease

pytestmark = pytest.mark.django_db


@pytest.fixture
def fixture(settings, tmp_path, monkeypatch):
    settings.QWEN_API_KEY = ""
    settings.MEDIA_ROOT = tmp_path
    result = create_scoresheet()
    # Never call a model, even if the developer's host has real credentials.
    monkeypatch.setattr(worker, "call_qwen", lambda _run: pytest.fail("LIVE_MODEL_FORBIDDEN"))
    settings.QWEN_API_KEY = "isolated-no-network-placeholder"
    return result


def full_snapshot():
    return {
        **snapshot(),
        **{model.__name__: list(model.objects.order_by("id").values())
           for model in (ApiIdempotencyRecord, InboxItem, EmailOutbox)},
    }


def client_context(fixture, role="admin", surface="WEB"):
    setup, _, _, _, sheet = fixture
    actor = setup[role]
    client = Client(enforce_csrf_checks=surface == "MINIAPP")
    if surface == "MINIAPP":
        client.defaults["HTTP_AUTHORIZATION"] = f"Bearer {issue_session(actor)}"
    else:
        client.force_login(actor)
    _, token, read_only, _ = acquire_edit_lease(
        scoresheet_id=sheet.id, actor=actor, client_id="retry-fixture", surface=surface,
    )
    assert not read_only and token
    sheet.refresh_from_db()
    return client, actor, {
        "expected_version": sheet.draft_version, "lease_token": token,
        "client_id": "retry-fixture", "surface": surface,
    }


def save_manual(fixture, actor, context):
    sheet = fixture[4]
    draft = copy.deepcopy(sheet.draft)
    draft["header"]["crew_chief"] = "人工主裁"
    draft["teams"][0]["head_coach"] = "人工教练"
    draft["officials"][0]["name"] = "人工记录员"
    draft["final_score"]["ended_at"] = "21:35"
    draft["recognition"] = {
        "run_id": "manual-table-personnel", "notes": "", "issues": [],
        "problem_paths": [], "applied_at": timezone.now().isoformat(),
        "table_personnel": ["人工记录台人员"],
    }
    save_draft_changes(
        scoresheet_id=sheet.id, actor=actor, **context,
        changes=[{"path": "/", "value": draft}],
    )
    sheet.refresh_from_db()
    context["expected_version"] = sheet.draft_version
    return copy.deepcopy(sheet.draft)


def provider_document(sheet, run_id):
    result = new_document(
        sheet.game_prior_snapshot, sheet.roster_snapshot, document_id=str(sheet.id),
        source=sheet.draft["source"],
    )
    result["header"]["crew_chief"] = "新识别主裁"
    result["recognition"] = {
        "run_id": str(run_id), "notes": "新识别依据", "issues": [], "problem_paths": [],
        "applied_at": timezone.now().isoformat(), "table_personnel": ["新识别记录台人员"],
    }
    return result


def post_retry(client, sheet, context, **extra):
    return client.post(
        f"/api/v1/scoresheets/{sheet.id}/recognition/retry",
        data=json.dumps({**context, "confirmed_overwrite": True, **extra}),
        content_type="application/json",
    )


@pytest.mark.parametrize("role", ["ordinary_admin", "admin"])
@pytest.mark.parametrize("surface", ["WEB", "MINIAPP"])
def test_manual_failed_retry_replaces_entire_draft_and_preserves_history(
    fixture, monkeypatch, role, surface
):
    sheet = fixture[4]
    client, actor, context = client_context(fixture, role, surface)
    before_draft = save_manual(fixture, actor, context)
    sheet.game_prior_snapshot["confirmed_player_bindings"] = [{
        "side": "A", "row": 1, "name": sheet.draft["teams"][0]["players"][0]["name"],
        "jersey_number": "4", "player_id": str(fixture[2]["A"].id),
    }]
    sheet.validation_report = {"previous": "must not survive retry"}
    sheet.validation_draft_version = sheet.draft_version
    sheet.reviewed_regions = {"TEAM_A": {"reviewed": True}}
    sheet.acknowledged_warnings = ["old-warning"]
    sheet.save()
    before_prior = copy.deepcopy(sheet.game_prior_snapshot)
    before_runs = list(sheet.recognition_runs.values())
    before_source = GameMediaAsset.objects.values().get(id=fixture[3].id)
    assert client.get(f"/api/v1/scoresheets/{sheet.id}").json()["recognition"]["can_retry"]

    response = post_retry(client, sheet, context)
    assert response.status_code == 200, response.content
    run_id = response.json()["id"]
    monkeypatch.setattr(worker, "call_qwen", lambda run: (
        provider_document(sheet, run.id), {"total_tokens": 0},
    ))
    assert worker.run_once("retry-fixture") == "succeeded"
    sheet.refresh_from_db()
    assert sheet.draft["header"]["crew_chief"] == "新识别主裁"
    assert sheet.draft["teams"][0]["head_coach"] == ""
    assert sheet.draft["officials"][0]["name"] == ""
    assert sheet.draft["final_score"]["ended_at"] == ""
    assert sheet.draft["recognition"]["table_personnel"] == ["新识别记录台人员"]
    assert sheet.draft["recognition"]["run_id"] == run_id
    assert sheet.draft_version == context["expected_version"] + 1
    assert not sheet.validation_report and sheet.validation_draft_version is None
    assert not sheet.reviewed_regions and not sheet.acknowledged_warnings
    assert not sheet.game_prior_snapshot.get("confirmed_player_bindings")
    assert sheet.game_prior_snapshot["unresolved_player_bindings"] == [{
        "side": "A", "row": 1, "name": sheet.draft["teams"][0]["players"][0]["name"],
    }]
    previous_ids = [run["id"] for run in before_runs]
    assert list(sheet.recognition_runs.filter(id__in=previous_ids).values()) == before_runs
    before_revision = sheet.revisions.get(
        event_sequence=sheet.change_logs.get(event_type="RECOGNITION_MANUAL_RETRY_QUEUED").event_sequence
    )
    assert before_revision.snapshot["draft"] == before_draft
    assert before_revision.snapshot["game_prior_snapshot"] == before_prior
    assert GameMediaAsset.objects.values().get(id=fixture[3].id) == before_source
    assert not sheet.publications.exists()
    assert not client.get(f"/api/v1/scoresheets/{sheet.id}").json()["recognition"]["can_retry"]


@pytest.mark.parametrize("role", ["ordinary_admin", "admin"])
def test_retry_failure_preserves_manual_draft_and_previous_evidence(fixture, monkeypatch, role):
    sheet = fixture[4]
    client, actor, context = client_context(fixture, role)
    original = save_manual(fixture, actor, context)
    before_prior = copy.deepcopy(sheet.game_prior_snapshot)
    response = post_retry(client, sheet, context)
    assert response.status_code == 200, response.content

    def fail(_run):
        raise worker.RecognitionAttemptError("MODEL_UNAVAILABLE", "模拟失败", retryable=False)

    monkeypatch.setattr(worker, "call_qwen", fail)
    assert worker.run_once("retry-fixture") == "failed"
    sheet.refresh_from_db()
    assert sheet.draft == original
    assert sheet.draft_version == context["expected_version"]
    assert sheet.game_prior_snapshot == before_prior
    assert sheet.status == GameScoresheet.Status.RECOGNITION_FAILED
    assert client.get(f"/api/v1/scoresheets/{sheet.id}").json()["recognition"]["can_retry"]


@pytest.mark.parametrize("role", ["ordinary_admin", "admin"])
@pytest.mark.parametrize("surface", ["WEB", "MINIAPP"])
def test_published_manual_sheet_never_allows_retry_even_after_superadmin_reacquires(
    fixture, role, surface
):
    setup, _, _, _, sheet = fixture
    publisher = setup[role]
    publish_token = obtain_lease(sheet, publisher)
    sheet = make_ready(sheet, publisher, publish_token)
    publication = publish_scoresheet(
        scoresheet_id=sheet.id, actor=publisher, expected_version=sheet.draft_version,
        lease_token=publish_token, client_id="web-1", surface="WEB",
    )
    client = Client(enforce_csrf_checks=surface == "MINIAPP")
    if surface == "MINIAPP":
        client.defaults["HTTP_AUTHORIZATION"] = f"Bearer {issue_session(publisher)}"
    else:
        client.force_login(publisher)
    _, token, _, _ = acquire_edit_lease(
        scoresheet_id=sheet.id, actor=publisher, client_id="retry-fixture", surface=surface,
    )
    sheet.refresh_from_db()
    before = full_snapshot()
    response = post_retry(client, sheet, {
        "expected_version": sheet.draft_version, "lease_token": token or "expired",
        "client_id": "retry-fixture", "surface": surface,
    })
    assert response.status_code == (403 if role == "ordinary_admin" else 409)
    assert response.json()["code"] == (
        "SUPERADMIN_REQUIRED" if role == "ordinary_admin" else "RECOGNITION_RETRY_PUBLISHED"
    )
    assert full_snapshot() == before
    assert ScoresheetPublication.objects.get(id=publication.id).snapshot == publication.snapshot
    assert not client.get(f"/api/v1/scoresheets/{sheet.id}").json()["recognition"]["can_retry"]


@pytest.mark.parametrize(
    "alteration", ["missing_confirmation", "stale_version", "succeeded", "wrong_source"]
)
def test_invalid_retry_is_zero_write(fixture, alteration):
    sheet = fixture[4]
    client, _, context = client_context(fixture)
    payload = {}
    if alteration == "missing_confirmation":
        payload["confirmed_overwrite"] = False
    elif alteration == "stale_version":
        context["expected_version"] -= 1
    elif alteration == "succeeded":
        sheet.recognition_runs.update(status=ScoresheetRecognitionRun.Status.SUCCEEDED)
    else:
        sheet.source_version += 1
        sheet.save(update_fields=["source_version"])
    before = full_snapshot()
    response = post_retry(client, sheet, context, **payload)
    assert response.status_code in {400, 404, 409}, response.content
    assert full_snapshot() == before


@pytest.mark.parametrize(
    "alteration", ["source", "new_run", "archived", "publication", "worker_token"]
)
def test_late_completion_rechecks_current_run_source_season_and_publication(fixture, alteration):
    setup, _, _, _, sheet = fixture
    client, actor, context = client_context(fixture)
    save_manual(fixture, actor, context)
    assert post_retry(client, sheet, context).status_code == 200
    claim = worker.claim_next_run("retry-fixture")
    assert claim is not None
    run = ScoresheetRecognitionRun.objects.get(id=claim.run_id)
    if alteration == "source":
        sheet.source_version += 1
        sheet.save(update_fields=["source_version"])
    elif alteration == "new_run":
        ScoresheetRecognitionRun.objects.create(
            scoresheet=sheet, source_asset=sheet.source_asset, source_version=sheet.source_version,
            base_draft_version=sheet.draft_version, cycle=run.cycle + 1,
        )
    elif alteration == "archived":
        Season.objects.filter(id=sheet.game.season_id).update(status=Season.Status.ARCHIVED)
    elif alteration == "publication":
        ScoresheetPublication.objects.create(
            scoresheet=sheet, source_asset=sheet.source_asset, publication_number=1,
            draft_version=sheet.draft_version, snapshot=copy.deepcopy(sheet.draft),
            published_by=setup["admin"], validation_report={},
        )
    else:
        from uuid import uuid4

        ScoresheetRecognitionRun.objects.filter(id=run.id).update(worker_lease_token=uuid4())
    before = full_snapshot()
    assert worker._complete_success(claim, provider_document(sheet, run.id), {}) == "superseded"
    after = full_snapshot()
    if alteration != "worker_token":
        before.pop("ScoresheetRecognitionRun")
        after.pop("ScoresheetRecognitionRun")
    assert after == before


def test_retry_uses_neutral_input_including_manual_table_personnel(fixture):
    sheet = fixture[4]
    client, actor, context = client_context(fixture)
    save_manual(fixture, actor, context)
    response = post_retry(client, sheet, context)
    run = ScoresheetRecognitionRun.objects.get(id=response.json()["id"])
    neutral = worker._recognition_document(sheet, run)
    assert neutral["recognition"] is None
    assert neutral["header"]["crew_chief"] == ""
    assert neutral["officials"][0]["name"] == ""
    assert neutral["source"] == sheet.draft["source"]
    assert document_digest(sheet.draft) != document_digest(neutral)


@pytest.mark.django_db(transaction=True)
def test_duplicate_retry_race_has_one_run_and_one_before_image(fixture):
    sheet = fixture[4]
    _, actor, context = client_context(fixture)
    save_manual(fixture, actor, context)
    barrier = Barrier(2)
    before_runs = sheet.recognition_runs.count()
    before_revisions = sheet.revisions.count()

    def submit():
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            try:
                result = retry_recognition(
                    scoresheet_id=sheet.id, actor=actor, **context, confirmed_overwrite=True,
                )
                return 200, str(result.id)
            except ScoresheetError as error:
                return error.status, error.code
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: submit(), range(2)))
    assert sorted(code for code, _ in results) == [200, 409]
    assert sheet.recognition_runs.count() == before_runs + 1
    assert sheet.revisions.count() == before_revisions + 1
    assert sheet.change_logs.filter(event_type="RECOGNITION_MANUAL_RETRY_QUEUED").count() == 1
    assert not ScoresheetEditLease.objects.filter(scoresheet=sheet).exists()
