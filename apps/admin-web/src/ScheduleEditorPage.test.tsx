import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

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

  it("keeps the newer game selected when an older request resolves late", async () => {
    let resolveFirst!: (game: MobileAdminGame) => void;
    const firstRequest = new Promise<MobileAdminGame>((resolve) => {
      resolveFirst = resolve;
    });
    const secondGame: MobileAdminGame = {
      ...baseGame,
      id: "game-women",
      code: "女甲·丙队vs丁队",
      division_id: "division-women",
      division_name: "女甲",
      division_gender: "WOMEN",
      home_name: "丙队",
      away_name: "丁队",
    };
    const client = {
      getAdminScheduleOptions: vi.fn().mockResolvedValue({
        periods: [], venues: [], teams: [],
      }),
      getAdminScheduleGame: vi.fn((id: string) => (
        id === baseGame.id ? firstRequest : Promise.resolve(secondGame)
      )),
    };

    render(
      <ScheduleEditorPage
        client={client as never}
        games={[baseGame, secondGame]}
        seasons={[season]}
        season={season}
        onSeasonChange={vi.fn()}
        onUpdated={vi.fn().mockResolvedValue(undefined)}
      />,
    );

    await userEvent.click(screen.getByRole("button", { name: /男甲.*甲队.*乙队/ }));
    await userEvent.click(screen.getByRole("button", { name: /女甲.*丙队.*丁队/ }));
    expect(await screen.findByRole("heading", { name: /丙队.*丁队/ })).toBeInTheDocument();

    resolveFirst(baseGame);
    await firstRequest;
    await waitFor(() => expect(screen.getByRole("heading", { name: /丙队.*丁队/ })).toBeInTheDocument());
    expect(screen.queryByRole("heading", { name: /甲队.*乙队/ })).not.toBeInTheDocument();
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

  it("routes archived schedule edits through the correction center", async () => {
    const archived = { ...season, status: "ARCHIVED" as const };
    const onOpenCorrection = vi.fn();
    const client = {
      getAdminScheduleOptions: vi.fn().mockResolvedValue({
        periods: [{ id: "period-1", code: "P1", name: "第一时段", start_time: "12:50:00" }],
        venues: [{ id: "venue-1", name: "五四东一" }],
        teams: [],
      }),
      getAdminScheduleGame: vi.fn().mockResolvedValue(baseGame),
      updateAdminScheduleGame: vi.fn(),
    };

    render(
      <ScheduleEditorPage
        client={client as never}
        games={[baseGame]}
        seasons={[archived]}
        season={archived}
        onSeasonChange={vi.fn()}
        onUpdated={vi.fn().mockResolvedValue(undefined)}
        onOpenCorrection={onOpenCorrection}
      />,
    );

    await userEvent.click(screen.getByRole("button", { name: /男甲.*甲队.*乙队/ }));
    expect(await screen.findByText(/归档赛季保持归档/)).toBeVisible();
    expect(screen.getByLabelText("比赛日期")).toBeEnabled();
    expect(screen.getByLabelText(/允许领队申请调赛/)).toBeEnabled();
    const submit = screen.getByRole("button", { name: "在纠错中心预览影响" });
    expect(submit).toBeDisabled();
    await userEvent.clear(screen.getByLabelText("比赛日期"));
    await userEvent.type(screen.getByLabelText("比赛日期"), "2026-03-22");
    expect(submit).toBeEnabled();
    await userEvent.click(submit);
    expect(client.updateAdminScheduleGame).not.toHaveBeenCalled();
    expect(onOpenCorrection).toHaveBeenCalledWith(
      expect.objectContaining({ id: baseGame.id, date: "2026-03-22" }),
    );
  });
});
