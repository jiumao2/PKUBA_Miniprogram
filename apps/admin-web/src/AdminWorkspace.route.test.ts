import { afterEach, describe, expect, it } from "vitest";

import {
  navigation,
  readInitialAdminRoute,
  selectAdminSeason,
} from "./AdminWorkspace";

afterEach(() => window.history.replaceState(null, "", "/"));

describe("admin workspace media navigation", () => {
  it("has one competition media entry and no standalone recordsheet or bracket entry", () => {
    const labels = navigation.map((item) => item.label);
    expect(labels.filter((label) => label === "比赛资料")).toHaveLength(1);
    expect(labels).not.toContain("记录表核对");
    expect(labels).not.toContain("淘汰赛管理");
  });

  it("restores a valid competition media deep link", () => {
    window.history.replaceState(
      null,
      "",
      "/?page=media&season_id=season-2026&game_id=game-18",
    );
    expect(readInitialAdminRoute()).toEqual({
      page: "media",
      seasonId: "season-2026",
      gameId: "game-18",
    });
  });

  it("falls back to overview for an invalid page", () => {
    window.history.replaceState(null, "", "/?page=obsolete&season_id=missing");
    expect(readInitialAdminRoute()).toEqual({
      page: "overview",
      seasonId: "missing",
      gameId: "",
    });
  });

  it("falls back from an invalid season id to the available setup season", () => {
    const seasons = [
      { id: "published", status: "PUBLISHED" },
      { id: "setup", status: "SETUP" },
    ];
    expect(
      selectAdminSeason(
        seasons as unknown as Parameters<typeof selectAdminSeason>[0],
        "missing",
      )?.id,
    ).toBe("setup");
  });
});
