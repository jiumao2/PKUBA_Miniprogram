// @vitest-environment jsdom
import React from "react";
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({
  consumeScheduleFocusIntent: vi.fn(),
}));

vi.mock("@tarojs/taro", async () => {
  const ReactModule = await import("react");
  return {
    useDidShow: (callback: () => void) => ReactModule.useEffect(callback, []),
  };
});
vi.mock("@tarojs/components", () => ({
  Text: ({ children, ...props }: any) => <span {...props}>{children}</span>,
  View: ({ children, ...props }: any) => <div {...props}>{children}</div>,
}));
vi.mock("../../api", () => ({ api: { getBrackets: vi.fn() } }));
vi.mock("../../components/bracket-view", () => ({ BracketView: () => null }));
vi.mock("../../components/schedule-day-scroller", () => ({
  ScheduleDayScroller: ({ focusIntent }: any) => (
    <div data-testid="schedule-focus">{focusIntent?.date ?? "today"}</div>
  ),
}));
vi.mock("../../navigation", () => ({
  consumeScheduleFocusIntent: state.consumeScheduleFocusIntent,
  navigateToOnce: vi.fn(),
}));
vi.mock("../../sharing", () => ({ usePublicPageShare: vi.fn() }));
vi.mock("../../tabbar", () => ({ syncTabBar: vi.fn() }));

import SchedulePage from "./index";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("SchedulePage calendar handoff", () => {
  it("consumes the home-calendar focus once when the tab becomes visible", async () => {
    state.consumeScheduleFocusIntent.mockReturnValue({ date: "2026-04-18", id: 9 });

    render(<SchedulePage />);

    expect(await screen.findByTestId("schedule-focus")).toHaveTextContent("2026-04-18");
    expect(state.consumeScheduleFocusIntent).toHaveBeenCalledTimes(1);
  });
});
