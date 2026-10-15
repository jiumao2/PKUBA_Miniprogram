// @vitest-environment jsdom
import React from "react";
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({
  getCurrentSeason: vi.fn(),
  getHomeDashboard: vi.fn(),
  switchTab: vi.fn(),
  switchToScheduleDate: vi.fn(),
  setTabBarHidden: vi.fn(),
}));

vi.mock("@tarojs/taro", async () => {
  const ReactModule = await import("react");
  return {
    default: { switchTab: state.switchTab, showShareMenu: vi.fn().mockResolvedValue({}) },
    useDidShow: (callback: () => void) => ReactModule.useEffect(callback, []),
    useShareAppMessage: vi.fn(),
    useShareTimeline: vi.fn(),
  };
});

vi.mock("@tarojs/components", () => ({
  Button: ({ children, onClick, ...props }: any) => (
    <button onClick={onClick} {...props}>{children}</button>
  ),
  Image: ({ src, ...props }: any) => <img src={src} {...props} />,
  Picker: ({ children, onCancel, onChange }: any) => (
    <div data-testid="calendar-range-picker">
      {children}
      <button
        data-testid="calendar-range-select"
        onClick={() => onChange({ detail: { value: 1 } })}
      >
        选择三月
      </button>
      <button data-testid="calendar-range-cancel" onClick={() => onCancel({})}>
        取消选择
      </button>
    </div>
  ),
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
vi.mock("../../navigation", () => ({
  navigateToOnce: vi.fn(),
  switchToScheduleDate: state.switchToScheduleDate,
}));
vi.mock("../../tabbar", () => ({
  setTabBarHidden: state.setTabBarHidden,
  syncTabBar: vi.fn(),
}));

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
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-04-01T04:00:00Z"));
  vi.clearAllMocks();
  state.getHomeDashboard.mockResolvedValue(dashboard);
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

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

  it.each([
    { days: [] },
    { days: [{ date: "2026-03-28", game_count: 0 }] },
  ])("hides the calendar without positive counts ($days) but keeps the empty-state entry", async ({ days }) => {
    state.getCurrentSeason.mockResolvedValue(season);
    state.getHomeDashboard.mockResolvedValue({ ...dashboard, daily_game_counts: days });

    render(<HomePage />);

    expect(await screen.findByText("暂无近期比赛")).toBeVisible();
    expect(screen.queryByText("比赛日历")).not.toBeInTheDocument();
    expect(screen.queryByTestId("calendar-range-picker")).not.toBeInTheDocument();
    fireEvent.click(screen.getByText("查看完整赛程"));
    expect(state.switchTab).toHaveBeenCalledWith({ url: "/pages/schedule/index" });
    expect(screen.queryByText("当前处于休赛期")).not.toBeInTheDocument();
    expect(screen.queryByText("暂时无法加载")).not.toBeInTheDocument();
  });

  it.each([
    ["2026-03-15", "RECENT_RESULTS"],
    ["2026-06-01", "NEXT_DAY"],
  ])("shows the calendar with only games on %s, even outside season bounds", async (date, mode) => {
    state.getCurrentSeason.mockResolvedValue(season);
    state.getHomeDashboard.mockResolvedValue({
      ...dashboard,
      mode,
      display_date: date,
      total_games: 1,
      daily_game_counts: [{ date, game_count: 1 }],
    });

    render(<HomePage />);

    await screen.findByText("比赛日历");
    expect(screen.getByLabelText(/4月1日.*0 场比赛.*今天/)).toBeVisible();
    fireEvent.click(screen.getByLabelText(/，1 场比赛/));
    expect(state.switchToScheduleDate).toHaveBeenCalledExactlyOnceWith(date);
  });

  it("defaults to game days plus today, then selects a month and sends a clicked day as a one-shot schedule focus", async () => {
    state.getCurrentSeason.mockResolvedValue(season);
    state.getHomeDashboard.mockResolvedValue({
      ...dashboard,
      mode: "NEXT_DAY",
      display_date: "2026-04-02",
      total_games: 1,
      daily_game_counts: [
        { date: "2026-03-28", game_count: 2 },
        { date: "2026-04-02", game_count: 1 },
      ],
    });

    render(<HomePage />);

    await screen.findByText("比赛日历");
    expect(screen.getByText("全部")).toBeVisible();
    expect(screen.getByText("3/23—4/5 · 3 场")).toBeVisible();
    expect(screen.getByLabelText(/4月1日.*今天/)).toBeVisible();
    fireEvent.click(screen.getByLabelText(/4月5日.*0 场比赛/));
    expect(state.switchToScheduleDate).toHaveBeenCalledExactlyOnceWith("2026-04-05");
    state.switchToScheduleDate.mockClear();

    state.setTabBarHidden.mockClear();
    fireEvent.click(screen.getByLabelText("选择比赛日历范围，当前全部"));
    expect(state.setTabBarHidden).toHaveBeenLastCalledWith(true);
    fireEvent.click(screen.getByTestId("calendar-range-cancel"));
    expect(state.setTabBarHidden).toHaveBeenLastCalledWith(false);
    expect(screen.getByText("全部")).toBeVisible();

    fireEvent.click(screen.getByLabelText("选择比赛日历范围，当前全部"));
    expect(state.setTabBarHidden).toHaveBeenLastCalledWith(true);
    fireEvent.click(screen.getByTestId("calendar-range-select"));
    expect(state.setTabBarHidden).toHaveBeenLastCalledWith(false);
    expect(screen.getByText("2026年3月")).toBeVisible();

    fireEvent.click(screen.getByLabelText(/3月21日 周六，0 场比赛/));
    expect(state.switchToScheduleDate).toHaveBeenCalledExactlyOnceWith("2026-03-21");
  });
});
