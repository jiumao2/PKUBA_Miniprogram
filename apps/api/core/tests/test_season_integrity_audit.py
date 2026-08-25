import json
from io import StringIO

import pytest
from django.core.management import call_command

pytestmark = pytest.mark.django_db(transaction=True)


def test_empty_database_passes_read_only_season_integrity_audit():
    output = StringIO()
    call_command("audit_season_integrity", "--json", stdout=output)

    payload = json.loads(output.getvalue())
    assert payload["ok"] is True
    assert payload["violations"] == {}
    assert all(count == 0 for count in payload["checks"].values())
