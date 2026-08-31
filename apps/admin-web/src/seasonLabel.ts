import type { AdminSeason } from "@pkuba/api-client";

export const adminSeasonStatusLabels: Record<string, string> = {
  SETUP: "准备中",
  PUBLISHED: "已公开",
  ARCHIVED: "已归档",
};

export function formatAdminSeasonLabel(
  season: Pick<AdminSeason, "year" | "name" | "status">,
) {
  return `${season.year} · ${season.name} · ${adminSeasonStatusLabels[season.status] ?? season.status}`;
}
