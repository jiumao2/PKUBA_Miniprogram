import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { AdminReschedulePage } from "@pkuba/api-client";
import { RescheduleManagementPage, statusClass } from "./RescheduleManagementPage";

const dataset: AdminReschedulePage = {
  season_id: "season-1",
  season_name: "北大杯",
  items: [
    {
      id: "request-1",
      request_type: "CROSS_WEEK",
      request_type_label: "跨周",
      status: "WAITING_ADMIN_DECISION",
      status_label: "等待管理员决定",
      requester_team_id: "team-1",
      requester_team_name: "法学院",
      game: {
        id: "game-1",
        code: "MA-A1-A2",
        division_name: "男甲",
        division_gender: "MEN",
        group_name: "A 组",
        date: "2026-04-11",
        start_time: "12:50",
        venue_name: "五四东一",
        home_name: "法学院",
        away_name: "经济学院",
        leader_adjustable: true,
        status: "SCHEDULED",
        version: 2,
      },
      original_date: "2026-04-11",
      original_start_time: "12:50",
      original_venue_name: "五四东一",
      original_home_name: "法学院",
      original_away_name: "经济学院",
      target_date: "2026-04-18",
      target_period_id: "period-1",
      target_period_name: "第一时段",
      target_start_time: "12:50",
      submit_deadline: "2026-04-08T16:00:00Z",
      confirmation_deadline: "2026-04-09T16:00:00Z",
      confirmations: [{
        id: "confirmation-1",
        team_id: "team-2",
        team_name: "经济学院",
        purpose: "OPPONENT",
        response: "ACCEPTED",
        responded_at: "2026-04-08T08:00:00Z",
      }],
      actions: ["ADMIN_APPROVE", "ADMIN_REJECT", "ADMIN_START_VOTE", "ADMIN_CANCEL"],
      is_terminal: false,
      version: 3,
      created_at: "2026-04-08T07:00:00Z",
      decided_at: null,
      resources: {
        game_lock_matches: true,
        reservation_status: "ACTIVE",
        reservation_id: "reservation-1",
        capacity: 3,
        game_count: 1,
        active_reservation_count: 1,
        used_count: 2,
        remaining_count: 1,
        venue_conflict: false,
        issues: [],
      },
    },
  ],
  summary: {
    active: 1,
    waiting_opponent: 0,
    waiting_admin_decision: 1,
    waiting_selected_teams: 0,
    waiting_admin_final: 0,
  },
  total: 1,
  page: 1,
  page_size: 30,
};

describe("RescheduleManagementPage", () => {
  it("uses distinct textual state treatments", () => {
    expect(statusClass("WAITING_ADMIN_DECISION")).toBe("decision");
    expect(statusClass("WAITING_ADMIN_FINAL")).toBe("final");
    expect(statusClass("APPROVED")).toBe("success");
  });

  it("renders queue, lock, reservation, capacity and authoritative actions", () => {
    type Props = Parameters<typeof RescheduleManagementPage>[0];
    const client = {
      listAdminRescheduleRequests: async () => dataset,
      getAdminRescheduleVoterCandidates: async () => [],
      actOnAdminReschedule: async () => dataset.items[0],
    } as unknown as Props["client"];
    const html = renderToStaticMarkup(
      <RescheduleManagementPage client={client} initialDataset={dataset} />,
    );

    expect(html).toContain("调赛申请");
    expect(html).toContain("等待管理员决定");
    expect(html).toContain("原比赛活动锁");
    expect(html).toContain("目标资源预留");
    expect(html).toContain("具体场地不公开");
    expect(html).not.toContain("五四东二");
    expect(html).toContain("目标时段容量");
  });
});
