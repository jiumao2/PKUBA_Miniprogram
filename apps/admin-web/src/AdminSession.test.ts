import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, createAdminClient } from "@pkuba/api-client";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("admin session client", () => {
  it("notifies the workspace immediately when a protected request loses its session", async () => {
    const onUnauthorized = vi.fn();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({ code: "UNAUTHORIZED", message: "管理员会话已失效。" }),
          { status: 401, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );
    const client = createAdminClient("", onUnauthorized);

    await expect(client.getMe()).rejects.toBeInstanceOf(ApiError);
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
  });
});
