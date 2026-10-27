from django.utils import timezone

from core.models import Game, GameScoresheet, ScoresheetEditLease, ScoresheetRecognitionRun
from core.services.inbox_tasks import close_scoresheet_tasks


def workflow_mode(game: Game) -> str:
    return "FORFEIT_ATTACHMENT" if game.status == Game.Status.FORFEIT else "NORMAL"


def reconcile_scoresheet_game_status(*, game: Game, actor, previous_status: str) -> None:
    """Called inside the game's locked update transaction; never change history."""
    if previous_status == game.status or not (
        Game.Status.FORFEIT in {previous_status, game.status} or game.status == Game.Status.VOID
    ):
        return
    sheet = GameScoresheet.objects.select_for_update().filter(game=game).first()
    if sheet is None:
        return
    from core.models import ScoresheetRevision
    from core.services.scoresheets import _event_locked, _revision_locked

    if game.status in {Game.Status.FORFEIT, Game.Status.VOID}:
        for run in sheet.recognition_runs.select_for_update().filter(
            status__in=["QUEUED", "RUNNING", "RETRY_WAIT"]
        ):
            run.result_only = True
            run.auto_apply_allowed = False
            run.next_attempt_at = None
            if run.status != ScoresheetRecognitionRun.Status.RUNNING:
                run.status = ScoresheetRecognitionRun.Status.STOPPED
                run.stopped_by = actor
                run.stopped_at = timezone.now()
                run.finished_at = run.stopped_at
                run.last_error_code = "GAME_RESULT_NO_SCORESHEET"
                run.last_error = "比赛无需记录表处理，未启动新的识别请求。"
            run.save()
        sheet.current_publication = None
        close_scoresheet_tasks(sheet.id, reason="GAME_RESULT_NO_SCORESHEET")
    sheet.status = (
        GameScoresheet.Status.DRAFT if sheet.source_asset_id else GameScoresheet.Status.NO_SOURCE
    )
    sheet.draft_version += 1
    sheet.validation_report = {}
    sheet.validation_draft_version = None
    sheet.acknowledged_warnings = []
    sheet.reviewed_regions = {}
    sheet.save()
    ScoresheetEditLease.objects.filter(scoresheet=sheet).delete()
    _event_locked(
        sheet,
        "GAME_STATUS_CHANGED",
        actor=actor,
        payload={
            "previous_status": previous_status,
            "game_status": game.status,
            "workflow_mode": workflow_mode(game),
        },
    )
    _revision_locked(sheet, ScoresheetRevision.Reason.EXPLICIT_SAVE, actor=actor)
