// @vitest-environment jsdom
import React from "react";
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
const state = vi.hoisted(() => ({ token: "synthetic-session", context: vi.fn(), detail: vi.fn(), history: vi.fn(), save: vi.fn(),
  modal: vi.fn(), hide: null as (() => void) | null }));
vi.mock("@tarojs/taro", async () => { const react = await import("react"); return {
  default: { showModal: state.modal }, useRouter: () => ({ params: {} }),
  useDidShow: (fn: () => void) => react.useEffect(fn, []), useDidHide: (fn: () => void) => { state.hide = fn; },
}; });
vi.mock("@tarojs/components", () => ({ View: ({ children }: any) => <div>{children}</div>, Text: ({ children }: any) => <span>{children}</span>,
  Button: ({ children, ...props }: any) => <button {...props}>{children}</button>,
  Picker: ({ children }: any) => <div>{children}</div>,
  Input: ({ onInput, maxlength, ...props }: any) => <input {...props} maxLength={maxlength} onChange={(e) => onInput({ detail: { value: e.target.value } })} />,
}));
vi.mock("../../api", () => ({ api: { getRegistrationContext: state.context, getRegistration: state.detail, getRegistrationHistory: state.history, saveRegistration: state.save } }));
vi.mock("../../auth", () => ({ getMiniAppSession: () => state.token }));
vi.mock("../../navigation", () => ({ navigateToOnce: vi.fn() }));
vi.mock("../../components/notification-subscribe", () => ({ NotificationSubscribe: () => null }));
import RegistrationPage from "./index";
const form = { team_name: "合成球队", uniform_color: "白", leader_name: "合成领队", phone: "10000000000", email: "local@example.invalid", players: [] };
const registration = { id: "r1", version: 2, status: "RETURNED", team_name: "合成球队", team_id: null, form, approved_form: null };
beforeEach(() => { vi.clearAllMocks(); state.token = "synthetic-session";
  state.context.mockResolvedValue({ season_id: "s1", season_name: "合成赛季", seasons: [{ id: "s1", name: "合成赛季" }], policy: { open: true }, registration, handovers: [] });
  state.detail.mockResolvedValue(registration); state.history.mockResolvedValue({ items: [{ id: "h1", version: 2, decision: "RETURNED", review_reason: "请核对名单" }], total: 1, page: 1, page_size: 10 });
  state.save.mockResolvedValue(registration); state.modal.mockResolvedValue({ confirm: true });
});
afterEach(cleanup);
describe("领队报名", () => {
  it("显示退回意见，修改后携带版本重新提交", async () => {
    render(<RegistrationPage />); await screen.findByText("审核意见：请核对名单");
    fireEvent.change(screen.getByPlaceholderText("填写球服颜色"), { target: { value: "蓝" } });
    fireEvent.click(screen.getByText("提交报名审核"));
    await waitFor(() => expect(state.save).toHaveBeenCalledOnce());
    expect(state.save.mock.calls[0][1]).toMatchObject({ expected_version: 2, submit: true, form: { uniform_color: "蓝" } });
  });
  it("截止后不显示修改和重新提交入口", async () => {
    state.context.mockResolvedValueOnce({ season_id: "s1", season_name: "合成赛季", seasons: [{ id: "s1", name: "合成赛季" }], policy: { open: false }, registration, handovers: [] });
    render(<RegistrationPage />); await screen.findByText("当前报名未开放或已截止");
    expect(screen.queryByText("提交报名审核")).toBeNull(); expect(screen.queryByText("添加队员")).toBeNull();
  });
  it("未知保存结果保留原请求和幂等键", async () => {
    state.save.mockRejectedValueOnce(new TypeError("网络中断")); render(<RegistrationPage />);
    fireEvent.click(await screen.findByText("保存草稿"));
    fireEvent.click(await screen.findByText("核对上次操作"));
    await waitFor(() => expect(state.save).toHaveBeenCalledTimes(2));
    expect(state.save.mock.calls[1]).toEqual(state.save.mock.calls[0]);
  });
  it("离开页面立即清除私密资料", async () => {
    render(<RegistrationPage />); await screen.findByDisplayValue("合成领队");
    const { act } = await import("react"); act(() => { state.hide?.(); });
    expect(screen.queryByDisplayValue("10000000000")).toBeNull();
  });
});
