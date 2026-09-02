import uuid

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0042_normalize_draw_assignment_validation"),
    ]

    operations = [
        migrations.CreateModel(
            name="AdminRegistrationPolicy",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "singleton_key",
                    models.PositiveSmallIntegerField(default=1, editable=False, unique=True),
                ),
                ("invite_code_hash", models.CharField(max_length=128)),
                ("version", models.PositiveIntegerField(default=1)),
                ("initialized_at", models.DateTimeField(default=django.utils.timezone.now)),
                (
                    "initialized_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="initialized_admin_registration_policies",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="updated_admin_registration_policies",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
        migrations.AddConstraint(
            model_name="adminregistrationpolicy",
            constraint=models.CheckConstraint(
                condition=models.Q(("singleton_key", 1)),
                name="admin_registration_policy_singleton",
            ),
        ),
    ]
