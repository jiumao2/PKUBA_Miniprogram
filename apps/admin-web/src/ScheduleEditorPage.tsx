import { type FormEvent, useEffect, useMemo, useState } from "react";
import type {
  AdminSeason,
  MobileAdminGame,
  MobileScheduleOptions,
  UpdateMobileAdminGame,
} from "@pkuba/api-client";

import "./operation-pages.css";

type AdminClient = ReturnType<typeof import("@pkuba/api-client").createAdminClient>;

const statuses = [
  { value: "SCHEDULED", label: "未赛" },
  { value: "COMPLETED", label: "已完成" },
  { value: "FORFEIT", label: "弃权" },
  { value: "VOID", label: "已作废" },
];

export function ScheduleEditorPage({
  client,
  games,
  seasons,
  season,
  onSeasonChange,
  onUpdated,
}: {
  client: AdminClient;
  games: MobileAdminGame[];
  seasons: AdminSeason[];
  season: AdminSeason;
  onSeasonChange: (seasonId: string) => void;
  onUpdated: () => Promise<void>;
}) {
  const [options, setOptions] = useState<MobileScheduleOptions | null>(null);
  const [selected, setSelected] = useState<MobileAdminGame | null>(null);
  const [divisionId, setDivisionId] = useState("all");
  const [adjustability, setAdjustability] = useState("all");
  const [query, setQuery] = useState("");
  const [cancelRequest, setCancelRequest] = useState(false);
  const [overrideRules, setOverrideRules] = useState(false);
  const [acknowledged, setAcknowledged] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    setSelected(null);
    client.getAdminScheduleOptions(season.id).then(setOptions).catch((reason: unknown) => {
      setMessage(reason instanceof Error ? reason.message : "无法读取赛程选项");
    });
  }, [client, season.id]);

  const divisions = useMemo(() => {
    const seen = new Map<string, string>();
    games.forEach((game) => seen.set(game.division_id, game.division_name));
    return [...seen.entries()];
  }, [games]);
  const shown = games.filter((game) => {
    if (divisionId !== "all" && game.division_id !== divisionId) return false;
    if (adjustability === "adjustable" && !game.leader_adjustable) return false;
    if (adjustability === "locked" && game.leader_adjustable) return false;
    const normalized = query.trim().toLowerCase();
    if (!normalized) return true;
    return `${game.code} ${game.home_name} ${game.away_name} ${game.venue_name}`
      .toLowerCase()
      .includes(normalized);
  });

  const openGame = async (game: MobileAdminGame) => {
    setMessage(null);
    try {
      setSelected(await client.getAdminScheduleGame(game.id));
      setCancelRequest(false);
      setOverrideRules(false);
      setAcknowledged(false);
    } catch (reason: unknown) {
      setMessage(reason instanceof Error ? reason.message : "无法读取比赛");
    }
  };

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!selected || !acknowledged) return;
    if (!window.confirm("确认直接修改这场比赛？保存后会立即影响公开赛程并写入审计日志。")) return;
    setBusy(true);
    setMessage(null);
    try {
      const payload: UpdateMobileAdminGame = {
        expected_version: selected.version,
        date: selected.date,
        period_id: selected.period_id,
        venue_id: selected.venue_id,
        home_team_id: selected.home_team_id ?? null,
        away_team_id: selected.away_team_id ?? null,
        home_score: selected.home_score,
        away_score: selected.away_score,
        status: selected.status,
        leader_adjustable: selected.leader_adjustable,
        cancel_active_request: cancelRequest,
        override_rules: overrideRules,
        confirmed: true,
      };
      const updated = await client.updateAdminScheduleGame(selected.id, payload);
      setSelected(updated);
      setAcknowledged(false);
      setMessage("比赛已更新，公开赛程已刷新。");
      await onUpdated();
    } catch (reason: unknown) {
      setMessage(reason instanceof Error ? reason.message : "比赛修改失败");
    } finally {
      setBusy(false);
    }
  };

  const teams = options?.teams.filter((team) => team.division_id === selected?.division_id) ?? [];

  return (
    <section className="schedule-editor-layout">
      <div className="schedule-browser">
        <div className="operation-heading">
          <div><h2>选择比赛</h2><p>逐场查看并编辑“领队可调 / 不可调”，所有修改写入审计。</p></div>
          <strong>{shown.length} 场</strong>
        </div>
        <label className="editor-season-select">
          操作赛季
          <select value={season.id} onChange={(event) => onSeasonChange(event.target.value)}>
            {seasons.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
          </select>
        </label>
        <div className="schedule-editor-filters">
          <select value={divisionId} onChange={(event) => setDivisionId(event.target.value)}>
            <option value="all">全部组别</option>
            {divisions.map(([id, name]) => <option key={id} value={id}>{name}</option>)}
          </select>
          <select aria-label="按领队可调状态筛选" value={adjustability} onChange={(event) => setAdjustability(event.target.value)}>
            <option value="all">全部可调状态</option>
            <option value="adjustable">领队可调</option>
            <option value="locked">领队不可调</option>
          </select>
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="球队、场地或比赛代码" />
        </div>
        <div className="schedule-color-legend" aria-label="比赛颜色说明">
          <span className="game-men">男篮</span>
          <span className="game-women">女篮</span>
          <span className="game-locked">已锁定 · 领队不可调</span>
        </div>
        <div className="schedule-editor-list">
          {shown.map((game) => (
            <button
              className={scheduleGameVisualClass(game, selected?.id === game.id)}
              key={game.id}
              onClick={() => void openGame(game)}
              type="button"
            >
              <span><strong>{game.division_name}</strong>{game.date} · {game.start_time}</span>
              <span>{game.home_name} <b>{score(game)}</b> {game.away_name}</span>
              <small>{game.venue_name} · {game.code} <em className={game.leader_adjustable ? "adjustable-state yes" : "adjustable-state no"}>{game.leader_adjustable ? "领队可调" : "已锁定 · 领队不可调"}</em></small>
            </button>
          ))}
          {shown.length === 0 && <div className="operation-empty compact"><p>当前筛选下没有比赛。</p></div>}
        </div>
      </div>

      <div className="schedule-editor-detail">
        {!selected || !options ? (
          <div className="operation-empty"><h2>赛程编辑</h2><p>选择左侧比赛开始编辑。</p></div>
        ) : (
          <form onSubmit={(event) => void submit(event)}>
            <div className="operation-heading">
              <div><p className="eyebrow">{selected.division_name}</p><h2>{selected.home_name} vs {selected.away_name}</h2></div>
              <span className="version-mark">v{selected.version}</span>
            </div>
            {selected.active_reschedule_request_id && (
              <div className="operation-warning">本场存在活动调赛申请。修改前必须明确取消，并释放其预留。</div>
            )}
            <div className={selected.leader_adjustable ? "adjustability-banner adjustable" : "adjustability-banner locked"}>
              <strong>{selected.leader_adjustable ? "领队可调" : "已锁定 · 领队不可调"}</strong>
              <span>{selected.leader_adjustable ? "双方领队可按调赛流程提交申请。" : "领队端不会开放本场调赛申请入口。"}</span>
            </div>
            <div className="schedule-form-grid">
              <label>比赛日期<input type="date" value={selected.date} onChange={(event) => setSelected({ ...selected, date: event.target.value })} /></label>
              <label>开赛时段<select value={selected.period_id} onChange={(event) => setSelected({ ...selected, period_id: event.target.value })}>{options.periods.map((period) => <option key={period.id} value={period.id}>{period.start_time} · {period.name}</option>)}</select></label>
              <label>场地<select value={selected.venue_id} onChange={(event) => setSelected({ ...selected, venue_id: event.target.value })}>{options.venues.map((venue) => <option key={venue.id} value={venue.id}>{venue.name}</option>)}</select></label>
              <label>比赛状态<select value={selected.status} onChange={(event) => setSelected({ ...selected, status: event.target.value })}>{statuses.map((status) => <option key={status.value} value={status.value}>{status.label}</option>)}</select></label>
              <label>主队<select value={selected.home_team_id ?? ""} onChange={(event) => setSelected({ ...selected, home_team_id: event.target.value || null })}><option value="">保留签位 · {selected.home_name}</option>{teams.map((team) => <option key={team.id} value={team.id}>{team.name}</option>)}</select></label>
              <label>客队<select value={selected.away_team_id ?? ""} onChange={(event) => setSelected({ ...selected, away_team_id: event.target.value || null })}><option value="">保留签位 · {selected.away_name}</option>{teams.map((team) => <option key={team.id} value={team.id}>{team.name}</option>)}</select></label>
              <label>主队比分<input min="0" type="number" value={selected.home_score ?? ""} onChange={(event) => setSelected({ ...selected, home_score: numeric(event.target.value) })} /></label>
              <label>客队比分<input min="0" type="number" value={selected.away_score ?? ""} onChange={(event) => setSelected({ ...selected, away_score: numeric(event.target.value) })} /></label>
            </div>
            <div className="schedule-checks">
              <label><input type="checkbox" checked={selected.leader_adjustable} onChange={(event) => setSelected({ ...selected, leader_adjustable: event.target.checked })} />允许领队申请调赛（关闭后显示为“领队不可调”）</label>
              {selected.active_reschedule_request_id && <label><input type="checkbox" checked={cancelRequest} onChange={(event) => setCancelRequest(event.target.checked)} />取消活动申请并释放预留</label>}
              <label><input type="checkbox" checked={overrideRules} onChange={(event) => setOverrideRules(event.target.checked)} />使用超级管理员例外（容量与日期）</label>
              <label className="critical-check"><input type="checkbox" checked={acknowledged} onChange={(event) => setAcknowledged(event.target.checked)} />我已核对日期、时段、场地、双方、比分及关联申请</label>
            </div>
            <button className="primary-action" disabled={!acknowledged || busy} type="submit">{busy ? "正在保存…" : "二次确认并保存"}</button>
          </form>
        )}
        {message && <p className="operation-message" role="status">{message}</p>}
      </div>
    </section>
  );
}

function score(game: MobileAdminGame) {
  return game.home_score === null || game.away_score === null ? "vs" : `${game.home_score}:${game.away_score}`;
}

function numeric(value: string) {
  return value === "" ? null : Number(value);
}

export function scheduleGameVisualClass(game: MobileAdminGame, active = false) {
  return [
    "schedule-editor-game",
    game.division_gender === "WOMEN" ? "game-women" : "game-men",
    game.leader_adjustable ? "game-adjustable" : "game-locked",
    active ? "active" : "",
  ].filter(Boolean).join(" ");
}
