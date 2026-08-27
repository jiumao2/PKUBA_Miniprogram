import type { RescheduleRequest } from "@pkuba/api-client";

type VenueVisibilityRequest = Pick<RescheduleRequest, "status" | "is_terminal"> & {
  game: Pick<RescheduleRequest["game"], "venue_name">;
};

export function targetVenueLabel(item: VenueVisibilityRequest) {
  if (item.status === "APPROVED") return item.game.venue_name;
  if (item.is_terminal) return "申请未生效，场地未公布";
  return "场地已内部预留，生效后公布";
}

export function voterCandidateScopeText(groupName: string | null) {
  return groupName
    ? `候选范围：${groupName}内除比赛双方外的启用球队。`
    : "候选范围：本组别内除比赛双方外的全部启用球队。";
}

export function voterCandidateEmptyText(groupName: string | null) {
  return groupName ? "没有可指定的同小组球队。" : "没有可指定的同组别球队。";
}
