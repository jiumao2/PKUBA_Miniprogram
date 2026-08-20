import type { components } from "./generated/schema";

export type Division = components["schemas"]["DivisionOut"];
export type Season = components["schemas"]["SeasonOut"];
export type Game = components["schemas"]["GameOut"];
export type HomeDashboard = components["schemas"]["HomeDashboardOut"];
export type Standings = components["schemas"]["StandingsOut"];
export type DivisionStandings = components["schemas"]["DivisionStandingsOut"];
export type GroupStandings = components["schemas"]["GroupStandingsOut"];
export type StandingsEntry = components["schemas"]["StandingsEntryOut"];
export type StandingsMatch = components["schemas"]["StandingsMatchOut"];
export type AdminAccount = components["schemas"]["AccountOut"];
export type AdminSession = components["schemas"]["AdminSessionOut"];
export type LoginChallenge = components["schemas"]["LoginChallengeOut"];
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
export type WeChatExchange = components["schemas"]["WeChatExchangeOut"];
export type MiniAppMe = components["schemas"]["MiniAppMeOut"];
export type ClaimableTeam = components["schemas"]["ClaimableTeamOut"];
export type SeasonInvite = components["schemas"]["SeasonInviteOut"];
export type Brackets = components["schemas"]["BracketsOut"];
export type DivisionBracket = components["schemas"]["DivisionBracketOut"];
export type BracketRound = components["schemas"]["BracketRoundOut"];
export type BracketGame = components["schemas"]["BracketGameOut"];
export type RescheduleRequest = components["schemas"]["RescheduleRequestOut"];
export type RescheduleGame = components["schemas"]["RescheduleGameOut"];
export type RescheduleTarget = components["schemas"]["RescheduleTargetOut"];
export type RescheduleVoterTeam = components["schemas"]["RescheduleVoterTeamOut"];
export type MobileAdminGame = components["schemas"]["AdminGameOut"];
export type MobileScheduleOptions = components["schemas"]["ScheduleOptionsOut"];
export type UpdateMobileAdminGame = components["schemas"]["UpdateAdminGameIn"];
export type GameMediaAsset = components["schemas"]["GameMediaAssetOut"];
export type GameMediaCollection = components["schemas"]["GameMediaCollectionOut"];

export interface RequestOptions {
  method?: "GET" | "POST" | "PUT";
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
  const json = (method: "POST" | "PUT", payload: object, token?: string): RequestOptions => ({
    method,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify(payload),
  });
  const bearer = (token: string): RequestOptions => ({
    headers: { Authorization: `Bearer ${token}` },
  });

  return {
    getCurrentSeason: () => send<Season>("/api/v1/public/season"),
    getHomeDashboard: () => send<HomeDashboard>("/api/v1/public/home"),
    getStandings: () => send<Standings>("/api/v1/public/standings"),
    getBrackets: () => send<Brackets>("/api/v1/public/brackets"),
    getGames: (query = "") => send<Game[]>(`/api/v1/public/games${query}`),
    getGame: (gameId: string) => send<Game>(`/api/v1/public/games/${gameId}`),
    getGameMedia: (gameId: string, token: string) =>
      send<GameMediaCollection>(`/api/v1/game-media/games/${gameId}`, bearer(token)),
    exchangeWeChat: (code: string) =>
      send<WeChatExchange>("/api/v1/auth/wechat/exchange", json("POST", { code })),
    completeProfile: (profileTicket: string, username: string) =>
      send<components["schemas"]["CompleteProfileOut"]>(
        "/api/v1/auth/wechat/complete-profile",
        json("POST", { profile_ticket: profileTicket, username }),
      ),
    getMiniAppMe: (token: string) =>
      send<MiniAppMe>("/api/v1/auth/me", bearer(token)),
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
      payload: { season_id: string; invite_code: string },
      token: string,
    ) => send<MiniAppMe>("/api/v1/auth/admin/register", json("POST", payload, token)),
    listRescheduleRequests: (token: string, activeOnly = false) =>
      send<RescheduleRequest[]>(
        `/api/v1/reschedule-requests/${activeOnly ? "?active_only=true" : ""}`,
        bearer(token),
      ),
    getEligibleRescheduleGames: (token: string) =>
      send<RescheduleGame[]>("/api/v1/reschedule-requests/eligible-games", bearer(token)),
    getRescheduleTargets: (gameId: string, token: string) =>
      send<RescheduleTarget[]>(
        `/api/v1/reschedule-requests/games/${gameId}/targets`,
        bearer(token),
      ),
    createRescheduleRequest: (
      payload: components["schemas"]["CreateRescheduleIn"],
      token: string,
    ) => send<RescheduleRequest>("/api/v1/reschedule-requests/", json("POST", payload, token)),
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

async function parseAdminResponse<T>(response: Response): Promise<T> {
  if (response.ok) {
    if (response.status === 204) return undefined as T;
    return (await response.json()) as T;
  }
  const fallback = { message: `请求失败（${response.status}）` };
  const error = (await response.json().catch(() => fallback)) as {
    message?: string;
    detail?: string;
    code?: string;
  };
  throw new ApiError(
    error.message ?? error.detail ?? fallback.message,
    response.status,
    error.code,
  );
}

export function createAdminClient(baseUrl = "") {
  const fetchAdmin = (path: string, init: RequestInit = {}) =>
    fetch(`${baseUrl}${path}`, { credentials: "include", ...init });
  const csrfHeaders = () => ({ "X-CSRFToken": csrfToken() });

  return {
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
      );
      if (!response.ok) await parseAdminResponse<never>(response);
      return response.blob();
    },
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
    confirmScheduleImport: async (batchId: string, payload: ConfirmScheduleImport) =>
      parseAdminResponse<ScheduleImport>(
        await fetchAdmin(`/api/v1/admin/schedule-imports/${batchId}/confirm`, {
          method: "POST",
          headers: { "Content-Type": "application/json", ...csrfHeaders() },
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
    getSeasonInvite: async (seasonId: string) =>
      parseAdminResponse<SeasonInvite>(
        await fetchAdmin(`/api/v1/admin/seasons/${seasonId}/admin-invite-code`),
      ),
    setSeasonInvite: async (seasonId: string, inviteCode: string, expectedVersion: number) =>
      parseAdminResponse<SeasonInvite>(
        await fetchAdmin(`/api/v1/admin/seasons/${seasonId}/admin-invite-code`, {
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
    listAdminGameMedia: async (reviewStatus = "", kind = "") => {
      const params = new URLSearchParams();
      if (reviewStatus) params.set("review_status", reviewStatus);
      if (kind) params.set("kind", kind);
      const query = params.toString();
      return parseAdminResponse<GameMediaAsset[]>(
        await fetchAdmin(`/api/v1/admin/game-media/${query ? `?${query}` : ""}`),
      );
    },
    reviewAdminGameMedia: async (
      assetId: string,
      payload: { expected_version: number; approve: boolean; note: string },
    ) =>
      parseAdminResponse<GameMediaAsset>(
        await fetchAdmin(`/api/v1/admin/game-media/${assetId}/review`, {
          method: "POST",
          headers: { "Content-Type": "application/json", ...csrfHeaders() },
          body: JSON.stringify(payload),
        }),
      ),
    replaceAdminGameMedia: async (
      assetId: string,
      expectedVersion: number,
      scoresheetCompleteConfirmed: boolean,
      file: File,
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
          headers: csrfHeaders(),
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
  };
}
