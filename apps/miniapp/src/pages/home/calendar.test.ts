import { describe, expect, it } from "vitest";

import { buildSeasonCalendar, densityLevel } from "./calendar";

describe("home season calendar", () => {
  it("always renders the current natural week plus two weeks on each side", () => {
    const cells = buildSeasonCalendar("2026-04-01", [
      { date: "2026-03-28", game_count: 3 },
      { date: "2026-04-02", game_count: 1 },
    ]);

    expect(cells).toHaveLength(35);
    expect(cells[0].date).toBe("2026-03-16");
    expect(cells[34].date).toBe("2026-04-19");
    expect(cells.every((cell) => !cell.outside)).toBe(true);
    expect(cells.find((cell) => cell.date === "2026-03-31")?.gameCount).toBe(0);
    expect(cells.find((cell) => cell.date === "2026-03-28")?.gameCount).toBe(3);
  });

  it("keeps exactly 35 cells across the year boundary", () => {
    const cells = buildSeasonCalendar("2027-01-01", []);
    expect(cells).toHaveLength(35);
    expect(cells[0].date).toBe("2026-12-14");
    expect(cells[34].date).toBe("2027-01-17");
    expect(cells.filter((cell) => cell.date === "2027-01-01")).toHaveLength(1);
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
