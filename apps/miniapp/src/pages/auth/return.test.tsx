// @vitest-environment jsdom
import React, { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { authReturnUrl } from "../../authReturn";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const state = vi.hoisted(() => ({
  params: {} as Record<string, string | undefined>,
  exchange: vi.fn(),
  completeProfile: vi.fn(),
  saveSession: vi.fn(),
  redirectTo: vi.fn(),
  switchTab: vi.fn(),
}));

vi.mock("@tarojs/components", () => ({
  View: ({ children }: React.PropsWithChildren) => <div>{children}</div>,
  Text: ({ children }: React.PropsWithChildren) => <span>{children}</span>,
  Button: ({ children, onClick, disabled }: React.PropsWithChildren<{ onClick?: () => void; disabled?: boolean }>) => <button disabled={disabled} onClick={onClick}>{children}</button>,
  Input: ({ value, onInput }: { value: string; onInput: (event: { detail: { value: string } }) => void }) => <input value={value} onInput={(event) => onInput({ detail: { value: event.currentTarget.value } })} onChange={() => {}} />,
}));
vi.mock("@tarojs/taro", () => ({ default: {
  getCurrentInstance: () => ({ router: { params: state.params } }),
  redirectTo: state.redirectTo,
  switchTab: state.switchTab,
} }));
vi.mock("../../auth", () => ({ exchangeCurrentWeChat: state.exchange, saveMiniAppSession: state.saveSession }));
vi.mock("../../api", () => ({ api: { completeProfile: state.completeProfile } }));

import AuthPage from "./index";

const me = { account: { id: "account", username: "测试领队", role: "USER", version: 1 }, leader_binding: null, admin_role: null, next_game: null };
let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  state.params = {};
  state.exchange.mockReset().mockResolvedValue({ requires_profile: false, me, session_token: "session" });
  state.completeProfile.mockReset().mockResolvedValue({ me, session_token: "new-session" });
  state.saveSession.mockReset();
  state.redirectTo.mockReset().mockResolvedValue({});
  state.switchTab.mockReset().mockResolvedValue({});
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); });

async function mountAndLogin() {
  await act(async () => root.render(<AuthPage />));
  await act(async () => container.querySelector("button")!.click());
}

describe.each([
  ["reschedule_ordinary", "/pages/reschedule-create/index?mode=same_week"],
  ["reschedule_handbook", "/pages/reschedule-create/index?mode=cross_week"],
  ["reschedule_requests", "/pages/reschedule-requests/index"],
])("login return %s", (entry, destination) => {
  it("returns an existing account to the original private entry", async () => {
    state.params = { return_to: entry };
    await mountAndLogin();
    expect(state.redirectTo).toHaveBeenCalledExactlyOnceWith({ url: destination });
    expect(state.switchTab).not.toHaveBeenCalled();
  });
  it("retains the entry through first-time nickname setup", async () => {
    state.params = { return_to: entry };
    state.exchange.mockResolvedValue({ requires_profile: true, profile_ticket: "ticket", me: null });
    await mountAndLogin();
    expect(state.redirectTo).not.toHaveBeenCalled();
    const input = container.querySelector("input")!;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, "新昵称");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => container.querySelector("button")!.click());
    expect(state.completeProfile).toHaveBeenCalledWith("ticket", "新昵称");
    expect(state.saveSession).toHaveBeenCalledWith("new-session");
    expect(state.redirectTo).toHaveBeenCalledExactlyOnceWith({ url: destination });
  });
});

it("preserves only a valid request ID, and rejects arbitrary redirects and prototype names", async () => {
  const id = "b2a3f8c1-7ad1-4a0f-b955-e558b4fe36e4";
  expect(authReturnUrl({ return_to: "reschedule_requests", request_id: id })).toBe(`/pages/reschedule-requests/index?request_id=${id}`);
  for (const request_id of ["../admin", `${id}&redirect=https://example.invalid`, "not-a-uuid"]) {
    expect(authReturnUrl({ return_to: "reschedule_requests", request_id })).toBe("/pages/reschedule-requests/index");
  }
  for (const return_to of ["https://example.invalid", "/pages/admin/index", "__proto__", "constructor"]) {
    expect(authReturnUrl({ return_to })).toBeNull();
  }
  state.params = { return_to: "https://example.invalid", redirect: "/pages/admin/index" };
  await mountAndLogin();
  expect(state.redirectTo).not.toHaveBeenCalled();
  expect(state.switchTab).toHaveBeenCalledExactlyOnceWith({ url: "/pages/mine/index" });
});

it.each(["用户取消登录", "微信登录暂不可用"])("keeps the original entry available after %s without claiming success", async (message) => {
  state.params = { return_to: "reschedule_handbook" };
  state.exchange.mockRejectedValueOnce(new Error(message));
  await mountAndLogin();
  expect(container.textContent).toContain(message);
  expect(state.redirectTo).not.toHaveBeenCalled();
  expect(state.switchTab).not.toHaveBeenCalled();
  await act(async () => container.querySelector("button")!.click());
  expect(state.redirectTo).toHaveBeenCalledExactlyOnceWith({ url: "/pages/reschedule-create/index?mode=cross_week" });
});

it.each(["leader", "admin"])("preserves the existing %s registration flow", async (intent) => {
  state.params = { intent };
  await mountAndLogin();
  expect(state.redirectTo).toHaveBeenCalledExactlyOnceWith({ url: `/pages/${intent}-register/index` });
});

it.each(["success", "failure"])("ignores a late login %s after leaving the login page", async (outcome) => {
  let resolve!: (value: unknown) => void;
  let reject!: (reason: unknown) => void;
  state.exchange.mockReturnValueOnce(new Promise((yes, no) => { resolve = yes; reject = no; }));
  state.params = { return_to: "reschedule_ordinary" };
  await mountAndLogin();
  await act(async () => root.render(<div>different page</div>));
  await act(async () => outcome === "success" ? resolve({ requires_profile: false, me }) : reject(new Error("late error")));
  expect(container.textContent).toBe("different page");
  expect(state.redirectTo).not.toHaveBeenCalled();
  expect(state.switchTab).not.toHaveBeenCalled();
});

it("only performs one exchange while a login is pending", async () => {
  state.exchange.mockReturnValue(new Promise(() => {}));
  await act(async () => root.render(<AuthPage />));
  await act(async () => { container.querySelector("button")!.click(); container.querySelector("button")!.click(); });
  expect(state.exchange).toHaveBeenCalledOnce();
});
