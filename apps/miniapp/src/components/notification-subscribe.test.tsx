// @vitest-environment jsdom
import React from "react";
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
const state = vi.hoisted(() => ({ config: vi.fn(), save: vi.fn(), request: vi.fn() }));
vi.mock("@tarojs/taro", async () => { const react = await import("react"); return {
  default: { requestSubscribeMessage: state.request }, useDidShow: (fn: () => void) => react.useEffect(fn, []), useDidHide: () => {},
}; });
vi.mock("@tarojs/components", () => ({ View: ({ children }: any) => <div>{children}</div>, Text: ({ children }: any) => <span>{children}</span>, Button: ({ children, ...props }: any) => <button {...props}>{children}</button> }));
vi.mock("../api", () => ({ api: { getNotificationConfig: state.config, saveNotificationChoice: state.save } }));
vi.mock("../auth", () => ({ getMiniAppSession: () => "synthetic-session" }));
import { NotificationSubscribe } from "./notification-subscribe";
beforeEach(() => { vi.clearAllMocks(); state.config.mockResolvedValue({ templates: [{ key: "game", template_id: "synthetic-template", type: "ONCE", name: "比赛变动提醒" }] });
  state.save.mockResolvedValue({ saved: true }); state.request.mockResolvedValue({ "synthetic-template": "accept" }); });
afterEach(cleanup);
it("仅用户点击才向微信申请订阅，并同步真实选择", async () => {
  render(<NotificationSubscribe />); const button = await screen.findByText("接收比赛变动提醒"); expect(state.request).not.toHaveBeenCalled();
  fireEvent.click(button); await waitFor(() => expect(state.save).toHaveBeenCalledOnce());
  expect(state.request).toHaveBeenCalledWith({ tmplIds: ["synthetic-template"] });
  expect(state.save.mock.calls[0][0]).toEqual({ template_id: "synthetic-template", choice: "accept" });
});
it("同步失败时重试原选择，不重复弹出订阅请求", async () => {
  state.save.mockRejectedValueOnce(new Error("网络中断")); render(<NotificationSubscribe />);
  fireEvent.click(await screen.findByText("接收比赛变动提醒")); fireEvent.click(await screen.findByText("核对订阅选择"));
  await waitFor(() => expect(state.save).toHaveBeenCalledTimes(2)); expect(state.request).toHaveBeenCalledOnce();
  expect(state.save.mock.calls[1]).toEqual(state.save.mock.calls[0]);
});
