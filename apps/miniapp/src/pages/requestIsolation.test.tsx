// @vitest-environment jsdom
// All data and services in this probe are synthetic. No real network, Qwen,
// database, account, or browser session is used. Assertions express the
// expected safe product behaviour, so reproduced defects intentionally fail.
import React, { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;

const state = vi.hoisted(() => ({
  api: {
    getEligibleRescheduleGames: vi.fn(),
    getRescheduleTargets: vi.fn(),
    createRescheduleRequest: vi.fn(),
    listInbox: vi.fn(),
    viewInboxTask: vi.fn(),
    listRescheduleRequests: vi.fn(),
  },
  token: "probe-only-not-a-real-session",
  mode: "cross_week",
  showModal: vi.fn(),
  showToast: vi.fn(),
  redirectTo: vi.fn(),
  navigateTo: vi.fn(),
  setNavigationBarTitle: vi.fn(),
}));

vi.mock("@tarojs/components", () => ({
  Button: ({ children, className, disabled, onClick }: any) =>
    React.createElement("button", { className, disabled, onClick }, children),
  Picker: ({ children, range, value, onChange }: any) =>
    React.createElement(
      "div",
      { "data-probe-picker-wrapper": true },
      React.createElement(
        "select",
        {
          "data-probe-picker": true,
          value: String(value),
          onChange: (event: any) => onChange?.({ detail: { value: event.target.value } }),
        },
        range.map((label: string, index: number) =>
          React.createElement("option", { key: index, value: String(index) }, label)),
      ),
      children,
    ),
  Text: ({ children, className }: any) =>
    React.createElement("span", { className }, children),
  View: ({ children, className, onClick }: any) =>
    React.createElement("div", { className, onClick }, children),
  Checkbox: ({ value, checked }: any) =>
    React.createElement("input", { type: "checkbox", value, checked, readOnly: true }),
  CheckboxGroup: ({ children }: any) => React.createElement("div", null, children),
  Label: ({ children, className }: any) => React.createElement("label", { className }, children),
}));

vi.mock("@tarojs/taro", async () => {
  const ReactModule = await import("react");
  return {
    default: {
      showModal: state.showModal,
      showToast: state.showToast,
      redirectTo: state.redirectTo,
      navigateTo: state.navigateTo,
      setNavigationBarTitle: state.setNavigationBarTitle,
    },
    // Model one actual page-show lifecycle event through React's effect.
    useDidShow: (callback: () => void) => ReactModule.useEffect(callback, []),
    useRouter: () => ({ params: { mode: state.mode } }),
  };
});

vi.mock("../api.ts", () => ({
  api: state.api,
}));
vi.mock("../auth.ts", () => ({
  getMiniAppSession: () => state.token,
}));

import RescheduleCreatePage from "./reschedule-create/index";
import InboxPage from "./inbox/index";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

const games = ["A", "B"].map((letter, index) => ({
  id: "probe-game-" + letter,
  code: "PROBE-" + letter,
  division_name: "探针男甲",
  division_gender: "MEN",
  group_name: "探针组",
  date: "2026-04-11",
  start_time: "12:50",
  venue_name: "探针场地",
  home_name: "主队" + letter,
  away_name: "客队" + letter,
  leader_adjustable: true,
  status: "SCHEDULED",
  version: index + 2,
}));

function target(letter: string) {
  return {
    date: letter === "A" ? "2026-04-12" : "2026-04-18",
    period_id: "probe-period-" + letter,
    period_code: "PROBE-" + letter,
    period_name: "探针时段" + letter,
    start_time: letter === "A" ? "12:50" : "15:50",
    request_type: letter === "A" ? "SAME_WEEK" : "CROSS_WEEK",
    request_type_label: letter === "A" ? "同一自然周" : "跨自然周",
    process_route: "HANDBOOK_REVIEW",
    process_route_label: "参赛手册审核",
    submit_deadline: "2026-04-08T16:00:00Z",
    confirmation_deadline: "2026-04-09T16:00:00Z",
  };
}

function task(id: string, status: "OPEN" | "CLOSED") {
  return {
    id,
    status,
    title: id,
    body: "仅供隔离探针",
    target_url: "/pages/home/index",
    created_at: "2026-04-01T00:00:00Z",
    due_at: null,
    read_at: null,
  };
}

let container: HTMLDivElement;
let root: Root;

async function flush() {
  await act(async () => {
    await Promise.resolve();
    await new Promise((resolve) => window.setTimeout(resolve, 0));
  });
}

async function click(element: Element | null | undefined) {
  expect(element, "probe target must exist").toBeTruthy();
  await act(async () => (element as HTMLElement).click());
  await flush();
}

async function chooseGame(index: number) {
  const picker = container.querySelector("select[data-probe-picker]") as HTMLSelectElement;
  expect(picker).toBeTruthy();
  await act(async () => {
    picker.value = String(index);
    picker.dispatchEvent(new Event("change", { bubbles: true }));
  });
  await flush();
}

function submitButton() {
  return [...container.querySelectorAll("button")].find(
    (button) => button.textContent?.trim() === "提交申请",
  );
}

function taskTitles() {
  return [...container.querySelectorAll(".task-title")].map((item) => item.textContent);
}

function record(id: string, detail: unknown) {
  console.log("PROBE_EVIDENCE " + JSON.stringify({ id, detail }));
}

beforeEach(() => {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  Object.values(state.api).forEach((mock) => mock.mockReset());
  state.token = "probe-only-not-a-real-session";
  state.mode = "cross_week";
  state.showModal.mockReset().mockResolvedValue({ confirm: true, cancel: false });
  state.showToast.mockReset().mockResolvedValue({});
  state.redirectTo.mockReset().mockResolvedValue({});
  state.navigateTo.mockReset().mockResolvedValue({});
  state.setNavigationBarTitle.mockReset().mockResolvedValue({});
  state.api.getEligibleRescheduleGames.mockResolvedValue(games);
  state.api.getRescheduleTargets.mockImplementation(async (gameId: string) => [
    target(gameId.endsWith("A") ? "A" : "B"),
  ]);
  state.api.createRescheduleRequest.mockResolvedValue({ id: "probe-request" });
  state.api.listRescheduleRequests.mockResolvedValue([]);
  vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new Error("REAL_NETWORK_FORBIDDEN"))));
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.unstubAllGlobals();
});


function modeTarget(letter: "A" | "B", suffix = "") {
  const row = target(letter);
  return {
    ...row,
    period_id: row.period_id + suffix,
    ...(state.mode === "same_week" ? {
      date: "2026-04-12", request_type: "SAME_WEEK",
      request_type_label: "同一自然周", process_route: "ORDINARY", process_route_label: "普通流程",
    } : {}),
  };
}

async function finish<T>(pending: ReturnType<typeof deferred<T>>, value: T) {
  await act(async () => pending.resolve(value));
  await flush();
}
async function fail(pending: ReturnType<typeof deferred<any>>, message: string) {
  await act(async () => pending.reject(new Error(message)));
  await flush();
}
async function mountPage(page: "create" | "inbox") {
  await act(async () => root.render(page === "create" ? <RescheduleCreatePage /> : <InboxPage />));
  await flush();
}
function ui() {
  const button = container.querySelector<HTMLButtonElement>(".load-more");
  return {
    text: container.textContent ?? "",
    gameIndex: container.querySelector<HTMLSelectElement>("select[data-probe-picker]")?.value,
    gameLabel: container.querySelector(".flow-picker-title")?.textContent,
    createLoading: (container.textContent ?? "").includes("正在核对可申请比赛和容量"),
    inboxLoading: (container.textContent ?? "").includes("正在读取任务"),
    rows: taskTitles(),
    activeTab: container.querySelector(".inbox-tab.is-active")?.textContent,
    inboxError: container.querySelector(".inbox-error")?.textContent ?? "",
    loadMoreVisible: Boolean(button),
    loadMoreDisabled: button?.disabled,
    loadMoreText: button?.textContent,
  };
}
async function closedTab() {
  await click([...container.querySelectorAll(".inbox-tab")].find(tab => tab.textContent === "已完成"));
}
function inboxCalls() {
  return state.api.listInbox.mock.calls.map(call => ({ status: call[1], cursor: call[2] }));
}
function submissionCalls() {
  return state.api.createRescheduleRequest.mock.calls.map(call => call[0]);
}

describe.each(["same_week", "cross_week"])("SUP076 current-game isolation %s", mode => {
  beforeEach(() => {
    state.mode = mode;
    state.api.getRescheduleTargets.mockImplementation(async (id: string) => [
      modeTarget(id.endsWith("A") ? "A" : "B"),
    ]);
  });

  it("CONTROL serial target selection and submission stays on B", async () => {
    await mountPage("create");
    await chooseGame(1);
    await click(submitButton());
    record("SUP076-control-" + mode, { ui: ui(), submissions: submissionCalls() });
    expect(submissionCalls()[0]).toMatchObject({
      game_id: "probe-game-B", target_period_id: "probe-period-B",
      process_route: mode === "same_week" ? "ORDINARY" : "HANDBOOK_REVIEW",
    });
  });

  it("late successful initial A response cannot enter B's submission", async () => {
    const a = deferred<any[]>(), b = deferred<any[]>();
    state.api.getRescheduleTargets.mockImplementation((id: string) => id.endsWith("A") ? a.promise : b.promise);
    await mountPage("create");
    await chooseGame(1);
    await finish(b, [modeTarget("B")]);
    const beforeOld = ui();
    await finish(a, [modeTarget("A")]);
    await click(submitButton());
    record("SUP076-late-success-" + mode, { beforeOld, afterOld: ui(), submissions: submissionCalls() });
    expect(submissionCalls()[0]).toMatchObject({ game_id: "probe-game-B", target_period_id: "probe-period-B" });
  });

  it("late failed initial A response cannot paint an error on successful B", async () => {
    const a = deferred<any[]>(), b = deferred<any[]>();
    state.api.getRescheduleTargets.mockImplementation((id: string) => id.endsWith("A") ? a.promise : b.promise);
    await mountPage("create");
    await chooseGame(1);
    await finish(b, [modeTarget("B")]);
    const beforeOld = ui();
    await fail(a, "OLD-A-ERROR");
    const afterOld = ui();
    await click(submitButton());
    record("SUP076-late-error-" + mode, { beforeOld, afterOld, submissions: submissionCalls() });
    expect(afterOld.text).not.toContain("OLD-A-ERROR");
    expect(submissionCalls()[0]).toMatchObject({ game_id: "probe-game-B", target_period_id: "probe-period-B" });
  });

  it.each(["success", "failure"])("old A %s/finally cannot end pending B loading or expose a stale submit", async outcome => {
    const a = deferred<any[]>(), b = deferred<any[]>();
    state.api.getRescheduleTargets.mockImplementation((id: string) => id.endsWith("A") ? a.promise : b.promise);
    await mountPage("create");
    await chooseGame(1);
    if (outcome === "success") await finish(a, [modeTarget("A")]);
    else await fail(a, "OLD-A-PENDING-ERROR");
    const whileBPending = ui();
    if (submitButton()) await click(submitButton());
    const prematureSubmissions = submissionCalls();
    await finish(b, [modeTarget("B")]);
    record("SUP076-old-finally-" + mode + "-" + outcome, {
      whileBPending, prematureSubmissions, afterB: ui(),
    });
    expect(whileBPending.gameIndex).toBe("1");
    expect(whileBPending.createLoading).toBe(true);
    expect(whileBPending.text).not.toContain("OLD-A-PENDING-ERROR");
    expect(prematureSubmissions).toEqual([]);
  });

  it("changeGame-to-changeGame responses are isolated after initial loading finished", async () => {
    const oldB = deferred<any[]>(), currentA = deferred<any[]>();
    state.api.getRescheduleTargets
      .mockResolvedValueOnce([modeTarget("A")])
      .mockReturnValueOnce(oldB.promise)
      .mockReturnValueOnce(currentA.promise);
    await mountPage("create");
    await chooseGame(1);
    await chooseGame(0);
    await finish(currentA, [modeTarget("A", "-current")]);
    const beforeOld = ui();
    await finish(oldB, [modeTarget("B")]);
    await click(submitButton());
    record("SUP076-change-change-" + mode, { beforeOld, afterOld: ui(), submissions: submissionCalls() });
    expect(submissionCalls()[0]).toMatchObject({ game_id: "probe-game-A", target_period_id: "probe-period-A-current" });
  });

  it("CONTROL a current target failure remains visible and retryable", async () => {
    state.api.getRescheduleTargets.mockRejectedValue(new Error("CURRENT-TARGET-ERROR"));
    await mountPage("create");
    record("SUP076-current-error-control-" + mode, ui());
    expect(ui().createLoading).toBe(false);
    expect(ui().text).toContain("CURRENT-TARGET-ERROR");
    expect(ui().text).toContain("重新加载");
    expect(submissionCalls()).toEqual([]);
  });
});

describe("SUP079 current-filter/page isolation", () => {
  for (const append of [false, true]) {
    for (const timing of ["after-current", "while-current-pending"] as const) {
      it.each(["success", "failure"])(
        (append ? "old load-more" : "old first page") + " %s " + timing + " cannot alter CLOSED",
        async outcome => {
          const old = deferred<any>(), closed = deferred<any>();
          state.api.listInbox.mockImplementation((_token: string, status: string, cursor: string) => {
            if (status === "CLOSED") return cursor ? Promise.resolve({ items: [], next_cursor: null }) : closed.promise;
            if (append && !cursor) return Promise.resolve({
              items: [task("OPEN-initial", "OPEN")], next_cursor: "open-more",
            });
            return old.promise;
          });
          await mountPage("inbox");
          if (append) await click(container.querySelector(".load-more"));
          await closedTab();
          if (timing === "after-current") await finish(closed, {
            items: [task("CLOSED-current", "CLOSED")], next_cursor: "closed-next",
          });
          const beforeOld = ui();
          if (outcome === "success") await finish(old, {
            items: [task("OPEN-obsolete", "OPEN")], next_cursor: "open-obsolete-next",
          });
          else await fail(old, "OLD-OPEN-ERROR");
          const afterOld = ui();
          let nextPageCall: unknown = null;
          if (timing === "after-current" && container.querySelector(".load-more")) {
            await click(container.querySelector(".load-more"));
            nextPageCall = inboxCalls().slice(-1)[0];
          }
          if (timing === "while-current-pending") await finish(closed, {
            items: [task("CLOSED-current", "CLOSED")], next_cursor: "closed-next",
          });
          record("SUP079-" + (append ? "append" : "first") + "-" + timing + "-" + outcome, {
            beforeOld, afterOld, nextPageCall, calls: inboxCalls(), final: ui(),
          });
          expect(afterOld.activeTab).toBe("已完成");
          if (timing === "while-current-pending") {
            expect(afterOld.inboxLoading).toBe(true);
            expect(afterOld.rows).toEqual([]);
          } else {
            expect(afterOld.rows).toEqual(["CLOSED-current"]);
            expect(nextPageCall).toEqual({ status: "CLOSED", cursor: "closed-next" });
          }
          expect(afterOld.inboxError).toBe("");
        },
      );
    }
  }

  it.each(["success", "failure"])("old append %s/finally cannot re-enable a new CLOSED append", async outcome => {
    const oldMore = deferred<any>(), closed = deferred<any>(), currentMore = deferred<any>();
    state.api.listInbox.mockImplementation((_token: string, status: string, cursor: string) => {
      if (status === "OPEN") return cursor ? oldMore.promise : Promise.resolve({
        items: [task("OPEN-initial", "OPEN")], next_cursor: "open-more",
      });
      return cursor ? currentMore.promise : closed.promise;
    });
    await mountPage("inbox");
    await click(container.querySelector(".load-more"));
    await closedTab();
    await finish(closed, { items: [task("CLOSED-first", "CLOSED")], next_cursor: "closed-more" });
    await click(container.querySelector(".load-more"));
    const beforeOld = ui();
    if (outcome === "success") await finish(oldMore, {
      items: [task("OPEN-obsolete-append", "OPEN")], next_cursor: "open-obsolete-next",
    });
    else await fail(oldMore, "OLD-OPEN-MORE-ERROR");
    const whileCurrentMorePending = ui();
    await finish(currentMore, { items: [task("CLOSED-second", "CLOSED")], next_cursor: null });
    record("SUP079-concurrent-append-finally-" + outcome, {
      beforeOld, whileCurrentMorePending, calls: inboxCalls(), final: ui(),
    });
    expect(beforeOld.loadMoreDisabled).toBe(true);
    expect(whileCurrentMorePending.loadMoreDisabled).toBe(true);
    expect(whileCurrentMorePending.loadMoreText).toContain("正在加载");
    expect(whileCurrentMorePending.rows).toEqual(["CLOSED-first"]);
    expect(whileCurrentMorePending.inboxError).toBe("");
  });

  it("CONTROL sequential CLOSED pagination appends only CLOSED rows", async () => {
    state.api.listInbox.mockImplementation(async (_token: string, status: string, cursor: string) => ({
      items: [task(status + (cursor ? "-second" : "-first"), status as "OPEN" | "CLOSED")],
      next_cursor: cursor ? null : status.toLowerCase() + "-next",
    }));
    await mountPage("inbox");
    await closedTab();
    await click(container.querySelector(".load-more"));
    record("SUP079-serial-control", { ui: ui(), calls: inboxCalls() });
    expect(taskTitles()).toEqual(["CLOSED-first", "CLOSED-second"]);
    expect(container.querySelector(".load-more")).toBeNull();
    expect(inboxCalls().slice(-1)[0]).toEqual({ status: "CLOSED", cursor: "closed-next" });
  });

  it("CONTROL a current inbox failure remains visible", async () => {
    state.api.listInbox.mockRejectedValue(new Error("CURRENT-INBOX-ERROR"));
    await mountPage("inbox");
    record("SUP079-current-error-control", ui());
    expect(ui().inboxError).toBe("CURRENT-INBOX-ERROR");
    expect(ui().inboxLoading).toBe(false);
    expect(ui().text).not.toContain("当前没有待处理任务");
  });
});
