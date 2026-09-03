// @vitest-environment jsdom
import { renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  showShareMenu: vi.fn().mockResolvedValue(undefined),
  useShareAppMessage: vi.fn(),
  useShareTimeline: vi.fn(),
}));

vi.mock("@tarojs/taro", () => ({
  default: { showShareMenu: mocks.showShareMenu },
  useShareAppMessage: mocks.useShareAppMessage,
  useShareTimeline: mocks.useShareTimeline,
}));

import { gameShareOptions, usePublicPageShare } from "./sharing";

describe("public share links", () => {
  it("keeps a precise game deep link without private context", () => {
    expect(gameShareOptions("game/id", "数学 34:25 外院")).toEqual({
      title: "数学 34:25 外院",
      path: "/pages/game-media/index?id=game%2Fid",
      query: "id=game%2Fid",
    });
  });

  it("registers friend and timeline sharing and exposes both menu entries", async () => {
    renderHook(() => usePublicPageShare({
      title: "PKUBA 赛事排名",
      path: "/pages/standings/index",
    }));

    expect(mocks.useShareAppMessage).toHaveBeenCalledOnce();
    expect(mocks.useShareAppMessage.mock.calls[0][0]()).toEqual({
      title: "PKUBA 赛事排名",
      path: "/pages/standings/index",
    });
    expect(mocks.useShareTimeline).toHaveBeenCalledOnce();
    expect(mocks.useShareTimeline.mock.calls[0][0]()).toEqual({
      title: "PKUBA 赛事排名",
      query: undefined,
    });
    await waitFor(() => expect(mocks.showShareMenu).toHaveBeenCalledWith({
      showShareItems: ["shareAppMessage", "shareTimeline"],
    }));
  });
});
