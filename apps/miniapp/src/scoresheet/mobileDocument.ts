import type { ScoresheetDetail } from "@pkuba/api-client";
import type {
  ScoreEvent,
  ScorePeriod,
  ScoresheetDocument,
  ScoresheetPlayer,
  ScoresheetTeam,
  TeamSide,
  ValidationIssue,
} from "@pkuba/scoresheet-domain";

type CanonicalFoul = Record<string, unknown> & {
  slot: number;
  code: string;
};

type CanonicalPlayer = Record<string, unknown> & {
  row: number;
  license_number: string;
  name: string;
  jersey_number: string;
  captain: boolean;
  participation: "none" | "starter" | "substitute";
  fouls: CanonicalFoul[];
  post_foul_markers: CanonicalFoul[];
};

type CanonicalTeam = Record<string, unknown> & {
  side: TeamSide;
  name: string;
  players: CanonicalPlayer[];
  timeouts: Array<Record<string, unknown> & { scope: "H1" | "H2" | "OT"; slot: number; minute: number }>;
  team_fouls: Array<Record<string, unknown> & { period: number; count: number }>;
  coach_fouls: CanonicalFoul[];
  coach_post_foul_markers: CanonicalFoul[];
  assistant_coach_fouls: CanonicalFoul[];
  assistant_coach_post_foul_markers: CanonicalFoul[];
  head_coach: string;
  assistant_coach: string;
};

type CanonicalScoreEvent = Record<string, unknown> & {
  sequence: number;
  team: TeamSide;
  period: number;
  points: number | null;
  cumulative_score: number;
  scorer_jersey: string;
  mark: "filled_dot" | "diagonal" | null;
  scorer_circled: boolean;
  boundary: "none" | "period_end" | "game_end";
  ink_role: "q1_q3" | "q2_q4_ot" | "neutral";
};

type CanonicalPeriodScore = Record<string, unknown> & {
  period: number;
  team_a: number;
  team_b: number;
};

type CanonicalOfficial = Record<string, unknown> & {
  role: string;
  name: string;
  signature: "present" | "absent" | "unclear";
};

export type CanonicalScoresheetDocument = Record<string, unknown> & {
  schema_version: string;
  revision: number;
  header: Record<string, string>;
  teams: CanonicalTeam[];
  score_events: CanonicalScoreEvent[];
  stated_period_scores: CanonicalPeriodScore[];
  final_score: Record<string, unknown> & {
    team_a: number;
    team_b: number;
    winner_name: string;
    ended_at: string;
  };
  officials: CanonicalOfficial[];
  source: Record<string, unknown> & {
    rotation: number;
    corners: number[][] | null;
  };
};

type MobileScoreEvent = ScoreEvent & {
  __canonical_sequence?: number;
  __canonical_points?: number | null;
  __canonical_period?: number;
};

export interface MobileScoresheetProjection {
  detail: ScoresheetDetail;
  canonical: CanonicalScoresheetDocument | null;
}

const PERIOD_LABELS: Record<number, ScorePeriod> = {
  1: "1",
  2: "2",
  3: "3",
  4: "4",
};

const FOUL_CODES = new Set(["P", "T", "U", "D", "C", "B", "GD", "F", "DI", "FL", "BD"]);

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}

function isCanonicalDocument(value: unknown): value is CanonicalScoresheetDocument {
  return Boolean(
    isRecord(value)
      && typeof value.schema_version === "string"
      && Array.isArray(value.teams)
      && Array.isArray(value.score_events),
  );
}

function canonicalTeam(document: CanonicalScoresheetDocument, side: TeamSide): CanonicalTeam {
  return document.teams.find((team) => team.side === side) ?? {
    side,
    name: "",
    players: [],
    timeouts: [],
    team_fouls: [],
    coach_fouls: [],
    coach_post_foul_markers: [],
    assistant_coach_fouls: [],
    assistant_coach_post_foul_markers: [],
    head_coach: "",
    assistant_coach: "",
  };
}

function teamId(document: CanonicalScoresheetDocument, side: TeamSide): string {
  const prior = isRecord(document.game_prior) ? document.game_prior : {};
  const team = isRecord(prior[side === "A" ? "team_a" : "team_b"])
    ? prior[side === "A" ? "team_a" : "team_b"] as Record<string, unknown>
    : {};
  return String(team.team_id ?? "");
}

function toMobilePlayer(side: TeamSide, player: CanonicalPlayer): ScoresheetPlayer {
  return {
    player_id: `canonical:${side}:${player.row}`,
    name: player.name,
    jersey_number: player.jersey_number,
    appeared: player.participation !== "none",
    starter: player.participation === "starter",
    captain: player.captain,
    fouls: clone(player.fouls),
  };
}

function toMobileTeam(document: CanonicalScoresheetDocument, side: TeamSide): ScoresheetTeam {
  const team = canonicalTeam(document, side);
  const timeouts: Record<string, unknown[]> = { H1: [], H2: [], OT: [] };
  for (const timeout of team.timeouts) {
    if (timeouts[timeout.scope]) timeouts[timeout.scope].push(clone(timeout));
  }
  const teamFouls: Record<string, unknown[]> = { "1": [], "2": [], "3": [], "4": [] };
  for (const row of team.team_fouls) {
    if (teamFouls[String(row.period)]) teamFouls[String(row.period)] = Array(row.count).fill("X");
  }
  return {
    team_id: teamId(document, side),
    name: team.name,
    players: team.players.map((player) => toMobilePlayer(side, player)),
    timeouts,
    team_fouls: teamFouls,
    head_coach: { name: team.head_coach, fouls: clone(team.coach_fouls) },
    assistant_coach: { name: team.assistant_coach, fouls: clone(team.assistant_coach_fouls) },
  };
}

function mobilePeriod(period: number): ScorePeriod {
  return PERIOD_LABELS[period] ?? "OT";
}

function mobileValue(points: number | null): 1 | 2 | 3 {
  return points === 2 || points === 3 ? points : 1;
}

function toMobileScoreEvent(document: CanonicalScoresheetDocument, event: CanonicalScoreEvent): MobileScoreEvent {
  const team = canonicalTeam(document, event.team);
  const player = team.players.find((row) => row.jersey_number === event.scorer_jersey);
  const value = mobileValue(event.points);
  return {
    id: `canonical:${event.sequence}`,
    sequence: event.sequence,
    team: event.team,
    player_id: player ? `canonical:${event.team}:${player.row}` : "",
    player_name: player?.name,
    player_number: event.scorer_jersey,
    value,
    period: mobilePeriod(event.period),
    cumulative: event.cumulative_score,
    mark: value === 1 ? "dot" : value === 3 ? "circle" : "slash",
    boundary: event.boundary === "period_end" ? "period" : event.boundary === "game_end" ? "game" : "none",
    __canonical_sequence: event.sequence,
    __canonical_points: event.points,
    __canonical_period: event.period,
  };
}

function officialByRole(document: CanonicalScoresheetDocument, role: string): CanonicalOfficial | undefined {
  return document.officials.find((official) => official.role === role);
}

function toMobileDocument(document: CanonicalScoresheetDocument): ScoresheetDocument {
  const periodScores = Object.fromEntries(
    (["1", "2", "3", "4", "OT"] as ScorePeriod[]).map((period) => [period, { A: null, B: null }]),
  ) as ScoresheetDocument["summary"]["period_scores"];
  for (const row of document.stated_period_scores) {
    const period = mobilePeriod(row.period);
    if (row.period <= 5) periodScores[period] = { A: row.team_a, B: row.team_b };
  }
  const teamA = canonicalTeam(document, "A");
  const teamB = canonicalTeam(document, "B");
  const officials: Record<string, string | boolean> = {};
  for (const official of document.officials) officials[official.role] = official.name;
  officials.crew_chief_signature = officialByRole(document, "crew_chief")?.signature === "present";
  officials.umpire_1_signature = officialByRole(document, "umpire_1")?.signature === "present";
  officials.umpire_2_signature = officialByRole(document, "umpire_2")?.signature === "present";
  officials.captain_protest_signature = officialByRole(document, "protest_captain")?.signature === "present";
  return {
    schema_version: 1,
    template_id: document.template_id ? String(document.template_id) : "pku-basketball-2019-v1",
    rule_profile: "fiba_2024",
    game: {
      competition: String(document.header.competition ?? ""),
      game_number: String(document.header.game_number ?? ""),
      date: String(document.header.date ?? ""),
      scheduled_time: String(document.header.scheduled_time ?? ""),
      venue: String(document.header.venue ?? ""),
      crew_chief: String(document.header.crew_chief ?? ""),
      umpire_1: String(document.header.umpire_1 ?? ""),
      umpire_2: String(document.header.umpire_2 ?? ""),
    },
    teams: {
      A: toMobileTeam(document, "A"),
      B: toMobileTeam(document, "B"),
    },
    running_score: document.score_events.map((event) => toMobileScoreEvent(document, event)),
    summary: {
      period_scores: periodScores,
      final_score: { A: document.final_score.team_a, B: document.final_score.team_b },
      winner_side: document.final_score.winner_name === teamA.name
        ? "A"
        : document.final_score.winner_name === teamB.name
          ? "B"
          : "",
      ended_at: document.final_score.ended_at,
    },
    officials,
    source_alignment: {
      corners: Array.isArray(document.source.corners)
        ? document.source.corners.map((point) => ({ x: Number(point[0] ?? 0), y: Number(point[1] ?? 0) }))
        : [],
      rotation: Number(document.source.rotation ?? 0),
    },
  };
}

function mobileIssuePath(path: string): string {
  if (path.startsWith("/header/")) return path.replace("/header/", "/game/");
  if (path.startsWith("/teams/0")) return path.replace("/teams/0", "/teams/A");
  if (path.startsWith("/teams/1")) return path.replace("/teams/1", "/teams/B");
  if (path.startsWith("/score_events")) return path.replace("/score_events", "/running_score");
  if (path.startsWith("/stated_period_scores")) return "/summary/period_scores";
  if (path.startsWith("/final_score/team_a")) return path.replace("/final_score/team_a", "/summary/final_score/A");
  if (path.startsWith("/final_score/team_b")) return path.replace("/final_score/team_b", "/summary/final_score/B");
  if (path.startsWith("/final_score")) return path.replace("/final_score", "/summary");
  if (path.startsWith("/source/rotation")) return "/source_alignment/rotation";
  if (path.startsWith("/source/corners")) return "/source_alignment/corners";
  return path;
}

function mobileIssue(issue: ValidationIssue): ValidationIssue {
  const paths = (issue as ValidationIssue & { paths?: string[] }).paths?.map(mobileIssuePath);
  return {
    ...issue,
    path: mobileIssuePath(issue.path),
    ...(paths ? { paths } : {}),
  };
}

export function projectScoresheetDetail(raw: ScoresheetDetail): MobileScoresheetProjection {
  const rawDraft = (raw as unknown as { draft: unknown }).draft;
  if (!isCanonicalDocument(rawDraft)) return { detail: raw, canonical: null };
  const canonical = clone(rawDraft);
  return {
    canonical,
    detail: {
      ...raw,
      draft: toMobileDocument(canonical),
      validation_report: {
        ...raw.validation_report,
        errors: (raw.validation_report.errors ?? []).map(mobileIssue),
        warnings: (raw.validation_report.warnings ?? []).map(mobileIssue),
      },
    },
  };
}

function parseFoul(value: unknown, slot: number): CanonicalFoul | null {
  if (isRecord(value) && typeof value.code === "string") {
    return { ...clone(value), slot, code: value.code } as CanonicalFoul;
  }
  const match = String(value ?? "").trim().toUpperCase().match(/^\(?([A-Z]+)\)?([123]|C)?$/);
  if (!match || !FOUL_CODES.has(match[1])) return null;
  return {
    slot,
    code: match[1],
    catalog_id: null,
    mark_style: String(value).trim().startsWith("(") ? "circled" : "plain",
    free_throws: /^[123]$/.test(match[2] ?? "") ? Number(match[2]) : null,
    cancelled: match[2] === "C",
    period: null,
  };
}

function mergeFouls(values: unknown[]): CanonicalFoul[] {
  return values
    .map((value, index) => parseFoul(value, index + 1))
    .filter((value): value is CanonicalFoul => value !== null);
}

function mergeTimeouts(team: ScoresheetTeam): CanonicalTeam["timeouts"] {
  const result: CanonicalTeam["timeouts"] = [];
  for (const scope of ["H1", "H2", "OT"] as const) {
    for (const [index, value] of (team.timeouts[scope] ?? []).entries()) {
      const minute = isRecord(value) ? Number(value.minute) : Number(value);
      if (!Number.isInteger(minute) || minute < 0 || minute > 10) continue;
      result.push({ ...(isRecord(value) ? clone(value) : {}), scope, slot: index + 1, minute });
    }
  }
  return result;
}

function mergeTeamFouls(team: ScoresheetTeam): CanonicalTeam["team_fouls"] {
  return [1, 2, 3, 4].map((period) => ({
    period,
    count: Math.max(0, Math.min(4, (team.team_fouls[String(period)] ?? []).length)),
  }));
}

function mergePlayer(player: ScoresheetPlayer, original: CanonicalPlayer | undefined, index: number): CanonicalPlayer {
  return {
    ...(original ? clone(original) : {}),
    row: original?.row ?? index + 1,
    license_number: original?.license_number ?? "",
    name: player.name,
    jersey_number: player.jersey_number,
    captain: player.captain,
    participation: player.starter ? "starter" : player.appeared ? "substitute" : "none",
    fouls: mergeFouls(player.fouls),
    post_foul_markers: clone(original?.post_foul_markers ?? []),
  };
}

function mergeTeam(mobile: ScoresheetTeam, original: CanonicalTeam): CanonicalTeam {
  return {
    ...clone(original),
    name: mobile.name,
    players: mobile.players.map((player, index) => mergePlayer(player, original.players[index], index)),
    timeouts: mergeTimeouts(mobile),
    team_fouls: mergeTeamFouls(mobile),
    coach_fouls: mergeFouls(mobile.head_coach.fouls),
    coach_post_foul_markers: clone(original.coach_post_foul_markers),
    assistant_coach_fouls: mergeFouls(mobile.assistant_coach.fouls),
    assistant_coach_post_foul_markers: clone(original.assistant_coach_post_foul_markers),
    head_coach: mobile.head_coach.name,
    assistant_coach: mobile.assistant_coach.name,
  };
}

function canonicalPeriod(event: MobileScoreEvent, original: CanonicalScoreEvent | undefined): number {
  if (event.period !== "OT") return Number(event.period);
  if (original && original.period >= 5 && event.__canonical_period === original.period) return original.period;
  return 5;
}

function mergeScoreEvent(event: MobileScoreEvent, original: CanonicalScoreEvent | undefined, index: number): CanonicalScoreEvent {
  const period = canonicalPeriod(event, original);
  const displayedOriginal = original ? mobileValue(original.points) : null;
  const pointsUnchanged = Boolean(original && event.value === displayedOriginal && event.__canonical_points === original.points);
  const points = pointsUnchanged ? original!.points : event.value;
  return {
    ...(original ? clone(original) : {}),
    sequence: index + 1,
    team: event.team,
    period,
    points,
    cumulative_score: event.cumulative,
    scorer_jersey: event.player_number,
    mark: pointsUnchanged ? original!.mark : points === 1 ? "filled_dot" : "diagonal",
    scorer_circled: pointsUnchanged ? original!.scorer_circled : points === 3,
    boundary: event.boundary === "period" ? "period_end" : event.boundary === "game" ? "game_end" : "none",
    ink_role: original && original.period === period
      ? original.ink_role
      : period === 1 || period === 3
        ? "q1_q3"
        : "q2_q4_ot",
  };
}

function updateOfficial(
  original: CanonicalOfficial,
  mobile: ScoresheetDocument["officials"],
): CanonicalOfficial {
  const signatureKey = original.role === "protest_captain"
    ? "captain_protest_signature"
    : `${original.role}_signature`;
  const visibleSignature = signatureKey in mobile;
  const nextPresent = Boolean(mobile[signatureKey]);
  const originalPresent = original.signature === "present";
  return {
    ...clone(original),
    name: typeof mobile[original.role] === "string" ? String(mobile[original.role]) : original.name,
    signature: visibleSignature && nextPresent !== originalPresent
      ? nextPresent ? "present" : "absent"
      : original.signature,
  };
}

export function mergeMobileDocument(
  mobile: ScoresheetDocument,
  canonical: CanonicalScoresheetDocument | null,
): ScoresheetDocument | CanonicalScoresheetDocument {
  if (!canonical) return mobile;
  const merged = clone(canonical);
  merged.header = {
    ...merged.header,
    ...Object.fromEntries(Object.entries(mobile.game).map(([key, value]) => [key, String(value ?? "")])),
  };
  merged.teams = (["A", "B"] as TeamSide[]).map((side) =>
    mergeTeam(mobile.teams[side], canonicalTeam(canonical, side)),
  );
  const originals = new Map(canonical.score_events.map((event) => [event.sequence, event]));
  merged.score_events = mobile.running_score.map((row, index) => {
    const event = row as MobileScoreEvent;
    return mergeScoreEvent(event, originals.get(event.__canonical_sequence ?? -1), index);
  });
  const byPeriod = new Map(canonical.stated_period_scores.map((row) => [row.period, clone(row)]));
  for (const [label, period] of [["1", 1], ["2", 2], ["3", 3], ["4", 4], ["OT", 5]] as const) {
    const row = mobile.summary.period_scores[label];
    if (row.A === null || row.B === null) byPeriod.delete(period);
    else byPeriod.set(period, { ...(byPeriod.get(period) ?? {}), period, team_a: row.A, team_b: row.B });
  }
  merged.stated_period_scores = [...byPeriod.values()].sort((left, right) => left.period - right.period);
  merged.final_score = {
    ...merged.final_score,
    team_a: mobile.summary.final_score.A ?? 0,
    team_b: mobile.summary.final_score.B ?? 0,
    winner_name: mobile.summary.winner_side === "A"
      ? mobile.teams.A.name
      : mobile.summary.winner_side === "B"
        ? mobile.teams.B.name
        : "",
    ended_at: mobile.summary.ended_at,
  };
  merged.officials = canonical.officials.map((official) => updateOfficial(official, mobile.officials));
  merged.source = {
    ...merged.source,
    rotation: mobile.source_alignment.rotation,
    corners: mobile.source_alignment.corners.length === 4
      ? mobile.source_alignment.corners.map((point) => [point.x, point.y])
      : null,
  };
  return merged;
}
