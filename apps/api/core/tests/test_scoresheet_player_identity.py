from __future__ import annotations

import copy

import pytest

from core.models import GameMediaAsset, GamePlayerStat, RosterPlayer, ScoresheetRecognitionRun
from core.services.scoresheets import ScoresheetError, publish_scoresheet, save_draft_changes
from core.tests.test_scoresheet_game_context import (
    acknowledge,
    mutation,
    review,
    snapshot,
    validate,
)
from core.tests.test_scoresheets import create_scoresheet, make_ready, obtain_lease

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def ready_sheet(settings, tmp_path):
    settings.QWEN_API_KEY = ""
    settings.MEDIA_ROOT = tmp_path
    setup, game, players, source, sheet = create_scoresheet()
    token = obtain_lease(sheet, setup["admin"])
    sheet = make_ready(sheet, setup["admin"], token)
    return setup, game, players, source, sheet, token


def _save(fixture, document):
    return save_draft_changes(
        **mutation(fixture), changes=[{"path": "/", "operation": "SET", "value": document}]
    )


def _bind_current_players(fixture, *, same_name: bool):
    """Use the actual review service to establish two stable player identities."""
    _, _, players, _, sheet, _ = fixture
    previous = players["A"]
    other = RosterPlayer.objects.filter(team=previous.team).exclude(id=previous.id).order_by(
        "jersey_number"
    ).first()
    assert other is not None
    previous.active = False
    previous.save(update_fields=["active", "updated_at"])
    chosen = RosterPlayer.objects.create(
        team=previous.team, name=previous.name, jersey_number=previous.jersey_number
    )
    sheet.refresh_from_db()
    document = copy.deepcopy(sheet.draft)
    rows = document["teams"][0]["players"]
    chosen_row = next(row for row in rows if row["jersey_number"] == chosen.jersey_number)
    other_row = next(row for row in rows if row["jersey_number"] == other.jersey_number)
    if same_name:
        other.name = chosen.name
        other.save(update_fields=["name", "updated_at"])
        other_row["name"] = chosen.name
        _save(fixture, document)
    report = validate(fixture).validation_report
    identities = {chosen_row["row"]: chosen, other_row["row"]: other}
    mappings = []
    for conflict in report["game_context"]["player_conflicts"]:
        assert conflict["side"] == "A" and conflict["row"] in identities
        mappings.append({
            "side": "A", "row": conflict["row"],
            "player_id": str(identities[conflict["row"]].id),
        })
    assert any(row["player_id"] == str(chosen.id) for row in mappings)
    review(fixture, player_mappings=mappings)
    assert not validate(fixture).validation_report["errors"]
    return chosen, other, chosen_row["row"], other_row["row"]


def _change_match_numbers(fixture, first_row, second_row, *, swap: bool):
    sheet = fixture[4]
    sheet.refresh_from_db()
    document = copy.deepcopy(sheet.draft)
    rows = document["teams"][0]["players"]
    first = next(row for row in rows if row["row"] == first_row)
    second = next(row for row in rows if row["row"] == second_row)
    old_first, old_second = first["jersey_number"], second["jersey_number"]
    changes = {old_first: old_second, old_second: old_first} if swap else {old_first: "42"}
    for row in rows:
        row["jersey_number"] = changes.get(row["jersey_number"], row["jersey_number"])
    for event in document["score_events"]:
        if event["team"] == "A":
            event["scorer_jersey"] = changes.get(event["scorer_jersey"], event["scorer_jersey"])
    return _save(fixture, document)


@pytest.mark.parametrize("same_name,swap", [(True, True), (True, False), (False, True)])
def test_game_local_number_changes_keep_explicit_player_identity(ready_sheet, same_name, swap):
    chosen, other, first_row, second_row = _bind_current_players(
        ready_sheet, same_name=same_name
    )
    sheet = ready_sheet[4]
    sheet.refresh_from_db()
    original_bindings = copy.deepcopy(sheet.game_prior_snapshot["confirmed_player_bindings"])
    original_source = list(GameMediaAsset.objects.values())
    source_fields = (
        "id", "game_id", "kind", "file_key", "file_sha256", "byte_size",
        "original_filename", "mime_type", "created_at",
    )
    original_source_evidence = list(GameMediaAsset.objects.values(*source_fields))
    original_runs = list(ScoresheetRecognitionRun.objects.values())
    edited = _change_match_numbers(ready_sheet, first_row, second_row, swap=swap)
    edited_teams = copy.deepcopy(edited.draft["teams"])
    report = validate(ready_sheet).validation_report
    assert not report["game_context"]["required"]
    assert not report["errors"], report["errors"]
    assert list(GameMediaAsset.objects.values()) == original_source
    acknowledge(ready_sheet)
    publication = publish_scoresheet(**mutation(ready_sheet))
    assert GamePlayerStat.objects.get(publication=publication, roster_player=chosen).points == 2
    assert GamePlayerStat.objects.get(publication=publication, roster_player=other).points == 0
    assert publication.snapshot["teams"] == edited_teams
    sheet.refresh_from_db()
    assert sheet.game_prior_snapshot["confirmed_player_bindings"] == original_bindings
    # Publication legitimately changes the asset's review/version metadata,
    # but must not replace the image or its immutable source evidence.
    assert list(GameMediaAsset.objects.values(*source_fields)) == original_source_evidence
    assert list(ScoresheetRecognitionRun.objects.values()) == original_runs


@pytest.mark.parametrize("change", ["inactive", "name", "ineligible"])
def test_binding_never_falls_back_to_same_name_when_authoritative_identity_changes(
    ready_sheet, change
):
    chosen, _, first_row, second_row = _bind_current_players(ready_sheet, same_name=True)
    _change_match_numbers(ready_sheet, first_row, second_row, swap=True)
    if change == "inactive":
        chosen.active = False
    elif change == "name":
        chosen.name = "已更正的权威姓名"
    else:
        chosen.eligible = False
    chosen.save()
    report = validate(ready_sheet).validation_report
    assert report["errors"]
    assert report["game_context"]["required"]
    before = snapshot()
    with pytest.raises(ScoresheetError) as failure:
        publish_scoresheet(**mutation(ready_sheet))
    assert failure.value.status in {400, 409}
    assert snapshot() == before
