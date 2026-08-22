from __future__ import annotations

from core.models import EmailOutbox

PUBLIC_MAILBOX = "pkubaoutward@163.com"


def enqueue_public_mail(
    *,
    event_key: str,
    subject: str,
    body: str,
    object_type: str,
    object_id,
) -> EmailOutbox:
    """Create one immutable public-mailbox message for an authoritative event."""

    if not event_key or len(event_key) > 180:
        raise ValueError("Email outbox event_key must contain 1 to 180 characters.")
    message, _ = EmailOutbox.objects.get_or_create(
        event_key=event_key,
        defaults={
            "recipient": PUBLIC_MAILBOX,
            "object_type": object_type[:64],
            "object_id": object_id,
            "subject": subject[:200],
            "body": body,
        },
    )
    return message
