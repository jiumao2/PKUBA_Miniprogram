import { describe, expect, it } from "vitest";

import {
  parseRescheduleEntryMode,
  RESCHEDULE_ENTRY_COPY,
  targetsForEntryMode,
} from "./mode";

describe("leader reschedule entry modes", () => {
  it("defaults unknown or missing routes to the ordinary same-week flow", () => {
    expect(parseRescheduleEntryMode(undefined)).toBe("same_week");
    expect(parseRescheduleEntryMode("unexpected")).toBe("same_week");
    expect(parseRescheduleEntryMode("cross_week")).toBe("cross_week");
  });

  it("keeps ordinary and cross-week target dates in separate entry flows", () => {
    const targets = [
      { id: "same", request_type: "SAME_WEEK" },
      { id: "cross", request_type: "CROSS_WEEK" },
    ];

    expect(targetsForEntryMode(targets, "same_week")).toEqual([targets[0]]);
    expect(targetsForEntryMode(targets, "cross_week")).toEqual([targets[1]]);
  });

  it("states the handbook and administrator decision boundary in the cross-week copy", () => {
    expect(RESCHEDULE_ENTRY_COPY.cross_week.guidance).toContain("《参赛手册》");
    expect(RESCHEDULE_ENTRY_COPY.cross_week.guidance).toContain("由管理员审核");
    expect(RESCHEDULE_ENTRY_COPY.cross_week.guidance).toContain("普通调赛办法");
  });
});
