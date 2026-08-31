import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type {
  AdminSeason,
  DrawAssignmentDataset,
  DrawAssignmentPreview,
  DrawGameAssignmentPreview,
  createAdminClient,
} from "@pkuba/api-client";
import { DrawMappingPage } from "./DrawMappingPage";

type AdminClient = ReturnType<typeof createAdminClient>;

const season: AdminSeason = {
  id: "20000000-0000-0000-0000-000000000001",
  name: "2027 北大杯",
  competition_type: "PKU_CUP",
  year: 2027,
  status: "SETUP",
  starts_on: "2027-03-01",
  ends_on: "2027-05-31",
  version: 4,
  divisions: [
    {
      id: "30000000-0000-0000-0000-000000000001",
      code: "men-a",
      name: "男甲",
      gender: "MEN",
      version: 1,
    },
  ],
};

const teamIds = [
  "40000000-0000-0000-0000-000000000001",
  "40000000-0000-0000-0000-000000000002",
  "40000000-0000-0000-0000-000000000003",
  "40000000-0000-0000-0000-000000000004",
];
const slotIds = [
  "50000000-0000-0000-0000-000000000001",
  "50000000-0000-0000-0000-000000000002",
  "50000000-0000-0000-0000-000000000003",
  "50000000-0000-0000-0000-000000000004",
];

const dataset: DrawAssignmentDataset = {
  season_id: season.id,
  season_name: season.name,
  season_status: "SETUP",
  season_version: 4,
  read_only: false,
  locked_reason: "",
  divisions: [
    {
      id: season.divisions[0].id,
      code: "men-a",
      name: "男甲",
      gender: "MEN",
      sort_order: 1,
      version: 1,
      slot_count: 4,
      active_team_count: 4,
      assigned_count: 0,
      complete: false,
      teams: teamIds.map((id, index) => ({
        id,
        name: `球队${index + 1}`,
        active: true,
      })),
      groups: [
        {
          id: "60000000-0000-0000-0000-000000000001",
          code: "A",
          name: "A 组",
          sort_order: 1,
          slots: slotIds.slice(0, 2).map((id, index) => ({
            id,
            code: `M-A-${index + 1}`,
            label: `A 组第 ${index + 1} 签位`,
            seed: index + 1,
            team_id: null,
            team_name: null,
            team_active: null,
          })),
        },
        {
          id: "60000000-0000-0000-0000-000000000002",
          code: "B",
          name: "B 组",
          sort_order: 2,
          slots: slotIds.slice(2).map((id, index) => ({
            id,
            code: `M-B-${index + 1}`,
            label: `B 组第 ${index + 1} 签位`,
            seed: index + 1,
            team_id: null,
            team_name: null,
            team_active: null,
          })),
        },
      ],
      phases: [],
    },
  ],
};

const preview: DrawAssignmentPreview = {
  season_id: season.id,
  season_version: 4,
  division_id: season.divisions[0].id,
  division_name: "男甲",
  change_count: 4,
  affected_game_count: 2,
  public_impact: false,
  requires_confirmation: true,
  can_apply: true,
  impact_hash: "impact-hash",
  changes: slotIds.map((slotId, index) => ({
    slot_id: slotId,
    slot_code: index < 2 ? `M-A-${index + 1}` : `M-B-${index - 1}`,
    group_name: index < 2 ? "A 组" : "B 组",
    before_team_id: null,
    before_team_name: null,
    after_team_id: teamIds[index],
    after_team_name: `球队${index + 1}`,
  })),
  affected_games: [],
  blockers: [],
};

const assignedDataset: DrawAssignmentDataset = {
  ...dataset,
  season_version: 5,
  divisions: dataset.divisions.map((division) => ({
    ...division,
    assigned_count: 4,
    complete: true,
    groups: division.groups.map((group) => ({
      ...group,
      slots: group.slots.map((slot) => {
        const index = slotIds.indexOf(slot.id);
        return {
          ...slot,
          team_id: teamIds[index],
          team_name: `球队${index + 1}`,
          team_active: true,
        };
      }),
    })),
  })),
};

const gamePreview: DrawGameAssignmentPreview = {
  season_id: season.id,
  season_version: 4,
  game_id: "70000000-0000-0000-0000-000000000001",
  game_version: 1,
  division_id: season.divisions[0].id,
  stage: "KNOCKOUT",
  round_number: 2,
  home_team_id: teamIds[2],
  home_team_name: "球队3",
  away_team_id: teamIds[1],
  away_team_name: "球队2",
  participant_changed: true,
  public_impact: true,
  warnings: [
    {
      code: "TEAM_NOT_CONFIRMED_PREVIOUS_WINNER",
      message: "球队3 不是紧邻上一轮当前已确认的胜队；保存需要再次确认。",
      side: "HOME",
      team_id: teamIds[2],
      team_name: "球队3",
    },
  ],
  blockers: [],
  requires_override: true,
  can_apply: true,
  references: {},
  impact_hash: "game-impact-hash",
};

const knockoutDataset: DrawAssignmentDataset = {
  ...dataset,
  season_status: "PUBLISHED",
  divisions: [
    {
      ...dataset.divisions[0],
      phases: [
        {
          key: "KNOCKOUT:2",
          stage: "KNOCKOUT",
          round_number: 2,
          label: "淘汰赛第 2 轮",
          previous_phase_key: "KNOCKOUT:1",
          previous_winner_ids: [teamIds[0], teamIds[1]],
          previous_results_complete: true,
          games: [
            {
              id: gamePreview.game_id,
              code: "KO2-1",
              stage: "KNOCKOUT",
              round_number: 2,
              date: "2027-05-01",
              start_time: "18:30",
              venue_name: "邱德拔体育馆",
              home_slot_id: "71000000-0000-0000-0000-000000000001",
              home_slot_code: "K3",
              home_slot_label: "淘汰赛第二轮主方",
              away_slot_id: "71000000-0000-0000-0000-000000000002",
              away_slot_code: "K4",
              away_slot_label: "淘汰赛第二轮客方",
              home_team_id: null,
              home_team_name: null,
              away_team_id: null,
              away_team_name: null,
              home_validation: {
                mode: "UNASSIGNED",
                source_game_id: null,
                source_game_version: null,
                source_version_stale: false,
                review_required: false,
                status: "UNASSIGNED",
              },
              away_validation: {
                mode: "UNASSIGNED",
                source_game_id: null,
                source_game_version: null,
                source_version_stale: false,
                review_required: false,
                status: "UNASSIGNED",
              },
              review_required: false,
              status: "SCHEDULED",
              home_score: null,
              away_score: null,
              version: 1,
            },
          ],
        },
      ],
    },
  ],
};

function clientWith(
  currentDataset: DrawAssignmentDataset = dataset,
  overrides: Partial<Record<keyof AdminClient, unknown>> = {},
) {
  return {
    getDrawAssignments: vi.fn().mockResolvedValue(currentDataset),
    previewDrawAssignments: vi.fn().mockResolvedValue(preview),
    updateDrawAssignments: vi.fn().mockResolvedValue(currentDataset),
    previewGameDrawAssignments: vi.fn().mockResolvedValue(gamePreview),
    updateGameDrawAssignments: vi.fn().mockResolvedValue(currentDataset),
    ...overrides,
  } as unknown as AdminClient;
}

function renderPage(client: AdminClient, currentDataset = dataset) {
  render(
    <DrawMappingPage
      client={client}
      seasons={[season]}
      seasonId={season.id}
      onSeasonChange={vi.fn()}
      onDataChanged={vi.fn().mockResolvedValue(undefined)}
      onOpenTeams={vi.fn()}
      onOpenConfiguration={vi.fn()}
    />,
  );
  return currentDataset;
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("DrawMappingPage", () => {
  it("removes selected teams from other dropdowns and applies a complete division after preview", async () => {
    const user = userEvent.setup();
    const client = clientWith();
    renderPage(client);

    const first = await screen.findByRole("combobox", { name: "M-A-1 对应球队" });
    const second = screen.getByRole("combobox", { name: "M-A-2 对应球队" });
    await user.selectOptions(first, teamIds[0]);
    expect(within(second).queryByRole("option", { name: "球队1" })).toBeNull();

    await user.selectOptions(second, teamIds[1]);
    await user.selectOptions(
      screen.getByRole("combobox", { name: "M-B-1 对应球队" }),
      teamIds[2],
    );
    await user.selectOptions(
      screen.getByRole("combobox", { name: "M-B-2 对应球队" }),
      teamIds[3],
    );
    await user.click(screen.getByRole("button", { name: "预览整组影响" }));

    expect(await screen.findByText("4 个签位将变更")).toBeTruthy();
    expect(client.previewDrawAssignments).toHaveBeenCalledWith(season.id, {
      expected_season_version: 4,
      division_id: season.divisions[0].id,
      assignments: slotIds.map((slotId, index) => ({
        slot_id: slotId,
        team_id: teamIds[index],
      })),
    });

    await user.click(screen.getByRole("button", { name: "确认整组保存" }));
    await waitFor(() =>
      expect(client.updateDrawAssignments).toHaveBeenCalledWith(
        season.id,
        expect.objectContaining({ impact_hash: "impact-hash" }),
      ),
    );
  });

  it("blocks saving when the active team and slot counts do not match", async () => {
    const mismatch = {
      ...dataset,
      divisions: [{ ...dataset.divisions[0], active_team_count: 3 }],
    } as DrawAssignmentDataset;
    renderPage(clientWith(mismatch), mismatch);

    expect(await screen.findByText("球队数与签位数不一致")).toBeTruthy();
    expect(
      (screen.getByRole("button", { name: "预览整组影响" }) as HTMLButtonElement).disabled,
    ).toBe(true);
  });

  it("clears group dirty state when a slot is restored to its server value", async () => {
    const user = userEvent.setup();
    renderPage(clientWith());

    const first = await screen.findByRole("combobox", { name: "M-A-1 对应球队" });
    expect(screen.getByText("已与服务器同步")).toBeVisible();
    await user.selectOptions(first, teamIds[0]);
    expect(screen.getByText("有未保存修改")).toBeVisible();
    await user.selectOptions(first, "");
    expect(screen.getByText("已与服务器同步")).toBeVisible();
    expect(screen.getByRole("button", { name: "撤销" })).toBeDisabled();
  });

  it("uses a successful group save as the next dirty-state baseline", async () => {
    const user = userEvent.setup();
    const client = clientWith(dataset, {
      updateDrawAssignments: vi.fn().mockResolvedValue(assignedDataset),
    });
    renderPage(client);

    for (const index of slotIds.keys()) {
      await user.selectOptions(
        await screen.findByRole("combobox", { name: `${index < 2 ? "M-A" : "M-B"}-${index < 2 ? index + 1 : index - 1} 对应球队` }),
        teamIds[index],
      );
    }
    await user.click(screen.getByRole("button", { name: "预览整组影响" }));
    await user.click(await screen.findByRole("button", { name: "确认整组保存" }));
    expect(await screen.findByText("已与服务器同步")).toBeVisible();

    const first = screen.getByRole("combobox", { name: "M-A-1 对应球队" });
    await user.selectOptions(first, "");
    expect(screen.getByText("有未保存修改")).toBeVisible();
    await user.selectOptions(first, teamIds[0]);
    expect(screen.getByText("已与服务器同步")).toBeVisible();
  });

  it("derives each game dirty state from the current server baseline", async () => {
    const user = userEvent.setup();
    renderPage(clientWith(knockoutDataset), knockoutDataset);

    await user.click(await screen.findByRole("button", { name: /淘汰赛第 2 轮/ }));
    const home = screen.getByRole("combobox", { name: "KO2-1 主方球队" });
    const away = screen.getByRole("combobox", { name: "KO2-1 客方球队" });
    await user.selectOptions(home, teamIds[2]);
    await user.selectOptions(away, teamIds[1]);
    expect(screen.getByText("本场有未保存修改")).toBeVisible();
    await user.selectOptions(home, "");
    await user.selectOptions(away, "");
    expect(screen.getByText("本场已保存")).toBeVisible();
    await user.selectOptions(home, teamIds[2]);
    expect(screen.getByText("本场有未保存修改")).toBeVisible();
  });

  it("keeps archived seasons read-only", async () => {
    const archived = {
      ...dataset,
      season_status: "ARCHIVED",
      read_only: true,
      locked_reason: "归档赛季的签位结果录入只读。",
    } as DrawAssignmentDataset;
    renderPage(clientWith(archived), archived);

    expect(await screen.findByText("当前页面只读")).toBeTruthy();
    expect(
      (screen.getByRole("combobox", { name: "M-A-1 对应球队" }) as HTMLSelectElement)
        .disabled,
    ).toBe(true);
  });

  it("requires an explicit warning override before saving a later knockout round", async () => {
    const client = clientWith(knockoutDataset, {
      updateGameDrawAssignments: vi.fn().mockResolvedValue(knockoutDataset),
    });
    const user = userEvent.setup();
    renderPage(client, knockoutDataset);

    await user.click(await screen.findByRole("button", { name: /淘汰赛第 2 轮/ }));
    await user.selectOptions(
      screen.getByRole("combobox", { name: "KO2-1 主方球队" }),
      teamIds[2],
    );
    await user.selectOptions(
      screen.getByRole("combobox", { name: "KO2-1 客方球队" }),
      teamIds[1],
    );
    await user.click(screen.getByRole("button", { name: "逐场预览" }));

    const save = await screen.findByRole("button", { name: "确认越级并保存" });
    expect(save).toBeDisabled();
    await user.click(
      screen.getByRole("checkbox", {
        name: "我确认越过上一轮胜队校验，并按当前球队保存",
      }),
    );
    await user.click(save);

    await waitFor(() =>
      expect(client.updateGameDrawAssignments).toHaveBeenCalledWith(
        season.id,
        gamePreview.game_id,
        expect.objectContaining({
          override_warnings: true,
          impact_hash: "game-impact-hash",
        }),
      ),
    );
  });
});
