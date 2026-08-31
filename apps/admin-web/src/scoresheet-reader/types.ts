import type {
  ScoresheetGameContextReview,
  DocumentStatus,
  FinalScore,
  FoulCode,
  FoulEntry,
  FoulMarkStyle,
  GamePeriod,
  GamePriorSnapshot,
  Header,
  InkRole,
  OfficialEntry,
  ParticipationStatus,
  PeriodScore,
  PlayerEntry,
  PostFoulMarker,
  PriorTeam,
  RecognitionDocumentState,
  RecognitionIssue,
  RegulationPeriod,
  RuleProfileId,
  ScoreBoundary,
  ScoreEvent,
  ScoreMark,
  ScoresheetDocument,
  SignaturePresence,
  SourceAsset,
  TeamEntry,
  TeamFoulPeriod,
  TeamSide,
  TimeoutEntry,
} from '@pkuba/scoresheet-domain';

export type {
  DocumentStatus,
  FinalScore,
  FoulCode,
  FoulEntry,
  FoulMarkStyle,
  GamePeriod,
  GamePriorSnapshot,
  Header,
  InkRole,
  OfficialEntry,
  ParticipationStatus,
  PeriodScore,
  PlayerEntry,
  PostFoulMarker,
  PriorTeam,
  RecognitionDocumentState,
  RecognitionIssue,
  RegulationPeriod,
  RuleProfileId,
  ScoreBoundary,
  ScoreEvent,
  ScoreMark,
  ScoresheetDocument,
  SignaturePresence,
  SourceAsset,
  TeamEntry,
  TeamFoulPeriod,
  TeamSide,
  TimeoutEntry,
} from '@pkuba/scoresheet-domain';

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
  game_context?: ScoresheetGameContextReview;
}

export interface ValidationResult {
  document: ScoresheetDocument;
  report: ValidationReport;
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
  can_upload_source: boolean;
  scoresheet_state: 'not_uploaded' | 'recognizing' | 'recognized' | 'recognition_failed' | 'confirmed';
}

export type GameQueueScope = 'ALL' | 'ACTION_REQUIRED' | 'IN_PROGRESS' | 'PUBLISHED';

export interface GameQueueQuery {
  seasonId?: string;
  gameId?: string;
  scope?: GameQueueScope;
  query?: string;
  page?: number;
  pageSize?: number;
}

export interface GameSummaryPage {
  items: GameSummary[];
  total: number;
  page: number;
  page_size: number;
  division_names: string[];
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
  can_retry?: boolean;
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

export { deepCloneDocument, teamBySide } from '@pkuba/scoresheet-domain';
