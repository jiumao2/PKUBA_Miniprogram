import { useEffect, useMemo, useRef, useState } from "react";

import type {
  AdminSeason,
  RosterDataset,
  RosterImport,
  RosterPlayerInput,
  SaveTeamRoster,
  TeamMaintenancePreview,
  TeamRoster,
  createAdminClient,
} from "@pkuba/api-client";

import { confirmAdminNavigation, useAdminDirtySource } from "./dirtyGuard";
import { formatAdminSeasonLabel } from "./seasonLabel";
import "./team-roster.css";

type AdminClient = ReturnType<typeof createAdminClient>;

interface TeamRosterPageProps {
  client: AdminClient;
  seasons: AdminSeason[];
  seasonId: string;
  onSeasonChange: (seasonId: string) => void;
  onDataChanged: () => Promise<void>;
  onOpenConfiguration: () => void;
}

interface PlayerDraft extends RosterPlayerInput {
  localKey: string;
}

interface TeamDraft {
  id?: string;
  expectedTeamVersion?: number;
  divisionId: string;
  name: string;
  active: boolean;
  players: PlayerDraft[];
}

interface RosterSummary {
  team_count: number;
  player_count: number;
  error_count: number;
  warning_count: number;
  division_stats: Array<{
    division_id: string;
    division_name: string;
    team_count: number;
    player_count: number;
    expected_team_count: number | null;
    slot_count_mismatch: boolean;
  }>;
  name_resolutions: Array<{
    key: string;
    division_name: string;
    source_name: string;
    canonical_name: string;
  }>;
  teams: Array<{
    division_id: string;
    division_name: string;
    name: string;
    source_names: string[];
    player_count: number;
  }>;
}

function statusLabel(status: string) {
  return {
    SETUP: "准备中",
    PUBLISHED: "已公开",
    ARCHIVED: "已归档",
  }[status] ?? status;
}

function playerDraft(player?: TeamRoster["players"][number]): PlayerDraft {
  return {
    localKey: player?.id ?? crypto.randomUUID(),
    id: player?.id,
    expected_version: player?.version,
    name: player?.name ?? "",
    jersey_number: player?.jersey_number ?? "",
    eligible: player?.eligible ?? true,
    active: player?.active ?? true,
  };
}

function teamDraft(team: TeamRoster): TeamDraft {
  return {
    id: team.id,
    expectedTeamVersion: team.version,
    divisionId: team.division_id,
    name: team.name,
    active: team.active,
    players: team.players.map((player) => playerDraft(player)),
  };
}

function cleanPlayers(players: PlayerDraft[]): RosterPlayerInput[] {
  return players.map(({ localKey: _localKey, ...player }) => player);
}

function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

export function TeamRosterPage({
  client,
  seasons,
  seasonId,
  onSeasonChange,
  onDataChanged,
  onOpenConfiguration,
}: TeamRosterPageProps) {
  const [dataset, setDataset] = useState<RosterDataset | null>(null);
  const [selectedTeamId, setSelectedTeamId] = useState<string | null>(null);
  const [draft, setDraft] = useState<TeamDraft | null>(null);
  const [divisionFilter, setDivisionFilter] = useState("all");
  const [query, setQuery] = useState("");
  const [dirty, setDirty] = useState(false);
  useAdminDirtySource(`team-roster:${seasonId}`, dirty);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [batch, setBatch] = useState<RosterImport | null>(null);
  const [auditVisible, setAuditVisible] = useState(false);
  const [warningsAcknowledged, setWarningsAcknowledged] = useState(false);
  const [resolutionDrafts, setResolutionDrafts] = useState<Record<string, string>>({});
  const [maintenancePreview, setMaintenancePreview] =
    useState<TeamMaintenancePreview | null>(null);
  const uploadRef = useRef<HTMLInputElement>(null);
  const loadGeneration = useRef(0);

  const loadDataset = async (targetSeasonId = seasonId) => {
    const generation = ++loadGeneration.current;
    setLoading(true);
    setError(null);
    try {
      const next = await client.getRosterDataset(targetSeasonId);
      if (generation !== loadGeneration.current) return;
      setDataset(next);
      setSelectedTeamId((current) => {
        if (current && next.teams.some((team) => team.id === current)) return current;
        return next.teams[0]?.id ?? null;
      });
    } catch (caught) {
      if (generation !== loadGeneration.current) return;
      setError(caught instanceof Error ? caught.message : "无法读取球队与名单。 ");
    } finally {
      if (generation === loadGeneration.current) setLoading(false);
    }
  };

  useEffect(() => {
    setDraft(null);
    setDirty(false);
    setBatch(null);
    setAuditVisible(false);
    setMaintenancePreview(null);
    void loadDataset();
    return () => {
      loadGeneration.current += 1;
    };
  }, [seasonId]);

  useEffect(() => {
    if (!dataset || !selectedTeamId) return;
    const team = dataset.teams.find((item) => item.id === selectedTeamId);
    if (team) {
      setDraft(teamDraft(team));
      setDirty(false);
      setMaintenancePreview(null);
    }
  }, [dataset, selectedTeamId]);

  useEffect(() => {
    const beforeUnload = (event: BeforeUnloadEvent) => {
      if (!dirty) return;
      event.preventDefault();
    };
    window.addEventListener("beforeunload", beforeUnload);
    return () => window.removeEventListener("beforeunload", beforeUnload);
  }, [dirty]);

  const filteredTeams = useMemo(() => {
    if (!dataset) return [];
    const key = query.trim().toLocaleLowerCase("zh-CN");
    return dataset.teams.filter(
      (team) =>
        (divisionFilter === "all" || team.division_id === divisionFilter) &&
        (!key || team.name.toLocaleLowerCase("zh-CN").includes(key)),
    );
  }, [dataset, divisionFilter, query]);

  const summary = batch?.summary as unknown as RosterSummary | undefined;
  const errorIssues = batch?.issues.filter((issue) => issue.severity === "ERROR") ?? [];
  const warningIssues = batch?.issues.filter((issue) => issue.severity === "WARNING") ?? [];
  const duplicateJerseys = useMemo(() => {
    const counts = new Map<string, number>();
    for (const player of draft?.players ?? []) {
      const number = player.jersey_number?.trim();
      if (number && player.active) counts.set(number, (counts.get(number) ?? 0) + 1);
    }
    return new Set([...counts].filter(([, count]) => count > 1).map(([number]) => number));
  }, [draft]);

  const markDraft = (next: TeamDraft) => {
    setDraft(next);
    setDirty(true);
    setNotice(null);
    setMaintenancePreview(null);
  };

  const selectTeam = (teamId: string) => {
    if (dirty && !window.confirm("当前球队有未保存修改，确定切换吗？")) return;
    setSelectedTeamId(teamId);
  };

  const startNewTeam = () => {
    if (!dataset || dataset.read_only) return;
    if (dirty && !window.confirm("当前球队有未保存修改，确定新建球队吗？")) return;
    setSelectedTeamId(null);
    setDraft({
      divisionId: divisionFilter !== "all" ? divisionFilter : dataset.divisions[0]?.id ?? "",
      name: "",
      active: true,
      players: [playerDraft()],
    });
    setDirty(true);
    setMaintenancePreview(null);
  };

  const refreshAll = async () => {
    await loadDataset();
    await onDataChanged();
  };

  const downloadTemplate = async () => {
    if (!dataset) return;
    setBusy(true);
    setError(null);
    try {
      const blob = await client.downloadRosterTemplate(dataset.season_id);
      saveBlob(blob, `PKUBA_${dataset.season_name}_球队名单模板.xlsx`);
      setNotice("模板已下载。请在对应组别页逐行填写，每名球员一行。");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "模板下载失败。 ");
    } finally {
      setBusy(false);
    }
  };

  const uploadRoster = async (file: File) => {
    if (!dataset) return;
    if (!file.name.toLowerCase().endsWith(".xlsx")) {
      setError("只允许上传 .xlsx 名单文件。");
      return;
    }
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const next = await client.uploadRoster(dataset.season_id, file);
      setBatch(next);
      setAuditVisible(true);
      setWarningsAcknowledged(false);
      const nextSummary = next.summary as unknown as RosterSummary;
      setResolutionDrafts(
        Object.fromEntries(
          (nextSummary.name_resolutions ?? []).map((item) => [item.key, item.canonical_name]),
        ),
      );
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "名单上传失败。 ");
    } finally {
      setBusy(false);
      if (uploadRef.current) uploadRef.current.value = "";
    }
  };

  const saveResolutions = async () => {
    if (!batch) return;
    setBusy(true);
    setError(null);
    try {
      const next = await client.resolveRosterNames(batch.id, resolutionDrafts);
      setBatch(next);
      setWarningsAcknowledged(false);
      const nextSummary = next.summary as unknown as RosterSummary;
      setResolutionDrafts(
        Object.fromEntries(
          nextSummary.name_resolutions.map((item) => [item.key, item.canonical_name]),
        ),
      );
      setNotice("标准名称已重新审计。系统没有自动合并任何球队。");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "名称处理失败。 ");
    } finally {
      setBusy(false);
    }
  };

  const confirmImport = async () => {
    if (!batch) return;
    setBusy(true);
    setError(null);
    try {
      const confirmed = await client.confirmRosterImport(batch.id, {
        expected_season_version: batch.base_season_version,
        warnings_acknowledged: warningsAcknowledged,
      });
      setBatch(confirmed);
      setNotice("球队与名单已原子确认；本赛季名单重新导入现已永久关闭。");
      await refreshAll();
      setAuditVisible(false);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "名单确认失败。 ");
    } finally {
      setBusy(false);
    }
  };

  const buildSavePayload = (token = ""): SaveTeamRoster | null => {
    if (!draft?.id || draft.expectedTeamVersion === undefined) return null;
    return {
      expected_team_version: draft.expectedTeamVersion,
      name: draft.name,
      active: draft.active,
      players: cleanPlayers(draft.players),
      maintenance_token: token,
    };
  };

  const saveExisting = async (token = "") => {
    if (!draft?.id) return;
    const payload = buildSavePayload(token);
    if (!payload) return;
    setBusy(true);
    setError(null);
    try {
      if (!token) {
        const preview = await client.previewTeamRoster(draft.id, payload);
        if (preview.requires_confirmation) {
          setMaintenancePreview(preview);
          return;
        }
      }
      const updated = await client.saveTeamRoster(draft.id, payload);
      setNotice(`已保存 ${updated.name} 的完整名单。`);
      setDirty(false);
      setMaintenancePreview(null);
      await refreshAll();
      setSelectedTeamId(updated.id);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "球队名单保存失败。 ");
    } finally {
      setBusy(false);
    }
  };

  const createTeam = async () => {
    if (!dataset || !draft || draft.id) return;
    setBusy(true);
    setError(null);
    try {
      const created = await client.createRosterTeam(dataset.season_id, {
        expected_season_version: dataset.season_version,
        division_id: draft.divisionId,
        name: draft.name,
        players: cleanPlayers(draft.players),
      });
      setNotice(`已创建 ${created.name}，并分配稳定球队 ID。`);
      setDirty(false);
      await refreshAll();
      setSelectedTeamId(created.id);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "球队创建失败。 ");
    } finally {
      setBusy(false);
    }
  };

  if (loading) {
    return <section className="roster-state">正在读取球队与名单…</section>;
  }
  if (!dataset) {
    return <section className="roster-state roster-state-error">{error ?? "暂无数据"}</section>;
  }

  if (auditVisible && batch && summary) {
    return (
      <section className="roster-audit" aria-label="名单导入审计">
        <div className="roster-audit-heading">
          <div>
            <p className="roster-kicker">上传批次 {batch.id.slice(0, 8)}</p>
            <h2>导入审计与最终变更</h2>
            <p>近似名称只提供建议。修改标准名称后必须重新审计，系统不会自动合并。</p>
          </div>
          <button className="secondary-action" type="button" onClick={() => setAuditVisible(false)}>
            返回在线编辑
          </button>
        </div>

        {error && <div className="roster-message error" role="alert">{error}</div>}
        {notice && <div className="roster-message" role="status">{notice}</div>}

        <div className="roster-audit-facts">
          <div><span>球队</span><strong>{summary.team_count}</strong></div>
          <div><span>球员</span><strong>{summary.player_count}</strong></div>
          <div className={errorIssues.length ? "danger" : ""}><span>错误</span><strong>{errorIssues.length}</strong></div>
          <div className={warningIssues.length ? "warning" : ""}><span>警告</span><strong>{warningIssues.length}</strong></div>
        </div>

        <div className="roster-audit-grid">
          <section className="roster-audit-section">
            <div className="roster-section-title">
              <div><p className="roster-kicker">01 · 问题列表</p><h3>先消除全部错误</h3></div>
              <span>{errorIssues.length ? "阻止确认" : "可进入确认"}</span>
            </div>
            {!batch.issues.length && <p className="roster-empty">未发现错误或警告。</p>}
            <div className="roster-issues">
              {batch.issues.map((issue, index) => (
                <article className={`roster-issue ${issue.severity.toLowerCase()}`} key={`${issue.code}-${index}`}>
                  <div><strong>{issue.severity === "ERROR" ? "错误" : "警告"}</strong><code>{issue.code}</code></div>
                  <p>{issue.message}</p>
                  {issue.cell && <span>{issue.cell}</span>}
                </article>
              ))}
            </div>
          </section>

          <section className="roster-audit-section">
            <div className="roster-section-title">
              <div><p className="roster-kicker">02 · 标准名称</p><h3>逐项确认球队名称</h3></div>
              <button className="secondary-action" type="button" disabled={busy} onClick={() => void saveResolutions()}>
                重新审计名称
              </button>
            </div>
            <div className="roster-resolution-list">
              {summary.name_resolutions.map((item) => (
                <label key={item.key}>
                  <span>{item.division_name} · 原填写：{item.source_name}</span>
                  <input
                    value={resolutionDrafts[item.key] ?? item.canonical_name}
                    maxLength={120}
                    onChange={(event) => setResolutionDrafts((current) => ({ ...current, [item.key]: event.target.value }))}
                  />
                </label>
              ))}
            </div>
          </section>
        </div>

        <section className="roster-audit-section">
          <div className="roster-section-title">
            <div><p className="roster-kicker">03 · 分组统计</p><h3>与签位方案核对</h3></div>
            <button className="text-action" type="button" onClick={onOpenConfiguration}>前往赛季与组别</button>
          </div>
          <div className="roster-division-stats">
            {summary.division_stats.map((item) => (
              <div className={item.slot_count_mismatch ? "mismatch" : ""} key={item.division_id}>
                <strong>{item.division_name}</strong>
                <span>{item.team_count} 队 · {item.player_count} 人</span>
                <small>{item.expected_team_count ? `签位方案 ${item.expected_team_count} 队` : "尚未配置小组/循环赛签位"}</small>
              </div>
            ))}
          </div>
        </section>

        <section className="roster-final-change">
          <div>
            <p className="roster-kicker">04 · 原子确认</p>
            <h3>一次创建整季球队与名单</h3>
            <p>确认后将关闭重新导入。此后所有纠错都通过在线编辑完成。</p>
          </div>
          <div className="roster-confirm-actions">
            {warningIssues.length > 0 && (
              <label className="roster-warning-check">
                <input type="checkbox" checked={warningsAcknowledged} onChange={(event) => setWarningsAcknowledged(event.target.checked)} />
                我已逐条核对警告，并确认近似名称中未合并的项目确为不同球队
              </label>
            )}
            <button
              className="primary-action"
              type="button"
              disabled={busy || errorIssues.length > 0 || (warningIssues.length > 0 && !warningsAcknowledged)}
              onClick={() => void confirmImport()}
            >
              {busy ? "处理中…" : "确认创建球队与名单"}
            </button>
          </div>
        </section>
      </section>
    );
  }

  return (
    <section className="team-roster-workspace">
      <div className="roster-toolbar">
        <label>
          <span>当前赛季</span>
          <select value={seasonId} onChange={(event) => {
            const nextSeasonId = event.target.value;
            void confirmAdminNavigation().then((confirmed) => {
              if (confirmed) onSeasonChange(nextSeasonId);
            });
          }}>
            {seasons.map((season) => <option key={season.id} value={season.id}>{formatAdminSeasonLabel(season)}</option>)}
          </select>
        </label>
        <div className="roster-toolbar-actions">
          <span className={`roster-season-status ${dataset.season_status.toLowerCase()}`}>{statusLabel(dataset.season_status)}</span>
          <button className="secondary-action" type="button" disabled={busy} onClick={() => void downloadTemplate()}>下载填写模板</button>
          <input
            ref={uploadRef}
            className="visually-hidden"
            type="file"
            accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            onChange={(event) => event.target.files?.[0] && void uploadRoster(event.target.files[0])}
          />
          <button
            className="primary-action"
            type="button"
            disabled={busy || !dataset.import_state.allowed}
            onClick={() => uploadRef.current?.click()}
          >
            上传并审计
          </button>
        </div>
      </div>

      <div className="roster-facts">
        <div><span>球队</span><strong>{dataset.active_team_count}</strong><small>共 {dataset.team_count}</small></div>
        <div><span>球员</span><strong>{dataset.active_player_count}</strong><small>共 {dataset.player_count}</small></div>
        <div><span>组别</span><strong>{dataset.divisions.length}</strong><small>按当前赛季生成模板</small></div>
        <div><span>导入状态</span><strong>{dataset.import_state.allowed ? "待导入" : dataset.import_state.confirmed_batch_id ? "已锁定" : "不可导入"}</strong><small>{dataset.import_state.allowed ? "赛季第一步" : "在线维护模式"}</small></div>
      </div>

      {error && <div className="roster-message error" role="alert">{error}</div>}
      {notice && <div className="roster-message" role="status">{notice}</div>}
      {!dataset.import_state.allowed && dataset.import_state.blockers.length > 0 && (
        <div className="roster-import-lock">
          <strong>{dataset.import_state.confirmed_batch_id ? "名单重新导入已关闭" : "当前赛季不允许首次导入"}</strong>
          <span>{dataset.import_state.blockers[0].message} 后续请使用右侧在线编辑。</span>
        </div>
      )}
      {batch && (
        <button className="roster-resume-audit" type="button" onClick={() => setAuditVisible(true)}>
          返回导入审计 · {batch.issues.filter((issue) => issue.severity === "ERROR").length} 个错误 / {batch.issues.filter((issue) => issue.severity === "WARNING").length} 个警告
        </button>
      )}

      <div className="roster-master-detail">
        <aside className="roster-team-index" aria-label="球队列表">
          <div className="roster-index-controls">
            <input aria-label="搜索球队" placeholder="搜索标准球队名称" value={query} onChange={(event) => setQuery(event.target.value)} />
            <select aria-label="筛选组别" value={divisionFilter} onChange={(event) => setDivisionFilter(event.target.value)}>
              <option value="all">全部组别</option>
              {dataset.divisions.map((division) => <option key={division.id} value={division.id}>{division.name}</option>)}
            </select>
          </div>
          <div className="roster-index-heading">
            <span>{filteredTeams.length} 支球队</span>
            <button type="button" disabled={dataset.read_only} onClick={startNewTeam}>＋ 新球队</button>
          </div>
          <div className="roster-team-list">
            {filteredTeams.map((team) => {
              const division = dataset.divisions.find((item) => item.id === team.division_id);
              const activePlayers = team.players.filter((player) => player.active).length;
              return (
                <button className={selectedTeamId === team.id ? "selected" : ""} type="button" key={team.id} onClick={() => selectTeam(team.id)}>
                  <span><strong>{team.name}</strong><small>{division?.name ?? "未分组"}</small></span>
                  <span className={activePlayers ? "" : "unassigned"}>{activePlayers ? `${activePlayers} 人` : "未录名单"}</span>
                </button>
              );
            })}
            {!filteredTeams.length && <p className="roster-empty">当前筛选下没有球队。</p>}
          </div>
        </aside>

        <section className="roster-editor" aria-label="球队名单编辑区">
          {!draft ? (
            <div className="roster-editor-empty"><strong>选择一支球队</strong><span>在左侧选择球队，或新建本赛季球队。</span></div>
          ) : (
            <>
              <div className="roster-editor-heading">
                <div><p className="roster-kicker">{draft.id ? "稳定球队 ID" : "新增球队"}</p><h2>{draft.id ? draft.id.slice(0, 8) : "尚未保存"}</h2></div>
                <label className="roster-active-toggle"><input type="checkbox" checked={draft.active} disabled={dataset.read_only} onChange={(event) => markDraft({ ...draft, active: event.target.checked })} /><span>球队启用</span></label>
              </div>
              <div className="roster-team-fields">
                <label><span>标准球队名称 *</span><input value={draft.name} maxLength={120} disabled={dataset.read_only} onChange={(event) => markDraft({ ...draft, name: event.target.value })} /></label>
                <label><span>组别 *</span><select value={draft.divisionId} disabled={Boolean(draft.id) || dataset.read_only} onChange={(event) => markDraft({ ...draft, divisionId: event.target.value })}>{dataset.divisions.map((division) => <option key={division.id} value={division.id}>{division.name}</option>)}</select></label>
              </div>
              <div className="roster-table-heading">
                <div><h3>球员名单</h3><p>姓名必填；号码可留空。同队启用球员的号码不能重复。</p></div>
                <button type="button" disabled={dataset.read_only} onClick={() => markDraft({ ...draft, players: [...draft.players, playerDraft()] })}>＋ 增加球员</button>
              </div>
              <div className="roster-player-table" role="table" aria-label="球员在线编辑表">
                <div className="roster-player-row header" role="row"><span>#</span><span>球员姓名 *</span><span>球衣号码</span><span>状态</span><span></span></div>
                {draft.players.map((player, index) => (
                  <div className={`roster-player-row ${!player.active ? "inactive" : ""}`} role="row" key={player.localKey}>
                    <span>{String(index + 1).padStart(2, "0")}</span>
                    <input aria-label={`第 ${index + 1} 名球员姓名`} value={player.name} maxLength={80} disabled={dataset.read_only} onChange={(event) => markDraft({ ...draft, players: draft.players.map((item) => item.localKey === player.localKey ? { ...item, name: event.target.value } : item) })} />
                    <label className={duplicateJerseys.has(player.jersey_number ?? "") ? "jersey-warning" : ""}><input aria-label={`第 ${index + 1} 名球员号码`} value={player.jersey_number ?? ""} inputMode="numeric" maxLength={2} disabled={dataset.read_only} onChange={(event) => markDraft({ ...draft, players: draft.players.map((item) => item.localKey === player.localKey ? { ...item, jersey_number: event.target.value } : item) })} />{duplicateJerseys.has(player.jersey_number ?? "") && <small>同队重号</small>}</label>
                    <label className="roster-player-status"><input type="checkbox" checked={player.active} disabled={dataset.read_only} onChange={(event) => markDraft({ ...draft, players: draft.players.map((item) => item.localKey === player.localKey ? { ...item, active: event.target.checked } : item) })} /><span>{player.active ? "启用" : "停用"}</span></label>
                    <button type="button" disabled={dataset.read_only} onClick={() => markDraft({ ...draft, players: draft.players.filter((item) => item.localKey !== player.localKey) })}>{player.id ? "停用" : "移除"}</button>
                  </div>
                ))}
                {!draft.players.length && <p className="roster-empty">尚未添加球员，可以先创建球队后再补录。</p>}
              </div>

              {maintenancePreview && (
                <div className="roster-maintenance-preview">
                  <div><strong>维护修改二次确认</strong><p>{maintenancePreview.message}</p><span>比赛 {maintenancePreview.references.games ?? 0} · 抽签 {maintenancePreview.references.draw_assignments ?? 0} · 领队 {maintenancePreview.references.leader_bindings ?? 0}</span></div>
                  <div><button className="secondary-action" type="button" onClick={() => setMaintenancePreview(null)}>取消</button><button className="danger-action" type="button" disabled={busy} onClick={() => void saveExisting(maintenancePreview.maintenance_token)}>确认维护修改</button></div>
                </div>
              )}
              <div className="roster-save-bar">
                <span>{dataset.read_only ? "归档赛季只读" : dirty ? "有未保存修改" : "已与服务器同步"}</span>
                <button className="primary-action" type="button" disabled={dataset.read_only || busy || !dirty || !draft.name.trim() || !draft.divisionId || draft.players.some((player) => !player.name.trim())} onClick={() => void (draft.id ? saveExisting() : createTeam())}>{busy ? "保存中…" : draft.id ? "保存完整名单" : "创建球队"}</button>
              </div>
            </>
          )}
        </section>
      </div>
    </section>
  );
}
