import { describe, expect, it, vi } from "vitest";
import type { Brackets, Game } from "@pkuba/api-client";

import { loadBracketState, loadScheduleState, type PublicScheduleApi } from "./load";

const brackets: Brackets = {
  season_id: "10000000-0000-0000-0000-000000000001",
  season_name: "测试赛季",
  divisions: [],
};

describe("schedule page resource loading", () => {
  it("keeps the schedule result when bracket loading fails", async () => {
    const game = { id: "20000000-0000-0000-0000-000000000001" } as Game;
    const client = {
      getGames: vi.fn().mockResolvedValue([game]),
      getBrackets: vi.fn().mockRejectedValue(new Error("淘汰赛服务暂不可用")),
    } as PublicScheduleApi;

    const [schedule, bracket] = await Promise.all([
      loadScheduleState(client),
      loadBracketState(client),
    ]);

    expect(schedule).toEqual({ games: [game], message: "" });
    expect(bracket).toEqual({ brackets: null, message: "淘汰赛服务暂不可用" });
  });

  it("keeps the bracket result when schedule loading fails", async () => {
    const client = {
      getGames: vi.fn().mockRejectedValue(new Error("赛程服务暂不可用")),
      getBrackets: vi.fn().mockResolvedValue(brackets),
    } as PublicScheduleApi;

    const [schedule, bracket] = await Promise.all([
      loadScheduleState(client),
      loadBracketState(client),
    ]);

    expect(schedule).toEqual({ games: [], message: "赛程服务暂不可用" });
    expect(bracket).toEqual({ brackets, message: "" });
  });
});
