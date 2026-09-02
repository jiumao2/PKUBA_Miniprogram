from datetime import date

import pytest
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor

from core.models import Account, AdminRegistrationPolicy

pytestmark = pytest.mark.django_db(transaction=True)

MIGRATE_FROM = ("core", "0042_normalize_draw_assignment_validation")
MIGRATE_TO = ("core", "0043_admin_registration_policy")


def test_upgrade_preserves_legacy_season_invite_fields_and_starts_policy_unconfigured():
    executor = MigrationExecutor(connection)
    executor.migrate([MIGRATE_FROM])
    old_apps = executor.loader.project_state([MIGRATE_FROM]).apps
    Season = old_apps.get_model("core", "Season")
    season = Season.objects.create(
        name="升级测试赛季",
        competition_type="PKU_CUP",
        year=2026,
        status="SETUP",
        timezone="Asia/Shanghai",
        starts_on=date(2026, 3, 1),
        ends_on=date(2026, 5, 31),
        admin_invite_code_hash="legacy-season-hash",
    )

    executor = MigrationExecutor(connection)
    executor.migrate([MIGRATE_TO])
    new_apps = executor.loader.project_state([MIGRATE_TO]).apps
    NewSeason = new_apps.get_model("core", "Season")
    Policy = new_apps.get_model("core", "AdminRegistrationPolicy")

    upgraded = NewSeason.objects.get(id=season.id)
    assert upgraded.admin_invite_code_hash == "legacy-season-hash"
    assert Policy.objects.count() == 0


def test_global_policy_database_constraints_allow_only_the_singleton_key():
    actor = Account.objects.create_user(
        username="policy-constraint-owner",
        password="StrongPass!2026",
        role=Account.Role.SUPERADMIN,
    )
    AdminRegistrationPolicy.objects.create(
        invite_code_hash="encoded-hash",
        initialized_by=actor,
        updated_by=actor,
    )

    with pytest.raises(IntegrityError), transaction.atomic():
        AdminRegistrationPolicy.objects.create(
            singleton_key=2,
            invite_code_hash="another-encoded-hash",
            initialized_by=actor,
            updated_by=actor,
        )
