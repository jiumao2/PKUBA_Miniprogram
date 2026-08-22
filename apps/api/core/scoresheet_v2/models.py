from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def utc_now() -> datetime:
    return datetime.now(UTC)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TeamSide(StrEnum):
    A = "A"
    B = "B"


class ParticipationStatus(StrEnum):
    NONE = "none"
    STARTER = "starter"
    SUBSTITUTE = "substitute"


class FoulCode(StrEnum):
    P = "P"
    T = "T"
    U = "U"
    D = "D"
    C = "C"
    B = "B"
    GD = "GD"
    F = "F"
    DI = "DI"
    FL = "FL"
    BD = "BD"


class FoulMarkStyle(StrEnum):
    PLAIN = "plain"
    CIRCLED = "circled"


class RuleProfileId(StrEnum):
    FIBA_2024 = "fiba_2024"
    FIBA_2026 = "fiba_2026"


class ScoreMark(StrEnum):
    FILLED_DOT = "filled_dot"
    DIAGONAL = "diagonal"


class ScoreBoundary(StrEnum):
    NONE = "none"
    PERIOD_END = "period_end"
    GAME_END = "game_end"


class InkRole(StrEnum):
    Q1_Q3 = "q1_q3"
    Q2_Q4_OT = "q2_q4_ot"
    NEUTRAL = "neutral"


class SignaturePresence(StrEnum):
    PRESENT = "present"
    ABSENT = "absent"
    UNCLEAR = "unclear"


class DocumentStatus(StrEnum):
    DRAFT = "draft"
    NEEDS_REVIEW = "needs_review"
    VALIDATED = "validated"
    CONFIRMED = "confirmed"


class ValidationSeverity(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class ValidationStatus(StrEnum):
    VALID = "valid"
    NEEDS_REVIEW = "needs_review"
    INVALID = "invalid"


class Header(StrictModel):
    competition: str = ""
    game_number: str = ""
    date: str = ""
    scheduled_time: str = ""
    venue: str = ""
    crew_chief: str = ""
    umpire_1: str = ""
    umpire_2: str = ""


class FoulEntry(StrictModel):
    slot: int = Field(ge=1, le=5)
    code: FoulCode
    catalog_id: str | None = None
    mark_style: FoulMarkStyle = FoulMarkStyle.PLAIN
    free_throws: int | None = Field(default=None, ge=1, le=3)
    cancelled: bool = False
    period: int | None = Field(default=None, ge=1, le=8)

    @model_validator(mode="after")
    def cancellation_and_free_throws_are_exclusive(self) -> FoulEntry:
        if self.cancelled and self.free_throws is not None:
            raise ValueError("a cancelled foul cannot also carry a free-throw number")
        return self


class PostFoulMarker(FoulEntry):
    """A rule marker written in the unboxed column after the last formal foul cell."""

    slot: int = Field(ge=1, le=2)


class PlayerEntry(StrictModel):
    row: int = Field(ge=1, le=12)
    license_number: str = ""
    name: str = ""
    jersey_number: str = ""
    captain: bool = False
    participation: ParticipationStatus = ParticipationStatus.NONE
    fouls: list[FoulEntry] = Field(default_factory=list)
    post_foul_markers: list[PostFoulMarker] = Field(default_factory=list)

    @field_validator("jersey_number")
    @classmethod
    def validate_jersey(cls, value: str) -> str:
        value = value.strip()
        if value and not (
            value in {"0", "00"}
            or (value.isdigit() and not value.startswith("0") and 1 <= int(value) <= 99)
        ):
            raise ValueError("jersey_number must be 0, 00, or an integer from 1 to 99")
        return value

    @model_validator(mode="after")
    def unique_foul_slots(self) -> PlayerEntry:
        slots = [entry.slot for entry in self.fouls]
        if len(slots) != len(set(slots)):
            raise ValueError("foul slots must be unique within a player")
        post_slots = [entry.slot for entry in self.post_foul_markers]
        if len(post_slots) != len(set(post_slots)):
            raise ValueError("post-foul marker slots must be unique within a player")
        return self


class TimeoutEntry(StrictModel):
    scope: Literal["H1", "H2", "OT"]
    slot: int = Field(ge=1, le=3)
    minute: int = Field(ge=0, le=10)


class TeamFoulPeriod(StrictModel):
    period: int = Field(ge=1, le=4)
    count: int = Field(ge=0, le=4)


class TeamEntry(StrictModel):
    side: TeamSide
    name: str = ""
    players: list[PlayerEntry] = Field(default_factory=list)
    timeouts: list[TimeoutEntry] = Field(default_factory=list)
    team_fouls: list[TeamFoulPeriod] = Field(default_factory=list)
    coach_fouls: list[FoulEntry] = Field(default_factory=list)
    coach_post_foul_markers: list[PostFoulMarker] = Field(default_factory=list)
    assistant_coach_fouls: list[FoulEntry] = Field(default_factory=list)
    assistant_coach_post_foul_markers: list[PostFoulMarker] = Field(default_factory=list)
    head_coach: str = ""
    assistant_coach: str = ""

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_coach_foul_slots(cls, value: Any) -> Any:
        """Move legacy fourth/fifth coach entries into the post-foul column on load."""
        if not isinstance(value, dict):
            return value
        coach_fouls = value.get("coach_fouls")
        if not isinstance(coach_fouls, list):
            return value
        formal: list[Any] = []
        migrated: list[Any] = list(value.get("coach_post_foul_markers") or [])
        changed = False
        occupied = {
            item.get("slot") for item in migrated if isinstance(item, dict) and item.get("slot")
        }
        for item in coach_fouls:
            slot = item.get("slot") if isinstance(item, dict) else getattr(item, "slot", None)
            if isinstance(slot, int) and slot > 3:
                changed = True
                target_slot = min(2, slot - 3)
                if target_slot in occupied:
                    continue
                payload = (
                    item.model_dump(mode="json") if isinstance(item, BaseModel) else dict(item)
                )
                payload["slot"] = target_slot
                migrated.append(payload)
                occupied.add(target_slot)
            else:
                formal.append(item)
        if changed:
            value = dict(value)
            value["coach_fouls"] = formal
            value["coach_post_foul_markers"] = migrated
        return value

    @model_validator(mode="after")
    def unique_rows(self) -> TeamEntry:
        rows = [player.row for player in self.players]
        if len(rows) != len(set(rows)):
            raise ValueError("player rows must be unique within a team")
        coach_slots = [entry.slot for entry in self.coach_fouls]
        if any(slot > 3 for slot in coach_slots):
            raise ValueError("the head coach has exactly 3 formal foul cells")
        if len(coach_slots) != len(set(coach_slots)):
            raise ValueError("coach foul slots must be unique")
        post_slots = [entry.slot for entry in self.coach_post_foul_markers]
        if len(post_slots) != len(set(post_slots)):
            raise ValueError("coach post-foul marker slots must be unique")
        assistant_slots = [entry.slot for entry in self.assistant_coach_fouls]
        if any(slot > 3 for slot in assistant_slots):
            raise ValueError("the first assistant coach has exactly 3 foul cells")
        if len(assistant_slots) != len(set(assistant_slots)):
            raise ValueError("assistant coach foul slots must be unique")
        assistant_post_slots = [entry.slot for entry in self.assistant_coach_post_foul_markers]
        if len(assistant_post_slots) != len(set(assistant_post_slots)):
            raise ValueError("assistant coach post-foul marker slots must be unique")
        return self


class ScoreEvent(StrictModel):
    sequence: int = Field(ge=1)
    team: TeamSide
    period: int = Field(ge=1, le=8)
    # Recognition may locate an outer jersey number before it can determine the
    # scoring value. Values above three are accepted only so imported/legacy
    # drafts remain inspectable; deterministic validation rejects them.
    points: int | None = Field(default=None, ge=1)
    cumulative_score: int = Field(ge=1, le=160)
    scorer_jersey: str
    mark: ScoreMark | None = None
    scorer_circled: bool = False
    boundary: ScoreBoundary = ScoreBoundary.NONE
    ink_role: InkRole = InkRole.NEUTRAL

    @field_validator("scorer_jersey")
    @classmethod
    def validate_scorer_jersey(cls, value: str) -> str:
        value = value.strip()
        if not value:
            return value
        if not (
            value in {"0", "00"}
            or (value.isdigit() and not value.startswith("0") and 1 <= int(value) <= 99)
        ):
            raise ValueError("scorer_jersey must be a valid jersey number")
        return value


class PeriodScore(StrictModel):
    period: int = Field(ge=1, le=8)
    team_a: int = Field(ge=0, le=160)
    team_b: int = Field(ge=0, le=160)


class FinalScore(StrictModel):
    team_a: int = Field(default=0, ge=0, le=160)
    team_b: int = Field(default=0, ge=0, le=160)
    winner_name: str = ""
    ended_at: str = ""


class OfficialEntry(StrictModel):
    role: Literal[
        "scorer",
        "assistant_scorer",
        "timer",
        "shot_clock_operator",
        "crew_chief",
        "umpire_1",
        "umpire_2",
        "protest_captain",
    ]
    name: str = ""
    signature: SignaturePresence = SignaturePresence.ABSENT


class SourceAsset(StrictModel):
    original_filename: str = ""
    original_url: str = ""
    aligned_url: str = ""
    version: int = Field(default=0, ge=0)
    content_sha256: str = ""
    width: int = 0
    height: int = 0
    rotation: int = 0
    corners: list[list[float]] | None = None


class PriorTeam(StrictModel):
    team_id: str
    name: str
    player_names: list[str]


class GamePriorSnapshot(StrictModel):
    game_id: str
    competition: str
    division: str
    date: str
    scheduled_time: str
    venue: str
    team_a: PriorTeam
    team_b: PriorTeam
    source_hash: str
    locked_paths: list[str] = Field(default_factory=list)


class RecognitionIssue(StrictModel):
    code: str
    path: str
    message: str
    observed: Any | None = None
    expected: Any | None = None


class RecognitionDocumentState(StrictModel):
    run_id: str
    notes: str = ""
    table_personnel: list[str] = Field(default_factory=list)
    problem_paths: list[str] = Field(default_factory=list)
    issues: list[RecognitionIssue] = Field(default_factory=list)
    applied_at: datetime = Field(default_factory=utc_now)


class ScoresheetDocument(StrictModel):
    schema_version: Literal["1.0.0", "1.1.0", "1.2.0", "1.3.0", "1.4.0"] = "1.4.0"
    rules_profile: RuleProfileId = RuleProfileId.FIBA_2024
    id: str
    revision: int = Field(default=0, ge=0)
    template_id: str = "pku-basketball-2019-v1"
    status: DocumentStatus = DocumentStatus.DRAFT
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    source: SourceAsset = Field(default_factory=SourceAsset)
    game_prior: GamePriorSnapshot | None = None
    recognition: RecognitionDocumentState | None = None
    header: Header = Field(default_factory=Header)
    teams: list[TeamEntry]
    score_events: list[ScoreEvent] = Field(default_factory=list)
    stated_period_scores: list[PeriodScore] = Field(default_factory=list)
    final_score: FinalScore = Field(default_factory=FinalScore)
    officials: list[OfficialEntry] = Field(default_factory=list)
    acknowledged_warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def exactly_two_teams(self) -> ScoresheetDocument:
        sides = [team.side for team in self.teams]
        if sorted(sides) != [TeamSide.A, TeamSide.B]:
            raise ValueError("document must contain exactly one team A and one team B")
        sequences = [event.sequence for event in self.score_events]
        if len(sequences) != len(set(sequences)):
            raise ValueError("score event sequence values must be unique")
        return self


class ValidationIssue(StrictModel):
    code: str
    severity: ValidationSeverity
    paths: list[str]
    message: str
    observed: Any | None = None
    expected: Any | None = None


class ValidationReport(StrictModel):
    status: ValidationStatus
    issues: list[ValidationIssue]
    checked_at: datetime = Field(default_factory=utc_now)


class DocumentUpdate(StrictModel):
    base_revision: int = Field(ge=0)
    document: ScoresheetDocument
    source: Literal["human", "undo", "redo"] = "human"


class AlignmentRequest(StrictModel):
    base_revision: int = Field(ge=0)
    rotation: Literal[0, 90, 180, 270] = 0
    corners: list[list[float]] | None = None

    @field_validator("corners")
    @classmethod
    def validate_corners(cls, value: list[list[float]] | None) -> list[list[float]] | None:
        if value is None:
            return value
        if len(value) != 4 or any(len(point) != 2 for point in value):
            raise ValueError("corners must contain four [x, y] points")
        if any(coordinate < 0 or coordinate > 1 for point in value for coordinate in point):
            raise ValueError("corner coordinates must be normalized to 0..1")
        return value


ChangeLogAction = Literal[
    "human_edit",
    "undo",
    "redo",
    "recognition_merge",
    "reupload",
    "confirm",
]


class FieldChange(StrictModel):
    path: str
    before: Any | None = None
    after: Any | None = None


class DocumentChangeLogEntry(StrictModel):
    id: int
    document_id: str
    action: ChangeLogAction
    summary: str
    changes: list[FieldChange] = Field(default_factory=list)
    created_at: datetime


class DocumentChangeLogPage(StrictModel):
    items: list[DocumentChangeLogEntry]
    next_before_id: int | None = None


class ConfirmRequest(StrictModel):
    base_revision: int = Field(ge=0)
    acknowledge_warning_codes: list[str] = Field(default_factory=list)


class ValidationRequest(StrictModel):
    base_revision: int = Field(ge=0)


class GameSummary(StrictModel):
    id: str
    competition: str
    division: str
    date: str
    scheduled_time: str
    venue: str
    team_a_name: str
    team_b_name: str
    ready: bool
    unavailable_reason: str = ""
    document_id: str | None = None
    scoresheet_state: Literal[
        "not_uploaded",
        "recognizing",
        "recognized",
        "recognition_failed",
        "confirmed",
    ] = "not_uploaded"


class GameDetail(GameSummary):
    prior: GamePriorSnapshot | None = None


class RecognitionUsage(StrictModel):
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    image_tokens: int = Field(default=0, ge=0)
    reasoning_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)


class RecognitionRun(StrictModel):
    id: str
    document_id: str
    base_revision: int = Field(ge=0)
    status: Literal[
        "pending",
        "connecting",
        "thinking",
        "structuring",
        "validating",
        "succeeded",
        "failed",
        "superseded",
        "interrupted",
    ]
    model: str
    prompt_version: str
    trigger: Literal["upload", "reupload", "retry", "manual", "legacy"] = "legacy"
    source_version: int = Field(default=0, ge=0)
    image_sha256: str = ""
    superseded_by_run_id: str | None = None
    retry_count: int = Field(default=0, ge=0)
    cached: bool = False
    auto_applied: bool = False
    applied_revision: int | None = None
    recognition_notes: str = ""
    usage: RecognitionUsage = Field(default_factory=RecognitionUsage)
    error: str = ""
    result: dict[str, Any] | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class RecognitionRequest(StrictModel):
    base_revision: int = Field(ge=0)


class DocumentRecognitionResponse(StrictModel):
    document: ScoresheetDocument
    recognition_run: RecognitionRun


class RecognitionRegionDiff(StrictModel):
    region: str
    label: str
    changed: bool
    current: Any
    recognized: Any


class RecognitionDiff(StrictModel):
    run_id: str
    document_id: str
    base_revision: int
    current_revision: int
    regions: list[RecognitionRegionDiff]


class RecognitionApplyRequest(StrictModel):
    base_revision: int = Field(ge=0)
    regions: list[str] = Field(min_length=1)
