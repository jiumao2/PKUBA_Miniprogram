// @vitest-environment jsdom
import React from "react";
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({
  token: "person-a", getRefereeContext: vi.fn(), listRefereeAvailability: vi.fn(),
  listRefereeAssignments: vi.fn(), getRefereeCrew: vi.fn(),
  setRefereeAvailability: vi.fn(), hide: null as (() => void) | null,
}));
vi.mock("@tarojs/taro", async () => {
  const react = await import("react");
  return {
    useDidShow: (callback: () => void) => react.useEffect(callback, []),
    useDidHide: (callback: () => void) => { state.hide = callback; },
  };
});
vi.mock("@tarojs/components", () => ({
  View: ({ children, ...props }: any) => <div {...props}>{children}</div>,
  Text: ({ children }: any) => <span>{children}</span>,
  Button: ({ children, size, ...props }: any) => <button {...props}>{children}</button>,
  Picker: ({ children, mode, onChange, disabled }: any) => <div>
    {children}<input aria-label={mode === "date" ? "选择报名日期" : "选择报名时段"}
      disabled={disabled} onChange={(event) => onChange({ detail: { value: event.target.value } })} />
  </div>,
}));
vi.mock("../../api", () => ({ api: {
  getRefereeContext: state.getRefereeContext, listRefereeAvailability: state.listRefereeAvailability,
  listRefereeAssignments: state.listRefereeAssignments, getRefereeCrew: state.getRefereeCrew,
  setRefereeAvailability: state.setRefereeAvailability,
} }));
vi.mock("../../auth", () => ({ getMiniAppSession: () => state.token }));
vi.mock("../../navigation", () => ({ navigateToOnce: vi.fn() }));
vi.mock("../../components/notification-subscribe", () => ({ NotificationSubscribe: () => null }));

import RefereePage from "./index";

const context = {
  season_id: "s1", season_name: "测试赛季", starts_on: "2026-11-01", ends_on: "2026-12-01",
  qualification: { id: "r1", active: true, version: 1 },
  periods: [{ id: "p1", name: "第一时段", start_time: "12:50:00" }],
};
const empty = { items: [], total: 0, page: 1, page_size: 10 };
beforeEach(() => {
  vi.clearAllMocks(); state.token = "person-a";
  state.getRefereeContext.mockResolvedValue(context);
  state.listRefereeAvailability.mockResolvedValue(empty);
  state.listRefereeAssignments.mockResolvedValue(empty);
  state.setRefereeAvailability.mockResolvedValue({});
});
afterEach(cleanup);

describe("执裁工作台", () => {
  it("只用固定时段报名，网络失败后核对原请求", async () => {
    state.setRefereeAvailability.mockRejectedValueOnce(new TypeError("网络中断"));
    render(<RefereePage />);
    const date = await screen.findByLabelText("选择报名日期");
    fireEvent.change(date, { target: { value: "2026-11-02" } });
    fireEvent.click(screen.getByText("报名此时段"));
    fireEvent.click(await screen.findByText("核对上次操作"));
    await waitFor(() => expect(state.setRefereeAvailability).toHaveBeenCalledTimes(2));
    expect(state.setRefereeAvailability.mock.calls[0]).toEqual(state.setRefereeAvailability.mock.calls[1]);
    expect(state.setRefereeAvailability.mock.calls[0].slice(0, 3)).toEqual([
      "s1", { date: "2026-11-02", period_id: "p1", active: true, expected_version: 0 }, "person-a",
    ]);
  });

  it("未开通资格时不能提交报名", async () => {
    state.getRefereeContext.mockResolvedValue({ ...context, qualification: null });
    render(<RefereePage />);
    expect(await screen.findByText(/资格尚未开通/)).toBeInTheDocument();
    expect(screen.queryByText("报名此时段")).toBeNull();
  });

  it("登录身份改变后忽略上一账号的迟到响应", async () => {
    let resolveAssignments!: (value: unknown) => void;
    state.listRefereeAssignments.mockReturnValue(new Promise((resolve) => { resolveAssignments = resolve; }));
    render(<RefereePage />);
    await waitFor(() => expect(state.listRefereeAssignments).toHaveBeenCalledOnce());
    state.token = "person-b";
    resolveAssignments({ ...empty, total: 1, items: [{ id: "private-old", role_name: "旧账号私有岗位" }] });
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(screen.queryByText("旧账号私有岗位")).toBeNull();
  });

  it("只在有效选派下提供本场组成员查看入口", async () => {
    state.listRefereeAssignments.mockResolvedValue({ ...empty, total: 1, items: [{
      id: "assignment-1", game_id: "game-1", role_name: "主裁判", active: true,
      home: "甲队", away: "乙队", date: "2026-11-02", period_name: "第一时段",
      venue_name: "测试场地", game_code: "G001", schedule_changed: false, game_status: "SCHEDULED",
    }] });
    state.getRefereeCrew.mockResolvedValue({ game_id: "game-1", members: [
      { role: "SCORER", role_name: "记录员", username: "本场其他成员" },
    ] });
    render(<RefereePage />);
    const button = await screen.findByText("查看本场执裁组");
    expect(screen.queryByText("记录员：本场其他成员")).toBeNull();
    fireEvent.click(button);
    expect(await screen.findByText("记录员：本场其他成员")).toBeInTheDocument();
    expect(state.getRefereeCrew).toHaveBeenCalledWith("game-1", "person-a");
  });
});
