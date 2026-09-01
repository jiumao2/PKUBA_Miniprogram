from __future__ import annotations

from typing import Any
from uuid import UUID

from django.core.serializers.json import DjangoJSONEncoder
from django.http import HttpRequest, JsonResponse
from ninja import Router, Schema
from pydantic import Field

from core.api_security import superadmin_session_auth
from core.services.advanced_data import (
    AdvancedDataError,
    apply_mutation,
    get_record,
    get_spec,
    list_records,
    model_catalog,
    preview_mutation,
)

router = Router(tags=["admin-advanced-data"], auth=superadmin_session_auth)


class AdvancedDataErrorOut(Schema):
    code: str
    message: str


class AdvancedFieldOut(Schema):
    name: str
    type: str
    relation: bool
    nullable: bool
    sensitive: bool


class AdvancedModelOut(Schema):
    key: str
    label: str
    model_name: str
    mutation_mode: str
    immutable: bool
    fields: list[AdvancedFieldOut]


class AdvancedRecordOut(Schema):
    id: str
    model: str
    values: dict[str, Any]


class AdvancedRecordListOut(Schema):
    model: str
    label: str
    mutation_mode: str
    total: int
    offset: int
    limit: int
    search: str
    sort: str
    direction: str
    items: list[AdvancedRecordOut]


class AdvancedMutationIn(Schema):
    operation: str
    object_id: UUID | None = None
    expected_version: int | None = None
    values: dict[str, Any] = Field(default_factory=dict)


class AdvancedMutationApplyIn(AdvancedMutationIn):
    impact_hash: str
    confirmed: bool


class AdvancedBlockerOut(Schema):
    code: str
    message: str
    count: int


class AdvancedMutationPreviewOut(Schema):
    model: str
    operation: str
    object_id: str | None
    expected_version: int | None
    before: AdvancedRecordOut | None
    after: AdvancedRecordOut | None
    references: dict[str, int]
    blockers: list[AdvancedBlockerOut]
    can_apply: bool
    requires_confirmation: bool
    impact_hash: str


def _response(data: object, *, status: int = 200) -> JsonResponse:
    response = JsonResponse(
        data,
        safe=not isinstance(data, list),
        status=status,
        encoder=DjangoJSONEncoder,
        json_dumps_params={"ensure_ascii": False},
    )
    response["Cache-Control"] = "no-store, max-age=0"
    response["Pragma"] = "no-cache"
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response


_PUBLIC_ERROR_MESSAGES = {
    "MODEL_NOT_FOUND": "高级数据模型不存在。",
    "RECORD_NOT_FOUND": "记录不存在。",
    "SORT_DIRECTION_INVALID": "排序方向无效。",
    "SORT_FIELD_INVALID": "排序字段不存在。",
    "FIELD_NOT_EDITABLE": "提交包含不能直接修改的字段。",
    "RELATED_RECORD_NOT_FOUND": "关联记录不存在。",
    "FIELD_INVALID": "字段值无效。",
    "DOMAIN_SERVICE_REQUIRED": "该模型只能通过对应业务页面和事务服务修改。",
    "OPERATION_INVALID": "高级数据操作类型无效。",
    "OBJECT_ID_REQUIRED": "修改或删除必须指定记录 ID。",
    "VERSION_CONFLICT": "记录已变化，请刷新后重试。",
    "SEASON_SCOPE_REQUIRED": "无法确定该记录所属赛季。",
    "CONFIRMATION_REQUIRED": "高级数据修改必须二次确认。",
    "IMPACT_HASH_MISMATCH": "记录影响已变化，请重新预览。",
    "MUTATION_BLOCKED": "高级数据修改存在阻塞项。",
    "SEASON_ARCHIVED": "已归档赛季只读。",
    "VALIDATION_FAILED": "提交的数据未通过校验。",
}
_DEFAULT_PUBLIC_ERROR_MESSAGE = "高级数据请求无效。"


def _error(error: AdvancedDataError) -> JsonResponse:
    if error.code in {"MODEL_NOT_FOUND", "RECORD_NOT_FOUND"}:
        status = 404
    elif error.code in {
        "VERSION_CONFLICT",
        "IMPACT_HASH_MISMATCH",
        "MUTATION_BLOCKED",
        "SEASON_ARCHIVED",
    }:
        status = 409
    else:
        status = 400
    return _response(
        {
            "code": error.code,
            "message": _PUBLIC_ERROR_MESSAGES.get(
                error.code,
                _DEFAULT_PUBLIC_ERROR_MESSAGE,
            ),
        },
        status=status,
    )


@router.get(
    "/advanced-data/models",
    response={200: list[AdvancedModelOut], 400: AdvancedDataErrorOut},
)
def list_advanced_models(request: HttpRequest):
    del request
    return _response(model_catalog())


@router.post(
    "/advanced-data/{model_key}/mutations/preview",
    response={
        200: AdvancedMutationPreviewOut,
        400: AdvancedDataErrorOut,
        404: AdvancedDataErrorOut,
        409: AdvancedDataErrorOut,
    },
)
def preview_advanced_mutation(
    request: HttpRequest,
    model_key: str,
    payload: AdvancedMutationIn,
):
    del request
    try:
        return _response(
            preview_mutation(
                spec=get_spec(model_key),
                operation=payload.operation,
                object_id=payload.object_id,
                expected_version=payload.expected_version,
                values=payload.values,
            )
        )
    except AdvancedDataError as error:
        return _error(error)


@router.post(
    "/advanced-data/{model_key}/mutations/apply",
    response={
        200: AdvancedRecordOut,
        400: AdvancedDataErrorOut,
        404: AdvancedDataErrorOut,
        409: AdvancedDataErrorOut,
    },
)
def apply_advanced_mutation(
    request: HttpRequest,
    model_key: str,
    payload: AdvancedMutationApplyIn,
):
    try:
        return _response(
            apply_mutation(
                actor=request.auth,
                spec=get_spec(model_key),
                operation=payload.operation,
                object_id=payload.object_id,
                expected_version=payload.expected_version,
                values=payload.values,
                impact_hash=payload.impact_hash,
                confirmed=payload.confirmed,
            )
        )
    except AdvancedDataError as error:
        return _error(error)


@router.get(
    "/advanced-data/{model_key}",
    response={
        200: AdvancedRecordListOut,
        400: AdvancedDataErrorOut,
        404: AdvancedDataErrorOut,
    },
)
def list_advanced_records(
    request: HttpRequest,
    model_key: str,
    offset: int = 0,
    limit: int = 50,
    search: str = "",
    sort: str = "",
    direction: str = "desc",
):
    del request
    try:
        if direction not in {"asc", "desc"}:
            raise AdvancedDataError("SORT_DIRECTION_INVALID", "排序方向无效。")
        return _response(
            list_records(
                get_spec(model_key),
                offset=offset,
                limit=limit,
                search=search,
                sort=sort,
                direction=direction,
            )
        )
    except AdvancedDataError as error:
        return _error(error)


@router.get(
    "/advanced-data/{model_key}/{object_id}",
    response={
        200: AdvancedRecordOut,
        400: AdvancedDataErrorOut,
        404: AdvancedDataErrorOut,
    },
)
def get_advanced_record(
    request: HttpRequest,
    model_key: str,
    object_id: UUID,
):
    del request
    try:
        return _response(get_record(get_spec(model_key), object_id))
    except AdvancedDataError as error:
        return _error(error)
