from __future__ import annotations

from typing import Literal

from django.db import transaction
from django.http import HttpRequest
from ninja import Header, Router, Schema, Status
from pydantic import Field

from core.api_referees import ERRORS, error_response
from core.api_security import miniapp_bearer_auth
from core.models import WeChatDelivery, WeChatSubscriptionPreference
from core.services.idempotency import IdempotencyError, execute_idempotent
from core.services.notifications import available_templates
from core.services.referees import RefereeError, current_actor

router = Router(tags=["notifications"], auth=miniapp_bearer_auth)


class NotificationTemplateOut(Schema):
    key: str
    template_id: str
    type: str
    name: str


class NotificationConfigOut(Schema):
    templates: list[NotificationTemplateOut]


class NotificationChoiceIn(Schema):
    template_id: str = Field(min_length=1, max_length=128)
    choice: Literal["accept", "reject", "ban", "filter"]


class NotificationChoiceOut(Schema):
    saved: bool


@router.get("/config", response=NotificationConfigOut)
def config(request: HttpRequest):
    return {"templates": available_templates()}


@transaction.atomic
def save_choice(request, payload):
    actor = current_actor(request.auth, lock=True)
    if payload.template_id not in {item["template_id"] for item in available_templates()}:
        raise RefereeError("该订阅模板尚未配置，请刷新。", "TEMPLATE_NOT_CONFIGURED", 409)
    WeChatSubscriptionPreference.objects.update_or_create(
        account=actor, template_id=payload.template_id, defaults={"choice": payload.choice}
    )
    if payload.choice == "accept":
        # This is a user preference hint, not a trusted platform quota counter.
        WeChatDelivery.objects.filter(
            inbox__account=actor, status="NEEDS_AUTH", template_id=payload.template_id
        ).update(status="PENDING", next_attempt_at=None)
    return 200, {"saved": True}


@router.post("/choices", response={200: NotificationChoiceOut, **ERRORS})
def choice(
    request: HttpRequest, payload: NotificationChoiceIn, idempotency_key: Header[str | None] = None
):
    try:
        status, body, _ = execute_idempotent(
            request=request,
            actor=request.auth,
            operation="notification.choice",
            fingerprint=payload.model_dump(),
            command=lambda: save_choice(request, payload),
        )
        return Status(status, body)
    except (RefereeError, IdempotencyError) as error:
        return error_response(error)
