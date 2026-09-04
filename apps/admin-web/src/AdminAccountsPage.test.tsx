import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
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
  {
    id: "admin-other",
    username: "court-admin",
    role: "ADMIN",
    is_active: true,
    version: 2,
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
    const dialog = screen.getByRole("alertdialog", { name: "降级 other-root？" });
    expect(dialog).toBeVisible();
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(client.demoteSuperadmin).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "取消" })).toHaveFocus();
    await user.click(screen.getByRole("button", { name: "确认操作" }));

    await waitFor(() => {
      expect(client.demoteSuperadmin).toHaveBeenCalledWith("super-other", 4);
    });
  });

  it("keeps a failed dangerous action inside its confirmation dialog", async () => {
    const user = userEvent.setup();
    const client = {
      listAdminAccounts: vi.fn().mockResolvedValue(accounts),
      getAdminRegistrationPolicy: vi.fn().mockResolvedValue({
        configured: false,
        initialized_at: null,
        updated_at: null,
        version: 0,
      }),
      demoteSuperadmin: vi.fn().mockRejectedValue(new Error("账号版本已变化，请刷新后重试。")),
    } as unknown as AdminClient;

    render(<AdminAccountsPage account={current} client={client} />);

    await user.click(await screen.findByRole("button", { name: "降级" }));
    await user.click(screen.getByRole("button", { name: "确认操作" }));

    const dialog = await screen.findByRole("alertdialog", { name: "降级 other-root？" });
    expect(within(dialog).getByRole("alert")).toHaveTextContent("账号版本已变化，请刷新后重试。");
    expect(dialog).toBeVisible();
  });

  it("keeps account writes behind visible dialogs with focus, Escape, and in-view feedback", async () => {
    const user = userEvent.setup();
    const client = {
      listAdminAccounts: vi.fn().mockResolvedValue(accounts),
      getAdminRegistrationPolicy: vi.fn().mockResolvedValue({
        configured: false,
        initialized_at: null,
        updated_at: null,
        version: 0,
      }),
      promoteAdmin: vi.fn().mockResolvedValue({}),
      renameAccount: vi.fn().mockResolvedValue({}),
      resetAdminPassword: vi.fn().mockResolvedValue({}),
    } as unknown as AdminClient;

    render(<AdminAccountsPage account={current} client={client} />);

    const identity = await screen.findByText("@court-admin");
    const row = identity.closest("[role='row']");
    expect(row).not.toBeNull();

    await user.click(within(row as HTMLElement).getByRole("button", { name: "升级" }));
    expect(client.promoteAdmin).not.toHaveBeenCalled();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("alertdialog", { name: "升级 court-admin？" })).not.toBeInTheDocument();
    expect(client.promoteAdmin).not.toHaveBeenCalled();

    await user.click(within(row as HTMLElement).getByRole("button", { name: "更正昵称" }));
    const username = screen.getByRole("textbox", { name: "新昵称" });
    expect(username).toHaveFocus();
    await user.clear(username);
    await user.type(username, "court-admin-updated");
    expect(client.renameAccount).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "确认更正" }));
    await waitFor(() => expect(client.renameAccount).toHaveBeenCalledWith(
      "admin-other",
      2,
      "court-admin-updated",
    ));
    expect(await screen.findByRole("status")).toHaveTextContent("账号昵称已更正");

    const refreshedRow = (await screen.findByText("@court-admin")).closest("[role='row']");
    await user.click(within(refreshedRow as HTMLElement).getByRole("button", { name: "重置密码" }));
    const passwordDialog = screen.getByRole("dialog", { name: "重置 court-admin 的密码" });
    const password = within(passwordDialog).getByLabelText("新密码");
    expect(password).toHaveFocus();
    await user.type(password, "new-pass");
    await user.type(within(passwordDialog).getByLabelText("再次输入"), "new-pass");
    expect(client.resetAdminPassword).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "确认重置" }));
    await waitFor(() => expect(client.resetAdminPassword).toHaveBeenCalledWith(
      "admin-other",
      2,
      "new-pass",
    ));
  });

  it("focuses the leader preview and confirms transfer and release in visible dialogs", async () => {
    const user = userEvent.setup();
    const binding = {
      id: "binding-1",
      season_id: "season-1",
      account_id: "admin-other",
      username: "court-admin",
      team_id: "team-1",
      team_name: "数学",
      active: true,
      released_at: null,
      released_by: null,
      release_reason: "",
      version: 3,
      created_at: "2026-03-01T00:00:00Z",
    };
    const leaderPreview = {
      season_id: "season-1",
      season_version: 7,
      changed: true,
      account_id: "admin-other",
      username: "court-admin",
      team_id: "team-1",
      team_name: "数学",
      release_bindings: [binding],
      impact_hash: "impact-1",
    };
    const client = {
      listAdminAccounts: vi.fn().mockResolvedValue(accounts),
      getAdminRegistrationPolicy: vi.fn().mockResolvedValue({
        configured: false,
        initialized_at: null,
        updated_at: null,
        version: 0,
      }),
      getRosterDataset: vi.fn().mockResolvedValue({
        divisions: [{ id: "division-1", name: "男甲" }],
        teams: [{ id: "team-1", division_id: "division-1", name: "数学", active: true }],
      }),
      listLeaderBindings: vi.fn().mockResolvedValue([binding]),
      previewLeaderTransfer: vi.fn().mockResolvedValue(leaderPreview),
      transferLeaderBinding: vi.fn().mockResolvedValue({}),
      releaseLeaderBinding: vi.fn().mockResolvedValue({}),
    } as unknown as AdminClient;
    const onDataChanged = vi.fn().mockResolvedValue(undefined);

    render(
      <AdminAccountsPage
        account={current}
        client={client}
        seasons={[{
          id: "season-1",
          name: "2026 北大杯",
          year: 2026,
          status: "PUBLISHED",
          version: 7,
        } as never]}
        seasonId="season-1"
        onDataChanged={onDataChanged}
      />,
    );

    await user.selectOptions(await screen.findByLabelText("目标账号"), "admin-other");
    await user.selectOptions(screen.getByLabelText("目标球队"), "team-1");
    await user.click(screen.getByRole("button", { name: "预览转移影响" }));

    const preview = await screen.findByText("将建立新绑定");
    expect(preview.parentElement).toHaveFocus();
    await user.click(screen.getByRole("button", { name: "确认原子转移" }));
    expect(screen.getByRole("alertdialog", { name: "确认原子转移领队绑定？" })).toBeVisible();
    expect(client.transferLeaderBinding).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "确认转移" }));
    await waitFor(() => expect(client.transferLeaderBinding).toHaveBeenCalledWith(
      "season-1",
      expect.objectContaining({
        account_id: "admin-other",
        team_id: "team-1",
        impact_hash: "impact-1",
        confirmed: true,
      }),
    ));

    const bindingRow = (await screen.findByText("现行绑定")).closest("[role='row']");
    await user.click(within(bindingRow as HTMLElement).getByRole("button", { name: "释放" }));
    expect(screen.getByRole("alertdialog", { name: "确认释放领队绑定？" })).toBeVisible();
    expect(client.releaseLeaderBinding).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "确认释放" }));
    await waitFor(() => expect(client.releaseLeaderBinding).toHaveBeenCalledWith(
      "binding-1",
      3,
      "超级管理员网页纠错",
    ));
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
