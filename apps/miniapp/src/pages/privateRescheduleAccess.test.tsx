// @vitest-environment jsdom
import React, { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@pkuba/api-client";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const state = vi.hoisted(() => ({
  token: "",
  mode: "same_week",
  requestId: "",
  show: undefined as (() => void) | undefined,
  api: {
    getEligibleRescheduleGames: vi.fn(),
    getRescheduleTargets: vi.fn(),
    listRescheduleRequests: vi.fn(),
    createRescheduleRequest: vi.fn(),
  },
  switchTab: vi.fn(),
  navigateTo: vi.fn(),
  setNavigationBarTitle: vi.fn(),
  showToast: vi.fn(),
}));

vi.mock("@tarojs/components", () => {
  const Wrapper = ({ children, className }: React.PropsWithChildren<{ className?: string }>) => React.createElement("div", { className }, children);
  return {
    View: Wrapper,
    Text: Wrapper,
    Picker: Wrapper,
    CheckboxGroup: Wrapper,
    Label: Wrapper,
    Checkbox: Wrapper,
    Button: ({ children, onClick, disabled }: React.PropsWithChildren<{ onClick?: () => void; disabled?: boolean }>) => React.createElement("button", { onClick, disabled }, children),
  };
});
vi.mock("@tarojs/taro", async () => {
  const hooks = await import("react");
  return {
    default: {
      switchTab: state.switchTab,
      navigateTo: state.navigateTo,
      setNavigationBarTitle: state.setNavigationBarTitle,
      showToast: state.showToast,
    },
    useRouter: () => ({ params: { mode: state.mode, request_id: state.requestId } }),
    useDidShow: (callback: () => void) => {
      const latest = hooks.useRef(callback);
      latest.current = callback;
      hooks.useEffect(() => {
        state.show = () => latest.current();
        state.show();
        return () => { state.show = undefined; };
      }, []);
    },
  };
});
vi.mock("../api", () => ({ api: state.api }));
vi.mock("../auth", () => ({ getMiniAppSession: () => state.token }));

import RescheduleCreatePage from "./reschedule-create/index";
import RescheduleRequestsPage from "./reschedule-requests/index";
import { authReturnUrl } from "../authReturn";

const cases = [
  { page: "create", mode: "same_week", empty: "当前没有满足政策和截止时间的可调比赛。" },
  { page: "create", mode: "cross_week", empty: "当前没有满足政策和截止时间的可调比赛。" },
  { page: "list", mode: "same_week", empty: "这里暂时没有申请。" },
] as const;
type PageCase = typeof cases[number];

const game = {
  id: "private-game", version: 1, division_name: "男甲", division_gender: "MEN",
  home_name: "私有主队", away_name: "私有客队", venue_name: "五四东一",
  date: "2026-04-11", start_time: "12:50", group_name: "A 组",
};
const request = {
  id: "private-request", game, version: 1, is_terminal: false,
  original_home_name: game.home_name, original_away_name: game.away_name,
  original_date: game.date, original_start_time: game.start_time, original_venue_name: game.venue_name,
  target_date: "2026-04-12", target_start_time: "12:50", target_venue_name: null,
  status: "WAITING_OPPONENT", status_label: "等待对手确认", requester_team_name: game.home_name,
  request_type: "SAME_WEEK", request_type_label: "同一自然周",
  process_route: "ORDINARY", process_route_label: "普通流程", review_classification_label: "",
  confirmations: [], actions: [],
};

let container: HTMLDivElement;
let root: Root;

async function flush() {
  await act(async () => {
    await Promise.resolve();
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

async function mount(entry: PageCase) {
  state.mode = entry.mode;
  await act(async () => root.render(entry.page === "create" ? <RescheduleCreatePage /> : <RescheduleRequestsPage />));
  await flush();
}

function fetchMock(entry: PageCase) {
  return entry.page === "create" ? state.api.getEligibleRescheduleGames : state.api.listRescheduleRequests;
}

beforeEach(() => {
  state.token = "";
  state.requestId = "";
  state.show = undefined;
  for (const method of Object.values(state.api)) method.mockReset().mockResolvedValue([]);
  state.switchTab.mockReset().mockResolvedValue({});
  state.navigateTo.mockReset().mockResolvedValue({});
  state.setNavigationBarTitle.mockReset().mockResolvedValue({});
  state.showToast.mockReset();
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

describe.each(cases)("private reschedule $page / $mode", (entry) => {
  it("shows a login entry instead of a business-empty result without a token", async () => {
    await mount(entry);
    expect(container.textContent).toContain("请先登录");
    expect(container.textContent).not.toContain(entry.empty);
    expect(fetchMock(entry)).not.toHaveBeenCalled();
    const button = [...container.querySelectorAll("button")].find((item) => item.textContent === "登录并继续");
    expect(button).toBeDefined();
    await act(async () => button!.click());
    const destination = state.navigateTo.mock.calls[0][0].url as string;
    const params = Object.fromEntries(new URLSearchParams(destination.split("?")[1]));
    expect(authReturnUrl(params)).toBe(entry.page === "list"
      ? "/pages/reschedule-requests/index"
      : `/pages/reschedule-create/index?mode=${entry.mode}`);
    expect(state.switchTab).not.toHaveBeenCalled();
  });

  it("reloads after identity restoration and only then shows a genuine empty result", async () => {
    await mount(entry);
    state.token = "restored-session";
    await act(async () => state.show?.());
    await flush();
    expect(fetchMock(entry)).toHaveBeenCalledWith("restored-session");
    expect(container.textContent).toContain(entry.empty);
    expect(container.textContent).not.toContain("请先登录");
  });

  it.each([401, 403])("distinguishes HTTP %i from an empty business list", async (status) => {
    state.token = "rejected-session";
    fetchMock(entry).mockRejectedValue(new ApiError("server auth message", status));
    await mount(entry);
    expect(container.textContent).toContain(status === 401 ? "登录状态已失效" : "没有此操作权限");
    expect(container.textContent).not.toContain(entry.empty);
    expect(container.textContent).toContain(status === 401 ? "登录并继续" : "前往我的");
    if (status === 403) {
      await act(async () => [...container.querySelectorAll("button")].find((button) => button.textContent === "前往我的")!.click());
      expect(state.switchTab).toHaveBeenCalledWith({ url: "/pages/mine/index" });
      expect(state.navigateTo).not.toHaveBeenCalled();
    }
  });

  it("shows a retryable API error and recovers without pretending there are no games", async () => {
    state.token = "session";
    fetchMock(entry).mockRejectedValueOnce(new ApiError("网络暂不可用", 503));
    await mount(entry);
    expect(container.textContent).toContain("网络暂不可用");
    expect(container.textContent).not.toContain(entry.empty);
    const retry = [...container.querySelectorAll("button")].find((item) => item.textContent === "重新加载");
    await act(async () => retry!.click());
    await flush();
    expect(container.textContent).toContain(entry.empty);
    expect(container.textContent).not.toContain("网络暂不可用");
  });

  it("clears previously loaded private content after session loss", async () => {
    state.token = "session";
    fetchMock(entry).mockResolvedValue([entry.page === "create" ? game : request]);
    await mount(entry);
    expect(container.textContent).toContain("私有主队");
    state.token = "";
    await act(async () => state.show?.());
    await flush();
    expect(container.textContent).not.toContain("私有主队");
    expect(container.textContent).not.toContain(entry.empty);
    expect(container.textContent).toContain("请先登录");
  });

  it("does not resurrect private content from a response pending before logout", async () => {
    state.token = "session";
    let complete!: (rows: unknown[]) => void;
    fetchMock(entry).mockReturnValue(new Promise((resolve) => { complete = resolve; }));
    await mount(entry);
    state.token = "";
    await act(async () => state.show?.());
    await act(async () => complete([entry.page === "create" ? game : request]));
    await flush();
    expect(container.textContent).not.toContain("私有主队");
    expect(container.textContent).not.toContain(entry.empty);
    expect(container.textContent).toContain("请先登录");
  });

  it.each(["success", "failure"])("ignores old identity %s and finally while the new identity is loading", async (outcome) => {
    state.token = "old-session";
    let resolveOld!: (rows: unknown[]) => void;
    let rejectOld!: (reason: unknown) => void;
    let resolveNew!: (rows: unknown[]) => void;
    fetchMock(entry).mockReturnValueOnce(new Promise((resolve, reject) => { resolveOld = resolve; rejectOld = reject; }));
    fetchMock(entry).mockReturnValueOnce(new Promise((resolve) => { resolveNew = resolve; }));
    await mount(entry);
    state.token = "new-session";
    await act(async () => state.show?.());
    await act(async () => outcome === "success"
      ? resolveOld([entry.page === "create" ? game : request])
      : rejectOld(new ApiError("expired old identity", 401)));
    await flush();
    expect(container.textContent).toContain(entry.page === "create" ? "正在核对" : "正在读取");
    expect(container.textContent).not.toContain("私有主队");
    expect(container.textContent).not.toContain("登录状态已失效");
    expect(container.textContent).not.toContain(entry.empty);
    await act(async () => resolveNew([]));
    await flush();
    expect(container.textContent).toContain(entry.empty);
  });

  it("hides the old account content immediately when switching to another logged-in account", async () => {
    state.token = "old-session";
    fetchMock(entry).mockResolvedValueOnce([entry.page === "create" ? game : request]);
    await mount(entry);
    expect(container.textContent).toContain("私有主队");
    fetchMock(entry).mockReturnValueOnce(new Promise(() => {}));
    state.token = "new-session";
    await act(async () => state.show?.());
    expect(container.textContent).not.toContain("私有主队");
    expect(container.textContent).toContain(entry.page === "create" ? "正在核对" : "正在读取");
  });

  it.each(["success", "failure"])("does not update or navigate after unmount with a pending %s", async (outcome) => {
    state.token = "session";
    let resolve!: (rows: unknown[]) => void;
    let reject!: (reason: unknown) => void;
    fetchMock(entry).mockReturnValueOnce(new Promise((yes, no) => { resolve = yes; reject = no; }));
    await mount(entry);
    await act(async () => root.render(<div>replacement page</div>));
    await act(async () => outcome === "success"
      ? resolve([entry.page === "create" ? game : request])
      : reject(new ApiError("expired", 401)));
    await flush();
    expect(container.textContent).toBe("replacement page");
    expect(state.navigateTo).not.toHaveBeenCalled();
    expect(state.switchTab).not.toHaveBeenCalled();
  });
});

it("keeps a valid request deep link across the explicit login action", async () => {
  state.requestId = "b2a3f8c1-7ad1-4a0f-b955-e558b4fe36e4";
  await mount(cases[2]);
  await act(async () => [...container.querySelectorAll("button")].find((button) => button.textContent === "登录并继续")!.click());
  expect(state.navigateTo).toHaveBeenCalledWith({
    url: `/pages/auth/index?return_to=reschedule_requests&request_id=${state.requestId}`,
  });
});

it("clears selected games when the target request reports expired identity", async () => {
  state.token = "session";
  state.api.getEligibleRescheduleGames.mockResolvedValue([game]);
  state.api.getRescheduleTargets.mockRejectedValue(new ApiError("expired", 401));
  await mount(cases[0]);
  expect(container.textContent).not.toContain("私有主队");
  expect(container.textContent).toContain("登录状态已失效");
  expect(container.textContent).not.toContain(cases[0].empty);
});
