// @vitest-environment jsdom

import React, { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
  .IS_REACT_ACT_ENVIRONMENT = true;

const testState = vi.hoisted(() => ({
  mode: "cross_week",
  api: {
    getEligibleRescheduleGames: vi.fn(),
    getRescheduleTargets: vi.fn(),
    createRescheduleRequest: vi.fn(),
    getMiniAppMe: vi.fn(),
    getGames: vi.fn(),
    listRescheduleRequests: vi.fn(),
  },
  navigateToOnce: vi.fn(),
  setNavigationBarTitle: vi.fn(),
  showModal: vi.fn(),
  showToast: vi.fn(),
  redirectTo: vi.fn(),
}));

vi.mock("@tarojs/components", () => ({
  Button: ({ children, className, disabled, onClick }: React.PropsWithChildren<{
    className?: string;
    disabled?: boolean;
    onClick?: () => void;
  }>) => React.createElement("button", { className, disabled, onClick }, children),
  Picker: ({ children }: React.PropsWithChildren) => React.createElement("div", null, children),
  Text: ({ children, className }: React.PropsWithChildren<{ className?: string }>) => (
    React.createElement("span", { className }, children)
  ),
  View: ({ children, className }: React.PropsWithChildren<{ className?: string }>) => (
    React.createElement("div", { className }, children)
  ),
}));

vi.mock("@tarojs/taro", async () => {
  const ReactModule = await import("react");
  const taro = {
    setNavigationBarTitle: testState.setNavigationBarTitle,
    showModal: testState.showModal,
    showToast: testState.showToast,
    redirectTo: testState.redirectTo,
  };
  return {
    default: taro,
    useDidShow: (callback: () => void) => ReactModule.useEffect(callback, []),
    useRouter: () => ({ params: { mode: testState.mode } }),
  };
});

vi.mock("../api", () => ({ api: testState.api }));
vi.mock("../auth", () => ({ getMiniAppSession: () => "miniapp-session" }));
vi.mock("../navigation", () => ({ navigateToOnce: testState.navigateToOnce }));
vi.mock("../components/game-timeline", () => ({
  GameTimeline: () => React.createElement("div", null, "比赛时间轴"),
}));

import LeaderWorkspacePage from "./leader/index";
import RescheduleCreatePage from "./reschedule-create/index";

const game = {
  id: "game-1",
  code: "MA-A1-A2",
  division_name: "男甲",
  division_gender: "MEN",
  group_name: "A 组",
  date: "2026-04-11",
  start_time: "12:50",
  venue_name: "五四东一",
  home_name: "法学院",
  away_name: "经济学院",
  leader_adjustable: true,
  status: "SCHEDULED",
  version: 2,
};

const targets = [
  {
    date: "2026-04-12",
    period_id: "period-same",
    period_code: "WEEKEND_1",
    period_name: "周末第一时段",
    start_time: "12:50",
    request_type: "SAME_WEEK",
    request_type_label: "同一自然周",
    process_route: "HANDBOOK_REVIEW",
    process_route_label: "参赛手册审核",
    submit_deadline: "2026-04-08T16:00:00Z",
    confirmation_deadline: "2026-04-09T16:00:00Z",
  },
  {
    date: "2026-04-18",
    period_id: "period-cross",
    period_code: "WEEKEND_1",
    period_name: "周末第一时段",
    start_time: "12:50",
    request_type: "CROSS_WEEK",
    request_type_label: "跨自然周",
    process_route: "HANDBOOK_REVIEW",
    process_route_label: "参赛手册审核",
    submit_deadline: "2026-04-08T16:00:00Z",
    confirmation_deadline: "2026-04-09T16:00:00Z",
  },
];

let container: HTMLDivElement;
let root: Root;

async function flush() {
  await act(async () => {
    await Promise.resolve();
    await new Promise((resolve) => window.setTimeout(resolve, 0));
  });
}

beforeEach(() => {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  testState.mode = "cross_week";
  Object.values(testState.api).forEach((mock) => mock.mockReset());
  testState.navigateToOnce.mockReset();
  testState.setNavigationBarTitle.mockReset();
  testState.showModal.mockReset().mockResolvedValue({ confirm: true, cancel: false });
  testState.showToast.mockReset();
  testState.redirectTo.mockReset();
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

describe("leader reschedule pages", () => {
  it("renders both leader entry buttons and navigates to their distinct routes", async () => {
    testState.api.getMiniAppMe.mockResolvedValue({
      account: { id: "account-1", username: "领队甲", role: "USER", active: true },
      leader_binding: {
        team_id: "team-1",
        team_name: "法学院",
        division_id: "division-1",
        division_name: "男甲",
        division_gender: "MEN",
      },
      admin: null,
    });
    testState.api.getGames.mockResolvedValue([]);
    testState.api.listRescheduleRequests.mockResolvedValue([]);

    await act(async () => root.render(<LeaderWorkspacePage />));
    await flush();
    const buttons = [...container.querySelectorAll("button")];
    const ordinary = buttons.find((item) => item.textContent?.includes("普通调赛"));
    const handbook = buttons.find((item) => item.textContent?.includes("跨周调赛"));
    expect(ordinary?.textContent).toContain("普通调赛");
    expect(handbook?.textContent).toContain("是否跨轮次由管理员审核");

    await act(async () => ordinary?.click());
    await act(async () => handbook?.click());
    expect(testState.navigateToOnce).toHaveBeenNthCalledWith(
      1,
      "/pages/reschedule-create/index?mode=same_week",
    );
    expect(testState.navigateToOnce).toHaveBeenNthCalledWith(
      2,
      "/pages/reschedule-create/index?mode=cross_week",
    );
  });

  it("requests all handbook targets and submits the persisted handbook route", async () => {
    testState.api.getEligibleRescheduleGames.mockResolvedValue([game]);
    testState.api.getRescheduleTargets.mockResolvedValue(targets);
    testState.api.createRescheduleRequest.mockResolvedValue({ id: "request-1" });

    await act(async () => root.render(<RescheduleCreatePage />));
    await flush();
    expect(testState.api.getRescheduleTargets).toHaveBeenCalledWith(
      "game-1",
      "HANDBOOK_REVIEW",
      "miniapp-session",
    );
    expect(container.textContent).toContain("可选择本周或跨周时段");
    expect(container.textContent).toContain("是否跨轮次由管理员审核");
    expect(container.textContent).toContain("同一自然周");

    const submit = [...container.querySelectorAll("button")].find(
      (item) => item.textContent === "提交申请",
    );
    await act(async () => submit?.click());
    await flush();
    expect(testState.api.createRescheduleRequest).toHaveBeenCalledWith(
      {
        game_id: "game-1",
        expected_game_version: 2,
        target_date: "2026-04-12",
        target_period_id: "period-same",
        process_route: "HANDBOOK_REVIEW",
      },
      "miniapp-session",
    );
  });
});
