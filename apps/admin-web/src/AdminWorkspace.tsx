import { useCallback, useEffect, useMemo, useState } from "react";
import {
  createAdminClient,
  createPkubaClient,
  type AdminAccount,
  type Game,
  type Season,
} from "@pkuba/api-client";
import logoUrl from "@pkuba/design-tokens/logo-full.svg";

import { formatGameDate, groupGamesByDate } from "./domain";
import { AdminAccountsPage } from "./AdminAccountsPage";
import { LoginScreen } from "./LoginScreen";
import { ScheduleImportPage } from "./ScheduleImportPage";

const baseUrl = import.meta.env.VITE_API_BASE_URL ?? "";
const client = createPkubaClient(baseUrl);
const adminClient = createAdminClient(baseUrl);

const navigation = [
  { id: "overview", label: "总览", available: true },
  { id: "season", label: "赛季与组别", available: false },
  { id: "teams", label: "球队与名单", available: false },
  { id: "schedule-import", label: "赛程导入", available: true },
  { id: "draw", label: "抽签映射", available: false },
  { id: "reschedule", label: "调赛处理", available: false },
  { id: "admins", label: "管理员账户", available: true },
] as const;

type PageId = (typeof navigation)[number]["id"];

export function AdminWorkspace() {
  const [account, setAccount] = useState<AdminAccount | null>();
  const [season, setSeason] = useState<Season | null>(null);
  const [games, setGames] = useState<Game[]>([]);
  const [page, setPage] = useState<PageId>("overview");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadPublicData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [nextSeason, nextGames] = await Promise.all([
        client.getCurrentSeason(),
        client.getGames(),
      ]);
      setSeason(nextSeason);
      setGames(nextGames);
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "无法读取赛事数据");
    } finally {
      setLoading(false);
    }
  }, []);

  const refreshPublicData = useCallback(async () => {
    try {
      const [nextSeason, nextGames] = await Promise.all([
        client.getCurrentSeason(),
        client.getGames(),
      ]);
      setSeason(nextSeason);
      setGames(nextGames);
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "无法刷新赛事数据");
    }
  }, []);

  useEffect(() => {
    adminClient
      .getSession()
      .then((session) => setAccount(session.account))
      .catch((reason: unknown) => {
        setError(reason instanceof Error ? reason.message : "无法检查登录状态");
        setAccount(null);
      });
  }, []);

  useEffect(() => {
    if (account) void loadPublicData();
  }, [account, loadPublicData]);

  const gameDays = useMemo(() => groupGamesByDate(games), [games]);

  if (account === undefined) {
    return <StatePanel title="正在检查登录状态" detail="请稍候。" />;
  }
  if (account === null) {
    return <LoginScreen client={adminClient} onLogin={setAccount} />;
  }

  const unresolved = games.filter((game) => !game.participants_resolved).length;
  const locked = games.filter((game) => !game.leader_adjustable).length;
  const canOpenPage = (pageId: PageId, available: boolean) =>
    available &&
    (!(["schedule-import", "admins"] as PageId[]).includes(pageId) ||
      account.role === "SUPERADMIN");

  const handleLogout = async () => {
    await adminClient.logout();
    setAccount(null);
  };

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <img className="brand" src={logoUrl} alt="PKUBA 1997" />
        <nav aria-label="赛事管理导航">
          {navigation.map((item) => (
            <button
              className={page === item.id ? "nav-item active" : "nav-item"}
              disabled={!canOpenPage(item.id, item.available)}
              key={item.id}
              onClick={() => setPage(item.id)}
              type="button"
            >
              <span>{item.label}</span>
              {!item.available && <small>待接入</small>}
              {(["schedule-import", "admins"] as PageId[]).includes(item.id) &&
                account.role !== "SUPERADMIN" && (
                <small>需超级管理员</small>
              )}
            </button>
          ))}
        </nav>
        <div className="sidebar-account">
          <strong>{account.display_name || account.username}</strong>
          <span>{account.role === "SUPERADMIN" ? "超级管理员" : "普通管理员"}</span>
          <button type="button" onClick={() => void handleLogout()}>
            退出登录
          </button>
        </div>
      </aside>

      <main className="workspace">
        <header className="topbar">
          <div>
            <p className="eyebrow">PKUBA 赛事工作台</p>
            <h1>
              {page === "schedule-import"
                ? "赛程导入"
                : page === "admins"
                  ? "管理员账户"
                  : season?.name ?? "赛事总览"}
            </h1>
          </div>
          <div className="topbar-actions">
            <span className="environment">本地开发</span>
            <span className="account-role">
              {account.role === "SUPERADMIN" ? "超级管理员" : "普通管理员"}
            </span>
          </div>
        </header>

        {page !== "admins" && loading && (
          <StatePanel title="正在读取赛程" detail="数据来自本地 Django API。" />
        )}
        {page !== "admins" && error && (
          <StatePanel
            title="暂时无法连接后端"
            detail={`${error}。请确认 Docker Desktop 与 API 服务已启动后刷新。`}
            tone="error"
          />
        )}
        {!loading && !error && season && page === "schedule-import" && (
          <ScheduleImportPage
            account={account}
            client={adminClient}
            games={games}
            season={season}
            onConfirmed={refreshPublicData}
          />
        )}
        {page === "admins" && (
          <AdminAccountsPage account={account} client={adminClient} />
        )}
        {!loading && !error && season && page === "overview" && (
          <>
            <section className="metrics" aria-label="赛季摘要">
              <Metric
                label="公开状态"
                value={season.status === "ACTIVE" ? "正式进行中" : "抽签前公开"}
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

function ScheduleOverview({ gameDays }: { gameDays: ReturnType<typeof groupGamesByDate> }) {
  return (
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
                    <span
                      className={game.participants_resolved ? "status ready" : "status waiting"}
                    >
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
