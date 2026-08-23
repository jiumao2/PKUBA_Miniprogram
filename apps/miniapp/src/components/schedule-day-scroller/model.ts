import type { ScheduleDay } from "@pkuba/api-client";

export type ScheduleRenderItem =
  | { kind: "day"; day: ScheduleDay }
  | { kind: "today-marker"; date: string };

export function mergeScheduleDays(
  current: ScheduleDay[],
  incoming: ScheduleDay[],
): ScheduleDay[] {
  const byDate = new Map(current.map((day) => [day.date, day]));
  incoming.forEach((day) => byDate.set(day.date, day));
  return Array.from(byDate.values()).sort((left, right) => left.date.localeCompare(right.date));
}

export function replaceScheduleRange(
  current: ScheduleDay[],
  incoming: ScheduleDay[],
  dateFrom: string,
  dateTo: string,
): ScheduleDay[] {
  return mergeScheduleDays(
    current.filter((day) => day.date < dateFrom || day.date > dateTo),
    incoming,
  );
}

export function scheduleRenderItems(
  days: ScheduleDay[],
  today: string,
  hasPrevious: boolean,
  hasNext: boolean,
): ScheduleRenderItem[] {
  if (!days.length) return [];
  if (days.some((day) => day.date === today)) {
    return days.map((day) => ({ kind: "day", day }));
  }
  const items: ScheduleRenderItem[] = [];
  let inserted = false;
  days.forEach((day, index) => {
    if (!inserted && today < day.date && (index > 0 || !hasPrevious)) {
      items.push({ kind: "today-marker", date: today });
      inserted = true;
    }
    items.push({ kind: "day", day });
  });
  if (!inserted && today > days[days.length - 1].date && !hasNext) {
    items.push({ kind: "today-marker", date: today });
  }
  return items;
}

export function scheduleDayAnchor(date: string) {
  return `schedule-day-${date}`;
}

export const TODAY_MARKER_ANCHOR = "schedule-today-marker";
