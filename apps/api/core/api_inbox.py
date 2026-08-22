from __future__ import annotations

from datetime import datetime
from uuid import UUID

from django.db.models import Q
from django.http import HttpRequest
from ninja import Router, Schema, Status

from core.api_security import miniapp_bearer_auth
from core.models import InboxItem
from core.services.inbox_tasks import mark_task_viewed

router = Router(tags=["inbox"], auth=miniapp_bearer_auth)


class InboxErrorOut(Schema):
    code: str
    message: str


class InboxSummaryOut(Schema):
    open_count: int
    display_count: str


class InboxTaskOut(Schema):
    id: UUID
    kind: str
    title: str
    body: str
    status: str
    due_at: datetime | None
    read_at: datetime | None
    closed_at: datetime | None
    close_reason: str
    target_url: str
    created_at: datetime
    updated_at: datetime


class InboxPageOut(Schema):
    items: list[InboxTaskOut]
    next_cursor: UUID | None


def _target_url(item: InboxItem) -> str:
    params = item.route_params if isinstance(item.route_params, dict) else {}
    if item.route == InboxItem.Route.RESCHEDULE_REQUEST:
        request_id = str(params.get("request_id") or item.object_id or "")
        return f"/pages/reschedule-requests/index?request_id={request_id}"
    if item.route == InboxItem.Route.SCORESHEET:
        scoresheet_id = str(params.get("scoresheet_id") or item.object_id or "")
        return f"/scoresheet/pages/editor/index?id={scoresheet_id}"
    return "/pages/admin/index"


def _serialize(item: InboxItem) -> dict[str, object]:
    return {
        "id": item.id,
        "kind": item.kind,
        "title": item.title,
        "body": item.body,
        "status": item.status,
        "due_at": item.due_at,
        "read_at": item.read_at,
        "closed_at": item.closed_at,
        "close_reason": item.close_reason,
        "target_url": _target_url(item),
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }


@router.get("/summary", response=InboxSummaryOut)
def inbox_summary(request: HttpRequest):
    count = InboxItem.objects.filter(
        account=request.auth,
        status=InboxItem.Status.OPEN,
    ).count()
    return {"open_count": count, "display_count": "99+" if count > 99 else str(count)}


@router.get("/", response={200: InboxPageOut, 400: InboxErrorOut})
def list_inbox(
    request: HttpRequest,
    status: str = InboxItem.Status.OPEN,
    cursor: UUID | None = None,
    page_size: int = 30,
):
    if status not in InboxItem.Status.values:
        return Status(400, {"code": "INBOX_STATUS_INVALID", "message": "任务状态不合法。"})
    page_size = min(max(page_size, 1), 50)
    items = InboxItem.objects.filter(account=request.auth, status=status)
    if cursor:
        anchor = InboxItem.objects.filter(
            id=cursor,
            account=request.auth,
            status=status,
        ).first()
        if anchor is None:
            return Status(400, {"code": "INBOX_CURSOR_INVALID", "message": "分页位置已失效。"})
        items = items.filter(
            Q(created_at__lt=anchor.created_at)
            | Q(created_at=anchor.created_at, id__lt=anchor.id)
        )
    rows = list(items.order_by("-created_at", "-id")[: page_size + 1])
    has_more = len(rows) > page_size
    rows = rows[:page_size]
    return {
        "items": [_serialize(item) for item in rows],
        "next_cursor": rows[-1].id if has_more and rows else None,
    }


@router.post(
    "/{task_id}/viewed",
    response={200: InboxTaskOut, 404: InboxErrorOut},
)
def view_inbox_task(request: HttpRequest, task_id: UUID):
    try:
        task = mark_task_viewed(account=request.auth, task_id=task_id)
    except InboxItem.DoesNotExist:
        return Status(404, {"code": "INBOX_TASK_NOT_FOUND", "message": "任务不存在。"})
    return _serialize(task)
