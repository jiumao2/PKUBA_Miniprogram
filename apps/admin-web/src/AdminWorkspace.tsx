import { type FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import {
  createAdminClient,
  createPkubaClient,
  ApiError,
  type AdminAccount,
  type AdminSeason,
  type CapacityLedgerRow,
  type Game,
  type MobileAdminGame,
  type Season,
} from "@pkuba/api-client";
import logoUrl from "@pkuba/design-tokens/pkuba-logo.png";

import { groupGamesByDate } from "./domain";
import { AdvancedDataPage } from "./AdvancedDataPage";
import { ArchiveManagementPage } from "./ArchiveManagementPage";
import { AdminAccountsPage } from "./AdminAccountsPage";
import { CapacityCalendar } from "./CapacityCalendar";
import { DrawMappingPage } from "./DrawMappingPage";
import { LoginScreen } from "./LoginScreen";
import { CompetitionMediaPage } from "./CompetitionMediaPage";
import { RescheduleManagementPage } from "./RescheduleManagementPage";
import { ScheduleEditorPage } from "./ScheduleEditorPage";
import { SchedulePlannerWorkspace } from "./SchedulePlannerWorkspace";
import { ScheduleOverview } from "./ScheduleOverview";
import { SeasonManagementPage } from "./SeasonManagementPage";
import { TeamRosterPage } from "./TeamRosterPage";

const baseUrl = import.meta.env.VITE_API_BASE_URL ?? "";
const client = createPkubaClient(baseUrl);
type AdminClient = ReturnType<typeof createAdminClient>;

const navigation = [
  { id: "overview", label: "总览", available: true },
  { id: "scoresheets", label: "记录表核对", available: true },
  { id: "season", label: "赛季与组别", available: true },
  { id: "teams", label: "球队与名单", available: true },
  { id: "schedule-import", label: "赛程编排", available: true },
  { id: "schedule-edit", label: "赛程编辑", available: true },
  { id: "draw", label: "抽签映射", available: true },
  { id: "reschedule", label: "调赛处理", available: true },
  { id: "media", label: "比赛资料", available: true },
  { id: "admins", label: "管理员账户", available: true },
  { id: "archives", label: "备份与归档", available: true },
  { id: "advanced", label: "高级数据", available: true },
] as const;

type PageId = (typeof navigation)[number]["id"];
const superadminPages: PageId[] = [
  "season",
  "teams",
  "schedule-import",
  "schedule-edit",
  "draw",
  "reschedule",
  "media",
  "admins",
  "archives",
  "advanced",
];

export function AdminWorkspace() {
  const [account, setAccount] = useState<AdminAccount | null>();
  const [season, setSeason] = useState<Season | null>(null);
  const [games, setGames] = useState<Game[]>([]);
  const [adminSeasons, setAdminSeasons] = useState<AdminSeason[]>([]);
  const [selectedAdminSeasonId, setSelectedAdminSeasonId] = useState("");
  const [adminGames, setAdminGames] = useState<MobileAdminGame[]>([]);
  const [capacityLedger, setCapacityLedger] = useState<CapacityLedgerRow[]>([]);
  const [page, setPage] = useState<PageId>("overview");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showPasswordDialog, setShowPasswordDialog] = useState(false);
  const [passwordNotice, setPasswordNotice] = useState<string | null>(null);
  const adminClient = useMemo(
    () => createAdminClient(baseUrl, () => setAccount(null)),
    [],
  );

  const loadPublicData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const nextSeason = await client.getCurrentSeason();
      const [nextGames, nextLedger] = await Promise.all([
        client.getGames(),
        adminClient.getCapacityLedger(nextSeason.id),
      ]);
      setSeason(nextSeason);
      setGames(nextGames);
      setCapacityLedger(nextLedger);
    } catch (reason: unknown) {
      if (reason instanceof ApiError && reason.code === "NO_PUBLIC_SEASON") {
        setSeason(null);
        setGames([]);
        setCapacityLedger([]);
      } else {
        setError(reason instanceof Error ? reason.message : "无法读取赛事数据");
      }
    } finally {
      setLoading(false);
    }
  }, []);

  const loadAdminData = useCallback(async (preferredSeasonId?: string) => {
    const nextSeasons = await adminClient.listAdminSeasons();
    setAdminSeasons(nextSeasons);
    const selected =
      nextSeasons.find((item) => item.id === preferredSeasonId) ??
      nextSeasons.find((item) => item.status === "SETUP") ??
      nextSeasons[0];
    if (!selected) {
      setSelectedAdminSeasonId("");
      setAdminGames([]);
      return;
    }
    setSelectedAdminSeasonId(selected.id);
    setAdminGames(await adminClient.listAdminScheduleGames(selected.id));
  }, []);

  const refreshPublicData = useCallback(async () => {
    try {
      const nextSeason = await client.getCurrentSeason();
      const [nextGames, nextLedger] = await Promise.all([
        client.getGames(),
        adminClient.getCapacityLedger(nextSeason.id),
      ]);
      setSeason(nextSeason);
      setGames(nextGames);
      setCapacityLedger(nextLedger);
    } catch (reason: unknown) {
      if (reason instanceof ApiError && reason.code === "NO_PUBLIC_SEASON") {
        setSeason(null);
        setGames([]);
        setCapacityLedger([]);
      } else {
        setError(reason instanceof Error ? reason.message : "无法刷新赛事数据");
      }
    }
  }, []);

  const refreshWorkspaceData = useCallback(async (preferredSeasonId?: string) => {
    await Promise.all([
      refreshPublicData(),
      loadAdminData(preferredSeasonId ?? selectedAdminSeasonId),
    ]);
  }, [loadAdminData, refreshPublicData, selectedAdminSeasonId]);

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
    if (account) {
      void loadPublicData();
      if (account.role === "SUPERADMIN") {
        void loadAdminData().catch((reason: unknown) => {
          setError(reason instanceof Error ? reason.message : "无法读取管理赛季");
        });
      } else {
        setAdminSeasons([]);
        setSelectedAdminSeasonId("");
        setAdminGames([]);
      }
    }
  }, [account, loadAdminData, loadPublicData]);

  useEffect(() => {
    if (
      account &&
      account.role !== "SUPERADMIN" &&
      superadminPages.includes(page)
    ) {
      setPage("overview");
    }
  }, [account, page]);

  useEffect(() => {
    if (!selectedAdminSeasonId) return;
    void adminClient
      .listAdminScheduleGames(selectedAdminSeasonId)
      .then(setAdminGames)
      .catch((reason: unknown) => {
        setError(reason instanceof Error ? reason.message : "无法读取管理赛程");
      });
  }, [selectedAdminSeasonId]);

  const gameDays = useMemo(() => groupGamesByDate(games), [games]);
  const selectedAdminSeason = adminSeasons.find(
    (item) => item.id === selectedAdminSeasonId,
  );

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
    (!superadminPages.includes(pageId) ||
      account.role === "SUPERADMIN");

  const handleLogout = async () => {
    await adminClient.logout();
    setAccount(null);
  };

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <img className="brand" src={logoUrl} alt="北大篮协 PKUBA·1997" />
        <nav aria-label="赛事管理导航">
          {navigation.map((item) => (
            <button
              className={page === item.id ? "nav-item active" : "nav-item"}
              disabled={!canOpenPage(item.id, item.available)}
              key={item.id}
              onClick={() => {
                if (item.id === "scoresheets") {
                  window.location.assign("/scoresheet.html");
                  return;
                }
                setPage(item.id);
              }}
              type="button"
            >
              <span>{item.label}</span>
              {!item.available && <small>待接入</small>}
              {superadminPages.includes(item.id) &&
                account.role !== "SUPERADMIN" && (
                <small>需超级管理员</small>
              )}
            </button>
          ))}
        </nav>
        <div className="sidebar-account">
          <strong>{account.username}</strong>
          <span>{account.role === "SUPERADMIN" ? "超级管理员" : "普通管理员"}</span>
          <button type="button" onClick={() => void handleLogout()}>
            退出登录
          </button>
        </div>
      </aside>

      <main className={page === "schedule-import" ? "workspace workspace-schedule-planner" : "workspace"}>
        <header className="topbar">
          <div>
            <p className="eyebrow">PKUBA 赛事工作台</p>
            <h1>
              {page === "schedule-import"
                ? "赛程编排"
                : page === "season"
                  ? "赛季与组别"
                : page === "teams"
                  ? "球队与名单"
                : page === "schedule-edit"
                  ? "赛程编辑"
                : page === "draw"
                  ? "抽签映射"
                : page === "media"
                    ? "比赛资料"
                : page === "reschedule"
                  ? "调赛处理"
                : page === "admins"
                  ? "管理员账户"
                : page === "archives"
                  ? "备份与归档"
                : page === "advanced"
                  ? "高级数据"
                  : season?.name ?? "赛事总览"}
            </h1>
          </div>
          <div className="topbar-actions">
            <button
              className="account-security-action"
              type="button"
              onClick={() => {
                setPasswordNotice(null);
                setShowPasswordDialog(true);
              }}
            >
              修改密码
            </button>
            <span className="environment">本地开发</span>
            <span className="account-role">
              {account.role === "SUPERADMIN" ? "超级管理员" : "普通管理员"}
            </span>
          </div>
        </header>

        {passwordNotice && (
          <div className="workspace-notice" role="status">
            <span>{passwordNotice}</span>
            <button type="button" onClick={() => setPasswordNotice(null)} aria-label="关闭提示">×</button>
          </div>
        )}

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
        {!loading && !error && selectedAdminSeason && page === "schedule-import" && (
          <SchedulePlannerWorkspace
            account={account}
            client={adminClient}
            seasons={adminSeasons}
            season={selectedAdminSeason}
            onSeasonChange={setSelectedAdminSeasonId}
            onDataChanged={refreshWorkspaceData}
            onOpenConfiguration={() => setPage("season")}
            onOpenEditor={() => setPage("schedule-edit")}
          />
        )}
        {!loading && !error && page === "season" && account.role === "SUPERADMIN" && (
          <SeasonManagementPage
            client={adminClient}
            seasons={adminSeasons}
            seasonId={selectedAdminSeasonId}
            onSeasonChange={setSelectedAdminSeasonId}
            onDataChanged={refreshWorkspaceData}
          />
        )}
        {!loading && !error && selectedAdminSeason && page === "teams" && account.role === "SUPERADMIN" && (
          <TeamRosterPage
            client={adminClient}
            seasons={adminSeasons}
            seasonId={selectedAdminSeason.id}
            onSeasonChange={setSelectedAdminSeasonId}
            onDataChanged={refreshWorkspaceData}
            onOpenConfiguration={() => setPage("season")}
          />
        )}
        {!loading && !error && selectedAdminSeason && page === "schedule-edit" && (
          <ScheduleEditorPage
            client={adminClient}
            games={adminGames}
            seasons={adminSeasons}
            season={selectedAdminSeason}
            onSeasonChange={setSelectedAdminSeasonId}
            onUpdated={refreshWorkspaceData}
          />
        )}
        {!loading && !error && selectedAdminSeason && page === "draw" && account.role === "SUPERADMIN" && (
          <DrawMappingPage
            client={adminClient}
            seasons={adminSeasons}
            seasonId={selectedAdminSeason.id}
            onSeasonChange={setSelectedAdminSeasonId}
            onDataChanged={() => refreshWorkspaceData(selectedAdminSeason.id)}
            onOpenTeams={() => setPage("teams")}
            onOpenConfiguration={() => setPage("season")}
          />
        )}
        {!loading && !error && page === "media" && (
          <CompetitionMediaPage
            accountRole={account.role}
            client={adminClient}
            seasonId={selectedAdminSeasonId || season?.id || ""}
          />
        )}
        {!loading && !error && page === "reschedule" && (
          <RescheduleManagementPage client={adminClient} />
        )}
        {page === "admins" && (
          <AdminAccountsPage
            account={account}
            client={adminClient}
            season={selectedAdminSeason ?? null}
          />
        )}
        {!loading && !error && page === "archives" && account.role === "SUPERADMIN" && (
          <ArchiveManagementPage
            client={adminClient}
            seasons={adminSeasons}
            seasonId={selectedAdminSeasonId}
            onSeasonChange={setSelectedAdminSeasonId}
          />
        )}
        {page === "advanced" && account.role === "SUPERADMIN" && (
          <AdvancedDataPage client={adminClient} />
        )}
        {!loading && !error && season && page === "overview" && (
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
            <CapacityCalendar ledger={capacityLedger} />
            <ScheduleOverview gameDays={gameDays} />
          </>
        )}
      </main>
      {showPasswordDialog && (
        <PasswordChangeDialog
          client={adminClient}
          onClose={() => setShowPasswordDialog(false)}
          onChanged={(updatedAccount) => {
            setAccount(updatedAccount);
            setShowPasswordDialog(false);
            setPasswordNotice("密码已修改，当前登录保持有效；下次登录请使用新密码。");
          }}
        />
      )}
    </div>
  );
}

function PasswordChangeDialog({
  client,
  onClose,
  onChanged,
}: {
  client: AdminClient;
  onClose: () => void;
  onChanged: (account: AdminAccount) => void;
}) {
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [newPasswordAgain, setNewPasswordAgain] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError(null);
    if (!currentPassword || !newPassword) {
      setError("请填写当前密码和新密码。");
      return;
    }
    if (Array.from(newPassword).length < 4) {
      setError("新密码至少需要 4 个字符。");
      return;
    }
    if (newPassword !== newPasswordAgain) {
      setError("两次输入的新密码不一致。");
      return;
    }
    setBusy(true);
    try {
      onChanged(await client.changePassword(currentPassword, newPassword));
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "密码修改失败");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="dialog-backdrop" role="presentation" onMouseDown={onClose}>
      <section
        className="password-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="password-dialog-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="dialog-heading">
          <div>
            <p className="eyebrow">账号安全</p>
            <h2 id="password-dialog-title">修改网页登录密码</h2>
          </div>
          <button className="dialog-close" type="button" onClick={onClose} aria-label="关闭">×</button>
        </div>
        <p className="dialog-detail">网页密码由管理员在小程序注册时自行设置。新密码只需至少 4 个字符，可以与当前密码相同；修改密码不会改变赛季邀请码。</p>
        <form className="password-form" onSubmit={(event) => void submit(event)}>
          <label>
            当前密码
            <input
              autoComplete="current-password"
              autoFocus
              type="password"
              value={currentPassword}
              onChange={(event) => setCurrentPassword(event.target.value)}
            />
          </label>
          <label>
            新密码
            <input
              autoComplete="new-password"
              type="password"
              value={newPassword}
              onChange={(event) => setNewPassword(event.target.value)}
            />
          </label>
          <label>
            再次输入新密码
            <input
              autoComplete="new-password"
              type="password"
              value={newPasswordAgain}
              onChange={(event) => setNewPasswordAgain(event.target.value)}
            />
          </label>
          {error && <p className="form-error" role="alert">{error}</p>}
          <div className="dialog-actions">
            <button className="dialog-secondary" type="button" onClick={onClose}>取消</button>
            <button className="primary-action" type="submit" disabled={busy}>
              {busy ? "正在保存…" : "保存新密码"}
            </button>
          </div>
        </form>
      </section>
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
