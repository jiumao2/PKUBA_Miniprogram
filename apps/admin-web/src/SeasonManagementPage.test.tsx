import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type {
  AdminSeason,
  SeasonConfiguration,
  createAdminClient,
} from "@pkuba/api-client";
import { SeasonManagementPage } from "./SeasonManagementPage";

type AdminClient = ReturnType<typeof createAdminClient>;

const historical: SeasonConfiguration = {
  id: "10000000-0000-0000-0000-000000000001",
  name: "北大杯",
  competition_type: "PKU_CUP",
  year: 2026,
  status: "PUBLISHED",
  starts_on: "2026-03-21",
  ends_on: "2026-05-10",
  timezone: "Asia/Shanghai",
  version: 1,
  editable: false,
  maintenance_required: false,
  locked_reason: "赛季公开后，组别、场地、时段与容量元信息即被锁定。",
  divisions: ["男甲", "男乙", "女甲", "女乙"].map((name, index) => ({
    id: `20000000-0000-0000-0000-00000000000${index + 1}`,
    code: `legacy-d${index + 1}`,
    name,
    gender: name.startsWith("男") ? "MEN" : "WOMEN",
    sort_order: index + 1,
    version: 1,
    team_count: [12, 23, 8, 14][index],
    group_count: [2, 5, 1, 3][index],
    game_count: [30, 57, 16, 43][index],
  })),
  venues: ["五四东一", "五四东二", "五四东三"].map((name, index) => ({
    id: `30000000-0000-0000-0000-00000000000${index + 1}`,
    name,
    sort_order: index + 1,
    active: true,
    game_count: 48,
  })),
  periods: [
    {
      id: "40000000-0000-0000-0000-000000000001",
      code: "legacy-1250",
      name: "12:50",
      start_time: "12:50",
      sort_order: 1,
      default_capacities: { WEEKDAY: 1, WEEKEND: 3 },
      game_count: 70,
      active_reservation_count: 0,
    },
    {
      id: "40000000-0000-0000-0000-000000000002",
      code: "legacy-2040",
      name: "20:40",
      start_time: "20:40",
      sort_order: 2,
      default_capacities: { WEEKDAY: 1, WEEKEND: 0 },
      game_count: 20,
      active_reservation_count: 0,
    },
  ],
  slot_families: [
    {
      id: "50000000-0000-0000-0000-000000000001",
      division_id: "20000000-0000-0000-0000-000000000001",
      division_code: "legacy-d1",
      division_name: "男甲",
      gender: "MEN",
      stage: "GROUP",
      stage_name: "小组赛",
      round_number: 1,
      prefix: "A",
      slot_count: 12,
      sort_order: 1,
      expected_game_count: 66,
    },
  ],
  grid_columns: [
    {
      id: "60000000-0000-0000-0000-000000000001",
      period_id: "40000000-0000-0000-0000-000000000001",
      period_code: "P1",
      period_name: "12:50",
      start_time: "12:50",
      venue_id: "30000000-0000-0000-0000-000000000001",
      venue_name: "五四东一",
      final_only: false,
      sort_order: 1,
    },
  ],
  date_capacity_overrides: [],
  over_capacity: [],
};

const seasons: AdminSeason[] = [{
  id: historical.id,
  name: historical.name,
  competition_type: historical.competition_type,
  year: historical.year,
  status: historical.status,
  starts_on: historical.starts_on,
  ends_on: historical.ends_on,
  version: historical.version,
  divisions: historical.divisions.map(
    ({ id, code, name, gender, version }) => ({
      id,
      code,
      name,
      gender,
      version,
    }),
  ),
}];

function clientWith(configuration: SeasonConfiguration, overrides = {}) {
  return {
    getSeasonConfiguration: vi.fn().mockResolvedValue(configuration),
    getCapacityLedger: vi.fn().mockResolvedValue([]),
    previewSeasonConfiguration: vi.fn().mockResolvedValue({
      season_id: configuration.id,
      season_version: configuration.version,
      maintenance_required: false,
      changed: true,
      over_capacity: [],
      affected_reschedule_request_ids: [],
      templates_invalidated: false,
      impact_hash: "preview-hash",
    }),
    updateSeasonConfiguration: vi.fn().mockResolvedValue(configuration),
    createAdminSeason: vi.fn(),
    ...overrides,
  } as unknown as AdminClient;
}

function renderPage(client: AdminClient) {
  render(
    <SeasonManagementPage
      client={client}
      seasons={seasons}
      seasonId={historical.id}
      onSeasonChange={vi.fn()}
      onDataChanged={vi.fn().mockResolvedValue(undefined)}
    />,
  );
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("SeasonManagementPage", () => {
  it("renders historical divisions and capacity as locked real data", async () => {
    renderPage(clientWith(historical));

    expect(await screen.findByText("只读")).toBeTruthy();
    expect(screen.getByRole("textbox", { name: "赛季名称" })).toHaveProperty("value", "北大杯");
    expect(screen.getByDisplayValue("男甲")).toBeTruthy();
    expect(screen.getByDisplayValue("女乙")).toBeTruthy();
    expect(screen.getByDisplayValue("五四东三")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "＋ 添加场地" })).toBeNull();
    expect(screen.getByLabelText("12:50周末容量").hasAttribute("disabled")).toBe(true);
  });

  it("adds a division and sends one versioned configuration transaction", async () => {
    const editable = { ...historical, status: "SETUP", editable: true, locked_reason: "" };
    const updateSeasonConfiguration = vi.fn().mockResolvedValue(editable);
    const client = clientWith(editable, { updateSeasonConfiguration });
    const user = userEvent.setup();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderPage(client);

    await screen.findByText("赛事组别");
    await user.click(screen.getByRole("button", { name: "＋ 添加组别" }));
    const nameInput = screen.getByDisplayValue("新组别");
    await user.clear(nameInput);
    await user.type(nameInput, "公开组");
    await user.click(screen.getByRole("button", { name: "预览并保存" }));

    await waitFor(() => expect(updateSeasonConfiguration).toHaveBeenCalledOnce());
    const [, payload] = updateSeasonConfiguration.mock.calls[0];
    expect(payload.expected_version).toBe(1);
    expect(payload.divisions).toHaveLength(5);
    expect(payload.divisions[4]).toMatchObject({ id: null, name: "公开组" });
  });

  it("uses list position for all four ordered resources and hides internal codes", async () => {
    const editable = { ...historical, status: "SETUP", editable: true, locked_reason: "" };
    const updateSeasonConfiguration = vi.fn().mockResolvedValue(editable);
    const client = clientWith(editable, { updateSeasonConfiguration });
    const user = userEvent.setup();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const { container } = render(
      <SeasonManagementPage
        client={client}
        seasons={seasons}
        seasonId={historical.id}
        onSeasonChange={vi.fn()}
        onDataChanged={vi.fn().mockResolvedValue(undefined)}
      />,
    );

    await screen.findByText("赛事组别");
    const handles = container.querySelectorAll('.row-drag-handle[draggable="true"]');
    expect(handles).toHaveLength(10);
    expect(container.querySelectorAll('.division-config-row[draggable="true"]')).toHaveLength(0);
    expect(screen.queryByLabelText("男甲代码")).toBeNull();
    expect(screen.queryByLabelText("12:50代码")).toBeNull();
    expect(screen.queryAllByText("顺序")).toHaveLength(0);
    expect(screen.queryByRole("button", { name: "上移女乙" })).toBeNull();
    expect(screen.queryByRole("button", { name: "下移女乙" })).toBeNull();

    const dataTransfer = { effectAllowed: "none", setData: vi.fn() };
    fireEvent.dragStart(screen.getByRole("button", { name: "拖动女乙排序" }), {
      dataTransfer,
    });
    const targetRow = screen.getByDisplayValue("女甲").closest(".division-config-row");
    expect(targetRow).not.toBeNull();
    fireEvent.dragOver(targetRow as Element, { dataTransfer });
    fireEvent.drop(targetRow as Element, { dataTransfer });
    await user.click(screen.getByRole("button", { name: "预览并保存" }));

    await waitFor(() => expect(updateSeasonConfiguration).toHaveBeenCalledOnce());
    const [, payload] = updateSeasonConfiguration.mock.calls[0];
    expect(payload.divisions.map((row: { name: string }) => row.name)).toEqual([
      "男甲",
      "男乙",
      "女乙",
      "女甲",
    ]);
    expect(payload.divisions.every((row: object) => !("sort_order" in row))).toBe(true);
    expect(payload.divisions.every((row: object) => !("code" in row))).toBe(true);
  });

  it("edits slot families while grid-column settings stay out of this page", async () => {
    const editable = { ...historical, status: "SETUP", editable: true, locked_reason: "" };
    const updateSeasonConfiguration = vi.fn().mockResolvedValue(editable);
    const client = clientWith(editable, { updateSeasonConfiguration });
    const user = userEvent.setup();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderPage(client);

    await screen.findByRole("heading", { name: "签位方案" });
    expect(screen.queryByText("赛程网格列")).toBeNull();
    await user.click(screen.getByRole("button", { name: "＋ 添加签位方案" }));
    expect(screen.getByDisplayValue("B")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "预览并保存" }));

    await waitFor(() => expect(updateSeasonConfiguration).toHaveBeenCalledOnce());
    const [, payload] = updateSeasonConfiguration.mock.calls[0];
    expect(payload.slot_families).toHaveLength(2);
    expect(payload.slot_families[1]).toMatchObject({ prefix: "B", stage: "GROUP" });
    expect(payload.grid_columns).toHaveLength(0);
  });

  it("lets a superadmin extend the ordered automatic-allocation venue pool", async () => {
    const editable = { ...historical, status: "SETUP", editable: true, locked_reason: "" };
    const updateSeasonConfiguration = vi.fn().mockResolvedValue(editable);
    const client = clientWith(editable, { updateSeasonConfiguration });
    const user = userEvent.setup();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderPage(client);

    await screen.findByRole("heading", { name: "标准场地" });
    await user.click(screen.getByRole("button", { name: "＋ 添加场地" }));
    const nameInput = screen.getByDisplayValue("新场地 4");
    await user.clear(nameInput);
    await user.type(nameInput, "新体育馆");
    expect(screen.getByLabelText("新体育馆名称")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "预览并保存" }));

    await waitFor(() => expect(updateSeasonConfiguration).toHaveBeenCalledOnce());
    const [, payload] = updateSeasonConfiguration.mock.calls[0];
    expect(payload.venues).toHaveLength(4);
    expect(payload.venues[3]).toMatchObject({
      id: null,
      name: "新体育馆",
      active: true,
    });
    expect(payload.venues.every((row: object) => !("sort_order" in row))).toBe(true);
  });

  it("creates a setup season from system defaults unless history is explicitly selected", async () => {
    const created = { ...historical, id: "10000000-0000-0000-0000-000000000002", status: "SETUP", editable: true, locked_reason: "" };
    const createAdminSeason = vi.fn().mockResolvedValue(created);
    const onDataChanged = vi.fn().mockResolvedValue(undefined);
    const onSeasonChange = vi.fn();
    const user = userEvent.setup();
    render(
      <SeasonManagementPage
        client={clientWith(historical, { createAdminSeason })}
        seasons={seasons}
        seasonId={historical.id}
        onSeasonChange={onSeasonChange}
        onDataChanged={onDataChanged}
      />,
    );

    await screen.findByText("只读");
    await user.click(screen.getByRole("button", { name: "新建赛季" }));
    expect(screen.getByLabelText("配置来源")).toHaveProperty("value", "");
    await user.click(screen.getByRole("button", { name: "创建赛季" }));

    await waitFor(() => expect(createAdminSeason).toHaveBeenCalledOnce());
    expect(createAdminSeason.mock.calls[0][0]).toMatchObject({
      competition_type: "PKU_CUP",
      template_season_id: null,
    });
    await waitFor(() => expect(onDataChanged).toHaveBeenCalledWith(created.id));
    expect(onSeasonChange).toHaveBeenCalledWith(created.id);
  });
});
