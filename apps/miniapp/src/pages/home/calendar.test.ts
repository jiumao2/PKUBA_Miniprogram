import { describe, expect, it } from "vitest";

import {
  buildSeasonCalendar,
  calendarRangeOptions,
  densityLevel,
  localDateKey,
} from "./calendar";

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

  it("builds natural-month grids and disables padding or out-of-season dates", () => {
    const august = buildSeasonCalendar(
      "2026-08-15",
      [{ date: "2026-08-01", game_count: 2 }],
      "month:2026-08",
      "2026-03-21",
      "2026-09-10",
    );
    expect(august).toHaveLength(42);
    expect(august[0]).toMatchObject({ date: "2026-07-27", outside: true });
    expect(august.find((cell) => cell.date === "2026-08-01")).toMatchObject({
      outside: false,
      gameCount: 2,
    });
    expect(august[41]).toMatchObject({ date: "2026-09-06", outside: true });

    const march = buildSeasonCalendar(
      "2026-03-25",
      [],
      "month:2026-03",
      "2026-03-21",
      "2026-05-10",
    );
    expect(march.find((cell) => cell.date === "2026-03-20")?.outside).toBe(true);
    expect(march.find((cell) => cell.date === "2026-03-21")?.outside).toBe(false);
  });

  it.each([
    ["2026-03-01", "2026-02-23", "2026-04-05"],
    ["2026-04-01", "2026-03-23", "2026-04-05"],
    ["2026-05-12", "2026-03-23", "2026-05-17"],
  ])("covers today %s and actual game days, regardless of season bounds", (today, first, last) => {
    const days = [
      { date: "2026-04-02", game_count: 1 },
      { date: "2026-01-01", game_count: 0 },
      { date: "2026-03-28", game_count: 2 },
      { date: "2026-12-31", game_count: 0 },
    ];
    const cells = buildSeasonCalendar(
      today,
      days,
      "all",
      "2026-04-01",
      "2026-04-01",
    );
    expect(cells[0].date).toBe(first);
    expect(cells[cells.length - 1].date).toBe(last);
    expect(cells.length % 7).toBe(0);
    expect(cells.every((cell) => !cell.outside)).toBe(true);
    expect(cells.filter((cell) => cell.date === today)).toHaveLength(1);
    expect(cells.find((cell) => cell.date === "2026-03-28")?.gameCount).toBe(2);
    expect(cells.reduce((total, cell) => total + cell.gameCount, 0)).toBe(3);
    expect(buildSeasonCalendar(today, days, "all", "2025-01-01", "2027-12-31")).toEqual(cells);
  });

  it.each([
    ["2026-04-01", "2026-04-02", "2026-03-30", "2026-04-05"],
    ["2027-01-01", "2026-12-27", "2026-12-21", "2027-01-03"],
  ])("aligns single-game and cross-year ranges for %s", (today, gameDate, first, last) => {
    const cells = buildSeasonCalendar(today, [{ date: gameDate, game_count: 1 }], "all");
    expect(cells[0].date).toBe(first);
    expect(cells[cells.length - 1].date).toBe(last);
    expect(cells.every((cell) => !cell.outside)).toBe(true);
    expect(cells[0].gameCount).toBe(0);
  });

  it("returns no all-range cells when there are no valid games", () => {
    expect(buildSeasonCalendar("2026-04-01", [], "all")).toEqual([]);
    expect(buildSeasonCalendar("2026-04-01", [{ date: "2026-03-28", game_count: 0 }], "all")).toEqual([]);
  });

  it("uses the Beijing date at midnight", () => {
    expect(localDateKey(new Date("2026-03-31T15:59:59Z"))).toBe("2026-03-31");
    expect(localDateKey(new Date("2026-03-31T16:00:00Z"))).toBe("2026-04-01");
  });

  it("offers recent, each season month, and complete-season ranges", () => {
    expect(calendarRangeOptions("2026-03-21", "2026-05-10")).toEqual([
      { value: "recent", label: "近期" },
      { value: "month:2026-03", label: "2026年3月" },
      { value: "month:2026-04", label: "2026年4月" },
      { value: "month:2026-05", label: "2026年5月" },
      { value: "all", label: "全部" },
    ]);
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
