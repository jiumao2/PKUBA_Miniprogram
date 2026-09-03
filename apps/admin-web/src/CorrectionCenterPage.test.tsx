import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type {
  AdminSeason,
  CompetitionCorrection,
  CompetitionCorrectionPreview,
  MobileAdminGame,
  createAdminClient,
} from "@pkuba/api-client";
import { CorrectionCenterPage } from "./CorrectionCenterPage";

type AdminClient = ReturnType<typeof createAdminClient>;

const season = {
  id: "season-1",
  name: "2026 北大杯",
  competition_type: "PKU_CUP",
  year: 2026,
  status: "PUBLISHED",
  starts_on: "2026-03-21",
  ends_on: "2026-05-10",
  version: 4,
  divisions: [],
} as AdminSeason;

const game: MobileAdminGame = {
  id: "game-final",
  code: "men-a-final",
  division_id: "division-men-a",
  division_name: "男甲",
  division_gender: "MEN",
  date: "2026-05-10",
  period_id: "period-1",
  period_code: "P1",
  period_name: "晚场",
  nominal_start_time: "20:00",
  start_time: "20:00",
  standard_venue_id: "venue-1",
  venue_name: "邱德拔体育馆",
  home_team_id: "team-1",
  away_team_id: "team-2",
  home_slot_id: "slot-home",
  away_slot_id: "slot-away",
  participants_managed_by_draw: true,
  home_name: "城环",
  away_name: "化学",
  home_score: null,
  away_score: null,
  status: "SCHEDULED",
  stage: "FINAL",
  leader_adjustable: true,
  active_reschedule_request_id: null,
  version: 9,
};

function preview(
  overrides: Partial<CompetitionCorrectionPreview> = {},
): CompetitionCorrectionPreview {
  return {
    season_id: season.id,
    season_name: season.name,
    season_status: season.status,
    season_version: season.version,
    changed: true,
    change_count: 1,
    public_impact: true,
    archived_impact: false,
    requires_scoresheet_republication: false,
    can_create: true,
    impact_hash: "impact-1",
    before: [],
    after: [],
    warnings: [],
    blockers: [],
    publication_impacts: [],
    downstream_impacts: [],
    ...overrides,
  };
}

function correction(
  status: CompetitionCorrection["status"],
): CompetitionCorrection {
  return {
    id: "correction-1234567890",
    season_id: season.id,
    season_name: season.name,
    status,
    reason: "",
    before_snapshot: {},
    proposed_changes: {},
    impact_snapshot: { publication_impacts: [] },
    impact_hash: "impact-1",
    created_by: "root",
    created_at: "2026-09-04T00:00:00+08:00",
    applied_by: status === "APPLIED" ? "root" : null,
    applied_at: status === "APPLIED" ? "2026-09-04T00:01:00+08:00" : null,
    cancelled_by: null,
    cancelled_at: null,
    version: status === "APPLIED" ? 2 : 1,
  };
}

function clientWith(overrides: Partial<AdminClient> = {}) {
  return {
    getAdminScheduleOptions: vi.fn().mockResolvedValue({
      periods: [{ id: "period-1", code: "P1", name: "晚场", start_time: "20:00" }],
      venues: [{ id: "venue-1", name: "邱德拔体育馆" }],
      teams: [
        { id: "team-1", division_id: game.division_id, name: "城环" },
        { id: "team-2", division_id: game.division_id, name: "化学" },
      ],
    }),
    getAdminScheduleGame: vi.fn().mockResolvedValue(game),
    listCompetitionCorrections: vi.fn().mockResolvedValue([]),
    previewCompetitionCorrection: vi.fn().mockResolvedValue(preview()),
    createCompetitionCorrection: vi.fn().mockResolvedValue(correction("READY")),
    applyCompetitionCorrection: vi.fn().mockResolvedValue(correction("APPLIED")),
    cancelCompetitionCorrection: vi.fn().mockResolvedValue(correction("CANCELLED")),
    ...overrides,
  } as unknown as AdminClient;
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("CorrectionCenterPage", () => {
  it("keeps the server baseline and version while applying a seeded draft", async () => {
    const client = clientWith();
    const onUpdated = vi.fn().mockResolvedValue(undefined);
    vi.spyOn(window, "confirm").mockReturnValue(true);
    render(
      <CorrectionCenterPage
        client={client}
        seasons={[season]}
        season={season}
        games={[game]}
        initialGameId={game.id}
        initialDraft={{ ...game, date: "2026-05-11", version: 2 }}
        onSeasonChange={vi.fn()}
        onUpdated={onUpdated}
        onOpenScoresheet={vi.fn()}
      />,
    );

    expect(await screen.findByLabelText("比赛日期")).toHaveValue("2026-05-11");
    expect(screen.getByText("v9")).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "生成影响预览" }));
    await waitFor(() => expect(client.previewCompetitionCorrection).toHaveBeenCalledTimes(1));
    expect(client.previewCompetitionCorrection).toHaveBeenCalledWith(
      expect.objectContaining({
        expected_season_version: 4,
        changes: [expect.objectContaining({
          game_id: game.id,
          expected_version: 9,
          date: "2026-05-11",
        })],
      }),
    );

    await userEvent.click(screen.getByLabelText(/我已核对修改前后内容/));
    await userEvent.click(screen.getByRole("button", { name: "冻结纠错单" }));
    expect(await screen.findByText("等待最终应用")).toBeVisible();
    expect(screen.getByLabelText("比赛日期")).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "确认并原子应用" }));
    await waitFor(() => expect(client.applyCompetitionCorrection).toHaveBeenCalled());
    await waitFor(() => expect(onUpdated).toHaveBeenCalled());
  });

  it("requires a new preview after selecting a downstream resolution", async () => {
    const first = preview({
      downstream_impacts: [{
        slot_id: "slot-final-home",
        slot_label: "决赛主队",
        current_team_name: "城环",
        new_winner_team_id: "team-2",
        source_game_id: game.id,
      }],
    });
    const client = clientWith({
      previewCompetitionCorrection: vi.fn()
        .mockResolvedValueOnce(first)
        .mockResolvedValueOnce(preview()),
    });
    render(
      <CorrectionCenterPage
        client={client}
        seasons={[season]}
        season={season}
        games={[game]}
        initialGameId={game.id}
        initialDraft={{ ...game, date: "2026-05-11" }}
        onSeasonChange={vi.fn()}
        onUpdated={vi.fn().mockResolvedValue(undefined)}
        onOpenScoresheet={vi.fn()}
      />,
    );

    await screen.findByLabelText("比赛日期");
    await userEvent.click(screen.getByRole("button", { name: "生成影响预览" }));
    const row = (await screen.findByText("决赛主队")).closest(".downstream-resolution");
    expect(row).not.toBeNull();
    await userEvent.selectOptions(within(row as HTMLElement).getByRole("combobox"), "SYNC_WINNER");
    expect(screen.getByText("PREVIEW_STALE")).toBeVisible();
    expect(screen.getByRole("button", { name: "冻结纠错单" })).toBeDisabled();

    await userEvent.click(
      screen.getByRole("button", { name: "按当前下游选择重新预览" }),
    );
    await waitFor(() => expect(client.previewCompetitionCorrection).toHaveBeenCalledTimes(2));
    expect(client.previewCompetitionCorrection).toHaveBeenLastCalledWith(
      expect.objectContaining({
        downstream_resolutions: [{
          slot_id: "slot-final-home",
          action: "SYNC_WINNER",
          team_id: null,
        }],
      }),
    );
    expect(screen.queryByText("PREVIEW_STALE")).not.toBeInTheDocument();
  });
});
