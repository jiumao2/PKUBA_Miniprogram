import { useMemo, useState } from "react";
import type { CapacityLedgerRow } from "@pkuba/api-client";

import "./capacity-calendar.css";

type DaySummary = {
  date: string;
  gameCount: number;
  reservationCount: number;
  effectiveCapacity: number;
  remainingCount: number;
  overCapacity: boolean;
  hasOverride: boolean;
};

type CalendarDay = DaySummary & { inRange: boolean };

const weekdayLabels = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"];

export function CapacityCalendar({
  ledger,
  variant = "panel",
  today = todayInShanghai(),
}: {
  ledger: CapacityLedgerRow[];
  variant?: "panel" | "embedded";
  today?: string;
}) {
  const calendar = useMemo(() => buildCalendar(ledger), [ledger]);
  const [hoveredDate, setHoveredDate] = useState<string | null>(null);
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const activeDate = hoveredDate ?? selectedDate;
  const activeDay = activeDate ? calendar?.byDate.get(activeDate) : undefined;

  if (!calendar) {
    return (
      <section className={`capacity-calendar capacity-calendar-${variant}`}>
        <header className="capacity-calendar-heading">
          <div>
            <p className="eyebrow">赛季负载</p>
            <h2>比赛与容量</h2>
          </div>
        </header>
        <p className="capacity-calendar-empty">当前赛季还没有比赛，排入首场比赛后将生成周历。</p>
      </section>
    );
  }

  const interaction = {
    activeDate,
    setHoveredDate,
    setSelectedDate: (date: string) => setSelectedDate((current) => current === date ? null : date),
  };

  return (
    <section className={`capacity-calendar capacity-calendar-${variant}`}>
      <header className="capacity-calendar-heading">
        <div>
          <p className="eyebrow">赛季负载</p>
          <h2>比赛与容量</h2>
        </div>
        <span className="capacity-calendar-range">
          {shortDate(calendar.firstDate)}—{shortDate(calendar.lastDate)} · {calendar.weeks.length} 周
        </span>
      </header>

      <div className="capacity-calendar-summary" aria-live="polite">
        {activeDay ? (
          <>
            <strong>{fullDate(activeDay.date)}</strong>
            <span>已排 {activeDay.gameCount} 场</span>
            <span>可用 {activeDay.remainingCount} 场</span>
            {activeDay.reservationCount > 0 && <span>含 {activeDay.reservationCount} 个预留</span>}
            {activeDay.hasOverride && <b>特殊容量</b>}
            {activeDay.overCapacity && <b className="capacity-over-label">有时段超容</b>}
          </>
        ) : (
          <>
            <strong>{calendar.gameDayCount} 个比赛日</strong>
            <span>共 {calendar.totalGames} 场</span>
            <span>移入日期可查看当天明细</span>
          </>
        )}
      </div>

      <div className="capacity-calendar-pair">
        <CalendarTable
          title="每日比赛数量"
          detail="红色越深，当天比赛越集中"
          metric="games"
          weeks={calendar.weeks}
          maxValue={calendar.maxGames}
          today={today}
          interaction={interaction}
        />
        <CalendarTable
          title="当天可用场次"
          detail="已扣除正式比赛与有效调赛预留"
          metric="remaining"
          weeks={calendar.weeks}
          maxValue={calendar.maxRemaining}
          today={today}
          interaction={interaction}
        />
      </div>
    </section>
  );
}

function CalendarTable({
  title,
  detail,
  metric,
  weeks,
  maxValue,
  today,
  interaction,
}: {
  title: string;
  detail: string;
  metric: "games" | "remaining";
  weeks: CalendarDay[][];
  maxValue: number;
  today: string;
  interaction: {
    activeDate: string | null;
    setHoveredDate: (date: string | null) => void;
    setSelectedDate: (date: string) => void;
  };
}) {
  return (
    <section className={`capacity-calendar-block capacity-calendar-${metric}`}>
      <header>
        <h3>{title}</h3>
        <p>{detail}</p>
      </header>
      <div className="capacity-calendar-scroll">
        <table>
          <thead>
            <tr>{weekdayLabels.map((label) => <th key={label} scope="col">{label}</th>)}</tr>
          </thead>
          <tbody>
            {weeks.map((week, weekIndex) => (
              <tr key={week[0]?.date ?? weekIndex}>
                {week.map((day) => {
                  if (!day.inRange) {
                    return <td className="capacity-day-outside" key={day.date} aria-hidden="true" />;
                  }
                  const value = metric === "games" ? day.gameCount : day.remainingCount;
                  const level = intensity(value, maxValue);
                  const isToday = day.date === today;
                  const status = day.overCapacity
                    ? "，有时段超出容量"
                    : day.hasOverride
                      ? "，使用特殊容量"
                      : "";
                  return (
                    <td
                      aria-current={isToday ? "date" : undefined}
                      aria-label={`${fullDate(day.date)}，已排 ${day.gameCount} 场，可用 ${day.remainingCount} 场${status}`}
                      className={[
                        "capacity-day",
                        `level-${level}`,
                        interaction.activeDate === day.date ? "is-active" : "",
                        isToday ? "is-today" : "",
                        day.overCapacity ? "is-over" : "",
                      ].filter(Boolean).join(" ")}
                      key={day.date}
                      onBlur={() => interaction.setHoveredDate(null)}
                      onClick={() => interaction.setSelectedDate(day.date)}
                      onFocus={() => interaction.setHoveredDate(day.date)}
                      onMouseEnter={() => interaction.setHoveredDate(day.date)}
                      onMouseLeave={() => interaction.setHoveredDate(null)}
                      tabIndex={0}
                    >
                      <span>{shortDate(day.date)}</span>
                      <strong>{value}</strong>
                      {isToday && <em aria-hidden="true">今</em>}
                      {day.hasOverride && <i aria-hidden="true" />}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <footer className="capacity-calendar-legend">
        <span>0</span>
        {[1, 2, 3, 4].map((level) => <i className={`level-${level}`} key={level} />)}
        <span>多</span>
      </footer>
    </section>
  );
}

function buildCalendar(ledger: CapacityLedgerRow[]) {
  const byDate = new Map<string, DaySummary>();
  ledger.forEach((row) => {
    const current = byDate.get(row.date) ?? {
      date: row.date,
      gameCount: 0,
      reservationCount: 0,
      effectiveCapacity: 0,
      remainingCount: 0,
      overCapacity: false,
      hasOverride: false,
    };
    current.gameCount += row.game_count;
    current.reservationCount += row.reservation_count;
    current.effectiveCapacity += row.effective_capacity;
    current.remainingCount += row.remaining_count;
    current.overCapacity ||= row.over_capacity;
    current.hasOverride ||= row.override_capacity !== null;
    byDate.set(row.date, current);
  });

  const matchDates = [...byDate.values()]
    .filter((day) => day.gameCount > 0)
    .map((day) => day.date)
    .sort();
  if (matchDates.length === 0) return null;

  const firstDate = matchDates[0];
  const lastDate = matchDates[matchDates.length - 1];
  const firstMonday = moveDate(firstDate, -mondayIndex(firstDate));
  const lastSunday = moveDate(lastDate, 6 - mondayIndex(lastDate));
  const weeks: CalendarDay[][] = [];
  let cursor = firstMonday;
  while (cursor <= lastSunday) {
    const week: CalendarDay[] = [];
    for (let index = 0; index < 7; index += 1) {
      const summary = byDate.get(cursor) ?? {
        date: cursor,
        gameCount: 0,
        reservationCount: 0,
        effectiveCapacity: 0,
        remainingCount: 0,
        overCapacity: false,
        hasOverride: false,
      };
      week.push({ ...summary, inRange: cursor >= firstDate && cursor <= lastDate });
      cursor = moveDate(cursor, 1);
    }
    weeks.push(week);
  }

  const visibleDays = weeks.flat().filter((day) => day.inRange);
  return {
    byDate,
    firstDate,
    lastDate,
    weeks,
    gameDayCount: matchDates.length,
    totalGames: visibleDays.reduce((sum, day) => sum + day.gameCount, 0),
    maxGames: Math.max(1, ...visibleDays.map((day) => day.gameCount)),
    maxRemaining: Math.max(1, ...visibleDays.map((day) => day.remainingCount)),
  };
}

function intensity(value: number, maxValue: number) {
  if (value <= 0) return 0;
  return Math.max(1, Math.min(4, Math.ceil(value / maxValue * 4)));
}

function mondayIndex(value: string) {
  const day = new Date(`${value}T00:00:00Z`).getUTCDay();
  return (day + 6) % 7;
}

function moveDate(value: string, days: number) {
  const date = new Date(`${value}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}

function shortDate(value: string) {
  const [, month, day] = value.split("-").map(Number);
  return `${month}/${day}`;
}

function fullDate(value: string) {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "long",
    day: "numeric",
    weekday: "short",
    timeZone: "UTC",
  }).format(new Date(`${value}T00:00:00Z`));
}

function todayInShanghai() {
  const parts = new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    timeZone: "Asia/Shanghai",
  }).formatToParts(new Date());
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${values.year}-${values.month}-${values.day}`;
}
