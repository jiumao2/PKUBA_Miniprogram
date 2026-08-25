from django.db import connection

from core.models import Account

# "PKUBADMI" encoded as a stable signed-bigint-safe PostgreSQL advisory key.
SUPERADMIN_COMMAND_LOCK_ID = 0x504B554241444D49


class SuperadminActorStateError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def lock_superadmin_commands() -> None:
    """Serialize low-frequency superadmin authority changes and business commands.

    Callers must already be inside ``transaction.atomic()``. PostgreSQL releases
    the transaction-level lock automatically on commit or rollback.
    """

    if connection.vendor != "postgresql":
        raise RuntimeError("superadmin command locking requires PostgreSQL")
    if not connection.in_atomic_block:
        raise RuntimeError("superadmin command locking requires transaction.atomic()")
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_advisory_xact_lock(%s)",
            [SUPERADMIN_COMMAND_LOCK_ID],
        )


def lock_current_superadmin_actor(actor: Account) -> Account:
    """Re-read and lock the authoritative actor after the command lock is held."""

    if not actor.is_pkuba_superadmin:
        raise SuperadminActorStateError("PERMISSION_DENIED")
    current = Account.objects.select_for_update().filter(id=actor.id).first()
    if current is None or not current.is_pkuba_superadmin:
        raise SuperadminActorStateError("ACTOR_STATE_CHANGED")
    if current.version != actor.version:
        raise SuperadminActorStateError("ACTOR_STATE_CHANGED")
    return current
