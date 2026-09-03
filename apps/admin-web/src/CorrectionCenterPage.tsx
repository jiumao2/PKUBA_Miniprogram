import { useEffect, useMemo, useRef, useState } from "react";
import type {
  AdminSeason,
  CompetitionCorrection,
  CompetitionCorrectionPreview,
  CompetitionGameChange,
  DownstreamResolution,
  MobileAdminGame,
  MobileScheduleOptions,
  createAdminClient,
} from "@pkuba/api-client";

import { confirmAdminNavigation, useAdminDirtySource } from "./dirtyGuard";
import { formatAdminSeasonLabel } from "./seasonLabel";
import "./correction-center.css";

type AdminClient = ReturnType<typeof createAdminClient>;

const resultStatuses = [
  { value: "SCHEDULED", label: "未赛" },
  { value: "COMPLETED", label: "已完成" },
  { value: "FORFEIT", label: "弃权" },
  { value: "VOID", label: "已作废" },
];

interface CorrectionCenterPageProps {
  client: AdminClient;
  seasons: AdminSeason[];
  season: AdminSeason;
  games: MobileAdminGame[];
  initialGameId?: string;
  initialDraft?: MobileAdminGame | null;
  onSeasonChange: (seasonId: string) => void;
  onUpdated: () => Promise<void>;
  onOpenScoresheet: (gameId: string) => void;
}

function editableSnapshot(game: MobileAdminGame) {
  return {
    date: game.date,
    period_id: game.period_id,
    start_time: game.start_time.slice(0, 5),
    standard_venue_id: game.standard_venue_id,
    venue_name: game.venue_name,
    home_team_id: game.home_team_id,
    away_team_id: game.away_team_id,
    home_score: game.home_score,
    away_score: game.away_score,
    status: game.status,
    leader_adjustable: game.leader_adjustable,
  };
}

function isChanged(game: MobileAdminGame, baseline: MobileAdminGame | undefined) {
  return Boolean(
    baseline
    && JSON.stringify(editableSnapshot(game)) !== JSON.stringify(editableSnapshot(baseline)),
  );
}

function toChange(
  game: MobileAdminGame,
  cancelActiveRequest: boolean,
  overrideRules: boolean,
): CompetitionGameChange {
  return {
    game_id: game.id,
    expected_version: game.version,
    date: game.date,
    period_id: game.period_id,
    start_time: game.start_time.slice(0, 5),
    standard_venue_id: game.standard_venue_id,
    venue_name: game.venue_name,
    home_team_id: game.home_team_id,
    away_team_id: game.away_team_id,
    home_score: game.home_score,
    away_score: game.away_score,
    status: game.status,
    leader_adjustable: game.leader_adjustable,
    cancel_active_request: cancelActiveRequest,
    override_rules: overrideRules,
  };
}

export function CorrectionCenterPage({
  client,
  seasons,
  season,
  games,
  initialGameId = "",
  initialDraft = null,
  onSeasonChange,
  onUpdated,
  onOpenScoresheet,
}: CorrectionCenterPageProps) {
  const [options, setOptions] = useState<MobileScheduleOptions | null>(null);
  const [drafts, setDrafts] = useState<Record<string, MobileAdminGame>>({});
  const [baselines, setBaselines] = useState<Record<string, MobileAdminGame>>({});
  const [selectedId, setSelectedId] = useState("");
  const [query, setQuery] = useState("");
  const [cancelRequests, setCancelRequests] = useState<Record<string, boolean>>({});
  const [overrideRules, setOverrideRules] = useState<Record<string, boolean>>({});
  const [reason, setReason] = useState("");
  const [preview, setPreview] = useState<CompetitionCorrectionPreview | null>(null);
  const [previewStale, setPreviewStale] = useState(false);
  const [resolutions, setResolutions] = useState<Record<string, DownstreamResolution>>({});
  const [correction, setCorrection] = useState<CompetitionCorrection | null>(null);
  const [history, setHistory] = useState<CompetitionCorrection[]>([]);
  const [acknowledged, setAcknowledged] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const loadGeneration = useRef(0);

  const changedDrafts = useMemo(
    () => Object.values(drafts).filter((game) => isChanged(game, baselines[game.id])),
    [baselines, drafts],
  );
  const dirty = changedDrafts.length > 0
    || Object.values(cancelRequests).some(Boolean)
    || Object.values(overrideRules).some(Boolean)
    || Boolean(reason.trim());
  useAdminDirtySource(`correction-center:${season.id}`, dirty && !correction);

  const selected = drafts[selectedId] ?? null;
  const locked = Boolean(correction);
  const scoresheetGameId = correction ? republishGameId(correction) : null;
  const shownGames = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase("zh-CN");
    if (!needle) return games;
    return games.filter((game) =>
      `${game.code} ${game.division_name} ${game.home_name} ${game.away_name}`
        .toLocaleLowerCase("zh-CN")
        .includes(needle),
    );
  }, [games, query]);

  const loadGame = async (
    gameId: string,
    seed?: MobileAdminGame | null,
    expectedGeneration?: number,
  ) => {
    setMessage("");
    if (drafts[gameId]) {
      setSelectedId(gameId);
      return;
    }
    setBusy(true);
    try {
      const fresh = await client.getAdminScheduleGame(gameId);
      const draft = seed?.id === gameId
        ? { ...fresh, ...seed, id: fresh.id, version: fresh.version }
        : fresh;
      if (expectedGeneration !== undefined && expectedGeneration !== loadGeneration.current) return;
      setDrafts((current) => ({ ...current, [gameId]: draft }));
      setBaselines((current) => ({ ...current, [gameId]: fresh }));
      setSelectedId(gameId);
    } catch (caught) {
      setMessage(caught instanceof Error ? caught.message : "无法读取比赛。");
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    const generation = ++loadGeneration.current;
    setOptions(null);
    setDrafts({});
    setBaselines({});
    setSelectedId("");
    setCancelRequests({});
    setOverrideRules({});
    setPreview(null);
    setPreviewStale(false);
    setCorrection(null);
    setResolutions({});
    setReason("");
    setAcknowledged(false);
    Promise.all([
      client.getAdminScheduleOptions(season.id),
      client.listCompetitionCorrections(season.id),
    ]).then(([nextOptions, nextHistory]) => {
      if (generation !== loadGeneration.current) return;
      setOptions(nextOptions);
      setHistory(nextHistory);
      const target = initialDraft?.id === initialGameId
        ? initialDraft
        : games.find((game) => game.id === initialGameId) ?? null;
      if (target) void loadGame(target.id, target, generation);
    }).catch((caught: unknown) => {
      if (generation === loadGeneration.current) {
        setMessage(caught instanceof Error ? caught.message : "无法读取纠错中心数据。");
      }
    });
    return () => {
      loadGeneration.current += 1;
    };
  }, [client, season.id]);

  const mutateSelected = (patch: Partial<MobileAdminGame>) => {
    if (!selected || locked) return;
    setDrafts((current) => ({
      ...current,
      [selected.id]: { ...selected, ...patch },
    }));
    setPreviewStale(Boolean(preview));
    setAcknowledged(false);
  };

  const removeDraft = (gameId: string) => {
    if (locked) return;
    setDrafts((current) => Object.fromEntries(Object.entries(current).filter(([id]) => id !== gameId)));
    setBaselines((current) => Object.fromEntries(Object.entries(current).filter(([id]) => id !== gameId)));
    setCancelRequests((current) => Object.fromEntries(Object.entries(current).filter(([id]) => id !== gameId)));
    setOverrideRules((current) => Object.fromEntries(Object.entries(current).filter(([id]) => id !== gameId)));
    setSelectedId((current) => current === gameId ? "" : current);
    setPreview(null);
    setPreviewStale(false);
    setAcknowledged(false);
  };

  const resolutionRows = Object.values(resolutions);
  const previewChanges = async () => {
    if (!changedDrafts.length) {
      setMessage("请先修改至少一场比赛。");
      return;
    }
    setBusy(true);
    setMessage("");
    try {
      const next = await client.previewCompetitionCorrection({
        season_id: season.id,
        expected_season_version: season.version,
        changes: changedDrafts.map((game) =>
          toChange(game, Boolean(cancelRequests[game.id]), Boolean(overrideRules[game.id])),
        ),
        downstream_resolutions: resolutionRows,
        reason,
      });
      setPreview(next);
      setPreviewStale(false);
      setAcknowledged(false);
    } catch (caught) {
      setMessage(caught instanceof Error ? caught.message : "纠错预览失败。");
    } finally {
      setBusy(false);
    }
  };

  const create = async () => {
    if (!preview?.can_create || previewStale || !acknowledged) return;
    if (!window.confirm("确认冻结本次纠错内容和影响？冻结后仍需执行最终应用或记录表重新发布。")) return;
    setBusy(true);
    setMessage("");
    try {
      const next = await client.createCompetitionCorrection({
        season_id: season.id,
        expected_season_version: season.version,
        changes: changedDrafts.map((game) =>
          toChange(game, Boolean(cancelRequests[game.id]), Boolean(overrideRules[game.id])),
        ),
        downstream_resolutions: resolutionRows,
        reason,
        impact_hash: preview.impact_hash,
        confirmed: true,
      });
      setCorrection(next);
      setHistory((current) => [next, ...current.filter((item) => item.id !== next.id)]);
      setMessage(next.status === "AWAITING_SCORESHEET"
        ? "纠错已冻结。请在记录表工作台复核并重新发布，届时整笔纠错原子生效。"
        : "纠错已冻结，可以执行最终应用。");
    } catch (caught) {
      setMessage(caught instanceof Error ? caught.message : "创建纠错单失败。");
    } finally {
      setBusy(false);
    }
  };

  const apply = async () => {
    if (!correction || correction.status !== "READY") return;
    if (!window.confirm("确认现在原子应用这笔纠错？公开赛程、赛果与审计将同时切换。")) return;
    setBusy(true);
    setMessage("");
    try {
      const applied = await client.applyCompetitionCorrection(
        correction.id,
        correction.version,
        correction.impact_hash,
      );
      setCorrection(applied);
      setHistory((current) => [applied, ...current.filter((item) => item.id !== applied.id)]);
      setMessage("纠错已原子应用，公开数据和正式赛果版本已同步。");
      setDrafts({});
      setBaselines({});
      await onUpdated();
    } catch (caught) {
      setMessage(caught instanceof Error ? caught.message : "应用纠错失败。");
    } finally {
      setBusy(false);
    }
  };

  const cancel = async () => {
    if (!correction || !["READY", "AWAITING_SCORESHEET"].includes(correction.status)) return;
    if (!window.confirm("确认取消这笔尚未应用的纠错单？冻结内容会保留在审计记录中。")) return;
    setBusy(true);
    setMessage("");
    try {
      const cancelled = await client.cancelCompetitionCorrection(
        correction.id,
        correction.version,
      );
      setHistory((current) => [cancelled, ...current.filter((item) => item.id !== cancelled.id)]);
      setCorrection(null);
      setPreview(null);
      setPreviewStale(false);
      setAcknowledged(false);
      setMessage("纠错单已取消；当前编辑内容未应用，可重新修改并生成新预览。");
    } catch (caught) {
      setMessage(caught instanceof Error ? caught.message : "取消纠错单失败。");
    } finally {
      setBusy(false);
    }
  };

  const teams = options?.teams.filter((team) => team.division_id === selected?.division_id) ?? [];

  return (
    <section className="correction-center" aria-label="超级管理员纠错中心">
      <header className="correction-hero">
        <div>
          <p className="eyebrow">版本化高风险操作</p>
          <h2>纠错中心</h2>
          <p>先冻结权威现状，再编辑目标状态、审阅影响，最后一次原子应用。</p>
        </div>
        <label>
          操作赛季
          <select value={season.id} onChange={(event) => {
            const nextId = event.target.value;
            void confirmAdminNavigation().then((confirmed) => {
              if (confirmed) onSeasonChange(nextId);
            });
          }}>
            {seasons.map((item) => (
              <option key={item.id} value={item.id}>{formatAdminSeasonLabel(item)}</option>
            ))}
          </select>
        </label>
      </header>

      <div className="correction-flow" aria-label="纠错流程">
        <span className="active">1 选择与编辑</span><span>2 影响预览</span><span>3 冻结纠错单</span><span>4 原子应用</span>
      </div>

      <div className="correction-layout">
        <aside className="correction-browser">
          <label className="correction-search">查找比赛<input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="代码、组别或球队" /></label>
          <div className="correction-game-list">
            {shownGames.map((game) => (
              <button key={game.id} type="button" disabled={locked && !drafts[game.id]} className={selectedId === game.id ? "active" : ""} onClick={() => void loadGame(game.id)}>
                <strong>{game.division_name} · {game.code}</strong>
                <span>{game.home_name} — {game.away_name}</span>
                <small>{game.date} · {game.start_time} · {game.status}</small>
              </button>
            ))}
          </div>
        </aside>

        <main className="correction-editor">
          {!selected || !options ? (
            <div className="operation-empty"><h2>选择一场比赛</h2><p>可将多场比赛放入同一纠错批次，以支持原子交换和下游同步。</p></div>
          ) : (
            <fieldset className="correction-editor-fields" disabled={locked}>
              <div className="correction-editor-heading">
                <div><p>{selected.division_name} · {selected.stage}</p><h3>{selected.home_name} — {selected.away_name}</h3></div>
                <button type="button" className="text-action destructive" onClick={() => removeDraft(selected.id)}>移出批次</button>
              </div>
              <div className="correction-authority-strip">
                <span>当前状态 <strong>{baselines[selected.id]?.status}</strong></span>
                <span>当前比分 <strong>{score(baselines[selected.id])}</strong></span>
                <span>版本 <strong>v{selected.version}</strong></span>
              </div>
              <div className="correction-form-grid">
                <label>比赛日期<input type="date" value={selected.date} onChange={(event) => mutateSelected({ date: event.target.value })} /></label>
                <label>容量时段<select value={selected.period_id} onChange={(event) => { const period = options.periods.find((item) => item.id === event.target.value); mutateSelected({ period_id: event.target.value, period_code: period?.code ?? selected.period_code, period_name: period?.name ?? selected.period_name }); }}>{options.periods.map((period) => <option key={period.id} value={period.id}>{period.code.toUpperCase()} · {period.name}</option>)}</select></label>
                <label>实际开赛时间<input type="time" value={selected.start_time.slice(0, 5)} onChange={(event) => mutateSelected({ start_time: event.target.value })} /></label>
                <label>标准场地<select value={selected.standard_venue_id ?? ""} onChange={(event) => { const venue = options.venues.find((item) => item.id === event.target.value); mutateSelected({ standard_venue_id: venue?.id ?? null, venue_name: venue?.name ?? selected.venue_name }); }}><option value="">其他场地</option>{options.venues.map((venue) => <option key={venue.id} value={venue.id}>{venue.name}</option>)}</select></label>
                <label>实际场地<input disabled={selected.standard_venue_id !== null} value={selected.venue_name} onChange={(event) => mutateSelected({ venue_name: event.target.value })} /></label>
                <label>比赛状态<select value={selected.status} onChange={(event) => { const status = event.target.value; mutateSelected({ status, ...(status === "SCHEDULED" || status === "VOID" ? { home_score: null, away_score: null } : {}) }); }}>{resultStatuses.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</select></label>
                <label>主队<select value={selected.home_team_id ?? ""} onChange={(event) => mutateSelected({ home_team_id: event.target.value || null })}><option value="">待定</option>{teams.map((team) => <option key={team.id} value={team.id}>{team.name}</option>)}</select></label>
                <label>客队<select value={selected.away_team_id ?? ""} onChange={(event) => mutateSelected({ away_team_id: event.target.value || null })}><option value="">待定</option>{teams.map((team) => <option key={team.id} value={team.id}>{team.name}</option>)}</select></label>
                <label>主队比分<input min="0" type="number" value={selected.home_score ?? ""} onChange={(event) => mutateSelected({ home_score: numeric(event.target.value) })} /></label>
                <label>客队比分<input min="0" type="number" value={selected.away_score ?? ""} onChange={(event) => mutateSelected({ away_score: numeric(event.target.value) })} /></label>
              </div>
              <div className="correction-checks">
                <label><input type="checkbox" checked={selected.leader_adjustable} onChange={(event) => mutateSelected({ leader_adjustable: event.target.checked })} />允许领队申请调赛</label>
                {selected.active_reschedule_request_id && <label><input type="checkbox" checked={Boolean(cancelRequests[selected.id])} onChange={(event) => { setCancelRequests((current) => ({ ...current, [selected.id]: event.target.checked })); setPreviewStale(Boolean(preview)); setAcknowledged(false); }} />取消活动调赛申请并释放资源</label>}
                <label><input type="checkbox" checked={Boolean(overrideRules[selected.id])} onChange={(event) => { setOverrideRules((current) => ({ ...current, [selected.id]: event.target.checked })); setPreviewStale(Boolean(preview)); setAcknowledged(false); }} />使用超级管理员赛程例外（仍不绕过数据不变量）</label>
              </div>
            </fieldset>
          )}

          {Object.keys(drafts).length > 0 && (
            <section className="correction-batch">
              <div><p className="eyebrow">当前批次</p><strong>{changedDrafts.length} 场已修改 / {Object.keys(drafts).length} 场已打开</strong></div>
              <div>{Object.values(drafts).map((game) => <button key={game.id} className={isChanged(game, baselines[game.id]) ? "dirty" : ""} type="button" onClick={() => setSelectedId(game.id)}>{game.code}</button>)}</div>
            </section>
          )}
          <label className="correction-reason">纠错理由（选填）<textarea disabled={locked} maxLength={500} value={reason} onChange={(event) => { setReason(event.target.value); setPreviewStale(Boolean(preview)); setAcknowledged(false); }} placeholder="例如：赛果录入错误、参赛方更正或归档资料恢复说明" /></label>
          <button className="primary-action" type="button" disabled={busy || !changedDrafts.length || locked} onClick={() => void previewChanges()}>{busy ? "正在核对…" : previewStale ? "重新生成影响预览" : "生成影响预览"}</button>
        </main>

        <aside className="correction-impact">
          <p className="eyebrow">权威影响</p>
          {!preview && !correction && <p className="subtle">修改后生成预览。系统会检查比赛不变量、场地与球队冲突、调赛、媒体、记录表及淘汰赛下游。</p>}
          {preview && !correction && (
            <>
              <h3>{preview.can_create ? "可以冻结纠错单" : "仍有阻塞项"}</h3>
              <div className="correction-impact-facts"><span>{preview.change_count} 场</span><span>{preview.public_impact ? "影响公开数据" : preview.archived_impact ? "归档纠错" : "未公开"}</span><span>{preview.requires_scoresheet_republication ? "需重发记录表" : "可直接应用"}</span></div>
              {preview.blockers.map((item, index) => <div className="correction-issue blocker" key={`${item.code}-${index}`}><strong>{item.code}</strong><span>{item.message}</span></div>)}
              {preview.warnings.map((item, index) => <div className="correction-issue warning" key={`${item.code}-${index}`}><strong>{item.code}</strong><span>{item.message}</span></div>)}
              {preview.downstream_impacts.map((impact) => {
                const value = resolutions[impact.slot_id];
                const sourceGame = games.find((game) => game.id === String(impact.source_game_id));
                const candidateTeams = options?.teams.filter((team) => team.division_id === sourceGame?.division_id) ?? [];
                return <div className="downstream-resolution" key={impact.slot_id}><strong>{impact.slot_label}</strong><span>当前：{impact.current_team_name}</span><select value={value?.action ?? ""} onChange={(event) => { const action = event.target.value as DownstreamResolution["action"]; setResolutions((current) => ({ ...current, [impact.slot_id]: { slot_id: impact.slot_id, action, team_id: action === "SET_TEAM" ? current[impact.slot_id]?.team_id ?? null : null } })); setPreviewStale(true); setAcknowledged(false); }}><option value="" disabled>请选择处理方式</option><option value="KEEP_OVERRIDE">保留人工覆盖</option><option value="SYNC_WINNER">同步新胜队</option><option value="CLEAR">清空为待定</option><option value="SET_TEAM">指定球队</option></select>{value?.action === "SET_TEAM" && <select value={value.team_id ?? ""} onChange={(event) => { setResolutions((current) => ({ ...current, [impact.slot_id]: { ...value, team_id: event.target.value || null } })); setPreviewStale(true); setAcknowledged(false); }}><option value="">选择球队</option>{candidateTeams.map((team) => <option key={team.id} value={team.id}>{team.name}</option>)}</select>}</div>;
              })}
              {previewStale && <div className="correction-issue warning"><strong>PREVIEW_STALE</strong><span>编辑内容或下游处理已变化，请重新生成影响预览。</span></div>}
              {preview.downstream_impacts.length > 0 && <button type="button" className="secondary-action" disabled={busy} onClick={() => void previewChanges()}>按当前下游选择重新预览</button>}
              {preview.can_create && !previewStale && <label className="critical-check"><input type="checkbox" checked={acknowledged} onChange={(event) => setAcknowledged(event.target.checked)} />我已核对修改前后内容、公开影响、记录表和下游处理</label>}
              {preview.can_create && <button type="button" className="danger-action" disabled={busy || previewStale || !acknowledged || Boolean(correction)} onClick={() => void create()}>冻结纠错单</button>}
            </>
          )}
          {correction && (
            <div className="correction-ready">
              <h3>{correction.status === "READY" ? "等待最终应用" : correction.status === "AWAITING_SCORESHEET" ? "等待记录表复核" : correction.status === "APPLIED" ? "已应用" : "纠错单已结束"}</h3>
              <code>{correction.id.slice(0, 12)}</code>
              {correction.status === "READY" && <button className="danger-action" type="button" disabled={busy} onClick={() => void apply()}>确认并原子应用</button>}
              {correction.status === "AWAITING_SCORESHEET" && scoresheetGameId && <button className="primary-action" type="button" onClick={() => onOpenScoresheet(scoresheetGameId)}>打开记录表复核</button>}
              {["READY", "AWAITING_SCORESHEET"].includes(correction.status) && <button className="secondary-action" type="button" disabled={busy} onClick={() => void cancel()}>取消纠错单</button>}
            </div>
          )}
        </aside>
      </div>

      {message && <p className="operation-message" role="status">{message}</p>}
      <section className="correction-history">
        <div><p className="eyebrow">审计入口</p><h3>最近纠错单</h3></div>
        <div>{history.slice(0, 8).map((item) => <article key={item.id}><strong>{item.status}</strong><span>{item.created_by} · {new Date(item.created_at).toLocaleString("zh-CN")}</span><small>{item.reason || "未填写理由"}</small></article>)}</div>
      </section>
    </section>
  );
}

function numeric(value: string) {
  return value === "" ? null : Number(value);
}

function score(game: MobileAdminGame | undefined) {
  if (!game || game.home_score === null || game.away_score === null) return "—";
  return `${game.home_score}:${game.away_score}`;
}

function republishGameId(correction: CompetitionCorrection): string | null {
  const impacts = correction.impact_snapshot.publication_impacts;
  if (!Array.isArray(impacts)) return null;
  const republish = impacts.find((value) => {
    if (!value || typeof value !== "object") return false;
    return (value as Record<string, unknown>).action === "REPUBLISH";
  });
  if (!republish || typeof republish !== "object") return null;
  const gameId = (republish as Record<string, unknown>).game_id;
  return typeof gameId === "string" ? gameId : null;
}
