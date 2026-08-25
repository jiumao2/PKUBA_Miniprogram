import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type {
  AdminAccount,
  AdminSeason,
  ScheduleImport,
  ScheduleImportReadiness,
  ScheduleImportResetPreview,
  SeasonConfiguration,
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
      version: 1,
    },
  ],
};

const readiness: ScheduleImportReadiness = {
  season_id: season.id,
  season_version: 4,
  ready: true,
  template_ready: true,
  division_count: 1,
  team_count: 12,
  period_count: 3,
  venue_count: 4,
  slot_family_count: 4,
  grid_column_count: 16,
  calendar_day_count: 92,
  expected_game_count: 3,
  existing_game_count: 2,
  blockers: [],
  template_blockers: [],
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

const configuration: SeasonConfiguration = {
  id: season.id,
  name: season.name,
  competition_type: season.competition_type,
  year: season.year,
  status: season.status,
  starts_on: season.starts_on,
  ends_on: season.ends_on,
  timezone: "Asia/Shanghai",
  version: season.version,
  editable: true,
  maintenance_required: false,
  locked_reason: "",
  divisions: season.divisions.map((row, index) => ({
    ...row,
    sort_order: index + 1,
    team_count: 12,
    group_count: 2,
    game_count: 3,
  })),
  venues: ["五四东一", "五四东二", "五四东三"].map((name, index) => ({
    id: `31000000-0000-0000-0000-00000000000${index + 1}`,
    name,
    sort_order: index + 1,
    active: true,
    game_count: 0,
  })),
  periods: ["12:50", "14:20", "15:50"].map((startTime, index) => ({
    id: `32000000-0000-0000-0000-00000000000${index + 1}`,
    code: `p${index + 1}`,
    name: `第${index + 1}时段`,
    start_time: startTime,
    sort_order: index + 1,
    default_capacities: { WEEKDAY: index === 0 ? 1 : 0, WEEKEND: 3 },
    game_count: 0,
    active_reservation_count: 0,
  })),
  slot_families: [{
    id: "33000000-0000-0000-0000-000000000001",
    division_id: season.divisions[0].id,
    division_code: season.divisions[0].code,
    division_name: season.divisions[0].name,
    gender: season.divisions[0].gender,
    stage: "GROUP",
    stage_name: "小组赛",
    round_number: 1,
    prefix: "A",
    slot_count: 12,
    sort_order: 1,
    expected_game_count: 66,
  }],
  grid_columns: [{
    id: "34000000-0000-0000-0000-000000000001",
    period_id: "32000000-0000-0000-0000-000000000001",
    period_code: "p1",
    period_name: "第一时段",
    start_time: "12:50",
    venue_id: "31000000-0000-0000-0000-000000000001",
    venue_name: "五四东一",
    final_only: false,
    sort_order: 1,
  }],
  date_capacity_overrides: [],
  over_capacity: [],
};

const validatedBatch: ScheduleImport = {
  id: "40000000-0000-0000-0000-000000000001",
  season_id: season.id,
  status: "VALIDATED",
  template_version: "3.3.0",
  file_sha256: "a".repeat(64),
  source_kind: "XLSX",
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
    covered_game_count: 3,
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
        code: "men-GRP-A1-A2",
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
        period_code: "P1",
        period_name: "第一时段",
        nominal_start_time: "12:50",
        start_time: "12:50",
        standard_venue_id: null,
        venue_name: "五四东一",
        final_only: false,
        leader_adjustable: true,
        cell: "E8",
      },
    ],
    prerequisites: {
      division_count: 1,
      team_count: 12,
      period_count: 3,
      venue_count: 4,
      slot_family_count: 4,
      grid_column_count: 16,
      calendar_day_count: 92,
      expected_game_count: 3,
    },
    error_count: 0,
    warning_count: 1,
  },
};

function clientWith(overrides: Partial<Record<keyof AdminClient, unknown>> = {}) {
  return {
    getScheduleImportReadiness: vi.fn().mockResolvedValue(readiness),
    getScheduleImportResetPreview: vi.fn().mockResolvedValue(noReset),
    getSeasonConfiguration: vi.fn().mockResolvedValue(configuration),
    previewSeasonConfiguration: vi.fn().mockResolvedValue({
      season_id: season.id,
      season_version: season.version,
      maintenance_required: false,
      changed: true,
      over_capacity: [],
      affected_reschedule_request_ids: [],
      templates_invalidated: true,
      impact_hash: "grid-preview-hash",
    }),
    updateSeasonConfiguration: vi.fn().mockResolvedValue(configuration),
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

function renderWorkspace(
  client: AdminClient,
  onOpenEditor = vi.fn(),
  onOpenConfiguration = vi.fn(),
) {
  const onDataChanged = vi.fn().mockResolvedValue(undefined);
  render(
    <ScheduleImportWorkspace
      account={account}
      client={client}
      seasons={[season]}
      season={season}
      onSeasonChange={vi.fn()}
      onDataChanged={onDataChanged}
      onOpenConfiguration={onOpenConfiguration}
      onOpenEditor={onOpenEditor}
    />,
  );
  return { onDataChanged, onOpenConfiguration };
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

    expect(await screen.findByText("模板可下载")).toBeTruthy();
    const stepList = screen.getByRole("list", { name: "赛程导入步骤" });
    expect(stepList.querySelectorAll(":scope > li")).toHaveLength(3);
    expect(screen.getByText("下载模板，只填写第三页")).toBeTruthy();
    expect(screen.getByRole("table", { name: "赛程网格列设置" })).toBeTruthy();
    expect(screen.getByText("新赛季固定预填当前系统的 16 个标准场地时段，不从历史赛季读取；只在需要改变第三页排版时调整。")).toBeTruthy();
    expect(screen.getByRole("table", { name: "第三页赛程网格填写示意" })).toBeTruthy();
    expect(screen.getByText("星期")).toBeTruthy();
    expect(screen.queryByText("比赛清单")).toBeNull();
    expect(screen.queryByText("签位代码")).toBeNull();
    expect(screen.queryByText(/可调政策/)).toBeNull();
    expect(screen.getAllByText("A1vsA2", { exact: false }).length).toBeGreaterThan(0);

    const file = new File(["xlsx"], "schedule.xlsx", {
      type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    });
    const dropzone = screen.getByRole("button", { name: /拖放工作簿/ });
    fireEvent.drop(dropzone, { dataTransfer: { files: [file] } });
    expect(screen.getByText("schedule.xlsx")).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "上传并校验" }));
    expect(await screen.findByText("men-GRP-A1-A2")).toBeTruthy();
    expect(screen.getByText("警告 · 1")).toBeTruthy();
    expect(screen.getByText("A 组 1 号签")).toBeTruthy();
    expect(screen.getByRole("list", { name: "最终赛程核对清单" })).toBeTruthy();
    expect(screen.getByText("对阵完整且唯一")).toBeTruthy();
    expect(screen.getByText("比赛数量正确")).toBeTruthy();
    expect(screen.getByText("比赛容量充足")).toBeTruthy();
    expect(screen.getByText("排期资源无冲突")).toBeTruthy();
    expect(screen.getByText("文件与新增边界通过")).toBeTruthy();
    expect(screen.getByText("5 / 5 通过")).toBeTruthy();

    await user.click(screen.getByLabelText(/我已逐项核对以上结果/));
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

  it("downloads a configured template even when the season is no longer in setup", async () => {
    const activeReadiness: ScheduleImportReadiness = {
      ...readiness,
      ready: false,
      blockers: [
        {
          code: "SEASON_NOT_SETUP",
          message: "当前赛季可以下载模板，但只有准备中的赛季可以上传并确认新赛程。",
          count: 1,
        },
      ],
    };
    const blob = new Blob(["xlsx"], {
      type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    });
    const downloadScheduleTemplate = vi.fn().mockResolvedValue(blob);
    const client = clientWith({
      getScheduleImportReadiness: vi.fn().mockResolvedValue(activeReadiness),
      downloadScheduleTemplate,
    });
    const createObjectURL = vi.fn().mockReturnValue("blob:pkuba-template");
    const revokeObjectURL = vi.fn();
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      value: createObjectURL,
    });
    Object.defineProperty(URL, "revokeObjectURL", {
      configurable: true,
      value: revokeObjectURL,
    });
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    const user = userEvent.setup();
    renderWorkspace(client);

    expect(await screen.findByText("模板可下载，但暂不能导入 · 1")).toBeTruthy();
    const downloadButton = screen.getByRole("button", { name: "下载 XLSX 模板" });
    expect((downloadButton as HTMLButtonElement).disabled).toBe(false);
    await user.click(downloadButton);

    await waitFor(() => expect(downloadScheduleTemplate).toHaveBeenCalledWith(season.id));
    expect(createObjectURL).toHaveBeenCalledWith(blob);
    expect(click).toHaveBeenCalledOnce();
    expect(await screen.findByText(/模板下载已开始/)).toBeTruthy();
  });

  it("configures missing grid columns on the import page", async () => {
    const templateBlocker = {
      code: "NO_GRID_COLUMNS",
      message: "请先在赛程导入页配置至少一个赛程网格列。",
      count: 1,
    };
    const emptyConfiguration = { ...configuration, grid_columns: [] };
    const updatedConfiguration = {
      ...configuration,
      version: configuration.version + 1,
      grid_columns: [{
        ...configuration.grid_columns[0],
        id: "34000000-0000-0000-0000-000000000009",
      }],
    };
    const getSeasonConfiguration = vi.fn()
      .mockResolvedValueOnce(emptyConfiguration)
      .mockResolvedValue(updatedConfiguration);
    const updateSeasonConfiguration = vi.fn().mockResolvedValue(updatedConfiguration);
    const client = clientWith({
      getSeasonConfiguration,
      updateSeasonConfiguration,
      getScheduleImportReadiness: vi.fn().mockResolvedValue({
        ...readiness,
        ready: false,
        template_ready: false,
        grid_column_count: 0,
        blockers: [templateBlocker],
        template_blockers: [templateBlocker],
      } satisfies ScheduleImportReadiness),
    });
    const onOpenConfiguration = vi.fn();
    const user = userEvent.setup();
    renderWorkspace(client, vi.fn(), onOpenConfiguration);

    expect(await screen.findByText("模板配置未完成")).toBeTruthy();
    expect(screen.getByText("请先在赛程导入页配置至少一个赛程网格列。")).toBeTruthy();
    expect(screen.getByText("至少添加一个时段与场地组合，模板才可下载。")).toBeTruthy();
    expect((screen.getByRole("button", { name: "下载 XLSX 模板" }) as HTMLButtonElement).disabled)
      .toBe(true);
    await user.click(screen.getByRole("button", { name: "＋ 添加列" }));
    expect(screen.getByRole("combobox", { name: "五四东一网格列时段" })).toBeTruthy();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    await user.click(screen.getByRole("button", { name: "保存网格列" }));
    await waitFor(() => expect(updateSeasonConfiguration).toHaveBeenCalledOnce());
    // V3.3 ignores the retired season-level grid configuration. Dynamic columns
    // live only in the independent schedule draft / workbook header.
    expect(updateSeasonConfiguration.mock.calls[0][1].grid_columns).toHaveLength(0);
    await user.click(screen.getByRole("button", { name: "前往赛季和组别" }));
    expect(onOpenConfiguration).toHaveBeenCalledOnce();
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
    await screen.findByText("模板可下载");

    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    await user.upload(input, new File(["xlsx"], "invalid.xlsx"));
    await user.click(screen.getByRole("button", { name: "上传并校验" }));

    expect(await screen.findByText("错误 · 1")).toBeTruthy();
    expect(screen.getByText("比赛编号 G001 已存在。")).toBeTruthy();
    expect(screen.getByText("尚不能确认创建")).toBeTruthy();
    expect(screen.getByText("4 / 5 通过")).toBeTruthy();
    expect((screen.getByRole("button", { name: "确认并创建" }) as HTMLButtonElement).disabled)
      .toBe(true);
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
