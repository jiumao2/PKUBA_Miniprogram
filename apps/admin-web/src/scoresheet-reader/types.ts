export type TeamSide = 'A' | 'B';
export type ParticipationStatus = 'none' | 'starter' | 'substitute';
export type FoulCode = 'P' | 'T' | 'U' | 'D' | 'C' | 'B' | 'GD' | 'F' | 'DI' | 'FL' | 'BD';
export type FoulMarkStyle = 'plain' | 'circled';
export type RuleProfileId = 'fiba_2024' | 'fiba_2026';
export type ScoreMark = 'filled_dot' | 'diagonal';
export type ScoreBoundary = 'none' | 'period_end' | 'game_end';
export type InkRole = 'q1_q3' | 'q2_q4_ot' | 'neutral';
export type SignaturePresence = 'present' | 'absent' | 'unclear';
export type DocumentStatus = 'draft' | 'needs_review' | 'validated' | 'confirmed';
export type GamePeriod = 1 | 2 | 3 | 4 | 5;
export type RegulationPeriod = 1 | 2 | 3 | 4;

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
  scope: 'H1' | 'H2' | 'OT';
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
  role:
    | 'scorer'
    | 'assistant_scorer'
    | 'timer'
    | 'shot_clock_operator'
    | 'crew_chief'
    | 'umpire_1'
    | 'umpire_2'
    | 'protest_captain';
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

export interface RecognitionDocumentState {
  run_id: string;
  notes: string;
  table_personnel: string[];
  problem_paths: string[];
  issues?: RecognitionIssue[];
  applied_at: string;
}

export interface RecognitionIssue {
  code: string;
  path: string;
  message: string;
  observed: unknown;
  expected: unknown;
}

export interface ScoresheetDocument {
  schema_version: '1.0.0' | '1.1.0' | '1.2.0' | '1.3.0' | '1.4.0';
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
  code: string;
  severity: 'error' | 'warning' | 'info';
  paths: string[];
  message: string;
  observed: unknown;
  expected: unknown;
}

export interface ValidationReport {
  status: 'valid' | 'needs_review' | 'invalid';
  issues: ValidationIssue[];
  checked_at: string;
}

export type ChangeLogAction =
  | 'human_edit'
  | 'undo'
  | 'redo'
  | 'recognition_merge'
  | 'reupload'
  | 'confirm';

export interface FieldChange {
  path: string;
  before: unknown;
  after: unknown;
}

export interface DocumentChangeLogEntry {
  id: number;
  document_id: string;
  action: ChangeLogAction;
  summary: string;
  changes: FieldChange[];
  created_at: string;
}

export interface DocumentChangeLogPage {
  items: DocumentChangeLogEntry[];
  next_before_id: number | null;
}

export interface GameSummary {
  id: string;
  competition: string;
  division: string;
  date: string;
  scheduled_time: string;
  venue: string;
  team_a_name: string;
  team_b_name: string;
  ready: boolean;
  unavailable_reason: string;
  document_id: string | null;
  scoresheet_state: 'not_uploaded' | 'recognizing' | 'recognized' | 'recognition_failed' | 'confirmed';
}

export interface GameDetail extends GameSummary {
  prior: GamePriorSnapshot | null;
}

export interface RecognitionUsage {
  input_tokens: number;
  output_tokens: number;
  image_tokens: number;
  reasoning_tokens: number;
  total_tokens: number;
}

export interface RecognitionRun {
  id: string;
  document_id: string;
  base_revision: number;
  status: 'pending' | 'connecting' | 'thinking' | 'structuring' | 'validating' | 'succeeded' | 'failed' | 'superseded' | 'interrupted';
  model: string;
  prompt_version: string;
  trigger?: 'upload' | 'reupload' | 'retry' | 'manual' | 'legacy';
  source_version?: number;
  image_sha256?: string;
  superseded_by_run_id?: string | null;
  retry_count?: number;
  attempt_count?: number;
  max_attempts?: number;
  next_attempt_at?: string | null;
  cached: boolean;
  auto_applied: boolean;
  applied_revision: number | null;
  recognition_notes: string;
  usage: RecognitionUsage;
  error: string;
  result: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
}

export interface DocumentRecognitionResponse {
  document: ScoresheetDocument;
  recognition_run: RecognitionRun;
}

export interface RecognitionRegionDiff {
  region: string;
  label: string;
  changed: boolean;
  current: unknown;
  recognized: unknown;
}

export interface RecognitionDiff {
  run_id: string;
  document_id: string;
  base_revision: number;
  current_revision: number;
  regions: RecognitionRegionDiff[];
}

export interface RectDefinition {
  x: number;
  y: number;
  width: number;
  height: number;
  baseline?: number;
  font_size?: number;
  anchor?: 'start' | 'middle';
}

export type CellBounds = [number, number, number, number];

export interface TemplateDefinition {
  template_id: string;
  display_name: string;
  coordinate_system: 'pdf_points_top_left';
  page: { width: number; height: number };
  outer_bounds: RectDefinition;
  header_fields: Record<string, RectDefinition>;
  team_layouts: Record<
    TeamSide,
    {
      section_top: number;
      section_bottom: number;
      team_name: RectDefinition;
      player_header_top: number;
      player_rows: number[];
      coach_rows: Record<'head' | 'assistant', [number, number]>;
      timeouts: Record<'H1' | 'H2' | 'OT', { cells: CellBounds[] }>;
      team_fouls: Record<string, { cells: CellBounds[] }>;
    }
  >;
  player_columns: {
    license: [number, number];
    name: [number, number];
    jersey: [number, number];
    participation: [number, number];
    fouls: [number, number][];
    coach_fouls: [number, number][];
    post_foul: [number, number];
  };
  running_score: {
    group_boundaries: number[];
    row_boundaries: number[];
    cell_offsets: Record<'a_player' | 'a_score' | 'b_score' | 'b_player', number>;
  };
  summary_fields: {
    period_a_x: number;
    period_b_x: number;
    period_baselines: number[];
    final_a: { x: number; baseline: number };
    final_b: { x: number; baseline: number };
    winner: { x: number; baseline: number; width: number; anchor?: 'start' | 'middle' };
    ended_at: { x: number; baseline: number; width: number; anchor?: 'start' | 'middle' };
  };
  official_fields: Record<string, { x: number; baseline: number; width: number; anchor?: 'start' | 'middle' }>;
  ink_styles: Record<string, Record<string, string>>;
  cells: {
    id: string;
    rect: RectDefinition;
    editor: string;
    data_path: string;
    ink_style: string;
  }[];
}

export const teamBySide = (document: ScoresheetDocument, side: TeamSide) =>
  document.teams.find((team) => team.side === side)!;

export const deepCloneDocument = (document: ScoresheetDocument): ScoresheetDocument =>
  structuredClone(document);
