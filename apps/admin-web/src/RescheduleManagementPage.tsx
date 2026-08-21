import { type FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import {
  ApiError,
  type AdminReschedulePage,
  type AdminRescheduleRequest,
  type RescheduleVoterTeam,
  createAdminClient,
} from "@pkuba/api-client";

import "./reschedule-management.css";

type AdminClient = ReturnType<typeof createAdminClient>;
type ViewFilter = "active" | "history";

const statusOptions = [
  ["", "全部状态"],
  ["WAITING_OPPONENT", "等待对手"],
  ["WAITING_ADMIN_DECISION", "等待管理员决定"],
  ["WAITING_SELECTED_TEAMS", "等待指定球队"],
  ["WAITING_ADMIN_FINAL", "等待管理员终审"],
  ["APPROVED", "通过"],
  ["REJECTED", "拒绝"],
  ["WITHDRAWN", "撤回"],
  ["EXPIRED", "过期"],
  ["ADMIN_CANCELLED", "管理员取消"],
] as const;

export function RescheduleManagementPage({ client, initialDataset = null }: { client: AdminClient; initialDataset?: AdminReschedulePage | null }) {
  const [dataset, setDataset] = useState<AdminReschedulePage | null>(initialDataset);
  const [view, setView] = useState<ViewFilter>("active");
  const [status, setStatus] = useState("");
  const [requestType, setRequestType] = useState("");
  const [queryInput, setQueryInput] = useState("");
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(1);
  const [selectedId, setSelectedId] = useState(initialDataset?.items[0]?.id ?? "");
  const [loading, setLoading] = useState(initialDataset === null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [voteRequestId, setVoteRequestId] = useState("");
  const [candidates, setCandidates] = useState<RescheduleVoterTeam[]>([]);
  const [selectedVoters, setSelectedVoters] = useState<string[]>([]);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const next = await client.listAdminRescheduleRequests({
        view,
        status,
        requestType,
        query,
        page,
        pageSize: 30,
      });
      setDataset(next);
      setSelectedId((current) =>
        next.items.some((item) => item.id === current)
          ? current
          : next.items[0]?.id ?? "",
      );
    } catch (reason: unknown) {
      setDataset(null);
      setError(reason instanceof Error ? reason.message : "无法读取调赛申请");
    } finally {
      setLoading(false);
    }
  }, [client, page, query, requestType, status, view]);

  useEffect(() => { void load(); }, [load]);

  const selected = dataset?.items.find((item) => item.id === selectedId) ?? null;
  const pages = Math.max(Math.ceil((dataset?.total ?? 0) / 30), 1);
  const divisions = useMemo(() => {
    const found = new Map<string, string>();
    for (const item of dataset?.items ?? []) {
      found.set(item.game.division_name, item.game.division_name);
    }
    return [...found.values()];
  }, [dataset]);

  const applySearch = (event: FormEvent) => {
    event.preventDefault();
    setPage(1);
    setQuery(queryInput.trim());
  };

  const runAction = async (
    item: AdminRescheduleRequest,
    action: string,
    title: string,
    consequence: string,
    selectedTeamIds: string[] = [],
  ) => {
    if (!window.confirm(`${title}\n\n${consequence}`)) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await client.actOnAdminReschedule(item.id, {
        expected_version: item.version,
        action,
        selected_team_ids: selectedTeamIds,
      });
      setVoteRequestId("");
      setSelectedVoters([]);
      setNotice(`${title}已完成。`);
      await load();
    } catch (reason: unknown) {
      if (reason instanceof ApiError && reason.status === 409) {
        setError(`${reason.message} 页面已刷新，请重新核对后操作。`);
        await load();
      } else {
        setError(reason instanceof Error ? reason.message : "调赛处理失败");
      }
    } finally {
      setBusy(false);
    }
  };

  const openVote = async (item: AdminRescheduleRequest) => {
    setBusy(true);
    setError("");
    try {
      setCandidates(await client.getAdminRescheduleVoterCandidates(item.id));
      setSelectedVoters([]);
      setVoteRequestId(item.id);
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "无法读取可投票球队");
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="reschedule-workspace">
      <SummaryStrip dataset={dataset} />
      <div className="reschedule-layout">
        <div className="reschedule-queue">
          <div className="reschedule-tabs" aria-label="申请范围">
            <button className={view === "active" ? "active" : ""} onClick={() => { setView("active"); setStatus(""); setPage(1); }} type="button">进行中</button>
            <button className={view === "history" ? "active" : ""} onClick={() => { setView("history"); setStatus(""); setPage(1); }} type="button">历史记录</button>
          </div>
          <form className="reschedule-search" onSubmit={applySearch}>
            <input aria-label="搜索调赛申请" onChange={(event) => setQueryInput(event.target.value)} placeholder="球队或比赛代码" value={queryInput} />
            <button type="submit">搜索</button>
          </form>
          <div className="reschedule-filters">
            <select aria-label="状态" value={status} onChange={(event) => { setStatus(event.target.value); setPage(1); }}>
              {statusOptions.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select>
            <select aria-label="类型" value={requestType} onChange={(event) => { setRequestType(event.target.value); setPage(1); }}>
              <option value="">全部类型</option>
              <option value="SAME_WEEK">同周</option>
              <option value="CROSS_WEEK">跨周</option>
            </select>
          </div>
          <div className="reschedule-queue-heading">
            <span>{dataset?.season_name ?? "当前赛季"}</span>
            <strong>{dataset?.total ?? 0} 项</strong>
          </div>
          <div className="reschedule-list" aria-busy={loading}>
            {dataset?.items.map((item) => (
              <RequestRow
                item={item}
                key={item.id}
                onSelect={() => {
                  setSelectedId(item.id);
                  setVoteRequestId("");
                }}
                selected={item.id === selectedId}
              />
            ))}
            {!loading && !dataset?.items.length && <div className="reschedule-empty">当前筛选下没有申请。</div>}
          </div>
          {pages > 1 && (
            <div className="reschedule-pagination">
              <button disabled={page <= 1} onClick={() => setPage((value) => value - 1)} type="button">上一页</button>
              <span>{page} / {pages}</span>
              <button disabled={page >= pages} onClick={() => setPage((value) => value + 1)} type="button">下一页</button>
            </div>
          )}
        </div>
        <div className="reschedule-detail">
          {selected ? (
            <RequestDetail
              busy={busy}
              candidates={candidates}
              item={selected}
              onAction={(action, title, consequence) => void runAction(selected, action, title, consequence)}
              onOpenVote={() => void openVote(selected)}
              onSubmitVote={() => void runAction(
                selected,
                "ADMIN_START_VOTE",
                "发起指定球队投票",
                `将等待 ${selectedVoters.length} 支球队在确认截止前表态；原比赛锁和目标预留继续保留。`,
                selectedVoters,
              )}
              selectedVoters={selectedVoters}
              setSelectedVoters={setSelectedVoters}
              voting={voteRequestId === selected.id}
            />
          ) : (
            <div className="reschedule-detail-empty"><h2>调赛申请</h2><p>选择左侧申请查看完整状态与可执行操作。</p></div>
          )}
          {notice && <p className="reschedule-notice" role="status">{notice}</p>}
          {error && <p className="reschedule-error" role="alert">{error}</p>}
        </div>
      </div>
      {divisions.length > 0 && <span className="sr-only">当前页组别：{divisions.join("、")}</span>}
    </section>
  );
}

function SummaryStrip({ dataset }: { dataset: AdminReschedulePage | null }) {
  const values = [
    ["进行中", dataset?.summary.active ?? 0],
    ["待管理员决定", dataset?.summary.waiting_admin_decision ?? 0],
    ["等待球队", dataset?.summary.waiting_selected_teams ?? 0],
    ["待终审", dataset?.summary.waiting_admin_final ?? 0],
  ];
  return <div className="reschedule-summary">{values.map(([label, value]) => <div key={label}><strong>{value}</strong><span>{label}</span></div>)}</div>;
}

function RequestRow({ item, selected, onSelect }: { item: AdminRescheduleRequest; selected: boolean; onSelect: () => void }) {
  return (
    <button className={`reschedule-row ${selected ? "active" : ""} ${item.game.division_gender === "WOMEN" ? "women" : "men"}`} onClick={onSelect} type="button">
      <span className="reschedule-row-meta"><strong>{item.game.division_name}</strong>{item.request_type_label}<i className={`reschedule-state ${statusClass(item.status)}`}>{item.status_label}</i></span>
      <b>{item.original_home_name}<em>vs</em>{item.original_away_name}</b>
      <span className="reschedule-row-route">{shortDate(item.original_date)} {item.original_start_time} <i>→</i> {shortDate(item.target_date)} {item.target_start_time}</span>
      <small>申请方：{item.requester_team_name}{item.resources.issues.length ? ` · ${item.resources.issues.length} 项资源异常` : ""}</small>
    </button>
  );
}

function RequestDetail({ item, busy, voting, candidates, selectedVoters, setSelectedVoters, onOpenVote, onSubmitVote, onAction }: {
  item: AdminRescheduleRequest;
  busy: boolean;
  voting: boolean;
  candidates: RescheduleVoterTeam[];
  selectedVoters: string[];
  setSelectedVoters: (ids: string[]) => void;
  onOpenVote: () => void;
  onSubmitVote: () => void;
  onAction: (action: string, title: string, consequence: string) => void;
}) {
  const has = (action: string) => item.actions.includes(action);
  const overdue = !item.is_terminal && ["WAITING_OPPONENT", "WAITING_SELECTED_TEAMS"].includes(item.status) && new Date(item.confirmation_deadline).getTime() <= Date.now();
  return (
    <>
      <header className="reschedule-detail-heading">
        <div><p className="eyebrow">{item.game.division_name} · {item.request_type_label}</p><h2>{item.original_home_name} vs {item.original_away_name}</h2><span>申请方：{item.requester_team_name}</span></div>
        <i className={`reschedule-state ${statusClass(item.status)}`}>{item.status_label}</i>
      </header>
      {overdue && <div className="reschedule-overdue">球队确认时限已到，等待系统过期任务处理；页面不会自行释放锁或预留。</div>}
      <div className="reschedule-route-detail">
        <Slot title="原赛程" date={item.original_date} time={item.original_start_time} venue={item.original_venue_name} />
        <span aria-hidden="true">→</span>
        <Slot title="目标赛程" date={item.target_date} time={item.target_start_time} venue={item.target_venue_name} />
      </div>
      <section className="reschedule-resource-panel">
        <h3>锁与容量</h3>
        <dl>
          <div><dt>领队政策</dt><dd>{item.game.leader_adjustable ? "允许调赛" : "永久不可调"}</dd></div>
          <div><dt>原比赛活动锁</dt><dd>{item.resources.game_lock_matches ? "由本申请持有" : item.is_terminal ? "已释放" : "状态异常"}</dd></div>
          <div><dt>目标场地预留</dt><dd>{reservationLabel(item.resources.reservation_status)} · {item.target_venue_name}</dd></div>
          <div><dt>目标时段容量</dt><dd>{item.resources.used_count} / {item.resources.capacity}（比赛 {item.resources.game_count} + 预留 {item.resources.active_reservation_count}）</dd></div>
        </dl>
        {!!item.resources.issues.length && <ul>{item.resources.issues.map((issue) => <li key={issue}>{issue}</li>)}</ul>}
      </section>
      <section className="reschedule-confirmations">
        <div><h3>确认记录</h3><span>截止 {formatDeadline(item.confirmation_deadline)}</span></div>
        {item.confirmations.map((confirmation) => (
          <div className="confirmation-line" key={confirmation.id}>
            <span>{confirmation.purpose === "OPPONENT" ? "对手确认" : "指定球队"}</span>
            <strong>{confirmation.team_name}</strong>
            <i className={confirmation.response.toLowerCase()}>{confirmationLabel(confirmation.response)}</i>
          </div>
        ))}
      </section>
      {voting && (
        <section className="reschedule-voters">
          <h3>指定参与投票的球队</h3>
          {candidates.map((team) => (
            <label key={team.id}><input checked={selectedVoters.includes(team.id)} onChange={(event) => setSelectedVoters(event.target.checked ? [...selectedVoters, team.id] : selectedVoters.filter((id) => id !== team.id))} type="checkbox" />{team.name}<span>{team.group_name}</span></label>
          ))}
          {!candidates.length && <p>没有可指定的同组球队。</p>}
          <button className="primary-action" disabled={busy || !selectedVoters.length} onClick={onSubmitVote} type="button">确认发起投票</button>
        </section>
      )}
      {!!item.actions.length && (
        <footer className="reschedule-actions">
          {has("ADMIN_APPROVE") && <button className="approve-action" disabled={busy} onClick={() => onAction("ADMIN_APPROVE", "直接批准跨周调赛", "比赛将立即移动到已预留的目标时段，预留原子转换为正式占用。")}>直接批准</button>}
          {has("ADMIN_START_VOTE") && <button className="secondary-action" disabled={busy} onClick={onOpenVote}>指定球队投票</button>}
          {has("ADMIN_REJECT") && <button className="secondary-action" disabled={busy} onClick={() => onAction("ADMIN_REJECT", "拒绝跨周调赛", "申请将结束，原比赛活动锁和目标场地预留会同时释放。")}>拒绝</button>}
          {has("ADMIN_FINAL_APPROVE") && <button className="approve-action" disabled={busy} onClick={() => onAction("ADMIN_FINAL_APPROVE", "终审通过", "比赛将立即移动到已预留的目标时段。")}>终审通过</button>}
          {has("ADMIN_FINAL_REJECT") && <button className="secondary-action" disabled={busy} onClick={() => onAction("ADMIN_FINAL_REJECT", "终审拒绝", "申请将结束，并释放原比赛活动锁和目标预留。")}>终审拒绝</button>}
          {has("ADMIN_CANCEL") && <button className="cancel-action" disabled={busy} onClick={() => onAction("ADMIN_CANCEL", "管理员取消申请", "该申请将进入管理员取消终态，原比赛活动锁和目标预留会同时释放。")}>管理员取消</button>}
        </footer>
      )}
    </>
  );
}

function Slot({ title, date, time, venue }: { title: string; date: string; time: string; venue: string }) {
  return <div><span>{title}</span><strong>{fullDate(date)} · {time}</strong><small>{venue}</small></div>;
}

export function statusClass(status: string) {
  if (["APPROVED"].includes(status)) return "success";
  if (["REJECTED", "EXPIRED", "ADMIN_CANCELLED"].includes(status)) return "ended";
  if (status === "WAITING_ADMIN_FINAL") return "final";
  if (status === "WAITING_ADMIN_DECISION") return "decision";
  return "waiting";
}

function reservationLabel(status: string) {
  return ({ ACTIVE: "有效预留", CONVERTED: "已转正式占用", RELEASED: "已释放" } as Record<string, string>)[status] ?? status;
}

function confirmationLabel(response: string) {
  return ({ PENDING: "待确认", ACCEPTED: "已同意", REJECTED: "已拒绝" } as Record<string, string>)[response] ?? response;
}

function shortDate(value: string) { return value.slice(5).replace("-", "/"); }
function fullDate(value: string) { return new Intl.DateTimeFormat("zh-CN", { month: "long", day: "numeric", weekday: "short", timeZone: "Asia/Shanghai" }).format(new Date(`${value}T00:00:00+08:00`)); }
function formatDeadline(value: string) { return new Intl.DateTimeFormat("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Shanghai" }).format(new Date(value)); }
