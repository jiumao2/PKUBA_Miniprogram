// @vitest-environment jsdom
import React, { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { makeDocument } from "../../../admin-web/src/scoresheet-reader/test/fixtures";
import type { ScoresheetDetail, ScoresheetSync } from "@pkuba/api-client";

const fixture = vi.hoisted(() => ({
  api: {
    getScoresheet: vi.fn(), acquireScoresheetLease: vi.fn(), heartbeatScoresheetLease: vi.fn(),
    releaseScoresheetLease: vi.fn(), syncScoresheet: vi.fn(), saveScoresheetDraft: vi.fn(),
    validateScoresheet: vi.fn(), publishScoresheet: vi.fn(), acknowledgeScoresheetWarnings: vi.fn(),
    retryScoresheetRecognition: vi.fn(), reviewScoresheetGameContext: vi.fn(),
  },
  storage: new Map<string, unknown>(), showModal: vi.fn(), chooseMedia: vi.fn(),
  replaceGameMedia: vi.fn(), server: null as ScoresheetDetail | null,
  holder: null as Record<string, unknown> | null,
}));

vi.mock("@tarojs/components", () => {
  const block = ({ children, className, id, onClick }: any) => React.createElement("div", { className, id, onClick }, children);
  return { View: block, Text: block, ScrollView: block, MovableArea: block, MovableView: block, Picker: block,
    Image: ({ className, src, onError }: any) => React.createElement("img", { className, src, onError }),
    Button: ({ children, className, disabled, onClick }: any) => React.createElement("button", { className, disabled, onClick }, children),
  };
});
vi.mock("@tarojs/taro", async () => {
  const react = await import("react");
  return { default: {
    getStorageSync: (key: string) => fixture.storage.get(key),
    setStorageSync: (key: string, value: unknown) => fixture.storage.set(key, structuredClone(value)),
    removeStorageSync: (key: string) => fixture.storage.delete(key),
    showModal: fixture.showModal, showToast: vi.fn(), chooseMedia: fixture.chooseMedia,
    onNetworkStatusChange: vi.fn(), offNetworkStatusChange: vi.fn(),
    createSelectorQuery: () => ({ select: () => ({ boundingClientRect: (cb: any) => ({ exec: () => cb({ width: 390, height: 550 }) }) }) }),
  }, useRouter: () => ({ params: { id: "retry-mini" } }),
    useDidShow: (fn: () => void) => react.useEffect(() => { fn(); }, []),
    useUnload: (fn: () => void) => react.useEffect(() => () => fn(), []),
  };
});
vi.mock("../api", () => ({ api: fixture.api, absoluteMediaUrl: (url: string) => url, replaceGameMedia: fixture.replaceGameMedia }));
vi.mock("../auth", () => ({ getMiniAppSession: () => "fixture-session" }));
vi.mock("./MobileStandardView", () => ({
  MobileStandardView: ({ document, onChange, readOnly }: any) => <div>
    <span data-crew>{document.header.crew_chief}</span>
    <span data-revision>{document.revision}</span>
    <button disabled={readOnly} onClick={() => {
      const next = structuredClone(document);
      next.header.crew_chief = "新人工主裁";
      onChange(next, true);
    }}>修改人工主裁</button>
    <button disabled={readOnly} onClick={() => {
      const next = structuredClone(document);
      next.header.crew_chief = "待保存人工主裁";
      onChange(next, false);
    }}>输入待保存主裁</button>
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
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}
async function poll() {
  await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
  await flush();
}
async function imageError() {
  const image = container.querySelector("img.mini-source-image");
  expect(image).not.toBeNull();
  await act(async () => { image!.dispatchEvent(new Event("error")); });
  await flush();
}
async function remountSource() {
  await click(button("标准表"));
  await click(button("原图"));
}
function syncPayload(): ScoresheetSync {
  return {
    scoresheet_id: fixture.server!.id, current_version: fixture.server!.draft_version,
    current_event: fixture.server!.event_sequence, events: [], requires_full_reload: false,
    lease: clone(fixture.holder) as ScoresheetSync["lease"], can_upload_source: fixture.server!.can_upload_source,
    reviewed_regions: clone(fixture.server!.reviewed_regions), validation_report: clone(fixture.server!.validation_report),
    status: fixture.server!.status, recognition: clone(fixture.server!.recognition), publication: clone(fixture.server!.publication),
    pending_correction: null,
  };
}
function expectReadOnlyRefresh(before: ScoresheetDetail) {
  expect(fixture.server!.draft).toEqual(before.draft);
  expect(fixture.server!.publication).toEqual(before.publication);
  expect(fixture.api.acquireScoresheetLease).toHaveBeenCalledOnce();
  for (const key of ["heartbeatScoresheetLease", "releaseScoresheetLease", "saveScoresheetDraft",
    "validateScoresheet", "publishScoresheet", "acknowledgeScoresheetWarnings", "retryScoresheetRecognition"] as const) {
    expect(fixture.api[key], key).not.toHaveBeenCalled();
  }
  expect(fixture.chooseMedia).not.toHaveBeenCalled();
  expect(fixture.replaceGameMedia).not.toHaveBeenCalled();
}

beforeEach(() => {
  (globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;
  vi.useFakeTimers();
  fixture.storage.clear();
  fixture.storage.set("pkuba-scoresheet-miniapp-client", "fixture-client");
  fixture.holder = null;
  Object.values(fixture.api).forEach((fn) => fn.mockReset());
  fixture.showModal.mockReset().mockResolvedValue({ confirm: true });
  fixture.chooseMedia.mockReset().mockResolvedValue({ tempFiles: [{ tempFilePath: '/fixture-new.jpg' }] });
  fixture.replaceGameMedia.mockReset().mockResolvedValue({});
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
    lease: null, publication: null, pending_correction: null, can_upload_source: true,
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
    can_upload_source: fixture.server!.can_upload_source,
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

describe("server-authoritative original image replacement", () => {
  it.each(["DRAFT", "PUBLISHED"])("hides forbidden source replacement despite an editable %s lease", async (status) => {
    fixture.server!.status = status as ScoresheetDetail["status"];
    fixture.server!.can_upload_source = false;
    await mount();
    expect(button("重传新原图")).toBeUndefined();
    expect(button("复位")).toBeDefined();
    expect(button("仅旋转视图")).toBeDefined();
    expect(fixture.chooseMedia).not.toHaveBeenCalled();
    expect(fixture.replaceGameMedia).not.toHaveBeenCalled();
  });

  it("permits an authorized source replacement with the original source version", async () => {
    await mount();
    await click(button("重传新原图"));
    expect(fixture.chooseMedia).toHaveBeenCalledOnce();
    expect(fixture.replaceGameMedia).toHaveBeenCalledExactlyOnceWith(
      "fixture-source", 1, "/fixture-new.jpg", true, "fixture-session",
    );
  });

  it("hides the action on a same-version capability change without mutating the draft", async () => {
    await mount();
    expect(button("重传新原图")).toBeDefined();
    const before = clone(fixture.server!.draft);
    fixture.server!.can_upload_source = false;
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    await flush();
    expect(button("重传新原图")).toBeUndefined();
    expect(fixture.server!.draft).toEqual(before);
    expect(fixture.api.saveScoresheetDraft).not.toHaveBeenCalled();
  });
});

describe("source capability read ordering", () => {
  beforeEach(() => {
    fixture.server!.status = "PUBLISHED";
    fixture.server!.publication = { id: "fixture-publication", publication_number: 1, published_at: "2026-08-01T00:00:00Z" };
  });

  it.each([false, true])("keeps a newer GET denial when old sync arrives (event advanced: %s)", async (advanceEvent) => {
    const before = clone(fixture.server!);
    await mount();
    const held = deferred<ScoresheetSync>();
    const old = syncPayload();
    fixture.api.syncScoresheet.mockReturnValueOnce(held.promise);
    await poll();
    fixture.server!.can_upload_source = false;
    if (advanceEvent) fixture.server!.event_sequence += 1;
    await imageError();
    expect(button("重传新原图")).toBeUndefined();
    await act(async () => { held.resolve(old); }); await flush();
    expect(button("重传新原图")).toBeUndefined();
    expect(fixture.server!.source).toEqual(before.source);
    expectReadOnlyRefresh(before);

    // A new request is still allowed to observe legitimate reauthorization.
    fixture.server!.can_upload_source = true;
    await poll();
    expect(button("重传新原图")?.disabled).toBe(false);
    expectReadOnlyRefresh(before);
  });

  it.each([false, true])("keeps the newer of two error-triggered GETs (new source: %s)", async (newSource) => {
    const before = clone(fixture.server!);
    await mount();
    const held = deferred<ScoresheetDetail>();
    const old = clone(fixture.server!);
    fixture.api.getScoresheet.mockReturnValueOnce(held.promise);
    await imageError();
    expect(fixture.api.getScoresheet).toHaveBeenCalledTimes(2);
    fixture.server!.can_upload_source = false;
    if (newSource) {
      fixture.server!.draft_version += 1;
      fixture.server!.event_sequence += 2;
      fixture.server!.source_version = 2;
      fixture.server!.source = { ...fixture.server!.source!, id: "fixture-source-2", version: 2, url: "/fixture-new.png" };
    }
    // SourceView deduplicates one URL error; its real tabs remount it for a second error.
    await remountSource(); await imageError();
    expect(fixture.api.getScoresheet).toHaveBeenCalledTimes(3);
    expect(button("重传新原图")).toBeUndefined();
    const latestUrl = fixture.server!.source!.url;
    await act(async () => { held.resolve(old); }); await flush();
    expect(button("重传新原图")).toBeUndefined();
    expect(container.querySelector("img.mini-source-image")?.getAttribute("src")).toBe(latestUrl);
    expectReadOnlyRefresh(before);
  });

  it("does not let a pending GET undo a newer same-watermark sync denial", async () => {
    const before = clone(fixture.server!);
    await mount();
    const held = deferred<ScoresheetDetail>();
    const old = clone(fixture.server!);
    fixture.api.getScoresheet.mockReturnValueOnce(held.promise);
    await imageError();
    fixture.server!.can_upload_source = false;
    await poll();
    expect(button("重传新原图")).toBeUndefined();
    await act(async () => { held.resolve(old); }); await flush();
    expect(button("重传新原图")).toBeUndefined();
    expectReadOnlyRefresh(before);
  });

  it("allows a newer GET to restore permission at the same version and event", async () => {
    const before = clone(fixture.server!);
    fixture.server!.can_upload_source = false;
    await mount();
    expect(button("重传新原图")).toBeUndefined();
    fixture.server!.can_upload_source = true;
    await imageError();
    expect(button("重传新原图")?.disabled).toBe(false);
    expect(fixture.server!.draft_version).toBe(before.draft_version);
    expect(fixture.server!.event_sequence).toBe(before.event_sequence);
    expectReadOnlyRefresh(before);
  });

  it("keeps a newer GET grant when an older denied poll returns", async () => {
    const before = clone(fixture.server!);
    fixture.server!.can_upload_source = false;
    await mount();
    const held = deferred<ScoresheetSync>();
    const denied = syncPayload();
    fixture.api.syncScoresheet.mockReturnValueOnce(held.promise);
    await poll();
    fixture.server!.can_upload_source = true;
    await imageError();
    expect(button("重传新原图")?.disabled).toBe(false);
    await act(async () => { held.resolve(denied); }); await flush();
    expect(button("重传新原图")?.disabled).toBe(false);
    expectReadOnlyRefresh(before);
  });

  it("keeps the sync watermark while its full detail reload is pending", async () => {
    const before = clone(fixture.server!);
    await mount();
    const old = clone(fixture.server!);
    fixture.server!.can_upload_source = false;
    fixture.server!.draft_version += 1;
    fixture.server!.event_sequence += 2;
    const current = clone(fixture.server!);
    const held = deferred<ScoresheetDetail>();
    fixture.api.syncScoresheet.mockResolvedValueOnce({ ...syncPayload(), requires_full_reload: true });
    fixture.api.getScoresheet.mockReturnValueOnce(held.promise).mockResolvedValueOnce(old);
    await poll();
    expect(button("重传新原图")).toBeUndefined();
    // A later request returning an older server snapshot is still stale.
    await imageError();
    expect(fixture.api.getScoresheet).toHaveBeenCalledTimes(3);
    expect(button("重传新原图")).toBeUndefined();
    await act(async () => { held.resolve(current); }); await flush();
    expect(button("重传新原图")).toBeUndefined();
    expectReadOnlyRefresh(before);
  });

  it("accepts a valid first GET while the newer GET is pending, then adopts the newer denial", async () => {
    const before = clone(fixture.server!);
    fixture.server!.can_upload_source = false;
    await mount();
    const first = deferred<ScoresheetDetail>(), second = deferred<ScoresheetDetail>();
    const granted = { ...clone(fixture.server!), can_upload_source: true };
    fixture.api.getScoresheet.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    await imageError(); await remountSource(); await imageError();
    await act(async () => { first.resolve(granted); }); await flush();
    expect(button("重传新原图")?.disabled).toBe(false);
    await act(async () => { second.resolve(clone(fixture.server!)); }); await flush();
    expect(button("重传新原图")).toBeUndefined();
    expectReadOnlyRefresh(before);
  });

  it("does not let a failed newer GET suppress a legitimate later poll", async () => {
    const before = clone(fixture.server!);
    fixture.server!.can_upload_source = false;
    await mount();
    fixture.api.getScoresheet.mockRejectedValueOnce(new Error("合成读取失败"));
    await imageError();
    expect(button("重传新原图")).toBeUndefined();
    fixture.server!.can_upload_source = true;
    await poll();
    expect(button("重传新原图")?.disabled).toBe(false);
    expectReadOnlyRefresh(before);
  });

  it("keeps pending manual input through newer and stale GETs without saving it early", async () => {
    const before = clone(fixture.server!);
    await mount();
    const held = deferred<ScoresheetDetail>();
    const old = clone(fixture.server!);
    fixture.api.getScoresheet.mockReturnValueOnce(held.promise);
    await imageError();
    await click(button("标准表")); await click(button("输入待保存主裁"));
    expect(container.querySelector("[data-crew]")?.textContent).toBe("待保存人工主裁");
    await click(button("原图"));
    fixture.server!.can_upload_source = false;
    await imageError();
    expect(button("重传新原图")).toBeUndefined();
    await act(async () => { held.resolve(old); }); await flush();
    expect(button("重传新原图")).toBeUndefined();
    await click(button("标准表"));
    expect(container.querySelector("[data-crew]")?.textContent).toBe("待保存人工主裁");
    expectReadOnlyRefresh(before);
  });
});

describe("source capability mutation observations", () => {
  async function editAndSave() {
    await click(button("标准表"));
    await click(button("修改人工主裁"));
    await click(button("原图"));
    expect(fixture.api.saveScoresheetDraft).toHaveBeenCalledOnce();
  }

  it("does not re-age an already committed save over a later same-watermark permission read", async () => {
    await mount();
    const held = deferred<ScoresheetDetail>();
    let committed!: ScoresheetDetail;
    fixture.api.saveScoresheetDraft.mockImplementationOnce(async (_id, _context, patches) => {
      fixture.server = { ...fixture.server!, draft: clone(patches[0].value), draft_version: 6, event_sequence: 11 };
      committed = clone(fixture.server);
      return held.promise;
    });
    await editAndSave();
    fixture.server!.can_upload_source = false;
    await imageError();
    expect(button("重传新原图")).toBeUndefined();
    await act(async () => { held.resolve(committed); }); await flush();
    expect(button("重传新原图")).toBeUndefined();
    expect(container.querySelector(".mini-sheet-save")?.textContent).toBe("已保存");
    await click(button("标准表"));
    expect(container.querySelector("[data-crew]")?.textContent).toBe("新人工主裁");
    await poll();
    expect(fixture.api.syncScoresheet).toHaveBeenLastCalledWith("retry-mini", 6, 11, "fixture-session");
    expect(fixture.api.saveScoresheetDraft).toHaveBeenCalledOnce();
    expect(fixture.api.acquireScoresheetLease).toHaveBeenCalledOnce();
    expect(fixture.replaceGameMedia).not.toHaveBeenCalled();
  });

  it("accepts a higher-watermark commit that actually happens after an intervening old GET", async () => {
    await mount();
    const held = deferred<ScoresheetDetail>();
    fixture.api.saveScoresheetDraft.mockReturnValueOnce(held.promise);
    await editAndSave();
    await imageError(); // The write has not happened; this is still v5/event10.
    const savedDraft = clone(fixture.api.saveScoresheetDraft.mock.calls[0][2][0].value);
    fixture.server = { ...fixture.server!, draft: savedDraft, draft_version: 6, event_sequence: 11 };
    await act(async () => { held.resolve(clone(fixture.server!)); }); await flush();
    await click(button("标准表"));
    expect(container.querySelector("[data-crew]")?.textContent).toBe("新人工主裁");
    expect(container.querySelector(".mini-sheet-save")?.textContent).toBe("已保存");
    await poll();
    expect(fixture.api.syncScoresheet).toHaveBeenLastCalledWith("retry-mini", 6, 11, "fixture-session");
    await click(button("原图"));
    expect(button("重传新原图")?.disabled).toBe(false);
  });

  it.each(["validate", "review", "ack", "publish"] as const)("keeps a newer same-watermark GET capability after delayed %s success", async (action) => {
    if (action === "review") fixture.server!.validation_report.game_context = {
      required: true, differences: [], player_conflicts: [], review_token: "synthetic-context-review",
    };
    if (action === "ack") fixture.server!.validation_report.warnings = [{
      id: "synthetic-warning", region: "SOURCE_GAME", path: "/header/crew_chief", message: "合成提醒",
      severity: "WARNING", code: "SYNTHETIC_WARNING", context: {},
    }];
    await mount();
    await click(button("标准表"));
    await click([...container.querySelectorAll<HTMLElement>(".mini-sheet-step")].find(row => row.textContent === "6发布"));
    const held = deferred<ScoresheetDetail>(), continuation = deferred<ScoresheetDetail>();
    let committed!: ScoresheetDetail;
    const mutation = action === "validate" ? fixture.api.validateScoresheet
      : action === "review" ? fixture.api.reviewScoresheetGameContext
        : action === "ack" ? fixture.api.acknowledgeScoresheetWarnings : fixture.api.publishScoresheet;
    mutation.mockImplementationOnce(() => {
      fixture.server = { ...fixture.server!, event_sequence: 11,
        status: action === "publish" ? "PUBLISHED" : fixture.server!.status };
      committed = clone(fixture.server);
      return held.promise;
    });
    if (action === "review") fixture.api.validateScoresheet.mockReturnValueOnce(continuation.promise);
    if (action === "ack") fixture.api.publishScoresheet.mockReturnValueOnce(continuation.promise);
    await click(button(action === "validate" ? "重新校验" : action === "review" ? "保留编辑并确认复核" : "校验并发布"));
    expect(mutation).toHaveBeenCalledOnce();
    await click(button("原图"));
    fixture.server!.can_upload_source = false;
    await imageError();
    expect(button("重传新原图")).toBeUndefined();
    await act(async () => { held.resolve(committed); }); await flush();
    // Review's validate and ack's publish remain pending here, so they cannot mask an old permission regression.
    expect(button("重传新原图")).toBeUndefined();
    if (action === "review" || action === "ack") {
      await act(async () => { continuation.resolve(clone(fixture.server!)); }); await flush();
    }
    if (action === "publish" || action === "ack") expect(container.querySelector(".mini-sheet-mode")?.textContent).toBe("只读查看");
    expect(fixture.api.acquireScoresheetLease).toHaveBeenCalledOnce();
    expect(fixture.api.saveScoresheetDraft).not.toHaveBeenCalled();
    expect(fixture.replaceGameMedia).not.toHaveBeenCalled();
    expect(fixture.api.retryScoresheetRecognition).not.toHaveBeenCalled();
  });

  it("accepts a later event-only validation result after an intervening GET", async () => {
    await mount();
    await click([...container.querySelectorAll<HTMLElement>(".mini-sheet-step")].find(row => row.textContent === "6发布"));
    const held = deferred<ScoresheetDetail>();
    fixture.api.validateScoresheet.mockReturnValueOnce(held.promise);
    await click(button("重新校验"));
    await imageError();
    fixture.server!.event_sequence = 11;
    fixture.server!.status = "READY";
    fixture.server!.validation_draft_version = 5;
    await act(async () => { held.resolve(clone(fixture.server!)); }); await flush();
    await poll();
    expect(fixture.api.syncScoresheet).toHaveBeenLastCalledWith("retry-mini", 5, 11, "fixture-session");
    await click(button("标准表"));
    expect(container.querySelector(".mini-publish-summary")?.textContent).toContain("0 个错误 · 0 个提醒");
  });

  it("ignores an obsolete sync error after a newer successful GET but reports the next current failure", async () => {
    await mount();
    const held = deferred<ScoresheetSync>();
    fixture.api.syncScoresheet.mockReturnValueOnce(held.promise);
    await poll();
    fixture.server!.can_upload_source = false;
    await imageError();
    await act(async () => { held.reject(new Error("旧轮询错误")); }); await flush();
    expect(container.querySelector(".mini-sheet-error")).toBeNull();
    expect(button("重传新原图")).toBeUndefined();
    fixture.api.syncScoresheet.mockRejectedValueOnce(new Error("当前轮询错误"));
    await poll();
    expect(container.querySelector(".mini-sheet-error")?.textContent).toContain("当前轮询错误");
    expect(fixture.api.acquireScoresheetLease).toHaveBeenCalledOnce();
    expect(fixture.api.saveScoresheetDraft).not.toHaveBeenCalled();
  });

  it("does not hide a current sync follow-up detail failure", async () => {
    await mount();
    fixture.api.syncScoresheet.mockResolvedValueOnce({ ...syncPayload(), requires_full_reload: true });
    fixture.api.getScoresheet.mockRejectedValueOnce(new Error("当前详情读取错误"));
    await poll();
    expect(container.querySelector(".mini-sheet-error")?.textContent).toContain("当前详情读取错误");
  });
});

describe("complete save bodies and capability observations", () => {
  beforeEach(() => {
    fixture.server!.draft.revision = 5;
    fixture.server!.status = "PUBLISHED";
    fixture.server!.publication = { id: "fixture-publication", publication_number: 1, published_at: "2026-08-01T00:00:00Z" };
    fixture.api.saveScoresheetDraft.mockImplementation(async (_id, mutation, patches) => {
      expect(mutation.expected_version).toBe(fixture.server!.draft_version);
      const version = fixture.server!.draft_version + 1;
      fixture.server = { ...fixture.server!, status: "DRAFT", draft_version: version,
        event_sequence: fixture.server!.event_sequence + 1, draft: { ...clone(patches[0].value), revision: version } };
      return clone(fixture.server!);
    });
  });

  async function beginHeldSave(commitImmediately = true) {
    const held = deferred<ScoresheetDetail>();
    let submitted!: ScoresheetDetail["draft"];
    let committed!: ScoresheetDetail;
    const commit = () => {
      fixture.server = { ...fixture.server!, status: "DRAFT", draft_version: 6, event_sequence: 11,
        draft: { ...clone(submitted), revision: 6 } };
      committed = clone(fixture.server);
      return committed;
    };
    fixture.api.saveScoresheetDraft.mockImplementationOnce((_id, mutation, patches) => {
      expect(mutation.expected_version).toBe(5);
      submitted = clone(patches[0].value);
      if (commitImmediately) commit();
      return held.promise;
    });
    await click(button("标准表")); await click(button("修改人工主裁")); await click(button("原图"));
    expect(fixture.api.saveScoresheetDraft).toHaveBeenCalledOnce();
    return { held, commit, get committed() { return committed; } };
  }

  function expectNoOtherWrites(before: ScoresheetDetail) {
    expect(fixture.server!.source).toEqual(before.source);
    expect(fixture.server!.source_version).toBe(before.source_version);
    expect(fixture.server!.publication).toEqual(before.publication);
    expect(fixture.api.acquireScoresheetLease).toHaveBeenCalledOnce();
    expect(fixture.api.heartbeatScoresheetLease).not.toHaveBeenCalled();
    expect(fixture.api.releaseScoresheetLease).not.toHaveBeenCalled();
    expect(fixture.api.publishScoresheet).not.toHaveBeenCalled();
    expect(fixture.api.retryScoresheetRecognition).not.toHaveBeenCalled();
    expect(fixture.chooseMedia).not.toHaveBeenCalled();
    expect(fixture.replaceGameMedia).not.toHaveBeenCalled();
  }

  it.each([true, false])("fills a complete saved draft after newer summary metadata without reverting capability (%s)", async (capability) => {
    const before = clone(fixture.server!);
    await mount();
    const save = await beginHeldSave();
    fixture.server!.can_upload_source = capability;
    fixture.api.syncScoresheet.mockResolvedValueOnce({ ...syncPayload(), requires_full_reload: true });
    await poll();
    expect(fixture.api.getScoresheet).toHaveBeenCalledOnce(); // Saving blocks a full GET, not the summary.
    expect(container.querySelector(".mini-sheet-save")?.textContent).toBe("保存中…");
    await act(async () => { save.held.resolve(save.committed); }); await flush();
    expect(Boolean(button("重传新原图"))).toBe(capability);
    expect(container.querySelector(".mini-sheet-save")?.textContent).toBe("已保存");
    await click(button("标准表"));
    expect(container.querySelector("[data-crew]")?.textContent).toBe("新人工主裁");
    expect(container.querySelector("[data-revision]")?.textContent).toBe("6");
    expect(fixture.storage.has("pkuba-scoresheet-miniapp-recovery:retry-mini")).toBe(false);
    await poll();
    expect(fixture.api.syncScoresheet).toHaveBeenLastCalledWith("retry-mini", 6, 11, "fixture-session");
    expect(fixture.api.saveScoresheetDraft).toHaveBeenCalledOnce();
    expectNoOtherWrites(before);
  });

  it("keeps later local input and rebases its next save on the complete body, not the summary's old draft", async () => {
    const before = clone(fixture.server!);
    await mount();
    const save = await beginHeldSave();
    fixture.server!.can_upload_source = false;
    fixture.api.syncScoresheet.mockResolvedValueOnce({ ...syncPayload(), requires_full_reload: true });
    await poll();
    await click(button("标准表")); await click(button("输入待保存主裁")); await click(button("原图"));
    await act(async () => { save.held.resolve(save.committed); }); await flush();
    const recovery = fixture.storage.get("pkuba-scoresheet-miniapp-recovery:retry-mini") as { baseVersion: number; local: ScoresheetDetail["draft"] };
    expect(recovery.baseVersion).toBe(6);
    expect(recovery.local.header.crew_chief).toBe("待保存人工主裁");
    expect(button("重传新原图")).toBeUndefined();
    await click(button("标准表"));
    expect(container.querySelector("[data-crew]")?.textContent).toBe("待保存人工主裁");
    await act(async () => { await vi.advanceTimersByTimeAsync(60); }); await flush();
    expect(fixture.api.saveScoresheetDraft).toHaveBeenCalledTimes(2);
    expect(fixture.api.saveScoresheetDraft.mock.calls[1][1].expected_version).toBe(6);
    expect(fixture.server!.draft.header.crew_chief).toBe("待保存人工主裁");
    expect(fixture.server!.draft_version).toBe(7);
    expect(container.querySelector("[data-revision]")?.textContent).toBe("7");
    expect(container.querySelector(".mini-sheet-save")?.textContent).toBe("已保存");
    expectNoOtherWrites(before);
  });

  it.each(["GET", "sync"] as const)("accepts a higher write body without its old actor grant, then permits a fresh %s grant", async (read) => {
    const before = clone(fixture.server!);
    await mount();
    const save = await beginHeldSave(false);
    fixture.server!.can_upload_source = false;
    await imageError(); // The newer actor sees v5/event10 and denies upload.
    expect(button("重传新原图")).toBeUndefined();
    const oldActorResponse = { ...save.commit(), can_upload_source: true };
    await act(async () => { save.held.resolve(oldActorResponse); }); await flush();
    expect(button("重传新原图")).toBeUndefined();
    await click(button("标准表"));
    expect(container.querySelector("[data-crew]")?.textContent).toBe("新人工主裁");
    expect(container.querySelector("[data-revision]")?.textContent).toBe("6");
    await poll();
    expect(fixture.api.syncScoresheet).toHaveBeenLastCalledWith("retry-mini", 6, 11, "fixture-session");
    fixture.server!.can_upload_source = true; // A later authorized observation must not be locked out.
    await click(button("原图"));
    if (read === "GET") await imageError();
    else await poll();
    expect(button("重传新原图")?.disabled).toBe(false);
    expect(fixture.api.saveScoresheetDraft).toHaveBeenCalledOnce();
    expectNoOtherWrites(before);
  });

  it("accepts an event-only validation body while preserving the newer actor's denial", async () => {
    const before = clone(fixture.server!);
    await mount();
    const held = deferred<ScoresheetDetail>();
    fixture.api.validateScoresheet.mockReturnValueOnce(held.promise);
    await click([...container.querySelectorAll<HTMLElement>(".mini-sheet-step")].find(row => row.textContent === "6发布"));
    await click(button("重新校验"));
    fixture.server!.can_upload_source = false;
    await imageError();
    fixture.server = { ...fixture.server!, status: "READY", validation_draft_version: 5, event_sequence: 11 };
    await act(async () => { held.resolve({ ...clone(fixture.server!), can_upload_source: true }); }); await flush();
    expect(button("重传新原图")).toBeUndefined();
    await click(button("标准表"));
    expect(container.querySelector(".mini-publish-summary")?.textContent).toContain("0 个错误 · 0 个提醒");
    await poll();
    expect(fixture.api.syncScoresheet).toHaveBeenLastCalledWith("retry-mini", 5, 11, "fixture-session");
    expect(fixture.api.validateScoresheet).toHaveBeenCalledOnce();
    expect(fixture.api.saveScoresheetDraft).not.toHaveBeenCalled();
    expectNoOtherWrites(before);
  });

  it("does not fill over a genuinely newer complete detail with an older successful body", async () => {
    const before = clone(fixture.server!);
    await mount();
    const save = await beginHeldSave();
    fixture.server = { ...fixture.server!, draft_version: 7, event_sequence: 12, can_upload_source: false,
      draft: { ...clone(fixture.server!.draft), revision: 7, header: { ...fixture.server!.draft.header, crew_chief: "后续权威主裁" } } };
    await imageError();
    await act(async () => { save.held.resolve(save.committed); }); await flush();
    expect(button("重传新原图")).toBeUndefined();
    await click(button("标准表"));
    expect(container.querySelector("[data-crew]")?.textContent).toBe("后续权威主裁");
    expect(container.querySelector("[data-revision]")?.textContent).toBe("7");
    await poll();
    expect(fixture.api.syncScoresheet).toHaveBeenLastCalledWith("retry-mini", 7, 12, "fixture-session");
    expect(fixture.api.saveScoresheetDraft).toHaveBeenCalledOnce();
    expectNoOtherWrites(before);
  });
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
