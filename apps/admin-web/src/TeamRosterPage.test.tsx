import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type {
  AdminSeason,
  RosterDataset,
  RosterImport,
  TeamMaintenancePreview,
  createAdminClient,
} from "@pkuba/api-client";
import { TeamRosterPage } from "./TeamRosterPage";

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

const dataset: RosterDataset = {
  season_id: season.id,
  season_name: season.name,
  season_status: "SETUP",
  season_version: 4,
  read_only: false,
  team_count: 1,
  active_team_count: 1,
  player_count: 1,
  active_player_count: 1,
  divisions: [
    {
      id: season.divisions[0].id,
      code: "men-a",
      name: "男甲",
      gender: "MEN",
      sort_order: 1,
    },
  ],
  teams: [
    {
      id: "40000000-0000-0000-0000-000000000001",
      season_id: season.id,
      division_id: season.divisions[0].id,
      name: "信息科学技术学院",
      active: true,
      version: 3,
      players: [
        {
          id: "50000000-0000-0000-0000-000000000001",
          name: "张三",
          jersey_number: "00",
          eligible: true,
          active: true,
          version: 2,
        },
      ],
    },
  ],
  import_state: {
    allowed: true,
    blockers: [],
    confirmed_batch_id: null,
    confirmed_at: null,
  },
};

const batch: RosterImport = {
  id: "60000000-0000-0000-0000-000000000001",
  season_id: season.id,
  status: "VALIDATED",
  template_version: "1.0.0",
  file_sha256: "a".repeat(64),
  base_season_version: 4,
  uploaded_at: "2027-01-01T00:00:00Z",
  confirmed_at: null,
  confirmed_by: null,
  issues: [
    {
      severity: "WARNING",
      code: "SIMILAR_TEAM_NAMES",
      cell: "",
      message: "两个名称可能是同一球队。",
      context: {},
    },
  ],
  summary: {
    team_count: 2,
    player_count: 2,
    error_count: 0,
    warning_count: 1,
    division_stats: [
      {
        division_id: season.divisions[0].id,
        division_name: "男甲",
        team_count: 2,
        player_count: 2,
        expected_team_count: 2,
        slot_count_mismatch: false,
      },
    ],
    name_resolutions: [
      {
        key: "resolution-1",
        division_name: "男甲",
        source_name: "信息学院",
        canonical_name: "信息学院",
      },
    ],
    teams: [
      {
        division_id: season.divisions[0].id,
        division_name: "男甲",
        name: "信息学院",
        source_names: ["信息学院"],
        player_count: 1,
      },
    ],
  },
};

function clientWith(overrides: Partial<Record<keyof AdminClient, unknown>> = {}) {
  return {
    getRosterDataset: vi.fn().mockResolvedValue(dataset),
    downloadRosterTemplate: vi.fn().mockResolvedValue(new Blob(["xlsx"])),
    uploadRoster: vi.fn().mockResolvedValue(batch),
    resolveRosterNames: vi.fn().mockResolvedValue(batch),
    confirmRosterImport: vi.fn().mockResolvedValue({ ...batch, status: "CONFIRMED" }),
    previewTeamRoster: vi.fn(),
    saveTeamRoster: vi.fn().mockResolvedValue(dataset.teams[0]),
    createRosterTeam: vi.fn(),
    ...overrides,
  } as unknown as AdminClient;
}

function renderPage(client: AdminClient) {
  render(
    <TeamRosterPage
      client={client}
      seasons={[season]}
      seasonId={season.id}
      onSeasonChange={vi.fn()}
      onDataChanged={vi.fn().mockResolvedValue(undefined)}
      onOpenConfiguration={vi.fn()}
    />,
  );
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("TeamRosterPage", () => {
  it("uploads a workbook, exposes name resolution, and requires warning acknowledgement", async () => {
    const user = userEvent.setup();
    const client = clientWith();
    renderPage(client);

    expect(await screen.findByText("信息科学技术学院")).toBeTruthy();
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File(["xlsx"], "roster.xlsx", {
      type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    });
    fireEvent.change(input, { target: { files: [file] } });

    expect(await screen.findByText("导入审计与最终变更")).toBeTruthy();
    expect(screen.getByDisplayValue("信息学院")).toBeTruthy();
    const confirm = screen.getByRole("button", {
      name: "确认创建球队与名单",
    }) as HTMLButtonElement;
    expect(confirm.disabled).toBe(true);
    await user.click(screen.getByRole("checkbox", { name: /我已逐条核对警告/ }));
    expect(confirm.disabled).toBe(false);
    await user.click(confirm);
    await waitFor(() => expect(client.confirmRosterImport).toHaveBeenCalledWith(batch.id, {
      expected_season_version: 4,
      warnings_acknowledged: true,
    }));
  });

  it("uses a maintenance preview before renaming a published-season team", async () => {
    const user = userEvent.setup();
    const activeDataset = { ...dataset, season_status: "PUBLISHED" } as RosterDataset;
    const preview: TeamMaintenancePreview = {
      team_id: dataset.teams[0].id,
      requires_confirmation: true,
      maintenance_token: "maintenance-token",
      changes: {},
      references: { games: 2, draw_assignments: 1, leader_bindings: 1 },
      message: "该修改会影响已开始赛季中的球队。",
    };
    const client = clientWith({
      getRosterDataset: vi.fn().mockResolvedValue(activeDataset),
      previewTeamRoster: vi.fn().mockResolvedValue(preview),
    });
    renderPage(client);

    const name = await screen.findByDisplayValue("信息科学技术学院");
    await user.clear(name);
    await user.type(name, "信息科学技术学院篮球队");
    await user.click(screen.getByRole("button", { name: "保存完整名单" }));

    expect(await screen.findByText("维护修改二次确认")).toBeTruthy();
    expect(screen.getByText(/比赛 2 · 抽签 1 · 领队 1/)).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "确认维护修改" }));
    await waitFor(() =>
      expect(client.saveTeamRoster).toHaveBeenCalledWith(
        dataset.teams[0].id,
        expect.objectContaining({ maintenance_token: "maintenance-token" }),
      ),
    );
  });
});
