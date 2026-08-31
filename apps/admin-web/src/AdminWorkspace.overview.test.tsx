import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const clients = vi.hoisted(() => ({
  public: {
    getCurrentSeason: vi.fn(),
    getGames: vi.fn(),
  },
  admin: {
    getSession: vi.fn(),
    getCapacityLedger: vi.fn(),
    listAdminSeasons: vi.fn(),
    listAdminScheduleGames: vi.fn(),
  },
}));

vi.mock("@pkuba/api-client", async () => {
  const actual = await vi.importActual<typeof import("@pkuba/api-client")>(
    "@pkuba/api-client",
  );
  return {
    ...actual,
    createPkubaClient: () => clients.public,
    createAdminClient: () => clients.admin,
  };
});

import { ApiError } from "@pkuba/api-client";

import { AdminWorkspace } from "./AdminWorkspace";

const setupSeason = {
  id: "season-setup",
  name: "2027 北大杯",
  competition_type: "PKU_CUP",
  year: 2027,
  status: "SETUP",
  starts_on: "2027-03-20",
  ends_on: "2027-05-30",
  version: 1,
  divisions: [],
};

const publishedSeason = {
  ...setupSeason,
  id: "season-published",
  name: "2026 北大杯",
  year: 2026,
  status: "PUBLISHED",
};

function account(role: "ADMIN" | "SUPERADMIN") {
  return {
    id: `${role.toLowerCase()}-account`,
    username: role === "SUPERADMIN" ? "super-admin" : "regular-admin",
    role,
    version: 1,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  window.history.replaceState(null, "", "/");
  clients.admin.getCapacityLedger.mockResolvedValue([]);
  clients.admin.listAdminScheduleGames.mockResolvedValue([]);
});

describe("AdminWorkspace overview without a published season", () => {
  it("guides a super administrator to configure the setup season", async () => {
    clients.admin.getSession.mockResolvedValue({ account: account("SUPERADMIN") });
    clients.admin.listAdminSeasons.mockResolvedValue([setupSeason]);
    clients.public.getCurrentSeason.mockRejectedValue(
      new ApiError("暂无已公开赛季", 404, "NO_PUBLIC_SEASON"),
    );

    render(<AdminWorkspace />);

    expect(await screen.findByRole("heading", { name: "暂无已公开赛季" })).toBeVisible();
    expect(screen.getByText(/请前往左侧“赛季与组别”完成赛季配置并公开/)).toBeVisible();
    expect(screen.getByText(/准备中的赛季不会显示为赛事总览/)).toBeVisible();
  });

  it("does not imply that a regular administrator may configure the season", async () => {
    clients.admin.getSession.mockResolvedValue({ account: account("ADMIN") });
    clients.admin.listAdminSeasons.mockResolvedValue([setupSeason]);
    clients.public.getCurrentSeason.mockRejectedValue(
      new ApiError("暂无已公开赛季", 404, "NO_PUBLIC_SEASON"),
    );

    render(<AdminWorkspace />);

    expect(await screen.findByRole("heading", { name: "暂无已公开赛季" })).toBeVisible();
    expect(screen.getByText(/请联系超级管理员/)).toBeVisible();
    expect(screen.queryByText(/请前往左侧“赛季与组别”/)).not.toBeInTheDocument();
  });

  it("keeps the published-season overview unchanged", async () => {
    clients.admin.getSession.mockResolvedValue({ account: account("SUPERADMIN") });
    clients.admin.listAdminSeasons.mockResolvedValue([publishedSeason]);
    clients.public.getCurrentSeason.mockResolvedValue(publishedSeason);
    clients.public.getGames.mockResolvedValue([]);

    render(<AdminWorkspace />);

    expect(await screen.findByText("赛季状态")).toBeVisible();
    expect(screen.getByText("已公开")).toBeVisible();
    expect(screen.queryByRole("heading", { name: "暂无已公开赛季" })).not.toBeInTheDocument();
  });
});
