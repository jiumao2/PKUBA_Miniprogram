export const WEEKDAYS = ["一", "二", "三", "四", "五", "六", "日"];

export type CalendarRange = "recent" | "all" | `month:${string}`;

export type CalendarRangeOption = {
  value: CalendarRange;
  label: string;
};

export type CalendarCell = {
  key: string;
  outside: boolean;
  date: string;
  gameCount: number;
};

export function calendarRangeOptions(
  seasonStart: string,
  seasonEnd: string,
): CalendarRangeOption[] {
  const options: CalendarRangeOption[] = [{ value: "recent", label: "近期" }];
  const cursor = startOfMonth(parseDate(seasonStart));
  const end = startOfMonth(parseDate(seasonEnd));
  while (cursor <= end) {
    const value = `${cursor.getUTCFullYear()}-${String(cursor.getUTCMonth() + 1).padStart(2, "0")}`;
    options.push({
      value: `month:${value}`,
      label: `${cursor.getUTCFullYear()}年${cursor.getUTCMonth() + 1}月`,
    });
    cursor.setUTCMonth(cursor.getUTCMonth() + 1);
  }
  options.push({ value: "all", label: "全部赛季" });
  return options;
}

export function buildSeasonCalendar(
  anchorDate: string,
  days: Array<{ date: string; game_count: number }>,
  range: CalendarRange = "recent",
  seasonStart = anchorDate,
  seasonEnd = anchorDate,
): CalendarCell[] {
  const counts = new Map(days.map((day) => [day.date, day.game_count]));
  const anchor = parseDate(anchorDate);
  let first: Date;
  let last: Date;
  let activeStart: Date;
  let activeEnd: Date;

  if (range.startsWith("month:")) {
    activeStart = startOfMonth(parseDate(`${range.slice(6)}-01`));
    activeEnd = endOfMonth(activeStart);
    first = startOfWeek(activeStart);
    last = endOfWeek(activeEnd);
  } else if (range === "all") {
    activeStart = parseDate(seasonStart);
    activeEnd = parseDate(seasonEnd);
    first = startOfWeek(activeStart);
    last = endOfWeek(activeEnd);
  } else {
    const weekStart = startOfWeek(anchor);
    first = addDays(weekStart, -14);
    last = addDays(weekStart, 20);
    activeStart = first;
    activeEnd = last;
  }

  const seasonFirst = parseDate(seasonStart);
  const seasonLast = parseDate(seasonEnd);
  const cells: CalendarCell[] = [];
  const cursor = new Date(first);
  while (cursor <= last) {
    const date = utcDateKey(cursor);
    const outside = range !== "recent" && (
      cursor < activeStart
      || cursor > activeEnd
      || cursor < seasonFirst
      || cursor > seasonLast
    );
    cells.push({
      key: date,
      outside,
      date,
      gameCount: outside ? 0 : counts.get(date) ?? 0,
    });
    cursor.setUTCDate(cursor.getUTCDate() + 1);
  }
  return cells;
}

export function densityLevel(value: number, maxValue: number) {
  if (value === 0 || maxValue === 0) return 0;
  return Math.max(1, Math.min(4, Math.ceil(value / maxValue * 4)));
}

export function localDateKey(value: Date) {
  const beijing = new Date(value.getTime() + 8 * 60 * 60 * 1000);
  return utcDateKey(beijing);
}

export function shortDate(value: string) {
  const [, month, day] = value.split("-").map(Number);
  return `${month}/${day}`;
}

function startOfWeek(value: Date) {
  const result = new Date(value);
  const mondayOffset = (result.getUTCDay() + 6) % 7;
  result.setUTCDate(result.getUTCDate() - mondayOffset);
  return result;
}

function endOfWeek(value: Date) {
  return addDays(startOfWeek(value), 6);
}

function startOfMonth(value: Date) {
  return new Date(Date.UTC(value.getUTCFullYear(), value.getUTCMonth(), 1));
}

function endOfMonth(value: Date) {
  return new Date(Date.UTC(value.getUTCFullYear(), value.getUTCMonth() + 1, 0));
}

function addDays(value: Date, count: number) {
  const result = new Date(value);
  result.setUTCDate(result.getUTCDate() + count);
  return result;
}

function parseDate(value: string) {
  const [year, month, day] = value.split("-").map(Number);
  return new Date(Date.UTC(year, month - 1, day));
}

function utcDateKey(value: Date) {
  return `${value.getUTCFullYear()}-${String(value.getUTCMonth() + 1).padStart(2, "0")}-${String(value.getUTCDate()).padStart(2, "0")}`;
}
