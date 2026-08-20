import type { Game } from "@pkuba/api-client";

import {
  formatGameDate,
  selectRecentGameDays,
  type GameDay,
} from "./domain";

export function ScheduleOverview({
  gameDays,
  today = todayInBeijing(),
}: {
  gameDays: GameDay[];
  today?: string;
}) {
  const visibleDays = selectRecentGameDays(gameDays, today);

  return (
    <section className="panel schedule-panel">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">公开赛程</p>
          <h2>近期赛程</h2>
        </div>
        <span className="subtle">
          显示 {visibleDays.length} / {gameDays.length} 个比赛日
        </span>
      </div>
      {visibleDays.length === 0 ? (
        <div className="empty-state">赛季已公开，但尚未安排比赛。</div>
      ) : (
        <div className="admin-schedule-timeline" aria-label="近期赛程">
          {visibleDays.map((day) => (
            <section className="admin-timeline-day" key={day.date}>
              <header className="admin-timeline-day-heading">
                <h3>{formatGameDate(day.date)}</h3>
                <span>{day.games.length} 场</span>
              </header>
              {day.times.map((timeGroup) => (
                <section className="admin-timeline-time-block" key={`${day.date}-${timeGroup.time}`}>
                  <header className="admin-timeline-time-heading">
                    <time dateTime={`${day.date}T${timeGroup.time}`}>{timeGroup.time}</time>
                    <span>{timeGroup.games.length} 场</span>
                  </header>
                  <div className="admin-timeline-games">
                    {timeGroup.games.map((game) => (
                      <AdminTimelineGame game={game} key={game.id} />
                    ))}
                  </div>
                </section>
              ))}
            </section>
          ))}
        </div>
      )}
    </section>
  );
}

function AdminTimelineGame({ game }: { game: Game }) {
  const women = game.division_gender === "WOMEN";
  const hasScore = game.home_score !== null && game.away_score !== null;
  const scoreLabel = hasScore ? `${game.home_score}:${game.away_score}` : "尚无比分";

  return (
    <article
      className={`admin-timeline-game ${women ? "admin-timeline-women" : "admin-timeline-men"}`}
      aria-label={`${game.home_name} 对 ${game.away_name}，${scoreLabel}`}
    >
      <div className="admin-timeline-game-topline">
        <span className="admin-timeline-division">
          {game.division_name}
        </span>
        <span className="admin-timeline-venue">{game.venue_name}</span>
      </div>
      <div className="admin-timeline-team-row">
        <strong title={game.home_name}>{game.home_name}</strong>
        <span className={`admin-timeline-score ${hasScore ? "has-score" : "no-score"}`}>
          {hasScore ? game.home_score : ""}
        </span>
      </div>
      <div className="admin-timeline-team-row">
        <strong title={game.away_name}>{game.away_name}</strong>
        <span className={`admin-timeline-score ${hasScore ? "has-score" : "no-score"}`}>
          {hasScore ? game.away_score : "vs"}
        </span>
      </div>
      <div className="admin-timeline-game-footline">
        <span>{game.group_name ?? stageLabel(game.stage)}</span>
        <span className="admin-timeline-footnote">
          {game.status === "FORFEIT" && <b className="admin-forfeit-label">弃权</b>}
          {!game.participants_resolved && <b>对阵待定</b>}
          <small>{game.code}</small>
        </span>
      </div>
    </article>
  );
}

function stageLabel(stage: string) {
  const labels: Record<string, string> = {
    GROUP: "小组赛",
    ROUND_ROBIN: "循环赛",
    KNOCKOUT: "淘汰赛",
    SEMIFINAL: "半决赛",
    FINAL: "决赛",
    RELEGATION: "保级赛",
  };
  return labels[stage] ?? "比赛";
}

function todayInBeijing() {
  const parts = new Intl.DateTimeFormat("en", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(new Date());
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${values.year}-${values.month}-${values.day}`;
}
