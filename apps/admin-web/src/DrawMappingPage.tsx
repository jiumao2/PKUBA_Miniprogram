import { useEffect, useMemo, useRef, useState } from "react";

import type {
  AdminSeason,
  DrawAssignmentDataset,
  DrawAssignmentPreview,
  DrawGameAssignmentPreview,
  createAdminClient,
} from "@pkuba/api-client";

import { useAdminDirtySource } from "./dirtyGuard";
import { formatAdminSeasonLabel } from "./seasonLabel";
import "./draw-mapping.css";

type AdminClient = ReturnType<typeof createAdminClient>;
type DrawDivision = DrawAssignmentDataset["divisions"][number];
type DrawPhase = DrawDivision["phases"][number];
type DrawPhaseGame = DrawPhase["games"][number];

interface DrawMappingPageProps {
  client: AdminClient;
  seasons: AdminSeason[];
  seasonId: string;
  onSeasonChange: (seasonId: string) => void;
  onDataChanged: () => Promise<void>;
  onOpenTeams: () => void;
  onOpenConfiguration: () => void;
}

interface GameDraft {
  homeTeamId: string;
  awayTeamId: string;
}

const statusLabels: Record<string, string> = {
  SETUP: "准备中",
  PUBLISHED: "已公开",
  ARCHIVED: "已归档",
};

function initialGroupDraft(division: DrawDivision): Record<string, string> {
  return Object.fromEntries(
    division.groups.flatMap((group) =>
      group.slots.map((slot) => [slot.id, slot.team_id ?? ""]),
    ),
  );
}

function initialGameDrafts(division: DrawDivision): Record<string, GameDraft> {
  return Object.fromEntries(
    division.phases.flatMap((phase) =>
      phase.games.map((game) => [
        game.id,
        {
          homeTeamId: game.home_team_id ?? "",
          awayTeamId: game.away_team_id ?? "",
        },
      ]),
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
  const [selectedPhaseKey, setSelectedPhaseKey] = useState("GROUP");
  const [groupDraft, setGroupDraft] = useState<Record<string, string>>({});
  const [gameDrafts, setGameDrafts] = useState<Record<string, GameDraft>>({});
  const [groupPreview, setGroupPreview] = useState<DrawAssignmentPreview | null>(null);
  const [gamePreview, setGamePreview] = useState<DrawGameAssignmentPreview | null>(null);
  const [focusedGameId, setFocusedGameId] = useState("");
  const [overrideConfirmed, setOverrideConfirmed] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busyKey, setBusyKey] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const loadGeneration = useRef(0);

  const applyDataset = (
    next: DrawAssignmentDataset,
    preferredDivisionId = selectedDivisionId,
  ) => {
    const nextDivision =
      next.divisions.find((item) => item.id === preferredDivisionId) ??
      next.divisions[0];
    setDataset(next);
    setSelectedDivisionId(nextDivision?.id ?? "");
    setGroupDraft(nextDivision ? initialGroupDraft(nextDivision) : {});
    setGameDrafts(nextDivision ? initialGameDrafts(nextDivision) : {});
    setGroupPreview(null);
    setGamePreview(null);
    setOverrideConfirmed(false);
    if (nextDivision) {
      const hasCurrentPhase =
        selectedPhaseKey === "GROUP" ||
        nextDivision.phases.some((item) => item.key === selectedPhaseKey);
      setSelectedPhaseKey(hasCurrentPhase ? selectedPhaseKey : "GROUP");
    }
  };

  const loadDataset = async (targetSeasonId = seasonId) => {
    const generation = ++loadGeneration.current;
    setLoading(true);
    setError(null);
    try {
      const next = await client.getDrawAssignments(targetSeasonId);
      if (generation !== loadGeneration.current) return;
      applyDataset(next);
    } catch (caught) {
      if (generation !== loadGeneration.current) return;
      setDataset(null);
      setError(caught instanceof Error ? caught.message : "无法读取签位结果。");
    } finally {
      if (generation === loadGeneration.current) setLoading(false);
    }
  };

  useEffect(() => {
    void loadDataset();
    return () => {
      loadGeneration.current += 1;
    };
  }, [seasonId]);

  const division = dataset?.divisions.find((item) => item.id === selectedDivisionId);
  const phase = division?.phases.find((item) => item.key === selectedPhaseKey);
  const slots = useMemo(
    () => division?.groups.flatMap((group) => group.slots) ?? [],
    [division],
  );
  const activeTeams = useMemo(
    () => division?.teams.filter((team) => team.active) ?? [],
    [division],
  );
  const usedGroupTeams = useMemo(
    () => new Set(Object.values(groupDraft).filter(Boolean)),
    [groupDraft],
  );
  const groupComplete =
    slots.length > 0 &&
    slots.length === activeTeams.length &&
    slots.every((slot) => groupDraft[slot.id]) &&
    new Set(Object.values(groupDraft)).size === slots.length;
  const groupCountMismatch = Boolean(
    division && division.active_team_count !== division.slot_count,
  );
  const readOnly = dataset?.read_only ?? true;
  const groupDirty = Boolean(division) && slots.some(
    (slot) => (groupDraft[slot.id] ?? "") !== (slot.team_id ?? ""),
  );
  const gameDirty = (game: DrawPhaseGame, draft: GameDraft | undefined) => Boolean(
    draft
    && (
      draft.homeTeamId !== (game.home_team_id ?? "")
      || draft.awayTeamId !== (game.away_team_id ?? "")
    )
  );
  const anyDirty = groupDirty || Boolean(
    division?.phases.some((item) => item.games.some((game) => gameDirty(game, gameDrafts[game.id]))),
  );
  useAdminDirtySource(`draw-mapping:${seasonId}`, anyDirty);
  useEffect(() => {
    const beforeUnload = (event: BeforeUnloadEvent) => {
      if (anyDirty) event.preventDefault();
    };
    window.addEventListener("beforeunload", beforeUnload);
    return () => window.removeEventListener("beforeunload", beforeUnload);
  }, [anyDirty]);

  const switchDivision = (next: DrawDivision) => {
    if (
      anyDirty &&
      !window.confirm("当前页面有未保存修改，确定放弃并切换组别吗？")
    ) {
      return;
    }
    setSelectedDivisionId(next.id);
    setSelectedPhaseKey("GROUP");
    setGroupDraft(initialGroupDraft(next));
    setGameDrafts(initialGameDrafts(next));
    setGroupPreview(null);
    setGamePreview(null);
    setFocusedGameId("");
    setError(null);
    setNotice(null);
  };

  const switchSeason = (nextSeasonId: string) => {
    if (
      anyDirty &&
      !window.confirm("当前页面有未保存修改，确定切换赛季吗？")
    ) {
      return;
    }
    onSeasonChange(nextSeasonId);
  };

  const switchPhase = (key: string) => {
    setSelectedPhaseKey(key);
    setGamePreview(null);
    setOverrideConfirmed(false);
    setFocusedGameId("");
    setError(null);
  };

  const groupAssignments = () =>
    slots.map((slot) => ({
      slot_id: slot.id,
      team_id: groupDraft[slot.id] ?? "",
    }));

  const previewGroup = async () => {
    if (!dataset || !division || !groupComplete) return;
    setBusyKey("GROUP");
    setError(null);
    try {
      setGroupPreview(
        await client.previewDrawAssignments(dataset.season_id, {
          expected_season_version: dataset.season_version,
          division_id: division.id,
          assignments: groupAssignments(),
        }),
      );
    } catch (caught) {
      setError(
        caught instanceof Error ? caught.message : "无法预览小组签位影响。",
      );
    } finally {
      setBusyKey("");
    }
  };

  const saveGroup = async () => {
    if (!dataset || !division || !groupPreview) return;
    setBusyKey("GROUP");
    setError(null);
    try {
      const updated = await client.updateDrawAssignments(dataset.season_id, {
        expected_season_version: dataset.season_version,
        division_id: division.id,
        assignments: groupAssignments(),
        impact_hash: groupPreview.impact_hash,
      });
      applyDataset(updated, division.id);
      setNotice(`${division.name}的小组初始签位已整组保存。`);
      await onDataChanged();
    } catch (caught) {
      setGroupPreview(null);
      setError(caught instanceof Error ? caught.message : "小组签位保存失败。");
    } finally {
      setBusyKey("");
    }
  };

  const updateGameDraft = (
    gameId: string,
    side: "homeTeamId" | "awayTeamId",
    teamId: string,
  ) => {
    setGameDrafts((current) => ({
      ...current,
      [gameId]: {
        ...current[gameId],
        [side]: teamId,
      },
    }));
    setFocusedGameId(gameId);
    setGamePreview(null);
    setOverrideConfirmed(false);
    setError(null);
    setNotice(null);
  };

  const previewGame = async (game: DrawPhaseGame) => {
    if (!dataset) return;
    const draft = gameDrafts[game.id];
    if (!draft?.homeTeamId || !draft.awayTeamId) return;
    setBusyKey(game.id);
    setFocusedGameId(game.id);
    setError(null);
    setOverrideConfirmed(false);
    try {
      setGamePreview(
        await client.previewGameDrawAssignments(dataset.season_id, game.id, {
          expected_season_version: dataset.season_version,
          expected_game_version: game.version,
          home_team_id: draft.homeTeamId,
          away_team_id: draft.awayTeamId,
        }),
      );
    } catch (caught) {
      setGamePreview(null);
      setError(
        caught instanceof Error ? caught.message : "无法预览该场签位影响。",
      );
    } finally {
      setBusyKey("");
    }
  };

  const saveGame = async (game: DrawPhaseGame) => {
    if (!dataset || !gamePreview) return;
    const draft = gameDrafts[game.id];
    setBusyKey(game.id);
    setError(null);
    try {
      const updated = await client.updateGameDrawAssignments(
        dataset.season_id,
        game.id,
        {
          expected_season_version: dataset.season_version,
          expected_game_version: game.version,
          home_team_id: draft.homeTeamId,
          away_team_id: draft.awayTeamId,
          override_warnings: gamePreview.requires_override,
          impact_hash: gamePreview.impact_hash,
        },
      );
      applyDataset(updated, division?.id);
      setSelectedPhaseKey(phase?.key ?? "GROUP");
      setNotice(`${game.code} 的双方签位已保存。`);
      await onDataChanged();
    } catch (caught) {
      setGamePreview(null);
      setError(caught instanceof Error ? caught.message : "该场签位保存失败。");
    } finally {
      setBusyKey("");
    }
  };

  if (loading) {
    return <section className="draw-state">正在读取签位和球队…</section>;
  }
  if (!dataset) {
    return (
      <section className="draw-state draw-state-error">
        {error ?? "暂无签位结果"}
      </section>
    );
  }

  const focusedGame =
    phase?.games.find((game) => game.id === focusedGameId) ?? phase?.games[0];
  const phaseUsedTeams = new Set(
    phase?.games.flatMap((game) => {
      const draft = gameDrafts[game.id];
      return draft
        ? [draft.homeTeamId, draft.awayTeamId].filter(Boolean)
        : [];
    }) ?? [],
  );
  const phaseUnassignedTeams = activeTeams.filter(
    (team) => !phaseUsedTeams.has(team.id),
  );
  const previousWinnerTeams =
    phase?.previous_winner_ids
      .map((teamId) => activeTeams.find((team) => team.id === teamId))
      .filter(
        (team): team is DrawDivision["teams"][number] => Boolean(team),
      ) ?? [];

  return (
    <section className="draw-workspace">
      <header className="draw-toolbar">
        <label>
          <span>赛季</span>
          <select
            value={seasonId}
            onChange={(event) => switchSeason(event.target.value)}
          >
            {seasons.map((season) => (
              <option key={season.id} value={season.id}>
                {formatAdminSeasonLabel(season)}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>组别</span>
          <select
            value={selectedDivisionId}
            onChange={(event) => {
              const next = dataset.divisions.find(
                (item) => item.id === event.target.value,
              );
              if (next) switchDivision(next);
            }}
          >
            {dataset.divisions.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </select>
        </label>
        <div className="draw-toolbar-status">
          <span
            className={`draw-season-status ${dataset.season_status.toLowerCase()}`}
          >
            {statusLabels[dataset.season_status] ?? dataset.season_status}
          </span>
        </div>
      </header>

      {error && (
        <div className="draw-message error" role="alert">
          {error}
        </div>
      )}
      {notice && (
        <div className="draw-message" role="status">
          {notice}
        </div>
      )}
      {readOnly && (
        <div className="draw-readonly">
          <strong>当前页面只读</strong>
          <span>{dataset.locked_reason}</span>
        </div>
      )}

      {!division ? (
        <div className="draw-empty">当前赛季没有可用组别。</div>
      ) : (
        <div className="draw-operation-layout">
          <nav className="draw-stage-nav" aria-label="签位阶段">
            <p>阶段</p>
            <button
              className={selectedPhaseKey === "GROUP" ? "active" : ""}
              type="button"
              onClick={() => switchPhase("GROUP")}
            >
              <span>小组初始签位</span>
              <small>
                {division.assigned_count}/{division.slot_count}
              </small>
            </button>
            {division.phases.map((item) => (
              <button
                className={`${selectedPhaseKey === item.key ? "active" : ""} ${
                  item.games.some((game) => game.review_required)
                    ? "needs-review"
                    : ""
                }`}
                key={item.key}
                type="button"
                onClick={() => switchPhase(item.key)}
              >
                <span>{item.label}</span>
                <small>
                  {
                    item.games.filter(
                      (game) => game.home_team_id && game.away_team_id,
                    ).length
                  }
                  /{item.games.length}
                </small>
              </button>
            ))}
          </nav>

          <main className="draw-editor">
            {selectedPhaseKey === "GROUP" ? (
              <GroupEditor
                division={division}
                draft={groupDraft}
                usedTeamIds={usedGroupTeams}
                readOnly={readOnly}
                busy={busyKey === "GROUP"}
                countMismatch={groupCountMismatch}
                complete={groupComplete}
                dirty={groupDirty}
                onChange={(slotId, teamId) => {
                  setGroupDraft((current) => ({
                    ...current,
                    [slotId]: teamId,
                  }));
                  setGroupPreview(null);
                  setError(null);
                }}
                onPreview={() => void previewGroup()}
                onReset={() => {
                  setGroupDraft(initialGroupDraft(division));
                  setGroupPreview(null);
                }}
                onOpenTeams={onOpenTeams}
                onOpenConfiguration={onOpenConfiguration}
              />
            ) : phase ? (
              <section className="draw-phase-editor">
                <div className="draw-section-heading">
                  <div>
                    <p>
                      {phase.stage === "RELEGATION"
                        ? "独立手工录入"
                        : "逐场手工录入"}
                    </p>
                    <h2>
                      {division.name} · {phase.label}
                    </h2>
                  </div>
                  <span>{phase.games.length} 场</span>
                </div>
                {phase.games.map((game) => {
                  const draft = gameDrafts[game.id];
                  return (
                    <GameEditor
                      key={game.id}
                      game={game}
                      draft={draft}
                      dirty={gameDirty(game, draft)}
                      teams={activeTeams}
                      phaseUsedTeams={phaseUsedTeams}
                      readOnly={readOnly}
                      busy={busyKey === game.id}
                      focused={focusedGame?.id === game.id}
                      onFocus={() => {
                        setFocusedGameId(game.id);
                        setGamePreview(null);
                        setOverrideConfirmed(false);
                      }}
                      onChange={(side, teamId) =>
                        updateGameDraft(game.id, side, teamId)
                      }
                      onPreview={() => void previewGame(game)}
                    />
                  );
                })}
                {!phase.games.length && (
                  <div className="draw-empty">当前阶段没有正式比赛。</div>
                )}
              </section>
            ) : (
              <div className="draw-empty">当前阶段不存在。</div>
            )}
          </main>

          <aside className="draw-validation-panel" aria-label="签位校验">
            {selectedPhaseKey === "GROUP" ? (
              <GroupInspector
                teams={activeTeams.filter(
                  (team) => !usedGroupTeams.has(team.id),
                )}
                preview={groupPreview}
                busy={busyKey === "GROUP"}
                onSave={() => void saveGroup()}
              />
            ) : (
              <GameInspector
                phase={phase}
                game={focusedGame}
                previousWinnerTeams={previousWinnerTeams}
                unassignedTeams={phaseUnassignedTeams}
                preview={gamePreview}
                overrideConfirmed={overrideConfirmed}
                busy={Boolean(focusedGame && busyKey === focusedGame.id)}
                onOverrideChange={setOverrideConfirmed}
                onSave={() => focusedGame && void saveGame(focusedGame)}
              />
            )}
          </aside>
        </div>
      )}
    </section>
  );
}

function GroupEditor({
  division,
  draft,
  usedTeamIds,
  readOnly,
  busy,
  countMismatch,
  complete,
  dirty,
  onChange,
  onPreview,
  onReset,
  onOpenTeams,
  onOpenConfiguration,
}: {
  division: DrawDivision;
  draft: Record<string, string>;
  usedTeamIds: Set<string>;
  readOnly: boolean;
  busy: boolean;
  countMismatch: boolean;
  complete: boolean;
  dirty: boolean;
  onChange: (slotId: string, teamId: string) => void;
  onPreview: () => void;
  onReset: () => void;
  onOpenTeams: () => void;
  onOpenConfiguration: () => void;
}) {
  if (!division.groups.length) {
    return (
      <div className="draw-empty draw-empty-action">
        <div>
          <strong>尚未生成小组初始签位</strong>
          <span>请先配置签位方案并完成赛程导入。</span>
        </div>
        <button
          className="secondary-action"
          type="button"
          onClick={onOpenConfiguration}
        >
          前往赛季与组别
        </button>
      </div>
    );
  }
  return (
    <section className="draw-group-editor">
      <div className="draw-section-heading">
        <div>
          <p>整组原子保存</p>
          <h2>{division.name} · 小组初始签位</h2>
        </div>
        <span>
          {division.assigned_count}/{division.slot_count}
        </span>
      </div>
      {countMismatch && (
        <div className="draw-mismatch" role="alert">
          <div>
            <strong>球队数与签位数不一致</strong>
            <span>
              {division.active_team_count} 支启用球队 / {division.slot_count} 个签位
            </span>
          </div>
          <div>
            <button className="text-action" type="button" onClick={onOpenTeams}>
              检查球队
            </button>
            <button
              className="text-action"
              type="button"
              onClick={onOpenConfiguration}
            >
              检查签位
            </button>
          </div>
        </div>
      )}
      {division.groups.map((group) => (
        <section className="draw-group-block" key={group.id}>
          <header>
            <strong>{group.name}</strong>
            <span>{group.code.toUpperCase()}</span>
          </header>
          {group.slots.map((slot) => {
            const selectedId = draft[slot.id] ?? "";
            const options = division.teams.filter(
              (team) =>
                (team.active &&
                  (!usedTeamIds.has(team.id) || team.id === selectedId)) ||
                team.id === selectedId,
            );
            return (
              <label className="draw-slot-row" key={slot.id}>
                <span>
                  <strong>{slot.code}</strong>
                  <small>{slot.label}</small>
                </span>
                <select
                  aria-label={`${slot.code} 对应球队`}
                  value={selectedId}
                  disabled={readOnly || busy}
                  onChange={(event) => onChange(slot.id, event.target.value)}
                >
                  <option value="">请选择球队</option>
                  {options.map((team) => (
                    <option key={team.id} value={team.id}>
                      {team.name}
                      {team.active ? "" : "（已停用）"}
                    </option>
                  ))}
                </select>
              </label>
            );
          })}
        </section>
      ))}
      <div className="draw-group-save">
        <span>{dirty ? "有未保存修改" : "已与服务器同步"}</span>
        <div>
          <button
            className="text-action"
            type="button"
            disabled={!dirty || busy}
            onClick={onReset}
          >
            撤销
          </button>
          <button
            className="primary-action"
            type="button"
            disabled={readOnly || busy || !dirty || !complete || countMismatch}
            onClick={onPreview}
          >
            {busy ? "检查中…" : "预览整组影响"}
          </button>
        </div>
      </div>
    </section>
  );
}

function GameEditor({
  game,
  draft,
  dirty,
  teams,
  phaseUsedTeams,
  readOnly,
  busy,
  focused,
  onFocus,
  onChange,
  onPreview,
}: {
  game: DrawPhaseGame;
  draft: GameDraft;
  dirty: boolean;
  teams: DrawDivision["teams"];
  phaseUsedTeams: Set<string>;
  readOnly: boolean;
  busy: boolean;
  focused: boolean;
  onFocus: () => void;
  onChange: (side: "homeTeamId" | "awayTeamId", teamId: string) => void;
  onPreview: () => void;
}) {
  const optionsFor = (currentId: string, otherId: string) =>
    teams.filter(
      (team) =>
        team.active &&
        team.id !== otherId &&
        (!phaseUsedTeams.has(team.id) || team.id === currentId),
    );
  return (
    <article
      className={`draw-game ${focused ? "focused" : ""} ${
        game.review_required ? "needs-review" : ""
      }`}
      onClick={onFocus}
    >
      <header>
        <div>
          <strong>{game.code}</strong>
          <span>{game.date} · {game.start_time}</span>
        </div>
        <div>
          <span>{game.venue_name}</span>
          {game.review_required && <b>签位待复核</b>}
        </div>
      </header>
      <div className="draw-game-sides">
        <label>
          <span>
            <small>主方签位</small>
            <strong>{game.home_slot_code || game.home_slot_label}</strong>
          </span>
          <select
            aria-label={`${game.code} 主方球队`}
            value={draft?.homeTeamId ?? ""}
            disabled={readOnly || busy}
            onChange={(event) => onChange("homeTeamId", event.target.value)}
          >
            <option value="">待抽签</option>
            {optionsFor(
              draft?.homeTeamId ?? "",
              draft?.awayTeamId ?? "",
            ).map((team) => (
              <option key={team.id} value={team.id}>
                {team.name}
              </option>
            ))}
          </select>
          <ValidationBadge validation={game.home_validation} />
        </label>
        <label>
          <span>
            <small>客方签位</small>
            <strong>{game.away_slot_code || game.away_slot_label}</strong>
          </span>
          <select
            aria-label={`${game.code} 客方球队`}
            value={draft?.awayTeamId ?? ""}
            disabled={readOnly || busy}
            onChange={(event) => onChange("awayTeamId", event.target.value)}
          >
            <option value="">待抽签</option>
            {optionsFor(
              draft?.awayTeamId ?? "",
              draft?.homeTeamId ?? "",
            ).map((team) => (
              <option key={team.id} value={team.id}>
                {team.name}
              </option>
            ))}
          </select>
          <ValidationBadge validation={game.away_validation} />
        </label>
      </div>
      <footer className="draw-game-save">
        <span>{dirty ? "本场有未保存修改" : "本场已保存"}</span>
        <button
          className="primary-action"
          type="button"
          disabled={
            readOnly ||
            busy ||
            !dirty ||
            !draft.homeTeamId ||
            !draft.awayTeamId
          }
          onClick={(event) => {
            event.stopPropagation();
            onPreview();
          }}
        >
          {busy ? "检查中…" : "逐场预览"}
        </button>
      </footer>
    </article>
  );
}

function ValidationBadge({
  validation,
}: {
  validation: DrawPhaseGame["home_validation"];
}) {
  if (validation.review_required) {
    return <b className="validation-badge warning">待复核</b>;
  }
  if (validation.status === "WINNER_CONFIRMED") {
    return <b className="validation-badge valid">胜队已确认</b>;
  }
  if (validation.mode === "SUPERADMIN_OVERRIDE") {
    return <b className="validation-badge override">已越过校验</b>;
  }
  if (validation.status === "AWAITING_RESULT") {
    return <b className="validation-badge pending">等待上一轮赛果</b>;
  }
  return <b className="validation-badge neutral">手工签位</b>;
}

function GroupInspector({
  teams,
  preview,
  busy,
  onSave,
}: {
  teams: DrawDivision["teams"];
  preview: DrawAssignmentPreview | null;
  busy: boolean;
  onSave: () => void;
}) {
  return (
    <>
      <header>
        <p>校验信息</p>
        <h3>小组签位</h3>
      </header>
      <section>
        <span className="inspector-label">未分配球队</span>
        <strong className="inspector-number">{teams.length}</strong>
        {teams.length ? (
          <ul>
            {teams.map((team) => (
              <li key={team.id}>{team.name}</li>
            ))}
          </ul>
        ) : (
          <p>全部球队已选择。</p>
        )}
      </section>
      {preview && (
        <section
          className={
            preview.blockers.length ? "inspector-alert" : "inspector-success"
          }
        >
          <strong>{preview.change_count} 个签位将变更</strong>
          <p>
            影响 {preview.affected_game_count} 场比赛；
            {preview.public_impact ? "会立即影响公开页面。" : "当前不会公开显示。"}
          </p>
          {preview.blockers.map((blocker) => (
            <p key={blocker.code}>{blocker.message}</p>
          ))}
          <button
            className="primary-action"
            type="button"
            disabled={busy || !preview.can_apply}
            onClick={onSave}
          >
            {busy ? "保存中…" : "确认整组保存"}
          </button>
        </section>
      )}
    </>
  );
}

function GameInspector({
  phase,
  game,
  previousWinnerTeams,
  unassignedTeams,
  preview,
  overrideConfirmed,
  busy,
  onOverrideChange,
  onSave,
}: {
  phase: DrawPhase | undefined;
  game: DrawPhaseGame | undefined;
  previousWinnerTeams: DrawDivision["teams"];
  unassignedTeams: DrawDivision["teams"];
  preview: DrawGameAssignmentPreview | null;
  overrideConfirmed: boolean;
  busy: boolean;
  onOverrideChange: (value: boolean) => void;
  onSave: () => void;
}) {
  return (
    <>
      <header>
        <p>校验信息</p>
        <h3>{game?.code ?? phase?.label ?? "选择比赛"}</h3>
      </header>
      {phase?.previous_phase_key && (
        <section>
          <span className="inspector-label">上一轮已产生胜队</span>
          {previousWinnerTeams.length ? (
            <ul>
              {previousWinnerTeams.map((team) => (
                <li key={team.id}>{team.name}</li>
              ))}
            </ul>
          ) : (
            <p>上一轮尚未产生正式胜队。</p>
          )}
        </section>
      )}
      <section>
        <span className="inspector-label">本轮未分配球队</span>
        <strong className="inspector-number">{unassignedTeams.length}</strong>
        {unassignedTeams.length > 0 && (
          <ul>
            {unassignedTeams.map((team) => (
              <li key={team.id}>{team.name}</li>
            ))}
          </ul>
        )}
      </section>
      {game?.review_required && (
        <section className="inspector-alert">
          <strong>签位待复核</strong>
          <p>上一轮赛果与当前人工签位不再一致。球队不会被自动修改。</p>
        </section>
      )}
      {preview && (
        <section
          className={
            preview.warnings.length ? "inspector-alert" : "inspector-success"
          }
        >
          <strong>{preview.can_apply ? "预览完成" : "当前不能保存"}</strong>
          <p>
            {preview.public_impact
              ? "保存后会立即影响当前公开对阵。"
              : "当前赛季未公开。"}
          </p>
          {preview.blockers.map((blocker) => (
            <p key={blocker.code}>{blocker.message}</p>
          ))}
          {preview.warnings.map((warning) => (
            <p key={`${warning.side}-${warning.code}`}>{warning.message}</p>
          ))}
          {preview.requires_override && (
            <label className="override-confirm">
              <input
                type="checkbox"
                checked={overrideConfirmed}
                onChange={(event) => onOverrideChange(event.target.checked)}
              />
              <span>我确认越过上一轮胜队校验，并按当前球队保存</span>
            </label>
          )}
          <button
            className={
              preview.requires_override ? "danger-action" : "primary-action"
            }
            type="button"
            disabled={
              busy ||
              !preview.can_apply ||
              (preview.requires_override && !overrideConfirmed)
            }
            onClick={onSave}
          >
            {busy
              ? "保存中…"
              : preview.requires_override
                ? "确认越级并保存"
                : "确认保存本场"}
          </button>
        </section>
      )}
    </>
  );
}
