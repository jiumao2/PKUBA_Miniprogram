from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from uuid import UUID

from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import IntegrityError, models, transaction

from core import models as core_models


class AdvancedDataError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class ModelSpec:
    key: str
    model: type[models.Model]
    label: str
    mutation_fields: tuple[str, ...] = ()
    immutable: bool = False

    @property
    def mutation_mode(self) -> str:
        return "VALIDATED_MASTER" if self.mutation_fields else "READ_ONLY"


MODEL_SPECS = (
    ModelSpec("accounts", core_models.Account, "账号", immutable=True),
    ModelSpec("wechat-identities", core_models.WeChatIdentity, "微信身份", immutable=True),
    ModelSpec("admin-profiles", core_models.AdminProfile, "管理员资料", immutable=True),
    ModelSpec("wechat-auth-tickets", core_models.WeChatAuthTicket, "微信注册票据", immutable=True),
    ModelSpec("miniapp-sessions", core_models.MiniAppSession, "小程序会话", immutable=True),
    ModelSpec("seasons", core_models.Season, "赛季"),
    ModelSpec("divisions", core_models.Division, "组别"),
    ModelSpec(
        "competition-groups",
        core_models.CompetitionGroup,
        "小组",
        ("division", "code", "name", "sort_order"),
    ),
    ModelSpec(
        "participant-slots",
        core_models.ParticipantSlot,
        "参赛签位",
        ("division", "group", "code", "label", "seed"),
    ),
    ModelSpec("teams", core_models.Team, "球队"),
    ModelSpec("draw-assignments", core_models.DrawAssignment, "抽签映射"),
    ModelSpec("roster-players", core_models.RosterPlayer, "名单球员"),
    ModelSpec("leader-bindings", core_models.SeasonLeaderBinding, "领队绑定"),
    ModelSpec(
        "web-login-challenges",
        core_models.WebLoginChallenge,
        "网页扫码挑战",
        immutable=True,
    ),
    ModelSpec("venues", core_models.Venue, "标准场地"),
    ModelSpec("periods", core_models.Period, "标准时段"),
    ModelSpec("period-capacities", core_models.PeriodCapacity, "时段容量"),
    ModelSpec(
        "date-capacity-overrides",
        core_models.DatePeriodCapacityOverride,
        "特殊日期容量",
    ),
    ModelSpec("games", core_models.Game, "比赛"),
    ModelSpec("schedule-slot-families", core_models.ScheduleSlotFamily, "签位方案"),
    ModelSpec("schedule-grid-columns", core_models.ScheduleGridColumn, "赛程网格列"),
    ModelSpec("schedule-grid-drafts", core_models.ScheduleGridDraft, "赛程草稿"),
    ModelSpec("schedule-grid-draft-columns", core_models.ScheduleGridDraftColumn, "赛程草稿列"),
    ModelSpec("schedule-grid-draft-cells", core_models.ScheduleGridDraftCell, "赛程草稿单元格"),
    ModelSpec("schedule-slot-locks", core_models.ScheduleSlotLock, "时段串行锁", immutable=True),
    ModelSpec("slot-reservations", core_models.SlotReservation, "场地预留"),
    ModelSpec("reschedule-requests", core_models.RescheduleRequest, "调赛申请"),
    ModelSpec("team-confirmations", core_models.TeamConfirmation, "球队确认"),
    ModelSpec("schedule-import-batches", core_models.ScheduleImportBatch, "赛程导入批次"),
    ModelSpec("import-issues", core_models.ImportIssue, "赛程导入问题"),
    ModelSpec("roster-import-batches", core_models.RosterImportBatch, "名单导入批次"),
    ModelSpec("roster-import-issues", core_models.RosterImportIssue, "名单导入问题"),
    ModelSpec("game-media-assets", core_models.GameMediaAsset, "比赛图片"),
    ModelSpec("game-scoresheets", core_models.GameScoresheet, "记录表工作区"),
    ModelSpec("scoresheet-revisions", core_models.ScoresheetRevision, "记录表修订", immutable=True),
    ModelSpec(
        "scoresheet-recognition-runs",
        core_models.ScoresheetRecognitionRun,
        "记录表识别任务",
    ),
    ModelSpec(
        "scoresheet-change-logs",
        core_models.ScoresheetChangeLog,
        "记录表修改日志",
        immutable=True,
    ),
    ModelSpec(
        "scoresheet-publications",
        core_models.ScoresheetPublication,
        "记录表发布",
        immutable=True,
    ),
    ModelSpec("game-team-stats", core_models.GameTeamStat, "球队单场统计", immutable=True),
    ModelSpec("game-player-stats", core_models.GamePlayerStat, "球员单场统计", immutable=True),
    ModelSpec("scoresheet-edit-leases", core_models.ScoresheetEditLease, "记录表编辑租约"),
    ModelSpec("inbox-items", core_models.InboxItem, "任务箱项目"),
    ModelSpec("email-outbox", core_models.EmailOutbox, "邮件发件箱"),
    ModelSpec(
        "api-idempotency-records",
        core_models.ApiIdempotencyRecord,
        "接口幂等记录",
        immutable=True,
    ),
    ModelSpec("archive-jobs", core_models.ArchiveJob, "归档任务", immutable=True),
    ModelSpec("media-purge-jobs", core_models.MediaPurgeJob, "照片清理任务", immutable=True),
    ModelSpec("admin-audit-logs", core_models.AdminAuditLog, "管理员审计日志", immutable=True),
)

REGISTRY = {spec.key: spec for spec in MODEL_SPECS}
SENSITIVE_FIELD_MARKERS = ("password", "openid", "token", "hash", "secret")


def get_spec(key: str) -> ModelSpec:
    try:
        return REGISTRY[key]
    except KeyError as error:
        raise AdvancedDataError("MODEL_NOT_FOUND", "高级数据模型不存在。") from error


def _field_metadata(field: models.Field) -> dict[str, object]:
    name = field.name
    return {
        "name": name,
        "type": field.get_internal_type(),
        "relation": bool(field.is_relation),
        "nullable": bool(getattr(field, "null", False)),
        "sensitive": any(marker in name.casefold() for marker in SENSITIVE_FIELD_MARKERS),
    }


def model_catalog() -> list[dict[str, object]]:
    return [
        {
            "key": spec.key,
            "label": spec.label,
            "model_name": spec.model.__name__,
            "mutation_mode": spec.mutation_mode,
            "immutable": spec.immutable,
            "fields": [_field_metadata(field) for field in spec.model._meta.concrete_fields],
        }
        for spec in MODEL_SPECS
    ]


def serialize_instance(instance: models.Model) -> dict[str, object]:
    values: dict[str, object] = {}
    for field in instance._meta.concrete_fields:
        value = getattr(instance, field.attname)
        if isinstance(value, UUID):
            value = str(value)
        values[field.name] = value
    return {
        "id": str(instance.pk),
        "model": instance._meta.model_name,
        "values": values,
    }


def list_records(spec: ModelSpec, *, offset: int, limit: int) -> dict[str, object]:
    offset = max(0, offset)
    limit = min(100, max(1, limit))
    query = spec.model.objects.all()
    field_names = {field.name for field in spec.model._meta.concrete_fields}
    if "created_at" in field_names:
        query = query.order_by("-created_at")
    else:
        query = query.order_by(spec.model._meta.pk.name)
    total = query.count()
    return {
        "model": spec.key,
        "label": spec.label,
        "mutation_mode": spec.mutation_mode,
        "total": total,
        "offset": offset,
        "limit": limit,
        "items": [serialize_instance(item) for item in query[offset : offset + limit]],
    }


def get_record(spec: ModelSpec, object_id: UUID) -> dict[str, object]:
    instance = spec.model.objects.filter(pk=object_id).first()
    if instance is None:
        raise AdvancedDataError("RECORD_NOT_FOUND", "记录不存在。")
    return serialize_instance(instance)


def _season_for(instance: models.Model):
    if isinstance(instance, core_models.Season):
        return instance
    if hasattr(instance, "season_id"):
        return instance.season
    if isinstance(instance, (core_models.CompetitionGroup, core_models.ParticipantSlot)):
        return instance.division.season
    return None


def _apply_values(instance: models.Model, spec: ModelSpec, values: dict[str, object]) -> None:
    unknown = set(values) - set(spec.mutation_fields)
    if unknown:
        raise AdvancedDataError(
            "FIELD_NOT_EDITABLE",
            f"以下字段不能在高级数据页直接修改：{', '.join(sorted(unknown))}",
        )
    for name, raw in values.items():
        field = instance._meta.get_field(name)
        if isinstance(field, models.ForeignKey):
            if raw in {None, ""} and field.null:
                setattr(instance, name, None)
                continue
            related = field.remote_field.model.objects.filter(pk=raw).first()
            if related is None:
                raise AdvancedDataError("RELATED_RECORD_NOT_FOUND", f"{name} 关联记录不存在。")
            setattr(instance, name, related)
        else:
            try:
                setattr(instance, name, field.to_python(raw))
            except (TypeError, ValueError, ValidationError) as error:
                raise AdvancedDataError("FIELD_INVALID", f"{name} 的值无效。") from error


def _references(instance: models.Model) -> dict[str, int]:
    result: dict[str, int] = {}
    for relation in instance._meta.related_objects:
        accessor = relation.get_accessor_name()
        try:
            related = getattr(instance, accessor)
            count = related.count() if hasattr(related, "count") else 1
        except ObjectDoesNotExist:
            count = 0
        if count:
            result[accessor] = count
    return result


def _canonical_preview(
    *,
    spec: ModelSpec,
    operation: str,
    object_id: UUID | None,
    expected_version: int | None,
    values: dict[str, object],
    lock: bool,
) -> tuple[dict[str, object], models.Model | None]:
    if not spec.mutation_fields:
        raise AdvancedDataError(
            "DOMAIN_SERVICE_REQUIRED",
            "该模型只能通过对应业务页面和事务服务修改。",
        )
    if operation not in {"CREATE", "UPDATE", "DELETE"}:
        raise AdvancedDataError("OPERATION_INVALID", "高级数据操作类型无效。")
    if operation == "CREATE":
        instance = spec.model()
        before = None
    else:
        if object_id is None:
            raise AdvancedDataError("OBJECT_ID_REQUIRED", "修改或删除必须指定记录 ID。")
        query = spec.model.objects
        if lock:
            query = query.select_for_update()
        instance = query.filter(pk=object_id).first()
        if instance is None:
            raise AdvancedDataError("RECORD_NOT_FOUND", "记录不存在。")
        if hasattr(instance, "version") and expected_version is not None:
            if instance.version != expected_version:
                raise AdvancedDataError("VERSION_CONFLICT", "记录已变化，请刷新后重试。")
        before = serialize_instance(instance)
    if operation != "DELETE":
        _apply_values(instance, spec, values)
    season = _season_for(instance)
    if season is None:
        raise AdvancedDataError("SEASON_SCOPE_REQUIRED", "无法确定该记录所属赛季。")
    blockers: list[dict[str, object]] = []
    if season.status == core_models.Season.Status.ARCHIVED:
        blockers.append(
            {"code": "SEASON_ARCHIVED", "message": "已归档赛季只读。", "count": 1}
        )
    elif season.status != core_models.Season.Status.SETUP:
        blockers.append(
            {
                "code": "SETUP_ONLY",
                "message": "高级数据页只允许修改准备期且无引用的普通主数据。",
                "count": 1,
            }
        )
    references = _references(instance) if operation == "DELETE" and instance.pk else {}
    if references:
        blockers.append(
            {
                "code": "RECORD_IN_USE",
                "message": "记录已有引用，不能物理删除。",
                "count": sum(references.values()),
            }
        )
    after = None if operation == "DELETE" else serialize_instance(instance)
    canonical = {
        "model": spec.key,
        "operation": operation,
        "object_id": str(object_id) if object_id else None,
        "expected_version": expected_version,
        "before": before,
        "after": after,
        "references": references,
        "blockers": blockers,
    }
    hash_canonical = canonical
    if operation == "CREATE" and after is not None:
        generated_values = {**after["values"], spec.model._meta.pk.name: None}
        hash_canonical = {
            **canonical,
            "after": {**after, "id": None, "values": generated_values},
        }
    impact_hash = hashlib.sha256(
        json.dumps(hash_canonical, cls=DjangoJSONEncoder, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return (
        {
            **canonical,
            "can_apply": not blockers,
            "requires_confirmation": True,
            "impact_hash": impact_hash,
        },
        instance,
    )


def preview_mutation(
    *,
    spec: ModelSpec,
    operation: str,
    object_id: UUID | None,
    expected_version: int | None,
    values: dict[str, object],
) -> dict[str, object]:
    preview, _ = _canonical_preview(
        spec=spec,
        operation=operation,
        object_id=object_id,
        expected_version=expected_version,
        values=values,
        lock=False,
    )
    return preview


@transaction.atomic
def apply_mutation(
    *,
    actor: core_models.Account,
    spec: ModelSpec,
    operation: str,
    object_id: UUID | None,
    expected_version: int | None,
    values: dict[str, object],
    impact_hash: str,
    confirmed: bool,
) -> dict[str, object]:
    if not confirmed:
        raise AdvancedDataError("CONFIRMATION_REQUIRED", "高级数据修改必须二次确认。")
    preview, instance = _canonical_preview(
        spec=spec,
        operation=operation,
        object_id=object_id,
        expected_version=expected_version,
        values=values,
        lock=True,
    )
    if preview["impact_hash"] != impact_hash:
        raise AdvancedDataError("IMPACT_HASH_MISMATCH", "记录影响已变化，请重新预览。")
    if preview["blockers"]:
        raise AdvancedDataError("MUTATION_BLOCKED", "高级数据修改存在阻塞项。")
    try:
        if operation == "DELETE":
            result = preview["before"]
            instance.delete()
        else:
            instance.full_clean()
            if hasattr(instance, "version") and instance.pk:
                instance.version += 1
            instance.save()
            result = serialize_instance(instance)
    except (IntegrityError, ValidationError) as error:
        raise AdvancedDataError("VALIDATION_FAILED", str(error)) from error
    core_models.AdminAuditLog.objects.create(
        actor=actor,
        action=f"ADVANCED_DATA_{operation}",
        object_type=spec.model.__name__,
        object_id=(instance.pk if instance.pk else object_id),
        before=(
            {}
            if preview["before"] is None
            else json.loads(json.dumps(preview["before"], cls=DjangoJSONEncoder))
        ),
        after=(
            {}
            if operation == "DELETE"
            else json.loads(json.dumps(result, cls=DjangoJSONEncoder))
        ),
        metadata={
            "model_key": spec.key,
            "impact_hash": impact_hash,
            "advanced_data": True,
        },
    )
    return result
