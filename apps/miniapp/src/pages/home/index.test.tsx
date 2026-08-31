// @vitest-environment jsdom
import React from "react";
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({
  getCurrentSeason: vi.fn(),
  getHomeDashboard: vi.fn(),
  switchTab: vi.fn(),
}));

vi.mock("@tarojs/taro", async () => {
  const ReactModule = await import("react");
  return {
    default: { switchTab: state.switchTab },
    useDidShow: (callback: () => void) => ReactModule.useEffect(callback, []),
  };
});

vi.mock("@tarojs/components", () => ({
  Button: ({ children, onClick, ...props }: any) => (
    <button onClick={onClick} {...props}>{children}</button>
  ),
  Image: ({ src, ...props }: any) => <img src={src} {...props} />,
  Text: ({ children, ...props }: any) => <span {...props}>{children}</span>,
  View: ({ children, ...props }: any) => <div {...props}>{children}</div>,
}));

vi.mock("../../api", () => ({
  api: {
    getCurrentSeason: state.getCurrentSeason,
    getHomeDashboard: state.getHomeDashboard,
  },
}));
vi.mock("../../components/game-timeline", () => ({ GameTimeline: () => null }));
vi.mock("../../navigation", () => ({ navigateToOnce: vi.fn() }));
vi.mock("../../tabbar", () => ({ syncTabBar: vi.fn() }));

import { ApiError } from "@pkuba/api-client";

import HomePage from "./index";

const season = {
  id: "season-published",
  name: "2026 北大杯",
  competition_type: "PKU_CUP",
  year: 2026,
  status: "PUBLISHED",
  starts_on: "2026-03-21",
  ends_on: "2026-05-31",
  version: 1,
  divisions: [],
};

const dashboard = {
  mode: "EMPTY",
  display_date: null,
  total_games: 0,
  games: [],
  calendar_start_date: null,
  calendar_end_date: null,
  daily_game_counts: [],
};

beforeEach(() => {
  vi.clearAllMocks();
  state.getHomeDashboard.mockResolvedValue(dashboard);
});
afterEach(cleanup);

describe("HomePage public-season states", () => {
  it("renders NO_PUBLIC_SEASON as a normal offseason state", async () => {
    state.getCurrentSeason.mockRejectedValue(
      new ApiError("当前处于休赛期，暂无公开赛季。", 404, "NO_PUBLIC_SEASON"),
    );

    render(<HomePage />);

    expect(await screen.findByText("当前处于休赛期")).toBeVisible();
    expect(screen.getByText("暂无公开赛季。")).toBeVisible();
    expect(screen.queryByText("暂时无法加载")).not.toBeInTheDocument();
    expect(screen.queryByText(/请稍后重试/)).not.toBeInTheDocument();
  });

  it("keeps a punctuated operational error readable", async () => {
    state.getCurrentSeason.mockRejectedValue(
      new ApiError("网关暂时繁忙。", 503, "UPSTREAM_UNAVAILABLE"),
    );

    render(<HomePage />);

    expect(await screen.findByText("暂时无法加载")).toBeVisible();
    expect(screen.getByText("网关暂时繁忙。请稍后重试。")).toBeVisible();
    expect(screen.queryByText(/。，/)).not.toBeInTheDocument();
  });

  it("keeps the successful dashboard visible", async () => {
    state.getCurrentSeason.mockResolvedValue(season);

    render(<HomePage />);

    expect(await screen.findByText("比赛日历")).toBeVisible();
    expect(screen.getByText("暂无近期比赛")).toBeVisible();
    expect(screen.queryByText("当前处于休赛期")).not.toBeInTheDocument();
    expect(screen.queryByText("暂时无法加载")).not.toBeInTheDocument();
  });
});
