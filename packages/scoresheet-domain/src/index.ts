export const SCORESHEET_REGIONS = [
  "SOURCE_GAME",
  "TEAM_A",
  "TEAM_B",
  "RUNNING_SCORE",
  "SUMMARY",
  "OFFICIALS",
] as const;

export type ScoresheetRegion = (typeof SCORESHEET_REGIONS)[number];
export type ScoresheetSurface = "WEB" | "MINIAPP";
export type TeamSide = "A" | "B";
export type ParticipationStatus = "none" | "starter" | "substitute";
export type FoulCode = "P" | "T" | "U" | "D" | "C" | "B" | "GD" | "F" | "DI" | "FL" | "BD";
export type FoulMarkStyle = "plain" | "circled";
export type RuleProfileId = "fiba_2024" | "fiba_2026";
export type ScoreMark = "filled_dot" | "diagonal";
export type ScoreBoundary = "none" | "period_end" | "game_end";
export type InkRole = "q1_q3" | "q2_q4_ot" | "neutral";
export type SignaturePresence = "present" | "absent" | "unclear";
export type DocumentStatus = "draft" | "needs_review" | "validated" | "confirmed";
export type GamePeriod = 1 | 2 | 3 | 4 | 5;
export type RegulationPeriod = 1 | 2 | 3 | 4;
export type TimeoutScope = "H1" | "H2" | "OT";
export type OfficialRole =
  | "scorer"
  | "assistant_scorer"
  | "timer"
  | "shot_clock_operator"
  | "crew_chief"
  | "umpire_1"
  | "umpire_2"
  | "protest_captain";
export type FoulEditorGroup = "player" | "coach" | "post_foul";
export type FoulSuffix = "" | "1" | "2" | "3" | "c";

export interface FoulEditorOption {
  code: string;
  catalogId: string;
  markStyle: "plain" | "circled";
  allowedSuffixes: FoulSuffix[];
}

const FIBA_2024_FOUL_OPTIONS: Record<FoulEditorGroup, FoulEditorOption[]> = {
  player: [
    { code: "P", catalogId: "player.personal", markStyle: "plain", allowedSuffixes: ["", "1", "2", "3", "c"] },
    { code: "T", catalogId: "player.technical", markStyle: "plain", allowedSuffixes: ["", "1", "2", "3", "c"] },
    { code: "U", catalogId: "player.unsportsmanlike", markStyle: "plain", allowedSuffixes: ["", "1", "2", "3", "c"] },
    { code: "D", catalogId: "player.disqualifying", markStyle: "plain", allowedSuffixes: ["", "1", "2", "3", "c"] },
  ],
  coach: [
    { code: "C", catalogId: "coach.personal_technical", markStyle: "plain", allowedSuffixes: ["", "1", "2", "3", "c"] },
    { code: "B", catalogId: "coach.bench_technical", markStyle: "plain", allowedSuffixes: ["", "1", "2", "3", "c"] },
    { code: "D", catalogId: "coach.disqualifying", markStyle: "plain", allowedSuffixes: ["", "1", "2", "3", "c"] },
    { code: "F", catalogId: "system.fighting_remainder", markStyle: "plain", allowedSuffixes: [""] },
  ],
  post_foul: [
    { code: "D", catalogId: "system.post_disqualifying", markStyle: "plain", allowedSuffixes: ["", "1", "2", "3", "c"] },
    { code: "GD", catalogId: "system.game_disqualification", markStyle: "plain", allowedSuffixes: [""] },
    { code: "F", catalogId: "system.fighting_remainder", markStyle: "plain", allowedSuffixes: [""] },
  ],
};

/** Current FIBA 2024 foul catalogue shared by the web and miniapp editors. */
export function fiba2024FoulEditorOptions(group: FoulEditorGroup): FoulEditorOption[] {
  return FIBA_2024_FOUL_OPTIONS[group].map((option) => ({
    ...option,
    allowedSuffixes: [...option.allowedSuffixes],
  }));
}

export const REGION_LABELS: Record<ScoresheetRegion, string> = {
  SOURCE_GAME: "原图与比赛信息",
  TEAM_A: "A 队",
  TEAM_B: "B 队",
  RUNNING_SCORE: "逐次得分",
  SUMMARY: "节比分与最终结果",
  OFFICIALS: "工作人员",
};

export const TEMPLATE_REGION_BOUNDS: Record<
  ScoresheetRegion,
  { x: number; y: number; width: number; height: number }
> = {
  SOURCE_GAME: { x: 37.2, y: 79, width: 535.2, height: 45.8 },
  TEAM_A: { x: 37.2, y: 124.8, width: 309, height: 268.2 },
  TEAM_B: { x: 37.2, y: 393, width: 309, height: 267.6 },
  RUNNING_SCORE: { x: 346.2, y: 150.6, width: 226.2, height: 510 },
  SUMMARY: { x: 346.2, y: 660.6, width: 226.2, height: 124.8 },
  OFFICIALS: { x: 37.2, y: 660.6, width: 309, height: 124.8 },
};

export const TIMEOUT_SLOT_COUNTS: Record<TimeoutScope, number> = { H1: 2, H2: 3, OT: 3 };
export const TIMEOUT_SCOPE_LABELS: Record<TimeoutScope, string> = {
  H1: "上半场",
  H2: "下半场",
  OT: "决胜期",
};

export const OFFICIAL_ROLES: OfficialRole[] = [
  "scorer",
  "assistant_scorer",
  "timer",
  "shot_clock_operator",
  "crew_chief",
  "umpire_1",
  "umpire_2",
  "protest_captain",
];

export const OFFICIAL_LABELS: Record<OfficialRole, string> = {
  scorer: "记录员",
  assistant_scorer: "助理记录员",
  timer: "计时员",
  shot_clock_operator: "24 秒计时员",
  crew_chief: "主裁",
  umpire_1: "第一副裁",
  umpire_2: "第二副裁",
  protest_captain: "球队抗议队长",
};

export interface Header {
  competition: string;
  game_number: string;
  date: string;
  scheduled_time: string;
  venue: string;
  crew_chief: string;
  umpire_1: string;
  umpire_2: string;
}

export interface FoulEntry {
  slot: number;
  code: FoulCode;
  catalog_id?: string | null;
  mark_style?: FoulMarkStyle;
  free_throws: number | null;
  cancelled: boolean;
  period: GamePeriod | null;
}

export type PostFoulMarker = FoulEntry;

export interface PlayerEntry {
  row: number;
  license_number: string;
  name: string;
  jersey_number: string;
  captain: boolean;
  participation: ParticipationStatus;
  fouls: FoulEntry[];
  post_foul_markers: PostFoulMarker[];
}

export interface TimeoutEntry {
  scope: TimeoutScope;
  slot: number;
  minute: number;
}

export interface TeamFoulPeriod {
  period: RegulationPeriod;
  count: number;
}

export interface TeamEntry {
  side: TeamSide;
  name: string;
  players: PlayerEntry[];
  timeouts: TimeoutEntry[];
  team_fouls: TeamFoulPeriod[];
  coach_fouls: FoulEntry[];
  coach_post_foul_markers: PostFoulMarker[];
  assistant_coach_fouls: FoulEntry[];
  assistant_coach_post_foul_markers: PostFoulMarker[];
  head_coach: string;
  assistant_coach: string;
}

export interface ScoreEvent {
  sequence: number;
  team: TeamSide;
  period: GamePeriod;
  points: number | null;
  cumulative_score: number;
  scorer_jersey: string;
  mark: ScoreMark | null;
  scorer_circled: boolean;
  boundary: ScoreBoundary;
  ink_role: InkRole;
}

export interface PeriodScore {
  period: GamePeriod;
  team_a: number;
  team_b: number;
}

export interface FinalScore {
  team_a: number;
  team_b: number;
  winner_name: string;
  ended_at: string;
}

export interface OfficialEntry {
  role: OfficialRole;
  name: string;
  signature: SignaturePresence;
}

export interface SourceAsset {
  original_filename: string;
  original_url: string;
  aligned_url: string;
  version?: number;
  content_sha256?: string;
  width: number;
  height: number;
  rotation: number;
  corners: number[][] | null;
}

export interface PriorTeam {
  team_id: string;
  name: string;
  player_names: string[];
}

export interface GamePriorSnapshot {
  game_id: string;
  competition: string;
  division: string;
  date: string;
  scheduled_time: string;
  venue: string;
  team_a: PriorTeam;
  team_b: PriorTeam;
  source_hash: string;
  locked_paths: string[];
}

export interface RecognitionIssue {
  code: string;
  path: string;
  message: string;
  observed: unknown;
  expected: unknown;
}

export interface RecognitionDocumentState {
  run_id: string;
  notes: string;
  table_personnel: string[];
  problem_paths: string[];
  issues?: RecognitionIssue[];
  applied_at: string;
}

/**
 * A draft may contain manually entered, unassigned table personnel before any
 * recognition result exists.  Keep that compatibility state distinguishable
 * from an applied Qwen result so the editor does not report recognition as
 * completed merely because an administrator entered a name.
 */
export const MANUAL_TABLE_PERSONNEL_RUN_ID = "manual-table-personnel";

/** Authoritative scoresheet v1.4 document shared by API, web, and miniapp. */
export interface ScoresheetDocument {
  schema_version: "1.0.0" | "1.1.0" | "1.2.0" | "1.3.0" | "1.4.0";
  rules_profile?: RuleProfileId;
  id: string;
  revision: number;
  template_id: string;
  status: DocumentStatus;
  created_at: string;
  updated_at: string;
  source: SourceAsset;
  game_prior?: GamePriorSnapshot | null;
  recognition?: RecognitionDocumentState | null;
  header: Header;
  teams: TeamEntry[];
  score_events: ScoreEvent[];
  stated_period_scores: PeriodScore[];
  final_score: FinalScore;
  officials: OfficialEntry[];
  acknowledged_warnings: string[];
}

export interface ValidationIssue {
  id: string;
  severity: "ERROR" | "WARNING";
  code: string;
  region: ScoresheetRegion;
  path: string;
  message: string;
  context: Record<string, unknown>;
}

export interface ScoresheetGameContextReview {
  required: boolean;
  differences: Array<{ field: string; label: string; before: string; after: string }>;
  player_conflicts: Array<{
    side: "A" | "B";
    row: number;
    name: string;
    choices: Array<{ id: string; name: string; label?: string }>;
  }>;
  review_token: string | null;
}

export interface ScoresheetContextPlayerMapping {
  side: "A" | "B";
  row: number;
  player_id: string;
}

export interface ScoresheetDetail {
  id: string;
  game: Record<string, unknown>;
  source: null | {
    id: string;
    url: string;
    filename: string;
    width: number;
    height: number;
    version: number;
  };
  source_version: number;
  status: string;
  draft: ScoresheetDocument;
  draft_version: number;
  event_sequence: number;
  reviewed_regions: Partial<Record<ScoresheetRegion, { draft_version: number }>>;
  validation_report: {
    errors?: ValidationIssue[];
    warnings?: ValidationIssue[];
    computed?: Record<string, unknown>;
    game_context?: ScoresheetGameContextReview;
  };
  validation_draft_version: number | null;
  acknowledged_warnings: string[];
  recognition: null | {
    id: string;
    status: string;
    attempt_count: number;
    max_attempts: number;
    next_attempt_at: string | null;
    model: string;
    prompt_version: string;
    image_sha256: string;
    auto_apply_allowed: boolean;
    last_error_code: string;
    last_error: string;
  };
  lease: null | {
    account_id: string;
    username: string;
    client_id: string;
    surface: ScoresheetSurface;
    expires_at: string;
  };
  publication: null | {
    id: string;
    publication_number: number;
    published_at: string;
  };
}

export interface ScoresheetQueueItem {
  game_id: string;
  game_code: string;
  game_label: string;
  competition: string;
  division_name: string;
  venue: string;
  home_name: string;
  away_name: string;
  date: string;
  start_time: string;
  scoresheet_id: string | null;
  source_asset_id: string | null;
  status: string;
  draft_version: number | null;
  recognition_status: string | null;
  recognition_attempt: number;
  recognition_max_attempts: number;
  next_attempt_at: string | null;
  publication_number: number | null;
}

export const SCORE_BLOCKS = [
  { key: "1-40", start: 1, end: 40 },
  { key: "41-80", start: 41, end: 80 },
  { key: "81-120", start: 81, end: 120 },
  { key: "121-160", start: 121, end: 160 },
] as const;

export function regionForPath(path: string): ScoresheetRegion | "ALL" {
  if (path === "" || path === "/") return "ALL";
  if (path.startsWith("/teams/0")) return "TEAM_A";
  if (path.startsWith("/teams/1")) return "TEAM_B";
  if (path.startsWith("/score_events")) return "RUNNING_SCORE";
  if (path.startsWith("/stated_period_scores") || path.startsWith("/final_score")) return "SUMMARY";
  if (path.startsWith("/officials") || path.startsWith("/recognition/table_personnel")) return "OFFICIALS";
  return "SOURCE_GAME";
}

export function deepCloneDocument(document: ScoresheetDocument): ScoresheetDocument {
  return JSON.parse(JSON.stringify(document)) as ScoresheetDocument;
}

export function hasRecognitionResult(document: ScoresheetDocument): boolean {
  return Boolean(
    document.recognition
    && document.recognition.run_id !== MANUAL_TABLE_PERSONNEL_RUN_ID,
  );
}

export function setTablePersonnel(
  document: ScoresheetDocument,
  names: string[],
): ScoresheetDocument {
  const nextNames = [...names];
  if (!document.recognition) {
    if (nextNames.length === 0) return document;
    document.recognition = {
      run_id: MANUAL_TABLE_PERSONNEL_RUN_ID,
      notes: "",
      table_personnel: nextNames,
      problem_paths: [],
      issues: [],
      applied_at: new Date().toISOString(),
    };
    return document;
  }
  document.recognition.table_personnel = nextNames;
  return document;
}

export function teamBySide(document: ScoresheetDocument, side: TeamSide): TeamEntry {
  const team = document.teams.find((candidate) => candidate.side === side);
  if (!team) throw new Error(`记录表缺少 ${side} 队`);
  return team;
}

export function isValidJerseyNumber(value: string, allowBlank = true): boolean {
  const normalized = value.trim();
  if (!normalized) return allowBlank;
  return normalized === "0" || normalized === "00" || /^(?:[1-9]|[1-9][0-9])$/.test(normalized);
}

export function emptyPlayer(row: number): PlayerEntry {
  return {
    row,
    license_number: "",
    name: "",
    jersey_number: "",
    captain: false,
    participation: "none",
    fouls: [],
    post_foul_markers: [],
  };
}

export function paperPlayerRows(team: TeamEntry): PlayerEntry[] {
  const byRow = new Map(team.players.map((player) => [player.row, player]));
  return Array.from({ length: 12 }, (_, index) => byRow.get(index + 1) ?? emptyPlayer(index + 1));
}

export function isBlankPlayer(player: PlayerEntry): boolean {
  return !player.license_number.trim()
    && !player.name.trim()
    && !player.jersey_number.trim()
    && !player.captain
    && player.participation === "none"
    && player.fouls.length === 0
    && player.post_foul_markers.length === 0;
}

export function sparsePlayerRows(players: PlayerEntry[]): PlayerEntry[] {
  return players
    .filter((player) => !isBlankPlayer(player))
    .map((player) => ({ ...player }))
    .sort((left, right) => left.row - right.row);
}

export function isOrderedFoulSlotEnabled(entries: FoulEntry[], slot: number): boolean {
  return slot === 1 || entries.some((entry) => entry.slot === slot - 1);
}

export function isOrderedPostFoulSlotEnabled(
  formalEntries: FoulEntry[],
  postEntries: FoulEntry[],
  requiredFormalSlot: number,
  slot: number,
): boolean {
  return formalEntries.some((entry) => entry.slot === requiredFormalSlot)
    && (slot === 1 || postEntries.some((entry) => entry.slot === slot - 1));
}

export function setOrderedFormalFoul(
  formalEntries: FoulEntry[],
  postEntries: FoulEntry[],
  slot: number,
  value: FoulEntry | null | undefined,
): { formalEntries: FoulEntry[]; postEntries: FoulEntry[] } {
  if (!value) {
    return {
      formalEntries: formalEntries.filter((entry) => entry.slot < slot),
      postEntries: [],
    };
  }
  if (!isOrderedFoulSlotEnabled(formalEntries, slot)) {
    return { formalEntries: [...formalEntries], postEntries: [...postEntries] };
  }
  const next = formalEntries.filter((entry) => entry.slot !== slot);
  next.push({ ...value, slot });
  next.sort((left, right) => left.slot - right.slot);
  return { formalEntries: next, postEntries: [...postEntries] };
}

export function setOrderedPostFoul(
  formalEntries: FoulEntry[],
  postEntries: FoulEntry[],
  requiredFormalSlot: number,
  slot: number,
  value: FoulEntry | null | undefined,
): FoulEntry[] {
  if (!value) return postEntries.filter((entry) => entry.slot < slot);
  if (!isOrderedPostFoulSlotEnabled(formalEntries, postEntries, requiredFormalSlot, slot)) {
    return [...postEntries];
  }
  const next = postEntries.filter((entry) => entry.slot !== slot);
  next.push({ ...value, slot });
  return next.sort((left, right) => left.slot - right.slot);
}

export function semanticScoresheetPath(path: string, document?: ScoresheetDocument): string {
  if (!path || path === "/") return "整份记录表";
  const parts = path.split("/").filter(Boolean);
  if (parts[0] === "header") {
    const labels: Record<string, string> = {
      competition: "赛事",
      game_number: "比赛编号",
      date: "比赛日期",
      scheduled_time: "开赛时间",
      venue: "比赛场地",
      crew_chief: "主裁",
      umpire_1: "第一副裁",
      umpire_2: "第二副裁",
    };
    return labels[parts[1]] ?? "比赛信息";
  }
  if (parts[0] === "teams") {
    const teamIndex = Number(parts[1]);
    const side = teamIndex === 1 ? "B" : "A";
    const team = document?.teams[teamIndex];
    if (parts[2] === "players") {
      const playerIndex = Number(parts[3]);
      const row = team?.players[playerIndex]?.row ?? playerIndex + 1;
      const labels: Record<string, string> = {
        license_number: "证件号码",
        name: "姓名",
        jersey_number: "球衣号码",
        participation: "上场状态",
        captain: "队长",
        fouls: "犯规格",
        post_foul_markers: "附加标记",
      };
      return `${side} 队第 ${row} 行${labels[parts[4]] ?? "队员信息"}`;
    }
    const labels: Record<string, string> = {
      name: "球队名称",
      timeouts: "暂停分钟",
      team_fouls: "全队犯规",
      head_coach: "教练员",
      assistant_coach: "助理教练员",
      coach_fouls: "教练犯规",
      coach_post_foul_markers: "教练附加标记",
      assistant_coach_fouls: "助理教练犯规",
      assistant_coach_post_foul_markers: "助理教练附加标记",
    };
    return `${side} 队${labels[parts[2]] ?? "信息"}`;
  }
  if (parts[0] === "score_events") {
    const index = Number(parts[1]);
    const event = document?.score_events[index];
    return event
      ? `${event.team} 队累计 ${event.cumulative_score} 分格`
      : `逐次得分第 ${index + 1} 项`;
  }
  if (parts[0] === "stated_period_scores") {
    const index = Number(parts[1]);
    const period = document?.stated_period_scores[index]?.period ?? index + 1;
    const periodLabel = period === 5 ? "决胜期合计" : `第 ${period} 节`;
    const side = parts[2] === "team_b" ? "B 队" : parts[2] === "team_a" ? "A 队" : "比分";
    return `${periodLabel} · ${side}`;
  }
  if (parts[0] === "final_score") {
    const labels: Record<string, string> = {
      team_a: "A 队最终比分",
      team_b: "B 队最终比分",
      winner_name: "胜队",
      ended_at: "比赛结束时间",
    };
    return labels[parts[1]] ?? "最终结果";
  }
  if (parts[0] === "officials") {
    const index = Number(parts[1]);
    const role = document?.officials[index]?.role;
    return role ? OFFICIAL_LABELS[role] : "工作人员";
  }
  if (
    parts[0] === "table_personnel"
    || (parts[0] === "recognition" && parts[1] === "table_personnel")
  ) return "记录台人员";
  return "记录表字段";
}

export function timeoutMinute(team: TeamEntry, scope: TimeoutScope, slot: number): number | null {
  return team.timeouts.find((timeout) => timeout.scope === scope && timeout.slot === slot)?.minute ?? null;
}

export function setTimeoutMinute(
  team: TeamEntry,
  scope: TimeoutScope,
  slot: number,
  minute: number | null,
): TeamEntry {
  const next = { ...team, timeouts: team.timeouts.filter((entry) => !(entry.scope === scope && entry.slot === slot)) };
  if (minute !== null) next.timeouts.push({ scope, slot, minute: Math.max(0, Math.min(10, Math.trunc(minute))) });
  next.timeouts.sort((left, right) => left.scope.localeCompare(right.scope) || left.slot - right.slot);
  return next;
}

export function semanticMark(points: number | null): Pick<ScoreEvent, "mark" | "scorer_circled"> {
  if (points === 1) return { mark: "filled_dot", scorer_circled: false };
  if (points === 2) return { mark: "diagonal", scorer_circled: false };
  if (points === 3) return { mark: "diagonal", scorer_circled: true };
  return { mark: null, scorer_circled: false };
}

export function periodScore(document: ScoresheetDocument, period: GamePeriod): PeriodScore {
  return document.stated_period_scores.find((score) => score.period === period)
    ?? { period, team_a: 0, team_b: 0 };
}

export function setPeriodScore(
  document: ScoresheetDocument,
  period: GamePeriod,
  side: TeamSide,
  value: number,
): ScoresheetDocument {
  const normalized = Math.max(0, Math.min(160, Math.trunc(value)));
  const current = periodScore(document, period);
  const next = { ...current, [side === "A" ? "team_a" : "team_b"]: normalized };
  const index = document.stated_period_scores.findIndex((score) => score.period === period);
  if (index >= 0) document.stated_period_scores[index] = next;
  else document.stated_period_scores.push(next);
  document.stated_period_scores.sort((left, right) => left.period - right.period);
  return deriveScoreEvents(document);
}

export function deriveFinalScore(document: ScoresheetDocument): FinalScore {
  const totals = document.stated_period_scores.reduce(
    (result, score) => ({ team_a: result.team_a + score.team_a, team_b: result.team_b + score.team_b }),
    { team_a: 0, team_b: 0 },
  );
  const teamA = teamBySide(document, "A");
  const teamB = teamBySide(document, "B");
  document.final_score = {
    ...document.final_score,
    ...totals,
    winner_name: totals.team_a > totals.team_b ? teamA.name : totals.team_b > totals.team_a ? teamB.name : "",
  };
  return document.final_score;
}

export function periodCheckpoints(
  document: ScoresheetDocument,
  side: TeamSide,
): Array<{ period: GamePeriod; cumulative: number }> {
  const byPeriod = new Map(document.stated_period_scores.map((score) => [score.period, score]));
  const periods: GamePeriod[] = [1, 2, 3, 4];
  if (byPeriod.has(5)) periods.push(5);
  let cumulative = 0;
  return periods.map((period) => {
    const score = byPeriod.get(period);
    if (score) cumulative += side === "A" ? score.team_a : score.team_b;
    return { period, cumulative };
  });
}

function periodForScore(
  cumulativeScore: number,
  checkpoints: Array<{ period: GamePeriod; cumulative: number }>,
): GamePeriod {
  const covering = checkpoints.find((checkpoint) => cumulativeScore <= checkpoint.cumulative);
  if (covering) return covering.period;
  const last = checkpoints.at(-1);
  return last && last.cumulative > 0 ? last.period : 1;
}

export function deriveScoreEvents(document: ScoresheetDocument): ScoresheetDocument {
  deriveFinalScore(document);
  const bySide = new Map<TeamSide, ScoreEvent[]>([
    ["A", document.score_events.filter((event) => event.team === "A")],
    ["B", document.score_events.filter((event) => event.team === "B")],
  ]);
  (["A", "B"] as TeamSide[]).forEach((side) => {
    const events = bySide.get(side)!
      .sort((left, right) => left.cumulative_score - right.cumulative_score || left.sequence - right.sequence);
    const checkpoints = periodCheckpoints(document, side);
    let previous = 0;
    events.forEach((event) => {
      const delta = event.cumulative_score - previous;
      event.points = delta >= 1 ? delta : null;
      Object.assign(event, semanticMark(event.points));
      event.period = periodForScore(event.cumulative_score, checkpoints);
      event.ink_role = event.period === 1 || event.period === 3 ? "q1_q3" : "q2_q4_ot";
      event.boundary = "none";
      previous = event.cumulative_score;
    });
    const byCumulative = new Map(events.map((event) => [event.cumulative_score, event]));
    checkpoints.forEach(({ cumulative }) => {
      if (cumulative > 0) {
        const event = byCumulative.get(cumulative);
        if (event) event.boundary = "period_end";
      }
    });
  });
  const latestA = bySide.get("A")!.at(-1);
  const latestB = bySide.get("B")!.at(-1);
  if (
    latestA
    && latestB
    && latestA.cumulative_score === document.final_score.team_a
    && latestB.cumulative_score === document.final_score.team_b
  ) {
    latestA.boundary = "game_end";
    latestB.boundary = "game_end";
  }
  document.score_events.sort((left, right) => (
    left.period - right.period
    || left.team.localeCompare(right.team)
    || left.cumulative_score - right.cumulative_score
    || left.sequence - right.sequence
  ));
  document.score_events.forEach((event, index) => { event.sequence = index + 1; });
  return document;
}

export function setScoreCell(
  document: ScoresheetDocument,
  side: TeamSide,
  cumulativeScore: number,
  scorerJersey: string,
): ScoreEvent {
  let event = document.score_events.find(
    (candidate) => candidate.team === side && candidate.cumulative_score === cumulativeScore,
  );
  if (!event) {
    event = {
      sequence: Math.max(0, ...document.score_events.map((candidate) => candidate.sequence)) + 1,
      team: side,
      period: 1,
      points: null,
      cumulative_score: cumulativeScore,
      scorer_jersey: scorerJersey,
      mark: null,
      scorer_circled: false,
      boundary: "none",
      ink_role: "neutral",
    };
    document.score_events.push(event);
  } else {
    event.scorer_jersey = scorerJersey;
  }
  deriveScoreEvents(document);
  return document.score_events.find(
    (candidate) => candidate.team === side && candidate.cumulative_score === cumulativeScore,
  )!;
}

export function removeScoreCell(
  document: ScoresheetDocument,
  side: TeamSide,
  cumulativeScore: number,
): ScoresheetDocument {
  document.score_events = document.score_events.filter(
    (event) => event.team !== side || event.cumulative_score !== cumulativeScore,
  );
  return deriveScoreEvents(document);
}

export function scoreTotalsByPeriod(document: ScoresheetDocument, side: TeamSide): Map<number, number> {
  const totals = new Map<number, number>();
  document.score_events
    .filter((event) => event.team === side)
    .forEach((event) => {
      if (event.points === 1 || event.points === 2 || event.points === 3) {
        totals.set(event.period, (totals.get(event.period) ?? 0) + event.points);
      }
    });
  return totals;
}

export function scoreGridRow(
  cumulative: number,
): { block: 0 | 1 | 2 | 3; row: number } | null {
  if (!Number.isInteger(cumulative) || cumulative < 1 || cumulative > 160) return null;
  return {
    block: Math.floor((cumulative - 1) / 40) as 0 | 1 | 2 | 3,
    row: (cumulative - 1) % 40,
  };
}

export function affectedRegions(
  events: Array<{ changed_fields?: Array<{ path?: string }> }>,
): Set<ScoresheetRegion> {
  const regions = new Set<ScoresheetRegion>();
  for (const event of events) {
    for (const change of event.changed_fields ?? []) {
      const region = regionForPath(change.path ?? "/");
      if (region === "ALL") SCORESHEET_REGIONS.forEach((item) => regions.add(item));
      else regions.add(region);
    }
  }
  return regions;
}
