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
    [422, { detail: [{ loc: ["query", "page"], msg: "必须大于 0" }] }, "页码：必须大于 0", undefined],
    [422, { detail: [{ loc: ["body", "__proto__", "constructor"], msg: "提交内容无效" }] }, "提交内容无效", undefined],
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

  it.each([
    ["date_from_datetime_parsing", "date", "Input should be a valid date or datetime, input is too short", "日期：请填写有效日期"],
    ["date_parsing", "date", "Input should be a valid date", "日期：请填写有效日期"],
    ["missing", "date", "Field required", "日期：此项必填"],
    ["int_parsing", "capacity", "Input should be a valid integer", "容量：请填写整数"],
    ["int_type", "capacity", "Input should be a valid integer", "容量：请填写整数"],
    ["int_from_float", "capacity", "Input should be a valid integer, got a number with a fractional part", "容量：请填写整数"],
    ["greater_than_equal", "capacity", "Input should be greater than or equal to 0", "容量：数值低于允许的最小值"],
    ["less_than_equal", "capacity", "Input should be less than or equal to 100", "容量：数值超过允许的最大值"],
    ["float_parsing", "capacity", "Input should be a valid number", "容量：请填写有效数字"],
    ["string_too_short", "name", "String should have at least 1 character", "名称：填写内容过短"],
    ["string_too_long", "name", "String should have at most 120 characters", "名称：填写内容过长"],
    ["time_parsing", "start_time", "Input should be in a valid time format", "开赛时间：请填写有效时间"],
    ["uuid_parsing", "period_id", "Input should be a valid UUID", "时段：所选项目无效，请重新选择"],
    ["bool_parsing", "active", "Input should be a valid boolean", "启用状态：请选择有效的启用或关闭状态"],
    ["list_type", "players", "Input should be a valid list", "球员：请填写有效列表"],
  ])("localizes built-in %s with a readable nested field position", async (type, field, msg, expected) => {
    const error = await errorFromResponse({ detail: [{
      type, loc: ["body", "payload", "date_capacity_overrides", 0, field], msg,
      input: "private-input", ctx: { error: "private-context", ge: "private-limit" },
    }] });
    expect(error).toMatchObject({
      status: 422,
      message: `特殊日期容量 · 第 1 项 · ${expected}`,
    });
    expect(String(error)).not.toMatch(/date_capacity_overrides|Input should|Field required|private-/);
  });

  it.each([
    { type: "unknown_validator", msg: "private-path: bad value" },
    { type: "__proto__", msg: "private-value" },
    { type: { internal: "private-type" }, msg: { internal: "private-message" } },
    { msg: "Traceback: private-error <html>invalid</html>" },
  ])("uses a safe Chinese fallback for unknown or malformed validators", async (field) => {
    const error = await errorFromResponse({ detail: [{
      ...field,
      loc: ["body", "payload", "internal_state", "__proto__", "constructor", { key: "private-field" }],
      input: "private-input", ctx: { error: "private-context" },
    }] });
    expect(error).toMatchObject({ status: 422 });
    expect((error as ApiError).message).toMatch(/填写内容无效|请求失败/);
    expect(String(error)).not.toMatch(/internal_state|__proto__|constructor|private-|Traceback|html|\[object Object\]/);
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
