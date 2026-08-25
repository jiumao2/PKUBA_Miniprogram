from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from django.db import IntegrityError, transaction
from django.utils import timezone

from core.models import (
    Account,
    GameScoresheet,
    InboxItem,
    RescheduleRequest,
    ScoresheetRecognitionRun,
    SeasonLeaderBinding,
    TeamConfirmation,
)
from core.services.email_outbox import enqueue_public_mail


def _active_superadmins():
    return Account.objects.filter(
        role=Account.Role.SUPERADMIN,
        is_active=True,
    ).order_by("id")


def _active_scoresheet_reviewers():
    return Account.objects.filter(
        role__in=[Account.Role.ADMIN, Account.Role.SUPERADMIN],
        is_active=True,
    ).order_by("id")


def _create_task(
    *,
    account: Account,
    dedupe_key: str,
    kind: str,
    title: str,
    body: str,
    object_type: str,
    object_id,
    route: str,
    route_params: dict[str, str],
    season_id=None,
    due_at: datetime | None = None,
) -> InboxItem:
    defaults = {
        "season_id": season_id,
        "kind": kind,
        "title": title[:160],
        "body": body,
        "object_type": object_type,
        "object_id": object_id,
        "route": route,
        "route_params": route_params,
        "status": InboxItem.Status.OPEN,
        "due_at": due_at,
    }
    try:
        task, _ = InboxItem.objects.get_or_create(
            account=account,
            dedupe_key=dedupe_key,
            defaults=defaults,
        )
    except IntegrityError:
        task = InboxItem.objects.get(account=account, dedupe_key=dedupe_key)
    return task


def _create_tasks(
    accounts: Iterable[Account],
    *,
    dedupe_key: str,
    kind: str,
    title: str,
    body: str,
    object_type: str,
    object_id,
    route: str,
    route_params: dict[str, str],
    season_id=None,
    due_at: datetime | None = None,
) -> list[InboxItem]:
    return [
        _create_task(
            account=account,
            dedupe_key=dedupe_key,
            kind=kind,
            title=title,
            body=body,
            object_type=object_type,
            object_id=object_id,
            route=route,
            route_params=route_params,
            season_id=season_id,
            due_at=due_at,
        )
        for account in accounts
    ]


def close_tasks(
    *,
    object_type: str,
    object_id,
    reason: str,
    exclude_dedupe_keys: set[str] | None = None,
) -> int:
    now = timezone.now()
    query = InboxItem.objects.filter(
        object_type=object_type,
        object_id=object_id,
        status=InboxItem.Status.OPEN,
    )
    if exclude_dedupe_keys:
        query = query.exclude(dedupe_key__in=exclude_dedupe_keys)
    return query.update(
        status=InboxItem.Status.CLOSED,
        closed_at=now,
        close_reason=reason[:64],
        updated_at=now,
    )


def _reschedule_summary(item: RescheduleRequest) -> tuple[str, str]:
    original = item.original_game_snapshot or {}
    title = f"{item.game.home_display} — {item.game.away_display}"
    body = "\n".join(
        [
            f"{item.game.division.name} · {item.get_request_type_display()}",
            (
                f"原赛程：{original.get('date', '')} "
                f"{original.get('start_time', '')} {original.get('venue_name', '')}"
            ).strip(),
            (
                f"目标：{item.target_date.isoformat()} "
                f"{item.target_start_time.strftime('%H:%M')}"
            ).strip(),
            _target_venue_notice(item),
        ]
    )
    return title, body


def _target_venue_notice(item: RescheduleRequest) -> str:
    if item.status == RescheduleRequest.Status.APPROVED:
        return f"比赛场地：{item.game.venue_name}"
    if item.is_terminal:
        return "申请未生效，预留场地未公开。"
    return "具体场地已由系统内部预留，将在调赛生效并更新正式赛程后公布。"


def _reschedule_anomaly(
    item: RescheduleRequest,
    *,
    code: str,
    message: str,
    notify_staff: bool,
) -> str:
    title, _ = _reschedule_summary(item)
    key = f"reschedule:{item.id}:anomaly:{code}"
    _create_tasks(
        _active_superadmins(),
        dedupe_key=key,
        kind="RESCHEDULE_ANOMALY",
        title=f"调赛异常 · {title}",
        body=message,
        object_type="RescheduleRequest",
        object_id=item.id,
        route=InboxItem.Route.RESCHEDULE_REQUEST,
        route_params={"request_id": str(item.id)},
        season_id=item.game.season_id,
    )
    if notify_staff:
        enqueue_public_mail(
            event_key=key,
            subject=f"[PKUBA] 调赛异常 · {title}",
            body=f"{message}\n\n{_reschedule_email_body(item)}",
            object_type="RescheduleRequest",
            object_id=item.id,
        )
    return key


def _reschedule_email_body(item: RescheduleRequest) -> str:
    original = item.original_game_snapshot or {}
    requested_at = timezone.localtime(item.created_at).strftime("%Y-%m-%d %H:%M")
    return "\n".join(
        [
            (
                f"原比赛：{original.get('date', '')} "
                f"{original.get('start_time', '')} "
                f"{item.game.home_display} — {item.game.away_display} "
                f"{original.get('venue_name', '')}"
            ).strip(),
            (
                f"调整后比赛：{item.target_date.isoformat()} "
                f"{item.target_start_time.strftime('%H:%M')} "
                f"{item.game.home_display} — {item.game.away_display}"
            ).strip(),
            _target_venue_notice(item),
            f"申请日期：{requested_at}",
            f"申请方：{item.requester_team.name}",
            f"申请类型：{item.get_request_type_display()}",
            f"组别：{item.game.division.name}",
            f"当前状态：{item.get_status_display()}",
            f"申请编号：{item.id}",
        ]
    )


def _enqueue_reschedule_status_email(item: RescheduleRequest) -> None:
    state_labels = {
        RescheduleRequest.Status.WAITING_OPPONENT: "协商中",
        RescheduleRequest.Status.WAITING_ADMIN_DECISION: "对手已同意，待审核",
        RescheduleRequest.Status.WAITING_SELECTED_TEAMS: "等待指定球队投票",
        RescheduleRequest.Status.WAITING_ADMIN_FINAL: "投票完成，待终审",
        RescheduleRequest.Status.APPROVED: "已通过",
        RescheduleRequest.Status.REJECTED: "已拒绝",
        RescheduleRequest.Status.WITHDRAWN: "已撤回",
        RescheduleRequest.Status.EXPIRED: "已过期",
        RescheduleRequest.Status.ADMIN_CANCELLED: "管理员已取消",
    }
    title, _ = _reschedule_summary(item)
    enqueue_public_mail(
        event_key=f"reschedule:{item.id}:status:{item.status}",
        subject=(
            f"[PKUBA] {item.get_request_type_display()}调赛申请"
            f"（{state_labels[item.status]}）{title}"
        ),
        body=_reschedule_email_body(item),
        object_type="RescheduleRequest",
        object_id=item.id,
    )


def _leader_for_team(item: RescheduleRequest, team_id):
    binding = (
        SeasonLeaderBinding.objects.select_related("account")
        .filter(
            season_id=item.game.season_id,
            team_id=team_id,
            active=True,
            account__is_active=True,
        )
        .first()
    )
    return binding.account if binding else None


def sync_reschedule_tasks(
    item: RescheduleRequest,
    *,
    notify_staff: bool = True,
) -> list[str]:
    """Synchronize actionable tasks for the request's current authoritative state."""

    item = (
        RescheduleRequest.objects.select_related(
            "game__season",
            "game__division",
            "game__home_team",
            "game__away_team",
            "requester_team",
        )
        .prefetch_related("confirmations__team")
        .get(id=item.id)
    )
    keep: set[str] = set()
    anomalies: list[str] = []
    title, body = _reschedule_summary(item)

    if item.status == RescheduleRequest.Status.WAITING_OPPONENT:
        confirmation = item.confirmations.filter(
            purpose=TeamConfirmation.Purpose.OPPONENT,
            response=TeamConfirmation.Response.PENDING,
        ).first()
        leader = _leader_for_team(item, confirmation.team_id) if confirmation else None
        if leader and confirmation:
            key = f"reschedule:{item.id}:opponent:{confirmation.team_id}"
            keep.add(key)
            _create_task(
                account=leader,
                dedupe_key=key,
                kind="RESCHEDULE_OPPONENT_CONFIRMATION",
                title=f"请确认调赛 · {title}",
                body=body,
                object_type="RescheduleRequest",
                object_id=item.id,
                route=InboxItem.Route.RESCHEDULE_REQUEST,
                route_params={"request_id": str(item.id)},
                season_id=item.game.season_id,
                due_at=item.confirmation_deadline,
            )
        else:
            anomalies.append(
                _reschedule_anomaly(
                    item,
                    code="OPPONENT_LEADER_MISSING",
                    message="对手球队没有有效领队，无法投递确认任务，请超级管理员处理。",
                    notify_staff=notify_staff,
                )
            )

    elif item.status == RescheduleRequest.Status.WAITING_ADMIN_DECISION:
        key = f"reschedule:{item.id}:admin-decision"
        keep.add(key)
        _create_tasks(
            _active_superadmins(),
            dedupe_key=key,
            kind="RESCHEDULE_ADMIN_DECISION",
            title=f"跨周调赛待审核 · {title}",
            body=body,
            object_type="RescheduleRequest",
            object_id=item.id,
            route=InboxItem.Route.RESCHEDULE_REQUEST,
            route_params={"request_id": str(item.id)},
            season_id=item.game.season_id,
            due_at=item.confirmation_deadline,
        )

    elif item.status == RescheduleRequest.Status.WAITING_SELECTED_TEAMS:
        for confirmation in item.confirmations.filter(
            purpose=TeamConfirmation.Purpose.VOTER,
            response=TeamConfirmation.Response.PENDING,
        ):
            leader = _leader_for_team(item, confirmation.team_id)
            if leader:
                key = f"reschedule:{item.id}:voter:{confirmation.team_id}"
                keep.add(key)
                _create_task(
                    account=leader,
                    dedupe_key=key,
                    kind="RESCHEDULE_VOTE",
                    title=f"请参与调赛投票 · {title}",
                    body=body,
                    object_type="RescheduleRequest",
                    object_id=item.id,
                    route=InboxItem.Route.RESCHEDULE_REQUEST,
                    route_params={"request_id": str(item.id)},
                    season_id=item.game.season_id,
                    due_at=item.confirmation_deadline,
                )
            else:
                anomalies.append(
                    _reschedule_anomaly(
                        item,
                        code=f"VOTER_LEADER_MISSING:{confirmation.team_id}",
                        message=(
                            f"指定投票球队 {confirmation.team.name} 没有有效领队，"
                            "无法投递投票任务，请超级管理员处理。"
                        ),
                        notify_staff=notify_staff,
                    )
                )

    elif item.status == RescheduleRequest.Status.WAITING_ADMIN_FINAL:
        key = f"reschedule:{item.id}:admin-final"
        keep.add(key)
        _create_tasks(
            _active_superadmins(),
            dedupe_key=key,
            kind="RESCHEDULE_ADMIN_FINAL",
            title=f"跨周调赛待终审 · {title}",
            body=body,
            object_type="RescheduleRequest",
            object_id=item.id,
            route=InboxItem.Route.RESCHEDULE_REQUEST,
            route_params={"request_id": str(item.id)},
            season_id=item.game.season_id,
        )

    keep.update(anomalies)
    close_tasks(
        object_type="RescheduleRequest",
        object_id=item.id,
        reason=item.status,
        exclude_dedupe_keys=keep,
    )
    if notify_staff:
        _enqueue_reschedule_status_email(item)
    return anomalies


def sync_scoresheet_recognition_tasks(
    scoresheet: GameScoresheet,
    run: ScoresheetRecognitionRun,
    *,
    notify_staff: bool = True,
) -> str | None:
    scoresheet = GameScoresheet.objects.select_related(
        "game__season",
        "game__division",
        "game__home_team",
        "game__away_team",
    ).get(id=scoresheet.id)
    review_key = (
        f"scoresheet:{scoresheet.id}:source:{run.source_version}:"
        f"cycle:{run.cycle}:review"
    )
    keep: set[str] = set()
    anomaly_key: str | None = None
    game_label = f"{scoresheet.game.home_display} — {scoresheet.game.away_display}"

    if (
        run.status == ScoresheetRecognitionRun.Status.SUCCEEDED
        and scoresheet.source_version == run.source_version
        and scoresheet.current_publication_id is None
    ):
        keep.add(review_key)
        _create_tasks(
            _active_scoresheet_reviewers(),
            dedupe_key=review_key,
            kind="SCORESHEET_REVIEW",
            title=f"记录表待核对 · {game_label}",
            body=(
                f"{scoresheet.game.division.name} · {scoresheet.game.date.isoformat()} "
                f"{scoresheet.game.start_time.strftime('%H:%M')}\n"
                "AI 图片识别已经完成，请核对草稿并完成服务端校验后发布。"
            ),
            object_type="GameScoresheet",
            object_id=scoresheet.id,
            route=InboxItem.Route.SCORESHEET,
            route_params={"scoresheet_id": str(scoresheet.id)},
            season_id=scoresheet.game.season_id,
        )
    elif (
        run.status == ScoresheetRecognitionRun.Status.FAILED
        and scoresheet.source_version == run.source_version
    ):
        anomaly_key = (
            f"scoresheet:{scoresheet.id}:source:{run.source_version}:"
            f"cycle:{run.cycle}:failed"
        )
        keep.add(anomaly_key)
        _create_tasks(
            _active_superadmins(),
            dedupe_key=anomaly_key,
            kind="SCORESHEET_RECOGNITION_FAILED",
            title=f"记录表识别异常 · {game_label}",
            body=run.last_error or "记录表 AI 识别达到最终失败状态。",
            object_type="GameScoresheet",
            object_id=scoresheet.id,
            route=InboxItem.Route.SCORESHEET,
            route_params={"scoresheet_id": str(scoresheet.id)},
            season_id=scoresheet.game.season_id,
        )
        if notify_staff:
            enqueue_public_mail(
                event_key=anomaly_key,
                subject=f"[PKUBA] 记录表识别异常 · {game_label}",
                body=(
                    f"{scoresheet.game.division.name} · "
                    f"{scoresheet.game.date.isoformat()} "
                    f"{scoresheet.game.start_time.strftime('%H:%M')}\n"
                    f"{run.last_error or '记录表 AI 识别达到最终失败状态。'}\n"
                    f"记录表编号：{scoresheet.id}"
                ),
                object_type="GameScoresheet",
                object_id=scoresheet.id,
            )

    close_tasks(
        object_type="GameScoresheet",
        object_id=scoresheet.id,
        reason=f"RECOGNITION_{run.status}",
        exclude_dedupe_keys=keep,
    )
    return anomaly_key


def close_scoresheet_tasks(scoresheet_id, *, reason: str) -> int:
    return close_tasks(
        object_type="GameScoresheet",
        object_id=scoresheet_id,
        reason=reason,
    )


def create_email_failure_tasks(*, outbox_id, subject: str, attempts: int) -> None:
    _create_tasks(
        _active_superadmins(),
        dedupe_key=f"email:{outbox_id}:failed",
        kind="EMAIL_DELIVERY_FAILED",
        title="公邮通知发送失败",
        body=f"邮件主题：{subject}\n已尝试 {attempts} 次，请检查 SMTP 配置和公邮授权码。",
        object_type="EmailOutbox",
        object_id=outbox_id,
        route=InboxItem.Route.ADMIN_WORKSPACE,
        route_params={},
    )


@transaction.atomic
def mark_task_viewed(*, account: Account, task_id) -> InboxItem:
    task = InboxItem.objects.select_for_update().filter(id=task_id, account=account).first()
    if task is None:
        raise InboxItem.DoesNotExist
    if task.read_at is None:
        task.read_at = timezone.now()
        task.save(update_fields=["read_at", "updated_at"])
    return task
