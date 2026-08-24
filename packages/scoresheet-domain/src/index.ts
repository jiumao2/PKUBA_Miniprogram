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
export type ScoreValue = number;
export type ScorePeriod = "1" | "2" | "3" | "4" | "5";
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

export interface ScoresheetPlayer {
  player_id: string;
  name: string;
  jersey_number: string;
  appeared: boolean;
  starter: boolean;
  captain: boolean;
  license_number?: string;
  fouls: Array<string | { code: string }>;
  post_foul_markers?: Array<string | { code: string }>;
}

export interface ScoresheetTeam {
  team_id: string;
  name: string;
  players: ScoresheetPlayer[];
  timeouts: Record<string, unknown[]>;
  team_fouls: Record<string, unknown[]>;
  head_coach: { name: string; fouls: unknown[] };
  assistant_coach: { name: string; fouls: unknown[] };
  coach_post_foul_markers?: unknown[];
  assistant_coach_post_foul_markers?: unknown[];
}

export interface ScoreEvent {
  id: string;
  sequence: number;
  team: TeamSide;
  player_id: string;
  player_name?: string;
  player_number: string;
  value: ScoreValue;
  period: ScorePeriod;
  cumulative: number;
  mark?: "dot" | "slash" | "circle";
  boundary?: "none" | "period" | "game";
}

export interface ScoresheetDocument {
  schema_version: 1;
  template_id: string;
  rule_profile: "fiba_2024";
  game: Record<string, string>;
  teams: Record<TeamSide, ScoresheetTeam>;
  running_score: ScoreEvent[];
  summary: {
    period_scores: Record<ScorePeriod, Record<TeamSide, number | null>>;
    final_score: Record<TeamSide, number | null>;
    winner_side: "" | TeamSide;
    ended_at: string;
  };
  officials: Record<string, string | boolean>;
  table_personnel: string[];
  source_alignment: {
    corners: Array<{ x: number; y: number }>;
    rotation: number;
  };
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
  if (path.startsWith("/teams/A")) return "TEAM_A";
  if (path.startsWith("/teams/B")) return "TEAM_B";
  if (path.startsWith("/running_score")) return "RUNNING_SCORE";
  if (path.startsWith("/summary")) return "SUMMARY";
  if (path.startsWith("/officials")) return "OFFICIALS";
  return "SOURCE_GAME";
}

export function teamTotal(events: ScoreEvent[], side: TeamSide): number {
  return events
    .filter((event) => event.team === side)
    .reduce((total, event) => total + event.value, 0);
}

export function nextLegalCumulative(
  events: ScoreEvent[],
  side: TeamSide,
  value: ScoreValue,
): number | null {
  const next = teamTotal(events, side) + value;
  return next <= 160 ? next : null;
}

export function canPlaceScore(
  events: ScoreEvent[],
  side: TeamSide,
  value: ScoreValue,
  cumulative: number,
): boolean {
  return nextLegalCumulative(events, side, value) === cumulative;
}

export function addScoreEvent(
  events: ScoreEvent[],
  input: {
    id: string;
    team: TeamSide;
    value: ScoreValue;
    period: ScorePeriod;
    player_id?: string;
    player_name?: string;
    player_number?: string;
  },
): ScoreEvent[] {
  const cumulative = nextLegalCumulative(events, input.team, input.value);
  if (cumulative === null) throw new Error("累计分不能超过 160 分");
  const event: ScoreEvent = {
    id: input.id,
    sequence: events.length + 1,
    team: input.team,
    value: input.value,
    period: input.period,
    player_id: input.player_id ?? "",
    player_name: input.player_name,
    player_number: input.player_number ?? "",
    cumulative,
    mark: input.value === 1 ? "dot" : input.value === 3 ? "circle" : "slash",
    boundary: "none",
  };
  return [...events, event];
}

export function normalizeScoreEvents(events: ScoreEvent[]): ScoreEvent[] {
  const totals: Record<TeamSide, number> = { A: 0, B: 0 };
  return events.map((event, index) => {
    totals[event.team] += event.value;
    return {
      ...event,
      sequence: index + 1,
      cumulative: totals[event.team],
      mark: event.value === 1 ? "dot" : event.value === 3 ? "circle" : "slash",
    };
  });
}

function scoreMark(value: number): ScoreEvent["mark"] {
  return value === 1 ? "dot" : value === 2 ? "slash" : value === 3 ? "circle" : undefined;
}

export function periodCheckpoints(
  document: ScoresheetDocument,
  side: TeamSide,
): Array<{ period: ScorePeriod; cumulative: number }> {
  let cumulative = 0;
  return (["1", "2", "3", "4", "5"] as ScorePeriod[])
    .filter((period) => Number(period) <= 4 || (
      document.summary.period_scores[period].A !== null
      && document.summary.period_scores[period].B !== null
    ))
    .map((period) => {
      cumulative += document.summary.period_scores[period][side] ?? 0;
      return { period, cumulative };
    });
}

function scorePeriod(
  cumulative: number,
  checkpoints: Array<{ period: ScorePeriod; cumulative: number }>,
): ScorePeriod {
  const covering = checkpoints.find((checkpoint) => cumulative <= checkpoint.cumulative);
  if (covering) return covering.period;
  const last = checkpoints.at(-1);
  return last && last.cumulative > 0 ? last.period : "1";
}

export function deriveScoreEvents(document: ScoresheetDocument): ScoresheetDocument {
  const bySide = new Map<TeamSide, ScoreEvent[]>([
    ["A", document.running_score.filter((event) => event.team === "A")],
    ["B", document.running_score.filter((event) => event.team === "B")],
  ]);
  (["A", "B"] as TeamSide[]).forEach((side) => {
    const events = bySide.get(side)!
      .sort((left, right) => left.cumulative - right.cumulative || left.sequence - right.sequence);
    const checkpoints = periodCheckpoints(document, side);
    let previous = 0;
    events.forEach((event) => {
      event.value = event.cumulative - previous;
      event.mark = scoreMark(event.value);
      event.period = scorePeriod(event.cumulative, checkpoints);
      event.boundary = "none";
      previous = event.cumulative;
    });
    const byCumulative = new Map(events.map((event) => [event.cumulative, event]));
    checkpoints.forEach(({ cumulative }) => {
      if (cumulative > 0) {
        const event = byCumulative.get(cumulative);
        if (event) event.boundary = "period";
      }
    });
  });
  const latestA = bySide.get("A")!.at(-1);
  const latestB = bySide.get("B")!.at(-1);
  if (
    latestA
    && latestB
    && latestA.cumulative === document.summary.final_score.A
    && latestB.cumulative === document.summary.final_score.B
  ) {
    latestA.boundary = "game";
    latestB.boundary = "game";
  }
  document.running_score.sort((left, right) => (
    Number(left.period) - Number(right.period)
    || left.team.localeCompare(right.team)
    || left.cumulative - right.cumulative
    || left.sequence - right.sequence
  ));
  document.running_score.forEach((event, index) => { event.sequence = index + 1; });
  return document;
}

export function setScoreCell(
  document: ScoresheetDocument,
  input: Omit<ScoreEvent, "sequence" | "value" | "mark" | "period" | "boundary"> & {
    cumulative: number;
  },
): ScoresheetDocument {
  let event = document.running_score.find(
    (candidate) => candidate.team === input.team && candidate.cumulative === input.cumulative,
  );
  if (!event) {
    event = {
      ...input,
      sequence: Math.max(0, ...document.running_score.map((candidate) => candidate.sequence)) + 1,
      value: 1,
      period: "1",
      mark: "dot",
      boundary: "none",
    };
    document.running_score.push(event);
  } else {
    event.player_id = input.player_id;
    event.player_name = input.player_name;
    event.player_number = input.player_number;
  }
  return deriveScoreEvents(document);
}

export function removeScoreCell(
  document: ScoresheetDocument,
  side: TeamSide,
  cumulative: number,
): ScoresheetDocument {
  document.running_score = document.running_score.filter(
    (event) => event.team !== side || event.cumulative !== cumulative,
  );
  return deriveScoreEvents(document);
}

function resequenceWithoutMovingCells(events: ScoreEvent[]): ScoreEvent[] {
  return events.map((event, index) => ({ ...event, sequence: index + 1 }));
}

export function insertScoreAt(
  events: ScoreEvent[],
  input: Omit<ScoreEvent, "sequence" | "value" | "mark"> & { cumulative: number },
): ScoreEvent[] {
  if (events.some((event) => event.team === input.team && event.cumulative === input.cumulative)) {
    throw new Error("该累计分格已经填写");
  }
  const sameSide = events
    .filter((event) => event.team === input.team)
    .sort((left, right) => left.cumulative - right.cumulative);
  const previous = [...sameSide].reverse().find((event) => event.cumulative < input.cumulative);
  const next = sameSide.find((event) => event.cumulative > input.cumulative);
  const value = input.cumulative - (previous?.cumulative ?? 0);
  const inserted: ScoreEvent = { ...input, sequence: 0, value, mark: scoreMark(value) };
  const result = events.map((event) => event.id === next?.id
    ? { ...event, value: event.cumulative - input.cumulative, mark: scoreMark(event.cumulative - input.cumulative) }
    : event);
  const insertIndex = next ? result.findIndex((event) => event.id === next.id) : result.length;
  result.splice(insertIndex < 0 ? result.length : insertIndex, 0, inserted);
  return resequenceWithoutMovingCells(result);
}

export function deleteScoreAt(events: ScoreEvent[], eventId: string): ScoreEvent[] {
  const removed = events.find((event) => event.id === eventId);
  if (!removed) return events;
  const previous = events
    .filter((event) => event.team === removed.team && event.cumulative < removed.cumulative)
    .sort((left, right) => right.cumulative - left.cumulative)[0];
  const next = events
    .filter((event) => event.team === removed.team && event.cumulative > removed.cumulative)
    .sort((left, right) => left.cumulative - right.cumulative)[0];
  return resequenceWithoutMovingCells(events
    .filter((event) => event.id !== eventId)
    .map((event) => event.id === next?.id
      ? { ...event, value: event.cumulative - (previous?.cumulative ?? 0), mark: scoreMark(event.cumulative - (previous?.cumulative ?? 0)) }
      : event));
}

export function deleteScoreEvent(events: ScoreEvent[], eventId: string): ScoreEvent[] {
  return normalizeScoreEvents(events.filter((event) => event.id !== eventId));
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
