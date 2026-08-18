import type { components } from "./generated/schema";

export type Division = components["schemas"]["DivisionOut"];
export type Season = components["schemas"]["SeasonOut"];
export type Game = components["schemas"]["GameOut"];
export type AdminAccount = components["schemas"]["AccountOut"];
export type AdminSession = components["schemas"]["AdminSessionOut"];
export type LoginChallenge = components["schemas"]["LoginChallengeOut"];
export type ScheduleImport = components["schemas"]["ScheduleImportOut"];
export type ImportIssue = components["schemas"]["ImportIssueOut"];
export type ConfirmScheduleImport = components["schemas"]["ConfirmScheduleImportIn"];
export type AdminManagedAccount = components["schemas"]["AdminAccountOut"];

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
  <T>(url: string): Promise<{ status: number; data: T }>;
}

async function browserRequest<T>(url: string): Promise<{ status: number; data: T }> {
  const response = await fetch(url, { headers: { Accept: "application/json" } });
  const data = (await response.json()) as T;
  return { status: response.status, data };
}

export function createPkubaClient(baseUrl = "", request: RequestAdapter = browserRequest) {
  const get = async <T>(path: string): Promise<T> => {
    const response = await request<T>(`${baseUrl}${path}`);
    if (response.status < 200 || response.status >= 300) {
      const error = response.data as { message?: string; code?: string };
      throw new ApiError(error.message ?? "请求失败", response.status, error.code);
    }
    return response.data;
  };

  return {
    getCurrentSeason: () => get<Season>("/api/v1/public/season"),
    getGames: (query = "") => get<Game[]>(`/api/v1/public/games${query}`),
    getGame: (gameId: string) => get<Game>(`/api/v1/public/games/${gameId}`),
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
  };
}
