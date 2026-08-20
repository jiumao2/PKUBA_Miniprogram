import { describe, expect, it } from "vitest";

import type { Game } from "@pkuba/api-client";
import { groupGamesByDate, selectRecentGameDays } from "./domain";

const baseGame: Game = {
  id: "1",
  code: "A-01",
  division_id: "d1",
  division_name: "男甲",
  division_gender: "MEN",
  group_name: "A 组",
  stage: "GROUP",
  round_number: 1,
  date: "2026-10-11",
  period_code: "P2",
  period_name: "第二时段",
  start_time: "10:00",
  venue_id: "v1",
  venue_name: "一号场",
  home_team_id: "t1",
  away_team_id: "t2",
  home_name: "甲队",
  away_name: "乙队",
  home_score: null,
  away_score: null,
  participants_resolved: true,
  leader_adjustable: true,
  status: "SCHEDULED",
  version: 1,
};

describe("groupGamesByDate", () => {
  it("sorts days and games without using array indexes as identity", () => {
    const grouped = groupGamesByDate([
      baseGame,
      { ...baseGame, id: "2", date: "2026-10-10", start_time: "14:00" },
      { ...baseGame, id: "3", date: "2026-10-11", start_time: "08:00" },
    ]);
    expect(grouped.map((day) => day.date)).toEqual(["2026-10-10", "2026-10-11"]);
    expect(grouped[1].games.map((game) => game.id)).toEqual(["3", "1"]);
    expect(grouped[1].times.map((group) => group.time)).toEqual(["08:00", "10:00"]);
    expect(grouped[1].times[0].games.map((game) => game.id)).toEqual(["3"]);
  });
});

describe("selectRecentGameDays", () => {
  const grouped = groupGamesByDate([
    { ...baseGame, id: "1", date: "2026-10-10" },
    { ...baseGame, id: "2", date: "2026-10-11" },
    { ...baseGame, id: "3", date: "2026-10-12" },
  ]);

  it("shows the next dates in chronological order while games remain", () => {
    expect(selectRecentGameDays(grouped, "2026-10-11", 2).map((day) => day.date)).toEqual([
      "2026-10-11",
      "2026-10-12",
    ]);
  });

  it("shows the latest dates first after the season schedule has ended", () => {
    expect(selectRecentGameDays(grouped, "2026-10-13", 2).map((day) => day.date)).toEqual([
      "2026-10-12",
      "2026-10-11",
    ]);
  });
});
