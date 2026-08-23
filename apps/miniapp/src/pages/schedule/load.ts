import type { Brackets, Game } from "@pkuba/api-client";

export interface PublicScheduleApi {
  getGames: () => Promise<Game[]>;
  getBrackets: () => Promise<Brackets>;
}

export interface ScheduleLoadState {
  games: Game[];
  message: string;
}

export interface BracketLoadState {
  brackets: Brackets | null;
  message: string;
}

export async function loadScheduleState(client: PublicScheduleApi): Promise<ScheduleLoadState> {
  try {
    const games = await client.getGames();
    return {
      games,
      message: games.length ? "" : "当前赛季尚未排入比赛。",
    };
  } catch (reason: unknown) {
    return { games: [], message: errorMessage(reason, "赛程读取失败") };
  }
}

export async function loadBracketState(client: PublicScheduleApi): Promise<BracketLoadState> {
  try {
    return { brackets: await client.getBrackets(), message: "" };
  } catch (reason: unknown) {
    return { brackets: null, message: errorMessage(reason, "淘汰赛读取失败") };
  }
}

function errorMessage(reason: unknown, fallback: string) {
  return reason instanceof Error && reason.message ? reason.message : fallback;
}
