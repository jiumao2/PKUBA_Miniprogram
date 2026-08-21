import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type {
  AdminSeason,
  DrawAssignmentDataset,
  DrawAssignmentPreview,
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
      operation_status: "SETUP",
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
      operation_status: "SETUP",
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

function clientWith(
  currentDataset: DrawAssignmentDataset = dataset,
  overrides: Partial<Record<keyof AdminClient, unknown>> = {},
) {
  return {
    getDrawAssignments: vi.fn().mockResolvedValue(currentDataset),
    previewDrawAssignments: vi.fn().mockResolvedValue(preview),
    updateDrawAssignments: vi.fn().mockResolvedValue(currentDataset),
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
    await user.click(screen.getByRole("button", { name: "检查并保存" }));

    expect(await screen.findByText("男甲抽签影响")).toBeTruthy();
    expect(client.previewDrawAssignments).toHaveBeenCalledWith(season.id, {
      expected_season_version: 4,
      division_id: season.divisions[0].id,
      assignments: slotIds.map((slotId, index) => ({
        slot_id: slotId,
        team_id: teamIds[index],
      })),
    });

    await user.click(screen.getByRole("button", { name: "确认写入抽签结果" }));
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
      (screen.getByRole("button", { name: "检查并保存" }) as HTMLButtonElement).disabled,
    ).toBe(true);
  });

  it("keeps archived seasons read-only", async () => {
    const archived = {
      ...dataset,
      season_status: "ARCHIVED",
      read_only: true,
      locked_reason: "归档赛季的抽签映射只读。",
    } as DrawAssignmentDataset;
    renderPage(clientWith(archived), archived);

    expect(await screen.findByText("当前页面只读")).toBeTruthy();
    expect(
      (screen.getByRole("combobox", { name: "M-A-1 对应球队" }) as HTMLSelectElement)
        .disabled,
    ).toBe(true);
  });
});
