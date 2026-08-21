import { useEffect, useMemo, useState } from "react";

import type {
  AdminSeason,
  DrawAssignmentDataset,
  DrawAssignmentPreview,
  createAdminClient,
} from "@pkuba/api-client";

import "./draw-mapping.css";

type AdminClient = ReturnType<typeof createAdminClient>;
type DrawDivision = DrawAssignmentDataset["divisions"][number];

interface DrawMappingPageProps {
  client: AdminClient;
  seasons: AdminSeason[];
  seasonId: string;
  onSeasonChange: (seasonId: string) => void;
  onDataChanged: () => Promise<void>;
  onOpenTeams: () => void;
  onOpenConfiguration: () => void;
}

const statusLabels: Record<string, string> = {
  SETUP: "准备中",
  PRE_DRAW_PUBLIC: "抽签前公开",
  ACTIVE: "进行中",
  ARCHIVED: "已归档",
};

function divisionDraft(division: DrawDivision): Record<string, string> {
  return Object.fromEntries(
    division.groups.flatMap((group) =>
      group.slots.map((slot) => [slot.id, slot.team_id ?? ""]),
    ),
  );
}

export function DrawMappingPage({
  client,
  seasons,
  seasonId,
  onSeasonChange,
  onDataChanged,
  onOpenTeams,
  onOpenConfiguration,
}: DrawMappingPageProps) {
  const [dataset, setDataset] = useState<DrawAssignmentDataset | null>(null);
  const [selectedDivisionId, setSelectedDivisionId] = useState("");
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [dirty, setDirty] = useState(false);
  const [preview, setPreview] = useState<DrawAssignmentPreview | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const loadDataset = async (targetSeasonId = seasonId) => {
    setLoading(true);
    setError(null);
    try {
      const next = await client.getDrawAssignments(targetSeasonId);
      setDataset(next);
      const selected =
        next.divisions.find((division) => division.id === selectedDivisionId) ??
        next.divisions.find((division) => division.slot_count > 0) ??
        next.divisions[0];
      setSelectedDivisionId(selected?.id ?? "");
      setDraft(selected ? divisionDraft(selected) : {});
      setDirty(false);
      setPreview(null);
    } catch (caught) {
      setDataset(null);
      setError(caught instanceof Error ? caught.message : "无法读取抽签映射。");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadDataset();
  }, [seasonId]);

  useEffect(() => {
    const beforeUnload = (event: BeforeUnloadEvent) => {
      if (!dirty) return;
      event.preventDefault();
    };
    window.addEventListener("beforeunload", beforeUnload);
    return () => window.removeEventListener("beforeunload", beforeUnload);
  }, [dirty]);

  const selectedDivision = dataset?.divisions.find(
    (division) => division.id === selectedDivisionId,
  );
  const slots = useMemo(
    () => selectedDivision?.groups.flatMap((group) => group.slots) ?? [],
    [selectedDivision],
  );
  const activeTeams = useMemo(
    () => selectedDivision?.teams.filter((team) => team.active) ?? [],
    [selectedDivision],
  );
  const usedTeamIds = useMemo(
    () => new Set(Object.values(draft).filter(Boolean)),
    [draft],
  );
  const unassignedTeams = activeTeams.filter((team) => !usedTeamIds.has(team.id));
  const selectedActiveTeamIds = Object.values(draft).filter((teamId) =>
    activeTeams.some((team) => team.id === teamId),
  );
  const complete =
    slots.length > 0 &&
    activeTeams.length === slots.length &&
    slots.every((slot) => Boolean(draft[slot.id])) &&
    new Set(selectedActiveTeamIds).size === slots.length;
  const countMismatch = Boolean(
    selectedDivision && selectedDivision.active_team_count !== selectedDivision.slot_count,
  );
  const readOnly = dataset?.read_only ?? true;

  const resetSelectedDivision = (division = selectedDivision) => {
    if (!division) return;
    setDraft(divisionDraft(division));
    setDirty(false);
    setPreview(null);
    setError(null);
  };

  const switchDivision = (division: DrawDivision) => {
    if (
      dirty &&
      !window.confirm(`当前${selectedDivision?.name ?? "组别"}有未保存修改，确定放弃吗？`)
    ) {
      return;
    }
    setSelectedDivisionId(division.id);
    setDraft(divisionDraft(division));
    setDirty(false);
    setPreview(null);
    setError(null);
    setNotice(null);
  };

  const switchSeason = (nextSeasonId: string) => {
    if (dirty && !window.confirm("当前组别有未保存修改，确定切换赛季吗？")) return;
    onSeasonChange(nextSeasonId);
  };

  const updateSlot = (slotId: string, teamId: string) => {
    setDraft((current) => ({ ...current, [slotId]: teamId }));
    setDirty(true);
    setPreview(null);
    setError(null);
    setNotice(null);
  };

  const buildAssignments = () =>
    slots.map((slot) => ({ slot_id: slot.id, team_id: draft[slot.id] ?? "" }));

  const checkChanges = async () => {
    if (!dataset || !selectedDivision || !complete) return;
    setBusy(true);
    setError(null);
    try {
      setPreview(
        await client.previewDrawAssignments(dataset.season_id, {
          expected_season_version: dataset.season_version,
          division_id: selectedDivision.id,
          assignments: buildAssignments(),
        }),
      );
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "无法预览抽签影响。");
    } finally {
      setBusy(false);
    }
  };

  const saveChanges = async () => {
    if (!dataset || !selectedDivision || !preview) return;
    setBusy(true);
    setError(null);
    try {
      const updated = await client.updateDrawAssignments(dataset.season_id, {
        expected_season_version: dataset.season_version,
        division_id: selectedDivision.id,
        assignments: buildAssignments(),
        impact_hash: preview.impact_hash,
      });
      setDataset(updated);
      const nextDivision = updated.divisions.find(
        (division) => division.id === selectedDivision.id,
      );
      if (nextDivision) setDraft(divisionDraft(nextDivision));
      setDirty(false);
      setPreview(null);
      setNotice(`${selectedDivision.name}的抽签结果已保存，并同步到相关比赛。`);
      await onDataChanged();
    } catch (caught) {
      setPreview(null);
      setError(caught instanceof Error ? caught.message : "抽签映射保存失败。");
    } finally {
      setBusy(false);
    }
  };

  if (loading) {
    return <section className="draw-state">正在读取签位和球队…</section>;
  }
  if (!dataset) {
    return <section className="draw-state draw-state-error">{error ?? "暂无抽签数据"}</section>;
  }

  return (
    <section className="draw-workspace">
      <div className="draw-toolbar">
        <label>
          <span>当前赛季</span>
          <select value={seasonId} onChange={(event) => switchSeason(event.target.value)}>
            {seasons.map((season) => (
              <option key={season.id} value={season.id}>{season.name}</option>
            ))}
          </select>
        </label>
        <div className="draw-toolbar-status">
          <span className={`draw-season-status ${dataset.season_status.toLowerCase()}`}>
            {statusLabels[dataset.season_status] ?? dataset.season_status}
          </span>
          <span>{dataset.divisions.filter((division) => division.complete).length} / {dataset.divisions.length} 个组别完成</span>
        </div>
      </div>

      <div className="draw-heading">
        <div>
          <p className="draw-kicker">初始签位</p>
          <h2>按组别确认球队落位</h2>
          <p>每支启用球队只能出现一次。保存后，相关小组赛将立即显示真实球队。</p>
        </div>
        <div className="draw-progress" aria-label="组别完成进度">
          {dataset.divisions.map((division) => {
            const assigned = division.id === selectedDivisionId && dirty
              ? Object.values(draft).filter(Boolean).length
              : division.assigned_count;
            return (
              <button
                className={division.id === selectedDivisionId ? "active" : ""}
                type="button"
                key={division.id}
                onClick={() => switchDivision(division)}
              >
                <span>{division.name}</span>
                <strong>{assigned}/{division.slot_count}</strong>
                <small>{division.complete ? "已完成" : division.slot_count ? "待填写" : "无签位"}</small>
              </button>
            );
          })}
        </div>
      </div>

      {error && <div className="draw-message error" role="alert">{error}</div>}
      {notice && <div className="draw-message" role="status">{notice}</div>}
      {readOnly && <div className="draw-readonly"><strong>当前页面只读</strong><span>{dataset.locked_reason}</span></div>}

      {!selectedDivision ? (
        <div className="draw-empty">当前赛季没有可用组别。</div>
      ) : !slots.length ? (
        <div className="draw-empty draw-empty-action">
          <div>
            <strong>{selectedDivision.name}尚未生成初始签位</strong>
            <span>请先配置签位方案并完成赛程导入；淘汰赛占位不会显示在本页。</span>
          </div>
          <button className="secondary-action" type="button" onClick={onOpenConfiguration}>前往赛季与组别</button>
        </div>
      ) : (
        <>
          {countMismatch && (
            <div className="draw-mismatch" role="alert">
              <div>
                <strong>球队数与签位数不一致</strong>
                <span>{selectedDivision.active_team_count} 支启用球队 / {selectedDivision.slot_count} 个初始签位，必须一致后才能保存。</span>
              </div>
              <div>
                <button className="text-action" type="button" onClick={onOpenTeams}>检查球队与名单</button>
                <button className="text-action" type="button" onClick={onOpenConfiguration}>检查签位方案</button>
              </div>
            </div>
          )}

          <div className="draw-layout">
            <div className="draw-groups">
              {selectedDivision.groups.map((group) => (
                <section className="draw-group" key={group.id} aria-labelledby={`draw-group-${group.id}`}>
                  <div className="draw-group-heading">
                    <div>
                      <span>{group.code.toUpperCase()}</span>
                      <h3 id={`draw-group-${group.id}`}>{group.name}</h3>
                    </div>
                    <small>{group.slots.filter((slot) => Boolean(draft[slot.id])).length}/{group.slots.length} 已填写</small>
                  </div>
                  <div className="draw-slot-table" role="table" aria-label={`${group.name}签位映射`}>
                    <div className="draw-slot-row header" role="row">
                      <span>签位</span><span>对应球队</span><span>状态</span>
                    </div>
                    {group.slots.map((slot) => {
                      const currentTeam = selectedDivision.teams.find(
                        (team) => team.id === draft[slot.id],
                      );
                      const options = selectedDivision.teams.filter(
                        (team) =>
                          (team.active && (!usedTeamIds.has(team.id) || team.id === draft[slot.id])) ||
                          team.id === draft[slot.id],
                      );
                      return (
                        <label className="draw-slot-row" role="row" key={slot.id}>
                          <span className="draw-slot-code"><strong>{slot.code}</strong><small>{slot.label}</small></span>
                          <select
                            aria-label={`${slot.code} 对应球队`}
                            value={draft[slot.id] ?? ""}
                            disabled={readOnly || busy}
                            onChange={(event) => updateSlot(slot.id, event.target.value)}
                          >
                            <option value="">请选择球队</option>
                            {options.map((team) => (
                              <option key={team.id} value={team.id}>
                                {team.name}{team.active ? "" : "（已停用，须替换）"}
                              </option>
                            ))}
                          </select>
                          <span className={currentTeam?.active ? "assigned" : currentTeam ? "inactive" : "empty"}>
                            {currentTeam?.active ? "已分配" : currentTeam ? "球队已停用" : "待选择"}
                          </span>
                        </label>
                      );
                    })}
                  </div>
                </section>
              ))}
            </div>

            <aside className="draw-inspector" aria-label="未分配球队">
              <div>
                <p className="draw-kicker">未分配球队</p>
                <strong>{unassignedTeams.length}</strong>
                <span>支</span>
              </div>
              {unassignedTeams.length ? (
                <ul>{unassignedTeams.map((team) => <li key={team.id}>{team.name}</li>)}</ul>
              ) : (
                <p className="draw-all-assigned">全部启用球队均已选择。</p>
              )}
              <small>已选择的球队会从其他签位下拉栏中移除，避免重复落位。</small>
            </aside>
          </div>

          <div className="draw-savebar">
            <span>{readOnly ? "归档赛季只读" : dirty ? `${selectedDivision.name}有未保存修改` : "已与服务器同步"}</span>
            <div>
              <button className="text-action" type="button" disabled={!dirty || busy} onClick={() => resetSelectedDivision()}>撤销修改</button>
              <button className="primary-action" type="button" disabled={readOnly || busy || !dirty || !complete || countMismatch} onClick={() => void checkChanges()}>
                {busy ? "检查中…" : "检查并保存"}
              </button>
            </div>
          </div>
        </>
      )}

      {preview && (
        <div className="dialog-backdrop" role="presentation" onMouseDown={() => !busy && setPreview(null)}>
          <section className="draw-preview-dialog" role="dialog" aria-modal="true" aria-labelledby="draw-preview-title" onMouseDown={(event) => event.stopPropagation()}>
            <div className="dialog-heading">
              <div>
                <p className="draw-kicker">保存前核对</p>
                <h2 id="draw-preview-title">{preview.division_name}抽签影响</h2>
              </div>
              <button className="dialog-close" type="button" disabled={busy} onClick={() => setPreview(null)} aria-label="关闭">×</button>
            </div>
            <div className="draw-preview-summary">
              <div><span>签位变更</span><strong>{preview.change_count}</strong></div>
              <div><span>受影响比赛</span><strong>{preview.affected_game_count}</strong></div>
              <div><span>公开影响</span><strong>{preview.public_impact ? "有" : "无"}</strong></div>
            </div>
            {preview.blockers.length > 0 && (
              <div className="draw-preview-blockers" role="alert">
                {preview.blockers.map((blocker) => <p key={blocker.code}><strong>{blocker.count} 项</strong>{blocker.message}</p>)}
              </div>
            )}
            <div className="draw-preview-changes">
              {preview.changes.map((change) => (
                <div key={change.slot_id}>
                  <strong>{change.slot_code}</strong>
                  <span>{change.before_team_name ?? "未分配"}</span>
                  <span aria-hidden="true">→</span>
                  <span>{change.after_team_name}</span>
                </div>
              ))}
            </div>
            {preview.affected_games.length > 0 && (
              <details className="draw-preview-games">
                <summary>查看受影响比赛（{preview.affected_game_count}）</summary>
                {preview.affected_games.slice(0, 12).map((game) => (
                  <p key={game.id}><strong>{game.code}</strong><span>{game.date} · {game.start_time}</span><span>{game.before_home_name} vs {game.before_away_name}</span><span>更新为 {game.after_home_name} vs {game.after_away_name}</span></p>
                ))}
                {preview.affected_games.length > 12 && <small>另有 {preview.affected_games.length - 12} 场，保存时将按同一映射同步更新。</small>}
              </details>
            )}
            <div className="dialog-actions">
              <button className="secondary-action" type="button" disabled={busy} onClick={() => setPreview(null)}>返回修改</button>
              <button className="primary-action" type="button" disabled={busy || !preview.can_apply || !preview.requires_confirmation} onClick={() => void saveChanges()}>
                {busy ? "保存中…" : "确认写入抽签结果"}
              </button>
            </div>
          </section>
        </div>
      )}
    </section>
  );
}
