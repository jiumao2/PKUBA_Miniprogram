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
});
