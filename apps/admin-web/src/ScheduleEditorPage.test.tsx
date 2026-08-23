import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { AdminSeason, MobileAdminGame } from "@pkuba/api-client";
import { ScheduleEditorPage, scheduleGameVisualClass } from "./ScheduleEditorPage";

const season: AdminSeason = {
  id: "season-1",
  name: "2026 北大杯",
  competition_type: "PKU_CUP",
  year: 2026,
  status: "SETUP",
  starts_on: "2026-03-21",
  ends_on: "2026-05-10",
  version: 1,
  divisions: [],
};

const baseGame: MobileAdminGame = {
  id: "game-men",
  code: "男甲·甲队vs乙队",
  division_id: "division-men",
  division_name: "男甲",
  division_gender: "MEN",
  date: "2026-03-21",
  period_id: "period-1",
  period_code: "P1",
  period_name: "第一时段",
  nominal_start_time: "12:50",
  start_time: "12:50",
  standard_venue_id: "venue-1",
  venue_name: "五四东一",
  home_team_id: "team-1",
  away_team_id: "team-2",
  home_slot_id: "slot-1",
  away_slot_id: "slot-2",
  participants_managed_by_draw: true,
  home_name: "甲队",
  away_name: "乙队",
  home_score: null,
  away_score: null,
  status: "SCHEDULED",
  stage: "GROUP",
  leader_adjustable: true,
  active_reschedule_request_id: null,
  version: 1,
};

describe("ScheduleEditorPage colors", () => {
  it("marks gender and policy lock as separate visual states", () => {
    expect(scheduleGameVisualClass(baseGame, true)).toBe(
      "schedule-editor-game game-men game-adjustable active",
    );
    expect(scheduleGameVisualClass({
      ...baseGame,
      division_gender: "WOMEN",
      leader_adjustable: false,
    })).toBe("schedule-editor-game game-women game-locked");
  });

  it("renders a visible legend and locked label in addition to color", () => {
    const lockedWomenGame: MobileAdminGame = {
      ...baseGame,
      id: "game-women",
      code: "女甲·丙队vs丁队",
      division_id: "division-women",
      division_name: "女甲",
      division_gender: "WOMEN",
      home_name: "丙队",
      away_name: "丁队",
      leader_adjustable: false,
    };
    type Props = Parameters<typeof ScheduleEditorPage>[0];
    const html = renderToStaticMarkup(
      <ScheduleEditorPage
        client={{} as Props["client"]}
        games={[baseGame, lockedWomenGame]}
        seasons={[season]}
        season={season}
        onSeasonChange={() => undefined}
        onUpdated={async () => undefined}
      />,
    );

    expect(html).toContain("比赛颜色说明");
    expect(html).toContain("game-men game-adjustable");
    expect(html).toContain("game-women game-locked");
    expect(html).toContain("已锁定 · 领队不可调");
  });
});
