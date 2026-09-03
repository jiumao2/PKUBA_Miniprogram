import { describe, expect, it } from "vitest";

import { PLAYER_SORTS, TEAM_SORTS } from "./sorts";

describe("data leaderboard sort controls", () => {
  it("does not expose team game-count sorting", () => {
    expect(TEAM_SORTS.map(([value]) => value)).not.toContain("games_played");
  });

  it("does not expose player appearance or start-count sorting", () => {
    const values = PLAYER_SORTS.map(([value]) => value);
    expect(values).not.toContain("games_played");
    expect(values).not.toContain("starts");
    expect(values).not.toContain("fouls_per_game");
  });

  it("defaults lower-is-stronger team defense to ascending order", () => {
    expect(TEAM_SORTS.find(([value]) => value === "points_against_per_game")?.[2])
      .toBe("asc");
    expect(TEAM_SORTS.filter(([value]) => value !== "points_against_per_game")
      .every(([, , order]) => order === "desc")).toBe(true);
  });
});
