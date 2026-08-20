from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.services.legacy_2026_import import LegacyImportError, inspect_legacy_2026


def test_legacy_import_rejects_incomplete_snapshot(tmp_path: Path):
    (tmp_path / "Private_2026北大杯.json").write_text(
        json.dumps({"GAME_NAME": "北大杯"}, ensure_ascii=False), encoding="utf-8"
    )
    (tmp_path / "Team_2026北大杯.json").write_text("", encoding="utf-8")
    (tmp_path / "Schedule_2026北大杯.json").write_text("", encoding="utf-8")
    with pytest.raises(LegacyImportError, match="球队 0/57"):
        inspect_legacy_2026(tmp_path)
