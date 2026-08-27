// @vitest-environment jsdom
import React, { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { makeDocument } from "../../../admin-web/src/scoresheet-reader/test/fixtures";
import type { ScoresheetDetail } from "@pkuba/api-client";

const fixture = vi.hoisted(() => ({
  api: {
    getScoresheet: vi.fn(), acquireScoresheetLease: vi.fn(), heartbeatScoresheetLease: vi.fn(),
    releaseScoresheetLease: vi.fn(), syncScoresheet: vi.fn(), saveScoresheetDraft: vi.fn(),
    validateScoresheet: vi.fn(), publishScoresheet: vi.fn(), acknowledgeScoresheetWarnings: vi.fn(),
    retryScoresheetRecognition: vi.fn(),
  },
  storage: new Map<string, unknown>(), showModal: vi.fn(), server: null as ScoresheetDetail | null,
  holder: null as Record<string, unknown> | null,
}));

vi.mock("@tarojs/components", () => {
  const block = ({ children, className, id, onClick }: any) => React.createElement("div", { className, id, onClick }, children);
  return { View: block, Text: block, ScrollView: block, MovableArea: block, MovableView: block,
    Image: () => React.createElement("span"),
    Button: ({ children, className, disabled, onClick }: any) => React.createElement("button", { className, disabled, onClick }, children),
  };
});
vi.mock("@tarojs/taro", async () => {
  const react = await import("react");
  return { default: {
    getStorageSync: (key: string) => fixture.storage.get(key),
    setStorageSync: (key: string, value: unknown) => fixture.storage.set(key, structuredClone(value)),
    removeStorageSync: (key: string) => fixture.storage.delete(key),
    showModal: fixture.showModal, showToast: vi.fn(),
    onNetworkStatusChange: vi.fn(), offNetworkStatusChange: vi.fn(),
    createSelectorQuery: () => ({ select: () => ({ boundingClientRect: (cb: any) => ({ exec: () => cb({ width: 390, height: 550 }) }) }) }),
  }, useRouter: () => ({ params: { id: "retry-mini" } }),
    useDidShow: (fn: () => void) => react.useEffect(() => { fn(); }, []),
    useUnload: (fn: () => void) => react.useEffect(() => () => fn(), []),
  };
});
vi.mock("../api", () => ({ api: fixture.api, absoluteMediaUrl: (url: string) => url, replaceGameMedia: vi.fn() }));
vi.mock("../auth", () => ({ getMiniAppSession: () => "fixture-session" }));
vi.mock("./MobileStandardView", () => ({
  MobileStandardView: ({ document, onChange, readOnly }: any) => <div>
    <span data-crew>{document.header.crew_chief}</span>
    <button disabled={readOnly} onClick={() => {
      const next = structuredClone(document);
      next.header.crew_chief = "新人工主裁";
      onChange(next, true);
    }}>修改人工主裁</button>
  </div>,
}));
import Editor from "./pages/editor";

let container: HTMLDivElement;
let root: Root;
const clone = <T,>(value: T): T => structuredClone(value);
const button = (label: string) => [...container.querySelectorAll("button")].find((row) => row.textContent === label);
async function flush() {
  await act(async () => { for (let i = 0; i < 8; i += 1) await Promise.resolve(); await vi.advanceTimersByTimeAsync(0); });
}
async function click(target: HTMLElement | undefined) {
  expect(target).toBeTruthy();
  await act(async () => { target!.click(); });
  await flush();
}
async function mount() { await act(async () => { root.render(<Editor />); }); await flush(); }

beforeEach(() => {
  (globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;
  vi.useFakeTimers();
  fixture.storage.clear();
  fixture.storage.set("pkuba-scoresheet-miniapp-client", "fixture-client");
  fixture.holder = null;
  Object.values(fixture.api).forEach((fn) => fn.mockReset());
  fixture.showModal.mockReset().mockResolvedValue({ confirm: true });
  const draft = makeDocument("retry-mini");
  draft.header.crew_chief = "原人工主裁";
  fixture.server = {
    id: "retry-mini", game: { id: "fixture-game", label: "数学 — 外院" },
    source: { id: "fixture-source", version: 1, url: "/fixture.png", filename: "fixture.png", width: 100, height: 100 },
    source_version: 1, status: "DRAFT", draft, draft_version: 5, event_sequence: 10,
    reviewed_regions: {}, validation_report: { errors: [], warnings: [] }, validation_draft_version: null,
    acknowledged_warnings: [], recognition: {
      id: "failed-run", status: "FAILED", can_retry: true, attempt_count: 4, max_attempts: 4,
      next_attempt_at: null, model: "fixture", prompt_version: "fixture", image_sha256: "fixture",
      auto_apply_allowed: false, last_error_code: "FIXTURE_FAILED", last_error: "模拟失败",
    },
    lease: null, publication: null,
  };
  fixture.api.getScoresheet.mockImplementation(async () => clone(fixture.server));
  fixture.api.acquireScoresheetLease.mockImplementation(async () => {
    fixture.holder = { client_id: "fixture-client", surface: "MINIAPP", username: "合成管理员" };
    return { read_only: false, lease_token: "fixture-lease", holder: fixture.holder };
  });
  fixture.api.heartbeatScoresheetLease.mockResolvedValue({});
  fixture.api.releaseScoresheetLease.mockImplementation(async () => { fixture.holder = null; });
  fixture.api.syncScoresheet.mockImplementation(async () => ({
    current_version: fixture.server!.draft_version, current_event: fixture.server!.event_sequence,
    events: [], requires_full_reload: false, lease: fixture.holder,
  }));
  fixture.api.saveScoresheetDraft.mockImplementation(async (_id, _context, patches) => {
    fixture.server = { ...fixture.server!, draft: clone(patches[0].value),
      draft_version: fixture.server!.draft_version + 1, event_sequence: fixture.server!.event_sequence + 1 };
    return clone(fixture.server);
  });
  fixture.api.validateScoresheet.mockImplementation(async () => clone(fixture.server));
  fixture.api.publishScoresheet.mockImplementation(async () => {
    fixture.holder = null;
    fixture.server = { ...fixture.server!, status: "PUBLISHED", event_sequence: fixture.server!.event_sequence + 1 };
    return clone(fixture.server);
  });
  fixture.api.retryScoresheetRecognition.mockResolvedValue({});
  vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new Error("LIVE_NETWORK_FORBIDDEN"))));
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
});

afterEach(async () => {
  await act(async () => { root.unmount(); });
  container.remove(); vi.clearAllTimers(); vi.useRealTimers(); vi.unstubAllGlobals();
});

describe("native failed recognition retry", () => {
  it("warns before a full overwrite; cancel keeps manual content and sends nothing", async () => {
    fixture.showModal.mockResolvedValue({ confirm: false });
    await mount(); await click(button("重新识别"));
    expect(fixture.showModal).toHaveBeenCalledWith(expect.objectContaining({
      content: expect.stringMatching(/覆盖整张草稿.*人工修改.*失败.*保留/),
    }));
    expect(fixture.api.retryScoresheetRecognition).not.toHaveBeenCalled();
    expect(fixture.server!.draft.header.crew_chief).toBe("原人工主裁");
  });

  it("saves manual input and sends explicit consent with the saved version", async () => {
    await mount(); await click(button("标准表")); await click(button("修改人工主裁"));
    expect(fixture.api.saveScoresheetDraft).toHaveBeenCalledOnce();
    await click(button("重新识别"));
    expect(fixture.api.retryScoresheetRecognition).toHaveBeenCalledExactlyOnceWith(
      "retry-mini", expect.objectContaining({ expected_version: 6, confirmed_overwrite: true }),
      "fixture-session", expect.any(String),
    );
  });

  it("keeps the draft after retry HTTP failure and allows a stable-key retry", async () => {
    fixture.api.retryScoresheetRecognition.mockRejectedValue(new Error("模拟失败"));
    await mount(); await click(button("标准表"));
    await click(button("重新识别")); await click(button("重新识别"));
    const calls = fixture.api.retryScoresheetRecognition.mock.calls;
    expect(calls).toHaveLength(2); expect(calls[1][3]).toBe(calls[0][3]);
    expect(container.querySelector("[data-crew]")?.textContent).toBe("原人工主裁");
  });

  it("published manual sheets stay without retry after two syncs reacquire a superadmin lease", async () => {
    await mount(); await click(button("标准表"));
    await click([...container.querySelectorAll<HTMLElement>(".mini-sheet-step")].find((e) => e.textContent === "6发布"));
    await click(button("校验并发布"));
    expect(fixture.api.publishScoresheet).toHaveBeenCalledOnce();
    expect(fixture.server!.status).toBe("PUBLISHED");
    for (let i = 0; i < 2; i += 1) { await act(async () => { await vi.advanceTimersByTimeAsync(2000); }); await flush(); }
    expect(fixture.api.acquireScoresheetLease.mock.calls.length).toBeGreaterThanOrEqual(2);
    expect(button("重新识别")).toBeUndefined();
    expect(fixture.api.retryScoresheetRecognition).not.toHaveBeenCalled();
  });

  it.each(["SUCCEEDED", "SUPERSEDED"])("does not retry a %s result", async (status) => {
    fixture.server!.recognition = { ...fixture.server!.recognition!, status, can_retry: false };
    await mount(); expect(button("重新识别")).toBeUndefined();
    expect(fixture.api.retryScoresheetRecognition).not.toHaveBeenCalled();
  });
});
