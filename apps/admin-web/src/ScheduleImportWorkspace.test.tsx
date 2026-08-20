import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type {
  AdminAccount,
  AdminSeason,
  ScheduleImport,
  ScheduleImportReadiness,
  ScheduleImportResetPreview,
  createAdminClient,
} from "@pkuba/api-client";
import { ScheduleImportWorkspace } from "./ScheduleImportWorkspace";

type AdminClient = ReturnType<typeof createAdminClient>;

const account: AdminAccount = {
  id: "10000000-0000-0000-0000-000000000001",
  username: "schedule-admin",
  role: "SUPERADMIN",
  version: 1,
};

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
      code: "men",
      name: "男子组",
      gender: "MEN",
    },
  ],
};

const readiness: ScheduleImportReadiness = {
  season_id: season.id,
  season_version: 4,
  ready: true,
  division_count: 1,
  team_count: 12,
  period_count: 3,
  venue_count: 4,
  open_grid_row_count: 30,
  existing_game_count: 2,
  blockers: [],
};

const noReset: ScheduleImportResetPreview = {
  season_id: season.id,
  season_name: season.name,
  season_version: 4,
  eligible: false,
  confirmed_batch_count: 0,
  game_count: 0,
  slot_count: 0,
  group_count: 0,
  batch_ids: [],
  blockers: [
    {
      code: "NO_CONFIRMED_IMPORTS",
      message: "本赛季没有可重置的已确认导入批次。",
      count: 1,
    },
  ],
};

const validatedBatch: ScheduleImport = {
  id: "40000000-0000-0000-0000-000000000001",
  season_id: season.id,
  status: "VALIDATED",
  template_version: "2.0.0",
  file_sha256: "a".repeat(64),
  issues: [
    {
      severity: "WARNING",
      code: "REFERENCE_ONLY",
      cell: "A6",
      message: "现有小组定义一致，将直接引用。",
      context: {},
    },
  ],
  summary: {
    existing_game_count: 2,
    new_group_count: 1,
    referenced_group_count: 0,
    new_slot_count: 2,
    referenced_slot_count: 0,
    new_game_count: 1,
    groups: [
      {
        action: "CREATE",
        division_code: "men",
        division_name: "男子组",
        code: "A",
        name: "A 组",
        sort_order: 1,
      },
    ],
    slots: [],
    games: [
      {
        action: "CREATE",
        code: "G001",
        division_code: "men",
        division_name: "男子组",
        group_code: "A",
        stage: "GROUP",
        stage_name: "小组赛",
        round_number: 1,
        home_slot_code: "A1",
        home_slot_label: "A 组 1 号签",
        away_slot_code: "A2",
        away_slot_label: "A 组 2 号签",
        date: "2027-03-08",
        period_code: "p1",
        period_name: "第一时段",
        start_time: "12:10",
        venue_code: "court-1",
        venue_name: "五四一号场",
        cell: "E8",
      },
    ],
    prerequisites: {
      division_count: 1,
      team_count: 12,
      period_count: 3,
      venue_count: 4,
      open_grid_row_count: 30,
    },
    error_count: 0,
    warning_count: 1,
  },
};

function clientWith(overrides: Partial<Record<keyof AdminClient, unknown>> = {}) {
  return {
    getScheduleImportReadiness: vi.fn().mockResolvedValue(readiness),
    getScheduleImportResetPreview: vi.fn().mockResolvedValue(noReset),
    downloadScheduleTemplate: vi.fn(),
    uploadSchedule: vi.fn().mockResolvedValue(validatedBatch),
    confirmScheduleImport: vi.fn().mockResolvedValue({
      ...validatedBatch,
      status: "CONFIRMED",
      summary: {
        ...validatedBatch.summary,
        confirmed_season_version: 5,
      },
    }),
    resetScheduleImports: vi.fn(),
    ...overrides,
  } as unknown as AdminClient;
}

function renderWorkspace(client: AdminClient, onOpenEditor = vi.fn()) {
  const onDataChanged = vi.fn().mockResolvedValue(undefined);
  render(
    <ScheduleImportWorkspace
      account={account}
      client={client}
      seasons={[season]}
      season={season}
      onSeasonChange={vi.fn()}
      onDataChanged={onDataChanged}
      onOpenEditor={onOpenEditor}
    />,
  );
  return { onDataChanged };
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("ScheduleImportWorkspace", () => {
  it("supports drag upload, warning preview, confirmation, and editor handoff", async () => {
    const user = userEvent.setup();
    const client = clientWith();
    const onOpenEditor = vi.fn();
    renderWorkspace(client, onOpenEditor);

    expect(await screen.findByText("可以导入")).toBeTruthy();
    expect(screen.getByText("赛制定义")).toBeTruthy();
    expect(screen.getByText("主方签位代码")).toBeTruthy();

    const file = new File(["xlsx"], "schedule.xlsx", {
      type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    });
    const dropzone = screen.getByRole("button", { name: /拖放工作簿/ });
    fireEvent.drop(dropzone, { dataTransfer: { files: [file] } });
    expect(screen.getByText("schedule.xlsx")).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "上传并校验" }));
    expect(await screen.findByText("G001")).toBeTruthy();
    expect(screen.getByText("警告 · 1")).toBeTruthy();
    expect(screen.getByText("A 组 1 号签")).toBeTruthy();

    await user.click(screen.getByLabelText(/我已核对将新增/));
    await user.click(screen.getByRole("button", { name: "确认并创建" }));
    await waitFor(() => {
      expect(client.confirmScheduleImport).toHaveBeenCalledWith(validatedBatch.id, {
        expected_season_version: 4,
      });
    });
    expect(await screen.findByText("本批次已创建完成")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "前往赛程编辑" }));
    expect(onOpenEditor).toHaveBeenCalledOnce();
  });

  it("classifies blocking errors and disables confirmation", async () => {
    const invalidBatch: ScheduleImport = {
      ...validatedBatch,
      issues: [
        {
          severity: "ERROR",
          code: "GAME_CODE_ALREADY_EXISTS",
          cell: "A6",
          message: "比赛编号 G001 已存在。",
          context: {},
        },
        ...validatedBatch.issues,
      ],
      summary: { ...validatedBatch.summary, error_count: 1 },
    };
    const client = clientWith({ uploadSchedule: vi.fn().mockResolvedValue(invalidBatch) });
    const user = userEvent.setup();
    renderWorkspace(client);
    await screen.findByText("可以导入");

    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    await user.upload(input, new File(["xlsx"], "invalid.xlsx"));
    await user.click(screen.getByRole("button", { name: "上传并校验" }));

    expect(await screen.findByText("错误 · 1")).toBeTruthy();
    expect(screen.getByText("比赛编号 G001 已存在。")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "确认并创建" })).toBeNull();
  });

  it("requires reset precheck, exact season name, checkbox, and browser confirmation", async () => {
    const resetPreview: ScheduleImportResetPreview = {
      ...noReset,
      season_version: 8,
      eligible: true,
      confirmed_batch_count: 2,
      game_count: 18,
      slot_count: 12,
      group_count: 3,
      batch_ids: ["batch-1", "batch-2"],
      blockers: [],
    };
    const resetScheduleImports = vi.fn().mockResolvedValue({
      season_id: season.id,
      season_version: 9,
      rolled_back_at: "2026-08-20T10:00:00+08:00",
      game_count: 18,
      slot_count: 12,
      group_count: 3,
      batch_count: 2,
    });
    const client = clientWith({
      getScheduleImportResetPreview: vi.fn().mockResolvedValue(resetPreview),
      resetScheduleImports,
    });
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const user = userEvent.setup();
    renderWorkspace(client);

    expect(await screen.findByText("18 场比赛")).toBeTruthy();
    const resetButton = screen.getByRole("button", { name: "二次确认并重置" }) as HTMLButtonElement;
    expect(resetButton.disabled).toBe(true);
    await user.type(screen.getByPlaceholderText(season.name), season.name);
    await user.click(screen.getByLabelText(/我理解重置后需要重新上传/));
    expect(resetButton.disabled).toBe(false);
    await user.click(resetButton);

    await waitFor(() => {
      expect(resetScheduleImports).toHaveBeenCalledWith(season.id, {
        expected_season_version: 8,
        season_name: season.name,
      });
    });
    expect(window.confirm).toHaveBeenCalledOnce();
    expect(await screen.findByText(/重置完成：删除 18 场比赛/)).toBeTruthy();
  });
});
