import { useEffect, useMemo, useState } from "react";
import { createPkubaClient, type Game, type Season } from "@pkuba/api-client";
import logoUrl from "@pkuba/design-tokens/pkuba-logo.png";

import { groupGamesByDate } from "./domain";
import { ScheduleOverview } from "./ScheduleOverview";

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
        <img className="brand" src={logoUrl} alt="北大篮协 PKUBA·1997" />
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
              <Metric
                label="赛季状态"
                value={season.status === "PUBLISHED" ? "已公开" : season.status === "ARCHIVED" ? "已归档" : "准备中"}
              />
              <Metric label="已排比赛" value={`${games.length} 场`} />
              <Metric label="待抽签占位" value={`${unresolved} 场`} />
              <Metric label="领队不可调" value={`${locked} 场`} />
            </section>

            <ScheduleOverview gameDays={gameDays} />
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
