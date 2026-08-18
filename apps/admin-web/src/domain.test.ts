import { describe, expect, it } from "vitest";

import type { Game } from "@pkuba/api-client";
import { groupGamesByDate } from "./domain";

const baseGame: Game = {
  id: "1",
  code: "A-01",
  division_id: "d1",
  division_name: "男甲",
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
  });
});
