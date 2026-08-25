import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ScheduleDraft } from "@pkuba/api-client";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SchedulePlannerWorkspace } from "./SchedulePlannerWorkspace";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const season = {
  id: "season-1",
  name: "2026 北大杯",
  competition_type: "PKU_CUP",
  year: 2026,
  status: "SETUP",
  starts_on: "2026-03-21",
  ends_on: "2026-03-22",
  version: 3,
  divisions: [],
};

const draft: ScheduleDraft = {
  id: "draft-1",
  season_id: season.id,
  season_version: season.version,
  version: 2,
  template_version: "3.3.0",
  source_name: "",
  updated_at: "2026-08-21T00:00:00Z",
  periods: [
    { id: "period-1", code: "p1", name: "第一时段", start_time: "12:50" },
  ],
  dates: [
    { date: "2026-03-21", weekday: "周六" },
    { date: "2026-03-22", weekday: "周日" },
  ],
  columns: [
    {
      id: "column-1",
      period_id: "period-1",
      period_code: "p1",
      period_name: "第一时段",
      start_time: "12:50",
      venue_name: "五四东一",
      final_only: false,
      sort_order: 1,
    },
  ],
  cells: [],
  matchup_pool: [
    {
      key: "men-a|GROUP|A1|A2",
      matchup: "A1vsA2",
      division_code: "men-a",
      division_name: "男甲",
      gender: "MEN",
      stage: "GROUP",
      stage_name: "小组赛",
      scheduled: false,
      already_formal: false,
    },
  ],
  summary: {
    expected_game_count: 1,
    draft_game_count: 0,
    locked_game_count: 0,
    column_count: 1,
    calendar_day_count: 2,
  },
};

const readiness = {
  season_id: season.id,
  season_version: season.version,
  ready: true,
  template_ready: true,
  division_count: 1,
  team_count: 2,
  period_count: 1,
  venue_count: 0,
  slot_family_count: 1,
  grid_column_count: 1,
  calendar_day_count: 2,
  expected_game_count: 1,
  existing_game_count: 0,
  blockers: [],
  template_blockers: [],
};

const batch = {
  id: "batch-1",
  season_id: season.id,
  status: "VALIDATED",
  template_version: "3.3.0",
  file_sha256: "0".repeat(64),
  summary: {
    existing_game_count: 0,
    covered_game_count: 1,
    new_group_count: 1,
    referenced_group_count: 0,
    new_slot_count: 2,
    referenced_slot_count: 0,
    new_game_count: 1,
    groups: [],
    slots: [],
    games: [
      {
        action: "CREATE",
        code: "men-a-GRP-A1-A2",
        division_code: "men-a",
        division_name: "男甲",
        group_code: "A",
        stage: "GROUP",
        stage_name: "小组赛",
        round_number: 1,
        home_slot_code: "A1",
        home_slot_label: "A1",
        away_slot_code: "A2",
        away_slot_label: "A2",
        date: "2026-03-21",
        period_code: "p1",
        period_name: "第一时段",
        start_time: "12:50",
        venue_name: "五四东一",
        cell: "赛程网格!C7",
      },
    ],
    prerequisites: {
      division_count: 1,
      team_count: 2,
      period_count: 1,
      venue_count: 0,
      slot_family_count: 1,
      grid_column_count: 1,
      calendar_day_count: 2,
      expected_game_count: 1,
    },
    error_count: 0,
    warning_count: 0,
  },
  issues: [],
};

function clientWith(overrides = {}) {
  return {
    getScheduleDraft: vi.fn().mockResolvedValue(draft),
    getScheduleImportReadiness: vi.fn().mockResolvedValue(readiness),
    getScheduleImportResetPreview: vi.fn().mockResolvedValue({
      season_id: season.id,
      season_name: season.name,
      season_version: season.version,
      eligible: false,
      confirmed_batch_count: 0,
      game_count: 0,
      slot_count: 0,
      group_count: 0,
      batch_ids: [],
      blockers: [{ code: "NO_CONFIRMED_IMPORTS", message: "没有可重置批次", count: 1 }],
    }),
    updateScheduleDraft: vi.fn().mockResolvedValue(draft),
    downloadScheduleTemplate: vi.fn().mockResolvedValue(new Blob(["xlsx"])),
    exportScheduleDraft: vi.fn().mockResolvedValue(new Blob(["xlsx"])),
    importScheduleDraft: vi.fn().mockResolvedValue(draft),
    validateScheduleDraft: vi.fn().mockResolvedValue(batch),
    confirmScheduleImport: vi.fn().mockResolvedValue({ ...batch, status: "CONFIRMED" }),
    resetScheduleImports: vi.fn(),
    ...overrides,
  };
}

function renderWorkspace(client = clientWith()) {
  render(
    <SchedulePlannerWorkspace
      account={{ id: "admin-1", username: "admin", role: "SUPERADMIN", version: 1 }}
      client={client as never}
      seasons={[season] as never}
      season={season as never}
      onSeasonChange={vi.fn()}
      onDataChanged={vi.fn().mockResolvedValue(undefined)}
      onOpenConfiguration={vi.fn()}
      onOpenEditor={vi.fn()}
    />,
  );
  return client;
}

describe("SchedulePlannerWorkspace", () => {
  it("presents one three-step planner with editable grid and V3.3 sheet guidance", async () => {
    renderWorkspace();
    expect(await screen.findByRole("heading", { name: "赛程网格" })).toBeTruthy();
    expect(screen.getByText("编排草稿")).toBeTruthy();
    expect(screen.getByText("核对规则")).toBeTruthy();
    expect(screen.getByText("确认创建")).toBeTruthy();
    expect(screen.getByText(/填写说明 \/ 签位定义（仅提示） \/ 赛程网格/)).toBeTruthy();
    expect(screen.queryByText("比赛清单")).toBeNull();
    expect(screen.queryByText("标准场地时段")).toBeNull();
    expect(screen.getByText("A1vsA2")).toBeTruthy();
  });

  it("downloads the generated blank template when upstream configuration is ready", async () => {
    const createObjectURL = vi.fn().mockReturnValue("blob:pkuba-v33-template");
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      value: createObjectURL,
    });
    Object.defineProperty(URL, "revokeObjectURL", {
      configurable: true,
      value: vi.fn(),
    });
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    const client = renderWorkspace();
    await screen.findByRole("heading", { name: "赛程网格" });

    const download = screen.getByRole("button", { name: "下载空白模板" });
    expect((download as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(download);

    await waitFor(() => expect(client.downloadScheduleTemplate).toHaveBeenCalledWith(season.id));
    expect(createObjectURL).toHaveBeenCalledOnce();
    expect(click).toHaveBeenCalledOnce();
    expect(await screen.findByText(/空白模板已开始下载/)).toBeTruthy();
  });

  it("offers a full-canvas focus mode and a collapsible inspector", async () => {
    renderWorkspace();
    await screen.findByRole("heading", { name: "赛程网格" });
    const workspace = document.querySelector(".schedule-planner-workspace") as HTMLElement;
    const main = document.querySelector(".planner-main") as HTMLElement;

    fireEvent.click(screen.getByRole("button", { name: "进入专注编排" }));
    expect(workspace.className).toContain("focus-mode");
    expect(document.body.style.overflow).toBe("hidden");

    fireEvent.click(screen.getByRole("button", { name: "收起待排/核对" }));
    expect(main.className).toContain("inspector-hidden");
    expect(screen.queryByRole("button", { name: /待排比赛/ })).toBeNull();
    expect(screen.getByRole("button", { name: "显示待排/核对" })).toBeTruthy();

    fireEvent.keyDown(window, { key: "Escape" });
    expect(workspace.className).not.toContain("focus-mode");
    expect(document.body.style.overflow).toBe("");
  });

  it("requires six successful checks and an explicit acknowledgement before confirm", async () => {
    const client = renderWorkspace();
    await screen.findByRole("heading", { name: "赛程网格" });
    fireEvent.click(screen.getByRole("button", { name: "保存并核对" }));

    expect(await screen.findByText("无缺漏或重复比赛")).toBeTruthy();
    expect(screen.getByText("比赛数量正确")).toBeTruthy();
    expect(screen.getByText("比赛容量正确")).toBeTruthy();
    expect(screen.getByText("日期与列头可识别")).toBeTruthy();
    expect(screen.getByText("场地与参赛方无冲突")).toBeTruthy();
    expect(screen.getByText("新增边界与并发安全")).toBeTruthy();

    const confirm = screen.getByRole("button", { name: "确认创建 1 场比赛" });
    expect((confirm as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByLabelText(/我已逐项核对以上结果/));
    expect((confirm as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(confirm);

    await waitFor(() => expect(client.confirmScheduleImport).toHaveBeenCalledOnce());
    expect(await screen.findByText("已创建正式赛程")).toBeTruthy();
  });
});
