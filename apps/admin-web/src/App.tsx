import { useEffect, useMemo, useState } from "react";
import { createPkubaClient, type Game, type Season } from "@pkuba/api-client";
import logoUrl from "@pkuba/design-tokens/logo-full.svg";

import { formatGameDate, groupGamesByDate } from "./domain";

const client = createPkubaClient(import.meta.env.VITE_API_BASE_URL ?? "");

const navigation = [
  "总览",
  "赛季与组别",
  "球队与名单",
  "赛程导入",
  "抽签映射",
  "调赛处理",
  "管理员与审计",
];

export function App() {
  const [season, setSeason] = useState<Season | null>(null);
  const [games, setGames] = useState<Game[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    Promise.all([client.getCurrentSeason(), client.getGames()])
      .then(([nextSeason, nextGames]) => {
        if (!active) return;
        setSeason(nextSeason);
        setGames(nextGames);
      })
      .catch((reason: unknown) => {
        if (active) setError(reason instanceof Error ? reason.message : "无法读取赛事数据");
      })
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, []);

  const gameDays = useMemo(() => groupGamesByDate(games), [games]);
  const unresolved = games.filter((game) => !game.participants_resolved).length;
  const locked = games.filter((game) => !game.leader_adjustable).length;

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <img className="brand" src={logoUrl} alt="PKUBA 1997" />
        <nav aria-label="赛事管理导航">
          {navigation.map((item, index) => (
            <button className={index === 0 ? "nav-item active" : "nav-item"} key={item} type="button">
              <span>{item}</span>
              {index > 0 && <small>待接入</small>}
            </button>
          ))}
        </nav>
        <div className="sidebar-foot">本地开发环境 · PostgreSQL 权威数据</div>
      </aside>

      <main className="workspace">
        <header className="topbar">
          <div>
            <p className="eyebrow">PKUBA 赛事工作台</p>
            <h1>{season?.name ?? "赛事总览"}</h1>
          </div>
          <div className="topbar-actions">
            <span className="environment">开发环境</span>
            <button className="primary-action" type="button" disabled>
              新建赛季
            </button>
          </div>
        </header>

        {loading && <StatePanel title="正在读取赛程" detail="数据来自本地 Django API。" />}
        {error && (
          <StatePanel
            title="暂时无法连接后端"
            detail={`${error}。请确认 Docker Desktop 与 API 服务已启动后刷新。`}
            tone="error"
          />
        )}
        {!loading && !error && season && (
          <>
            <section className="metrics" aria-label="赛季摘要">
              <Metric label="公开状态" value={season.status === "ACTIVE" ? "正式进行中" : "抽签前公开"} />
              <Metric label="已排比赛" value={`${games.length} 场`} />
              <Metric label="待抽签占位" value={`${unresolved} 场`} />
              <Metric label="领队不可调" value={`${locked} 场`} />
            </section>

            <section className="panel schedule-panel">
              <div className="panel-heading">
                <div>
                  <p className="eyebrow">公开接口实时数据</p>
                  <h2>近期赛程</h2>
                </div>
                <span className="subtle">共 {gameDays.length} 个比赛日</span>
              </div>

              {gameDays.length === 0 ? (
                <div className="empty-state">赛季已公开，但尚未安排比赛。</div>
              ) : (
                <div className="schedule-table" role="table" aria-label="近期赛程">
                  {gameDays.slice(0, 4).map((day) => (
                    <div className="day-group" key={day.date}>
                      <div className="day-label">{formatGameDate(day.date)}</div>
                      <div className="day-games">
                        {day.games.map((game) => (
                          <div className="game-row" role="row" key={game.id}>
                            <span className="game-time">{game.start_time}</span>
                            <span className="game-code">{game.code}</span>
                            <strong>{game.home_name}</strong>
                            <span className="versus">vs</span>
                            <strong>{game.away_name}</strong>
                            <span className="game-meta">{game.venue_name}</span>
                            <span className={game.participants_resolved ? "status ready" : "status waiting"}>
                              {game.participants_resolved ? "已确认" : "待抽签"}
                            </span>
                          </div>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </section>
          </>
        )}
      </main>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="metric">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function StatePanel({
  title,
  detail,
  tone = "neutral",
}: {
  title: string;
  detail: string;
  tone?: "neutral" | "error";
}) {
  return (
    <section className={`state-panel ${tone}`}>
      <h2>{title}</h2>
      <p>{detail}</p>
    </section>
  );
}
