import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AdminSeason, createAdminClient } from "@pkuba/api-client";

import { ArchiveManagementPage } from "./ArchiveManagementPage";

type AdminClient = ReturnType<typeof createAdminClient>;

const season: AdminSeason = {
  id: "10000000-0000-0000-0000-000000000001",
  name: "北大杯",
  competition_type: "PKU_CUP",
  year: 2026,
  status: "ARCHIVED",
  starts_on: "2026-03-21",
  ends_on: "2026-05-10",
  version: 7,
  divisions: [],
};

const nextSeason: AdminSeason = {
  ...season,
  id: "10000000-0000-0000-0000-000000000002",
  name: "新生杯",
  competition_type: "FRESHMAN_CUP",
  version: 3,
};

const storageSummary = {
  disk_total_bytes: 100 * 1024 ** 3,
  disk_used_bytes: 30 * 1024 ** 3,
  disk_free_bytes: 70 * 1024 ** 3,
  reserve_bytes: 25 * 1024 ** 3,
  database_bytes: 29 * 1024 ** 2,
  online_media_bytes: 2 * 1024 ** 3,
  staged_artifact_bytes: 0,
  seasons: [],
};

const emptyPage = { items: [], total: 0, page: 1, page_size: 100 };

const purgePreview = (targetSeason: AdminSeason, files: number) => ({
  season_id: targetSeason.id,
  season_version: targetSeason.version,
  files,
  bytes: files * 1024,
  by_kind: {},
  data_archive_id: "30000000-0000-0000-0000-000000000001",
  photo_archive_id: "30000000-0000-0000-0000-000000000002",
  preview_hash: `preview-${targetSeason.id}`,
  ready: true,
  blockers: [],
});

const baseClient = () => ({
  getArchiveStorageSummary: vi.fn().mockResolvedValue(storageSummary),
  listSeasonExports: vi.fn().mockResolvedValue(emptyPage),
  listSystemBackups: vi.fn().mockResolvedValue(emptyPage),
  listMediaPurgeJobs: vi.fn().mockResolvedValue(emptyPage),
});

afterEach(() => cleanup());

describe("ArchiveManagementPage", () => {
  it("shows storage, blockers, and resumes a failed purge", async () => {
    const retryMediaPurge = vi.fn().mockResolvedValue({});
    const client = {
      getArchiveStorageSummary: vi.fn().mockResolvedValue({
        disk_total_bytes: 100 * 1024 ** 3,
        disk_used_bytes: 30 * 1024 ** 3,
        disk_free_bytes: 70 * 1024 ** 3,
        reserve_bytes: 25 * 1024 ** 3,
        database_bytes: 29 * 1024 ** 2,
        online_media_bytes: 2 * 1024 ** 3,
        staged_artifact_bytes: 0,
        seasons: [{
          season_id: season.id,
          season_name: season.name,
          season_year: season.year,
          season_status: season.status,
          scoresheet_bytes: 1024 ** 3,
          group_photo_bytes: 512 * 1024 ** 2,
          game_photo_bytes: 512 * 1024 ** 2,
          online_bytes: 2 * 1024 ** 3,
          online_files: 375,
        }],
      }),
      listSeasonExports: vi.fn().mockResolvedValue({ items: [], total: 0, page: 1, page_size: 100 }),
      listSystemBackups: vi.fn().mockResolvedValue({ items: [], total: 0, page: 1, page_size: 100 }),
      listMediaPurgeJobs: vi.fn().mockResolvedValue({
        items: [{
          id: "20000000-0000-0000-0000-000000000001",
          season_id: season.id,
          status: "FAILED",
          expected_files: 375,
          expected_bytes: 2 * 1024 ** 3,
          deleted_files: 180,
          deleted_bytes: 1024 ** 3,
          missing_files: 1,
          warnings: [],
          error_code: "MEDIA_PURGE_FAILED",
          error_message: "磁盘暂时不可用",
          completed_at: null,
          created_at: "2026-08-23T10:00:00+08:00",
          version: 3,
        }],
        total: 1,
        page: 1,
        page_size: 100,
      }),
      previewMediaPurge: vi.fn().mockResolvedValue({
        season_id: season.id,
        season_version: season.version,
        files: 195,
        bytes: 1024 ** 3,
        by_kind: {},
        data_archive_id: null,
        photo_archive_id: null,
        preview_hash: "preview",
        ready: false,
        blockers: [{ code: "FINAL_DATA_ARCHIVE_REQUIRED", message: "缺少归档后的最终赛季数据包。" }],
      }),
      retryMediaPurge,
    } as unknown as AdminClient;

    render(
      <ArchiveManagementPage
        client={client}
        seasons={[season]}
        seasonId={season.id}
        onSeasonChange={vi.fn()}
      />,
    );

    expect(await screen.findByText("70.0 GiB 可用")).toBeInTheDocument();
    expect(screen.getByText("缺少归档后的最终赛季数据包。")).toBeInTheDocument();
    expect(screen.getByText("180 / 375 个文件")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "继续清理" }));
    await waitFor(() => expect(retryMediaPurge).toHaveBeenCalledWith(
      "20000000-0000-0000-0000-000000000001",
      3,
    ));
  });

  it("clears the external-copy confirmation whenever the selected season changes", async () => {
    const client = {
      ...baseClient(),
      previewMediaPurge: vi.fn((seasonId: string) => Promise.resolve(
        purgePreview(seasonId === season.id ? season : nextSeason, 4),
      )),
    } as unknown as AdminClient;

    const { rerender } = render(
      <ArchiveManagementPage
        client={client}
        seasons={[season, nextSeason]}
        seasonId={season.id}
        onSeasonChange={vi.fn()}
      />,
    );

    const checkbox = await screen.findByRole("checkbox", {
      name: "我已将最终数据包和照片包保存到服务器以外的位置",
    });
    await userEvent.click(checkbox);
    expect(checkbox).toBeChecked();

    rerender(
      <ArchiveManagementPage
        client={client}
        seasons={[season, nextSeason]}
        seasonId={nextSeason.id}
        onSeasonChange={vi.fn()}
      />,
    );

    await waitFor(() => expect(screen.getByRole("checkbox", {
      name: "我已将最终数据包和照片包保存到服务器以外的位置",
    })).not.toBeChecked());
  });

  it("ignores a late purge preview from the previously selected season", async () => {
    let resolveFirstPreview: ((value: ReturnType<typeof purgePreview>) => void) | undefined;
    const firstPreview = new Promise<ReturnType<typeof purgePreview>>((resolve) => {
      resolveFirstPreview = resolve;
    });
    const client = {
      ...baseClient(),
      previewMediaPurge: vi.fn((seasonId: string) => (
        seasonId === season.id
          ? firstPreview
          : Promise.resolve(purgePreview(nextSeason, 22))
      )),
    } as unknown as AdminClient;

    const { rerender } = render(
      <ArchiveManagementPage
        client={client}
        seasons={[season, nextSeason]}
        seasonId={season.id}
        onSeasonChange={vi.fn()}
      />,
    );
    rerender(
      <ArchiveManagementPage
        client={client}
        seasons={[season, nextSeason]}
        seasonId={nextSeason.id}
        onSeasonChange={vi.fn()}
      />,
    );

    expect(await screen.findByText("22")).toBeInTheDocument();
    await act(async () => {
      resolveFirstPreview?.(purgePreview(season, 11));
      await firstPreview;
    });

    await waitFor(() => expect(screen.queryByText("11")).not.toBeInTheDocument());
    expect(screen.getByText("22")).toBeInTheDocument();
  });
});
