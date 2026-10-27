from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.db import IntegrityError, close_old_connections, transaction
from django.test import Client, override_settings
from django.utils import timezone

from core.models import (
    Account,
    BusinessNotificationEvent,
    Division,
    InboxItem,
    MiniAppSession,
    RegistrationDecision,
    RegistrationPlayerClaim,
    RegistrationPolicy,
    RosterPlayer,
    Season,
    SeasonLeaderBinding,
    Team,
    TeamRegistration,
    WeChatDelivery,
    WeChatIdentity,
    WeChatSubscriptionPreference,
)
from core.services import registrations as r
from core.services.notifications import DeliveryUnavailable, process_delivery, queue_notice
from core.services.referees import RefereeError
from core.services.wechat import token_digest

pytestmark = pytest.mark.django_db


@pytest.fixture
def data():
    today = timezone.localdate()
    season = Season.objects.create(
        name="合成报名赛季",
        competition_type="PKU_CUP",
        year=today.year,
        status="PUBLISHED",
        starts_on=today,
        ends_on=today + timedelta(days=60),
    )
    division = Division.objects.create(season=season, code="men", name="测试组")
    users = [Account.objects.create_user(username=f"registration-user-{i}") for i in range(3)]
    admin = Account.objects.create_user(username="registration-admin", role="ADMIN")
    RegistrationPolicy.objects.create(season=season, deadline=timezone.now() + timedelta(days=2))
    return season, division, users, admin


def form(name="合成球队", student="001234", jersey="00"):
    return {
        "team_name": name,
        "uniform_color": "白",
        "leader_name": "合成领队",
        "phone": "10000000000",
        "email": "synthetic@example.invalid",
        "players": [
            {
                "id": "",
                "college": "测试学院",
                "name": "合成队员",
                "student_number": student,
                "jersey_number": jersey,
            }
        ],
    }


def submit(data, index=0, student="001234"):
    season, _, users, _ = data
    item = r.create_registration(
        actor=users[index], season_id=season.id, team_name=f"合成球队{index}"
    )
    return r.save_registration(
        actor=users[index],
        registration_id=item.id,
        expected_version=item.version,
        form=form(f"合成球队{index}", student),
        submit=True,
    )


def review(data, item, approved=True):
    _, division, _, admin = data
    return r.review_registration(
        actor=admin,
        registration_id=item.id,
        expected_version=item.version,
        approved=approved,
        reason="请核对" if not approved else "",
        division_id=division.id,
    )


def headers(user):
    token = f"registration-local-test-{user.id}"
    MiniAppSession.objects.create(
        account=user, token_hash=token_digest(token), expires_at=timezone.now() + timedelta(hours=1)
    )
    return {"HTTP_AUTHORIZATION": f"Bearer {token}"}


def test_create_draft_private_and_approval(data):
    season, division, users, _ = data
    item = submit(data)
    assert not Team.objects.exists()
    assert RegistrationPlayerClaim.objects.get().student_number == "001234"
    with pytest.raises(RefereeError) as error:
        r.get_registration(item.id, users[1])
    assert error.value.status == 403
    item = review(data, item)
    assert item.status == "APPROVED"
    assert item.team.division == division
    assert SeasonLeaderBinding.objects.get(active=True).account == users[0]
    assert RosterPlayer.objects.get().jersey_number == "00"
    assert RegistrationDecision.objects.count() == 1
    assert "student_number" not in Client().get("/api/v1/public/standings").content.decode()
    assert RegistrationPlayerClaim.objects.get().player_id == RosterPlayer.objects.get().id


def test_return_releases_and_resubmit_rechecks(data):
    item = review(data, submit(data), False)
    assert not RegistrationPlayerClaim.objects.exists()
    other = submit(data, 1)
    assert other.status == "PENDING"
    with pytest.raises(RefereeError) as error:
        r.save_registration(
            actor=data[2][0],
            registration_id=item.id,
            expected_version=item.version,
            form=item.form,
            submit=True,
        )
    assert error.value.code == "STUDENT_ALREADY_REGISTERED"


def test_deadline_blocks_drafts_resubmit_and_changes(data):
    item = review(data, submit(data))
    RegistrationPolicy.objects.update(deadline=timezone.now() - timedelta(seconds=1))
    with pytest.raises(RefereeError) as error:
        r.save_registration(
            actor=data[2][0],
            registration_id=item.id,
            expected_version=item.version,
            form=item.form,
            submit=True,
            reason="增员",
        )
    assert error.value.code == "REGISTRATION_CLOSED"
    with pytest.raises(RefereeError):
        r.create_registration(actor=data[2][1], season_id=data[0].id, team_name="另一队")


def test_deadline_still_allows_pending_review(data):
    item = submit(data)
    RegistrationPolicy.objects.update(deadline=timezone.now() - timedelta(seconds=1))
    assert review(data, item).status == "APPROVED"


def test_setup_season_can_register_before_public_schedule(data):
    data[0].status = "SETUP"
    data[0].save(update_fields=["status"])
    item = submit(data)
    client = Client()
    response = client.get("/api/v1/registrations/context", **headers(data[2][0]))
    assert response.status_code == 200
    assert response.json()["registration"]["id"] == str(item.id)
    assert response.json()["policy"]["open"]
    assert not Team.objects.exists()


def test_existing_team_link_retains_roster_ids(data):
    team = Team.objects.create(season=data[0], division=data[1], name="已有球队")
    player = RosterPlayer.objects.create(team=team, name="原有队员", jersey_number="1")
    SeasonLeaderBinding.objects.create(season=data[0], team=team, account=data[2][0])
    item = r.create_registration(actor=data[2][0], season_id=data[0].id, team_name="不应新建")
    details = form("已有球队")
    details["players"][0]["id"] = str(player.id)
    details["players"][0]["name"] = player.name
    item = r.save_registration(
        actor=data[2][0],
        registration_id=item.id,
        expected_version=item.version,
        form=details,
        submit=True,
        reason="补全报名资料",
    )
    review(data, item)
    assert RosterPlayer.objects.count() == 1
    assert RegistrationPlayerClaim.objects.get().player_id == player.id


def test_remove_then_readd_keeps_historical_identity(data):
    item = review(data, submit(data))
    identity = RosterPlayer.objects.get().id
    without = {**item.form, "players": []}
    item = r.save_registration(
        actor=data[2][0],
        registration_id=item.id,
        expected_version=item.version,
        form=without,
        submit=True,
        reason="移除",
    )
    item = review(data, item)
    assert not RegistrationPlayerClaim.objects.exists()
    item = r.save_registration(
        actor=data[2][0],
        registration_id=item.id,
        expected_version=item.version,
        form=form(item.team.name),
        submit=True,
        reason="重新加入",
    )
    review(data, item)
    assert RosterPlayer.objects.get().id == identity
    assert RosterPlayer.objects.get().active


def test_return_after_deadline_cannot_reopen(data):
    item = submit(data)
    RegistrationPolicy.objects.update(deadline=timezone.now() - timedelta(seconds=1))
    item = review(data, item, False)
    assert not RegistrationPlayerClaim.objects.exists()
    with pytest.raises(RefereeError) as error:
        r.save_registration(
            actor=data[2][0],
            registration_id=item.id,
            expected_version=item.version,
            form=item.form,
            submit=True,
        )
    assert error.value.code == "REGISTRATION_CLOSED"


def test_old_leader_cannot_replay_private_create_after_handover(data):
    client = Client()
    auth = headers(data[2][0])
    payload = {"season_id": str(data[0].id), "team_name": "接口交接队"}
    response = client.post(
        "/api/v1/registrations/",
        json.dumps(payload),
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY="handover-local",
        **auth,
    )
    item = TeamRegistration.objects.get(id=response.json()["id"])
    transfer = r.initiate_handover(
        actor=data[2][0],
        registration_id=item.id,
        expected_version=item.version,
        username=data[2][1].username,
    )
    r.resolve_handover(
        actor=data[2][1],
        handover_id=transfer.id,
        expected_version=1,
        accept=True,
        contact={
            "leader_name": "合成接任",
            "phone": "10000000001",
            "email": "next@example.invalid",
        },
    )
    again = client.post(
        "/api/v1/registrations/",
        json.dumps(payload),
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY="handover-local",
        **auth,
    )
    assert again.status_code == 403
    assert "next@example.invalid" not in again.content.decode()


def test_released_binding_does_not_keep_former_leader_ownership(data):
    item = review(data, submit(data))
    SeasonLeaderBinding.objects.filter(team=item.team, active=True).update(
        active=False, released_at=timezone.now()
    )
    with pytest.raises(RefereeError):
        r.get_registration(item.id, data[2][0])
    next_item = r.create_registration(
        actor=data[2][0], season_id=data[0].id, team_name="新的合成队"
    )
    assert next_item.id != item.id


def test_roster_change_review_preserves_identity_and_reject_releases_additions(data):
    item = review(data, submit(data))
    player_id = RosterPlayer.objects.get().id
    edited = {
        **item.form,
        "players": [
            {**item.form["players"][0], "jersey_number": "2"},
            {**form(student="999")["players"][0], "name": "新增合成队员", "jersey_number": "3"},
        ],
    }
    item = r.save_registration(
        actor=data[2][0],
        registration_id=item.id,
        expected_version=item.version,
        form=edited,
        submit=True,
        reason="增加一名队员",
    )
    assert RosterPlayer.objects.count() == 1
    assert RegistrationPlayerClaim.objects.count() == 2
    item = review(data, item, False)
    assert RegistrationPlayerClaim.objects.count() == 1
    item = r.save_registration(
        actor=data[2][0],
        registration_id=item.id,
        expected_version=item.version,
        form=edited,
        submit=True,
        reason="修改名单",
    )
    review(data, item)
    assert RosterPlayer.objects.get(id=player_id).jersey_number == "2"
    assert RosterPlayer.objects.count() == 2


def test_handover_accepts_and_revokes_private_access(data):
    item = review(data, submit(data))
    transfer = r.initiate_handover(
        actor=data[2][0],
        registration_id=item.id,
        expected_version=item.version,
        username=data[2][1].username,
    )
    r.resolve_handover(
        actor=data[2][1],
        handover_id=transfer.id,
        expected_version=transfer.version,
        accept=True,
        contact={
            "leader_name": "合成接任者",
            "phone": "10000000001",
            "email": "next@example.invalid",
        },
    )
    assert r.get_registration(item.id, data[2][1]).leader_id == data[2][1].id
    with pytest.raises(RefereeError):
        r.get_registration(item.id, data[2][0])
    assert SeasonLeaderBinding.objects.filter(active=True).get().account_id == data[2][1].id


def test_stale_handover_cannot_transfer_changed_application(data):
    item = submit(data)
    transfer = r.initiate_handover(
        actor=data[2][0],
        registration_id=item.id,
        expected_version=item.version,
        username=data[2][1].username,
    )
    review(data, item, False)
    with pytest.raises(RefereeError) as error:
        r.resolve_handover(
            actor=data[2][1],
            handover_id=transfer.id,
            expected_version=1,
            accept=True,
            contact={
                "leader_name": "合成接任",
                "phone": "10000000001",
                "email": "a@example.invalid",
            },
        )
    assert error.value.code == "VERSION_CONFLICT"


def test_database_prevents_claiming_other_team_while_registration_exists(data):
    item = submit(data)
    team = Team.objects.create(season=data[0], division=data[1], name="已有球队")
    with pytest.raises(IntegrityError), transaction.atomic():
        SeasonLeaderBinding.objects.create(season=data[0], account=data[2][0], team=team)
    assert item.status == "PENDING"


def test_idempotent_create_and_private_access(data):
    client = Client()
    auth = headers(data[2][0])
    payload = json.dumps({"season_id": str(data[0].id), "team_name": "接口合成队"})
    first = client.post(
        "/api/v1/registrations/",
        payload,
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY="create-local",
        **auth,
    )
    assert first.status_code == 200, first.content
    again = client.post(
        "/api/v1/registrations/",
        payload,
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY="create-local",
        **auth,
    )
    assert again.json()["id"] == first.json()["id"]
    outsider = client.get(f"/api/v1/registrations/{first.json()['id']}", **headers(data[2][1]))
    assert outsider.status_code == 403
    assert TeamRegistration.objects.count() == 1


def test_notifications_atomic_deduped_and_no_contact_data(data):
    person = data[2][0]
    item = submit(data)
    before = BusinessNotificationEvent.objects.count()
    with pytest.raises(RuntimeError), transaction.atomic():
        queue_notice(season_id=data[0].id, kind="GAME", object_id=item.id, recipients=[person.id])
        raise RuntimeError("rollback")
    assert BusinessNotificationEvent.objects.count() == before
    with transaction.atomic():
        queue_notice(
            season_id=data[0].id,
            kind="REGISTRATION",
            object_id=item.id,
            recipients=[person.id, person.id],
        )
        queue_notice(
            season_id=data[0].id, kind="REGISTRATION", object_id=item.id, recipients=[person.id]
        )
    notices = InboxItem.objects.filter(account=person)
    # This test runs within one transaction, including submit; one object/event per account.
    assert notices.count() == 1
    assert "10000000000" not in notices.get().body
    assert "001234" not in notices.get().body
    assert WeChatDelivery.objects.filter(inbox__account=person).count() == 1


TEMPLATES = {
    "business": {
        "template_id": "synthetic-template",
        "type": "ONCE",
        "name": "测试业务提醒",
        "fields": {"thing1": "title", "time2": "time"},
    }
}


@override_settings(
    WECHAT_SUBSCRIBE_ENABLED=True,
    WECHAT_APP_ID="synthetic-app",
    WECHAT_APP_SECRET="synthetic-secret",
    WECHAT_SUBSCRIBE_TEMPLATES=TEMPLATES,
)
@pytest.mark.parametrize(
    "result,status",
    [({"errcode": 0}, "SENT"), ({"errcode": 43101}, "NEEDS_AUTH"), ({"errcode": 47003}, "FAILED")],
)
def test_wechat_delivery_outcomes(data, result, status):
    person = data[2][0]
    item = InboxItem.objects.create(
        account=person,
        kind="NOTICE_GAME",
        title="合成比赛有更新",
        route="GAME",
        dedupe_key="local-delivery",
    )
    WeChatIdentity.objects.create(account=person, app_id="synthetic-app", openid="synthetic-openid")
    WeChatSubscriptionPreference.objects.create(
        account=person, template_id="synthetic-template", choice="accept"
    )
    with patch(
        "core.services.notifications._post",
        side_effect=[{"access_token": "synthetic-token"}, result],
    ) as post:
        assert process_delivery()
        sent = post.call_args.args[1]
        assert sent["data"]["thing1"]["value"] == item.title
        assert "openid" not in json.dumps(sent["data"])
    assert WeChatDelivery.objects.get(inbox=item).status == status


@override_settings(
    WECHAT_SUBSCRIBE_ENABLED=True,
    WECHAT_APP_ID="synthetic-app",
    WECHAT_APP_SECRET="synthetic-secret",
    WECHAT_SUBSCRIBE_TEMPLATES=TEMPLATES,
)
def test_unknown_send_does_not_retry(data):
    person = data[2][0]
    item = InboxItem.objects.create(
        account=person, kind="NOTICE_GAME", title="比赛变动", route="GAME", dedupe_key="unknown"
    )
    WeChatIdentity.objects.create(account=person, app_id="synthetic-app", openid="synthetic-openid")
    WeChatSubscriptionPreference.objects.create(
        account=person, template_id="synthetic-template", choice="accept"
    )
    with patch(
        "core.services.notifications._post",
        side_effect=[{"access_token": "synthetic-token"}, DeliveryUnavailable()],
    ):
        assert process_delivery()
    assert WeChatDelivery.objects.get(inbox=item).status == "UNKNOWN"
    assert not process_delivery()
    assert InboxItem.objects.filter(id=item.id).exists()


@pytest.mark.django_db(transaction=True)
def test_concurrent_same_student_only_one_application_wins(data):
    ids = [
        r.create_registration(actor=user, season_id=data[0].id, team_name=f"并发队{i}").id
        for i, user in enumerate(data[2][:2])
    ]

    def run(index):
        close_old_connections()
        try:
            r.save_registration(
                actor=Account.objects.get(id=data[2][index].id),
                registration_id=ids[index],
                expected_version=1,
                form=form(f"并发队{index}"),
                submit=True,
            )
            return "ok"
        except RefereeError as error:
            return error.code
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(run, [0, 1])) == ["STUDENT_ALREADY_REGISTERED", "ok"]
    assert RegistrationPlayerClaim.objects.count() == 1
