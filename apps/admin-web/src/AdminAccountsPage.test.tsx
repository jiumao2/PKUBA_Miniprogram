import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AdminAccount, AdminManagedAccount, createAdminClient } from "@pkuba/api-client";

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
      getAdminRegistrationPolicy: vi.fn().mockResolvedValue({
        configured: false,
        initialized_at: null,
        updated_at: null,
        version: 0,
      }),
      demoteSuperadmin: vi.fn().mockResolvedValue({
        ...accounts[1],
        role: "ADMIN",
        version: 5,
      }),
    } as unknown as AdminClient;

    render(
      <AdminAccountsPage account={current} client={client} />,
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

  it("reads the global invite policy when there is no season", async () => {
    const client = {
      listAdminAccounts: vi.fn().mockResolvedValue(accounts),
      getAdminRegistrationPolicy: vi.fn().mockResolvedValue({
        configured: false,
        initialized_at: null,
        updated_at: null,
        version: 0,
      }),
    } as unknown as AdminClient;

    render(<AdminAccountsPage account={current} client={client} />);

    expect(await screen.findByText(/尚未初始化/)).toBeVisible();
    expect(screen.getByText(/管理员注册不依赖赛季状态/)).toBeVisible();
    expect(screen.getByRole("button", { name: "更新邀请码" })).toBeDisabled();
    expect(client.getAdminRegistrationPolicy).toHaveBeenCalledTimes(1);
  });

  it("rotates the global invite without a season", async () => {
    const user = userEvent.setup();
    const client = {
      listAdminAccounts: vi.fn().mockResolvedValue(accounts),
      getAdminRegistrationPolicy: vi.fn().mockResolvedValue({
        configured: true,
        initialized_at: "2026-03-01T00:00:00Z",
        updated_at: null,
        version: 3,
      }),
      setAdminRegistrationPolicy: vi.fn().mockResolvedValue({
        configured: true,
        initialized_at: "2026-03-01T00:00:00Z",
        updated_at: "2026-03-02T00:00:00Z",
        version: 4,
      }),
    } as unknown as AdminClient;

    render(<AdminAccountsPage account={current} client={client} />);

    await screen.findByText(/管理员注册不依赖赛季状态/);
    const fields = screen.getAllByLabelText(/新邀请码|再次输入/);
    await user.type(fields[0], "GLOBAL-INVITE-2026");
    await user.type(fields[1], "GLOBAL-INVITE-2026");
    await user.click(screen.getByRole("button", { name: "更新邀请码" }));

    await waitFor(() => {
      expect(client.setAdminRegistrationPolicy).toHaveBeenCalledWith(
        "GLOBAL-INVITE-2026",
        3,
      );
    });
  });
});
