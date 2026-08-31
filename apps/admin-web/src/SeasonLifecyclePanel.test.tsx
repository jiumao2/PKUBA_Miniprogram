import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { SeasonConfiguration, createAdminClient } from "@pkuba/api-client";

import { SeasonLifecyclePanel } from "./SeasonLifecyclePanel";

type AdminClient = ReturnType<typeof createAdminClient>;

const configuration: SeasonConfiguration = {
  id: "10000000-0000-0000-0000-000000000001",
  name: "2027 北大杯",
  competition_type: "PKU_CUP",
  year: 2027,
  status: "SETUP",
  starts_on: "2027-03-01",
  ends_on: "2027-05-31",
  timezone: "Asia/Shanghai",
  version: 3,
  editable: true,
  maintenance_required: false,
  locked_reason: "",
  divisions: [
    {
      id: "20000000-0000-0000-0000-000000000001",
      code: "men-a",
      name: "男甲",
      gender: "MEN",
      sort_order: 1,
      version: 2,
      team_count: 12,
      group_count: 2,
      game_count: 30,
    },
  ],
  venues: [],
  periods: [],
  slot_families: [],
  date_capacity_overrides: [],
  over_capacity: [],
};

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("SeasonLifecyclePanel", () => {
  it("previews and confirms publishing the season", async () => {
    const previewSeasonLifecycle = vi.fn().mockResolvedValue({
      season_id: configuration.id,
      season_version: 3,
      before_season_status: "SETUP",
      after_season_status: "PUBLISHED",
      target_status: "PUBLISHED",
      blockers: [],
      references: {},
      changed: true,
      can_apply: true,
      impact_hash: "lifecycle-hash",
    });
    const applySeasonLifecycle = vi.fn().mockResolvedValue({});
    const onApplied = vi.fn().mockResolvedValue(undefined);
    const client = { previewSeasonLifecycle, applySeasonLifecycle } as unknown as AdminClient;
    vi.spyOn(window, "confirm").mockReturnValue(true);

    render(
      <SeasonLifecyclePanel
        client={client}
        configuration={configuration}
        dirty={false}
        onApplied={onApplied}
      />,
    );
    await userEvent.setup().click(screen.getByRole("button", { name: "公开赛季" }));

    await waitFor(() => expect(applySeasonLifecycle).toHaveBeenCalledOnce());
    expect(applySeasonLifecycle.mock.calls[0][1]).toMatchObject({
      expected_season_version: 3,
      target_status: "PUBLISHED",
      impact_hash: "lifecycle-hash",
    });
    expect(onApplied).toHaveBeenCalledOnce();
  });

  it("blocks lifecycle commands while configuration is dirty", () => {
    render(
      <SeasonLifecyclePanel
        client={{} as AdminClient}
        configuration={configuration}
        dirty
        onApplied={vi.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: "公开赛季" })).toBeDisabled();
  });
});
