import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { createAdminClient } from "@pkuba/api-client";

import { MediaReviewPage } from "./MediaReviewPage";

type AdminClient = ReturnType<typeof createAdminClient>;

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("MediaReviewPage", () => {
  it("shows archived media metadata without a broken image or mutation controls", async () => {
    const client = {
      listAdminGameMedia: vi.fn().mockResolvedValue([
        {
          id: "10000000-0000-0000-0000-000000000001",
          game_id: "20000000-0000-0000-0000-000000000001",
          game_code: "M-001",
          game_label: "男甲 · 工学 vs 数学",
          kind: "GAME_PHOTO",
          storage_status: "PURGED",
          content_url: "",
          original_filename: "photo.jpg",
          mime_type: "image/jpeg",
          byte_size: 1024,
          width: 1200,
          height: 800,
          sort_order: 0,
          scoresheet_complete_confirmed: false,
          review_status: "PENDING",
          review_note: "",
          uploaded_by: "core-developer",
          created_at: "2026-05-10T12:00:00+08:00",
          version: 3,
        },
      ]),
    } as unknown as AdminClient;

    render(<MediaReviewPage client={client} />);

    expect(await screen.findByText("照片已归档至线下备份")).toBeTruthy();
    expect(screen.queryByRole("img")).toBeNull();
    expect(screen.queryByRole("button", { name: "通过" })).toBeNull();
    expect(screen.queryByRole("button", { name: "退回" })).toBeNull();
    expect(screen.queryByRole("button", { name: "删除" })).toBeNull();
    expect(screen.queryByRole("button", { name: "上传替换图片" })).toBeNull();
  });
});
