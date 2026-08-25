import { describe, expect, it, vi } from "vitest";

import { copyStaffEmail, STAFF_EMAIL } from "./copy";

describe("special reschedule staff email", () => {
  it("copies the association email and reports success", async () => {
    const copy = vi.fn().mockResolvedValue(undefined);
    const notify = vi.fn().mockResolvedValue(undefined);

    await expect(copyStaffEmail(copy, notify)).resolves.toBe(true);
    expect(copy).toHaveBeenCalledWith({ data: STAFF_EMAIL });
    expect(notify).toHaveBeenCalledWith({ title: "公邮已复制", icon: "success" });
  });

  it("reports a clipboard failure without hiding it", async () => {
    const copy = vi.fn().mockRejectedValue(new Error("clipboard denied"));
    const notify = vi.fn().mockResolvedValue(undefined);

    await expect(copyStaffEmail(copy, notify)).resolves.toBe(false);
    expect(notify).toHaveBeenCalledWith({
      title: "复制失败，请长按邮箱复制",
      icon: "none",
    });
  });
});
