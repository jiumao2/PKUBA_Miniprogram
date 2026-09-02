import { describe, expect, it } from "vitest";

import { passwordCharacterCount, validateAdminRegistration } from "./validation";

describe("administrator registration validation", () => {
  it("requires the current season invite code", () => {
    expect(validateAdminRegistration("  ", "2468", "2468")).toBe("请填写管理员邀请码。");
  });

  it("requires at least four Unicode characters", () => {
    expect(passwordCharacterCount("篮协一二")).toBe(4);
    expect(validateAdminRegistration("global-invite", "123", "123")).toBe(
      "网页密码至少需要 4 个字符。",
    );
    expect(validateAdminRegistration("global-invite", "篮协一二", "篮协一二")).toBeNull();
  });

  it("requires matching passwords without imposing strength rules", () => {
    expect(validateAdminRegistration("global-invite", "1111", "2222")).toBe(
      "两次输入的网页密码不一致。",
    );
    expect(validateAdminRegistration("global-invite", "1111", "1111")).toBeNull();
  });
});
