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
export type ScoreValue = 1 | 2 | 3;
export type ScorePeriod = "1" | "2" | "3" | "4" | "OT";

export const REGION_LABELS: Record<ScoresheetRegion, string> = {
  SOURCE_GAME: "原图与比赛信息",
  TEAM_A: "A 队",
  TEAM_B: "B 队",
  RUNNING_SCORE: "逐次得分",
  SUMMARY: "节比分与最终结果",
  OFFICIALS: "工作人员与签名",
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
  fouls: Array<string | { code: string }>;
}

export interface ScoresheetTeam {
  team_id: string;
  name: string;
  players: ScoresheetPlayer[];
  timeouts: Record<string, unknown[]>;
  team_fouls: Record<string, unknown[]>;
  head_coach: { name: string; fouls: unknown[] };
  assistant_coach: { name: string; fouls: unknown[] };
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
  date: string;
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
