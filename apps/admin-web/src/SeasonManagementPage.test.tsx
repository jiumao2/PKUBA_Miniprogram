import { cleanup, render, screen, waitFor } from "@testing-library/react";
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
  status: "ACTIVE",
  starts_on: "2026-03-21",
  ends_on: "2026-05-10",
  timezone: "Asia/Shanghai",
  version: 1,
  editable: false,
  locked_reason: "赛季公开后，组别、场地、时段与容量元信息即被锁定。",
  divisions: ["男甲", "男乙", "女甲", "女乙"].map((name, index) => ({
    id: `20000000-0000-0000-0000-00000000000${index + 1}`,
    code: `legacy-d${index + 1}`,
    name,
    gender: name.startsWith("男") ? "MEN" : "WOMEN",
    sort_order: index + 1,
    team_count: [12, 23, 8, 14][index],
    group_count: [2, 5, 1, 3][index],
    game_count: [30, 57, 16, 43][index],
  })),
  venues: ["五四东一", "五四东二", "五四东三"].map((name, index) => ({
    id: `30000000-0000-0000-0000-00000000000${index + 1}`,
    code: `legacy-v0${index + 1}`,
    name,
    sort_order: index + 1,
    active: true,
    game_count: 48,
    active_reservation_count: 0,
  })),
  periods: [
    {
      id: "40000000-0000-0000-0000-000000000001",
      code: "legacy-1250",
      name: "12:50",
      start_time: "12:50",
      sort_order: 1,
      capacities: [1, 1, 1, 1, 1, 3, 3],
      game_count: 70,
      active_reservation_count: 0,
    },
    {
      id: "40000000-0000-0000-0000-000000000002",
      code: "legacy-2040",
      name: "20:40",
      start_time: "20:40",
      sort_order: 2,
      capacities: [1, 1, 1, 1, 1, 0, 0],
      game_count: 20,
      active_reservation_count: 0,
    },
  ],
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
  divisions: historical.divisions.map(({ id, code, name, gender }) => ({ id, code, name, gender })),
}];

function clientWith(configuration: SeasonConfiguration, overrides = {}) {
  return {
    getSeasonConfiguration: vi.fn().mockResolvedValue(configuration),
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

    expect(await screen.findByText("基础配置已锁定")).toBeTruthy();
    expect(screen.getByRole("textbox", { name: "赛季名称" })).toHaveProperty("value", "北大杯");
    expect(screen.getByDisplayValue("男甲")).toBeTruthy();
    expect(screen.getByDisplayValue("女乙")).toBeTruthy();
    expect(screen.getByDisplayValue("五四东三")).toBeTruthy();
    expect(screen.getByLabelText("周六12:50容量").hasAttribute("disabled")).toBe(true);
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
    await user.click(screen.getByRole("button", { name: "保存全部配置" }));

    await waitFor(() => expect(updateSeasonConfiguration).toHaveBeenCalledOnce());
    const [, payload] = updateSeasonConfiguration.mock.calls[0];
    expect(payload.expected_version).toBe(1);
    expect(payload.divisions).toHaveLength(5);
    expect(payload.divisions[4]).toMatchObject({ id: null, name: "公开组" });
  });

  it("creates a setup season from the selected historical configuration", async () => {
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

    await screen.findByText("基础配置已锁定");
    await user.click(screen.getByRole("button", { name: "新建赛季" }));
    expect(screen.getByRole("combobox", { name: "配置来源" })).toHaveProperty("value", historical.id);
    await user.click(screen.getByRole("button", { name: "创建并开始配置" }));

    await waitFor(() => expect(createAdminSeason).toHaveBeenCalledOnce());
    expect(createAdminSeason.mock.calls[0][0]).toMatchObject({
      competition_type: "PKU_CUP",
      template_season_id: historical.id,
    });
    await waitFor(() => expect(onDataChanged).toHaveBeenCalledWith(created.id));
    expect(onSeasonChange).toHaveBeenCalledWith(created.id);
  });
});
