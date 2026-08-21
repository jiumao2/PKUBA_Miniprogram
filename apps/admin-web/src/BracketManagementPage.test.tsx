import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AdminSeason, BracketManagement, createAdminClient } from "@pkuba/api-client";

import { BracketManagementPage } from "./BracketManagementPage";

type AdminClient = ReturnType<typeof createAdminClient>;

const season: AdminSeason = {
  id: "10000000-0000-0000-0000-000000000001",
  name: "2026 北大杯",
  competition_type: "PKU_CUP",
  year: 2026,
  status: "ACTIVE",
  starts_on: "2026-03-01",
  ends_on: "2026-05-31",
  version: 3,
  divisions: [
    {
      id: "20000000-0000-0000-0000-000000000001",
      code: "men-a",
      name: "男甲",
      gender: "MEN",
      operation_status: "ACTIVE",
      version: 2,
    },
  ],
};

const data: BracketManagement = {
  season_id: season.id,
  season_name: season.name,
  season_status: "ACTIVE",
  season_version: 3,
  division_id: season.divisions[0].id,
  division_name: "男甲",
  division_status: "ACTIVE",
  division_version: 2,
  relation_mode: "LEGACY_DERIVED",
  read_only: false,
  locked_reason: "",
  games: [
    {
      id: "30000000-0000-0000-0000-000000000001",
      code: "SF-1",
      stage: "SEMIFINAL",
      round_number: 1,
      date: "2026-05-01",
      start_time: "12:50",
      home_name: "球队一",
      away_name: "球队二",
      home_team_id: "40000000-0000-0000-0000-000000000001",
      away_team_id: "40000000-0000-0000-0000-000000000002",
      home_score: 70,
      away_score: 60,
      status: "COMPLETED",
      version: 1,
    },
    {
      id: "30000000-0000-0000-0000-000000000002",
      code: "FINAL",
      stage: "FINAL",
      round_number: 2,
      date: "2026-05-08",
      start_time: "20:30",
      home_name: "半决赛胜者",
      away_name: "待定",
      home_team_id: null,
      away_team_id: null,
      home_score: null,
      away_score: null,
      status: "SCHEDULED",
      version: 1,
    },
  ],
  feeds: [],
  legacy_suggestions: [
    {
      source_game_id: "30000000-0000-0000-0000-000000000001",
      target_game_id: "30000000-0000-0000-0000-000000000002",
      target_side: "HOME",
    },
  ],
};

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("BracketManagementPage", () => {
  it("shows legacy relations and confirms them through preview", async () => {
    const getBracketManagement = vi.fn().mockResolvedValue(data);
    const previewBracketRelations = vi.fn().mockResolvedValue({
      season_id: season.id,
      season_version: 3,
      division_id: season.divisions[0].id,
      division_version: 2,
      relation_mode_before: "LEGACY_DERIVED",
      relation_mode_after: "AUTHORITATIVE",
      added_count: 1,
      removed_count: 0,
      unchanged_count: 0,
      blockers: [],
      can_apply: true,
      impact_hash: "bracket-hash",
      relations: data.legacy_suggestions,
    });
    const applyBracketRelations = vi.fn().mockResolvedValue({
      ...data,
      relation_mode: "AUTHORITATIVE",
      feeds: [
        {
          id: "50000000-0000-0000-0000-000000000001",
          ...data.legacy_suggestions[0],
          applied_winner_id: null,
          applied_winner_name: null,
          applied_source_version: null,
          version: 1,
        },
      ],
    });
    const client = {
      getBracketManagement,
      previewBracketRelations,
      applyBracketRelations,
    } as unknown as AdminClient;
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const user = userEvent.setup();

    render(
      <BracketManagementPage
        client={client}
        seasons={[season]}
        seasonId={season.id}
        onSeasonChange={vi.fn()}
      />,
    );
    expect(await screen.findByText("历史推导")).toBeTruthy();
    expect(screen.getByLabelText("FINAL主队胜者来源")).toHaveProperty(
      "value",
      data.games[0].id,
    );
    await user.click(screen.getByRole("button", { name: "确认历史关系" }));

    await waitFor(() => expect(applyBracketRelations).toHaveBeenCalledOnce());
    expect(applyBracketRelations.mock.calls[0][2]).toMatchObject({
      impact_hash: "bracket-hash",
      relations: data.legacy_suggestions,
    });
  });

  it("does not request a stale division while switching seasons", async () => {
    const secondSeason: AdminSeason = {
      ...season,
      id: "10000000-0000-0000-0000-000000000002",
      name: "演示赛季",
      divisions: [
        {
          ...season.divisions[0],
          id: "20000000-0000-0000-0000-000000000002",
        },
      ],
    };
    const getBracketManagement = vi.fn().mockImplementation(
      async (requestedSeasonId: string, requestedDivisionId: string) => ({
        ...data,
        season_id: requestedSeasonId,
        division_id: requestedDivisionId,
      }),
    );
    const client = { getBracketManagement } as unknown as AdminClient;
    const { rerender } = render(
      <BracketManagementPage
        client={client}
        seasons={[season, secondSeason]}
        seasonId={season.id}
        onSeasonChange={vi.fn()}
      />,
    );
    await waitFor(() => expect(getBracketManagement).toHaveBeenCalledWith(
      season.id,
      season.divisions[0].id,
    ));

    rerender(
      <BracketManagementPage
        client={client}
        seasons={[season, secondSeason]}
        seasonId={secondSeason.id}
        onSeasonChange={vi.fn()}
      />,
    );

    await waitFor(() => expect(getBracketManagement).toHaveBeenCalledWith(
      secondSeason.id,
      secondSeason.divisions[0].id,
    ));
    expect(getBracketManagement).not.toHaveBeenCalledWith(
      secondSeason.id,
      season.divisions[0].id,
    );
  });
});
