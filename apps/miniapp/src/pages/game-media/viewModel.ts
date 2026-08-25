import { formatOfficialScore, type GameMediaAsset } from "@pkuba/api-client";

export type MediaKind = "SCORESHEET" | "GROUP_PHOTO" | "GAME_PHOTO";

export function gameHeadingScore(
  homeScore: number | null,
  awayScore: number | null,
): string {
  return formatOfficialScore(homeScore, awayScore, " : ") ?? "VS";
}

export function mediaGroupPresentation(
  kind: MediaKind,
  assetCount: number,
  canUpload: boolean,
) {
  const emptyActionLabel = kind === "GAME_PHOTO"
    ? "添加其他照片"
    : kind === "GROUP_PHOTO"
      ? "上传比赛合照"
      : "上传记录表";
  return {
    emptyActionLabel,
    showEmptyAction: assetCount === 0 && canUpload,
    showAddMore: kind === "GAME_PHOTO" && assetCount > 0 && canUpload,
  };
}

export function mediaAssetActions(
  asset: Pick<GameMediaAsset, "can_replace" | "can_delete" | "storage_status" | "content_url">,
) {
  const online = asset.storage_status === "ONLINE" && Boolean(asset.content_url);
  return {
    online,
    showReplace: asset.can_replace && online,
    showDelete: asset.can_delete,
  };
}
