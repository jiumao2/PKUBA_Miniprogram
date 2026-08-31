import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AdminAccount, AdminManagedAccount, createAdminClient } from "@pkuba/api-client";
import type { AdminSeason } from "@pkuba/api-client";

import { AdminAccountsPage } from "./AdminAccountsPage";

type AdminClient = ReturnType<typeof createAdminClient>;

const current = {
  id: "super-current",
  username: "current-root",
  role: "SUPERADMIN",
  version: 1,
} as AdminAccount;

const accounts = [
  { ...current, is_active: true },
  {
    id: "super-other",
    username: "other-root",
    role: "SUPERADMIN",
    is_active: true,
    version: 4,
  },
] as AdminManagedAccount[];

afterEach(cleanup);

describe("AdminAccountsPage", () => {
  it("offers demotion only for another superadmin and requires confirmation", async () => {
    const user = userEvent.setup();
    const client = {
      listAdminAccounts: vi.fn().mockResolvedValue(accounts),
      demoteSuperadmin: vi.fn().mockResolvedValue({
        ...accounts[1],
        role: "ADMIN",
        version: 5,
      }),
    } as unknown as AdminClient;

    render(
      <AdminAccountsPage
        account={current}
        client={client}
        season={null}
      />,
    );

    const demote = await screen.findByRole("button", { name: "降级" });
    expect(screen.getAllByText(/当前账号/)).toHaveLength(1);
    await user.click(demote);
    expect(screen.getByRole("heading", { name: "降级 other-root？" })).toBeVisible();
    await user.click(screen.getByRole("button", { name: "确认操作" }));

    await waitFor(() => {
      expect(client.demoteSuperadmin).toHaveBeenCalledWith("super-other", 4);
    });
  });

  it("disables invite rotation when there is no published season", async () => {
    const client = {
      listAdminAccounts: vi.fn().mockResolvedValue(accounts),
      getSeasonInvite: vi.fn(),
    } as unknown as AdminClient;

    render(<AdminAccountsPage account={current} client={client} season={null} />);

    expect(await screen.findByText("管理员邀请码暂不可用")).toBeVisible();
    expect(screen.getByText(/当前没有已公开赛季/)).toBeVisible();
    expect(screen.getByRole("button", { name: "更新邀请码" })).toBeDisabled();
    expect(client.getSeasonInvite).not.toHaveBeenCalled();
  });

  it("shows the full published-season identity for invite rotation", async () => {
    const published = {
      id: "season-published",
      year: 2026,
      name: "北大杯",
      status: "PUBLISHED",
      competition_type: "PKU_CUP",
      starts_on: "2026-03-01",
      ends_on: "2026-05-31",
      version: 3,
      divisions: [],
    } as AdminSeason;
    const client = {
      listAdminAccounts: vi.fn().mockResolvedValue(accounts),
      getSeasonInvite: vi.fn().mockResolvedValue({
        season_id: published.id,
        configured: true,
        uses_default_invite: false,
        updated_at: null,
        version: 3,
      }),
    } as unknown as AdminClient;

    render(<AdminAccountsPage account={current} client={client} season={published} />);

    expect(await screen.findByText(/2026 · 北大杯 · 已公开/)).toBeVisible();
    expect(client.getSeasonInvite).toHaveBeenCalledWith(published.id);
  });
});
