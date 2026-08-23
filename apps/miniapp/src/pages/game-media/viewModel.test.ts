import { describe, expect, it } from "vitest";

import { mediaAssetActions, mediaGroupPresentation } from "./viewModel";

describe("game media presentation", () => {
  it("keeps one inline empty action per media category", () => {
    expect(mediaGroupPresentation("SCORESHEET", 0, true)).toEqual({
      emptyActionLabel: "上传记录表",
      showEmptyAction: true,
      showAddMore: false,
    });
    expect(mediaGroupPresentation("GROUP_PHOTO", 0, true)).toEqual({
      emptyActionLabel: "上传比赛合照",
      showEmptyAction: true,
      showAddMore: false,
    });
    expect(mediaGroupPresentation("GAME_PHOTO", 0, true)).toEqual({
      emptyActionLabel: "添加其他照片",
      showEmptyAction: true,
      showAddMore: false,
    });
  });

  it("only offers an additional-entry action for existing other photos", () => {
    expect(mediaGroupPresentation("GROUP_PHOTO", 1, true).showAddMore).toBe(false);
    expect(mediaGroupPresentation("SCORESHEET", 1, true).showAddMore).toBe(false);
    expect(mediaGroupPresentation("GAME_PHOTO", 2, true).showAddMore).toBe(true);
  });

  it("uses server permissions and hides replacement for offline files", () => {
    expect(mediaAssetActions({
      can_replace: true,
      can_delete: false,
      storage_status: "ONLINE",
      content_url: "/media.jpg",
    })).toEqual({ online: true, showReplace: true, showDelete: false });
    expect(mediaAssetActions({
      can_replace: true,
      can_delete: true,
      storage_status: "PURGED",
      content_url: "",
    })).toEqual({ online: false, showReplace: false, showDelete: true });
  });
});
