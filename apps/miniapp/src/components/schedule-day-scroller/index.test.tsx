// @vitest-environment jsdom
import React from "react";
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({ getScheduleDays: vi.fn() }));

vi.mock("@tarojs/components", () => ({
  Button: ({ children, onClick, ...props }: any) => (
    <button onClick={onClick} {...props}>{children}</button>
  ),
  ScrollView: ({
    children,
    scrollIntoView,
    scrollY: _scrollY,
    enhanced: _enhanced,
    showScrollbar: _showScrollbar,
    upperThreshold: _upperThreshold,
    lowerThreshold: _lowerThreshold,
    onScrollToUpper: _onScrollToUpper,
    onScrollToLower: _onScrollToLower,
    ...props
  }: any) => (
    <div data-scroll-target={scrollIntoView} {...props}>{children}</div>
  ),
  Text: ({ children, ...props }: any) => <span {...props}>{children}</span>,
  View: ({ children, ...props }: any) => <div {...props}>{children}</div>,
}));
vi.mock("../../api", () => ({ api: { getScheduleDays: state.getScheduleDays } }));
vi.mock("../game-timeline", () => ({ GameTimeline: () => <div>比赛列表</div> }));

import { ScheduleDayScroller } from "./index";
import { scheduleDayAnchor } from "./model";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("ScheduleDayScroller initial focus", () => {
  it("commits the today placeholder and anchor in the first render update", async () => {
    state.getScheduleDays.mockResolvedValue({
      today: "2026-09-03",
      focus_date: "2026-09-03",
      days: [
        { date: "2026-09-02", games: [{ id: "game-before" }] },
        { date: "2026-09-03", games: [] },
        { date: "2026-09-04", games: [{ id: "game-after" }] },
      ],
      previous_cursor: "2026-09-02",
      next_cursor: "2026-09-04",
      has_previous: false,
      has_next: false,
      total_games: 2,
    });

    render(<ScheduleDayScroller refreshKey={0} onGameClick={vi.fn()} />);

    expect(await screen.findByText("今日无比赛")).toBeVisible();
    await waitFor(() => expect(
      document.querySelector("[data-scroll-target]")?.getAttribute("data-scroll-target"),
    ).toBe(scheduleDayAnchor("2026-09-03")));

    const scroller = document.querySelector("[data-scroll-target]");
    fireEvent.scroll(scroller!);
    await waitFor(() => expect(scroller?.getAttribute("data-scroll-target")).toBe(""));
    fireEvent.click(screen.getByRole("button", { name: "回到今天" }));
    await waitFor(() => expect(scroller?.getAttribute("data-scroll-target")).toBe(
      scheduleDayAnchor("2026-09-03"),
    ));
  });

  it("requests and scrolls to the server-resolved day for a home-calendar intent", async () => {
    state.getScheduleDays.mockResolvedValue({
      today: "2026-09-03",
      focus_date: "2026-04-15",
      days: [{ date: "2026-04-15", games: [{ id: "game-target" }] }],
      previous_cursor: "2026-04-15",
      next_cursor: "2026-04-15",
      has_previous: true,
      has_next: true,
      total_games: 20,
    });

    render(
      <ScheduleDayScroller
        focusIntent={{ date: "2026-04-14", id: 1 }}
        refreshKey={1}
        onGameClick={vi.fn()}
      />,
    );

    await waitFor(() => expect(state.getScheduleDays).toHaveBeenCalledWith(
      expect.stringContaining("anchor_date=2026-04-14"),
    ));
    await waitFor(() => expect(
      document.querySelector("[data-scroll-target]")?.getAttribute("data-scroll-target"),
    ).toBe(scheduleDayAnchor("2026-04-15")));
  });

  it("reloads around today when today is outside the calendar-targeted window", async () => {
    state.getScheduleDays
      .mockResolvedValueOnce({
        today: "2026-09-03",
        focus_date: "2026-04-15",
        days: [{ date: "2026-04-15", games: [{ id: "game-target" }] }],
        previous_cursor: "2026-04-15",
        next_cursor: "2026-04-15",
        has_previous: true,
        has_next: true,
        total_games: 20,
      })
      .mockResolvedValueOnce({
        today: "2026-09-03",
        focus_date: "2026-09-03",
        days: [{ date: "2026-09-03", games: [] }],
        previous_cursor: null,
        next_cursor: null,
        has_previous: false,
        has_next: false,
        total_games: 20,
      });

    render(
      <ScheduleDayScroller
        focusIntent={{ date: "2026-04-14", id: 2 }}
        refreshKey={1}
        onGameClick={vi.fn()}
      />,
    );

    await screen.findByRole("button", { name: "回到今天" });
    fireEvent.click(screen.getByRole("button", { name: "回到今天" }));

    await waitFor(() => expect(state.getScheduleDays).toHaveBeenCalledTimes(2));
    expect(state.getScheduleDays.mock.calls[1][0]).not.toContain("anchor_date");
    await waitFor(() => expect(
      document.querySelector("[data-scroll-target]")?.getAttribute("data-scroll-target"),
    ).toBe(scheduleDayAnchor("2026-09-03")));
  });

  it("isolates a late response after a newer calendar focus arrives", async () => {
    let resolveFirst!: (value: unknown) => void;
    let resolveSecond!: (value: unknown) => void;
    state.getScheduleDays
      .mockImplementationOnce(() => new Promise((resolve) => { resolveFirst = resolve; }))
      .mockImplementationOnce(() => new Promise((resolve) => { resolveSecond = resolve; }));

    const view = render(
      <ScheduleDayScroller
        focusIntent={{ date: "2026-04-10", id: 3 }}
        refreshKey={1}
        onGameClick={vi.fn()}
      />,
    );
    await waitFor(() => expect(state.getScheduleDays).toHaveBeenCalledTimes(1));
    view.rerender(
      <ScheduleDayScroller
        focusIntent={{ date: "2026-04-20", id: 4 }}
        refreshKey={2}
        onGameClick={vi.fn()}
      />,
    );
    await waitFor(() => expect(state.getScheduleDays).toHaveBeenCalledTimes(2));

    resolveSecond({
      today: "2026-09-03",
      focus_date: "2026-04-20",
      days: [{ date: "2026-04-20", games: [{ id: "new-game" }] }],
      previous_cursor: null,
      next_cursor: null,
      has_previous: false,
      has_next: false,
      total_games: 1,
    });
    await screen.findByText("4月20日 周一");

    resolveFirst({
      today: "2026-09-03",
      focus_date: "2026-04-10",
      days: [{ date: "2026-04-10", games: [{ id: "old-game" }] }],
      previous_cursor: null,
      next_cursor: null,
      has_previous: false,
      has_next: false,
      total_games: 1,
    });
    await waitFor(() => expect(screen.queryByText("4月10日 周五")).not.toBeInTheDocument());
    expect(document.querySelector("[data-scroll-target]")?.getAttribute("data-scroll-target"))
      .toBe(scheduleDayAnchor("2026-04-20"));
  });

  it("retries the same calendar anchor after an initial request failure", async () => {
    state.getScheduleDays
      .mockRejectedValueOnce(new Error("网络暂时不可用"))
      .mockResolvedValueOnce({
        today: "2026-09-03",
        focus_date: "2026-04-21",
        days: [{ date: "2026-04-21", games: [{ id: "retry-game" }] }],
        previous_cursor: null,
        next_cursor: null,
        has_previous: false,
        has_next: false,
        total_games: 1,
      });

    render(
      <ScheduleDayScroller
        focusIntent={{ date: "2026-04-19", id: 5 }}
        refreshKey={1}
        onGameClick={vi.fn()}
      />,
    );

    await screen.findByText("网络暂时不可用");
    fireEvent.click(screen.getByRole("button", { name: "重新加载" }));
    await waitFor(() => expect(state.getScheduleDays).toHaveBeenCalledTimes(2));
    expect(state.getScheduleDays.mock.calls.map(([query]) => query)).toEqual([
      expect.stringContaining("anchor_date=2026-04-19"),
      expect.stringContaining("anchor_date=2026-04-19"),
    ]);
    expect(await screen.findByText("4月21日 周二")).toBeVisible();
  });
});
