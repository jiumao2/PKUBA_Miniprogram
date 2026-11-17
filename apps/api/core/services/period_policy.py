"""Period policy is separate from capacity and from a game's source policy."""

from django.db.migrations.recorder import MigrationRecorder

from core.models import Game, Period, RescheduleRequest, Season

ACTIVATION_MIGRATION = "0050_activate_period_usage_policy"


def period_management_enabled():
    """Bridge also enables management after rolling back from the activation release.

    Do not cache this receipt or use a slot-local flag: the retained application
    must respect the database's activation state. Remove in the activation release.
    """
    return MigrationRecorder.Migration.objects.filter(
        app="core", name=ACTIVATION_MIGRATION
    ).exists()


def lock_game_season(*, game_id=None, request_id=None):
    """Acquire the season before request/game locks, matching configuration writes."""
    if request_id is not None:
        season_id = (
            RescheduleRequest.objects.filter(id=request_id)
            .values_list("game__season_id", flat=True)
            .first()
        )
    else:
        season_id = Game.objects.filter(id=game_id).values_list("season_id", flat=True).first()
    if season_id is not None:
        return Season.objects.select_for_update().get(id=season_id)
    return None


def progress_block_reason(request):
    if request.is_terminal or request.target_period.usage == Period.Usage.OPEN:
        return None
    return "目标时段已不开放调赛，请撤回或联系管理员取消申请后重新选择。"


def period_deletion_blocker(period):
    if period.season.status != Season.Status.SETUP:
        return "公开后仅可停用；归档赛季只读。"
    if (
        period.games.exists()
        or RescheduleRequest.objects.filter(target_period=period).exists()
        or period.reservations.exists()
        or period.schedule_grid_draft_columns.exists()
        or period.slot_locks.exists()
    ):
        return "时段已被比赛、调赛、预留或草稿列引用，只能停用。"
    return None
