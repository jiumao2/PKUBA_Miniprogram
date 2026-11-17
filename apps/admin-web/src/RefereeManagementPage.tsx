import { useCallback, useEffect, useRef, useState } from "react";
import {
  ApiError, createIdempotencyKey, refereeRoleLabels,
  type AdminSeason, type PagedResponse, type RefereeCandidate, type RefereeGame,
  type RefereeQualification, type createAdminClient,
} from "@pkuba/api-client";

import { confirmAdminNavigation, useAdminDirtySource } from "./dirtyGuard";
import { formatAdminSeasonLabel } from "./seasonLabel";
import "./referee-management.css";

type Client = ReturnType<typeof createAdminClient>;
type Pending = { run: () => Promise<unknown>; label: string };

export function RefereeManagementPage({ client, seasons, seasonId, onSeasonChange }: {
  client: Client; seasons: AdminSeason[]; seasonId: string;
  onSeasonChange: (value: string) => void;
}) {
  const season = seasons.find((item) => item.id === seasonId);
  const readOnly = season?.status === "ARCHIVED";
  const [tab, setTab] = useState<"games" | "people">("games");
  const [qualifications, setQualifications] = useState<PagedResponse<RefereeQualification>>();
  const [games, setGames] = useState<PagedResponse<RefereeGame>>();
  const [peoplePage, setPeoplePage] = useState(1);
  const [gamePage, setGamePage] = useState(1);
  const [date, setDate] = useState("");
  const [username, setUsername] = useState("");
  const [editing, setEditing] = useState<RefereeGame | null>(null);
  const [selected, setSelected] = useState<Record<string, string>>({});
  const [candidates, setCandidates] = useState<RefereeCandidate[]>([]);
  const [candidatePage, setCandidatePage] = useState(0);
  const [candidateTotal, setCandidateTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [candidatesLoading, setCandidatesLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState<Pending | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const generation = useRef(0);
  const candidateGeneration = useRef(0);
  const running = useRef(false);
  const pendingRef = useRef<Pending | null>(null);
  const editingRef = useRef<RefereeGame | null>(null);
  editingRef.current = editing;
  useAdminDirtySource("referees", Boolean(editing || username.trim() || pending));

  const load = useCallback(async () => {
    if (!seasonId) return;
    const sequence = ++generation.current;
    setLoading(true);
    setError("");
    try {
      const [people, matches] = await Promise.all([
        client.listRefereeQualifications(seasonId, peoplePage),
        client.listRefereeGames(seasonId, gamePage, date),
      ]);
      if (sequence !== generation.current) return;
      setQualifications(people);
      setGames(matches);
    } catch (reason) {
      if (sequence === generation.current) {
        setError(reason instanceof Error ? reason.message : "裁判信息读取失败");
      }
    } finally {
      if (sequence === generation.current) setLoading(false);
    }
  }, [client, seasonId, peoplePage, gamePage, date]);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => () => { generation.current += 1; candidateGeneration.current += 1; }, []);

  const perform = async (command: Pending) => {
    if (running.current) return;
    running.current = true;
    const sequence = generation.current;
    pendingRef.current = command;
    setPending(command);
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await command.run();
      if (sequence !== generation.current) return;
      pendingRef.current = null;
      setPending(null);
      setUsername("");
      setEditing(null);
      candidateGeneration.current += 1;
      setNotice(`${command.label}已保存。`);
      await load();
    } catch (reason) {
      if (sequence !== generation.current) return;
      setError(reason instanceof Error ? reason.message : "保存结果暂时无法确认");
      if (reason instanceof ApiError && reason.status < 500) {
        pendingRef.current = null;
        setPending(null);
      }
    } finally {
      running.current = false;
      setBusy(false);
    }
  };

  const changeQualification = (name: string, active: boolean, version: number) => {
    if (pendingRef.current) return;
    const key = createIdempotencyKey();
    const payload = { username: name, active, expected_version: version };
    void perform({ label: "人员资格", run: () => client.setRefereeQualification(seasonId, payload, key) });
  };

  const loadCandidates = async (game: RefereeGame, page = 1) => {
    const sequence = ++candidateGeneration.current;
    setCandidatesLoading(true);
    setError("");
    try {
      const result = await client.listRefereeCandidates(seasonId, game.id, page);
      if (sequence !== candidateGeneration.current || editingRef.current?.id !== game.id) return;
      setCandidates((previous) => page === 1 ? result.items : [...previous, ...result.items]);
      setCandidatePage(result.page);
      setCandidateTotal(result.total);
    } catch (reason) {
      if (sequence === candidateGeneration.current) {
        setError(reason instanceof Error ? reason.message : "报名人员读取失败");
      }
    } finally {
      if (sequence === candidateGeneration.current) setCandidatesLoading(false);
    }
  };

  const openGame = async (game: RefereeGame) => {
    if (pendingRef.current || (editing && !(await confirmAdminNavigation()))) return;
    editingRef.current = game;
    setEditing(game);
    setSelected(Object.fromEntries(game.assignments
      .filter((item) => game.roles.includes(item.role))
      .map((item) => [item.role, item.referee_id])));
    setCandidates([]);
    setCandidatePage(0);
    setCandidateTotal(0);
    void loadCandidates(game);
  };

  const saveAssignments = () => {
    if (!editing || pendingRef.current) return;
    const game = editing;
    const key = createIdempotencyKey();
    const assignments = game.status === "SCHEDULED" ? game.roles.flatMap((role) => selected[role]
      ? [{ role, referee_id: selected[role] }] : []) : [];
    void perform({ label: "比赛选派", run: () => client.assignReferees(seasonId, game.id, {
      expected_version: game.version, assignments,
    }, key) });
  };

  const locked = busy || Boolean(pending);
  const roleNames = (game: RefereeGame) => game.roles.flatMap((role) => {
    const item = game.assignments.find((assignment) => assignment.role === role);
    return item ? [`${item.role_name}：${item.username}`] : [];
  }).join("；");

  return <section className="referee-management">
    <div className="referee-toolbar">
      <label>赛季<select aria-label="裁判管理赛季" value={seasonId} disabled={locked} onChange={async (event) => {
        const value = event.target.value;
        if (await confirmAdminNavigation()) onSeasonChange(value);
      }}>
        {seasons.map((item) => <option key={item.id} value={item.id}>{formatAdminSeasonLabel(item)}</option>)}
      </select></label>
      <button type="button" disabled={locked || loading} onClick={async () => {
        if (editing && !(await confirmAdminNavigation())) return;
        candidateGeneration.current += 1; setEditing(null); void load();
      }}>刷新</button>
    </div>
    <p className="referee-hint">人员填写可用日期和固定时段，管理员按岗位选派。选派保存后直接生效。</p>
    {readOnly && <p className="referee-warning">赛季已归档，仅可查看。</p>}
    <div className="referee-tabs">
      <button type="button" aria-pressed={tab === "games"} onClick={() => setTab("games")}>比赛选派</button>
      <button type="button" aria-pressed={tab === "people"} onClick={() => setTab("people")}>人员资格</button>
    </div>
    {error && <p className="referee-warning" role="alert">{error}</p>}
    {notice && <p role="status">{notice}</p>}
    {pending && !busy && <div className="referee-warning">
      <p>上次保存结果尚未确认，请先核对，避免重复操作。</p>
      <button type="button" onClick={() => void perform(pending)}>核对上次操作</button>
    </div>}
    {loading && <p role="status">正在读取裁判与记录台信息…</p>}
    {tab === "people" && <>
      <form className="referee-toolbar" onSubmit={(event) => {
        event.preventDefault();
        if (username.trim()) changeQualification(username.trim(), true, 0);
      }}>
        <label>人员唯一昵称<input aria-label="人员唯一昵称" value={username} maxLength={32}
          disabled={locked || readOnly} placeholder="对方先在小程序微信登录并设置昵称"
          onChange={(event) => setUsername(event.target.value)} /></label>
        <button disabled={locked || readOnly || !username.trim()} type="submit">开通人员资格</button>
      </form>
      <p className="referee-hint">本赛季资格适用于裁判和记录台岗位，不会授予网站管理员权限。</p>
      {!loading && qualifications?.items.length === 0 && <p>本赛季尚未开通人员资格。</p>}
      <div className="referee-table-scroll"><table>
        <thead><tr><th>昵称</th><th>资格状态</th><th>操作</th></tr></thead>
        <tbody>{qualifications?.items.map((item) => <tr key={item.id}>
          <td>{item.username}</td><td>{item.active ? "已开通" : "已停用"}</td>
          <td><button type="button" disabled={locked || readOnly} onClick={() =>
            changeQualification(item.username, !item.active, item.version)}>
            {item.active ? "停用资格" : "恢复资格"}
          </button></td>
        </tr>)}</tbody>
      </table></div>
      <Pager page={peoplePage} total={qualifications?.total ?? 0} size={20} disabled={locked || loading}
        onChange={setPeoplePage} />
    </>}
    {tab === "games" && <>
      <label className="referee-date">比赛日期<input type="date" aria-label="比赛日期" value={date}
        disabled={locked || Boolean(editing)} onChange={(event) => { setDate(event.target.value); setGamePage(1); }} /></label>
      {!loading && games?.items.length === 0 && <p>所选范围内暂无比赛。</p>}
      <div className="referee-game-list">{games?.items.map((game) => <article className="referee-game" key={game.id}>
        <div className="referee-game-heading"><h3>{game.home} — {game.away}</h3>
          <span>{game.stage === "FINAL" ? "决赛" : "比赛"} · {game.code}</span></div>
        <p>{game.date} · {game.period_name} · {game.start_time.slice(0, 5)} · {game.venue_name}</p>
        <p>{roleNames(game) || "尚未选派人员"}</p>
        {game.assignments.some((item) => item.schedule_changed) &&
          <p className="referee-warning">赛程或岗位范围已变更，原选派需重新处理。</p>}
        {editing?.id !== game.id && <button type="button" disabled={locked || readOnly}
          onClick={() => void openGame(game)}>{game.status === "SCHEDULED" ? "选派 / 调整人员" : "查看 / 撤回原选派"}</button>}
        {editing?.id === game.id && <div className="referee-editor">
          <p>留空表示暂不安排该岗位。每人每场只能承担一个岗位，同一日期时段不能选派到多场比赛。</p>
          {game.status === "SCHEDULED" && game.roles.map((role) => {
            const original = game.assignments.find((item) => item.role === role);
            return <label className="referee-role" key={role}>{refereeRoleLabels[role] ?? role}
              <select aria-label={refereeRoleLabels[role] ?? role} value={selected[role] ?? ""}
                disabled={locked || candidatesLoading} onChange={(event) =>
                  setSelected((previous) => ({ ...previous, [role]: event.target.value }))}>
                <option value="">暂不选派</option>
                {selected[role] && !candidates.some((item) => item.referee_id === selected[role]) &&
                  <option value={selected[role]}>{original?.username ?? "原选派人员"}（请核对当前报名）</option>}
                {candidates.map((item) => <option key={item.referee_id} value={item.referee_id}
                  disabled={!item.available || Object.entries(selected).some(([otherRole, value]) =>
                    otherRole !== role && value === item.referee_id)}>
                  {item.username}{item.available ? "" : `（已选派到 ${item.conflict_game_code}）`}
                </option>)}
              </select>
            </label>;
          })}
          {candidatesLoading && <p>正在读取该时段报名人员…</p>}
          {!candidatesLoading && !candidates.length && game.status === "SCHEDULED" &&
            <p>该日期时段暂无有效报名，请先开通人员资格并让人员填写可用时段。</p>}
          {candidates.length < candidateTotal && <button type="button" disabled={locked || candidatesLoading}
            onClick={() => void loadCandidates(game, candidatePage + 1)}>加载更多报名人员</button>}
          <div className="referee-toolbar">
            <button type="button" disabled={locked || candidatesLoading} onClick={saveAssignments}>保存选派</button>
            <button type="button" disabled={locked} onClick={() => setSelected({})}>清空所有岗位</button>
            <button type="button" disabled={locked} onClick={() => {
              candidateGeneration.current += 1; setEditing(null);
            }}>取消编辑</button>
          </div>
        </div>}
      </article>)}</div>
      <Pager page={gamePage} total={games?.total ?? 0} size={20}
        disabled={locked || loading || Boolean(editing)} onChange={setGamePage} />
    </>}
  </section>;
}

function Pager({ page, total, size, disabled, onChange }: {
  page: number; total: number; size: number; disabled: boolean; onChange: (page: number) => void;
}) {
  return <div className="referee-toolbar referee-pager">
    <button type="button" disabled={disabled || page <= 1} onClick={() => onChange(page - 1)}>上一页</button>
    <span>第 {page} 页 · 共 {total} 项</span>
    <button type="button" disabled={disabled || page * size >= total} onClick={() => onChange(page + 1)}>下一页</button>
  </div>;
}
