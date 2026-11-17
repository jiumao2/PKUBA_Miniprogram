from __future__ import annotations

import json
from datetime import time, timedelta
from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import override_settings
from django.utils import timezone

from core.models import (
    Account,
    Division,
    Game,
    InboxItem,
    Period,
    Season,
    Team,
    WeChatDelivery,
    WeChatIdentity,
    WeChatSubscriptionPreference,
)
from core.services.notifications import process_delivery, template_config, template_errors

MAPPING = {
    "game": {
        "template_id": "synthetic-game-template",
        "type": "ONCE",
        "name": "合成比赛更新提醒",
        "fields": {
            "thing1": "game_title",
            "time2": "game_start_time",
            "thing3": "game_location",
            "phrase4": "game_status",
            "thing5": "summary",
        },
    }
}


@pytest.mark.parametrize(
    "key,item",
    [
        ("game", {**MAPPING["game"], "template_id": "REPLACE_WITH_TEMPLATE"}),
        ("game", {**MAPPING["game"], "template_id": {}}),
        ("game", {**MAPPING["game"], "type": "FOREVER"}),
        ("game", {**MAPPING["game"], "fields": {"phrase1": "game_title"}}),
        ("game", {**MAPPING["game"], "fields": {"time1": "summary"}}),
        ("game", {**MAPPING["game"], "fields": {"thing1": "game_start_time"}}),
        ("game", {**MAPPING["game"], "fields": {"name1": "title"}}),
        ("game", {**MAPPING["game"], "fields": {"thing1": {"email": "private"}}}),
        ("game", {**MAPPING["game"], "fields": {"thing1": "leader_phone"}}),
        ("business", MAPPING["game"]),
        ("registration", MAPPING["game"]),
        ("unknown", MAPPING["game"]),
    ],
)
def test_incompatible_or_private_mappings_are_not_exposed_to_subscription(key, item):
    assert template_errors(item, key=key)
    with override_settings(WECHAT_SUBSCRIBE_TEMPLATES={key: item}):
        assert template_config() == {}


def test_configuration_check_is_offline_and_does_not_expose_values(tmp_path):
    source = tmp_path / "templates.json"
    source.write_text(json.dumps(MAPPING), encoding="utf-8-sig")
    output = StringIO()
    with (
        override_settings(WECHAT_APP_SECRET="do-not-display-this", WECHAT_SUBSCRIBE_ENABLED=False),
        patch.object(connection, "cursor", side_effect=AssertionError("No database access")),
        patch(
            "core.services.notifications._post", side_effect=AssertionError("No platform access")
        ),
    ):
        call_command("check_wechat_notifications", config_file=str(source), stdout=output)
    report = json.loads(output.getvalue())
    assert report["configuration"] == "VALID"
    assert report["sending_enabled"] is False
    assert report["platform_verified"] is False
    assert report["templates"] == ["game"]
    assert "synthetic-game-template" not in output.getvalue()
    assert "do-not-display-this" not in output.getvalue()


def test_invalid_config_error_does_not_echo_values(tmp_path):
    source = tmp_path / "templates.json"
    source.write_text('{"secret-value":{"template_id":"private-value"}}', encoding="utf-8")
    with pytest.raises(CommandError) as error:
        call_command("check_wechat_notifications", config_file=str(source))
    assert "secret-value" not in str(error.value)
    assert "private-value" not in str(error.value)


@pytest.mark.parametrize("content", ["not-json", "[]", "{}"])
def test_configuration_check_rejects_empty_or_invalid_json(tmp_path, content):
    source = tmp_path / "templates.json"
    source.write_text(content, encoding="utf-8")
    with pytest.raises(CommandError):
        call_command("check_wechat_notifications", config_file=str(source))


@pytest.fixture
def delivery_context(db):
    day = timezone.localdate() + timedelta(days=10)
    season = Season.objects.create(
        name="合成通知赛季",
        competition_type="PKU_CUP",
        year=day.year,
        status="PUBLISHED",
        starts_on=day,
        ends_on=day + timedelta(days=20),
    )
    division = Division.objects.create(season=season, code="test", name="合成组")
    period = Period.objects.create(season=season, code="p1", name="午间", start_time=time(12, 50))
    home = Team.objects.create(season=season, division=division, name="合成主队")
    away = Team.objects.create(season=season, division=division, name="合成客队")
    game = Game.objects.create(
        season=season,
        division=division,
        code="NOTICE-1",
        date=day,
        period=period,
        start_time=time(12, 50),
        venue_name="合成球场",
        home_team=home,
        away_team=away,
    )
    person = Account.objects.create_user(username="synthetic-notice-user")
    inbox = InboxItem.objects.create(
        season=season,
        account=person,
        kind="NOTICE_GAME",
        title="比赛有更新",
        route="GAME",
        object_id=game.id,
        dedupe_key="game-detail",
    )
    WeChatIdentity.objects.create(account=person, app_id="synthetic-app", openid="synthetic-openid")
    WeChatSubscriptionPreference.objects.create(
        account=person, template_id=MAPPING["game"]["template_id"], choice="accept"
    )
    return game, inbox


@override_settings(
    WECHAT_SUBSCRIBE_ENABLED=True,
    WECHAT_APP_ID="synthetic-app",
    WECHAT_APP_SECRET="synthetic-secret",
    WECHAT_SUBSCRIBE_TEMPLATES=MAPPING,
)
def test_game_fields_use_latest_schedule_instead_of_event_creation_time(delivery_context):
    game, inbox = delivery_context
    # The notice exists before this schedule update: dispatch must use the latest state.
    Game.objects.filter(id=game.id).update(start_time=time(15, 20), venue_name="最新球场" * 10)
    with patch(
        "core.services.notifications._post",
        side_effect=[{"access_token": "local-token"}, {"errcode": 0}],
    ) as post:
        assert process_delivery()
    payload = post.call_args.args[1]
    assert payload["data"]["time2"]["value"] == f"{game.date:%Y年%m月%d日} 15:20"
    assert payload["data"]["thing3"]["value"] == ("最新球场" * 10)[:20]
    assert len(payload["data"]["thing1"]["value"]) <= 20
    assert payload["data"]["phrase4"]["value"] == "未赛"
    assert payload["page"] == f"pages/game-media/index?id={game.id}"
    assert WeChatDelivery.objects.get(inbox=inbox).status == "SENT"


@override_settings(
    WECHAT_SUBSCRIBE_ENABLED=True,
    WECHAT_APP_ID="synthetic-app",
    WECHAT_APP_SECRET="synthetic-secret",
    WECHAT_SUBSCRIBE_TEMPLATES=MAPPING,
)
@pytest.mark.parametrize("missing", ["object", "season", "route", "venue"])
def test_missing_game_context_keeps_inbox_without_any_platform_request(delivery_context, missing):
    game, inbox = delivery_context
    if missing == "object":
        inbox.object_id = None
    elif missing == "season":
        inbox.season = None
    elif missing == "route":
        inbox.route = "REGISTRATION"
    else:
        Game.objects.filter(id=game.id).update(venue_name="")
    inbox.save()
    with patch("core.services.notifications._post") as post:
        assert process_delivery()
    post.assert_not_called()
    delivery = WeChatDelivery.objects.get(inbox=inbox)
    assert (delivery.status, delivery.last_code) == ("FAILED", "TEMPLATE_CONTEXT_MISSING")
    assert InboxItem.objects.filter(id=inbox.id).exists()
    assert not process_delivery()
