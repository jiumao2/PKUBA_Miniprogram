import { describe, expect, it, vi } from "vitest";
import type { MiniAppMe, WeChatExchange } from "@pkuba/api-client";

import { resolveMiniAppIdentityWith, type MiniAppIdentityAdapter } from "./identity";

const administrator: MiniAppMe = {
  account: {
    id: "account-1",
    username: "管理员",
    role: "ADMIN",
    version: 1,
  },
  leader_binding: null,
  admin_role: "ADMIN",
  next_game: null,
};

function adapter(overrides: Partial<MiniAppIdentityAdapter> = {}): MiniAppIdentityAdapter {
  return {
    readToken: () => "",
    clearToken: () => undefined,
    getMe: async () => administrator,
    exchange: async () => ({
      requires_profile: false,
      profile_ticket: null,
      session_token: "fresh-token",
      me: administrator,
    }),
    ...overrides,
  };
}

describe("miniapp identity restoration", () => {
  it("reuses a valid saved session without exchanging WeChat code", async () => {
    const exchange = vi.fn<() => Promise<WeChatExchange>>();
    const result = await resolveMiniAppIdentityWith(adapter({
      readToken: () => "saved-token",
      exchange,
    }));

    expect(result).toEqual({
      me: administrator,
      token: "saved-token",
      requiresProfile: false,
    });
    expect(exchange).not.toHaveBeenCalled();
  });

  it("silently restores a known administrator after a missing session", async () => {
    const result = await resolveMiniAppIdentityWith(adapter());

    expect(result.me?.admin_role).toBe("ADMIN");
    expect(result.token).toBe("fresh-token");
    expect(result.requiresProfile).toBe(false);
  });

  it("clears an invalid session before exchanging a fresh WeChat code", async () => {
    const clearToken = vi.fn();
    const result = await resolveMiniAppIdentityWith(adapter({
      readToken: () => "expired-token",
      clearToken,
      getMe: async () => { throw new Error("expired"); },
    }));

    expect(clearToken).toHaveBeenCalledOnce();
    expect(result.token).toBe("fresh-token");
    expect(result.me?.admin_role).toBe("ADMIN");
  });

  it("keeps an unknown OpenID anonymous on a public detail page", async () => {
    const result = await resolveMiniAppIdentityWith(adapter({
      exchange: async () => ({
        requires_profile: true,
        profile_ticket: "profile-ticket",
        session_token: null,
        me: null,
      }),
    }));

    expect(result).toEqual({ me: null, token: "", requiresProfile: true });
  });
});
