import { useCallback, useEffect, useRef, useState } from "react";
import {
  ApiError, createIdempotencyKey, registrationStatusLabels, type AdminSeason,
  type createAdminClient, type PagedResponse, type Registration, type RegistrationHistory,
  type RegistrationPolicy, type RegistrationSummary,
} from "@pkuba/api-client";
import { confirmAdminNavigation, useAdminDirtySource } from "./dirtyGuard";
import "./registration-management.css";

type Client = ReturnType<typeof createAdminClient>;
type Command = { run: () => Promise<unknown>; label: string };

export function RegistrationManagementPage({ client, season, seasons, onSeasonChange }: {
  client: Client; season: AdminSeason; seasons: AdminSeason[]; onSeasonChange: (id: string) => void;
}) {
  const [policy, setPolicy] = useState<RegistrationPolicy>();
  const [deadline, setDeadline] = useState("");
  const [rows, setRows] = useState<PagedResponse<RegistrationSummary>>();
  const [page, setPage] = useState(1);
  const [status, setStatus] = useState("");
  const [selected, setSelected] = useState<Registration>();
  const [history, setHistory] = useState<PagedResponse<RegistrationHistory>>();
  const [division, setDivision] = useState("");
  const [reason, setReason] = useState("");
  const [confirm, setConfirm] = useState<boolean | null>(null);
  const [pending, setPending] = useState<Command | null>(null);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const generation = useRef(0);
  const detailGeneration = useRef(0);
  const selectedId = useRef("");
  const detailRef = useRef<HTMLDivElement>(null);
  const running = useRef(false);
  const unresolved = useRef<Command | null>(null);
  const dirtyDeadline = Boolean(policy && deadline !== localDateTime(policy.deadline));
  useAdminDirtySource("registrations", Boolean(dirtyDeadline || reason || confirm !== null || pending));
  const readOnly = season.status === "ARCHIVED";

  const load = useCallback(async () => {
    const seq = ++generation.current;
    setLoading(true); setError("");
    try {
      const [nextPolicy, nextRows] = await Promise.all([
        client.getRegistrationPolicy(season.id), client.listRegistrations(season.id, page, status),
      ]);
      if (seq !== generation.current) return;
      setPolicy(nextPolicy); setDeadline(localDateTime(nextPolicy.deadline)); setRows(nextRows);
    } catch (e) {
      if (seq === generation.current) { setRows(undefined); setPolicy(undefined); setError(e instanceof Error ? e.message : "报名读取失败"); }
    } finally { if (seq === generation.current) setLoading(false); }
  }, [client, season.id, page, status]);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => () => { generation.current += 1; detailGeneration.current += 1; }, []);
  useEffect(() => { if (selected) detailRef.current?.scrollIntoView?.({ block: "start", behavior: "smooth" }); }, [selected?.id]);

  const close = () => {
    detailGeneration.current += 1; selectedId.current = "";
    setSelected(undefined); setHistory(undefined); setReason(""); setConfirm(null);
  };
  const open = async (id: string, historyPage = 1) => {
    if (unresolved.current || (historyPage === 1 && !(await confirmAdminNavigation()))) return;
    const seq = ++detailGeneration.current;
    selectedId.current = id; setDetailLoading(true); setError("");
    setSelected(undefined); setHistory(undefined); setConfirm(null); setReason("");
    try {
      const [item, log] = await Promise.all([client.getRegistration(id), client.getRegistrationHistory(id, historyPage)]);
      if (seq !== detailGeneration.current || selectedId.current !== id) return;
      setSelected(item); setHistory(log); setDivision(item.team_id ? "" : season.divisions[0]?.id ?? "");
    } catch (e) { if (seq === detailGeneration.current) setError(e instanceof Error ? e.message : "报名详情读取失败"); }
    finally { if (seq === detailGeneration.current) setDetailLoading(false); }
  };
  const perform = async (command: Command) => {
    if (running.current) return;
    running.current = true; unresolved.current = command; setPending(command); setBusy(true); setError(""); setNotice("");
    const seq = generation.current;
    try {
      await command.run();
      if (seq !== generation.current) return;
      unresolved.current = null; setPending(null); close(); setNotice(`${command.label}已保存。`); await load();
    } catch (e) {
      if (seq !== generation.current) return;
      setError(e instanceof Error ? e.message : "保存结果暂时无法确认");
      if (e instanceof ApiError && e.status < 500) { unresolved.current = null; setPending(null); setConfirm(null); }
    } finally { running.current = false; setBusy(false); }
  };
  const saveDeadline = () => {
    if (!policy || unresolved.current) return;
    const parsed = deadline ? new Date(deadline) : null;
    if (parsed && Number.isNaN(parsed.getTime())) { setError("请填写有效的报名截止时间。"); return; }
    const key = createIdempotencyKey();
    void perform({ label: "报名截止时间", run: () => client.setRegistrationPolicy(season.id, parsed?.toISOString() ?? null, policy.version, key) });
  };
  const review = () => {
    if (!selected || confirm === null || unresolved.current) return;
    const id = selected.id;
    const payload = { expected_version: selected.version, approved: confirm, reason, division_id: division || null };
    const key = createIdempotencyKey();
    void perform({ label: confirm ? "审核通过" : "退回意见", run: () => client.reviewRegistration(id, payload, key) });
  };
  const locked = loading || busy || Boolean(pending);

  return <section className="registration-management">
    <div className="registration-toolbar">
      <label>赛季<select aria-label="报名赛季" value={season.id} disabled={busy || Boolean(pending)} onChange={async (e) => {
        const id = e.target.value; if (await confirmAdminNavigation()) onSeasonChange(id);
      }}>{seasons.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</select></label>
      <button disabled={busy || Boolean(pending)} onClick={async () => { if (await confirmAdminNavigation()) { close(); await load(); } }}>刷新</button>
    </div>
    <p className="registration-muted">报名表仅本队当前领队和管理员可见。学号、电话和邮箱不会公开。</p>
    {readOnly && <p>归档赛季只读。</p>}
    {loading && <p role="status">正在读取报名…</p>}
    {error && <p role="alert" className="registration-error">{error}</p>}
    {notice && <p role="status">{notice}</p>}
    {pending && !busy && <div className="registration-panel"><p>上次保存结果尚未确认，请先核对。</p>
      <button onClick={() => void perform(pending)}>核对上次操作</button></div>}
    {policy && <div className="registration-panel"><h2>报名时间</h2>
      <label>截止时间（本机时区）<input aria-label="报名截止时间" type="datetime-local" value={deadline} disabled={locked || readOnly} onChange={(e) => setDeadline(e.target.value)} /></label>
      <p>{policy.open ? "当前允许报名" : "当前未开放或已截止"}；截止后不能修改、重新提交或发起名单变更。</p>
      <button disabled={locked || readOnly || !dirtyDeadline} onClick={saveDeadline}>保存截止时间</button>
    </div>}
    <div className="registration-toolbar"><label>状态<select aria-label="报名状态" value={status} disabled={locked} onChange={async (e) => {
      const value = e.target.value; if (await confirmAdminNavigation()) { close(); setPage(1); setStatus(value); }
    }}><option value="">全部</option>{Object.entries(registrationStatusLabels).map(([key, name]) => <option key={key} value={key}>{name}</option>)}</select></label></div>
    {rows && <><p>共 {rows.total} 份报名</p><div className="registration-list">{rows.items.map((item) => <article key={item.id}>
      <div><strong>{item.team_name}</strong><p>{registrationStatusLabels[item.status]}</p></div>
      <button disabled={locked || detailLoading} onClick={() => void open(item.id)}>查看报名</button>
    </article>)}</div>{!rows.items.length && <p>暂无符合条件的报名。</p>}
      <div className="registration-toolbar"><button disabled={locked || page <= 1} onClick={async () => { if (await confirmAdminNavigation()) { close(); setPage(page - 1); } }}>上一页</button>
        <span>第 {page} 页</span><button disabled={locked || page * rows.page_size >= rows.total} onClick={async () => { if (await confirmAdminNavigation()) { close(); setPage(page + 1); } }}>下一页</button></div>
    </>}
    {detailLoading && <p role="status">正在读取私密报名表…</p>}
    {selected && <div ref={detailRef} className="registration-panel"><div className="registration-toolbar"><h2>{selected.team_name}</h2>
      <button disabled={locked} onClick={async () => { if (await confirmAdminNavigation()) close(); }}>关闭详情</button></div>
      <p>{registrationStatusLabels[selected.status]} · 球服颜色：{selected.form.uniform_color}</p>
      <p>领队：{selected.form.leader_name} · 电话：{selected.form.phone} · 邮箱：{selected.form.email}</p>
      <div className="registration-table-wrap"><table><thead><tr><th>学院</th><th>姓名</th><th>学号</th><th>球衣号码</th></tr></thead>
        <tbody>{(selected.form.players ?? []).map((p, i) => <tr key={p.id || i}><td>{p.college}</td><td>{p.name}</td><td>{p.student_number}</td><td>{p.jersey_number}</td></tr>)}</tbody></table></div>
      {!readOnly && ["PENDING", "CHANGE_PENDING"].includes(selected.status) && <div className="registration-review">
        {!selected.team_id && <label>指定参赛组别<select aria-label="指定参赛组别" value={division} disabled={locked} onChange={(e) => setDivision(e.target.value)}>
          <option value="">请选择</option>{season.divisions.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}</select></label>}
        <label>审核意见<textarea aria-label="审核意见" value={reason} maxLength={500} disabled={locked} onChange={(e) => setReason(e.target.value)} /></label>
        <div className="registration-toolbar"><button disabled={locked || (!selected.team_id && !division)} onClick={() => setConfirm(true)}>通过报名</button>
          <button disabled={locked || !reason.trim()} onClick={() => setConfirm(false)}>退回修改</button></div>
        {confirm !== null && <div role="dialog" aria-label="确认报名审核" className="registration-confirm">
          <p>{confirm ? "确认通过本版本？正式名单将锁定。" : "确认退回本版本？未通过新报名的队员资格将释放；截止后不能重提。"}</p>
          <button disabled={locked} onClick={review}>{confirm ? "确认通过" : "确认退回"}</button>
          <button disabled={locked} onClick={() => setConfirm(null)}>取消</button></div>}
      </div>}
      {history && <div><h3>提交与审核记录</h3>{history.items.map((h) => <article key={h.id} className="registration-history">
        <p>版本 {h.version} · {new Date(h.submitted_at).toLocaleString()} · {h.decision === "PENDING" ? "待审核" : h.decision === "APPROVED" ? "通过" : "退回"}</p>
        {h.reason && <p>申请原因：{h.reason}</p>}{h.review_reason && <p>审核意见：{h.review_reason}</p>}
        <details><summary>查看该次提交名单</summary>{(h.form.players ?? []).map((p, i) => <p key={i}>{p.college} · {p.name} · {p.student_number} · {p.jersey_number}</p>)}</details>
      </article>)}<div className="registration-toolbar"><button disabled={locked || history.page <= 1} onClick={() => void open(selected.id, history.page - 1)}>较新记录</button>
        <button disabled={locked || history.page * history.page_size >= history.total} onClick={() => void open(selected.id, history.page + 1)}>较早记录</button></div></div>}
    </div>}
  </section>;
}

function localDateTime(value: string | null) {
  if (!value) return "";
  const date = new Date(value);
  return new Date(date.getTime() - date.getTimezoneOffset() * 60_000).toISOString().slice(0, 16);
}
