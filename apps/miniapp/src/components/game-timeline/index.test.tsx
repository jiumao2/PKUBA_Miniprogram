// @vitest-environment jsdom
import React from "react";
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Game } from "@pkuba/api-client";

vi.mock("@tarojs/components", () => ({
  Text: ({ children, ...props }: any) => <span {...props}>{children}</span>,
  View: ({ children, ...props }: any) => <div {...props}>{children}</div>,
}));

import { GameTimeline } from "./index";

const game = {
  id: "game-1",
  code: "M-A-01",
  division_id: "division-1",
  division_name: "男甲",
  division_gender: "MEN",
  group_name: "A 组",
  stage: "GROUP",
  round_number: 1,
  date: "2026-04-18",
  slot_code: "p1",
  slot_name: "第一时段",
  period_code: "p1",
  period_name: "第一时段",
  start_time: "12:50",
  venue_name: "五四东一",
  home_team_id: "team-1",
  away_team_id: "team-2",
  home_name: "数学",
  away_name: "外院",
  home_score: 34,
  away_score: 25,
  participants_resolved: true,
  leader_adjustable: false,
  status: "COMPLETED",
  version: 1,
} as Game;

afterEach(cleanup);

describe("GameTimeline compact cards", () => {
  it("puts division and phase on the first line without an empty status row", () => {
    const view = render(<GameTimeline games={[game]} showDates={false} />);

    expect(screen.getByText("男甲 · A 组")).toBeVisible();
    expect(view.container.querySelector(".timeline-game-footline")).toBeNull();
  });

  it("keeps the status row only when the match needs public attention", () => {
    const view = render(
      <GameTimeline
        games={[{ ...game, status: "FORFEIT", participants_resolved: false }]}
        showDates={false}
      />,
    );

    expect(view.container.querySelector(".timeline-game-footline")).not.toBeNull();
    expect(screen.getByText("弃权")).toBeVisible();
    expect(screen.getByText("对阵待定")).toBeVisible();
  });
});
