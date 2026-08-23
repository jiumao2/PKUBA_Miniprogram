import type { ScheduleDay } from "@pkuba/api-client";

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

export function scheduleDayAnchor(date: string) {
  return `schedule-day-${date}`;
}
