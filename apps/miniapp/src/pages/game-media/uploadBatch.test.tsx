// @vitest-environment jsdom
import React, { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { GameMediaCollection, PublicGameDetail } from "@pkuba/api-client";
vi.hoisted(() => { vi.stubGlobal("PKUBA_API_BASE_URL", "https://synthetic.invalid"); });

const f = vi.hoisted(() => ({
  upload: vi.fn(), media: vi.fn(), sequence: 0, session: "",
  outcomes: [] as Array<"success" | "refused" | "unknown">,
  detail: null as unknown as PublicGameDetail,
  collection: null as unknown as GameMediaCollection,
}));
vi.mock("@tarojs/components", () => ({
  View: ({ children, className }: any) => <div className={className}>{children}</div>,
  Text: ({ children }: any) => <span>{children}</span>, Image: () => <span />,
  Button: ({ children, className, disabled, onClick }: any) =>
    <button className={className} disabled={disabled} onClick={onClick}>{children}</button>,
}));
vi.mock("@tarojs/taro", async () => {
  const react = await import("react");
  return {
    default: {
      uploadFile: f.upload, request: vi.fn(() => { throw new Error("LIVE_NETWORK_FORBIDDEN"); }),
      showModal: vi.fn(async () => ({ confirm: true })), showToast: vi.fn(), previewImage: vi.fn(),
      chooseMedia: vi.fn(async () => ({ tempFiles: ["first", "second"].map(name => ({ tempFilePath: `/synthetic/${name}.png` })) })),
    },
    useRouter: () => ({ params: { id: "synthetic-game" } }),
    useDidShow: (callback: () => void) => react.useEffect(() => { callback(); }, []),
  };
});
vi.mock("../../auth", () => ({
  getMiniAppSession: () => f.session,
  resolveMiniAppIdentity: async () => ({ token: f.session, me: { admin_role: "SUPERADMIN", leader_binding: null } }),
}));
vi.mock("../../api", async importOriginal => {
  const actual = await importOriginal<typeof import("../../api")>();
  return { ...actual, api: { getGameDetail: async () => structuredClone(f.detail), getGameMedia: f.media } };
});
import Page from "./index";

let root: Root;
let container: HTMLDivElement;
async function flush() {
  await act(async () => { for (let index = 0; index < 16; index++) await Promise.resolve(); });
}
async function click(button: HTMLButtonElement) {
  await act(async () => button.click());
  await flush();
}
beforeEach(() => {
  (globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;
  f.session = `synthetic-batch-${++f.sequence}`;
  f.detail = {
    game: { id: "synthetic-game", date: "2026-09-01", start_time: "18:00", division_name: "合成组", home_name: "甲", away_name: "乙", home_score: null, away_score: null, venue_name: "合成场地" },
    group_photos: [], stats: null,
  } as unknown as PublicGameDetail;
  f.collection = { assets: [], can_upload: true } as unknown as GameMediaCollection;
  f.media.mockReset().mockImplementation(async () => structuredClone(f.collection));
  f.upload.mockReset().mockImplementation((options: any) => {
    const outcome = f.outcomes.shift() ?? "success";
    const asset = { id: options.filePath, version: 1, kind: "GAME_PHOTO", storage_status: "ONLINE", content_url: "/synthetic.png", can_replace: true, can_delete: true };
    if (outcome !== "refused") f.collection.assets.push(asset as any);
    options.success(outcome === "refused"
      ? { statusCode: 400, data: JSON.stringify({ message: "图片内容不合法", code: "INVALID_IMAGE" }) }
      : { statusCode: 201, data: outcome === "unknown" ? "{" : JSON.stringify(asset) });
    return { onProgressUpdate() {} };
  });
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); });

describe("media batch upload feedback", () => {
  it("refreshes partial success and explicitly retries only the unsuccessful file with its original key", async () => {
    f.outcomes = ["success", "refused"];
    await act(async () => root.render(<Page />)); await flush();
    const reads = f.media.mock.calls.length;
    await click(Array.from(container.querySelectorAll("button")).find(button => button.textContent === "添加其他照片")!);
    expect(f.upload).toHaveBeenCalledTimes(2);
    expect(f.media.mock.calls.length).toBeGreaterThan(reads);
    expect(container.textContent).toContain("已上传 1 张，失败 1 张，结果未确认 0 张");
    const original = f.upload.mock.calls[1][0];
    const retry = Array.from(container.querySelectorAll("button")).find(button => button.textContent === "仅重试未完成的照片")!;
    await click(retry);
    expect(f.upload).toHaveBeenCalledTimes(3);
    expect(f.upload.mock.calls[2][0].filePath).toBe(original.filePath);
    expect(f.upload.mock.calls[2][0].header["Idempotency-Key"]).toBe(original.header["Idempotency-Key"]);
    expect(container.textContent).not.toContain("仅重试未完成的照片");
  });

  it("refreshes even when every response body is lost and does not label unconfirmed results as failures", async () => {
    f.outcomes = ["unknown", "unknown"];
    await act(async () => root.render(<Page />)); await flush();
    const reads = f.media.mock.calls.length;
    await click(Array.from(container.querySelectorAll("button")).find(button => button.textContent === "添加其他照片")!);
    expect(f.upload).toHaveBeenCalledTimes(2);
    expect(f.media.mock.calls.length).toBeGreaterThan(reads);
    expect(container.textContent).toContain("已上传 0 张，失败 0 张，结果未确认 2 张");
    expect(container.querySelectorAll(".admin-media-row:not(.is-empty)")).toHaveLength(2);
  });
});
