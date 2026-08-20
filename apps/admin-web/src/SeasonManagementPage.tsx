import { type CSSProperties, type FormEvent, useEffect, useMemo, useState } from "react";
import type {
  AdminSeason,
  CreateSeason,
  SeasonConfiguration,
  UpdateSeasonConfiguration,
} from "@pkuba/api-client";

import "./season-management.css";

type AdminClient = ReturnType<typeof import("@pkuba/api-client").createAdminClient>;

type DivisionDraft = SeasonConfiguration["divisions"][number] & { key: string };
type VenueDraft = SeasonConfiguration["venues"][number] & { key: string };
type PeriodDraft = SeasonConfiguration["periods"][number] & { key: string };
type ConfigurationDraft = Omit<
  SeasonConfiguration,
  "divisions" | "venues" | "periods"
> & {
  divisions: DivisionDraft[];
  venues: VenueDraft[];
  periods: PeriodDraft[];
};

const weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"];
const statusLabels: Record<string, string> = {
  SETUP: "准备中",
  PRE_DRAW_PUBLIC: "抽签前公开",
  ACTIVE: "进行中",
  ARCHIVED: "已归档",
};

let localKey = 0;
const nextKey = (prefix: string) => `${prefix}-new-${++localKey}`;

function nextSortOrder(rows: Array<{ sort_order: number }>) {
  return Math.max(0, ...rows.map((row) => row.sort_order)) + 1;
}

function nextCode(rows: Array<{ code: string }>, prefix: string) {
  const used = new Set(rows.map((row) => row.code.toLowerCase()));
  let index = rows.length + 1;
  while (used.has(`${prefix}${index}`)) index += 1;
  return `${prefix}${index}`;
}

function toDraft(configuration: SeasonConfiguration): ConfigurationDraft {
  return {
    ...configuration,
    divisions: configuration.divisions.map((row) => ({ ...row, key: row.id })),
    venues: configuration.venues.map((row) => ({ ...row, key: row.id })),
    periods: configuration.periods.map((row) => ({
      ...row,
      capacities: [...row.capacities],
      key: row.id,
    })),
  };
}

function toUpdatePayload(draft: ConfigurationDraft): UpdateSeasonConfiguration {
  return {
    expected_version: draft.version,
    name: draft.name,
    competition_type: draft.competition_type,
    year: draft.year,
    starts_on: draft.starts_on,
    ends_on: draft.ends_on,
    divisions: draft.divisions.map(({ key: _key, team_count: _teams, group_count: _groups, game_count: _games, ...row }) => ({
      id: row.id || null,
      code: row.code,
      name: row.name,
      gender: row.gender,
      sort_order: row.sort_order,
    })),
    venues: draft.venues.map(({ key: _key, game_count: _games, active_reservation_count: _reservations, ...row }) => ({
      id: row.id || null,
      code: row.code,
      name: row.name,
      sort_order: row.sort_order,
      active: row.active,
    })),
    periods: draft.periods.map(({ key: _key, game_count: _games, active_reservation_count: _reservations, ...row }) => ({
      id: row.id || null,
      code: row.code,
      name: row.name,
      start_time: row.start_time,
      sort_order: row.sort_order,
      capacities: row.capacities,
    })),
  };
}

export function SeasonManagementPage({
  client,
  seasons,
  seasonId,
  onSeasonChange,
  onDataChanged,
}: {
  client: AdminClient;
  seasons: AdminSeason[];
  seasonId: string;
  onSeasonChange: (seasonId: string) => void;
  onDataChanged: (preferredSeasonId?: string) => Promise<void>;
}) {
  const [configuration, setConfiguration] = useState<SeasonConfiguration | null>(null);
  const [draft, setDraft] = useState<ConfigurationDraft | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [messageTone, setMessageTone] = useState<"success" | "error">("success");
  const [showCreate, setShowCreate] = useState(false);

  const loadConfiguration = async (id = seasonId) => {
    if (!id) {
      setConfiguration(null);
      setDraft(null);
      setLoading(false);
      return;
    }
    setLoading(true);
    setMessage(null);
    try {
      const next = await client.getSeasonConfiguration(id);
      setConfiguration(next);
      setDraft(toDraft(next));
      setDirty(false);
    } catch (reason: unknown) {
      setMessageTone("error");
      setMessage(reason instanceof Error ? reason.message : "无法读取赛季配置");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadConfiguration();
  }, [seasonId]);

  const activeVenueCount = draft?.venues.filter((row) => row.active).length ?? 0;
  const weeklyCapacity = useMemo(
    () => draft?.periods.reduce(
      (sum, period) => sum + period.capacities.reduce((rowSum, value) => rowSum + value, 0),
      0,
    ) ?? 0,
    [draft?.periods],
  );

  const markChanged = (next: ConfigurationDraft) => {
    setDraft(next);
    setDirty(true);
    setMessage(null);
  };

  const updateDivision = (key: string, patch: Partial<DivisionDraft>) => {
    if (!draft) return;
    markChanged({
      ...draft,
      divisions: draft.divisions.map((row) => row.key === key ? { ...row, ...patch } : row),
    });
  };

  const updateVenue = (key: string, patch: Partial<VenueDraft>) => {
    if (!draft) return;
    markChanged({
      ...draft,
      venues: draft.venues.map((row) => row.key === key ? { ...row, ...patch } : row),
    });
  };

  const updatePeriod = (key: string, patch: Partial<PeriodDraft>) => {
    if (!draft) return;
    markChanged({
      ...draft,
      periods: draft.periods.map((row) => row.key === key ? { ...row, ...patch } : row),
    });
  };

  const submit = async () => {
    if (!draft || !draft.editable || !dirty) return;
    if (!window.confirm("确认保存赛季基础配置？组别、场地、时段和容量将作为一个事务同时更新。")) {
      return;
    }
    setBusy(true);
    setMessage(null);
    try {
      const updated = await client.updateSeasonConfiguration(
        draft.id,
        toUpdatePayload(draft),
      );
      setConfiguration(updated);
      setDraft(toDraft(updated));
      setDirty(false);
      setMessageTone("success");
      setMessage("赛季配置已保存，并写入审计日志。");
      await onDataChanged(updated.id);
    } catch (reason: unknown) {
      setMessageTone("error");
      setMessage(reason instanceof Error ? reason.message : "赛季配置保存失败");
    } finally {
      setBusy(false);
    }
  };

  if (loading) {
    return <section className="season-management-state">正在读取赛季配置…</section>;
  }

  if (!draft) {
    return (
      <section className="season-management-state">
        <h2>尚无赛季</h2>
        <p>先创建一个准备中的赛季，再配置组别、场地、时段与容量。</p>
        <button className="primary-action" type="button" onClick={() => setShowCreate(true)}>
          新建赛季
        </button>
        {showCreate && (
          <CreateSeasonDialog
            client={client}
            seasons={seasons}
            sourceSeasonId={seasonId}
            onClose={() => setShowCreate(false)}
            onCreated={async (created) => {
              setShowCreate(false);
              await onDataChanged(created.id);
              onSeasonChange(created.id);
            }}
          />
        )}
      </section>
    );
  }

  const editable = draft.editable;
  return (
    <div className="season-management">
      <div className="season-management-toolbar">
        <label>
          管理赛季
          <select
            value={seasonId}
            onChange={(event) => {
              if (dirty && !window.confirm("当前修改尚未保存，确认切换赛季？")) return;
              onSeasonChange(event.target.value);
            }}
          >
            {seasons.map((season) => (
              <option key={season.id} value={season.id}>
                {season.year} · {season.name} · {statusLabels[season.status] ?? season.status}
              </option>
            ))}
          </select>
        </label>
        <div>
          <button className="secondary-action" type="button" onClick={() => void loadConfiguration()}>
            重新读取
          </button>
          <button className="primary-action" type="button" onClick={() => setShowCreate(true)}>
            新建赛季
          </button>
        </div>
      </div>

      <section className="season-facts" aria-label="赛季配置摘要">
        <div><span>状态</span><strong>{statusLabels[draft.status] ?? draft.status}</strong></div>
        <div><span>组别</span><strong>{draft.divisions.length}</strong></div>
        <div><span>启用场地</span><strong>{activeVenueCount}</strong></div>
        <div><span>比赛时段</span><strong>{draft.periods.length}</strong></div>
        <div><span>每周标准容量</span><strong>{weeklyCapacity} 场</strong></div>
      </section>

      {!editable && (
        <div className="season-lock-notice" role="note">
          <strong>基础配置已锁定</strong>
          <span>{draft.locked_reason}</span>
        </div>
      )}

      <section className="season-config-section">
        <div className="season-section-heading">
          <div><p className="eyebrow">SEASON</p><h2>赛季信息</h2></div>
          <span>版本 v{draft.version}</span>
        </div>
        <div className="season-core-grid">
          <label className="season-name-field">赛季名称<input disabled={!editable} value={draft.name} onChange={(event) => markChanged({ ...draft, name: event.target.value })} /></label>
          <label>赛事类型<select disabled={!editable} value={draft.competition_type} onChange={(event) => markChanged({ ...draft, competition_type: event.target.value })}><option value="PKU_CUP">北大杯</option><option value="FRESHMAN_CUP">新生杯</option></select></label>
          <label>赛季年份<input disabled={!editable} min="2000" max="2100" type="number" value={draft.year} onChange={(event) => markChanged({ ...draft, year: Number(event.target.value) })} /></label>
          <label>开始日期<input disabled={!editable} type="date" value={draft.starts_on} onChange={(event) => markChanged({ ...draft, starts_on: event.target.value })} /></label>
          <label>结束日期<input disabled={!editable} type="date" value={draft.ends_on} onChange={(event) => markChanged({ ...draft, ends_on: event.target.value })} /></label>
          <label>时区<input disabled value={draft.timezone} /></label>
        </div>
      </section>

      <section className="season-config-section">
        <div className="season-section-heading">
          <div><p className="eyebrow">DIVISIONS</p><h2>赛事组别</h2><p>组别决定球队、签位和赛程的所属范围。</p></div>
          {editable && <button className="text-action" type="button" onClick={() => markChanged({ ...draft, divisions: [...draft.divisions, { id: "", key: nextKey("division"), code: nextCode(draft.divisions, "division-"), name: "新组别", gender: "MEN", sort_order: nextSortOrder(draft.divisions), team_count: 0, group_count: 0, game_count: 0 }] })}>＋ 添加组别</button>}
        </div>
        <div className="division-config-table">
          <div className="division-config-row division-config-head"><span>顺序</span><span>代码</span><span>显示名称</span><span>分类</span><span>已关联</span><span /></div>
          {draft.divisions.map((row) => {
            const referenced = row.team_count + row.group_count + row.game_count > 0;
            return <div className="division-config-row" key={row.key}>
              <input aria-label={`${row.name}顺序`} disabled={!editable} min="0" type="number" value={row.sort_order} onChange={(event) => updateDivision(row.key, { sort_order: Number(event.target.value) })} />
              <input aria-label={`${row.name}代码`} disabled={!editable} value={row.code} onChange={(event) => updateDivision(row.key, { code: event.target.value })} />
              <input aria-label={`${row.name}显示名称`} disabled={!editable} value={row.name} onChange={(event) => updateDivision(row.key, { name: event.target.value })} />
              <select aria-label={`${row.name}分类`} disabled={!editable} value={row.gender} onChange={(event) => updateDivision(row.key, { gender: event.target.value })}><option value="MEN">男子</option><option value="WOMEN">女子</option></select>
              <span className="resource-usage">{row.team_count} 队 · {row.group_count} 组 · {row.game_count} 场</span>
              <button aria-label={`删除${row.name}`} className="row-remove" disabled={!editable || referenced} title={referenced ? "已有引用，不能删除" : "删除组别"} type="button" onClick={() => markChanged({ ...draft, divisions: draft.divisions.filter((item) => item.key !== row.key) })}>×</button>
            </div>;
          })}
        </div>
      </section>

      <section className="season-config-section">
        <div className="season-section-heading">
          <div><p className="eyebrow">VENUES</p><h2>场地</h2><p>容量只统计启用场地，排序同时决定调赛时的自动分配顺序。</p></div>
          {editable && <button className="text-action" type="button" onClick={() => markChanged({ ...draft, venues: [...draft.venues, { id: "", key: nextKey("venue"), code: nextCode(draft.venues, "venue-"), name: "新场地", sort_order: nextSortOrder(draft.venues), active: true, game_count: 0, active_reservation_count: 0 }] })}>＋ 添加场地</button>}
        </div>
        <div className="venue-config-table">
          <div className="venue-config-row venue-config-head"><span>顺序</span><span>代码</span><span>场地名称</span><span>状态</span><span>已关联</span><span /></div>
          {draft.venues.map((row) => {
            const referenced = row.game_count + row.active_reservation_count > 0;
            return <div className="venue-config-row" key={row.key}>
              <input aria-label={`${row.name}顺序`} disabled={!editable} min="0" type="number" value={row.sort_order} onChange={(event) => updateVenue(row.key, { sort_order: Number(event.target.value) })} />
              <input aria-label={`${row.name}代码`} disabled={!editable} value={row.code} onChange={(event) => updateVenue(row.key, { code: event.target.value })} />
              <input aria-label={`${row.name}名称`} disabled={!editable} value={row.name} onChange={(event) => updateVenue(row.key, { name: event.target.value })} />
              <label className="venue-active-toggle"><input checked={row.active} disabled={!editable || (referenced && row.active)} type="checkbox" onChange={(event) => updateVenue(row.key, { active: event.target.checked })} /><span>{row.active ? "启用" : "停用"}</span></label>
              <span className="resource-usage">{row.game_count} 场 · {row.active_reservation_count} 个预留</span>
              <button aria-label={`删除${row.name}`} className="row-remove" disabled={!editable || referenced} title={referenced ? "已有引用，不能删除" : "删除场地"} type="button" onClick={() => markChanged({ ...draft, venues: draft.venues.filter((item) => item.key !== row.key) })}>×</button>
            </div>;
          })}
        </div>
      </section>

      <section className="season-config-section">
        <div className="season-section-heading">
          <div><p className="eyebrow">PERIODS</p><h2>比赛时段</h2><p>开赛时间和排序在整个赛季中保持稳定。</p></div>
          {editable && <button className="text-action" type="button" onClick={() => { const sortOrder = nextSortOrder(draft.periods); markChanged({ ...draft, periods: [...draft.periods, { id: "", key: nextKey("period"), code: nextCode(draft.periods, "p"), name: `第${sortOrder}时段`, start_time: "12:00", sort_order: sortOrder, capacities: [0, 0, 0, 0, 0, 0, 0], game_count: 0, active_reservation_count: 0 }] }); }}>＋ 添加时段</button>}
        </div>
        <div className="period-config-table">
          <div className="period-config-row period-config-head"><span>顺序</span><span>代码</span><span>显示名称</span><span>开赛时间</span><span>已关联</span><span /></div>
          {draft.periods.map((row) => {
            const referenced = row.game_count + row.active_reservation_count > 0;
            return <div className="period-config-row" key={row.key}>
              <input aria-label={`${row.name}顺序`} disabled={!editable} min="0" type="number" value={row.sort_order} onChange={(event) => updatePeriod(row.key, { sort_order: Number(event.target.value) })} />
              <input aria-label={`${row.name}代码`} disabled={!editable} value={row.code} onChange={(event) => updatePeriod(row.key, { code: event.target.value })} />
              <input aria-label={`${row.name}显示名称`} disabled={!editable} value={row.name} onChange={(event) => updatePeriod(row.key, { name: event.target.value })} />
              <input aria-label={`${row.name}开赛时间`} disabled={!editable} type="time" value={row.start_time.slice(0, 5)} onChange={(event) => updatePeriod(row.key, { start_time: event.target.value })} />
              <span className="resource-usage">{row.game_count} 场 · {row.active_reservation_count} 个预留</span>
              <button aria-label={`删除${row.name}`} className="row-remove" disabled={!editable || referenced} title={referenced ? "已有引用，不能删除" : "删除时段"} type="button" onClick={() => markChanged({ ...draft, periods: draft.periods.filter((item) => item.key !== row.key) })}>×</button>
            </div>;
          })}
        </div>
      </section>

      <section className="season-config-section capacity-section">
        <div className="season-section-heading">
          <div><p className="eyebrow">CAPACITY</p><h2>每周容量</h2><p>填写每个星期、每个时段允许安排的比赛数；0 表示普通流程不可用。</p></div>
          <span>上限 {activeVenueCount} 场 / 时段</span>
        </div>
        <div className="capacity-matrix-wrap">
          <div className="capacity-matrix" style={{ "--period-count": draft.periods.length } as CSSProperties}>
            <div className="capacity-grid-row capacity-header-row">
              <div className="capacity-corner">星期</div>
              {draft.periods.map((period) => <div className="capacity-period-head" key={period.key}><strong>{period.start_time.slice(0, 5)}</strong><span>{period.name}</span></div>)}
            </div>
            {weekdays.map((weekday, weekdayIndex) => (
              <div className="capacity-grid-row" key={weekday}>
                <div className={`capacity-row-label ${weekdayIndex >= 5 ? "weekend" : ""}`}>{weekday}</div>
                {draft.periods.map((period) => {
                  const value = period.capacities[weekdayIndex] ?? 0;
                  return <label className={`capacity-cell ${value === 0 ? "zero" : value === activeVenueCount ? "full" : "open"}`} key={`${weekday}-${period.key}`}><input aria-label={`${weekday}${period.name}容量`} disabled={!editable} max={activeVenueCount} min="0" type="number" value={value} onChange={(event) => { const capacities = [...period.capacities]; capacities[weekdayIndex] = Number(event.target.value); updatePeriod(period.key, { capacities }); }} /><span>场</span></label>;
                })}
              </div>
            ))}
          </div>
        </div>
      </section>

      {editable && (
        <div className={`season-save-bar ${dirty ? "dirty" : ""}`}>
          <div><strong>{dirty ? "有尚未保存的修改" : "配置已与服务器同步"}</strong><span>保存时服务端会再次检查版本、引用、已有比赛和有效预留。</span></div>
          {dirty && <button className="text-action" type="button" onClick={() => configuration && (setDraft(toDraft(configuration)), setDirty(false), setMessage(null))}>撤销本次修改</button>}
          <button className="primary-action" disabled={!dirty || busy} type="button" onClick={() => void submit()}>{busy ? "正在保存…" : "保存全部配置"}</button>
        </div>
      )}
      {message && <p className={`season-management-message ${messageTone}`} role="status">{message}</p>}

      {showCreate && (
        <CreateSeasonDialog
          client={client}
          seasons={seasons}
          sourceSeasonId={seasonId}
          onClose={() => setShowCreate(false)}
          onCreated={async (created) => {
            setShowCreate(false);
            await onDataChanged(created.id);
            onSeasonChange(created.id);
          }}
        />
      )}
    </div>
  );
}

function CreateSeasonDialog({
  client,
  seasons,
  sourceSeasonId,
  onClose,
  onCreated,
}: {
  client: AdminClient;
  seasons: AdminSeason[];
  sourceSeasonId: string;
  onClose: () => void;
  onCreated: (created: SeasonConfiguration) => Promise<void>;
}) {
  const defaultYear = Math.max(new Date().getFullYear(), ...seasons.map((item) => item.year)) + 1;
  const [form, setForm] = useState<CreateSeason>({
    name: `${defaultYear} 年北大杯`,
    competition_type: "PKU_CUP",
    year: defaultYear,
    starts_on: `${defaultYear}-03-01`,
    ends_on: `${defaultYear}-05-31`,
    template_season_id: sourceSeasonId || null,
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await onCreated(await client.createAdminSeason(form));
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "赛季创建失败");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="dialog-backdrop" role="presentation" onMouseDown={onClose}>
      <section className="season-create-dialog" role="dialog" aria-modal="true" aria-labelledby="create-season-title" onMouseDown={(event) => event.stopPropagation()}>
        <div className="dialog-heading"><div><p className="eyebrow">NEW SEASON</p><h2 id="create-season-title">新建准备赛季</h2></div><button className="dialog-close" type="button" onClick={onClose} aria-label="关闭">×</button></div>
        <p className="dialog-detail">新赛季只创建组别和排期元信息，不复制球队、赛程、身份或比分。</p>
        <form className="season-create-form" onSubmit={(event) => void submit(event)}>
          <label className="wide">赛季名称<input autoFocus required value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} /></label>
          <label>赛事类型<select value={form.competition_type} onChange={(event) => setForm({ ...form, competition_type: event.target.value })}><option value="PKU_CUP">北大杯</option><option value="FRESHMAN_CUP">新生杯</option></select></label>
          <label>赛季年份<input min="2000" max="2100" required type="number" value={form.year} onChange={(event) => setForm({ ...form, year: Number(event.target.value) })} /></label>
          <label>开始日期<input required type="date" value={form.starts_on} onChange={(event) => setForm({ ...form, starts_on: event.target.value })} /></label>
          <label>结束日期<input required type="date" value={form.ends_on} onChange={(event) => setForm({ ...form, ends_on: event.target.value })} /></label>
          <label className="wide">配置来源<select value={form.template_season_id ?? ""} onChange={(event) => setForm({ ...form, template_season_id: event.target.value || null })}><option value="">系统标准配置</option>{seasons.map((season) => <option key={season.id} value={season.id}>沿用 {season.year} · {season.name} 的组别、场地、时段与容量</option>)}</select></label>
          <p className="season-create-note wide">从历史赛季沿用配置时，仅复制启用场地和容量基线；历史比赛、球队和个人数据不会进入新赛季。</p>
          {error && <p className="dialog-error wide" role="alert">{error}</p>}
          <div className="dialog-actions wide"><button className="secondary-action" type="button" onClick={onClose}>取消</button><button className="primary-action" disabled={busy} type="submit">{busy ? "正在创建…" : "创建并开始配置"}</button></div>
        </form>
      </section>
    </div>
  );
}
