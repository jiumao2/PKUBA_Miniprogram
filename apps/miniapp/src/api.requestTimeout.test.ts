import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.hoisted(() => {
  vi.stubGlobal("PKUBA_API_BASE_URL", "https://synthetic.invalid");
});
const taro = vi.hoisted(() => ({ request: vi.fn(), uploadFile: vi.fn() }));
vi.mock("@tarojs/taro", () => ({ default: taro }));

import { api } from "./api";

beforeEach(() => {
  vi.useFakeTimers();
  taro.request.mockReset();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("ordinary miniapp request timeout", () => {
  it("fails a hanging request after 30 seconds and allows a clean retry", async () => {
    taro.request.mockImplementationOnce((options) => {
      setTimeout(
        () => options.fail({ errMsg: "request:fail timeout" }),
        options.timeout,
      );
    });
    const first = api.getCurrentSeason();
    const firstOutcome = vi.fn();
    void first.catch(firstOutcome);

    await vi.advanceTimersByTimeAsync(29_999);
    expect(firstOutcome).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    await expect(first).rejects.toThrow("请求超时，请重试。");
    expect(taro.request.mock.calls[0][0].timeout).toBe(30_000);

    taro.request.mockImplementationOnce((options) => {
      options.success({ statusCode: 200, data: { id: "season-retry" } });
    });
    await expect(api.getCurrentSeason()).resolves.toEqual({ id: "season-retry" });
  });
});
