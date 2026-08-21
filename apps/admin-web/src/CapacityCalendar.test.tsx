import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import type { CapacityLedgerRow } from "@pkuba/api-client";

import { CapacityCalendar } from "./CapacityCalendar";

function row(
  date: string,
  period: number,
  values: Partial<CapacityLedgerRow> = {},
): CapacityLedgerRow {
  return {
    date,
    day_type: "WEEKEND",
    period_id: `40000000-0000-0000-0000-00000000000${period}`,
    period_code: `P${period}`,
    period_name: `第${period}时段`,
    nominal_start_time: "12:50",
    default_capacity: 3,
    override_capacity: null,
    effective_capacity: 3,
    game_count: 0,
    reservation_count: 0,
    used_count: 0,
    remaining_count: 3,
    over_capacity: false,
    ...values,
  };
}

afterEach(cleanup);

describe("CapacityCalendar", () => {
  it("uses the first and last match dates and aggregates every period by day", () => {
    render(<CapacityCalendar today="2026-03-21" ledger={[
      row("2026-03-21", 1, { game_count: 2, used_count: 2, remaining_count: 1 }),
      row("2026-03-21", 2, { game_count: 1, used_count: 1, remaining_count: 2 }),
      row("2026-03-29", 1, { game_count: 1, used_count: 1, remaining_count: 2 }),
      row("2026-04-05", 1),
    ]} />);

    expect(screen.getByText("3/21—3/29 · 2 周")).toBeTruthy();
    expect(screen.getByText("2 个比赛日")).toBeTruthy();
    expect(screen.getByText("共 4 场")).toBeTruthy();
    expect(screen.getAllByLabelText(/3月21日.*已排 3 场.*可用 3 场/)).toHaveLength(2);
    expect(screen.getAllByText("今")).toHaveLength(2);
    expect(document.querySelectorAll('[aria-current="date"]')).toHaveLength(2);
    expect(screen.queryByLabelText(/4月5日/)).toBeNull();
  });

  it("synchronizes day detail across both calendars and identifies manual overrides", async () => {
    const user = userEvent.setup();
    render(<CapacityCalendar ledger={[
      row("2026-04-04", 1, {
        game_count: 3,
        used_count: 3,
        remaining_count: 1,
        override_capacity: 4,
        effective_capacity: 4,
      }),
    ]} />);

    await user.hover(screen.getAllByLabelText(/4月4日.*已排 3 场.*可用 1 场/)[0]);
    expect(screen.getByText("已排 3 场")).toBeTruthy();
    expect(screen.getByText("可用 1 场")).toBeTruthy();
    expect(screen.getByText("特殊容量")).toBeTruthy();
    expect(document.querySelectorAll(".capacity-day.is-active")).toHaveLength(2);
  });

  it("does not invent a season range before the first game is scheduled", () => {
    render(<CapacityCalendar ledger={[row("2026-03-21", 1)]} />);
    expect(screen.getByText("当前赛季还没有比赛，排入首场比赛后将生成周历。")).toBeTruthy();
  });
});
