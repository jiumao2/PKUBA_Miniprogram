from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from .models import (
    ScoreMark,
    ScoresheetDocument,
    TeamSide,
    ValidationIssue,
    ValidationReport,
    ValidationSeverity,
    ValidationStatus,
)
from .scoring import period_checkpoints


def _issue(
    code: str,
    severity: ValidationSeverity,
    paths: list[str],
    message: str,
    observed: object | None = None,
    expected: object | None = None,
) -> ValidationIssue:
    return ValidationIssue(
        code=code,
        severity=severity,
        paths=paths,
        message=message,
        observed=observed,
        expected=expected,
    )


def _valid_jersey(value: str) -> bool:
    if not value:
        return True
    return value in {"0", "00"} or (
        value.isdigit() and not value.startswith("0") and 1 <= int(value) <= 99
    )


def _has_slot_gap(entries: list) -> bool:
    slots = sorted(entry.slot for entry in entries)
    return bool(slots) and slots != list(range(1, slots[-1] + 1))


def _foul_suffix(entry) -> str:
    if entry.free_throws is not None:
        return str(entry.free_throws)
    return "c" if entry.cancelled else ""


def describe_recognition_problem(document: ScoresheetDocument, path: str) -> str:
    """Turn an internal recognition path into an actionable, human-readable warning."""
    teams = {team.side: team for team in document.teams}

    player = re.fullmatch(r"/teams/(0|1)/players/row/(\d+)(?:/([^/]+))?", path)
    if player:
        side = TeamSide.A if player.group(1) == "0" else TeamSide.B
        row = int(player.group(2))
        field = {
            "name": "姓名",
            "jersey_number": "球衣号码",
            "participation": "参赛标记",
            "fouls": "犯规",
        }.get(player.group(3) or "", "内容")
        return f"{side} 队第 {row} 行球员的{field}未能从图片中可靠确定，需要人工核对。"

    legacy_player = re.fullmatch(r"/teams/(0|1)/players/(\d+)(?:/([^/]+))?", path)
    if legacy_player:
        side = TeamSide.A if legacy_player.group(1) == "0" else TeamSide.B
        team = teams[side]
        index = int(legacy_player.group(2))
        row = team.players[index].row if index < len(team.players) else index + 1
        field = {
            "name": "姓名",
            "jersey_number": "球衣号码",
            "participation": "参赛标记",
            "fouls": "犯规",
        }.get(legacy_player.group(3) or "", "内容")
        return f"{side} 队第 {row} 行球员的{field}未能从图片中可靠确定，需要人工核对。"

    coach = re.fullmatch(r"/teams/(0|1)/(head_coach|assistant_coach)(?:/([^/]+))?", path)
    if coach:
        side = TeamSide.A if coach.group(1) == "0" else TeamSide.B
        role = "教练员" if coach.group(2) == "head_coach" else "助理教练员"
        field = "犯规" if coach.group(3) == "fouls" else "姓名"
        return f"{side} 队{role}{field}未能从图片中可靠确定，需要人工核对。"

    score = re.fullmatch(
        r"/score_events/(A|B)/cumulative/(\d+)(?:/(delta|points|scorer_jersey|period))?",
        path,
    )
    if score:
        side = score.group(1)
        cumulative = int(score.group(2))
        detail = score.group(3)
        if detail == "delta":
            return (
                f"{side} 队累计 {cumulative} 分候选事件与上一项的分差不是 1、2 或 3，需要人工核对。"
            )
        if detail == "points":
            return f"{side} 队累计 {cumulative} 分的本次得分值与累计分差值不一致，需要人工核对。"
        if detail == "scorer_jersey":
            return f"{side} 队累计 {cumulative} 分的得分号码未能从图片中可靠确定。"
        if detail == "period":
            return f"{side} 队累计 {cumulative} 分的所属节次未能可靠确定，需要人工核对。"
        return f"{side} 队累计 {cumulative} 分的得分事件未能可靠确定，需要人工核对。"

    period_end = re.fullmatch(r"/score_events/(A|B)/period/(\d+)/boundary", path)
    legacy_period_end = re.fullmatch(r"/score_events/(A|B)/period_(\d+)_end", path)
    matched_period_end = period_end or legacy_period_end
    if matched_period_end:
        side = matched_period_end.group(1)
        period = int(matched_period_end.group(2))
        label = f"第 {period} 节" if period <= 4 else "决胜期"
        return f"{side} 队{label}结束累计分未能与书面节比分对应，需要人工核对。"

    legacy_score = re.fullmatch(r"/score_events/(A|B)/(\d+)(?:/([^/]+))?", path)
    if legacy_score:
        side = legacy_score.group(1)
        position = int(legacy_score.group(2)) + 1
        detail = "得分号码" if legacy_score.group(3) == "scorer_jersey" else "内容"
        return (
            f"{side} 队第 {position} 个旧识别得分候选项的{detail}未能可靠确定；"
            "旧结果未保存更稳定的累计分位置，需要人工定位核对。"
        )

    period_score = re.fullmatch(r"/stated_period_scores/(\d+)(?:/(A|B))?", path)
    if period_score:
        period = int(period_score.group(1))
        side = f" {period_score.group(2)} 队" if period_score.group(2) else ""
        label = f"第 {period} 节" if period <= 4 else "决胜期"
        return f"{label}{side}书面得分未能从图片中可靠确定，需要人工核对。"

    final_field = re.fullmatch(r"/final_score/(team_a|team_b|winner_name|ended_at)", path)
    if final_field:
        label = {
            "team_a": "A 队最终比分",
            "team_b": "B 队最终比分",
            "winner_name": "胜队名称",
            "ended_at": "比赛结束时间",
        }[final_field.group(1)]
        return f"{label}未能从图片中可靠确定，需要人工核对。"

    official = re.fullmatch(r"/officials/([^/]+)/name", path)
    if official:
        return f"{official.group(1)}对应的裁判员姓名未能从图片中可靠确定，需要人工核对。"

    return f"识别结果中的字段 {path} 未能从图片中可靠确定，需要人工核对。"


def validate_document(
    document: ScoresheetDocument,
    rule_profiles_path: Path,
) -> ValidationReport:
    issues: list[ValidationIssue] = []
    profiles = json.loads(rule_profiles_path.read_text(encoding="utf-8"))
    profile = profiles[document.rules_profile.value]
    counted_player_foul_codes = {
        marking["code"]
        for marking in profile["foul_markings"]
        if "player" in marking.get("editor_groups", [])
    }
    allowed_foul_markings: dict[str, set[tuple[str, str, str]]] = defaultdict(set)
    for marking in profile["foul_markings"]:
        for subject in marking.get("subjects", []):
            for suffix in marking.get("allowed_suffixes", [""]):
                allowed_foul_markings[subject].add(
                    (marking["code"], marking.get("style", "plain"), suffix)
                )
    teams = {team.side: team for team in document.teams}

    def validate_foul_entries(entries: list, subject: str, path: str) -> None:
        allowed = allowed_foul_markings[subject]
        for index, entry in enumerate(entries):
            signature = (entry.code.value, entry.mark_style.value, _foul_suffix(entry))
            if signature in allowed:
                continue
            issues.append(
                _issue(
                    "FOUL_MARKING_NOT_IN_RULE_PROFILE",
                    ValidationSeverity.ERROR,
                    [f"{path}/{index}"],
                    (
                        f"该犯规写法不适用于当前对象或 {profile['label']} 规则；"
                        "请在对应的队员、教练员或附加列选项中重新选择。"
                    ),
                    observed={
                        "code": entry.code,
                        "style": entry.mark_style,
                        "suffix": _foul_suffix(entry),
                        "subject": subject,
                    },
                    expected=sorted(f"{code}{suffix}:{style}" for code, style, suffix in allowed),
                )
            )

    if document.recognition is not None:
        for path in document.recognition.problem_paths:
            issues.append(
                _issue(
                    "RECOGNITION_REVIEW_REQUIRED",
                    ValidationSeverity.WARNING,
                    [path],
                    describe_recognition_problem(document, path),
                )
            )
        for recognition_issue in document.recognition.issues:
            issues.append(
                _issue(
                    recognition_issue.code,
                    ValidationSeverity.WARNING,
                    [recognition_issue.path],
                    recognition_issue.message,
                    observed=recognition_issue.observed,
                    expected=recognition_issue.expected,
                )
            )
        if document.recognition.notes.strip():
            issues.append(
                _issue(
                    "RECOGNITION_NOTES",
                    ValidationSeverity.WARNING,
                    ["/recognition/notes"],
                    f"模型备注：{document.recognition.notes.strip()}",
                )
            )

    for side in (TeamSide.A, TeamSide.B):
        team = teams[side]
        team_index = 0 if side == TeamSide.A else 1
        if not team.name.strip():
            issues.append(
                _issue(
                    "MISSING_TEAM_NAME",
                    ValidationSeverity.ERROR,
                    [f"/teams/{team_index}/name"],
                    f"{side} 队名称不能为空。",
                )
            )

        jerseys = [player.jersey_number for player in team.players if player.jersey_number]
        if len(jerseys) < 5:
            issues.append(
                _issue(
                    "MISSING_ROSTER",
                    ValidationSeverity.ERROR,
                    [f"/teams/{team_index}/players"],
                    f"{side} 队至少需要 5 名有号码的队员。",
                    observed=len(jerseys),
                    expected=">= 5",
                )
            )
        duplicates = sorted(number for number, count in Counter(jerseys).items() if count > 1)
        if duplicates:
            issues.append(
                _issue(
                    "DUPLICATE_JERSEY",
                    ValidationSeverity.ERROR,
                    [f"/teams/{team_index}/players"],
                    f"{side} 队存在重复号码：{', '.join(duplicates)}。",
                    observed=duplicates,
                )
            )

        for player_index, player in enumerate(team.players):
            if not _valid_jersey(player.jersey_number):
                issues.append(
                    _issue(
                        "INVALID_JERSEY",
                        ValidationSeverity.ERROR,
                        [f"/teams/{team_index}/players/{player_index}/jersey_number"],
                        f"{side} 队第 {player.row} 行号码必须为 0、00 或 1–99。",
                        observed=player.jersey_number,
                        expected="0, 00, or 1-99",
                    )
                )
            if player.jersey_number and not player.name.strip():
                issues.append(
                    _issue(
                        "MISSING_PLAYER_NAME",
                        ValidationSeverity.WARNING,
                        [f"/teams/{team_index}/players/{player_index}/name"],
                        f"{side} 队 {player.jersey_number} 号尚未填写姓名。",
                    )
                )
            counted_fouls = sum(
                foul.code.value in counted_player_foul_codes for foul in player.fouls
            )
            if counted_fouls > 5:
                issues.append(
                    _issue(
                        "FOUL_LIMIT_EXCEEDED",
                        ValidationSeverity.ERROR,
                        [f"/teams/{team_index}/players/{player_index}/fouls"],
                        f"{side} 队 {player.jersey_number or player.name} 的计数犯规超过 5 次。",
                        observed=counted_fouls,
                        expected="<= 5",
                    )
                )
            if _has_slot_gap(player.fouls):
                issues.append(
                    _issue(
                        "FOUL_SLOT_GAP",
                        ValidationSeverity.ERROR,
                        [f"/teams/{team_index}/players/{player_index}/fouls"],
                        (
                            f"{side} 队 {player.jersey_number or player.name} "
                            "的犯规格必须从第 1 格连续填写。"
                        ),
                        observed=sorted(foul.slot for foul in player.fouls),
                    )
                )
            if _has_slot_gap(player.post_foul_markers):
                issues.append(
                    _issue(
                        "POST_FOUL_SLOT_GAP",
                        ValidationSeverity.ERROR,
                        [f"/teams/{team_index}/players/{player_index}/post_foul_markers"],
                        "第五格后的附加标记必须连续填写。",
                        observed=sorted(marker.slot for marker in player.post_foul_markers),
                    )
                )
            if player.post_foul_markers and not any(foul.slot == 5 for foul in player.fouls):
                issues.append(
                    _issue(
                        "POST_FOUL_WITHOUT_LAST_CELL",
                        ValidationSeverity.ERROR,
                        [f"/teams/{team_index}/players/{player_index}/post_foul_markers"],
                        "只有第 5 个正式犯规格已填写后，才可使用其后的假想列。",
                    )
                )
            validate_foul_entries(
                player.fouls,
                "player",
                f"/teams/{team_index}/players/{player_index}/fouls",
            )
            validate_foul_entries(
                player.post_foul_markers,
                "post_foul",
                f"/teams/{team_index}/players/{player_index}/post_foul_markers",
            )

        starters = sum(player.participation == "starter" for player in team.players)
        if starters != 5:
            issues.append(
                _issue(
                    "STARTER_COUNT_MISMATCH",
                    ValidationSeverity.WARNING,
                    [f"/teams/{team_index}/players"],
                    f"{side} 队应标记 5 名首发队员。",
                    observed=starters,
                    expected=5,
                )
            )
        if jerseys and not any(player.captain for player in team.players):
            issues.append(
                _issue(
                    "MISSING_CAPTAIN",
                    ValidationSeverity.WARNING,
                    [f"/teams/{team_index}/players"],
                    f"{side} 队尚未标记队长。",
                )
            )

        if _has_slot_gap(team.coach_fouls):
            issues.append(
                _issue(
                    "COACH_FOUL_SLOT_GAP",
                    ValidationSeverity.ERROR,
                    [f"/teams/{team_index}/coach_fouls"],
                    f"{side} 队教练员的 3 个正式犯规格必须从第 1 格连续填写。",
                    observed=sorted(foul.slot for foul in team.coach_fouls),
                )
            )
        if _has_slot_gap(team.coach_post_foul_markers):
            issues.append(
                _issue(
                    "COACH_POST_FOUL_SLOT_GAP",
                    ValidationSeverity.ERROR,
                    [f"/teams/{team_index}/coach_post_foul_markers"],
                    "教练员第 3 格后的附加标记必须连续填写。",
                )
            )
        if team.coach_post_foul_markers and not any(foul.slot == 3 for foul in team.coach_fouls):
            issues.append(
                _issue(
                    "COACH_POST_FOUL_WITHOUT_LAST_CELL",
                    ValidationSeverity.ERROR,
                    [f"/teams/{team_index}/coach_post_foul_markers"],
                    "只有教练员第 3 个正式犯规格已填写后，才可使用其后的附加列。",
                )
            )
        validate_foul_entries(
            team.coach_fouls,
            "head_coach",
            f"/teams/{team_index}/coach_fouls",
        )
        validate_foul_entries(
            team.coach_post_foul_markers,
            "post_foul",
            f"/teams/{team_index}/coach_post_foul_markers",
        )

        assistant_fouls = sorted(team.assistant_coach_fouls, key=lambda foul: foul.slot)
        if _has_slot_gap(assistant_fouls):
            issues.append(
                _issue(
                    "ASSISTANT_COACH_FOUL_SLOT_GAP",
                    ValidationSeverity.ERROR,
                    [f"/teams/{team_index}/assistant_coach_fouls"],
                    "助理教练员的 3 个犯规格必须从第 1 格连续填写。",
                    observed=sorted(foul.slot for foul in assistant_fouls),
                )
            )
        assistant_post = team.assistant_coach_post_foul_markers
        if _has_slot_gap(assistant_post):
            issues.append(
                _issue(
                    "ASSISTANT_COACH_POST_FOUL_SLOT_GAP",
                    ValidationSeverity.ERROR,
                    [f"/teams/{team_index}/assistant_coach_post_foul_markers"],
                    "助理教练员第 3 格后的附加标记必须连续填写。",
                )
            )
        if assistant_post and not any(foul.slot == 3 for foul in assistant_fouls):
            issues.append(
                _issue(
                    "ASSISTANT_COACH_POST_FOUL_WITHOUT_LAST_CELL",
                    ValidationSeverity.ERROR,
                    [f"/teams/{team_index}/assistant_coach_post_foul_markers"],
                    "只有助理教练员第 3 个正式犯规格已填写后，才可使用其后的附加列。",
                )
            )
        validate_foul_entries(
            assistant_fouls,
            "assistant_coach",
            f"/teams/{team_index}/assistant_coach_fouls",
        )
        validate_foul_entries(
            assistant_post,
            "post_foul",
            f"/teams/{team_index}/assistant_coach_post_foul_markers",
        )

    for key, field, label, code in (
        ("competition", document.header.competition, "竞赛名称", "MISSING_COMPETITION"),
        ("date", document.header.date, "日期", "MISSING_DATE"),
        ("venue", document.header.venue, "地点", "MISSING_VENUE"),
        ("scheduled_time", document.header.scheduled_time, "计划时间", "MISSING_SCHEDULED_TIME"),
    ):
        if not field.strip():
            issues.append(
                _issue(
                    code,
                    ValidationSeverity.WARNING,
                    [f"/header/{key}"],
                    f"{label}尚未填写。",
                )
            )

    if not document.score_events:
        issues.append(
            _issue(
                "MISSING_SCORE_EVENTS",
                ValidationSeverity.ERROR,
                ["/score_events"],
                "尚未录入任何逐次得分事件。",
            )
        )
    cell_counts = Counter((event.team, event.cumulative_score) for event in document.score_events)
    duplicate_cells = [cell for cell, count in cell_counts.items() if count > 1]
    for side, cumulative in duplicate_cells:
        issues.append(
            _issue(
                "DUPLICATE_SCORE_CELL",
                ValidationSeverity.ERROR,
                [f"/score_events/{side.value}/cumulative/{cumulative}"],
                f"{side.value} 队累计 {cumulative} 分格存在多个得分事件。",
                observed=cell_counts[(side, cumulative)],
                expected=1,
            )
        )
    if not document.final_score.ended_at.strip():
        issues.append(
            _issue(
                "MISSING_END_TIME",
                ValidationSeverity.WARNING,
                ["/final_score/ended_at"],
                "比赛结束时间尚未填写。",
            )
        )

    indexed_events = sorted(
        enumerate(document.score_events),
        key=lambda item: item[1].sequence,
    )
    observed_sequences = [event.sequence for _, event in indexed_events]
    expected_sequences = list(range(1, len(indexed_events) + 1))
    if observed_sequences != expected_sequences:
        issues.append(
            _issue(
                "SCORE_EVENT_SEQUENCE_GAP",
                ValidationSeverity.ERROR,
                ["/score_events"],
                "逐次得分事件序号必须从 1 开始连续递增。",
                observed=observed_sequences,
                expected=expected_sequences,
            )
        )

    previous_period = 0
    for event_index, event in indexed_events:
        if event.period < previous_period:
            issues.append(
                _issue(
                    "SCORE_PERIOD_ORDER",
                    ValidationSeverity.ERROR,
                    [f"/score_events/{event_index}/period", "/score_events"],
                    "逐次得分节次发生倒退；事件必须按第 1 节至决胜期顺序排列。",
                    observed=event.period,
                    expected=f">= {previous_period}",
                )
            )
        previous_period = event.period

    events_by_team: dict[TeamSide, list] = defaultdict(list)
    for _, event in indexed_events:
        events_by_team[event.team].append(event)
    for events in events_by_team.values():
        events.sort(key=lambda event: (event.cumulative_score, event.sequence))

    checkpoints_by_side = {
        side: period_checkpoints(document, side) for side in (TeamSide.A, TeamSide.B)
    }

    period_totals: dict[int, dict[TeamSide, int]] = defaultdict(
        lambda: {TeamSide.A: 0, TeamSide.B: 0}
    )
    computed_final = {TeamSide.A: 0, TeamSide.B: 0}

    for side in (TeamSide.A, TeamSide.B):
        team = teams[side]
        team_index = 0 if side == TeamSide.A else 1
        roster = {player.jersey_number for player in team.players if player.jersey_number}
        previous = 0
        for event in events_by_team[side]:
            event_index = document.score_events.index(event)
            delta = event.cumulative_score - previous
            valid_points = event.points in {1, 2, 3}
            valid_delta = delta in {1, 2, 3}
            if event.points is None:
                issues.append(
                    _issue(
                        "UNRESOLVED_SCORE_POINTS",
                        ValidationSeverity.WARNING,
                        [f"/score_events/{event_index}/points"],
                        f"{side} 队累计 {event.cumulative_score} 分的本次得分仍待确定。",
                        observed=None,
                        expected=[1, 2, 3],
                    )
                )
            elif not valid_points or (valid_delta and event.points != delta):
                issues.append(
                    _issue(
                        "INVALID_SCORE_POINTS",
                        ValidationSeverity.ERROR,
                        [f"/score_events/{event_index}/points"],
                        "本次得分必须是1、2或3，并等于相邻固定累积分格的差值。",
                        observed=event.points,
                        expected=delta if valid_delta else [1, 2, 3],
                    )
                )
            if not valid_delta:
                issues.append(
                    _issue(
                        "SCORE_SEQUENCE_GAP",
                        ValidationSeverity.ERROR,
                        [f"/score_events/{event_index}/cumulative_score"],
                        (
                            f"{side} 队本次累计分与上一项相差 {delta} 分；"
                            "相邻已填写格之间只能派生1、2或3分。"
                        ),
                        observed=event.cumulative_score,
                        expected=f"{previous + 1}至{previous + 3}",
                    )
                )
            crossed = [
                (period, checkpoint)
                for period, checkpoint in checkpoints_by_side[side]
                if previous < checkpoint < event.cumulative_score
            ]
            if crossed:
                issues.append(
                    _issue(
                        "SCORE_EVENT_CROSSES_PERIOD_BOUNDARY",
                        ValidationSeverity.ERROR,
                        [
                            f"/score_events/{event_index}/cumulative_score",
                            "/stated_period_scores",
                        ],
                        (
                            f"{side.value} 队从累计 {previous} 分到 {event.cumulative_score} 分的"
                            "派生事件跨过了书面节末检查点。"
                        ),
                        observed={"from": previous, "to": event.cumulative_score},
                        expected=[
                            {"period": period, "checkpoint": checkpoint}
                            for period, checkpoint in crossed
                        ],
                    )
                )
            previous = event.cumulative_score
            if valid_delta:
                period_totals[event.period][side] += delta

            if not event.scorer_jersey:
                issues.append(
                    _issue(
                        "MISSING_SCORER",
                        ValidationSeverity.ERROR,
                        [f"/score_events/{event_index}/scorer_jersey"],
                        f"{side} 队累计 {event.cumulative_score} 分尚未填写得分号码。",
                    )
                )
            elif event.scorer_jersey not in roster:
                issues.append(
                    _issue(
                        "UNKNOWN_SCORER",
                        ValidationSeverity.ERROR,
                        [
                            f"/score_events/{event_index}/scorer_jersey",
                            f"/teams/{team_index}/players",
                        ],
                        f"得分号码 {event.scorer_jersey} 不在 {side} 队名单中。",
                        observed=event.scorer_jersey,
                    )
                )

            mark_ok = (
                event.points is None
                and event.mark is None
                and not event.scorer_circled
                or (
                    (
                        event.points == 1
                        and event.mark == ScoreMark.FILLED_DOT
                        and not event.scorer_circled
                    )
                    or (
                        event.points == 2
                        and event.mark == ScoreMark.DIAGONAL
                        and not event.scorer_circled
                    )
                    or (
                        event.points == 3
                        and event.mark == ScoreMark.DIAGONAL
                        and event.scorer_circled
                    )
                )
            )
            if valid_points and not mark_ok:
                issues.append(
                    _issue(
                        "SCORE_MARK_DELTA_MISMATCH",
                        ValidationSeverity.ERROR,
                        [
                            f"/score_events/{event_index}/points",
                            f"/score_events/{event_index}/mark",
                            f"/score_events/{event_index}/scorer_circled",
                        ],
                        "得分分值与黑点、斜杠或三分圈标记不一致。",
                        observed={
                            "points": event.points,
                            "mark": event.mark,
                            "scorer_circled": event.scorer_circled,
                        },
                    )
                )
        computed_final[side] = previous

    period_counts = Counter(score.period for score in document.stated_period_scores)
    duplicate_periods = sorted(period for period, count in period_counts.items() if count > 1)
    if duplicate_periods:
        duplicate_paths = [
            f"/stated_period_scores/{index}"
            for index, score in enumerate(document.stated_period_scores)
            if score.period in duplicate_periods
        ]
        issues.append(
            _issue(
                "DUPLICATE_PERIOD_SCORE",
                ValidationSeverity.ERROR,
                duplicate_paths,
                "每个节次只能填写一行书面节比分。",
                observed=duplicate_periods,
                expected="unique periods",
            )
        )
    stated_periods = {}
    for score in document.stated_period_scores:
        stated_periods.setdefault(score.period, score)
    periods_to_check = sorted({1, 2, 3, 4} | set(period_totals) | set(stated_periods))
    for period in periods_to_check:
        totals = period_totals[period]
        stated = stated_periods.get(period)
        if stated is None:
            issues.append(
                _issue(
                    "MISSING_PERIOD_SCORE",
                    ValidationSeverity.ERROR,
                    [f"/stated_period_scores/{period}"],
                    f"第 {period} 节的书面节比分尚未填写，无法核对累计分。",
                )
            )
            continue
        expected = {"team_a": totals[TeamSide.A], "team_b": totals[TeamSide.B]}
        observed = {"team_a": stated.team_a, "team_b": stated.team_b}
        if observed != expected:
            issues.append(
                _issue(
                    "PERIOD_SCORE_MISMATCH",
                    ValidationSeverity.ERROR,
                    [
                        f"/stated_period_scores/{document.stated_period_scores.index(stated)}",
                        "/score_events",
                    ],
                    f"第 {period} 节书面比分与逐次得分不一致。",
                    observed=observed,
                    expected=expected,
                )
            )

        for side in (TeamSide.A, TeamSide.B):
            checkpoint = dict(checkpoints_by_side[side]).get(period)
            if checkpoint is None or checkpoint <= 0:
                continue
            checkpoint_events = [
                event for event in events_by_team[side] if event.cumulative_score == checkpoint
            ]
            if not checkpoint_events:
                issues.append(
                    _issue(
                        "PERIOD_BOUNDARY_WITHOUT_EVENT",
                        ValidationSeverity.ERROR,
                        [
                            f"/score_events/{side.value}/period/{period}/boundary",
                            f"/stated_period_scores/{period}",
                        ],
                        (
                            f"{side.value} 队第 {period} 节书面累计检查点为 {checkpoint} 分，"
                            "但该累积分格没有得分事件，不能生成节末标记。"
                        ),
                        observed=checkpoint,
                        expected="该累积分格存在号码或待识别事件",
                    )
                )

    stated_sum = {
        TeamSide.A: sum(score.team_a for score in document.stated_period_scores),
        TeamSide.B: sum(score.team_b for score in document.stated_period_scores),
    }
    final_observed = {
        TeamSide.A: document.final_score.team_a,
        TeamSide.B: document.final_score.team_b,
    }
    if final_observed != computed_final:
        issues.append(
            _issue(
                "FINAL_SCORE_MISMATCH",
                ValidationSeverity.ERROR,
                ["/final_score", "/score_events"],
                "书面最终比分与累计分最后结果不一致。",
                observed={key.value: value for key, value in final_observed.items()},
                expected={key.value: value for key, value in computed_final.items()},
            )
        )
    if final_observed != stated_sum:
        issues.append(
            _issue(
                "PERIOD_SUM_MISMATCH",
                ValidationSeverity.ERROR,
                ["/final_score", "/stated_period_scores"],
                "各节比分之和与书面最终比分不一致。",
                observed={key.value: value for key, value in final_observed.items()},
                expected={key.value: value for key, value in stated_sum.items()},
            )
        )

    if document.game_prior is not None:
        canonical_names = {
            TeamSide.A: document.game_prior.team_a.name,
            TeamSide.B: document.game_prior.team_b.name,
        }
    else:
        canonical_names = {
            TeamSide.A: teams[TeamSide.A].name,
            TeamSide.B: teams[TeamSide.B].name,
        }

    expected_winner = ""
    if document.final_score.team_a > document.final_score.team_b:
        expected_winner = canonical_names[TeamSide.A]
    elif document.final_score.team_b > document.final_score.team_a:
        expected_winner = canonical_names[TeamSide.B]
    else:
        issues.append(
            _issue(
                "TIED_FINAL_SCORE",
                ValidationSeverity.ERROR,
                ["/final_score/team_a", "/final_score/team_b"],
                "篮球比赛终场不能为平分；请检查最终比分和可能遗漏的决胜期记录。",
                observed={
                    "team_a": document.final_score.team_a,
                    "team_b": document.final_score.team_b,
                },
                expected="两队最终比分不同",
            )
        )
    if expected_winner and document.final_score.winner_name != expected_winner:
        issues.append(
            _issue(
                "WINNER_MISMATCH",
                ValidationSeverity.ERROR,
                ["/final_score/winner_name"],
                "胜队必须是最终比分更高一队的主数据标准名称。",
                observed=document.final_score.winner_name,
                expected=expected_winner,
            )
        )

    if any(issue.severity == ValidationSeverity.ERROR for issue in issues):
        status = ValidationStatus.INVALID
    elif any(issue.severity == ValidationSeverity.WARNING for issue in issues):
        status = ValidationStatus.NEEDS_REVIEW
    else:
        status = ValidationStatus.VALID
    return ValidationReport(status=status, issues=issues)
