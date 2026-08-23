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
  });
});
