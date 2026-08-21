import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { Game } from "@pkuba/api-client";
import { groupGamesByDate } from "./domain";
import { ScheduleOverview } from "./ScheduleOverview";

const baseGame: Game = {
  id: "game-1",
  code: "M-A-01",
  division_id: "division-men",
  division_name: "男甲",
  division_gender: "MEN",
  group_name: "A 组",
  stage: "GROUP",
  round_number: 1,
  date: "2026-05-10",
  slot_code: "P4-二体一号场",
  slot_name: "第四时段 · 二体一号场",
  period_code: "P4",
  period_name: "第四时段",
  start_time: "15:50",
  venue_name: "二体一号场",
  home_team_id: "team-1",
  away_team_id: "team-2",
  home_name: "甲队",
  away_name: "乙队",
  home_score: 42,
  away_score: 38,
  participants_resolved: true,
  leader_adjustable: false,
  status: "COMPLETED",
  version: 1,
};

describe("ScheduleOverview", () => {
  it("renders date and time groups with gender, teams, venue, and scores", () => {
    const womenGame: Game = {
      ...baseGame,
      id: "game-2",
      code: "W-A-01",
      division_id: "division-women",
      division_name: "女甲",
      division_gender: "WOMEN",
      venue_name: "二体二号场",
      home_team_id: "team-3",
      away_team_id: "team-4",
      home_name: "丙队",
      away_name: "丁队",
      home_score: 51,
      away_score: 47,
    };
    const html = renderToStaticMarkup(
      <ScheduleOverview
        gameDays={groupGamesByDate([baseGame, womenGame])}
        today="2026-08-19"
      />,
    );

    expect(html).toContain("15:50");
    expect(html).toContain("男甲");
    expect(html).toContain("女甲");
    expect(html).not.toContain("男篮 · 男甲");
    expect(html).not.toContain("女篮 · 女甲");
    expect(html).toContain("甲队");
    expect(html).toContain("乙队");
    expect(html).toContain("42");
    expect(html).toContain("38");
    expect(html).toContain("二体二号场");
  });
});
