export const WEEKDAYS = ["一", "二", "三", "四", "五", "六", "日"];

export type CalendarCell = {
  key: string;
  outside: boolean;
  date: string;
  gameCount: number;
};

export function buildSeasonCalendar(
  startDate: string,
  endDate: string,
  days: Array<{ date: string; game_count: number }>,
): CalendarCell[] {
  const counts = new Map(days.map((day) => [day.date, day.game_count]));
  const start = parseDate(startDate);
  const end = parseDate(endDate);
  const mondayOffset = (start.getUTCDay() + 6) % 7;
  const cells: CalendarCell[] = Array.from({ length: mondayOffset }, (_, index) => ({
    key: `before-${index}`,
    outside: true,
    date: "",
    gameCount: 0,
  }));
  const cursor = new Date(start);
  while (cursor <= end) {
    const date = utcDateKey(cursor);
    cells.push({
      key: date,
      outside: false,
      date,
      gameCount: counts.get(date) ?? 0,
    });
    cursor.setUTCDate(cursor.getUTCDate() + 1);
  }
  const trailing = (7 - (cells.length % 7)) % 7;
  for (let index = 0; index < trailing; index += 1) {
    cells.push({ key: `after-${index}`, outside: true, date: "", gameCount: 0 });
  }
  return cells;
}

export function densityLevel(value: number, maxValue: number) {
  if (value === 0 || maxValue === 0) return 0;
  return Math.max(1, Math.min(4, Math.ceil(value / maxValue * 4)));
}

export function localDateKey(value: Date) {
  return `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, "0")}-${String(value.getDate()).padStart(2, "0")}`;
}

export function shortDate(value: string) {
  const [, month, day] = value.split("-").map(Number);
  return `${month}/${day}`;
}

function parseDate(value: string) {
  const [year, month, day] = value.split("-").map(Number);
  return new Date(Date.UTC(year, month - 1, day));
}

function utcDateKey(value: Date) {
  return `${value.getUTCFullYear()}-${String(value.getUTCMonth() + 1).padStart(2, "0")}-${String(value.getUTCDate()).padStart(2, "0")}`;
}
