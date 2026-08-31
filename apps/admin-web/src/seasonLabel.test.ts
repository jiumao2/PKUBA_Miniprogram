import { describe, expect, it } from "vitest";

import { formatAdminSeasonLabel } from "./seasonLabel";

describe("formatAdminSeasonLabel", () => {
  it("distinguishes same-name seasons by year and status", () => {
    const published = formatAdminSeasonLabel({ year: 2026, name: "北大杯", status: "PUBLISHED" });
    const setup = formatAdminSeasonLabel({ year: 2027, name: "北大杯", status: "SETUP" });

    expect(published).toBe("2026 · 北大杯 · 已公开");
    expect(setup).toBe("2027 · 北大杯 · 准备中");
    expect(published).not.toBe(setup);
  });
});
