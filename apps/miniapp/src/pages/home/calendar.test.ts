import { describe, expect, it } from "vitest";

import { buildSeasonCalendar, densityLevel } from "./calendar";

describe("home season calendar", () => {
  it("fills every day across month and natural-week boundaries", () => {
    const cells = buildSeasonCalendar("2026-03-28", "2026-04-02", [
      { date: "2026-03-28", game_count: 3 },
      { date: "2026-04-02", game_count: 1 },
    ]);

    expect(cells).toHaveLength(14);
    expect(cells.slice(0, 5).every((cell) => cell.outside)).toBe(true);
    expect(cells.filter((cell) => !cell.outside).map((cell) => cell.date)).toEqual([
      "2026-03-28",
      "2026-03-29",
      "2026-03-30",
      "2026-03-31",
      "2026-04-01",
      "2026-04-02",
    ]);
    expect(cells.find((cell) => cell.date === "2026-03-31")?.gameCount).toBe(0);
  });

  it("renders a one-day Monday season as one complete week", () => {
    const cells = buildSeasonCalendar("2026-03-23", "2026-03-23", [
      { date: "2026-03-23", game_count: 2 },
    ]);
    expect(cells).toHaveLength(7);
    expect(cells[0]).toMatchObject({ outside: false, gameCount: 2 });
    expect(cells.slice(1).every((cell) => cell.outside)).toBe(true);
  });

  it("maps zero and positive counts to five visible levels", () => {
    expect([0, 1, 2, 3, 4].map((value) => densityLevel(value, 4))).toEqual([
      0,
      1,
      2,
      3,
      4,
    ]);
  });
});
