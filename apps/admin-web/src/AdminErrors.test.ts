import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, createAdminClient } from "@pkuba/api-client";

afterEach(() => vi.unstubAllGlobals());

async function errorFromResponse(body: unknown, status = 422, json = true) {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(
    json ? JSON.stringify(body) : String(body),
    { status, headers: { "Content-Type": json ? "application/json" : "text/html" } },
  )));
  return createAdminClient().getMe().catch((error: unknown) => error);
}

describe("administrator API errors", () => {
  it("renders Ninja field-validation arrays without exposing input or context", async () => {
    const error = await errorFromResponse({
      detail: [
        { loc: ["body", "payload", "starts_on"], msg: "请输入有效日期", input: "private-input" },
        { loc: ["body", "payload", "players", 1, "name"], msg: "此项必填", ctx: { detail: "private-context" } },
      ],
      code: "VALIDATION_ERROR",
      traceback: "private-traceback",
    });
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({
      message: "开始日期：请输入有效日期；球员 · 第 2 项 · 名称：此项必填",
      status: 422,
      code: "VALIDATION_ERROR",
    });
    expect(String(error)).not.toMatch(/\[object Object\]|private-/);
  });

  it.each([
    [403, { message: "当前账号没有权限", code: "FORBIDDEN" }, "当前账号没有权限", "FORBIDDEN"],
    [409, { message: "内容已更新，请刷新", code: "VERSION_CONFLICT" }, "内容已更新，请刷新", "VERSION_CONFLICT"],
    [422, { detail: "日期无效" }, "日期无效", undefined],
    [400, { message: "优先保留业务错误", detail: [{ msg: "字段错误" }] }, "优先保留业务错误", undefined],
    [422, { detail: [{ msg: "提交内容无效" }, { msg: {} }, null] }, "提交内容无效", undefined],
    [422, { detail: [{ loc: ["query", "page"], msg: "必须大于 0" }] }, "page：必须大于 0", undefined],
    [422, { detail: [{ loc: ["body", "__proto__", "constructor"], msg: "提交内容无效" }] }, "__proto__ · constructor：提交内容无效", undefined],
    [500, { message: { secret: "hidden" }, detail: { trace: "hidden" }, code: {} }, "请求失败（500）", undefined],
    [422, { detail: [] }, "请求失败（422）", undefined],
    [422, null, "请求失败（422）", undefined],
    [500, ["unexpected"], "请求失败（500）", undefined],
  ])("preserves readable errors and status %i", async (status, body, message, code) => {
    const error = await errorFromResponse(body, status);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ message, status });
    expect((error as ApiError).code).toBe(code);
  });

  it("uses a safe fallback for an HTML 500 response", async () => {
    const error = await errorFromResponse("<html>private stack trace</html>", 500, false);
    expect(error).toMatchObject({ message: "请求失败（500）", status: 500 });
    expect(String(error)).not.toContain("private");
  });

  it("still signals session expiry once and keeps successful data unchanged", async () => {
    const onUnauthorized = vi.fn();
    vi.stubGlobal("fetch", vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ detail: [{ msg: "登录已过期" }] }), { status: 401 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ username: "测试管理员" }), { status: 200 })));
    const client = createAdminClient("", onUnauthorized);
    await expect(client.getMe()).rejects.toMatchObject({ message: "登录已过期", status: 401 });
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
    await expect(client.getMe()).resolves.toEqual({ username: "测试管理员" });
  });
});
