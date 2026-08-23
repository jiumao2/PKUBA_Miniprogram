import { describe, expect, it } from "vitest";
import type { ScheduleDay } from "@pkuba/api-client";

import {
  mergeScheduleDays,
  replaceScheduleRange,
} from "./model";

const day = (date: string) => ({ date, games: [] }) as ScheduleDay;

describe("schedule day window", () => {
  it("merges bidirectional pages without duplicates or disorder", () => {
    expect(mergeScheduleDays(
      [day("2026-04-03"), day("2026-04-04")],
      [day("2026-04-01"), day("2026-04-03")],
    ).map((item) => item.date)).toEqual([
      "2026-04-01",
      "2026-04-03",
      "2026-04-04",
    ]);
  });

  it("revalidates only the loaded range", () => {
    expect(replaceScheduleRange(
      [day("2026-04-01"), day("2026-04-03"), day("2026-04-05")],
      [day("2026-04-04")],
      "2026-04-03",
      "2026-04-04",
    ).map((item) => item.date)).toEqual([
      "2026-04-01",
      "2026-04-04",
      "2026-04-05",
    ]);
  });
});
