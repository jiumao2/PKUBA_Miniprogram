import type { RescheduleRequest } from "@pkuba/api-client";

type VenueVisibilityRequest = Pick<RescheduleRequest, "status" | "is_terminal"> & {
  game: Pick<RescheduleRequest["game"], "venue_name">;
};

export function targetVenueLabel(item: VenueVisibilityRequest) {
  if (item.status === "APPROVED") return item.game.venue_name;
  if (item.is_terminal) return "申请未生效，场地未公布";
  return "场地已内部预留，生效后公布";
}
