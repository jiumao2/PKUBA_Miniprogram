from __future__ import annotations

import json
import re
from datetime import timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings
from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone

from core.models import Game, WeChatDelivery, WeChatIdentity

TEMPLATE_KEYS = {"registration", "handover", "team", "game", "referee", "task", "business"}
TEXT_SOURCES = {"title", "summary", "status", "game_title", "game_location", "game_status"}
TIME_SOURCES = {"date", "time", "game_start_time"}


def template_errors(item, *, key):
    """Return safe diagnostics, never echo configuration values or credentials."""
    if key not in TEMPLATE_KEYS:
        return ["未知的通知用途。"]
    if not isinstance(item, dict):
        return ["模板配置必须是对象。"]
    template_id = item.get("template_id")
    errors = []
    if (
        not isinstance(template_id, str)
        or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", template_id)
        or template_id.startswith("REPLACE_")
    ):
        errors.append("请填写公众平台生成的真实模板 ID。")
    if item.get("type") not in ("ONCE", "LONG"):
        errors.append("模板类型必须为 ONCE 或 LONG。")
    if "name" in item and not isinstance(item["name"], str):
        errors.append("模板名称必须是文字。")
    fields = item.get("fields")
    if not isinstance(fields, dict) or not fields:
        return errors + ["请填写模板关键词映射。"]
    for index, (field, source) in enumerate(fields.items(), 1):
        prefix = re.fullmatch(r"(thing|time|date|phrase)[0-9]+", field)
        if not prefix:
            errors.append(f"第 {index} 个关键词代码暂不支持，请核对真实字段类型。")
            continue
        if not isinstance(source, str) or source not in TEXT_SOURCES | TIME_SOURCES:
            errors.append(f"第 {index} 个关键词的数据来源不受支持。")
            continue
        kind = prefix.group(1)
        compatible = (
            (kind == "thing" and source in TEXT_SOURCES)
            or (kind == "time" and source in {"time", "game_start_time"})
            or (kind == "date" and source in TIME_SOURCES)
            or (kind == "phrase" and source in {"status", "game_status"})
        )
        if not compatible:
            errors.append(f"第 {index} 个关键词的类型与数据来源不匹配。")
        if source.startswith("game_") and key != "game":
            errors.append(f"第 {index} 个关键词的比赛数据仅适用于 game 通知。")
    return errors


def queue_notice(*, season_id, kind, object_id, recipients):
    # PostgreSQL groups changes to the same business object in one transaction.
    # Both inbox and delivery intent roll back with the originating business write.
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT queue_business_notice(%s, %s, %s, %s::jsonb)",
            [season_id, kind, object_id, json.dumps(sorted({str(i) for i in recipients if i}))],
        )


def template_config():
    result = {}
    raw = getattr(settings, "WECHAT_SUBSCRIBE_TEMPLATES", {})
    if not isinstance(raw, dict):
        return result
    for key, item in raw.items():
        if template_errors(item, key=key):
            continue
        result[key] = item
    return result


def available_templates():
    return [
        {
            "key": key,
            "template_id": value["template_id"],
            "type": value["type"],
            "name": value.get("name", "业务变动提醒"),
        }
        for key, value in template_config().items()
    ]


class DeliveryUnavailable(Exception):
    pass


class TemplateContextUnavailable(Exception):
    pass


def _post(path, payload):
    url = settings.WECHAT_API_BASE_URL.rstrip("/") + path
    request = Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=8) as response:  # noqa: S310
            result = json.loads(response.read().decode())
        if not isinstance(result, dict):
            raise DeliveryUnavailable
        return result
    except (HTTPError, URLError, TimeoutError, OSError, ValueError) as error:
        # Do not persist exceptions: their text may contain access tokens or OpenID.
        raise DeliveryUnavailable from error


def _token():
    response = _post(
        "/cgi-bin/stable_token",
        {
            "grant_type": "client_credential",
            "appid": settings.WECHAT_APP_ID,
            "secret": settings.WECHAT_APP_SECRET,
            "force_refresh": False,
        },
    )
    token = response.get("access_token")
    if not isinstance(token, str) or not token:
        raise DeliveryUnavailable
    return token


def _data(delivery, config):
    now = timezone.localtime(delivery.inbox.created_at)
    values = {
        "title": delivery.inbox.title[:20],
        "summary": "相关业务已更新，请进入小程序查看",
        "date": now.strftime("%Y年%m月%d日"),
        "time": now.strftime("%Y年%m月%d日 %H:%M"),
        "status": "已更新",
    }
    sources = set(config["fields"].values())
    if any(source.startswith("game_") for source in sources):
        inbox = delivery.inbox
        game = (
            Game.objects.select_related("home_team", "away_team")
            .filter(id=inbox.object_id, season_id=inbox.season_id)
            .first()
            if inbox.route == "GAME" and inbox.kind == "NOTICE_GAME" and inbox.object_id
            else None
        )
        if game is None:
            raise TemplateContextUnavailable
        home = game.home_team.name if game.home_team else "待定"
        away = game.away_team.name if game.away_team else "待定"
        values.update(
            game_title=f"{game.code} {home}对{away}",
            game_start_time=f"{game.date:%Y年%m月%d日} {game.start_time:%H:%M}",
            game_location=game.venue_name,
            game_status=game.get_status_display(),
        )
    data = {}
    for field, source in config["fields"].items():
        value = " ".join(values[source].split())
        if field.startswith("thing"):
            value = value[:20]
        if not value:
            raise TemplateContextUnavailable
        data[field] = {"value": value}
    return data


@transaction.atomic
def claim_delivery():
    now = timezone.now()
    # A process dying after sending cannot safely send the same message again.
    WeChatDelivery.objects.filter(
        status="SENDING", updated_at__lt=now - timedelta(minutes=2)
    ).update(status="UNKNOWN", last_code="WORKER_INTERRUPTED", updated_at=now)
    row = (
        WeChatDelivery.objects.select_for_update(skip_locked=True)
        .filter(status="PENDING")
        .filter(Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=now))
        .order_by("created_at", "id")
        .first()
    )
    if row is None:
        return None
    row.status = "SENDING"
    row.attempts += 1
    row.save(update_fields=["status", "attempts", "updated_at"])
    return row.id


def process_delivery():
    delivery_id = claim_delivery()
    if delivery_id is None:
        return False
    row = WeChatDelivery.objects.select_related("inbox__account").get(id=delivery_id)
    inbox = row.inbox
    configs = template_config()
    config = (
        configs.get(row.template_key)
        or (configs.get("registration") if row.template_key == "handover" else None)
        or configs.get("business")
    )
    state, code = "NOT_CONFIGURED", "NOT_CONFIGURED"
    if not inbox.account.is_active:
        state, code = "SKIPPED", "ACCOUNT_INACTIVE"
    elif (
        not config
        or not settings.WECHAT_APP_ID
        or not settings.WECHAT_APP_SECRET
        or not settings.WECHAT_SUBSCRIBE_ENABLED
    ):
        pass
    else:
        identity = WeChatIdentity.objects.filter(
            account_id=inbox.account_id, app_id=settings.WECHAT_APP_ID
        ).first()
        preference = inbox.account.wechatsubscriptionpreference_set.filter(
            template_id=config["template_id"]
        ).first()
        if identity is None or preference is None or preference.choice != "accept":
            state, code = "NEEDS_AUTH", "SUBSCRIBE_REQUIRED"
        else:
            # A handover must not forward private follow-up notifications to the former leader.
            from core.models import TeamRegistration
            from core.services.registrations import owns_registration

            registration = (
                TeamRegistration.objects.filter(id=inbox.object_id).first()
                if inbox.route == "REGISTRATION"
                else None
            )
            if (
                registration
                and inbox.kind != "NOTICE_HANDOVER"
                and not inbox.account.is_pkuba_admin
                and not owns_registration(registration, inbox.account)
            ):
                state, code = "SKIPPED", "ACCESS_CHANGED"
            else:
                try:
                    data = _data(row, config)
                except TemplateContextUnavailable:
                    state, code = "FAILED", "TEMPLATE_CONTEXT_MISSING"
                else:
                    try:
                        token = _token()
                    except DeliveryUnavailable:
                        state, code = "PENDING", "TOKEN_UNAVAILABLE"
                    else:
                        state, code = _send(row, identity, config, token, data)
    if state == "PENDING" and row.attempts >= 5:
        state = "FAILED"
    WeChatDelivery.objects.filter(id=row.id, status="SENDING").update(
        status=state,
        last_code=code,
        template_id=config["template_id"] if config else "",
        next_attempt_at=timezone.now() + timedelta(seconds=min(60 * 2**row.attempts, 900))
        if state == "PENDING"
        else None,
        updated_at=timezone.now(),
    )
    return True


def _send(row, identity, config, token, data):
    from core.api_inbox import _target_url

    try:
        result = _post(
            "/cgi-bin/message/subscribe/send?" + urlencode({"access_token": token}),
            {
                "touser": identity.openid,
                "template_id": config["template_id"],
                "page": _target_url(row.inbox).lstrip("/"),
                "data": data,
                "miniprogram_state": settings.WECHAT_SUBSCRIBE_STATE,
                "lang": "zh_CN",
            },
        )
        raw_code = result.get("errcode")
        code = str(raw_code) if isinstance(raw_code, int) else "INVALID_RESPONSE"
        if raw_code == 0:
            return "SENT", code
        if raw_code == 43101:
            return "NEEDS_AUTH", code
        if raw_code in [40001, 40014, 42001, 43108, -1]:
            return "PENDING", code
        return ("FAILED" if isinstance(raw_code, int) else "UNKNOWN"), code
    except DeliveryUnavailable:
        return "UNKNOWN", "SEND_RESULT_UNKNOWN"
