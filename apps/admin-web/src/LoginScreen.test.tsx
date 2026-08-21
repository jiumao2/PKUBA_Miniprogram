import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { createAdminClient } from "@pkuba/api-client";

import { LoginScreen } from "./LoginScreen";

vi.mock("qrcode", () => ({
  toString: vi.fn().mockResolvedValue('<svg xmlns="http://www.w3.org/2000/svg" />'),
}));

const account = {
  id: "11111111-1111-4111-8111-111111111111",
  username: "admin-user",
  role: "ADMIN",
  version: 1,
};

const webChallenge = {
  scan_payload: "PKUBA_ADMIN_WEB_LOGIN:1:A1B2C3:abcdefghijklmnopqrstuvwxyzABCDEFGH12345678",
  browser_token: "browser-secret",
  verification_code: "A1B2C3",
  expires_at: new Date(Date.now() + 300_000).toISOString(),
  expires_in: 300,
};

function makeClient(overrides: Record<string, unknown> = {}) {
  return {
    createWebLoginChallenge: vi.fn().mockResolvedValue(webChallenge),
    getWebLoginStatus: vi.fn().mockResolvedValue({
      status: "PENDING",
      expires_at: webChallenge.expires_at,
      expires_in: 300,
      confirmed_username: null,
    }),
    consumeWebLogin: vi.fn().mockResolvedValue(account),
    getLoginChallenge: vi.fn().mockResolvedValue({
      challenge: "password-challenge",
      csrf_token: "csrf",
      expires_in: 300,
    }),
    passwordLogin: vi.fn().mockResolvedValue(account),
    ...overrides,
  } as unknown as ReturnType<typeof createAdminClient>;
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("LoginScreen", () => {
  it("defaults to a session-bound WeChat QR challenge", async () => {
    const client = makeClient();
    render(<LoginScreen client={client} onLogin={vi.fn()} />);

    expect(screen.getByRole("tab", { name: "微信扫码" }).getAttribute("aria-selected")).toBe(
      "true",
    );
    await screen.findByAltText("管理员网页登录二维码");
    expect(screen.getByText("A1B2C3")).toBeTruthy();
    expect(client.createWebLoginChallenge).toHaveBeenCalledTimes(1);
    expect(screen.getByText(/扫码登录管理后台/)).toBeTruthy();
  });

  it("consumes a confirmed challenge and restores the admin session", async () => {
    const onLogin = vi.fn();
    const client = makeClient({
      getWebLoginStatus: vi.fn().mockResolvedValue({
        status: "CONFIRMED",
        expires_at: webChallenge.expires_at,
        expires_in: 299,
        confirmed_username: account.username,
      }),
    });
    render(<LoginScreen client={client} onLogin={onLogin} />);

    await waitFor(() => expect(client.consumeWebLogin).toHaveBeenCalledWith("browser-secret"), {
      timeout: 2_000,
    });
    expect(onLogin).toHaveBeenCalledWith(account);
  });

  it("keeps personal username and password login as a fallback", async () => {
    const user = userEvent.setup();
    const onLogin = vi.fn();
    const client = makeClient();
    render(<LoginScreen client={client} onLogin={onLogin} />);

    fireEvent.click(screen.getByRole("tab", { name: "密码登录" }));
    await user.type(screen.getByLabelText("用户名"), "admin-user");
    await user.type(screen.getByLabelText("密码"), "PersonalPass!2026");
    fireEvent.click(screen.getByRole("button", { name: "登录" }));

    await waitFor(() => {
      expect(client.passwordLogin).toHaveBeenCalledWith(
        "admin-user",
        "PersonalPass!2026",
        "password-challenge",
      );
    });
    expect(onLogin).toHaveBeenCalledWith(account);
  });
});
