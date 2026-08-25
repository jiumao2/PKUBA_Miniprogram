import { renderToStaticMarkup } from "react-dom/server";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AdminReschedulePage } from "@pkuba/api-client";
import {
  RescheduleManagementPage,
  reviewClassificationText,
  statusClass,
} from "./RescheduleManagementPage";

const dataset: AdminReschedulePage = {
  season_id: "season-1",
  season_name: "北大杯",
  items: [
    {
      id: "request-1",
      request_type: "CROSS_WEEK",
      request_type_label: "跨自然周",
      process_route: "HANDBOOK_REVIEW",
      process_route_label: "参赛手册审核",
      review_classification: null,
      review_classification_label: null,
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

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

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
    expect(html).toContain("日期关系");
    expect(html).toContain("处理通道");
    expect(html).toContain("待超级管理员认定");
  });

  it("distinguishes ordinary, pending handbook and classified handbook routes", () => {
    expect(reviewClassificationText({
      process_route: "ORDINARY",
      review_classification_label: null,
    })).toContain("普通流程无需认定");
    expect(reviewClassificationText({
      process_route: "HANDBOOK_REVIEW",
      review_classification_label: null,
    })).toBe("待超级管理员认定");
    expect(reviewClassificationText({
      process_route: "HANDBOOK_REVIEW",
      review_classification_label: "跨轮次调整",
    })).toBe("跨轮次调整");
  });

  it("submits the administrator classification selected by each action", async () => {
    const user = userEvent.setup();
    const ordinaryDataset: AdminReschedulePage = {
      ...dataset,
      items: [{
        ...dataset.items[0],
        request_type: "SAME_WEEK",
        request_type_label: "同一自然周",
      }],
    };
    const actOnAdminReschedule = vi.fn(async () => ordinaryDataset.items[0]);
    type Props = Parameters<typeof RescheduleManagementPage>[0];
    const client = {
      listAdminRescheduleRequests: vi.fn(async () => ordinaryDataset),
      getAdminRescheduleVoterCandidates: vi.fn(async () => []),
      actOnAdminReschedule,
    } as unknown as Props["client"];
    vi.spyOn(window, "confirm").mockReturnValue(true);
    render(
      <RescheduleManagementPage client={client} initialDataset={ordinaryDataset} />,
    );

    await user.click(screen.getByRole("button", { name: "按普通办法批准" }));

    await waitFor(() => expect(actOnAdminReschedule).toHaveBeenCalledWith(
      "request-1",
      {
        expected_version: 3,
        action: "ADMIN_APPROVE",
        classification: "ORDINARY",
        selected_team_ids: [],
      },
    ));
  });

  it("submits cross-round approval and authoritative voter ids", async () => {
    const user = userEvent.setup();
    const candidates = [
      { id: "team-3", name: "数学科学学院", division_name: "男甲", group_name: "A 组" },
      { id: "team-4", name: "物理学院", division_name: "男甲", group_name: "A 组" },
    ];
    const actOnAdminReschedule = vi.fn(async () => dataset.items[0]);
    type Props = Parameters<typeof RescheduleManagementPage>[0];
    const client = {
      listAdminRescheduleRequests: vi.fn(async () => dataset),
      getAdminRescheduleVoterCandidates: vi.fn(async () => candidates),
      actOnAdminReschedule,
    } as unknown as Props["client"];
    vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<RescheduleManagementPage client={client} initialDataset={dataset} />);

    await user.click(screen.getByRole("button", { name: "认定跨轮次并批准" }));
    await waitFor(() => expect(actOnAdminReschedule).toHaveBeenCalledWith(
      "request-1",
      {
        expected_version: 3,
        action: "ADMIN_APPROVE",
        classification: "CROSS_ROUND",
        selected_team_ids: [],
      },
    ));
    actOnAdminReschedule.mockClear();

    await user.click(screen.getByRole("button", { name: "指定球队投票" }));
    await user.click(await screen.findByLabelText(/数学科学学院/));
    await user.click(screen.getByRole("button", { name: "确认发起投票" }));
    await waitFor(() => expect(actOnAdminReschedule).toHaveBeenCalledWith(
      "request-1",
      {
        expected_version: 3,
        action: "ADMIN_START_VOTE",
        classification: "CROSS_ROUND",
        selected_team_ids: ["team-3"],
      },
    ));
  });
});
