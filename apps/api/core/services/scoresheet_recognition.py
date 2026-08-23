from __future__ import annotations

import json
import logging
import tempfile
import uuid
import warnings
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from threading import Event, Thread

from django.conf import settings
from django.core.files.storage import default_storage
from django.db import close_old_connections, transaction
from django.db.models import Q
from django.utils import timezone
from PIL import Image, UnidentifiedImageError
from pydantic import ValidationError

from core.models import (
    GameScoresheet,
    ScoresheetRecognitionRun,
    ScoresheetRevision,
)
from core.scoresheet_schema_v2 import merge_recognition_result
from core.scoresheet_v2.models import ScoresheetDocument
from core.scoresheet_v2.recognition import (
    PROMPT_VERSION,
    RecognitionImageError,
    build_context,
    map_payload_to_document,
    validate_provider_payload,
)
from core.services.inbox_tasks import sync_scoresheet_recognition_tasks
from core.services.scoresheets import _event_locked, _revision_locked

RETRY_DELAYS = (30, 30, 30)
WORKER_LEASE_SECONDS = 5 * 60
WORKER_LEASE_REFRESH_SECONDS = 60
SOURCE_CHUNK_BYTES = 1024 * 1024
logger = logging.getLogger(__name__)


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


def _renew_worker_lease(claim: ClaimedRun) -> bool:
    updated = ScoresheetRecognitionRun.objects.filter(
        id=claim.run_id,
        status=ScoresheetRecognitionRun.Status.RUNNING,
        worker_lease_token=claim.worker_token,
    ).update(
        worker_lease_expires_at=timezone.now() + timedelta(seconds=WORKER_LEASE_SECONDS)
    )
    return updated == 1


def _worker_lease_heartbeat(claim: ClaimedRun, stop: Event) -> None:
    close_old_connections()
    try:
        while not stop.wait(WORKER_LEASE_REFRESH_SECONDS):
            try:
                if not _renew_worker_lease(claim):
                    return
            except Exception:  # noqa: BLE001 - keep renewing after transient database failures.
                logger.exception("Failed to renew scoresheet recognition worker lease")
                close_old_connections()
    finally:
        close_old_connections()


@contextmanager
def _maintain_worker_lease(claim: ClaimedRun) -> Iterator[None]:
    stop = Event()
    heartbeat = Thread(
        target=_worker_lease_heartbeat,
        args=(claim, stop),
        name=f"scoresheet-lease-{claim.run_id}",
        daemon=True,
    )
    heartbeat.start()
    try:
        yield
    finally:
        stop.set()
        heartbeat.join(timeout=5)


@contextmanager
def _source_image_path(run: ScoresheetRecognitionRun) -> Iterator[Path]:
    if run.source_asset.deleted_at or not default_storage.exists(run.source_asset.file_key):
        raise RecognitionAttemptError(
            "SOURCE_MISSING", "记录表原图已被替换或删除。", retryable=False
        )
    temporary_path: Path | None = None
    try:
        try:
            local_path = Path(default_storage.path(run.source_asset.file_key))
        except (AttributeError, NotImplementedError):
            local_path = None
        if local_path is not None:
            if not local_path.is_file() or local_path.stat().st_size == 0:
                raise RecognitionAttemptError(
                    "IMAGE_INVALID", "记录表图片为空或无法读取。", retryable=False
                )
            yield local_path
            return

        suffix = Path(run.source_asset.file_key).suffix
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temporary:
            temporary_path = Path(temporary.name)
            with default_storage.open(run.source_asset.file_key, "rb") as source:
                while chunk := source.read(SOURCE_CHUNK_BYTES):
                    temporary.write(chunk)
        if temporary_path.stat().st_size == 0:
            raise RecognitionAttemptError(
                "IMAGE_INVALID", "记录表图片为空或无法读取。", retryable=False
            )
        yield temporary_path
    except RecognitionAttemptError:
        raise
    except OSError as exc:
        raise RecognitionAttemptError(
            "IMAGE_INVALID", "记录表图片无法读取。", retryable=False
        ) from exc
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _provider_prior(scoresheet: GameScoresheet) -> dict[str, object]:
    prior = scoresheet.game_prior_snapshot
    roster = scoresheet.roster_snapshot
    if not roster.get("A") or not roster.get("B"):
        raise RecognitionAttemptError("ROSTER_MISSING", "双方冻结名单不完整。", retryable=False)
    return {
        "teams": {
            side: {
                "name": prior.get("team_a" if side == "A" else "team_b", {}).get("display_name"),
                "players": [{"name": row.get("display_name")} for row in roster.get(side, [])],
            }
            for side in ("A", "B")
        },
    }


def _prompt(prior: dict[str, object]) -> str:
    """Compatibility helper used by security-focused tests and diagnostics."""

    return (
        "识别请求只包含以下球队与候选球员姓名："
        + json.dumps(prior, ensure_ascii=False, separators=(",", ":"))
    )


def _usage_payload(raw: object) -> dict[str, int]:
    value = raw.model_dump(mode="json") if hasattr(raw, "model_dump") else {}
    usage = value.get("usage") or {}
    input_details = usage.get("input_tokens_details") or usage.get("prompt_tokens_details") or {}
    output_details = (
        usage.get("output_tokens_details") or usage.get("completion_tokens_details") or {}
    )
    input_tokens = int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "image_tokens": int(usage.get("image_tokens") or input_details.get("image_tokens") or 0),
        "reasoning_tokens": int(output_details.get("reasoning_tokens") or 0),
        "total_tokens": int(usage.get("total_tokens") or input_tokens + output_tokens),
    }


def _provider_failure(error: Exception) -> RecognitionAttemptError:
    status = getattr(error, "status_code", None)
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", {}) or {}
    retry_after = _retry_after(headers.get("Retry-After"))
    if isinstance(status, int):
        return RecognitionAttemptError(
            f"QWEN_HTTP_{status}",
            f"Qwen 请求失败（{status}）：{str(error)[:500]}",
            retryable=status == 429 or 500 <= status <= 599,
            retry_after_seconds=retry_after,
        )
    name = type(error).__name__
    retryable = name in {
        "APITimeoutError",
        "APIConnectionError",
        "ConnectError",
        "ReadTimeout",
        "TimeoutError",
    }
    return RecognitionAttemptError(
        "QWEN_NETWORK_ERROR" if retryable else "QWEN_PROVIDER_ERROR",
        "Qwen 网络请求超时或连接失败。" if retryable else f"Qwen 请求失败：{str(error)[:500]}",
        retryable=retryable,
        retry_after_seconds=retry_after,
    )


def call_qwen(run: ScoresheetRecognitionRun) -> tuple[dict[str, object], dict[str, object]]:
    try:
        document = ScoresheetDocument.model_validate(run.scoresheet.draft)
        prior = document.game_prior
        if prior is None or not prior.team_a.player_names or not prior.team_b.player_names:
            raise RecognitionAttemptError(
                "ROSTER_MISSING", "双方冻结名单不完整。", retryable=False
            )
        rule_path = settings.BASE_DIR / "core" / "assets" / "scoresheet" / "rule_profiles.json"
        with _source_image_path(run) as image_path, warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            context = build_context(
                prior,
                image_path,
                rule_path,
                settings.SCORESHEET_RECOGNITION_UPSCALE_TARGET_PIXELS,
            )
    except RecognitionAttemptError:
        raise
    except RecognitionImageError as exc:
        raise RecognitionAttemptError(
            "IMAGE_DATA_URI_TOO_LARGE", str(exc), retryable=False
        ) from exc
    except (
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        UnidentifiedImageError,
        OSError,
        ValueError,
    ) as exc:
        raise RecognitionAttemptError(
            "IMAGE_INVALID", "记录表图片无法安全预处理。", retryable=False
        ) from exc

    run.model_name = settings.QWEN_MODEL
    run.prompt_version = PROMPT_VERSION
    run.image_sha256 = context.image_sha256
    run.save(update_fields=["model_name", "prompt_version", "image_sha256", "updated_at"])

    api_key = settings.QWEN_API_KEY
    if not api_key:
        raise RecognitionAttemptError(
            "CREDENTIALS_MISSING", "服务端未配置 QWEN_API_KEY。", retryable=False
        )
    try:
        from openai import OpenAI

        client = OpenAI(
            api_key=api_key,
            base_url=settings.QWEN_BASE_URL,
            timeout=settings.SCORESHEET_RECOGNITION_TIMEOUT_SECONDS,
            max_retries=0,
        )
        response = client.chat.completions.create(
            model=settings.QWEN_MODEL,
            messages=[
                {"role": "system", "content": context.system_prompt},
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": context.image_data_url}},
                        {"type": "text", "text": context.user_prompt},
                    ],
                },
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "scoresheet_recognition",
                    "strict": True,
                    "schema": context.schema,
                },
            },
            seed=1234,
            stream=True,
            stream_options={"include_usage": True},
            extra_body={
                "enable_thinking": True,
                "reasoning_effort": settings.QWEN_REASONING_EFFORT,
                "vl_high_resolution_images": True,
                "preserve_thinking": False,
            },
        )
        content_parts: list[str] = []
        usage: dict[str, int] = {
            "input_tokens": 0,
            "output_tokens": 0,
            "image_tokens": 0,
            "reasoning_tokens": 0,
            "total_tokens": 0,
        }
        for chunk in response:
            chunk_usage = _usage_payload(chunk)
            if chunk_usage["total_tokens"]:
                usage = chunk_usage
            if not chunk.choices:
                continue
            content = chunk.choices[0].delta.content
            if isinstance(content, str) and content:
                content_parts.append(content)
    except Exception as exc:
        raise _provider_failure(exc) from exc

    content = "".join(content_parts).strip()
    if not content:
        raise RecognitionAttemptError(
            "PROVIDER_SCHEMA_INVALID", "Qwen 未返回可解析的 JSON。", retryable=True
        )
    try:
        provider_payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise RecognitionAttemptError(
            "RESULT_JSON_INVALID", "模型结果不是有效 JSON。", retryable=True
        ) from exc
    try:
        validated, normalization_issues = validate_provider_payload(
            context,
            provider_payload,
            prior,
        )
        mapped = map_payload_to_document(
            document,
            validated,
            str(run.id),
            rule_path,
            normalization_issues=normalization_issues,
        )
    except (ValidationError, ValueError) as exc:
        raise RecognitionAttemptError(
            "RESULT_SCHEMA_INVALID",
            f"识别结果未通过严格结构校验：{str(exc)[:1000]}",
            retryable=True,
        ) from exc
    return mapped.model_dump(mode="json"), usage


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
        recognition_state = result.get("recognition")
        recognition_notes = (
            str(recognition_state.get("notes") or "")
            if isinstance(recognition_state, dict)
            else str(result.get("recognition_notes") or "")
        )
        if scoresheet.draft_version != run.base_draft_version or not run.auto_apply_allowed:
            # The provider response is valid and retained for audit, but a human
            # has already changed the shared draft. Applying it would silently
            # destroy newer work, so only publish a sync event and keep the
            # administrator's draft authoritative.
            run.status = ScoresheetRecognitionRun.Status.SUCCEEDED
            run.provider_result = result
            run.provider_usage = usage
            run.applied_draft_version = None
            run.recognition_notes = recognition_notes
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
                    "applied_draft_version",
                    "recognition_notes",
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
                    "reason": (
                        "DRAFT_CHANGED_DURING_RECOGNITION"
                        if scoresheet.draft_version != run.base_draft_version
                        else "HUMAN_CHANGES_REQUIRE_DIFF"
                    ),
                    "usage": usage,
                },
            )
            sync_scoresheet_recognition_tasks(scoresheet, run)
            return "stored_not_applied"
        scoresheet.draft = merge_recognition_result(
            scoresheet.draft,
            result,
            scoresheet.roster_snapshot,
            run_id=str(run.id),
        )
        scoresheet.draft_version += 1
        scoresheet.draft["revision"] = scoresheet.draft_version
        scoresheet.draft["updated_at"] = timezone.now().isoformat()
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
        run.applied_draft_version = scoresheet.draft_version
        run.recognition_notes = recognition_notes
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
                "applied_draft_version",
                "recognition_notes",
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
        sync_scoresheet_recognition_tasks(scoresheet, run)
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
        sync_scoresheet_recognition_tasks(scoresheet, run)
        return "failed"


def execute_claim(claim: ClaimedRun) -> str:
    run = ScoresheetRecognitionRun.objects.select_related("scoresheet", "source_asset").get(
        id=claim.run_id
    )
    with _maintain_worker_lease(claim):
        try:
            result, usage = call_qwen(run)
        except RecognitionAttemptError as failure:
            return _complete_failure(claim, failure)
        # Persist unexpected provider/decoder failures for operator review.
        except Exception as exc:
            return _complete_failure(
                claim,
                RecognitionAttemptError("UNEXPECTED_RECOGNITION_ERROR", str(exc), retryable=True),
            )
    return _complete_success(claim, result, usage)


def run_once(worker_name: str) -> str | None:
    claim = claim_next_run(worker_name)
    if claim is None:
        return None
    return execute_claim(claim)
