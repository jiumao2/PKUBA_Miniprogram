from __future__ import annotations

import base64
import io
import json
import uuid
from dataclasses import dataclass
from datetime import timedelta
from email.utils import parsedate_to_datetime
from urllib import error, request

from django.conf import settings
from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from PIL import Image, ImageOps, UnidentifiedImageError

from core.models import (
    GameScoresheet,
    ScoresheetRecognitionRun,
    ScoresheetRevision,
)
from core.scoresheet_schema import merge_recognition_result
from core.services.scoresheets import _event_locked, _revision_locked

RETRY_DELAYS = (30, 120, 600)
WORKER_LEASE_SECONDS = 5 * 60


class RecognitionAttemptError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool,
        retry_after_seconds: int | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds


@dataclass(frozen=True)
class ClaimedRun:
    run_id: uuid.UUID
    worker_token: uuid.UUID


def _retry_after(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return max(0, int(value))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value)
            return max(0, int((parsed - timezone.now()).total_seconds()))
        except (TypeError, ValueError, OverflowError):
            return None


def claim_next_run(worker_name: str) -> ClaimedRun | None:
    now = timezone.now()
    with transaction.atomic():
        run = (
            ScoresheetRecognitionRun.objects.select_for_update(skip_locked=True)
            .filter(attempt_count__lt=4)
            .filter(
                Q(status=ScoresheetRecognitionRun.Status.QUEUED)
                | Q(
                    status=ScoresheetRecognitionRun.Status.RETRY_WAIT,
                    next_attempt_at__lte=now,
                )
                | Q(
                    status=ScoresheetRecognitionRun.Status.RUNNING,
                    worker_lease_expires_at__lte=now,
                )
            )
            .select_related("scoresheet")
            .order_by("next_attempt_at", "created_at")
            .first()
        )
        if run is None:
            return None
        scoresheet = GameScoresheet.objects.select_for_update().get(id=run.scoresheet_id)
        if (
            scoresheet.source_version != run.source_version
            or scoresheet.source_asset_id != run.source_asset_id
        ):
            run.status = ScoresheetRecognitionRun.Status.SUPERSEDED
            run.finished_at = now
            run.save(update_fields=["status", "finished_at", "updated_at"])
            return None
        token = uuid.uuid4()
        run.status = ScoresheetRecognitionRun.Status.RUNNING
        run.attempt_count += 1
        run.next_attempt_at = None
        run.worker_lease_token = token
        run.worker_lease_owner = worker_name[:96]
        run.worker_lease_expires_at = now + timedelta(seconds=WORKER_LEASE_SECONDS)
        run.save(
            update_fields=[
                "status",
                "attempt_count",
                "next_attempt_at",
                "worker_lease_token",
                "worker_lease_owner",
                "worker_lease_expires_at",
                "updated_at",
            ]
        )
        scoresheet.status = GameScoresheet.Status.RECOGNIZING
        scoresheet.save(update_fields=["status", "updated_at"])
        _event_locked(
            scoresheet,
            "RECOGNITION_ATTEMPT_STARTED",
            payload={
                "run_id": str(run.id),
                "attempt": run.attempt_count,
                "max_attempts": run.max_attempts,
            },
        )
        return ClaimedRun(run_id=run.id, worker_token=token)


def _safe_source_image(run: ScoresheetRecognitionRun) -> bytes:
    if run.source_asset.deleted_at or not default_storage.exists(run.source_asset.file_key):
        raise RecognitionAttemptError(
            "SOURCE_MISSING", "记录表原图已被替换或删除。", retryable=False
        )
    try:
        with default_storage.open(run.source_asset.file_key, "rb") as source:
            raw = source.read(20 * 1024 * 1024 + 1)
        if not raw or len(raw) > 20 * 1024 * 1024:
            raise RecognitionAttemptError(
                "IMAGE_INVALID", "记录表图片为空或超过安全大小。", retryable=False
            )
        with Image.open(io.BytesIO(raw)) as opened:
            image = ImageOps.exif_transpose(opened).convert("RGB")
            image.thumbnail((2600, 2600), Image.Resampling.LANCZOS)
            output = io.BytesIO()
            image.save(output, format="JPEG", quality=90, optimize=True)
            return output.getvalue()
    except RecognitionAttemptError:
        raise
    except (Image.DecompressionBombError, UnidentifiedImageError, OSError, ValueError) as exc:
        raise RecognitionAttemptError(
            "IMAGE_INVALID", "记录表图片无法安全预处理。", retryable=False
        ) from exc


def _provider_prior(scoresheet: GameScoresheet) -> dict[str, object]:
    prior = scoresheet.game_prior_snapshot
    roster = scoresheet.roster_snapshot
    if not roster.get("A") or not roster.get("B"):
        raise RecognitionAttemptError(
            "ROSTER_MISSING", "双方冻结名单不完整。", retryable=False
        )
    return {
        "teams": {
            side: {
                "name": prior.get("team_a" if side == "A" else "team_b", {}).get(
                    "display_name"
                ),
                "players": [
                    {"name": row.get("display_name")}
                    for row in roster.get(side, [])
                ],
            }
            for side in ("A", "B")
        },
    }


def _prompt(prior: dict[str, object]) -> str:
    return (
        "你是篮球记录表结构化识别器。读取完整的一页北京大学篮协记录表图片，"
        "只返回一个 JSON 对象，不要 Markdown。采用 FIBA 2024 记号。不要猜测看不清的内容，"
        "空白使用空字符串、false、空数组或 null。逐次得分保留纸面累计分，不要用计算值覆盖。\n"
        "JSON 顶层仅含 game、teams、running_score、summary、officials。"
        "teams 必含 A/B；球员只返回 name、jersey_number、appeared、starter、captain、fouls。"
        "running_score 每项包含 team(A/B)、player_name、player_number、value(1/2/3)、"
        "period(1/2/3/4/OT)、cumulative、mark(dot/slash/circle)、boundary(none/period/game)。"
        "summary.period_scores 使用 1/2/3/4/OT 键，每项含 A/B；final_score 含 A/B。"
        "officials 包含 scorer、assistant_scorer、timer、shot_clock_operator 及三个签名布尔值。\n"
        "已知球队与球员姓名如下；这些是唯一允许使用的人名与队名：\n"
        + json.dumps(prior, ensure_ascii=False, separators=(",", ":"))
    )


def _extract_content(payload: dict[str, object]) -> str:
    try:
        content = payload["choices"][0]["message"]["content"]  # type: ignore[index]
    except (KeyError, IndexError, TypeError) as exc:
        raise RecognitionAttemptError(
            "PROVIDER_SCHEMA_INVALID", "识别服务响应缺少 choices.message.content。", retryable=True
        ) from exc
    if isinstance(content, list):
        content = "".join(
            str(row.get("text", "")) for row in content if isinstance(row, dict)
        )
    if not isinstance(content, str):
        raise RecognitionAttemptError(
            "PROVIDER_SCHEMA_INVALID", "识别服务响应内容格式无效。", retryable=True
        )
    stripped = content.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        stripped = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    return stripped


def _validate_result_shape(result: object) -> dict[str, object]:
    if not isinstance(result, dict):
        raise RecognitionAttemptError(
            "RESULT_SCHEMA_INVALID", "识别结果必须是 JSON 对象。", retryable=True
        )
    required = {"game", "teams", "running_score", "summary", "officials"}
    if not required.issubset(result):
        raise RecognitionAttemptError(
            "RESULT_SCHEMA_INVALID", "识别结果缺少完整记录表区域。", retryable=True
        )
    if not isinstance(result.get("teams"), dict) or not isinstance(
        result.get("running_score"), list
    ):
        raise RecognitionAttemptError(
            "RESULT_SCHEMA_INVALID", "球队或逐次得分区域格式无效。", retryable=True
        )
    for event in result["running_score"]:  # type: ignore[index]
        if not isinstance(event, dict):
            raise RecognitionAttemptError(
                "RESULT_SCHEMA_INVALID", "逐次得分事件格式无效。", retryable=True
            )
    return result


def call_qwen(run: ScoresheetRecognitionRun) -> tuple[dict[str, object], dict[str, object]]:
    api_key = settings.QWEN_API_KEY
    if not api_key:
        raise RecognitionAttemptError(
            "CREDENTIALS_MISSING", "服务端未配置 QWEN_API_KEY。", retryable=False
        )
    image = _safe_source_image(run)
    prior = _provider_prior(run.scoresheet)
    body = {
        "model": settings.QWEN_MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": "data:image/jpeg;base64,"
                            + base64.b64encode(image).decode("ascii")
                        },
                    },
                    {"type": "text", "text": _prompt(prior)},
                ],
            }
        ],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }
    endpoint = settings.QWEN_BASE_URL.rstrip("/") + "/chat/completions"
    http_request = request.Request(
        endpoint,
        data=json.dumps(body, ensure_ascii=False).encode(),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with request.urlopen(http_request, timeout=settings.QWEN_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        status = exc.code
        detail = exc.read(2048).decode("utf-8", errors="replace")
        retryable = status == 429 or 500 <= status <= 599
        raise RecognitionAttemptError(
            f"QWEN_HTTP_{status}",
            f"Qwen 请求失败（{status}）：{detail[:500]}",
            retryable=retryable,
            retry_after_seconds=_retry_after(exc.headers.get("Retry-After")),
        ) from exc
    except (error.URLError, TimeoutError) as exc:
        raise RecognitionAttemptError(
            "QWEN_NETWORK_ERROR", "Qwen 网络请求超时或连接失败。", retryable=True
        ) from exc
    except json.JSONDecodeError as exc:
        raise RecognitionAttemptError(
            "PROVIDER_JSON_INVALID", "识别服务返回了无效 JSON。", retryable=True
        ) from exc
    try:
        result = json.loads(_extract_content(payload))
    except json.JSONDecodeError as exc:
        raise RecognitionAttemptError(
            "RESULT_JSON_INVALID", "模型结果不是有效 JSON。", retryable=True
        ) from exc
    return _validate_result_shape(result), payload.get("usage", {})


def _complete_success(
    claim: ClaimedRun, result: dict[str, object], usage: dict[str, object]
) -> str:
    with transaction.atomic():
        run = (
            ScoresheetRecognitionRun.objects.select_for_update()
            .select_related("scoresheet")
            .get(id=claim.run_id)
        )
        scoresheet = GameScoresheet.objects.select_for_update().get(id=run.scoresheet_id)
        if (
            run.status != ScoresheetRecognitionRun.Status.RUNNING
            or run.worker_lease_token != claim.worker_token
            or scoresheet.source_version != run.source_version
            or scoresheet.source_asset_id != run.source_asset_id
        ):
            if run.status == ScoresheetRecognitionRun.Status.RUNNING:
                run.status = ScoresheetRecognitionRun.Status.SUPERSEDED
                run.finished_at = timezone.now()
                run.save(update_fields=["status", "finished_at", "updated_at"])
            return "superseded"
        if scoresheet.draft_version != run.base_draft_version:
            # The provider response is valid and retained for audit, but a human
            # has already changed the shared draft. Applying it would silently
            # destroy newer work, so only publish a sync event and keep the
            # administrator's draft authoritative.
            run.status = ScoresheetRecognitionRun.Status.SUCCEEDED
            run.provider_result = result
            run.provider_usage = usage
            run.last_error_code = ""
            run.last_error = ""
            run.finished_at = timezone.now()
            run.worker_lease_token = None
            run.worker_lease_owner = ""
            run.worker_lease_expires_at = None
            run.save(
                update_fields=[
                    "status",
                    "provider_result",
                    "provider_usage",
                    "last_error_code",
                    "last_error",
                    "finished_at",
                    "worker_lease_token",
                    "worker_lease_owner",
                    "worker_lease_expires_at",
                    "updated_at",
                ]
            )
            scoresheet.status = GameScoresheet.Status.DRAFT
            scoresheet.save(update_fields=["status", "updated_at"])
            _event_locked(
                scoresheet,
                "RECOGNITION_STORED_NOT_APPLIED",
                payload={
                    "run_id": str(run.id),
                    "attempt": run.attempt_count,
                    "base_draft_version": run.base_draft_version,
                    "current_draft_version": scoresheet.draft_version,
                    "reason": "DRAFT_CHANGED_DURING_RECOGNITION",
                    "usage": usage,
                },
            )
            return "stored_not_applied"
        scoresheet.draft = merge_recognition_result(
            scoresheet.draft, result, scoresheet.roster_snapshot
        )
        scoresheet.draft_version += 1
        scoresheet.reviewed_regions = {}
        scoresheet.validation_report = {}
        scoresheet.validation_draft_version = None
        scoresheet.acknowledged_warnings = []
        scoresheet.status = GameScoresheet.Status.DRAFT
        scoresheet.save(
            update_fields=[
                "draft",
                "draft_version",
                "reviewed_regions",
                "validation_report",
                "validation_draft_version",
                "acknowledged_warnings",
                "status",
                "updated_at",
            ]
        )
        run.status = ScoresheetRecognitionRun.Status.SUCCEEDED
        run.provider_result = result
        run.provider_usage = usage
        run.last_error_code = ""
        run.last_error = ""
        run.finished_at = timezone.now()
        run.worker_lease_token = None
        run.worker_lease_owner = ""
        run.worker_lease_expires_at = None
        run.save(
            update_fields=[
                "status",
                "provider_result",
                "provider_usage",
                "last_error_code",
                "last_error",
                "finished_at",
                "worker_lease_token",
                "worker_lease_owner",
                "worker_lease_expires_at",
                "updated_at",
            ]
        )
        _event_locked(
            scoresheet,
            "RECOGNITION_APPLIED",
            changed_fields=[{"path": "/", "operation": "RECOGNITION_MERGE"}],
            payload={
                "run_id": str(run.id),
                "attempt": run.attempt_count,
                "usage": usage,
            },
        )
        _revision_locked(scoresheet, ScoresheetRevision.Reason.RECOGNITION_APPLIED)
        return "succeeded"


def _complete_failure(claim: ClaimedRun, failure: RecognitionAttemptError) -> str:
    with transaction.atomic():
        run = (
            ScoresheetRecognitionRun.objects.select_for_update()
            .select_related("scoresheet")
            .get(id=claim.run_id)
        )
        scoresheet = GameScoresheet.objects.select_for_update().get(id=run.scoresheet_id)
        if (
            run.status != ScoresheetRecognitionRun.Status.RUNNING
            or run.worker_lease_token != claim.worker_token
        ):
            return "superseded"
        if (
            scoresheet.source_version != run.source_version
            or scoresheet.source_asset_id != run.source_asset_id
        ):
            run.status = ScoresheetRecognitionRun.Status.SUPERSEDED
            run.finished_at = timezone.now()
            run.save(update_fields=["status", "finished_at", "updated_at"])
            return "superseded"
        run.last_error_code = failure.code
        run.last_error = str(failure)[:4000]
        run.worker_lease_token = None
        run.worker_lease_owner = ""
        run.worker_lease_expires_at = None
        if failure.retryable and run.attempt_count < run.max_attempts:
            default_delay = RETRY_DELAYS[run.attempt_count - 1]
            delay = max(default_delay, failure.retry_after_seconds or 0)
            run.status = ScoresheetRecognitionRun.Status.RETRY_WAIT
            run.next_attempt_at = timezone.now() + timedelta(seconds=delay)
            run.save(
                update_fields=[
                    "status",
                    "next_attempt_at",
                    "last_error_code",
                    "last_error",
                    "worker_lease_token",
                    "worker_lease_owner",
                    "worker_lease_expires_at",
                    "updated_at",
                ]
            )
            scoresheet.status = GameScoresheet.Status.RETRY_WAIT
            scoresheet.save(update_fields=["status", "updated_at"])
            _event_locked(
                scoresheet,
                "RECOGNITION_RETRY_WAIT",
                payload={
                    "run_id": str(run.id),
                    "attempt": run.attempt_count,
                    "max_attempts": run.max_attempts,
                    "next_attempt_at": run.next_attempt_at.isoformat(),
                    "error_code": failure.code,
                },
            )
            return "retry_wait"
        run.status = ScoresheetRecognitionRun.Status.FAILED
        run.finished_at = timezone.now()
        run.save(
            update_fields=[
                "status",
                "finished_at",
                "last_error_code",
                "last_error",
                "worker_lease_token",
                "worker_lease_owner",
                "worker_lease_expires_at",
                "updated_at",
            ]
        )
        scoresheet.status = GameScoresheet.Status.RECOGNITION_FAILED
        scoresheet.save(update_fields=["status", "updated_at"])
        _event_locked(
            scoresheet,
            "RECOGNITION_FAILED",
            payload={
                "run_id": str(run.id),
                "attempt": run.attempt_count,
                "max_attempts": run.max_attempts,
                "error_code": failure.code,
                "retryable": failure.retryable,
            },
        )
        return "failed"


def execute_claim(claim: ClaimedRun) -> str:
    run = (
        ScoresheetRecognitionRun.objects.select_related("scoresheet", "source_asset")
        .get(id=claim.run_id)
    )
    try:
        result, usage = call_qwen(run)
    except RecognitionAttemptError as failure:
        return _complete_failure(claim, failure)
    except Exception as exc:  # Persist unexpected provider/decoder failures for operator review.
        return _complete_failure(
            claim,
            RecognitionAttemptError(
                "UNEXPECTED_RECOGNITION_ERROR", str(exc), retryable=True
            ),
        )
    return _complete_success(claim, result, usage)


def run_once(worker_name: str) -> str | None:
    claim = claim_next_run(worker_name)
    if claim is None:
        return None
    return execute_claim(claim)
