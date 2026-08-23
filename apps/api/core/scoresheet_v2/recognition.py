from __future__ import annotations

# ruff: noqa: E501 -- the frozen ScoresheetReader prompt preserves exact line breaks.
import base64
import copy
import hashlib
import io
import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Self

from PIL import Image, ImageOps
from pydantic import BaseModel, ConfigDict, Field, create_model, model_validator

from .models import (
    DocumentStatus,
    FoulCode,
    FoulEntry,
    FoulMarkStyle,
    GamePriorSnapshot,
    InkRole,
    OfficialEntry,
    PeriodScore,
    PlayerEntry,
    PostFoulMarker,
    RecognitionDocumentState,
    RecognitionIssue,
    RuleProfileId,
    ScoreBoundary,
    ScoreEvent,
    ScoreMark,
    ScoresheetDocument,
    SignaturePresence,
    TeamEntry,
    TeamFoulPeriod,
    TeamSide,
    TimeoutEntry,
)

PROMPT_VERSION = "scoresheet-2026-08-20-v24-cn"
QWEN_DATA_URI_MAX_BYTES = 20_000_000
QWEN_WEBP_MAX_LONG_EDGE = 3840
QWEN_WEBP_MAX_SHORT_EDGE = 2160
JPEG_MIN_QUALITY = 1
JPEG_MAX_QUALITY = 95

SYSTEM_PROMPT = """【任务与输出边界】
你是FIBA 2024纸质手写篮球记录表转录器，严格输出符合给定JSON Schema的JSON。只输出实际填写的球员行和事件，空白行不要输出。无法可靠转录
的可空字段返回null，并在recognition_notes中用简短中文指出具体位置；没有问题时写空字符串。不要输出Schema以外字段。图片中的纸是篮球比赛的记录表，请你先定位记录表纸张的区域，总体看一下。记录表可以分为以下几个区域：
（1）最上方的表头，包含比赛的时间、地点、场次等元信息。
（2）左侧的球队登记区，包含A队和B队的球员名字、号码、是否上场、犯规次数以及犯规类型，以及教练的信息。
（3）每个球队登记区的上方有双方球队暂停使用的时间、每节犯规数量等信息。
（4）右侧为累积分区域，记录了每个队的每次得分与得分的队员。
（5）左下方为记录台与裁判的签名区。
（6）右下方为得分汇总区，汇总了单节得分与最终比分、胜队、比赛结束时间等。

总体浏览之后，你需要依次逐块放大查看仔细阅读，通过辨认手写字迹获取详细信息。

【球队登记区-姓名和号码】
需要从上到下依次读取队员名字与队员号码，姓名与号码需要左右一一对应，不能有错位的情况，填写完需要复查。
球员姓名只能从所属球队JSON Schema的唯一姓名枚举中选择。先辨认手写字形，再与本队枚举中
的完整姓名逐一比较；只有能唯一对应时才复制枚举原文。A、B队枚举不得混用，同队同一姓名
不得分配给两个球员行。不能唯一对应时返回null，不得猜测或创造近似姓名。教练姓名为自由
文本。球员号码必须从图片读取。

【球队登记区-上场与犯规符号】
队员登记区域中圈住的小x表示首发，普通x表示替补，空白表示未上场（未上场的队员也需要正确登记）。暂停格有三行，分别为上半场、下半场和加时赛的暂停使用情况，并分别有3格、3格和2格。暂停格内数字表示比赛分钟，你需要识别每一格各自中是双横线“=”还是数字；未使用格中的双横线“=”表示该格子未使用，不是暂停。
全队犯规区每一节会有4个格子，按照从左到右的顺序使用。每一格内大X表示该格已使用，双横线“=”表示该格未使用，需要正确区分“X”和“=”这两个符号，注意x只会在一个格子中标记，双横线可能会一笔连续跨越多个格子，可以通过是否连笔来判断符号。你需要统计每一节比赛格子的使用数量（X的数量）。全队犯规区左上、右上、左下、右下依次为第1,2,3,4节的全队犯规格。

队员有5个正式犯规格，教练员和助理教练员各有3个；穿过剩余格的横线表示未使用不是犯规。写在正式格后的附加标记使用后续position表示。
犯规使用简洁ASCII文本：队员犯规格可用P/T/U/D；教练员和助理教练员犯规格可用C/B/D/F；正式格后附加标记可用D/GD/F。P/T/U/D/C/B均可不带后缀、带罚球数字1/2/3，或带抵消标记c；数字与c不能同时出现。Pc中的c与P位于同一基线，不存在P2c。
整行横线后接斜线表示删除剩余空白队员行，不是球员、号码或犯规。

【累积分区-逐次得分读取】
每个40分区块从左到右固定为：A队外侧手写得分号码、A队内侧印刷累计分、B队内侧印刷。一共有4个左右排列紧密排列的40分区块，注意不要混淆不同区块的数据。
累计分、B队外侧手写得分号码；切勿将累计分识别为队员号码，需要重点检查。应该首先识别外侧号码作为得分依据：外侧号码每出现一次就保留为
一次候选得分事件，再读取同一横行的内侧累计分，并使用与上一次登记的外侧得分号码之间的间距、得分符号、累计分差值复核。实心黑点且标注得分队员号码表示1分罚球；斜杠且标注得分队员号码表示2分；斜杠且外侧得分球员号码被圈住表示3分，每一次得分只能是1分、2分或者3分；带圈的累计分与分值无关。圈住球员号码表示三分；圈住内侧累计分并在累计分及球员号码下画粗横线表示该节结束，不得混淆。双横线及剩余区域长斜线表示比赛结束。

【记录台与裁判签名区-记录台区域读取】
记录台区域只把能够辨认的人员姓名写入table_personnel，每项一个人，按首次出现顺序去重，
不分配记录员、助理记录员、计时员或24秒计时员岗位。officials只用于纸面角色明确的主裁判、
两名副裁判和申诉队长，每种role最多输出一次。

【交叉验证——输出前必须逐项执行】
完成初步转录后，必须使用图片中的不同区域对识别结果进行交叉验证，如遇到不一致的情况请重新对相关区域进行识别并修改结果。

一、登记号码与得分号码验证
1. 检查队员登记区域中球员姓名、球员号码、上场标志是否从左到右每行一一对应。列出从左到右的球员姓名、球员号码、上场标志进行检查。识别到的队员数量与号码数量应该相同。特别注意未上场的球员也需要正确识别。
2. 先从左侧球员登记区读取本队全部已填写球衣号码，再读取本队累积分外侧的得分球员号码。可根据手写字迹的特征判断二者是否为同一人。
3. 每一个可辨认的scorer_jersey都必须出现在同队登记号码中。
4. 如果得分号码与登记号码冲突，必须重新检查两处手写字迹，判断是登记号码还是得分号码读错。
5. 如果图片能够解决冲突，应修正读错的一处。

二、得分事件数量验证【对A、B两队分别根据外侧队员号码的填写数量统计得分次数】
1. 累积分中，与有效累计分标记位于同一横行的外侧球衣号码每出现一次，就代表一次得分事件。登记的外侧号码之间的上下间隔为该次得分分值。
2. 某个号码在有效得分行出现N次，输出的running_score中该号码也必须出现N次，不得去重或合并。
3. 每个得分事件都必须单独输出points。两次罚球均命中表示两次独立的1分事件，每项points=1。需要检查每个得分是不是更小得分的组合，如一次2分有可能为两次1分。
4. 如果号码字迹存在但无法辨认，仍保留对应得分事件，并令scorer_jersey为null。

三、累计分差值与得分符号验证【重点验证，用检查清单的方式一一验证】
为每只队伍列出检查清单表，并逐行检查：形状固定为N×6，其中N是队伍候选得分事件总数，每行对应一个得分事件。
六列为：team，填写A或B；cumulative_score，填写该次得分对应的印刷累积分；scorer_jersey，填写160行表格中该侧的外侧手写号码或null；
points，1、2、3；score_mark_matches_points，填写图片中得分方式的记录方式是否与points一致；scorer_jersey_matches_records，填写该得分队员的号码是否出现在球队登记区。检查表和复核过程无需输出。以下是一些注意事项：

1. 每队单独按累计分从小到大检查。第一项得分值等于第一项cumulative_score减0；后续项得分值等于当前cumulative_score减去上一项cumulative_score。得分的分值与累计分是否被圈住无关。
2. 每次差值只能为1、2或3，并且必须与该项输出的points完全相等。得分分值需要与外侧号码之间的上下间隔进行交叉验证。
3. 差值为1时，累计分应有实心黑点，表示罚球。
4. 差值为2时，累计分应有斜杠，得分球员号码不应被圈住。
5. 差值为3时，累计分应有斜杠，并且同一横行的得分球员号码应被圈住。
6. 圈住球员号码表示三分；圈住内侧累计分并在累计分及球员号码下方画粗横线表示该节结束。不得混淆这两类圆圈。
7. 如果相邻累计分差值大于3，必须检查中间1至2个印刷分数是否存在遗漏的黑点或斜杠，不得直接把大差值解释为三分。
8. 符号和差值冲突时重新检查图片；仍无法解决时保留视觉证据最明确的内容，并在recognition_notes中写明具体位置。

四、最终比分与胜队验证
1. Q1至Q4以及可选OT的书面得分之和必须等于最终比分。
2. 每队最后一个累积分的cumulative_score必须等于该队最终比分。
3. 纸面填写的胜队必须是最终比分更高的一队。
4. winner_name必须使用比赛主数据中该队的完整标准名称，不能使用简称、别名、手写近似形式或另一队名称。

五、冲突处理
1. 交叉验证发现不一致时，应返回图片重新检查相关区域。
2. 不得为了满足节比分、最终比分、号码次数或名单约束而虚构得分事件、球员号码或姓名。
3. 能够可靠解决时修正初步转录。
4. 无法可靠解决时：
   - 可空字段返回null；
   - 保留其余有明确视觉依据的内容；
   - 在recognition_notes中简要写明球队、字段和累计分或球员行位置。
5. recognition_notes只记录无法解决的问题；不要输出推理过程。
"""


REGION_LABELS = {
    "team_a_roster": "A 队球员与犯规",
    "team_a_meta": "A 队暂停、全队犯规与教练",
    "team_a_score": "A 队累积分",
    "team_b_roster": "B 队球员与犯规",
    "team_b_meta": "B 队暂停、全队犯规与教练",
    "team_b_score": "B 队累积分",
    "summary": "节比分与比赛结果",
    "officials": "记录台人员与裁判",
}
ALL_REGIONS = tuple(REGION_LABELS)


class OutputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RecognitionPayloadBase(OutputModel):
    @model_validator(mode="after")
    def validate_period_score_sequence(self) -> Self:
        periods = [entry.period for entry in getattr(self, "period_scores", [])]
        if periods not in ([1, 2, 3, 4], [1, 2, 3, 4, 5]):
            raise ValueError("period_scores必须依次为1、2、3、4，并可追加合并决胜期5")
        for team_name in ("team_a", "team_b"):
            team = getattr(self, team_name, None)
            scores = [entry.cumulative_score for entry in getattr(team, "running_score", [])]
            if scores != sorted(set(scores)):
                raise ValueError(f"{team_name}.running_score必须按累计分严格递增且不得重复")
        return self


class RecognizedTimeout(OutputModel):
    scope: Literal["H1", "H2", "OT"] = Field(
        description="暂停所属阶段：H1表示上半场，H2表示下半场，OT表示决胜期。"
    )
    slot: int = Field(ge=1, le=3, description="暂停格从左到右的位置。")
    minute: int = Field(ge=0, le=10, description="暂停格内填写的比赛分钟。")


class RecognizedTeamFoul(OutputModel):
    period: int = Field(ge=1, le=4, description="该组全队犯规格所属的节次。")
    count: int = Field(
        ge=0,
        le=4,
        description=(
            "由两条交叉斜线构成的独立大X数量；未使用格中的平行封闭横线不计，"
            "四格中只有封闭横线而没有独立大X时填0。"
        ),
    )


class RecognizedScoreEvent(OutputModel):
    cumulative_score: int = Field(
        ge=1,
        le=160,
        description="该队本次得分后的印刷累计分，不是本次得分分值。",
    )
    scorer_jersey: str | None = Field(
        default=None,
        pattern=r"^(?:0|00|[1-9][0-9]?)$",
        description=("该次得分对应的外侧手写球衣号码；字迹存在但无法辨认时返回null。"),
    )
    points: Literal[1, 2, 3] = Field(
        description=("该次得分的分值，只能是1、2或3；每次罚球命中必须作为独立的1分事件输出。"),
    )


class RecognizedPeriodScore(OutputModel):
    period: int = Field(
        ge=1,
        le=5,
        description=(
            "该书面单节比分所属的节次。第1至第4节必须依次出现。第5节表示全部决胜期"
            "得分的合计；决胜期栏只有封闭线而没有数字时不要输出第5节。"
        ),
    )
    team_a: int | None = Field(default=None, ge=0, le=160)
    team_b: int | None = Field(default=None, ge=0, le=160)


class RecognizedOfficial(OutputModel):
    role: Literal[
        "crew_chief",
        "umpire_1",
        "umpire_2",
        "protest_captain",
    ]
    name: str | None = Field(description="图片中的裁判员姓名；无法确定时返回null。")
    signature: Literal["present", "absent", "unclear"] = Field(
        description="只记录签名是否存在，不要根据签名辨认签署人。"
    )


@dataclass(frozen=True)
class RecognitionContext:
    payload_model: type[BaseModel]
    system_prompt: str
    user_prompt: str
    schema: dict[str, Any]
    image_bytes: bytes
    image_data_url: str
    image_sha256: str


class RecognitionProviderError(ValueError):
    """A malformed provider payload that is safe to surface as a schema failure."""


class RecognitionImageError(ValueError):
    """An image that cannot be prepared without violating the provider contract."""


def normalize_provider_payload(payload: Any) -> dict[str, Any]:
    """Accept the schema object, plus Qwen's occasional one-item array wrapper."""
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, list) and len(payload) == 1 and isinstance(payload[0], dict):
        return payload[0]
    raise RecognitionProviderError(
        "Qwen 返回的 JSON 顶层必须是对象；仅允许兼容包含一个对象的数组包装。"
    )


INTERNAL_NORMALIZATION_ISSUES_KEY = "_normalization_issues"


def _normalization_issue(
    code: str,
    path: str,
    message: str,
    observed: Any = None,
    expected: Any = None,
) -> RecognitionIssue:
    return RecognitionIssue(
        code=code,
        path=path,
        message=message,
        observed=observed,
        expected=expected,
    )


def normalize_running_score_payload(
    payload: dict[str, Any],
) -> tuple[dict[str, Any], list[RecognitionIssue]]:
    """Normalize sparse events and downgrade stored v23 grids without a paid retry."""
    normalized = copy.deepcopy(payload)
    issues: list[RecognitionIssue] = []
    raw_rows = normalized.get("running_score_rows")

    for team_key, side in (("team_a", TeamSide.A), ("team_b", TeamSide.B)):
        team = normalized.get(team_key)
        if not isinstance(team, dict):
            continue
        raw_events = team.get("running_score")
        if not isinstance(raw_events, list):
            raw_events = []
            if isinstance(raw_rows, list):
                previous = 0
                for row_index, row in enumerate(raw_rows, start=1):
                    if not isinstance(row, dict):
                        continue
                    cumulative = row.get("cumulative_score")
                    if type(cumulative) is not int or not 1 <= cumulative <= 160:
                        cumulative = row_index if row_index <= 160 else None
                    if cumulative is None:
                        continue
                    evidence = row.get(team_key)
                    if not isinstance(evidence, dict):
                        continue
                    jersey = evidence.get("scorer_jersey")
                    raw_points = evidence.get("points")
                    has_mark = evidence.get("has_score_mark") is True
                    if jersey is None and raw_points is None and not has_mark:
                        continue
                    points = (
                        raw_points if type(raw_points) is int and raw_points in {1, 2, 3} else None
                    )
                    if points is None:
                        delta = cumulative - previous
                        if delta in {1, 2, 3}:
                            points = delta
                            issues.append(
                                _normalization_issue(
                                    "RUNNING_SCORE_POINTS_DERIVED",
                                    f"/score_events/{side.value}/cumulative/{cumulative}/points",
                                    f"旧160行结果中{side.value}队累计{cumulative}分缺少有效points，已按相邻事件差值恢复为{delta}。",
                                    raw_points,
                                    delta,
                                )
                            )
                        else:
                            issues.append(
                                _normalization_issue(
                                    "RUNNING_SCORE_LEGACY_EVENT_DROPPED",
                                    f"/score_events/{side.value}/cumulative/{cumulative}/points",
                                    f"旧160行结果中{side.value}队累计{cumulative}分无法恢复为1、2或3分事件，未加入稀疏事件数组。",
                                    raw_points,
                                    [1, 2, 3],
                                )
                            )
                            continue
                    raw_events.append(
                        {
                            "cumulative_score": cumulative,
                            "scorer_jersey": jersey,
                            "points": points,
                        }
                    )
                    previous = cumulative
                if raw_events:
                    issues.append(
                        _normalization_issue(
                            "RUNNING_SCORE_LEGACY_GRID_CONVERTED",
                            f"/score_events/{side.value}",
                            f"已将旧160行{side.value}队累积分结果转换为稀疏得分事件数组。",
                            "running_score_rows",
                            f"{team_key}.running_score",
                        )
                    )

        sparse: list[dict[str, Any]] = []
        for index, item in enumerate(raw_events):
            if not isinstance(item, dict):
                issues.append(
                    _normalization_issue(
                        "RUNNING_SCORE_EVENT_DROPPED",
                        f"/score_events/{side.value}/event/{index + 1}",
                        "得分事件不是对象，已忽略。",
                        item,
                        "包含cumulative_score、scorer_jersey和points的对象",
                    )
                )
                continue
            event = dict(item)
            jersey = event.get("scorer_jersey")
            if isinstance(jersey, int) and not isinstance(jersey, bool):
                event["scorer_jersey"] = str(jersey)
                issues.append(
                    _normalization_issue(
                        "RUNNING_SCORE_JERSEY_NORMALIZED",
                        f"/score_events/{side.value}/event/{index + 1}/scorer_jersey",
                        "得分号码已由数字规范为字符串。",
                        jersey,
                        str(jersey),
                    )
                )
            elif isinstance(jersey, str):
                event["scorer_jersey"] = jersey.strip() or None
            sparse.append(event)
        team["running_score"] = sparse

    normalized.pop("running_score_rows", None)
    return normalized, issues


def _literal(values: list[str]) -> Any:
    return Literal.__getitem__(tuple(values))  # type: ignore[attr-defined]


def _load_rule_profile(path: Path, profile: RuleProfileId) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload[profile.value]


def _printed_foul_code(marking: dict[str, Any]) -> str:
    code = str(marking["code"])
    return f"({code})" if marking.get("style") == "circled" else code


def _allowed_foul_suffixes(marking: dict[str, Any]) -> list[str]:
    return [str(suffix) for suffix in marking.get("allowed_suffixes", [""])]


def _foul_tokens(profile: dict[str, Any], subjects: set[str]) -> list[str]:
    tokens: list[str] = []
    for marking in profile.get("foul_markings", []):
        if not subjects.intersection(marking.get("subjects", [])):
            continue
        printed = _printed_foul_code(marking)
        for suffix in _allowed_foul_suffixes(marking):
            token = f"{printed}{suffix}"
            if token not in tokens:
                tokens.append(token)
    return tokens


def build_payload_model(
    prior: GamePriorSnapshot,
    profile: RuleProfileId,
    rule_profiles_path: Path,
) -> type[BaseModel]:
    profile_payload = _load_rule_profile(rule_profiles_path, profile)
    player_token = _literal(_foul_tokens(profile_payload, {"player", "post_foul"}))
    coach_token = _literal(
        _foul_tokens(profile_payload, {"head_coach", "assistant_coach", "post_foul"})
    )

    player_foul = create_model(
        "RecognizedPlayerFoul",
        __base__=OutputModel,
        position=(
            int,
            Field(
                ge=1,
                le=7,
                description="犯规标记从左到右的位置；1至5为正式犯规格。",
            ),
        ),
        mark=(
            player_token,
            Field(description="所选规则档案允许的纸面犯规标记。"),
        ),
    )
    coach_foul = create_model(
        "RecognizedCoachFoul",
        __base__=OutputModel,
        position=(
            int,
            Field(
                ge=1,
                le=5,
                description="犯规标记从左到右的位置；1至3为正式犯规格。",
            ),
        ),
        mark=(
            coach_token,
            Field(description="所选规则档案允许的纸面犯规标记。"),
        ),
    )

    def player_model(side: str, names: list[str]) -> type[BaseModel]:
        name_type = _literal(names)
        return create_model(
            f"RecognizedPlayer{side}",
            __base__=OutputModel,
            row=(
                int,
                Field(
                    ge=1,
                    le=12,
                    description="球员在记录表登记区中从上到下的行号。",
                ),
            ),
            name=(
                name_type | None,
                Field(description="只能选择本队的唯一姓名；无法确定时返回null。"),
            ),
            jersey_number=(
                str | None,
                Field(
                    default=None,
                    pattern=r"^(?:0|00|[1-9][0-9]?)$",
                    description=("图片中的球衣号码，只能是0、00或不以0开头的一至两位数字。"),
                ),
            ),
            captain=(
                bool | None,
                Field(description="姓名后是否填写队长CAP标记；无法确定时返回null。"),
            ),
            participation=(
                Literal["none", "starter", "substitute"] | None,
                Field(
                    description=(
                        "圈住的小x表示starter，普通x表示substitute，没有参赛标记表示none。"
                    )
                ),
            ),
            fouls=(
                list[player_foul],
                Field(default_factory=list, description="只列出已经填写的犯规位置。"),
            ),
        )

    def team_model(side: str, names: list[str]) -> type[BaseModel]:
        player = player_model(side, names)
        coach = create_model(
            f"RecognizedCoach{side}",
            __base__=OutputModel,
            name=(
                str | None,
                Field(description="图片中的教练员姓名；无法确定时返回null。"),
            ),
            fouls=(
                list[coach_foul],
                Field(default_factory=list, description="只列出已经填写的犯规位置。"),
            ),
        )
        return create_model(
            f"RecognizedTeam{side}",
            __base__=OutputModel,
            players=(
                list[player],
                Field(default_factory=list, description="只输出实际填写的球员行。"),
            ),
            timeouts=(list[RecognizedTimeout], Field(default_factory=list)),
            team_fouls=(list[RecognizedTeamFoul], Field(default_factory=list)),
            head_coach=(coach, Field()),
            assistant_coach=(coach, Field()),
            running_score=(
                list[RecognizedScoreEvent],
                Field(
                    default_factory=list,
                    description=(
                        "该队按照累计分严格递增排列的逐次得分事件；每次得分分别输出，"
                        "不得合并或省略重复出现的得分号码。"
                    ),
                ),
            ),
        )

    team_a = team_model("A", prior.team_a.player_names)
    team_b = team_model("B", prior.team_b.player_names)
    winner_name = _literal([prior.team_a.name, prior.team_b.name])
    final_score = create_model(
        "RecognizedFinalScore",
        __base__=OutputModel,
        team_a=(int | None, Field(default=None, ge=0, le=160)),
        team_b=(int | None, Field(default=None, ge=0, le=160)),
        winner_name=(
            winner_name | None,
            Field(
                default=None,
                description=(
                    "纸面填写的胜队；只能选择最终比分较高一队的主数据完整标准名称，"
                    "无法确定时返回null。"
                ),
            ),
        ),
        ended_at=(
            str | None,
            Field(
                default=None,
                description="纸面填写的比赛结束时间，格式为HH:MM；无法确定时返回null。",
            ),
        ),
    )
    return create_model(
        "RecognitionPayload",
        __base__=RecognitionPayloadBase,
        period_scores=(
            list[RecognizedPeriodScore],
            Field(
                min_length=4,
                max_length=5,
                description=(
                    "严格按照第1、2、3、4节的顺序输出。只有纸面填写了决胜期得分时才追加"
                    "第5节；第5节表示全部决胜期得分的合计。"
                ),
            ),
        ),
        final_score=(final_score, Field()),
        team_a=(team_a, Field()),
        team_b=(team_b, Field()),
        table_personnel=(
            list[str],
            Field(
                default_factory=list,
                description=(
                    "记录台区域中能够辨认的人员姓名，每项一人，按照首次出现顺序去重。"
                    "不要分配记录员、助理记录员、计时员或24秒计时员岗位。"
                ),
            ),
        ),
        officials=(
            list[RecognizedOfficial],
            Field(
                default_factory=list,
                description=("只列出纸面角色明确的裁判员和申诉队长；每种role最多输出一项。"),
            ),
        ),
        recognition_notes=(
            str,
            Field(
                description=(
                    "简要指出每个无法可靠转录的问题及其具体位置；没有未解决问题时返回空字符串。"
                )
            ),
        ),
    )


def build_user_prompt(prior: GamePriorSnapshot, profile: RuleProfileId) -> str:
    # Only the two team names are included here. Candidate player names live in
    # the strict per-team JSON Schema enum; no account, UUID, season, date, or
    # venue data is sent to the recognition provider.
    return (
        f"请转录整张{profile.value.replace('_', ' ').upper()}篮球记录表。"
        f"A队为“{prior.team_a.name}”，B队为“{prior.team_b.name}”。"
        "每队唯一候选球员姓名只编码在JSON Schema中该队name字段的enum内。中文专有姓名"
        "属于源数据，必须逐字复制。不要重复输出已锁定的表头数据，也不得使用另一队的姓名。"
    )


def _data_url_size(payload_size: int, mime_type: str) -> int:
    prefix_size = len(f"data:{mime_type};base64,")
    return prefix_size + 4 * ((payload_size + 2) // 3)


def _make_data_url(payload: bytes, mime_type: str) -> str:
    return f"data:{mime_type};base64," + base64.b64encode(payload).decode("ascii")


def _flatten_to_rgb(image: Image.Image) -> Image.Image:
    if image.mode in {"RGBA", "LA"} or "transparency" in image.info:
        rgba = image.convert("RGBA")
        flattened = Image.new("RGB", rgba.size, "white")
        flattened.paste(rgba, mask=rgba.getchannel("A"))
        rgba.close()
        return flattened
    return image.convert("RGB")


def _encode_jpeg(image: Image.Image, quality: int) -> bytes:
    output = io.BytesIO()
    image.save(
        output,
        format="JPEG",
        quality=quality,
        subsampling=0,
        optimize=True,
    )
    return output.getvalue()


def _fit_jpeg_to_data_url(
    image: Image.Image,
    max_data_uri_bytes: int,
) -> tuple[bytes, str]:
    rgb = _flatten_to_rgb(image)
    try:
        payload = _encode_jpeg(rgb, JPEG_MAX_QUALITY)
        if _data_url_size(len(payload), "image/jpeg") <= max_data_uri_bytes:
            return payload, _make_data_url(payload, "image/jpeg")

        best_payload: bytes | None = None
        low = JPEG_MIN_QUALITY
        high = JPEG_MAX_QUALITY - 1
        while low <= high:
            quality = (low + high) // 2
            candidate = _encode_jpeg(rgb, quality)
            if _data_url_size(len(candidate), "image/jpeg") <= max_data_uri_bytes:
                best_payload = candidate
                low = quality + 1
            else:
                high = quality - 1
        if best_payload is None:
            raise RecognitionImageError(
                "图片在不缩小分辨率的条件下无法满足 Qwen 20 MB Base64 Data URI 限制。"
            )
        return best_payload, _make_data_url(best_payload, "image/jpeg")
    finally:
        rgb.close()


def _webp_exceeds_direct_resolution(width: int, height: int) -> bool:
    long_edge, short_edge = sorted((width, height), reverse=True)
    return long_edge > QWEN_WEBP_MAX_LONG_EDGE or short_edge > QWEN_WEBP_MAX_SHORT_EDGE


def prepare_image(
    path: Path,
    upscale_target_pixels: int,
    *,
    max_data_uri_bytes: int = QWEN_DATA_URI_MAX_BYTES,
) -> tuple[bytes, str, str]:
    with Image.open(path) as opened:
        image_format = (opened.format or "").upper()
        orientation = opened.getexif().get(274, 1)
        width, height = opened.size
        pixel_count = width * height
        needs_upscale = pixel_count < upscale_target_pixels
        webp_requires_jpeg = image_format == "WEBP" and _webp_exceeds_direct_resolution(
            width, height
        )
        mime_type = {
            "JPEG": "image/jpeg",
            "PNG": "image/png",
            "WEBP": "image/webp",
        }.get(image_format)

        if (
            mime_type is not None
            and orientation in (0, 1)
            and not needs_upscale
            and not webp_requires_jpeg
            and _data_url_size(path.stat().st_size, mime_type) <= max_data_uri_bytes
        ):
            payload = path.read_bytes()
            if _data_url_size(len(payload), mime_type) <= max_data_uri_bytes:
                digest = hashlib.sha256(payload).hexdigest()
                return payload, _make_data_url(payload, mime_type), digest

        image = ImageOps.exif_transpose(opened)
        image.load()
        if needs_upscale:
            target_scale = math.sqrt(upscale_target_pixels / pixel_count)
            scale = min(target_scale, 2.0)
            resized = image.resize(
                (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
                Image.Resampling.LANCZOS,
            )
            if resized is not image:
                image.close()
            image = resized
        try:
            payload, data_url = _fit_jpeg_to_data_url(image, max_data_uri_bytes)
            digest = hashlib.sha256(payload).hexdigest()
            return payload, data_url, digest
        finally:
            image.close()


def build_context(
    prior: GamePriorSnapshot,
    image_path: Path,
    rule_profiles_path: Path,
    upscale_target_pixels: int,
) -> RecognitionContext:
    payload_model = build_payload_model(prior, RuleProfileId.FIBA_2024, rule_profiles_path)
    schema = payload_model.model_json_schema()
    image_bytes, data_url, image_sha256 = prepare_image(image_path, upscale_target_pixels)
    return RecognitionContext(
        payload_model=payload_model,
        system_prompt=SYSTEM_PROMPT,
        user_prompt=build_user_prompt(prior, RuleProfileId.FIBA_2024),
        schema=schema,
        image_bytes=image_bytes,
        image_data_url=data_url,
        image_sha256=image_sha256,
    )


def validate_provider_payload(
    context: RecognitionContext,
    payload: Any,
    prior: GamePriorSnapshot,
) -> tuple[BaseModel, list[RecognitionIssue]]:
    sanitized = sanitize_unknown_player_names(normalize_provider_payload(payload), prior)
    normalized, issues = normalize_running_score_payload(sanitized)
    return context.payload_model.model_validate(normalized), issues


def sanitize_unknown_player_names(
    payload: dict[str, Any],
    prior: GamePriorSnapshot,
) -> dict[str, Any]:
    """Turn provider-invented player names into null without fuzzy matching."""
    problems: list[str] = []
    for team_key, side, allowed_names in (
        ("team_a", "A", set(prior.team_a.player_names)),
        ("team_b", "B", set(prior.team_b.player_names)),
    ):
        team = payload.get(team_key)
        if not isinstance(team, dict) or not isinstance(team.get("players"), list):
            continue
        for index, player in enumerate(team["players"]):
            if not isinstance(player, dict):
                continue
            name = player.get("name")
            if name is not None and name not in allowed_names:
                player["name"] = None
                row = player.get("row", index + 1)
                problems.append(f"{side}队第{row}行姓名不在唯一名单中，已置空")
    if problems:
        existing = payload.get("recognition_notes")
        notes = existing.strip() if isinstance(existing, str) else ""
        addition = "；".join(problems) + "。"
        payload["recognition_notes"] = f"{notes} {addition}".strip()
    return payload


def _valid_jersey(value: str | None) -> bool:
    if value is None or value == "":
        return True
    return value in {"0", "00"} or (
        value.isdigit() and not value.startswith("0") and 1 <= int(value) <= 99
    )


def _parse_foul(
    mark: str,
    slot: int,
    profile_payload: dict[str, Any],
    subject: str,
    *,
    post: bool,
) -> FoulEntry | PostFoulMarker:
    matched_marking: dict[str, Any] | None = None
    matched_suffix: str | None = None
    subject_order = ("post_foul", subject) if post else (subject, "post_foul")
    for required_subject in subject_order:
        for marking in profile_payload.get("foul_markings", []):
            if required_subject not in marking.get("subjects", []):
                continue
            printed = _printed_foul_code(marking)
            for suffix in _allowed_foul_suffixes(marking):
                if mark == f"{printed}{suffix}":
                    matched_marking = marking
                    matched_suffix = suffix
                    break
            if matched_marking is not None:
                break
        if matched_marking is not None:
            break
    if matched_marking is None or matched_suffix is None:
        raise ValueError(f"无法解析犯规记号 {mark}")
    code = FoulCode(str(matched_marking["code"]))
    style = FoulMarkStyle(str(matched_marking.get("style", "plain")))
    payload = {
        "slot": slot,
        "code": code,
        "catalog_id": matched_marking.get("id"),
        "mark_style": style,
        "free_throws": int(matched_suffix) if matched_suffix.isdigit() else None,
        "cancelled": matched_suffix == "c",
        "period": None,
    }
    return PostFoulMarker(**payload) if post else FoulEntry(**payload)


def _fouls(
    entries: list[BaseModel],
    formal_limit: int,
    profile_payload: dict[str, Any],
    subject: str,
    path: str,
    problems: list[str],
) -> tuple[list[FoulEntry], list[PostFoulMarker]]:
    formal: list[FoulEntry] = []
    post: list[PostFoulMarker] = []
    for entry in sorted(entries, key=lambda value: value.position):
        position = int(entry.position)
        try:
            if position <= formal_limit:
                formal.append(
                    _parse_foul(entry.mark, position, profile_payload, subject, post=False)
                )
            elif position <= formal_limit + 2:
                post.append(
                    _parse_foul(
                        entry.mark,
                        position - formal_limit,
                        profile_payload,
                        subject,
                        post=True,
                    )
                )
            else:
                problems.append(path)
        except ValueError:
            problems.append(path)
    return formal, post


def _recognized_team(
    raw: BaseModel,
    side: TeamSide,
    profile_payload: dict[str, Any],
    problems: list[str],
) -> TeamEntry:
    team_index = 0 if side == TeamSide.A else 1
    players: list[PlayerEntry] = []
    for player in sorted(raw.players, key=lambda value: value.row):
        path = f"/teams/{team_index}/players/row/{player.row}"
        name = player.name or ""
        jersey = (player.jersey_number or "").strip()
        if not name:
            problems.append(f"{path}/name")
        if not _valid_jersey(jersey):
            problems.append(f"{path}/jersey_number")
            jersey = ""
        if player.jersey_number is None:
            problems.append(f"{path}/jersey_number")
        if player.participation is None:
            problems.append(f"{path}/participation")
        fouls, post = _fouls(
            player.fouls,
            5,
            profile_payload,
            "player",
            f"{path}/fouls",
            problems,
        )
        players.append(
            PlayerEntry(
                row=player.row,
                name=name,
                jersey_number=jersey,
                captain=bool(player.captain),
                participation=player.participation or "none",
                fouls=fouls,
                post_foul_markers=post,
            )
        )

    def coach(
        value: BaseModel,
        name_path: str,
        subject: str,
    ) -> tuple[str, list[FoulEntry], list[PostFoulMarker]]:
        if value.name is None:
            problems.append(name_path)
        fouls, post = _fouls(
            value.fouls,
            3,
            profile_payload,
            subject,
            name_path.rsplit("/", 1)[0] + "/fouls",
            problems,
        )
        return value.name or "", fouls, post

    head_name, head_fouls, head_post = coach(
        raw.head_coach,
        f"/teams/{team_index}/head_coach",
        "head_coach",
    )
    assistant_name, assistant_fouls, assistant_post = coach(
        raw.assistant_coach,
        f"/teams/{team_index}/assistant_coach",
        "assistant_coach",
    )
    return TeamEntry(
        side=side,
        players=players,
        timeouts=[TimeoutEntry(**entry.model_dump()) for entry in raw.timeouts],
        team_fouls=[TeamFoulPeriod(**entry.model_dump()) for entry in raw.team_fouls],
        coach_fouls=head_fouls,
        coach_post_foul_markers=head_post,
        assistant_coach_fouls=assistant_fouls,
        assistant_coach_post_foul_markers=assistant_post,
        head_coach=head_name,
        assistant_coach=assistant_name,
    )


def _score_events(
    raw_events: list[BaseModel],
    side: TeamSide,
    period_scores: list[BaseModel],
    final_score: BaseModel,
    problems: list[str],
    recognition_issues: list[RecognitionIssue],
) -> list[ScoreEvent]:
    events: list[ScoreEvent] = []
    checkpoints: list[tuple[int, int]] = []
    running_total = 0
    score_field = "team_a" if side == TeamSide.A else "team_b"
    for period_score in sorted(period_scores, key=lambda item: item.period):
        period_value = getattr(period_score, score_field)
        if period_value is None:
            problems.append(f"/stated_period_scores/{period_score.period}/{side.value}")
            continue
        running_total += period_value
        checkpoints.append((period_score.period, running_total))
    written_final = getattr(final_score, score_field)
    if written_final is not None and checkpoints and checkpoints[-1][1] != written_final:
        problems.append(f"/final_score/{score_field}")

    def inferred_period(cumulative_score: int, path: str) -> int:
        for period, checkpoint in checkpoints:
            if cumulative_score <= checkpoint:
                return period
        problems.append(f"{path}/period")
        return checkpoints[-1][0] if checkpoints else 1

    previous = 0
    for item in raw_events:
        cumulative = item.cumulative_score
        path = f"/score_events/{side.value}/cumulative/{cumulative}"
        jersey = (item.scorer_jersey or "").strip()
        if not jersey:
            problems.append(f"{path}/scorer_jersey")

        points = item.points
        delta = cumulative - previous
        if delta != points:
            problems.append(f"{path}/delta")
            recognition_issues.append(
                RecognitionIssue(
                    code="RUNNING_SCORE_POINTS_MISMATCH",
                    path=f"{path}/points",
                    message=(
                        f"{side.value} 队累计 {cumulative} 分的模型分值为 {points}，"
                        f"与上一得分事件的累计分差值 {delta} 不一致；已保留模型分值等待人工核对。"
                    ),
                    observed=points,
                    expected=delta,
                )
            )
        period = inferred_period(cumulative, path)
        events.append(
            ScoreEvent(
                sequence=1,
                team=side,
                period=period,
                points=points,
                cumulative_score=cumulative,
                scorer_jersey=jersey,
                mark=(
                    ScoreMark.FILLED_DOT
                    if points == 1
                    else ScoreMark.DIAGONAL
                    if points in {2, 3}
                    else None
                ),
                scorer_circled=points == 3,
                boundary=ScoreBoundary.NONE,
                ink_role=InkRole.Q1_Q3 if period in {1, 3} else InkRole.Q2_Q4_OT,
            )
        )
        previous = cumulative
    events_by_score = {event.cumulative_score: event for event in events}
    for period, checkpoint in checkpoints:
        event = events_by_score.get(checkpoint)
        if event is None:
            problems.append(f"/score_events/{side.value}/period/{period}/boundary")
        else:
            event.boundary = ScoreBoundary.PERIOD_END
    return events


def _resequence(events: list[ScoreEvent]) -> list[ScoreEvent]:
    ordered = sorted(
        events,
        key=lambda event: (event.period, event.team.value, event.cumulative_score),
    )
    for sequence, event in enumerate(ordered, start=1):
        event.sequence = sequence
    return ordered


def _region_for_path(path: str) -> str | None:
    if path.startswith("/teams/0/players"):
        return "team_a_roster"
    if path.startswith("/teams/0"):
        return "team_a_meta"
    if path.startswith("/teams/1/players"):
        return "team_b_roster"
    if path.startswith("/teams/1"):
        return "team_b_meta"
    if path.startswith("/score_events/A"):
        return "team_a_score"
    if path.startswith("/score_events/B"):
        return "team_b_score"
    if path.startswith("/final_score") or path.startswith("/stated_period_scores"):
        return "summary"
    if (
        path.startswith("/officials")
        or path.startswith("/recognition/table_personnel")
        or path.startswith("/header/crew")
        or path.startswith("/header/umpire")
    ):
        return "officials"
    return None


def map_payload_to_document(
    document: ScoresheetDocument,
    payload: BaseModel,
    run_id: str,
    rule_profiles_path: Path,
    regions: set[str] | None = None,
    normalization_issues: list[RecognitionIssue] | None = None,
) -> ScoresheetDocument:
    selected = set(ALL_REGIONS if regions is None else regions)
    unknown = selected - set(ALL_REGIONS)
    if unknown:
        raise ValueError(f"未知识别合并区域：{', '.join(sorted(unknown))}")
    result = document.model_copy(deep=True)
    profile_payload = _load_rule_profile(rule_profiles_path, document.rules_profile)
    problems: list[str] = []
    recognition_issues = list(normalization_issues or [])
    recognized_teams = {
        TeamSide.A: _recognized_team(payload.team_a, TeamSide.A, profile_payload, problems),
        TeamSide.B: _recognized_team(payload.team_b, TeamSide.B, profile_payload, problems),
    }
    for side, prefix in ((TeamSide.A, "team_a"), (TeamSide.B, "team_b")):
        target = next(team for team in result.teams if team.side == side)
        source = recognized_teams[side]
        if f"{prefix}_roster" in selected:
            target.players = source.players
        if f"{prefix}_meta" in selected:
            target.timeouts = source.timeouts
            target.team_fouls = source.team_fouls
            target.coach_fouls = source.coach_fouls
            target.coach_post_foul_markers = source.coach_post_foul_markers
            target.assistant_coach_fouls = source.assistant_coach_fouls
            target.assistant_coach_post_foul_markers = source.assistant_coach_post_foul_markers
            target.head_coach = source.head_coach
            target.assistant_coach = source.assistant_coach
        if f"{prefix}_score" in selected:
            result.score_events = [event for event in result.score_events if event.team != side]
            result.score_events.extend(
                _score_events(
                    getattr(payload, prefix).running_score,
                    side,
                    payload.period_scores,
                    payload.final_score,
                    problems,
                    recognition_issues,
                )
            )

    if "summary" in selected:
        scores: list[PeriodScore] = []
        for item in payload.period_scores:
            if item.team_a is None:
                problems.append(f"/stated_period_scores/{item.period}/A")
            if item.team_b is None:
                problems.append(f"/stated_period_scores/{item.period}/B")
            if item.team_a is None or item.team_b is None:
                continue
            scores.append(PeriodScore(period=item.period, team_a=item.team_a, team_b=item.team_b))
        result.stated_period_scores = scores
        final = payload.final_score
        if final.team_a is None:
            problems.append("/final_score/team_a")
        if final.team_b is None:
            problems.append("/final_score/team_b")
        if final.winner_name is None:
            problems.append("/final_score/winner_name")
        if final.ended_at is None:
            problems.append("/final_score/ended_at")
        result.final_score.team_a = final.team_a or 0
        result.final_score.team_b = final.team_b or 0
        result.final_score.winner_name = final.winner_name or ""
        result.final_score.ended_at = final.ended_at or ""

    existing_table_personnel = (
        list(document.recognition.table_personnel) if document.recognition else []
    )
    table_personnel = existing_table_personnel
    if "officials" in selected:
        table_personnel = []
        seen_personnel: set[str] = set()
        for raw_name in payload.table_personnel:
            name = " ".join(raw_name.strip().split())
            if name and name not in seen_personnel:
                table_personnel.append(name)
                seen_personnel.add(name)
        by_role = {entry.role: entry for entry in result.officials}
        for item in payload.officials:
            if item.name is None:
                problems.append(f"/officials/{item.role}/name")
            by_role[item.role] = OfficialEntry(
                role=item.role,
                name=item.name or "",
                signature=SignaturePresence(item.signature),
            )
        result.officials = list(by_role.values())
        for role in ("crew_chief", "umpire_1", "umpire_2"):
            official = by_role.get(role)
            setattr(result.header, role, official.name if official else "")

    result.score_events = _resequence(result.score_events)
    retained = [
        path
        for path in (document.recognition.problem_paths if document.recognition else [])
        if _region_for_path(path) not in selected
    ]
    relevant = [path for path in problems if _region_for_path(path) in selected]
    retained_issues = [
        issue
        for issue in (document.recognition.issues if document.recognition else [])
        if _region_for_path(issue.path) not in selected
    ]
    relevant_issues = [
        issue for issue in recognition_issues if _region_for_path(issue.path) in selected
    ]
    merged_issues: list[RecognitionIssue] = []
    seen_issue_keys: set[tuple[str, str, str]] = set()
    for issue in retained_issues + relevant_issues:
        key = (issue.code, issue.path, issue.message)
        if key not in seen_issue_keys:
            merged_issues.append(issue)
            seen_issue_keys.add(key)
    result.recognition = RecognitionDocumentState(
        run_id=run_id,
        notes=payload.recognition_notes,
        table_personnel=table_personnel,
        problem_paths=sorted(set(retained + relevant)),
        issues=merged_issues,
        applied_at=datetime.now(UTC),
    )
    result.schema_version = "1.4.0"
    result.status = DocumentStatus.NEEDS_REVIEW
    result.acknowledged_warnings = []
    return result
