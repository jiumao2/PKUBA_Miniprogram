import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AdminSeason, GameMediaAsset, ScoresheetQueueItem, createAdminClient } from "@pkuba/api-client";
import { ApiError } from "@pkuba/api-client";

import { GameMediaWorkbench, scoresheetHref } from "./GameMediaWorkbench";

type AdminClient = ReturnType<typeof createAdminClient>;

const seasons = [
  { id: "season-live", name: "2026 北大杯", status: "PUBLISHED", year: 2026, divisions: [] },
] as unknown as AdminSeason[];

const games: ScoresheetQueueItem[] = [
  {
    game_id: "game-one", can_upload_source: true, game_code: "LEGACY-851a630369b794bd067aa399222b4f76", game_label: "男甲 · 数学 — 外院",
    competition: "2026 北大杯", division_name: "男甲", venue: "第一体育馆",
    home_name: "数学", away_name: "外院", date: "2026-08-20", start_time: "18:00",
    scoresheet_id: "sheet-one", source_asset_id: "source-one", status: "DRAFT",
    draft_version: 2, recognition_status: "succeeded", recognition_attempt: 1,
    recognition_max_attempts: 4, next_attempt_at: null, publication_number: null,
  },
  {
    game_id: "game-two", can_upload_source: true, game_code: "W-002", game_label: "女甲 · 物院 — 化院",
    competition: "2026 北大杯", division_name: "女甲", venue: "五四东一",
    home_name: "物院", away_name: "化院", date: "2026-08-21", start_time: "19:30",
    scoresheet_id: null, source_asset_id: null, status: "NO_SOURCE",
    draft_version: null, recognition_status: null, recognition_attempt: 0,
    recognition_max_attempts: 4, next_attempt_at: null, publication_number: null,
  },
];

const photo = {
  id: "photo-one", game_id: "game-one", game_code: "M-001", game_label: "男甲 · 数学 — 外院",
  kind: "GROUP_PHOTO", storage_status: "ONLINE", content_url: "/photo-one.jpg",
  original_filename: "group.jpg", mime_type: "image/jpeg", byte_size: 2048,
  width: 1200, height: 800, sort_order: 0, scoresheet_complete_confirmed: false,
  uploaded_by: "admin", created_at: "2026-08-20T20:00:00+08:00", version: 1,
  can_replace: true, can_delete: true,
} as GameMediaAsset;

afterEach(() => {
  cleanup();
  window.history.replaceState(null, "", "/");
  vi.restoreAllMocks();
});

function clientWith(overrides: Partial<AdminClient> = {}) {
  return {
    getScoresheetQueuePage: vi.fn().mockImplementation(async (options = {}) => {
      let items = options.gameId
        ? games.filter((game) => game.game_id === options.gameId)
        : [...games];
      if (options.processing === "UPLOAD") {
        items = items.filter((game) => !game.source_asset_id);
      } else if (options.processing === "SCORESHEET_REVIEW") {
        items = items.filter((game) => Boolean(game.source_asset_id) && game.status !== "PUBLISHED");
      } else if (options.processing === "COMPLETE") {
        items = items.filter((game) => game.status === "PUBLISHED");
      }
      if (options.divisionName) {
        items = items.filter((game) => game.division_name === options.divisionName);
      }
      return {
        items,
        total: items.length,
        page: options.page ?? 1,
        page_size: options.pageSize ?? 20,
        division_names: ["男甲", "女甲"],
      };
    }),
    listAdminGameMedia: vi.fn().mockResolvedValue([photo]),
    uploadAdminGameMedia: vi.fn().mockResolvedValue(photo),
    replaceAdminGameMedia: vi.fn(),
    deleteAdminGameMedia: vi.fn(),
    ...overrides,
  } as unknown as AdminClient;
}

describe("GameMediaWorkbench", () => {
  it.each(["refused", "unknown"] as const)("refreshes a partially uploaded batch and distinguishes %s results", async outcome => {
    const user = userEvent.setup();
    const stored: GameMediaAsset[] = [];
    const client = clientWith({
      listAdminGameMedia: vi.fn().mockImplementation(async () => [...stored]),
      uploadAdminGameMedia: vi.fn()
        .mockImplementationOnce(async () => { stored.push({ ...photo, kind: "GAME_PHOTO" }); return stored[0]; })
        .mockRejectedValueOnce(outcome === "refused"
          ? new ApiError("图片内容不合法", 400, "INVALID_IMAGE")
          : new SyntaxError("Unexpected end of JSON input")),
    });
    render(<GameMediaWorkbench client={client} seasons={seasons} seasonId="season-live"
      initialGameId="game-one" onSeasonChange={vi.fn()} />);
    const picker = await screen.findByLabelText("添加其他照片");
    const reads = vi.mocked(client.listAdminGameMedia).mock.calls.length;
    expect(picker).not.toBeDisabled();
    // The visible label opens the native picker; its hidden input has no pointer events.
    fireEvent.change(picker, { target: { files: [new File(["first"], "first.png", { type: "image/png" }),
      new File(["second"], "second.png", { type: "image/png" })] } });
    await waitFor(() => expect(client.uploadAdminGameMedia).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(vi.mocked(client.listAdminGameMedia).mock.calls.length).toBeGreaterThan(reads));
    expect(await screen.findByText(/已上传 1 张/)).toHaveTextContent(outcome === "refused" ? "失败 1 张" : "结果未确认 1 张");
    expect(client.uploadAdminGameMedia).toHaveBeenCalledTimes(2);
    const originalAttempt = vi.mocked(client.uploadAdminGameMedia).mock.calls[1];
    await user.click(screen.getByRole("button", { name: "仅重试未完成的照片" }));
    await waitFor(() => expect(client.uploadAdminGameMedia).toHaveBeenCalledTimes(3));
    const retry = vi.mocked(client.uploadAdminGameMedia).mock.calls[2];
    expect(retry[3]).toBe(originalAttempt[3]);
    expect(retry[4]).toBe(originalAttempt[4]);
    expect(await screen.findByText("已添加 1 张其他照片。")).toBeVisible();
    expect(screen.queryByRole("button", { name: "仅重试未完成的照片" })).not.toBeInTheDocument();
  });

  it("binds archived correction confirmation to one concrete scoresheet", () => {
    expect(scoresheetHref("season-live", "game-one", {
      archivedView: true,
      correctionDocumentId: "sheet-one",
    })).toBe(
      "/scoresheet.html?season_id=season-live&game_id=game-one&archived_view=1&archived_correction=sheet-one",
    );
  });

  it("aggregates records and photos by game, restores selection and filters the game index", async () => {
    const user = userEvent.setup();
    const client = clientWith();
    render(
      <GameMediaWorkbench
        client={client}
        seasons={seasons}
        seasonId="season-live"
        initialGameId="game-one"
        onSeasonChange={vi.fn()}
      />,
    );

    expect(await screen.findByText("记录表需要人工核对或发布")).toBeVisible();
    const summary = screen.getByLabelText("比赛资料摘要");
    expect(summary).toHaveTextContent("2 场符合条件");
    expect(screen.getByRole("button", { name: /继续核对/ })).toBeVisible();
    expect(await screen.findByRole("button", { name: "删除照片" })).toBeVisible();
    expect(screen.getByText("添加其他照片")).toBeVisible();
    expect(screen.queryByText("上传比赛合照")).not.toBeInTheDocument();
    expect(screen.queryByText(/待审核|待审/)).not.toBeInTheDocument();
    expect(screen.queryByText("LEGACY-851a630369b794bd067aa399222b4f76")).not.toBeInTheDocument();
    expect(screen.getByPlaceholderText("搜索球队、日期或场地")).toBeVisible();
    await waitFor(() => expect(window.location.search).toContain("game_id=game-one"));

    await user.selectOptions(screen.getByLabelText("处理状态"), "UPLOAD");
    expect(await screen.findByText("尚未上传记录表原图")).toBeVisible();
    expect(screen.getByRole("button", { name: /上传并识别/ })).toBeVisible();
    expect(screen.queryByText("数学")).not.toBeInTheDocument();
  });

  it("offers one group-photo upload and a multi-file other-photo upload when empty", async () => {
    const client = clientWith({ listAdminGameMedia: vi.fn().mockResolvedValue([]) });
    render(
      <GameMediaWorkbench
        client={client}
        seasons={seasons}
        seasonId="season-live"
        initialGameId="game-one"
        onSeasonChange={vi.fn()}
      />,
    );

    expect(await screen.findByText("尚未上传比赛合照")).toBeVisible();
    expect(screen.getByText("上传比赛合照")).toBeVisible();
    expect(screen.getByText("添加其他照片")).toBeVisible();
    expect(screen.queryByText(/待审核|通过|未通过/)).not.toBeInTheDocument();
  });

  it("never exposes the scoresheet source through photo deletion controls", async () => {
    const scoresheetAsset = {
      ...photo,
      id: "source-one",
      kind: "SCORESHEET",
      can_delete: true,
    } as GameMediaAsset;
    const client = clientWith({
      listAdminGameMedia: vi.fn().mockResolvedValue([scoresheetAsset]),
    });
    render(
      <GameMediaWorkbench
        client={client}
        seasons={seasons}
        seasonId="season-live"
        initialGameId="game-one"
        onSeasonChange={vi.fn()}
      />,
    );

    expect(await screen.findByText("尚无比赛照片")).toBeVisible();
    expect(screen.queryByRole("button", { name: "删除照片" })).not.toBeInTheDocument();
  });

  it("keeps archived media readable while hiding every mutation control", async () => {
    const archivedSeasons = [{ ...seasons[0], status: "ARCHIVED" }] as unknown as AdminSeason[];
    const archivedGame = [{ ...games[0], status: "PUBLISHED", publication_number: 3 }];
    const archivedPhoto = { ...photo, storage_status: "PURGED", content_url: "" } as GameMediaAsset;
    const client = clientWith({
      getScoresheetQueuePage: vi.fn().mockResolvedValue({
        items: archivedGame,
        total: 1,
        page: 1,
        page_size: 20,
        division_names: ["男甲"],
      }),
      listAdminGameMedia: vi.fn().mockResolvedValue([archivedPhoto]),
    });
    render(
      <GameMediaWorkbench
        client={client}
        seasons={archivedSeasons}
        seasonId="season-live"
        isSuperadmin
        onSeasonChange={vi.fn()}
      />,
    );

    expect(await screen.findByText("已归档赛季")).toBeVisible();
    expect(await screen.findByText("原图已离线归档")).toBeVisible();
    expect(screen.getByRole("button", { name: /查看记录表/ })).toBeVisible();
    expect(screen.getByRole("button", { name: /受控纠错/ })).toBeVisible();
    expect(screen.queryByRole("button", { name: "删除照片" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "上传替换" })).not.toBeInTheDocument();
  });

  it("isolates a photo API failure from recordsheet actions", async () => {
    const client = clientWith({ listAdminGameMedia: vi.fn().mockRejectedValue(new Error("照片接口不可用")) });
    render(
      <GameMediaWorkbench
        client={client}
        seasons={seasons}
        seasonId="season-live"
        initialGameId="game-two"
        onSeasonChange={vi.fn()}
      />,
    );

    expect(await screen.findByText(/照片接口不可用/)).toBeVisible();
    expect(screen.getByText("尚未上传记录表原图")).toBeVisible();
    expect(screen.getByRole("button", { name: /上传并识别/ })).toBeVisible();
    await waitFor(() => expect(client.getScoresheetQueuePage).toHaveBeenCalledWith(expect.objectContaining({
      seasonId: "season-live",
      scope: "ALL",
    })));
  });

  it("falls back from an invalid game id to the first available match", async () => {
    render(
      <GameMediaWorkbench
        client={clientWith()}
        seasons={seasons}
        seasonId="season-live"
        initialGameId="missing-game"
        onSeasonChange={vi.fn()}
      />,
    );

    expect(await screen.findByText("记录表需要人工核对或发布")).toBeVisible();
    await waitFor(() => expect(window.location.search).toContain("game_id=game-one"));
  });
});
