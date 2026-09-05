import { beforeEach, describe, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({ switchTab: vi.fn() }));

vi.mock("@tarojs/taro", () => ({
  default: {
    navigateTo: vi.fn(),
    switchTab: state.switchTab,
  },
}));

import {
  consumeScheduleFocusIntent,
  switchToScheduleDate,
} from "./navigation";

beforeEach(() => {
  state.switchTab.mockReset().mockResolvedValue({});
  consumeScheduleFocusIntent();
});

describe("schedule tab navigation intent", () => {
  it("is kept in memory and consumed exactly once", async () => {
    await switchToScheduleDate("2026-04-18");

    expect(state.switchTab).toHaveBeenCalledExactlyOnceWith({
      url: "/pages/schedule/index",
    });
    expect(consumeScheduleFocusIntent()).toMatchObject({ date: "2026-04-18" });
    expect(consumeScheduleFocusIntent()).toBeNull();
  });

  it("does not leave a stale intent when tab switching fails", async () => {
    state.switchTab.mockRejectedValueOnce(new Error("switch failed"));

    await expect(switchToScheduleDate("2026-04-19")).rejects.toThrow("switch failed");
    expect(consumeScheduleFocusIntent()).toBeNull();
  });
});
