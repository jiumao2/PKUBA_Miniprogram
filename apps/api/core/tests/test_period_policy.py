from datetime import time, timedelta
from unittest.mock import patch

import pytest
from django.test import Client

from core.api_admin import PreviewSeasonConfigurationIn
from core.api_reschedule import _actions, _request_out
from core.models import Period, RescheduleRequest, Season, SlotReservation, TeamConfirmation
from core.services.rescheduling import (
    RescheduleError,
    admin_cancel_request,
    admin_decide_review_route,
    admin_final_decision,
    available_reschedule_targets,
    respond_as_selected_team,
    respond_to_opponent,
    submit_reschedule,
    withdraw_request,
)
from core.services.season_management import (
    SeasonManagementError,
    create_season,
    preview_season_configuration,
    season_configuration,
    update_season_configuration,
)
from core.services.wechat import issue_session
from core.tests.factories import reschedule_setup
from core.tests.test_rescheduling import assign_group_teams, valid_submission_time
from core.tests.test_role_workspaces import post_json, put_json
from core.tests.test_season_management import _update_payload

pytestmark = pytest.mark.django_db


def configuration_payload(season):
    return PreviewSeasonConfigurationIn(
        **_update_payload(season_configuration(season))
    ).model_dump()


def save(setup, payload):
    season = setup["season"]
    preview = preview_season_configuration(
        season=season, expected_version=season.version, payload=payload
    )
    updated = update_season_configuration(
        actor=setup["superadmin"],
        season_id=season.id,
        expected_version=season.version,
        payload=payload,
        maintenance_confirmed=True,
        impact_hash=preview["impact_hash"],
    )
    setup["season"] = updated
    return updated


@pytest.mark.parametrize("usage", [Period.Usage.ADMIN_ONLY, Period.Usage.DISABLED])
@pytest.mark.parametrize("route", RescheduleRequest.ProcessRoute.values)
def test_restricted_target_excluded_and_direct_submit_rejected(usage, route):
    setup = reschedule_setup()
    game = setup["games"][0]
    period = setup["period"]
    period.usage = usage
    period.save()
    now = valid_submission_time(game.date, setup["target_date"])
    targets = available_reschedule_targets(
        actor=setup["accounts"][0], game_id=game.id, process_route=route, now=now
    )
    assert all(str(row["period_id"]) != str(period.id) for row in targets)
    with pytest.raises(RescheduleError, match="不开放调赛"):
        submit_reschedule(
            actor=setup["accounts"][0],
            game_id=game.id,
            expected_game_version=game.version,
            target_date=setup["target_date"],
            target_period_id=period.id,
            process_route=route,
            now=now,
        )
    assert not RescheduleRequest.objects.exists()
    assert not SlotReservation.objects.exists()


def submit(setup):
    game = setup["games"][0]
    now = valid_submission_time(game.date, setup["target_date"])
    request = submit_reschedule(
        actor=setup["accounts"][0],
        game_id=game.id,
        expected_game_version=game.version,
        target_date=setup["target_date"],
        target_period_id=setup["period"].id,
        now=now,
    )
    return request, now


def test_legacy_request_blocks_accept_but_preserves_withdraw_and_history():
    setup = reschedule_setup()
    request, now = submit(setup)
    Period.objects.filter(id=request.target_period_id).update(usage=Period.Usage.ADMIN_ONLY)
    request.refresh_from_db()
    assert _request_out(request, setup["accounts"][1])["progress_block_reason"]
    assert "WITHDRAW" in _actions(request, setup["accounts"][0])
    with pytest.raises(RescheduleError) as raised:
        respond_to_opponent(
            actor=setup["accounts"][1],
            request_id=request.id,
            expected_version=request.version,
            accept=True,
            now=now,
        )
    assert raised.value.code == "TARGET_PERIOD_NOT_OPEN"
    request.refresh_from_db()
    assert request.status == RescheduleRequest.Status.WAITING_OPPONENT
    assert request.confirmations.get().response == TeamConfirmation.Response.PENDING
    withdraw_request(
        actor=setup["accounts"][0], request_id=request.id, expected_version=request.version, now=now
    )
    request.refresh_from_db()
    assert request.status == RescheduleRequest.Status.WITHDRAWN


def test_policy_change_requires_explicit_cancellation_and_can_reenable():
    setup = reschedule_setup()
    request, now = submit(setup)
    payload = configuration_payload(setup["season"])
    payload["periods"][0]["usage"] = Period.Usage.DISABLED
    preview = preview_season_configuration(
        season=setup["season"], expected_version=setup["season"].version, payload=payload
    )
    assert str(request.id) in preview["period_blocking_request_ids"]
    with pytest.raises(SeasonManagementError) as raised:
        save(setup, payload)
    assert raised.value.code == "PERIOD_ACTIVE_REQUESTS"
    withdraw_request(
        actor=setup["accounts"][0], request_id=request.id, expected_version=request.version, now=now
    )
    before = list(setup["season"].games.values("id", "date", "period_id", "start_time", "status"))
    save(setup, payload)
    payload = configuration_payload(setup["season"])
    # Old clients omit usage: existing restrictions are preserved.
    save(setup, payload)
    setup["period"].refresh_from_db()
    assert setup["period"].usage == Period.Usage.DISABLED
    payload["periods"][0]["usage"] = Period.Usage.OPEN
    save(setup, payload)
    assert (
        list(setup["season"].games.values("id", "date", "period_id", "start_time", "status"))
        == before
    )
    request.refresh_from_db()
    assert request.is_terminal


def test_add_delete_and_copy_custom_periods():
    setup = reschedule_setup()
    season = setup["season"]
    season.status = Season.Status.SETUP
    season.save()
    payload = configuration_payload(season)
    payload["periods"].append(
        {
            "name": "午间补赛",
            "start_time": time(11, 15),
            "default_capacities": {"WEEKDAY": 0, "WEEKEND": 0},
        }
    )
    save(setup, payload)
    period = setup["season"].periods.get(name="午间补赛")
    assert period.usage == Period.Usage.ADMIN_ONLY
    copy = create_season(
        actor=setup["superadmin"],
        name="下届",
        competition_type=season.competition_type,
        year=season.year + 1,
        starts_on=season.starts_on + timedelta(days=365),
        ends_on=season.ends_on + timedelta(days=365),
        template_season_id=season.id,
    )
    copied = copy.periods.get(code=period.code)
    assert (copied.name, copied.start_time, copied.usage) == (
        period.name,
        period.start_time,
        period.usage,
    )
    assert copied.id != period.id
    payload = configuration_payload(setup["season"])
    payload["periods"] = [row for row in payload["periods"] if str(row["id"]) != str(period.id)]
    save(setup, payload)
    assert not Period.objects.filter(id=period.id).exists()


def test_disabled_capacity_cannot_be_changed_by_direct_configuration_request():
    setup = reschedule_setup()
    Period.objects.filter(id=setup["period"].id).update(usage=Period.Usage.DISABLED)
    payload = configuration_payload(setup["season"])
    payload["periods"][0]["default_capacities"]["WEEKDAY"] += 1
    with pytest.raises(SeasonManagementError) as raised:
        save(setup, payload)
    assert raised.value.code == "PERIOD_CAPACITY_READ_ONLY"
    payload["periods"][0]["usage"] = Period.Usage.OPEN
    save(setup, payload)


@pytest.mark.parametrize("step", ["vote", "approve", "voter", "final"])
def test_every_forward_review_step_rechecks_period_policy(step):
    setup = reschedule_setup()
    assign_group_teams(setup)
    game = setup["games"][0]
    now = valid_submission_time(game.date, setup["target_date"])
    request = submit_reschedule(
        actor=setup["accounts"][0],
        game_id=game.id,
        expected_game_version=game.version,
        target_date=setup["target_date"],
        target_period_id=setup["period"].id,
        process_route="HANDBOOK_REVIEW",
        now=now,
    )
    request = respond_to_opponent(
        actor=setup["accounts"][1],
        request_id=request.id,
        expected_version=request.version,
        accept=True,
        now=now,
    )
    if step in {"voter", "final"}:
        request = admin_decide_review_route(
            actor=setup["superadmin"],
            request_id=request.id,
            expected_version=request.version,
            action="vote",
            classification="CROSS_ROUND",
            selected_team_ids=[setup["teams"][2].id],
            now=now,
        )
    if step == "final":
        request = respond_as_selected_team(
            actor=setup["accounts"][2],
            request_id=request.id,
            expected_version=request.version,
            accept=True,
            now=now,
        )
    Period.objects.filter(id=request.target_period_id).update(usage=Period.Usage.DISABLED)
    request.refresh_from_db()
    before = (request.version, request.status, list(request.confirmations.values()))
    assert not {"ADMIN_APPROVE", "ADMIN_START_VOTE", "ADMIN_FINAL_APPROVE"}.intersection(
        _actions(request, setup["superadmin"])
    )
    with pytest.raises(RescheduleError) as raised:
        if step in {"vote", "approve"}:
            admin_decide_review_route(
                actor=setup["superadmin"],
                request_id=request.id,
                expected_version=request.version,
                action=step,
                classification="CROSS_ROUND",
                selected_team_ids=[setup["teams"][2].id] if step == "vote" else [],
                now=now,
            )
        elif step == "voter":
            respond_as_selected_team(
                actor=setup["accounts"][2],
                request_id=request.id,
                expected_version=request.version,
                accept=True,
                now=now,
            )
        else:
            admin_final_decision(
                actor=setup["superadmin"],
                request_id=request.id,
                expected_version=request.version,
                approve=True,
                now=now,
            )
    assert raised.value.code == "TARGET_PERIOD_NOT_OPEN"
    request.refresh_from_db()
    assert (request.version, request.status, list(request.confirmations.values())) == before
    admin_cancel_request(
        actor=setup["superadmin"], request_id=request.id, expected_version=request.version, now=now
    )
    request.refresh_from_db()
    assert request.status == RescheduleRequest.Status.ADMIN_CANCELLED


@pytest.mark.parametrize("status", [Season.Status.PUBLISHED, Season.Status.ARCHIVED])
def test_existing_period_cannot_be_deleted_after_setup(status):
    setup = reschedule_setup()
    setup["season"].status = status
    setup["season"].save()
    payload = configuration_payload(setup["season"])
    payload["periods"].pop()
    with pytest.raises(SeasonManagementError) as raised:
        save(setup, payload)
    assert raised.value.code in {"PERIOD_DELETE_BLOCKED", "SEASON_ARCHIVED"}


def test_delete_checks_empty_draft_columns_and_duplicate_active_times():
    from core.models import ScheduleGridDraft, ScheduleGridDraftColumn

    setup = reschedule_setup()
    season = setup["season"]
    season.status = Season.Status.SETUP
    season.save()
    period = season.periods.last()
    draft = ScheduleGridDraft.objects.create(season=season, updated_by=setup["superadmin"])
    ScheduleGridDraftColumn.objects.create(draft=draft, period=period, venue_name="本地场地")
    payload = configuration_payload(season)
    payload["periods"].pop()
    with pytest.raises(SeasonManagementError) as raised:
        save(setup, payload)
    assert raised.value.code == "PERIOD_DELETE_BLOCKED"
    payload = configuration_payload(season)
    payload["periods"][1]["start_time"] = payload["periods"][0]["start_time"]
    with pytest.raises(SeasonManagementError) as raised:
        save(setup, payload)
    assert raised.value.code == "DUPLICATE_VALUE"


@pytest.mark.django_db(transaction=True)
def test_submission_and_closing_period_serialize_without_partial_writes():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from django.db import connections

    setup = reschedule_setup()
    payload = configuration_payload(setup["season"])
    payload["periods"][0]["usage"] = Period.Usage.ADMIN_ONLY
    barrier = Barrier(2)

    def run(operation):
        connections.close_all()
        try:
            barrier.wait(timeout=10)
            operation()
            return "OK"
        except (SeasonManagementError, RescheduleError) as error:
            return error.code
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(run, lambda: submit(setup))
        second = pool.submit(run, lambda: save(setup, payload))
        result = {first.result(timeout=20), second.result(timeout=20)}
    assert result in [{"OK", "TARGET_PERIOD_NOT_OPEN"}, {"OK", "PERIOD_ACTIVE_REQUESTS"}]
    setup["period"].refresh_from_db()
    assert not (setup["period"].usage != Period.Usage.OPEN and RescheduleRequest.objects.exists())


def test_old_client_http_requests_receive_structured_policy_conflict():
    setup = reschedule_setup()
    request, now = submit(setup)
    Period.objects.filter(id=setup["period"].id).update(usage=Period.Usage.ADMIN_ONLY)
    client = Client()
    with patch("core.services.rescheduling.timezone.now", return_value=now):
        response = post_json(
            client, f"/api/v1/reschedule-requests/{request.id}/opponent-response",
            {"expected_version": request.version, "accept": True},
            issue_session(setup["accounts"][1]),
        )
    assert response.status_code == 409
    assert response.json()["code"] == "TARGET_PERIOD_NOT_OPEN"
    request.refresh_from_db()
    assert request.status == RescheduleRequest.Status.WAITING_OPPONENT
    assert request.confirmations.get().response == TeamConfirmation.Response.PENDING


def test_disabled_period_allows_result_correction_but_not_rescheduling():
    from core.models import Game
    from core.services.competition_corrections import preview_correction
    from core.tests.test_competition_corrections import _change

    setup = reschedule_setup()
    game = setup["games"][0]
    Period.objects.filter(id=game.period_id).update(usage=Period.Usage.DISABLED)
    payload = _change(game, home_score=64, away_score=61, status=Game.Status.COMPLETED,
                      override_rules=True, confirmed=True)
    payload.pop("game_id")
    token = issue_session(setup["superadmin"])
    response = put_json(Client(), f"/api/v1/admin/mobile/games/{game.id}", payload, token)
    assert response.status_code == 200, response.content
    game.refresh_from_db()
    assert (game.home_score, game.away_score) == (64, 61)
    payload.update(expected_version=game.version, date=(game.date + timedelta(days=1)).isoformat())
    response = put_json(Client(), f"/api/v1/admin/mobile/games/{game.id}", payload, token)
    assert response.status_code == 409
    assert response.json()["code"] == "TARGET_PERIOD_DISABLED"
    kwargs = dict(actor=setup["superadmin"], season_id=setup["season"].id,
                  expected_season_version=setup["season"].version,
                  downstream_resolutions=[], reason="本地测试")
    unchanged = preview_correction(**kwargs, changes=[_change(game, home_score=65)])
    assert not any(item["code"] == "TARGET_PERIOD_DISABLED" for item in unchanged["blockers"])
    changed = preview_correction(**kwargs, changes=[_change(game, date=payload["date"])])
    assert any(item["code"] == "TARGET_PERIOD_DISABLED" for item in changed["blockers"])


def test_custom_period_template_and_disabled_draft_keep_explicit_semantics():
    from core.services.schedule_drafts import (
        get_or_create_schedule_draft,
        replace_schedule_draft,
        serialize_schedule_draft,
    )
    from core.services.schedule_imports_v3 import ScheduleImportError, _default_grid_columns
    from core.tests.test_schedule_imports_v3 import _setup

    setup = _setup()
    period = Period.objects.create(season=setup["season"], code="custom", name="新时段",
                                   start_time=time(10, 10), usage=Period.Usage.ADMIN_ONLY)
    columns = [row for row in _default_grid_columns(setup["season"])
               if row.period_id == str(period.id)]
    assert columns and all(not row.final_only for row in columns)
    draft = get_or_create_schedule_draft(actor=setup["actor"], season=setup["season"])
    period.usage = Period.Usage.DISABLED
    period.save()
    snapshot = serialize_schedule_draft(draft)
    assert any(row["period_id"] == str(period.id) for row in snapshot["columns"])
    assert all(row.period_id != str(period.id) for row in _default_grid_columns(setup["season"]))
    with pytest.raises(ScheduleImportError, match="停用"):
        replace_schedule_draft(actor=setup["actor"], season=setup["season"],
                               expected_version=draft.version,
                               columns=snapshot["columns"], cells=[])
    draft.refresh_from_db()
    assert draft.version == snapshot["version"]
