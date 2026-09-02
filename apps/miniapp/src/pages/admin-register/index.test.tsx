// @vitest-environment jsdom
import React from "react";
import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({
  shown: null as null | (() => void),
  api: {
    getCurrentSeason: vi.fn(),
    getMiniAppMe: vi.fn(),
    registerAdmin: vi.fn(),
  },
}));

vi.mock("@tarojs/taro", () => ({
  default: { redirectTo: vi.fn(), switchTab: vi.fn() },
  useDidShow: (callback: () => void) => {
    state.shown = callback;
  },
}));
vi.mock("@tarojs/components", () => ({
  View: ({ children, className }: any) => <div className={className}>{children}</div>,
  Text: ({ children, className, onClick }: any) => (
    <span className={className} onClick={onClick}>{children}</span>
  ),
  Button: ({ children, disabled, onClick }: any) => (
    <button disabled={disabled} onClick={onClick}>{children}</button>
  ),
  Input: ({ onInput, placeholder, value }: any) => (
    <input
      aria-label={placeholder}
      value={value}
      onChange={(event) => onInput({ detail: { value: event.target.value } })}
    />
  ),
}));
vi.mock("../../api", () => ({ api: state.api }));
vi.mock("../../auth", () => ({ getMiniAppSession: () => "mini-session" }));

import AdminRegisterPage from "./index";

afterEach(cleanup);

beforeEach(() => {
  vi.clearAllMocks();
  state.shown = null;
  state.api.getMiniAppMe.mockResolvedValue({
    account: { id: "account-user", username: "测试用户", role: "USER", version: 1 },
    leader_binding: null,
    admin_role: null,
    next_game: null,
  });
  state.api.registerAdmin.mockResolvedValue({
    account: { id: "account-user", username: "测试用户", role: "ADMIN", version: 2 },
    leader_binding: null,
    admin_role: "ADMIN",
    next_game: null,
  });
});

describe("AdminRegisterPage", () => {
  it("registers with the global invite without loading a public season", async () => {
    render(<AdminRegisterPage />);
    await act(async () => state.shown?.());
    await screen.findByText("填写注册信息");

    fireEvent.change(screen.getByLabelText("管理员邀请码"), {
      target: { value: "global-admin-invite" },
    });
    fireEvent.change(screen.getByLabelText("设置网页密码（至少 4 个字符）"), {
      target: { value: "1234" },
    });
    fireEvent.change(screen.getByLabelText("再次输入网页密码"), {
      target: { value: "1234" },
    });
    fireEvent.click(screen.getByRole("button", { name: "确认注册" }));

    await waitFor(() => {
      expect(state.api.registerAdmin).toHaveBeenCalledWith(
        { invite_code: "global-admin-invite", password: "1234" },
        "mini-session",
      );
    });
    expect(state.api.getCurrentSeason).not.toHaveBeenCalled();
    expect(await screen.findByText("管理员身份已生效")).toBeInTheDocument();
  });
});
