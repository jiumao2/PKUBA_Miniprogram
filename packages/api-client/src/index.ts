import type { components } from "./generated/schema";
import type {
  ScoresheetContextPlayerMapping,
  ScoresheetDetail,
  ScoresheetQueueItem,
  ScoresheetRegion,
  ScoresheetSurface,
} from "@pkuba/scoresheet-domain";

export type {
  ScoresheetContextPlayerMapping,
  ScoresheetGameContextReview,
  ScoresheetDetail,
  ScoresheetQueueItem,
  ScoresheetRegion,
  ScoresheetSurface,
} from "@pkuba/scoresheet-domain";

export function formatOfficialScore(
  homeScore: number | null,
  awayScore: number | null,
  separator = ":",
): string | null {
  if (homeScore === null || awayScore === null) return null;
  return `${homeScore}${separator}${awayScore}`;
}

export type Division = components["schemas"]["DivisionOut"];
export type Season = components["schemas"]["SeasonOut"];
export type Game = components["schemas"]["GameOut"];
export type ScheduleDays = components["schemas"]["ScheduleDaysOut"];
export type ScheduleDay = components["schemas"]["ScheduleDayOut"];
export type PublicGameDetail = components["schemas"]["PublicGameDetailOut"];
export type HomeDashboard = components["schemas"]["HomeDashboardOut"];
export type Standings = components["schemas"]["StandingsOut"];
export type DivisionStandings = components["schemas"]["DivisionStandingsOut"];
export type GroupStandings = components["schemas"]["GroupStandingsOut"];
export type StandingsEntry = components["schemas"]["StandingsEntryOut"];
export type StandingsMatch = components["schemas"]["StandingsMatchOut"];
export type AdminAccount = components["schemas"]["AccountOut"];
export type AdminSession = components["schemas"]["AdminSessionOut"];
export type LoginChallenge = components["schemas"]["LoginChallengeOut"];
export type AdminWebLoginChallenge = components["schemas"]["AdminWebLoginChallengeOut"];
export type AdminWebLoginStatus = components["schemas"]["AdminWebLoginStatusOut"];
export type AdminWebLoginConfirmation = components["schemas"]["AdminWebLoginConfirmOut"];
export type ScheduleImport = components["schemas"]["ScheduleImportOut"];
export type ImportIssue = components["schemas"]["ImportIssueOut"];
export type ConfirmScheduleImport = components["schemas"]["ConfirmScheduleImportIn"];
export type ScheduleImportReadiness =
  components["schemas"]["ScheduleImportReadinessOut"];
export type ScheduleImportResetPreview =
  components["schemas"]["ScheduleImportResetPreviewOut"];
export type ScheduleImportReset = components["schemas"]["ScheduleImportResetIn"];
export type ScheduleImportResetResult =
  components["schemas"]["ScheduleImportResetResultOut"];
export type AdminManagedAccount = components["schemas"]["AdminAccountOut"];
export type AdminSeason = components["schemas"]["AdminSeasonOut"];
export type SeasonConfiguration = components["schemas"]["SeasonConfigurationOut"];
export type CreateSeason = components["schemas"]["CreateSeasonIn"];
export type UpdateSeasonConfiguration =
  components["schemas"]["UpdateSeasonConfigurationIn"];
export type PreviewSeasonConfiguration =
  components["schemas"]["PreviewSeasonConfigurationIn"];
export type SeasonConfigurationPreview =
  components["schemas"]["SeasonConfigurationPreviewOut"];
export type CapacityLedgerRow = components["schemas"]["CapacityLedgerRowOut"];
export type LifecycleCommand = components["schemas"]["LifecycleCommandIn"];
export type LifecycleApply = components["schemas"]["LifecycleApplyIn"];
export type LifecyclePreview = components["schemas"]["LifecyclePreviewOut"];
export type AdvancedModel = components["schemas"]["AdvancedModelOut"];
export type AdvancedRecord = components["schemas"]["AdvancedRecordOut"];
export type AdvancedRecordList = components["schemas"]["AdvancedRecordListOut"];
export type AdvancedMutation = components["schemas"]["AdvancedMutationIn"];
export type AdvancedMutationApply =
  components["schemas"]["AdvancedMutationApplyIn"];
export type AdvancedMutationPreview =
  components["schemas"]["AdvancedMutationPreviewOut"];
export type RosterDataset = components["schemas"]["RosterDatasetOut"];
export type RosterDivision = components["schemas"]["RosterDivisionOut"];
export type TeamRoster = components["schemas"]["TeamRosterOut"];
export type RosterPlayer = components["schemas"]["RosterPlayerOut"];
export type RosterImport = components["schemas"]["RosterImportOut"];
export type RosterImportIssue = components["schemas"]["RosterImportIssueOut"];
export type RosterImportReadiness = components["schemas"]["RosterImportReadinessOut"];
export type RosterPlayerInput = components["schemas"]["RosterPlayerIn"];
export type CreateTeamRoster = components["schemas"]["CreateTeamRosterIn"];
export type SaveTeamRoster = components["schemas"]["SaveTeamRosterIn"];
export type TeamMaintenancePreview = components["schemas"]["TeamMaintenancePreviewOut"];
export type DrawAssignmentDataset = components["schemas"]["DrawDatasetOut"];
export type DrawAssignmentPreview = components["schemas"]["DrawPreviewOut"];
export type PreviewDrawAssignments = components["schemas"]["DrawPreviewIn"];
export type ApplyDrawAssignments = components["schemas"]["DrawApplyIn"];
export type DrawGameAssignmentPreview = components["schemas"]["DrawGamePreviewOut"];
export type PreviewGameDrawAssignments = components["schemas"]["DrawGamePreviewIn"];
export type ApplyGameDrawAssignments = components["schemas"]["DrawGameApplyIn"];
export type WeChatExchange = components["schemas"]["WeChatExchangeOut"];
export type MiniAppMe = components["schemas"]["MiniAppMeOut"];
export type ClaimableTeam = components["schemas"]["ClaimableTeamOut"];
export type AdminRegistrationPolicy = components["schemas"]["AdminRegistrationPolicyOut"];
export type Brackets = components["schemas"]["BracketsOut"];
export type DivisionBracket = components["schemas"]["DivisionBracketOut"];
export type BracketRound = components["schemas"]["BracketRoundOut"];
export type BracketGame = components["schemas"]["BracketGameOut"];
export type RescheduleRequest = components["schemas"]["RescheduleRequestOut"];
export type RescheduleGame = components["schemas"]["RescheduleGameOut"];
export type RescheduleTarget = components["schemas"]["RescheduleTargetOut"];
export type RescheduleVoterTeam = components["schemas"]["RescheduleVoterTeamOut"];
export type AdminReschedulePage = components["schemas"]["AdminReschedulePageOut"];
export type AdminRescheduleRequest =
  components["schemas"]["AdminRescheduleRequestOut"];
export type AdminRescheduleAction = components["schemas"]["AdminRescheduleActionIn"];
export type MobileAdminGame = components["schemas"]["AdminGameOut"];
export type MobileAdminDashboard = components["schemas"]["MobileDashboardOut"];
export type MobileScheduleOptions = components["schemas"]["ScheduleOptionsOut"];
export type UpdateMobileAdminGame = components["schemas"]["UpdateAdminGameIn"];
export type GameMediaAsset = components["schemas"]["GameMediaAssetOut"];
export type GameMediaCollection = components["schemas"]["GameMediaCollectionOut"];

export interface AdminGameMediaFilters {
  kind?: string;
  seasonId?: string;
  gameId?: string;
}

export type ArchiveKind = "SEASON_DATA" | "SEASON_PHOTOS" | "SYSTEM_RAW";
export type ArchiveStatus = "QUEUED" | "BUILDING" | "READY" | "FAILED" | "EXPIRED" | "DISCARDED";

export interface ArchiveBlocker {
  code: string;
  message: string;
}

export interface ArchivePreview {
  kind: ArchiveKind;
  season_id: string | null;
  season_version: number | null;
  estimated_bytes: number;
  required_free_bytes: number;
  available_bytes: number;
  reserve_bytes: number;
  blockers: ArchiveBlocker[];
  ready: boolean;
}

export interface ArchiveJob {
  id: string;
  kind: ArchiveKind;
  season_id: string | null;
  season_name: string | null;
  season_version: number | null;
  is_final: boolean;
  status: ArchiveStatus;
  filename: string;
  byte_size: number;
  file_sha256: string;
  summary: Record<string, unknown>;
  error_code: string;
  error_message: string;
  download_count: number;
  last_downloaded_at: string | null;
  completed_at: string | null;
  expires_at: string | null;
  confirmed_saved_at: string | null;
  created_at: string;
  version: number;
}

export interface ArchiveDownloadTicket {
  url: string;
  expires_in: number;
  filename: string;
  byte_size: number;
  file_sha256: string;
}

export interface StorageSeason {
  season_id: string;
  season_name: string;
  season_year: number;
  season_status: string;
  scoresheet_bytes: number;
  group_photo_bytes: number;
  game_photo_bytes: number;
  online_bytes: number;
  online_files: number;
}

export interface StorageSummary {
  disk_total_bytes: number;
  disk_used_bytes: number;
  disk_free_bytes: number;
  reserve_bytes: number;
  database_bytes: number;
  online_media_bytes: number;
  staged_artifact_bytes: number;
  seasons: StorageSeason[];
}

export interface MediaPurgePreview {
  season_id: string;
  season_version: number;
  files: number;
  bytes: number;
  by_kind: Record<string, { files: number; bytes: number }>;
  data_archive_id: string | null;
  photo_archive_id: string | null;
  preview_hash: string;
  blockers: ArchiveBlocker[];
  ready: boolean;
}

export interface MediaPurgeJob {
  id: string;
  season_id: string;
  status: string;
  expected_files: number;
  expected_bytes: number;
  deleted_files: number;
  deleted_bytes: number;
  missing_files: number;
  warnings: Array<Record<string, unknown>>;
  error_code: string;
  error_message: string;
  completed_at: string | null;
  created_at: string;
  version: number;
}

export interface ScoresheetLeaseResponse {
  read_only: boolean;
  read_only_reason: string;
  lease_token: string | null;
  holder: {
    account_id: string;
    username: string;
    client_id: string;
    surface: ScoresheetSurface;
    expires_at: string;
  } | null;
}

export interface ScoresheetRecognitionCapability {
  configured: boolean;
  provider: string;
  model: string;
  prompt_version: string;
  max_attempts: number;
  retry_delays_seconds: number[];
}

export interface ScoresheetMutationContext {
  expected_version: number;
  lease_token: string;
  client_id: string;
  surface: ScoresheetSurface;
}

export interface ScoresheetDraftChange {
  path: string;
  operation?: "SET" | "DELETE";
  value?: unknown;
}

export interface ScoresheetSync {
  scoresheet_id: string;
  can_upload_source: boolean;
  current_version: number;
  current_event: number;
  requires_full_reload: boolean;
  events: Array<{
    event_sequence: number;
    draft_version: number;
    event_type: string;
    actor_name: string | null;
    client_id: string;
    surface: string;
    changed_fields: ScoresheetDraftChange[];
    payload: Record<string, unknown>;
    created_at: string;
  }>;
  reviewed_regions: ScoresheetDetail["reviewed_regions"];
  validation_report: ScoresheetDetail["validation_report"];
  status: string;
  recognition: ScoresheetDetail["recognition"];
  lease: ScoresheetDetail["lease"];
  publication: ScoresheetDetail["publication"];
}

export interface PublicScoresheetStat {
  publication_id: string;
  publication_number: number;
  game_id: string;
  game_code: string;
  date: string;
  start_time: string;
  division_name: string;
  home_name: string;
  away_name: string;
  home_score: number;
  away_score: number;
  team_stats: Array<Record<string, unknown>>;
  player_stats: Array<{
    team_id: string;
    team_name: string;
    player_id: string | null;
    player_name: string;
    jersey_number: string;
    appeared: boolean;
    starter: boolean;
    points: number;
    one_point_events: number;
    two_point_events: number;
    three_point_events: number;
    personal_fouls: number;
    foul_types: unknown[];
  }>;
  published_at: string;
}

export interface TeamLeaderboardItem {
  rank: number;
  team_id: string;
  team_name: string;
  division_id: string;
  division_name: string;
  division_gender: string;
  games_played: number;
  wins: number;
  losses: number;
  win_percentage: number;
  points_for: number;
  points_against: number;
  point_difference: number;
  points_per_game: number;
  points_against_per_game: number;
  point_difference_per_game: number;
}

export interface PlayerLeaderboardItem {
  rank: number;
  player_id: string;
  player_name: string;
  jersey_number: string;
  team_id: string;
  team_name: string;
  division_id: string;
  division_name: string;
  division_gender: string;
  games_played: number;
  starts: number;
  total_points: number;
  points_per_game: number;
  one_point_events: number;
  two_point_events: number;
  three_point_events: number;
  personal_fouls: number;
  fouls_per_game: number;
}

export interface LeaderboardPage<T> {
  season_id: string;
  season_name: string;
  division_id: string | null;
  sort: string;
  order: string;
  page: number;
  page_size: number;
  total: number;
  items: T[];
}

export interface PublishedGameSummary {
  publication_id: string;
  publication_number: number;
  game_id: string;
  game_code: string;
  date: string;
  start_time: string;
  division_id: string;
  division_name: string;
  division_gender: string;
  home_name: string;
  away_name: string;
  home_score: number;
  away_score: number;
  published_at: string;
}

export interface PublishedGamePage {
  season_id: string;
  season_name: string;
  division_id: string | null;
  page: number;
  page_size: number;
  total: number;
  items: PublishedGameSummary[];
}

export interface PagedResponse<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export interface ScoresheetQueuePage extends PagedResponse<ScoresheetQueueItem> {
  division_names: string[];
}

export type ScoresheetQueueScope = "ALL" | "ACTION_REQUIRED" | "IN_PROGRESS" | "PUBLISHED";

export interface ScoresheetQueueQuery {
  seasonId?: string;
  gameId?: string;
  scope?: ScoresheetQueueScope;
  processing?: "" | "UPLOAD" | "SCORESHEET_REVIEW" | "COMPLETE";
  divisionName?: string;
  query?: string;
  page?: number;
  pageSize?: number;
}

export interface InboxSummary {
  open_count: number;
  display_count: string;
}

export interface InboxTask {
  id: string;
  kind: string;
  title: string;
  body: string;
  status: "OPEN" | "CLOSED";
  due_at: string | null;
  read_at: string | null;
  closed_at: string | null;
  close_reason: string;
  target_url: string;
  created_at: string;
  updated_at: string;
}

export interface InboxPage {
  items: InboxTask[];
  next_cursor: string | null;
}

export interface ScheduleDraftColumn {
  id: string;
  period_id: string;
  period_code: string;
  period_name: string;
  start_time: string;
  venue_name: string;
  final_only: boolean;
  sort_order: number;
}

export interface ScheduleDraftCell {
  id: string;
  column_id: string;
  date: string;
  matchup: string;
  leader_adjustable: boolean;
}

export interface ScheduleDraftMatchup {
  key: string;
  matchup: string;
  division_code: string;
  division_name: string;
  gender: "MEN" | "WOMEN";
  stage: string;
  stage_name: string;
  scheduled: boolean;
  already_formal: boolean;
}

export interface ScheduleDraft {
  id: string;
  season_id: string;
  season_version: number;
  version: number;
  template_version: string;
  source_name: string;
  updated_at: string;
  columns: ScheduleDraftColumn[];
  cells: ScheduleDraftCell[];
  dates: { date: string; weekday: string }[];
  periods: { id: string; code: string; name: string; start_time: string }[];
  matchup_pool: ScheduleDraftMatchup[];
  summary: {
    expected_game_count: number;
    draft_game_count: number;
    locked_game_count: number;
    column_count: number;
    calendar_day_count: number;
  };
}

export interface UpdateScheduleDraft {
  expected_version: number;
  columns: Array<{
    id?: string;
    period_id: string;
    venue_name: string;
    final_only: boolean;
  }>;
  cells: Array<{
    column_id: string;
    date: string;
    matchup: string;
    leader_adjustable: boolean;
  }>;
}

export interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  headers?: Record<string, string>;
  body?: string;
}

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly code?: string,
  ) {
    super(message);
  }
}

export interface RequestAdapter {
  <T>(url: string, options?: RequestOptions): Promise<{ status: number; data: T }>;
}

export function createIdempotencyKey(): string {
  if (typeof globalThis.crypto?.randomUUID === "function") {
    return globalThis.crypto.randomUUID();
  }
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}-${Math.random().toString(36).slice(2)}`;
}

async function browserRequest<T>(
  url: string,
  options: RequestOptions = {},
): Promise<{ status: number; data: T }> {
  const response = await fetch(url, {
    method: options.method,
    headers: { Accept: "application/json", ...options.headers },
    body: options.body,
  });
  const data = response.status === 204 ? (undefined as T) : ((await response.json()) as T);
  return { status: response.status, data };
}

export function createPkubaClient(baseUrl = "", request: RequestAdapter = browserRequest) {
  const send = async <T>(path: string, options: RequestOptions = {}): Promise<T> => {
    const response = await request<T>(`${baseUrl}${path}`, options);
    if (response.status < 200 || response.status >= 300) {
      const error = response.data as { message?: string; code?: string };
      throw new ApiError(error.message ?? "请求失败", response.status, error.code);
    }
    return response.data;
  };
  const json = (
    method: "POST" | "PUT",
    payload: object,
    token?: string,
    idempotencyKey?: string,
  ): RequestOptions => ({
    method,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(idempotencyKey ? { "Idempotency-Key": idempotencyKey } : {}),
    },
    body: JSON.stringify(payload),
  });
  const bearer = (token: string): RequestOptions => ({
    headers: { Authorization: `Bearer ${token}` },
  });
  const collectPages = async <T>(
    path: string,
    query = "",
    options: RequestOptions = {},
  ): Promise<T[]> => {
    const params = new URLSearchParams(query.replace(/^\?/, ""));
    const pageSize = 100;
    const items: T[] = [];
    for (let page = 1; page <= 1000; page += 1) {
      params.set("page", String(page));
      params.set("page_size", String(pageSize));
      const result = await send<PagedResponse<T>>(`${path}?${params.toString()}`, options);
      items.push(...result.items);
      if (items.length >= result.total || result.items.length === 0) return items;
    }
    throw new ApiError("分页数据超过客户端安全上限", 500, "PAGINATION_LIMIT_EXCEEDED");
  };

  return {
    getCurrentSeason: () => send<Season>("/api/v1/public/season"),
    getHomeDashboard: () => send<HomeDashboard>("/api/v1/public/home"),
    getStandings: () => send<Standings>("/api/v1/public/standings"),
    getBrackets: () => send<Brackets>("/api/v1/public/brackets"),
    getGames: (query = "") => collectPages<Game>("/api/v1/public/games", query),
    getGame: (gameId: string) => send<Game>(`/api/v1/public/games/${gameId}`),
    getGameDetail: (gameId: string) =>
      send<PublicGameDetail>(`/api/v1/public/games/${gameId}/detail`),
    getScheduleDays: (query = "") =>
      send<ScheduleDays>(`/api/v1/public/schedule-days${query}`),
    getPublicScoresheetStats: (gameId?: string) =>
      send<PublicScoresheetStat[]>(
        `/api/v1/public/scoresheet-stats${gameId ? `?game_id=${encodeURIComponent(gameId)}` : ""}`,
      ),
    getTeamLeaderboard: (query = "") =>
      send<LeaderboardPage<TeamLeaderboardItem>>(
        `/api/v1/public/leaderboards/teams${query}`,
      ),
    getPlayerLeaderboard: (query = "") =>
      send<LeaderboardPage<PlayerLeaderboardItem>>(
        `/api/v1/public/leaderboards/players${query}`,
      ),
    getPublishedGameSummaries: (query = "") =>
      send<PublishedGamePage>(`/api/v1/public/scoresheet-games${query}`),
    getGameMedia: (gameId: string, token: string) =>
      send<GameMediaCollection>(`/api/v1/game-media/games/${gameId}`, bearer(token)),
    deleteGameMedia: (assetId: string, expectedVersion: number, token: string) =>
      send<void>(`/api/v1/game-media/assets/${assetId}`, {
        method: "DELETE",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ expected_version: expectedVersion }),
      }),
    getScoresheetQueuePage: (token: string, options: ScoresheetQueueQuery = {}) => {
      const params = new URLSearchParams();
      if (options.seasonId) params.set("season_id", options.seasonId);
      if (options.gameId) params.set("game_id", options.gameId);
      if (options.scope) params.set("scope", options.scope);
      if (options.processing) params.set("processing", options.processing);
      if (options.divisionName) params.set("division_name", options.divisionName);
      if (options.query) params.set("query", options.query);
      params.set("page", String(options.page ?? 1));
      params.set("page_size", String(options.pageSize ?? 20));
      return send<ScoresheetQueuePage>(
        `/api/v1/scoresheets/?${params.toString()}`,
        bearer(token),
      );
    },
    getScoresheet: (scoresheetId: string, token: string) =>
      send<ScoresheetDetail>(`/api/v1/scoresheets/${scoresheetId}`, bearer(token)),
    syncScoresheet: (
      scoresheetId: string,
      afterVersion: number,
      afterEvent: number,
      token: string,
    ) =>
      send<ScoresheetSync>(
        `/api/v1/scoresheets/${scoresheetId}/sync?after_version=${afterVersion}&after_event=${afterEvent}`,
        bearer(token),
      ),
    acquireScoresheetLease: (
      scoresheetId: string,
      clientId: string,
      surface: ScoresheetSurface,
      token: string,
      leaseToken = "",
    ) =>
      send<ScoresheetLeaseResponse>(
        `/api/v1/scoresheets/${scoresheetId}/lease`,
        json("POST", { client_id: clientId, surface, lease_token: leaseToken }, token),
      ),
    heartbeatScoresheetLease: (
      scoresheetId: string,
      leaseToken: string,
      clientId: string,
      surface: ScoresheetSurface,
      token: string,
    ) =>
      send<ScoresheetLeaseResponse>(
        `/api/v1/scoresheets/${scoresheetId}/lease/heartbeat`,
        json(
          "POST",
          { lease_token: leaseToken, client_id: clientId, surface },
          token,
        ),
      ),
    releaseScoresheetLease: (
      scoresheetId: string,
      leaseToken: string,
      clientId: string,
      surface: ScoresheetSurface,
      token: string,
    ) =>
      send<void>(
        `/api/v1/scoresheets/${scoresheetId}/lease/release`,
        json(
          "POST",
          { lease_token: leaseToken, client_id: clientId, surface },
          token,
        ),
      ),
    forceScoresheetLease: (
      scoresheetId: string,
      clientId: string,
      surface: ScoresheetSurface,
      token: string,
    ) =>
      send<ScoresheetLeaseResponse>(
        `/api/v1/scoresheets/${scoresheetId}/lease/force`,
        json(
          "POST",
          {
            client_id: clientId,
            surface,
            confirmed: true,
            archived_correction_confirmed: false,
          },
          token,
        ),
      ),
    saveScoresheetDraft: (
      scoresheetId: string,
      context: ScoresheetMutationContext,
      changes: ScoresheetDraftChange[],
      token: string,
      options: { changeType?: string; explicitSave?: boolean } = {},
    ) =>
      send<ScoresheetDetail>(`/api/v1/scoresheets/${scoresheetId}/draft`, {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({
          ...context,
          changes,
          change_type: options.changeType ?? "FIELD_EDIT",
          explicit_save: options.explicitSave ?? false,
        }),
      }),
    reviewScoresheetRegion: (
      scoresheetId: string,
      region: ScoresheetRegion,
      context: ScoresheetMutationContext,
      reviewed: boolean,
      token: string,
    ) =>
      send<ScoresheetDetail>(
        `/api/v1/scoresheets/${scoresheetId}/regions/${region}/review`,
        json("POST", { ...context, reviewed }, token),
      ),
    reviewScoresheetGameContext: (
      scoresheetId: string,
      context: ScoresheetMutationContext,
      reviewToken: string,
      playerMappings: ScoresheetContextPlayerMapping[],
      token: string,
      idempotencyKey: string,
    ) => send<ScoresheetDetail>(`/api/v1/scoresheets/${scoresheetId}/game-context/review`,
      json("POST", { ...context, review_token: reviewToken, confirmed: true,
        player_mappings: playerMappings }, token, idempotencyKey)),
    validateScoresheet: (
      scoresheetId: string,
      context: ScoresheetMutationContext,
      token: string,
    ) =>
      send<ScoresheetDetail>(
        `/api/v1/scoresheets/${scoresheetId}/validate`,
        json("POST", context, token),
      ),
    acknowledgeScoresheetWarnings: (
      scoresheetId: string,
      context: ScoresheetMutationContext,
      warningIds: string[],
      token: string,
    ) =>
      send<ScoresheetDetail>(
        `/api/v1/scoresheets/${scoresheetId}/warnings/acknowledge`,
        json("POST", { ...context, warning_ids: warningIds }, token),
      ),
    publishScoresheet: (
      scoresheetId: string,
      context: ScoresheetMutationContext,
      token: string,
      idempotencyKey = createIdempotencyKey(),
    ) =>
      send<ScoresheetDetail>(
        `/api/v1/scoresheets/${scoresheetId}/publish`,
        json("POST", context, token, idempotencyKey),
      ),
    retryScoresheetRecognition: (
      scoresheetId: string,
      context: ScoresheetMutationContext & { confirmed_overwrite: boolean },
      token: string,
      idempotencyKey = createIdempotencyKey(),
    ) =>
      send<Record<string, unknown>>(
        `/api/v1/scoresheets/${scoresheetId}/recognition/retry`,
        json("POST", context, token, idempotencyKey),
      ),
    getScoresheetRecognitionCapabilities: (token: string) =>
      send<ScoresheetRecognitionCapability>(
        "/api/v1/scoresheets/recognition/capabilities",
        bearer(token),
      ),
    exchangeWeChat: (code: string) =>
      send<WeChatExchange>("/api/v1/auth/wechat/exchange", json("POST", { code })),
    completeProfile: (profileTicket: string, username: string) =>
      send<components["schemas"]["CompleteProfileOut"]>(
        "/api/v1/auth/wechat/complete-profile",
        json("POST", { profile_ticket: profileTicket, username }),
      ),
    getMiniAppMe: (token: string) =>
      send<MiniAppMe>("/api/v1/auth/me", bearer(token)),
    getInboxSummary: (token: string) =>
      send<InboxSummary>("/api/v1/inbox/summary", bearer(token)),
    listInbox: (
      token: string,
      status: "OPEN" | "CLOSED" = "OPEN",
      cursor = "",
      pageSize = 30,
    ) => {
      const params = new URLSearchParams({ status, page_size: String(pageSize) });
      if (cursor) params.set("cursor", cursor);
      return send<InboxPage>(`/api/v1/inbox/?${params.toString()}`, bearer(token));
    },
    viewInboxTask: (taskId: string, token: string) =>
      send<InboxTask>(
        `/api/v1/inbox/${encodeURIComponent(taskId)}/viewed`,
        { method: "POST", ...bearer(token) },
      ),
    logoutMiniApp: (token: string) =>
      send<void>("/api/v1/auth/logout", { method: "POST", ...bearer(token) }),
    getClaimableTeams: (seasonId: string, token: string) =>
      send<ClaimableTeam[]>(
        `/api/v1/auth/leader/claimable-teams?season_id=${encodeURIComponent(seasonId)}`,
        bearer(token),
      ),
    claimLeaderTeam: (
      payload: { season_id: string; team_id: string; expected_team_version: number },
      token: string,
    ) => send<MiniAppMe>("/api/v1/auth/leader/claims", json("POST", payload, token)),
    registerAdmin: (
      payload: { invite_code: string; password: string },
      token: string,
    ) => send<MiniAppMe>("/api/v1/auth/admin/register", json("POST", payload, token)),
    confirmAdminWebLogin: (challengeToken: string, token: string) =>
      send<AdminWebLoginConfirmation>(
        "/api/v1/auth/admin/web-login/confirm",
        json("POST", { challenge_token: challengeToken }, token),
      ),
    listRescheduleRequests: (token: string, activeOnly = false) =>
      collectPages<RescheduleRequest>(
        "/api/v1/reschedule-requests/",
        activeOnly ? "active_only=true" : "",
        bearer(token),
      ),
    getEligibleRescheduleGames: (token: string) =>
      send<RescheduleGame[]>("/api/v1/reschedule-requests/eligible-games", bearer(token)),
    getRescheduleTargets: (gameId: string, processRoute: string, token: string) =>
      send<RescheduleTarget[]>(
        `/api/v1/reschedule-requests/games/${gameId}/targets?process_route=${encodeURIComponent(processRoute)}`,
        bearer(token),
      ),
    createRescheduleRequest: (
      payload: components["schemas"]["CreateRescheduleIn"],
      token: string,
      idempotencyKey = createIdempotencyKey(),
    ) => send<RescheduleRequest>(
      "/api/v1/reschedule-requests/",
      json("POST", payload, token, idempotencyKey),
    ),
    respondToRescheduleOpponent: (
      requestId: string,
      payload: components["schemas"]["VersionedResponseIn"],
      token: string,
    ) => send<RescheduleRequest>(
      `/api/v1/reschedule-requests/${requestId}/opponent-response`,
      json("POST", payload, token),
    ),
    respondAsSelectedTeam: (
      requestId: string,
      payload: components["schemas"]["VersionedResponseIn"],
      token: string,
    ) => send<RescheduleRequest>(
      `/api/v1/reschedule-requests/${requestId}/selected-team-response`,
      json("POST", payload, token),
    ),
    withdrawReschedule: (requestId: string, expectedVersion: number, token: string) =>
      send<RescheduleRequest>(
        `/api/v1/reschedule-requests/${requestId}/withdraw`,
        json("POST", { expected_version: expectedVersion }, token),
      ),
    getRescheduleVoterCandidates: (requestId: string, token: string) =>
      send<RescheduleVoterTeam[]>(
        `/api/v1/reschedule-requests/${requestId}/voter-candidates`,
        bearer(token),
      ),
    decideRescheduleAsAdmin: (
      requestId: string,
      payload: components["schemas"]["AdminDecisionIn"],
      token: string,
    ) => send<RescheduleRequest>(
      `/api/v1/reschedule-requests/${requestId}/admin-decision`,
      json("POST", payload, token),
    ),
    decideRescheduleFinal: (
      requestId: string,
      payload: components["schemas"]["VersionedResponseIn"],
      token: string,
    ) => send<RescheduleRequest>(
      `/api/v1/reschedule-requests/${requestId}/admin-final`,
      json("POST", payload, token),
    ),
    cancelRescheduleAsAdmin: (requestId: string, expectedVersion: number, token: string) =>
      send<RescheduleRequest>(
        `/api/v1/reschedule-requests/${requestId}/admin-cancel`,
        json("POST", { expected_version: expectedVersion }, token),
      ),
    getMobileScheduleOptions: (token: string) =>
      send<MobileScheduleOptions>("/api/v1/admin/mobile/schedule-options", bearer(token)),
    getMobileAdminDashboard: (token: string) =>
      send<MobileAdminDashboard>("/api/v1/admin/mobile/dashboard", bearer(token)),
    getMobileAdminGame: (gameId: string, token: string) =>
      send<MobileAdminGame>(`/api/v1/admin/mobile/games/${gameId}`, bearer(token)),
    updateMobileAdminGame: (
      gameId: string,
      payload: UpdateMobileAdminGame,
      token: string,
    ) => send<MobileAdminGame>(
      `/api/v1/admin/mobile/games/${gameId}`,
      json("PUT", payload, token),
    ),
  };
}

function csrfToken(): string {
  if (typeof document === "undefined") return "";
  const prefix = "pkuba_csrftoken=";
  const cookie = document.cookie
    .split(";")
    .map((part) => part.trim())
    .find((part) => part.startsWith(prefix));
  return cookie ? decodeURIComponent(cookie.slice(prefix.length)) : "";
}

function errorRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function validationLocation(value: unknown): string {
  if (!Array.isArray(value)) return "";
  const labels: Record<string, string> = {
    date: "日期",
    date_capacity_overrides: "特殊日期容量",
    capacity: "容量",
    period_id: "时段",
    starts_on: "开始日期",
    ends_on: "结束日期",
    start_time: "开赛时间",
    end_time: "结束时间",
    target_date: "目标日期",
    target_period_id: "目标时段",
    name: "名称",
    active: "启用状态",
    page: "页码",
    page_size: "每页数量",
    players: "球员",
    jersey_number: "号码",
  };
  return value
    .flatMap((part) => {
      if (typeof part === "number" && Number.isSafeInteger(part) && part >= 0) {
        return [`第 ${part + 1} 项`];
      }
      return typeof part === "string" && Object.prototype.hasOwnProperty.call(labels, part)
        ? [labels[part]] : [];
    })
    .join(" · ");
}

function validationMessage(field: Record<string, unknown>): string {
  // Localize known schema errors, not arbitrary engine messages or input/ctx.
  const messages: Record<string, string> = {
    missing: "此项必填",
    date_parsing: "请填写有效日期",
    date_type: "请填写有效日期",
    date_from_datetime_parsing: "请填写有效日期",
    date_from_datetime_inexact: "请填写不含时间的日期",
    datetime_parsing: "请填写有效日期和时间",
    datetime_type: "请填写有效日期和时间",
    datetime_from_date_parsing: "请填写有效日期和时间",
    time_parsing: "请填写有效时间",
    time_type: "请填写有效时间",
    int_parsing: "请填写整数",
    int_type: "请填写整数",
    int_from_float: "请填写整数",
    float_parsing: "请填写有效数字",
    float_type: "请填写有效数字",
    finite_number: "请填写有限数字",
    greater_than: "数值必须大于允许的下限",
    greater_than_equal: "数值低于允许的最小值",
    less_than: "数值必须小于允许的上限",
    less_than_equal: "数值超过允许的最大值",
    string_type: "请填写文本",
    string_too_short: "填写内容过短",
    string_too_long: "填写内容过长",
    string_pattern_mismatch: "填写格式不正确",
    bool_parsing: "请选择有效的启用或关闭状态",
    bool_type: "请选择有效的启用或关闭状态",
    list_type: "请填写有效列表",
    uuid_parsing: "所选项目无效，请重新选择",
    uuid_type: "所选项目无效，请重新选择",
    enum: "请选择有效选项",
    literal_error: "请选择有效选项",
  };
  if (typeof field.type === "string" && Object.prototype.hasOwnProperty.call(messages, field.type)) {
    return messages[field.type];
  }
  // Keep the existing custom Chinese field-message contract. Unknown typed
  // validators and internal paths must never be echoed as user instructions.
  if (field.type === undefined && typeof field.msg === "string"
    && /[\u3400-\u9fff]/.test(field.msg) && !/[<>\r\n]/.test(field.msg)) {
    return field.msg;
  }
  return "填写内容无效，请检查后重试";
}

function adminErrorMessage(value: unknown, fallback: string): string {
  const error = errorRecord(value);
  if (!error) return fallback;
  if (typeof error.message === "string" && error.message.trim()) return error.message;
  if (typeof error.detail === "string" && error.detail.trim()) return error.detail;
  if (!Array.isArray(error.detail)) return fallback;
  const messages = error.detail.flatMap((item) => {
    const field = errorRecord(item);
    if (!field || typeof field.msg !== "string" || !field.msg.trim()) return [];
    const location = validationLocation(field.loc);
    const message = validationMessage(field);
    // Only documented validation text is shown. Never stringify the response,
    // its input values, context, tracebacks, or arbitrary nested objects.
    return [location ? `${location}：${message}` : message];
  });
  return messages.length ? messages.join("；") : fallback;
}

async function parseAdminResponse<T>(response: Response): Promise<T> {
  if (response.ok) {
    if (response.status === 204) return undefined as T;
    return (await response.json()) as T;
  }
  const fallback = `请求失败（${response.status}）`;
  const error: unknown = await response.json().catch(() => null);
  const code = errorRecord(error)?.code;
  throw new ApiError(
    adminErrorMessage(error, fallback),
    response.status,
    typeof code === "string" ? code : undefined,
  );
}

export function createAdminClient(baseUrl = "", onUnauthorized?: () => void) {
  const fetchAdmin = async (path: string, init: RequestInit = {}) => {
    const response = await fetch(`${baseUrl}${path}`, { credentials: "include", ...init });
    if (response.status === 401) onUnauthorized?.();
    return response;
  };
  const csrfHeaders = () => ({ "X-CSRFToken": csrfToken() });
  const collectAdminPages = async <T>(
    path: string,
    initialParams = new URLSearchParams(),
  ): Promise<T[]> => {
    const params = new URLSearchParams(initialParams.toString());
    const items: T[] = [];
    for (let page = 1; page <= 1000; page += 1) {
      params.set("page", String(page));
      params.set("page_size", "100");
      const result = await parseAdminResponse<PagedResponse<T>>(
        await fetchAdmin(`${path}?${params.toString()}`),
      );
      items.push(...result.items);
      if (items.length >= result.total || result.items.length === 0) return items;
    }
    throw new ApiError("分页数据超过客户端安全上限", 500, "PAGINATION_LIMIT_EXCEEDED");
  };

  return {
    createWebLoginChallenge: async () =>
      parseAdminResponse<AdminWebLoginChallenge>(
        await fetchAdmin("/api/v1/auth/admin/web-login/challenge", { method: "POST" }),
      ),
    getWebLoginStatus: async () =>
      parseAdminResponse<AdminWebLoginStatus>(
        await fetchAdmin("/api/v1/auth/admin/web-login/status"),
      ),
    consumeWebLogin: async (browserToken: string) =>
      parseAdminResponse<AdminAccount>(
        await fetchAdmin("/api/v1/auth/admin/web-login/consume", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ browser_token: browserToken }),
        }),
      ),
    getLoginChallenge: async () =>
      parseAdminResponse<LoginChallenge>(
        await fetchAdmin("/api/v1/auth/admin/login-challenge"),
      ),
    passwordLogin: async (username: string, password: string, challenge: string) =>
      parseAdminResponse<AdminAccount>(
        await fetchAdmin("/api/v1/auth/admin/password-login", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ username, password, challenge }),
        }),
      ),
    getSession: async () =>
      parseAdminResponse<AdminSession>(await fetchAdmin("/api/v1/auth/admin/session")),
    getMe: async () =>
      parseAdminResponse<AdminAccount>(await fetchAdmin("/api/v1/auth/admin/me")),
    changePassword: async (currentPassword: string, newPassword: string) =>
      parseAdminResponse<AdminAccount>(
        await fetchAdmin("/api/v1/auth/admin/change-password", {
          method: "POST",
          headers: { "Content-Type": "application/json", ...csrfHeaders() },
          body: JSON.stringify({
            current_password: currentPassword,
            new_password: newPassword,
          }),
        }),
      ),
    logout: async () =>
      parseAdminResponse<void>(
        await fetchAdmin("/api/v1/auth/admin/logout", {
          method: "POST",
          headers: csrfHeaders(),
        }),
      ),
    downloadScheduleTemplate: async (seasonId: string) => {
      const response = await fetchAdmin(
        `/api/v1/admin/seasons/${seasonId}/schedule-template`,
        {
          headers: {
            Accept: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
          },
        },
      );
      if (!response.ok) await parseAdminResponse<never>(response);
      const blob = await response.blob();
      if (blob.size === 0) {
        throw new ApiError("服务器返回了空模板，请重试。", response.status);
      }
      return blob;
    },
    getScheduleDraft: async (seasonId: string) =>
      parseAdminResponse<ScheduleDraft>(
        await fetchAdmin(`/api/v1/admin/seasons/${seasonId}/schedule-draft`),
      ),
    updateScheduleDraft: async (seasonId: string, payload: UpdateScheduleDraft) =>
      parseAdminResponse<ScheduleDraft>(
        await fetchAdmin(`/api/v1/admin/seasons/${seasonId}/schedule-draft`, {
          method: "PUT",
          headers: { "Content-Type": "application/json", ...csrfHeaders() },
          body: JSON.stringify(payload),
        }),
      ),
    importScheduleDraft: async (seasonId: string, expectedVersion: number, file: File) => {
      const form = new FormData();
      form.append("schedule_file", file);
      return parseAdminResponse<ScheduleDraft>(
        await fetchAdmin(
          `/api/v1/admin/seasons/${seasonId}/schedule-draft/import-xlsx?expected_version=${expectedVersion}`,
          {
            method: "POST",
            headers: csrfHeaders(),
            body: form,
          },
        ),
      );
    },
    exportScheduleDraft: async (seasonId: string) => {
      const response = await fetchAdmin(
        `/api/v1/admin/seasons/${seasonId}/schedule-draft/export-xlsx`,
        {
          headers: {
            Accept: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
          },
        },
      );
      if (!response.ok) await parseAdminResponse<never>(response);
      const blob = await response.blob();
      if (blob.size === 0) throw new ApiError("服务器返回了空草稿。", response.status);
      return blob;
    },
    validateScheduleDraft: async (seasonId: string, expectedVersion: number) =>
      parseAdminResponse<ScheduleImport>(
        await fetchAdmin(
          `/api/v1/admin/seasons/${seasonId}/schedule-draft/validate`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json", ...csrfHeaders() },
            body: JSON.stringify({ expected_version: expectedVersion }),
          },
        ),
      ),
    getScheduleImportReadiness: async (seasonId: string) =>
      parseAdminResponse<ScheduleImportReadiness>(
        await fetchAdmin(
          `/api/v1/admin/seasons/${seasonId}/schedule-import-readiness`,
        ),
      ),
    uploadSchedule: async (seasonId: string, file: File) => {
      const form = new FormData();
      form.append("schedule_file", file);
      return parseAdminResponse<ScheduleImport>(
        await fetchAdmin(`/api/v1/admin/seasons/${seasonId}/schedule-imports`, {
          method: "POST",
          headers: csrfHeaders(),
          body: form,
        }),
      );
    },
    getScheduleImport: async (batchId: string) =>
      parseAdminResponse<ScheduleImport>(
        await fetchAdmin(`/api/v1/admin/schedule-imports/${batchId}`),
      ),
    confirmScheduleImport: async (
      batchId: string,
      payload: ConfirmScheduleImport,
      idempotencyKey = createIdempotencyKey(),
    ) =>
      parseAdminResponse<ScheduleImport>(
        await fetchAdmin(`/api/v1/admin/schedule-imports/${batchId}/confirm`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "Idempotency-Key": idempotencyKey,
            ...csrfHeaders(),
          },
          body: JSON.stringify(payload),
        }),
      ),
    getScheduleImportResetPreview: async (seasonId: string) =>
      parseAdminResponse<ScheduleImportResetPreview>(
        await fetchAdmin(
          `/api/v1/admin/seasons/${seasonId}/schedule-import-reset`,
        ),
      ),
    resetScheduleImports: async (seasonId: string, payload: ScheduleImportReset) =>
      parseAdminResponse<ScheduleImportResetResult>(
        await fetchAdmin(
          `/api/v1/admin/seasons/${seasonId}/schedule-import-reset`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json", ...csrfHeaders() },
            body: JSON.stringify(payload),
          },
        ),
      ),
    getRosterDataset: async (seasonId: string) =>
      parseAdminResponse<RosterDataset>(
        await fetchAdmin(`/api/v1/admin/roster/seasons/${seasonId}/roster`),
      ),
    getRosterImportReadiness: async (seasonId: string) =>
      parseAdminResponse<RosterImportReadiness>(
        await fetchAdmin(
          `/api/v1/admin/roster/seasons/${seasonId}/roster-import-readiness`,
        ),
      ),
    downloadRosterTemplate: async (seasonId: string) => {
      const response = await fetchAdmin(
        `/api/v1/admin/roster/seasons/${seasonId}/roster-template`,
        {
          headers: {
            Accept: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
          },
        },
      );
      if (!response.ok) await parseAdminResponse<never>(response);
      const blob = await response.blob();
      if (blob.size === 0) {
        throw new ApiError("服务器返回了空模板，请重试。", response.status);
      }
      return blob;
    },
    uploadRoster: async (seasonId: string, file: File) => {
      const form = new FormData();
      form.append("roster_file", file);
      return parseAdminResponse<RosterImport>(
        await fetchAdmin(`/api/v1/admin/roster/seasons/${seasonId}/roster-imports`, {
          method: "POST",
          headers: csrfHeaders(),
          body: form,
        }),
      );
    },
    getRosterImport: async (batchId: string) =>
      parseAdminResponse<RosterImport>(
        await fetchAdmin(`/api/v1/admin/roster/roster-imports/${batchId}`),
      ),
    resolveRosterNames: async (batchId: string, resolutions: Record<string, string>) =>
      parseAdminResponse<RosterImport>(
        await fetchAdmin(
          `/api/v1/admin/roster/roster-imports/${batchId}/resolutions`,
          {
            method: "PUT",
            headers: { "Content-Type": "application/json", ...csrfHeaders() },
            body: JSON.stringify({ resolutions }),
          },
        ),
      ),
    confirmRosterImport: async (
      batchId: string,
      payload: components["schemas"]["ConfirmRosterImportIn"],
    ) =>
      parseAdminResponse<RosterImport>(
        await fetchAdmin(`/api/v1/admin/roster/roster-imports/${batchId}/confirm`, {
          method: "POST",
          headers: { "Content-Type": "application/json", ...csrfHeaders() },
          body: JSON.stringify(payload),
        }),
      ),
    createRosterTeam: async (seasonId: string, payload: CreateTeamRoster) =>
      parseAdminResponse<TeamRoster>(
        await fetchAdmin(`/api/v1/admin/roster/seasons/${seasonId}/teams`, {
          method: "POST",
          headers: { "Content-Type": "application/json", ...csrfHeaders() },
          body: JSON.stringify(payload),
        }),
      ),
    previewTeamRoster: async (teamId: string, payload: SaveTeamRoster) =>
      parseAdminResponse<TeamMaintenancePreview>(
        await fetchAdmin(`/api/v1/admin/roster/teams/${teamId}/roster-preview`, {
          method: "POST",
          headers: { "Content-Type": "application/json", ...csrfHeaders() },
          body: JSON.stringify(payload),
        }),
      ),
    saveTeamRoster: async (teamId: string, payload: SaveTeamRoster) =>
      parseAdminResponse<TeamRoster>(
        await fetchAdmin(`/api/v1/admin/roster/teams/${teamId}/roster`, {
          method: "PUT",
          headers: { "Content-Type": "application/json", ...csrfHeaders() },
          body: JSON.stringify(payload),
        }),
      ),
    getDrawAssignments: async (seasonId: string) =>
      parseAdminResponse<DrawAssignmentDataset>(
        await fetchAdmin(`/api/v1/admin/seasons/${seasonId}/draw-assignments`),
      ),
    previewDrawAssignments: async (
      seasonId: string,
      payload: PreviewDrawAssignments,
    ) =>
      parseAdminResponse<DrawAssignmentPreview>(
        await fetchAdmin(
          `/api/v1/admin/seasons/${seasonId}/draw-assignments/preview`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json", ...csrfHeaders() },
            body: JSON.stringify(payload),
          },
        ),
      ),
    updateDrawAssignments: async (
      seasonId: string,
      payload: ApplyDrawAssignments,
      idempotencyKey = createIdempotencyKey(),
    ) =>
      parseAdminResponse<DrawAssignmentDataset>(
        await fetchAdmin(`/api/v1/admin/seasons/${seasonId}/draw-assignments`, {
          method: "PUT",
          headers: {
            "Content-Type": "application/json",
            "Idempotency-Key": idempotencyKey,
            ...csrfHeaders(),
          },
          body: JSON.stringify(payload),
        }),
      ),
    previewGameDrawAssignments: async (
      seasonId: string,
      gameId: string,
      payload: PreviewGameDrawAssignments,
    ) =>
      parseAdminResponse<DrawGameAssignmentPreview>(
        await fetchAdmin(
          `/api/v1/admin/seasons/${seasonId}/draw-assignments/games/${gameId}/preview`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json", ...csrfHeaders() },
            body: JSON.stringify(payload),
          },
        ),
      ),
    updateGameDrawAssignments: async (
      seasonId: string,
      gameId: string,
      payload: ApplyGameDrawAssignments,
      idempotencyKey = createIdempotencyKey(),
    ) =>
      parseAdminResponse<DrawAssignmentDataset>(
        await fetchAdmin(
          `/api/v1/admin/seasons/${seasonId}/draw-assignments/games/${gameId}`,
          {
            method: "PUT",
            headers: {
              "Content-Type": "application/json",
              "Idempotency-Key": idempotencyKey,
              ...csrfHeaders(),
            },
            body: JSON.stringify(payload),
          },
        ),
      ),
    getArchiveStorageSummary: async () =>
      parseAdminResponse<StorageSummary>(
        await fetchAdmin("/api/v1/admin/archives/storage-summary"),
      ),
    previewSeasonExport: async (
      seasonId: string,
      kind: Extract<ArchiveKind, "SEASON_DATA" | "SEASON_PHOTOS">,
      expectedSeasonVersion: number,
    ) =>
      parseAdminResponse<ArchivePreview>(
        await fetchAdmin(`/api/v1/admin/seasons/${seasonId}/exports/preview`, {
          method: "POST",
          headers: { "Content-Type": "application/json", ...csrfHeaders() },
          body: JSON.stringify({ kind, expected_season_version: expectedSeasonVersion }),
        }),
      ),
    createSeasonExport: async (
      seasonId: string,
      kind: Extract<ArchiveKind, "SEASON_DATA" | "SEASON_PHOTOS">,
      expectedSeasonVersion: number,
      idempotencyKey = createIdempotencyKey(),
    ) =>
      parseAdminResponse<ArchiveJob>(
        await fetchAdmin(`/api/v1/admin/seasons/${seasonId}/exports`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "Idempotency-Key": idempotencyKey,
            ...csrfHeaders(),
          },
          body: JSON.stringify({ kind, expected_season_version: expectedSeasonVersion }),
        }),
      ),
    listSeasonExports: async (seasonId: string) =>
      parseAdminResponse<PagedResponse<ArchiveJob>>(
        await fetchAdmin(`/api/v1/admin/seasons/${seasonId}/exports?page=1&page_size=100`),
      ),
    previewSystemBackup: async () =>
      parseAdminResponse<ArchivePreview>(
        await fetchAdmin("/api/v1/admin/system-backups/preview", {
          method: "POST",
          headers: csrfHeaders(),
        }),
      ),
    createSystemBackup: async (
      currentPassword: string,
      idempotencyKey = createIdempotencyKey(),
    ) =>
      parseAdminResponse<ArchiveJob>(
        await fetchAdmin("/api/v1/admin/system-backups", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "Idempotency-Key": idempotencyKey,
            ...csrfHeaders(),
          },
          body: JSON.stringify({ current_password: currentPassword }),
        }),
      ),
    listSystemBackups: async () =>
      parseAdminResponse<PagedResponse<ArchiveJob>>(
        await fetchAdmin("/api/v1/admin/system-backups?page=1&page_size=100"),
      ),
    issueArchiveDownloadTicket: async (jobId: string) =>
      parseAdminResponse<ArchiveDownloadTicket>(
        await fetchAdmin(`/api/v1/admin/archive-jobs/${jobId}/download-ticket`, {
          method: "POST",
          headers: csrfHeaders(),
        }),
      ),
    confirmArchiveSaved: async (
      jobId: string,
      expectedVersion: number,
      idempotencyKey = createIdempotencyKey(),
    ) =>
      parseAdminResponse<ArchiveJob>(
        await fetchAdmin(`/api/v1/admin/archive-jobs/${jobId}/confirm-saved`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "Idempotency-Key": idempotencyKey,
            ...csrfHeaders(),
          },
          body: JSON.stringify({
            expected_version: expectedVersion,
            confirmed_external_copy: true,
          }),
        }),
      ),
    discardArchive: async (
      jobId: string,
      expectedVersion: number,
      idempotencyKey = createIdempotencyKey(),
    ) =>
      parseAdminResponse<ArchiveJob>(
        await fetchAdmin(`/api/v1/admin/archive-jobs/${jobId}/discard`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "Idempotency-Key": idempotencyKey,
            ...csrfHeaders(),
          },
          body: JSON.stringify({
            expected_version: expectedVersion,
            confirmed_external_copy: false,
          }),
        }),
      ),
    previewMediaPurge: async (seasonId: string) =>
      parseAdminResponse<MediaPurgePreview>(
        await fetchAdmin(`/api/v1/admin/seasons/${seasonId}/media-purge/preview`, {
          method: "POST",
          headers: csrfHeaders(),
        }),
      ),
    listMediaPurgeJobs: async (seasonId: string) =>
      parseAdminResponse<PagedResponse<MediaPurgeJob>>(
        await fetchAdmin(`/api/v1/admin/seasons/${seasonId}/media-purge?page=1&page_size=100`),
      ),
    applyMediaPurge: async (
      seasonId: string,
      payload: {
        preview_hash: string;
        expected_season_version: number;
        confirmed_external_copy: boolean;
        confirm_permanent_delete: boolean;
      },
      idempotencyKey = createIdempotencyKey(),
    ) =>
      parseAdminResponse<MediaPurgeJob>(
        await fetchAdmin(`/api/v1/admin/seasons/${seasonId}/media-purge/apply`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "Idempotency-Key": idempotencyKey,
            ...csrfHeaders(),
          },
          body: JSON.stringify(payload),
        }),
      ),
    retryMediaPurge: async (
      jobId: string,
      expectedVersion: number,
      idempotencyKey = createIdempotencyKey(),
    ) =>
      parseAdminResponse<MediaPurgeJob>(
        await fetchAdmin(`/api/v1/admin/media-purge-jobs/${jobId}/retry`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "Idempotency-Key": idempotencyKey,
            ...csrfHeaders(),
          },
          body: JSON.stringify({ expected_version: expectedVersion }),
        }),
      ),
    listAdminSeasons: async () =>
      parseAdminResponse<AdminSeason[]>(await fetchAdmin("/api/v1/admin/seasons")),
    createAdminSeason: async (payload: CreateSeason) =>
      parseAdminResponse<SeasonConfiguration>(
        await fetchAdmin("/api/v1/admin/seasons", {
          method: "POST",
          headers: { "Content-Type": "application/json", ...csrfHeaders() },
          body: JSON.stringify(payload),
        }),
      ),
    getSeasonConfiguration: async (seasonId: string) =>
      parseAdminResponse<SeasonConfiguration>(
        await fetchAdmin(`/api/v1/admin/seasons/${seasonId}/configuration`),
      ),
    previewSeasonConfiguration: async (
      seasonId: string,
      payload: PreviewSeasonConfiguration,
    ) =>
      parseAdminResponse<SeasonConfigurationPreview>(
        await fetchAdmin(
          `/api/v1/admin/seasons/${seasonId}/configuration/preview`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json", ...csrfHeaders() },
            body: JSON.stringify(payload),
          },
        ),
      ),
    getCapacityLedger: async (
      seasonId: string,
      startsOn?: string,
      endsOn?: string,
    ) => {
      const query = new URLSearchParams();
      if (startsOn) query.set("starts_on", startsOn);
      if (endsOn) query.set("ends_on", endsOn);
      const suffix = query.size ? `?${query.toString()}` : "";
      return parseAdminResponse<CapacityLedgerRow[]>(
        await fetchAdmin(
          `/api/v1/admin/seasons/${seasonId}/capacity-ledger${suffix}`,
        ),
      );
    },
    updateSeasonConfiguration: async (
      seasonId: string,
      payload: UpdateSeasonConfiguration,
    ) =>
      parseAdminResponse<SeasonConfiguration>(
        await fetchAdmin(`/api/v1/admin/seasons/${seasonId}/configuration`, {
          method: "PUT",
          headers: { "Content-Type": "application/json", ...csrfHeaders() },
          body: JSON.stringify(payload),
        }),
      ),
    previewSeasonLifecycle: async (seasonId: string, payload: LifecycleCommand) =>
      parseAdminResponse<LifecyclePreview>(
        await fetchAdmin(`/api/v1/admin/seasons/${seasonId}/lifecycle/preview`, {
          method: "POST",
          headers: { "Content-Type": "application/json", ...csrfHeaders() },
          body: JSON.stringify(payload),
        }),
      ),
    applySeasonLifecycle: async (
      seasonId: string,
      payload: LifecycleApply,
      idempotencyKey = createIdempotencyKey(),
    ) =>
      parseAdminResponse<LifecyclePreview>(
        await fetchAdmin(`/api/v1/admin/seasons/${seasonId}/lifecycle/apply`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "Idempotency-Key": idempotencyKey,
            ...csrfHeaders(),
          },
          body: JSON.stringify(payload),
        }),
      ),
    listAdvancedModels: async () =>
      parseAdminResponse<AdvancedModel[]>(
        await fetchAdmin("/api/v1/admin/advanced-data/models"),
      ),
    listAdvancedRecords: async (
      modelKey: string,
      offset = 0,
      limit = 50,
      options: { search?: string; sort?: string; direction?: "asc" | "desc" } = {},
    ) => {
      const query = new URLSearchParams({
        offset: String(offset),
        limit: String(limit),
      });
      if (options.search?.trim()) query.set("search", options.search.trim());
      if (options.sort) query.set("sort", options.sort);
      if (options.direction) query.set("direction", options.direction);
      return (
      parseAdminResponse<AdvancedRecordList>(
        await fetchAdmin(
          `/api/v1/admin/advanced-data/${modelKey}?${query.toString()}`,
        ),
      ));
    },
    getAdvancedRecord: async (modelKey: string, objectId: string) =>
      parseAdminResponse<AdvancedRecord>(
        await fetchAdmin(`/api/v1/admin/advanced-data/${modelKey}/${objectId}`),
      ),
    previewAdvancedMutation: async (modelKey: string, payload: AdvancedMutation) =>
      parseAdminResponse<AdvancedMutationPreview>(
        await fetchAdmin(`/api/v1/admin/advanced-data/${modelKey}/mutations/preview`, {
          method: "POST",
          headers: { "Content-Type": "application/json", ...csrfHeaders() },
          body: JSON.stringify(payload),
        }),
      ),
    applyAdvancedMutation: async (
      modelKey: string,
      payload: AdvancedMutationApply,
    ) =>
      parseAdminResponse<AdvancedRecord>(
        await fetchAdmin(`/api/v1/admin/advanced-data/${modelKey}/mutations/apply`, {
          method: "POST",
          headers: { "Content-Type": "application/json", ...csrfHeaders() },
          body: JSON.stringify(payload),
        }),
      ),
    listAdminAccounts: async () =>
      parseAdminResponse<AdminManagedAccount[]>(
        await fetchAdmin("/api/v1/admin/accounts"),
      ),
    promoteAdmin: async (accountId: string, expectedVersion: number) =>
      parseAdminResponse<AdminManagedAccount>(
        await fetchAdmin(`/api/v1/admin/accounts/${accountId}/promote`, {
          method: "POST",
          headers: { "Content-Type": "application/json", ...csrfHeaders() },
          body: JSON.stringify({ expected_version: expectedVersion }),
        }),
      ),
    demoteSuperadmin: async (accountId: string, expectedVersion: number) =>
      parseAdminResponse<AdminManagedAccount>(
        await fetchAdmin(`/api/v1/admin/accounts/${accountId}/demote`, {
          method: "POST",
          headers: { "Content-Type": "application/json", ...csrfHeaders() },
          body: JSON.stringify({ expected_version: expectedVersion }),
        }),
      ),
    setAdminActive: async (
      accountId: string,
      expectedVersion: number,
      active: boolean,
    ) =>
      parseAdminResponse<AdminManagedAccount>(
        await fetchAdmin(`/api/v1/admin/accounts/${accountId}/active`, {
          method: "POST",
          headers: { "Content-Type": "application/json", ...csrfHeaders() },
          body: JSON.stringify({ expected_version: expectedVersion, active }),
        }),
      ),
    getAdminRegistrationPolicy: async () =>
      parseAdminResponse<AdminRegistrationPolicy>(
        await fetchAdmin("/api/v1/admin/admin-registration-policy"),
      ),
    setAdminRegistrationPolicy: async (inviteCode: string, expectedVersion: number) =>
      parseAdminResponse<AdminRegistrationPolicy>(
        await fetchAdmin("/api/v1/admin/admin-registration-policy", {
          method: "PUT",
          headers: { "Content-Type": "application/json", ...csrfHeaders() },
          body: JSON.stringify({ invite_code: inviteCode, expected_version: expectedVersion }),
        }),
      ),
    getAdminScheduleOptions: async (seasonId?: string) =>
      parseAdminResponse<MobileScheduleOptions>(
        await fetchAdmin(
          `/api/v1/admin/schedule/options${seasonId ? `?season_id=${encodeURIComponent(seasonId)}` : ""}`,
        ),
      ),
    listAdminScheduleGames: async (seasonId: string) =>
      parseAdminResponse<MobileAdminGame[]>(
        await fetchAdmin(
          `/api/v1/admin/schedule/games?season_id=${encodeURIComponent(seasonId)}`,
        ),
      ),
    getAdminScheduleGame: async (gameId: string) =>
      parseAdminResponse<MobileAdminGame>(
        await fetchAdmin(`/api/v1/admin/schedule/games/${gameId}`),
      ),
    updateAdminScheduleGame: async (
      gameId: string,
      payload: UpdateMobileAdminGame,
    ) =>
      parseAdminResponse<MobileAdminGame>(
        await fetchAdmin(`/api/v1/admin/schedule/games/${gameId}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json", ...csrfHeaders() },
          body: JSON.stringify(payload),
        }),
      ),
    listAdminRescheduleRequests: async (filters: {
      view?: "active" | "history" | "all";
      status?: string;
      requestType?: string;
      processRoute?: string;
      divisionId?: string;
      query?: string;
      page?: number;
      pageSize?: number;
    } = {}) => {
      const params = new URLSearchParams();
      if (filters.view) params.set("view", filters.view);
      if (filters.status) params.set("status", filters.status);
      if (filters.requestType) params.set("request_type", filters.requestType);
      if (filters.processRoute) params.set("process_route", filters.processRoute);
      if (filters.divisionId) params.set("division_id", filters.divisionId);
      if (filters.query) params.set("q", filters.query);
      if (filters.page) params.set("page", String(filters.page));
      if (filters.pageSize) params.set("page_size", String(filters.pageSize));
      const query = params.toString();
      return parseAdminResponse<AdminReschedulePage>(
        await fetchAdmin(
          `/api/v1/admin/reschedule-requests${query ? `?${query}` : ""}`,
        ),
      );
    },
    getAdminRescheduleVoterCandidates: async (requestId: string) =>
      parseAdminResponse<RescheduleVoterTeam[]>(
        await fetchAdmin(
          `/api/v1/admin/reschedule-requests/${requestId}/voter-candidates`,
        ),
      ),
    actOnAdminReschedule: async (
      requestId: string,
      payload: AdminRescheduleAction,
    ) =>
      parseAdminResponse<AdminRescheduleRequest>(
        await fetchAdmin(
          `/api/v1/admin/reschedule-requests/${requestId}/actions`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json", ...csrfHeaders() },
            body: JSON.stringify(payload),
          },
        ),
      ),
    listAdminGameMedia: async (filters: AdminGameMediaFilters = {}) => {
      const params = new URLSearchParams();
      if (filters.kind) params.set("kind", filters.kind);
      if (filters.seasonId) params.set("season_id", filters.seasonId);
      if (filters.gameId) params.set("game_id", filters.gameId);
      return collectAdminPages<GameMediaAsset>("/api/v1/admin/game-media/", params);
    },
    uploadAdminGameMedia: async (
      gameId: string,
      kind: "SCORESHEET" | "GROUP_PHOTO" | "GAME_PHOTO",
      scoresheetCompleteConfirmed: boolean,
      file: File,
      idempotencyKey = createIdempotencyKey(),
    ) => {
      const form = new FormData();
      form.append("kind", kind);
      form.append(
        "scoresheet_complete_confirmed",
        scoresheetCompleteConfirmed ? "true" : "false",
      );
      form.append("image", file);
      return parseAdminResponse<GameMediaAsset>(
        await fetchAdmin(`/api/v1/admin/game-media/games/${gameId}`, {
          method: "POST",
          headers: { "Idempotency-Key": idempotencyKey, ...csrfHeaders() },
          body: form,
        }),
      );
    },
    replaceAdminGameMedia: async (
      assetId: string,
      expectedVersion: number,
      scoresheetCompleteConfirmed: boolean,
      file: File,
      idempotencyKey = createIdempotencyKey(),
    ) => {
      const form = new FormData();
      form.append("expected_version", String(expectedVersion));
      form.append(
        "scoresheet_complete_confirmed",
        scoresheetCompleteConfirmed ? "true" : "false",
      );
      form.append("image", file);
      return parseAdminResponse<GameMediaAsset>(
        await fetchAdmin(`/api/v1/admin/game-media/${assetId}/replace`, {
          method: "POST",
          headers: { "Idempotency-Key": idempotencyKey, ...csrfHeaders() },
          body: form,
        }),
      );
    },
    deleteAdminGameMedia: async (assetId: string, expectedVersion: number) =>
      parseAdminResponse<void>(
        await fetchAdmin(`/api/v1/admin/game-media/${assetId}`, {
          method: "DELETE",
          headers: { "Content-Type": "application/json", ...csrfHeaders() },
          body: JSON.stringify({ expected_version: expectedVersion }),
        }),
      ),
    getScoresheetQueuePage: async (options: ScoresheetQueueQuery = {}) => {
      const params = new URLSearchParams();
      if (options.seasonId) params.set("season_id", options.seasonId);
      if (options.gameId) params.set("game_id", options.gameId);
      if (options.scope) params.set("scope", options.scope);
      if (options.processing) params.set("processing", options.processing);
      if (options.divisionName) params.set("division_name", options.divisionName);
      if (options.query) params.set("query", options.query);
      params.set("page", String(options.page ?? 1));
      params.set("page_size", String(options.pageSize ?? 20));
      return parseAdminResponse<ScoresheetQueuePage>(
        await fetchAdmin(`/api/v1/scoresheets/?${params.toString()}`),
      );
    },
    getScoresheet: async (scoresheetId: string) =>
      parseAdminResponse<ScoresheetDetail>(
        await fetchAdmin(`/api/v1/scoresheets/${scoresheetId}`),
      ),
    syncScoresheet: async (
      scoresheetId: string,
      afterVersion: number,
      afterEvent: number,
    ) =>
      parseAdminResponse<ScoresheetSync>(
        await fetchAdmin(
          `/api/v1/scoresheets/${scoresheetId}/sync?after_version=${afterVersion}&after_event=${afterEvent}`,
        ),
      ),
    acquireScoresheetLease: async (
      scoresheetId: string,
      clientId: string,
      surface: ScoresheetSurface,
      leaseToken = "",
      archivedCorrectionConfirmed = false,
    ) =>
      parseAdminResponse<ScoresheetLeaseResponse>(
        await fetchAdmin(`/api/v1/scoresheets/${scoresheetId}/lease`, {
          method: "POST",
          headers: { "Content-Type": "application/json", ...csrfHeaders() },
          body: JSON.stringify({
            client_id: clientId,
            surface,
            lease_token: leaseToken,
            archived_correction_confirmed: archivedCorrectionConfirmed,
          }),
        }),
      ),
    heartbeatScoresheetLease: async (
      scoresheetId: string,
      leaseToken: string,
      clientId: string,
      surface: ScoresheetSurface,
    ) =>
      parseAdminResponse<ScoresheetLeaseResponse>(
        await fetchAdmin(`/api/v1/scoresheets/${scoresheetId}/lease/heartbeat`, {
          method: "POST",
          headers: { "Content-Type": "application/json", ...csrfHeaders() },
          body: JSON.stringify({
            lease_token: leaseToken,
            client_id: clientId,
            surface,
          }),
        }),
      ),
    releaseScoresheetLease: async (
      scoresheetId: string,
      leaseToken: string,
      clientId: string,
      surface: ScoresheetSurface,
    ) =>
      parseAdminResponse<void>(
        await fetchAdmin(`/api/v1/scoresheets/${scoresheetId}/lease/release`, {
          method: "POST",
          headers: { "Content-Type": "application/json", ...csrfHeaders() },
          body: JSON.stringify({
            lease_token: leaseToken,
            client_id: clientId,
            surface,
          }),
        }),
      ),
    forceScoresheetLease: async (
      scoresheetId: string,
      clientId: string,
      surface: ScoresheetSurface,
      archivedCorrectionConfirmed = false,
    ) =>
      parseAdminResponse<ScoresheetLeaseResponse>(
        await fetchAdmin(`/api/v1/scoresheets/${scoresheetId}/lease/force`, {
          method: "POST",
          headers: { "Content-Type": "application/json", ...csrfHeaders() },
          body: JSON.stringify({
            client_id: clientId,
            surface,
            confirmed: true,
            archived_correction_confirmed: archivedCorrectionConfirmed,
          }),
        }),
      ),
    saveScoresheetDraft: async (
      scoresheetId: string,
      context: ScoresheetMutationContext,
      changes: ScoresheetDraftChange[],
      options: { changeType?: string; explicitSave?: boolean } = {},
    ) =>
      parseAdminResponse<ScoresheetDetail>(
        await fetchAdmin(`/api/v1/scoresheets/${scoresheetId}/draft`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json", ...csrfHeaders() },
          body: JSON.stringify({
            ...context,
            changes,
            change_type: options.changeType ?? "FIELD_EDIT",
            explicit_save: options.explicitSave ?? false,
          }),
        }),
      ),
    reviewScoresheetRegion: async (
      scoresheetId: string,
      region: ScoresheetRegion,
      context: ScoresheetMutationContext,
      reviewed: boolean,
    ) =>
      parseAdminResponse<ScoresheetDetail>(
        await fetchAdmin(`/api/v1/scoresheets/${scoresheetId}/regions/${region}/review`, {
          method: "POST",
          headers: { "Content-Type": "application/json", ...csrfHeaders() },
          body: JSON.stringify({ ...context, reviewed }),
        }),
      ),
    reviewScoresheetGameContext: async (
      scoresheetId: string,
      context: ScoresheetMutationContext,
      reviewToken: string,
      playerMappings: ScoresheetContextPlayerMapping[],
      idempotencyKey: string,
    ) => parseAdminResponse<ScoresheetDetail>(
      await fetchAdmin(`/api/v1/scoresheets/${scoresheetId}/game-context/review`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "Idempotency-Key": idempotencyKey,
          ...csrfHeaders() },
        body: JSON.stringify({ ...context, review_token: reviewToken,
          confirmed: true, player_mappings: playerMappings }),
      }),
    ),
    validateScoresheet: async (
      scoresheetId: string,
      context: ScoresheetMutationContext,
    ) =>
      parseAdminResponse<ScoresheetDetail>(
        await fetchAdmin(`/api/v1/scoresheets/${scoresheetId}/validate`, {
          method: "POST",
          headers: { "Content-Type": "application/json", ...csrfHeaders() },
          body: JSON.stringify(context),
        }),
      ),
    acknowledgeScoresheetWarnings: async (
      scoresheetId: string,
      context: ScoresheetMutationContext,
      warningIds: string[],
    ) =>
      parseAdminResponse<ScoresheetDetail>(
        await fetchAdmin(`/api/v1/scoresheets/${scoresheetId}/warnings/acknowledge`, {
          method: "POST",
          headers: { "Content-Type": "application/json", ...csrfHeaders() },
          body: JSON.stringify({ ...context, warning_ids: warningIds }),
        }),
      ),
    publishScoresheet: async (
      scoresheetId: string,
      context: ScoresheetMutationContext,
      idempotencyKey = createIdempotencyKey(),
    ) =>
      parseAdminResponse<ScoresheetDetail>(
        await fetchAdmin(`/api/v1/scoresheets/${scoresheetId}/publish`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "Idempotency-Key": idempotencyKey,
            ...csrfHeaders(),
          },
          body: JSON.stringify(context),
        }),
      ),
    getScoresheetRecognitionCapabilities: async () =>
      parseAdminResponse<ScoresheetRecognitionCapability>(
        await fetchAdmin("/api/v1/scoresheets/recognition/capabilities"),
      ),
    downloadScoresheetPdf: async (scoresheetId: string) => {
      const response = await fetchAdmin(`/api/v1/scoresheets/${scoresheetId}/exports/pdf`);
      if (!response.ok) await parseAdminResponse<never>(response);
      return response.blob();
    },
    downloadScoresheetCsv: async (scoresheetId: string) => {
      const response = await fetchAdmin(`/api/v1/scoresheets/${scoresheetId}/exports/csv`);
      if (!response.ok) await parseAdminResponse<never>(response);
      return response.blob();
    },
    downloadSeasonScoresheetStats: async (seasonId: string) => {
      const response = await fetchAdmin(
        `/api/v1/scoresheets/exports/seasons/${seasonId}/xlsx`,
      );
      if (!response.ok) await parseAdminResponse<never>(response);
      return response.blob();
    },
  };
}
