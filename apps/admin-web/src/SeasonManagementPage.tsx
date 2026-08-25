import { type DragEvent, type FormEvent, useEffect, useMemo, useRef, useState } from "react";
import type {
  AdminSeason,
  CapacityLedgerRow,
  CreateSeason,
  PreviewSeasonConfiguration,
  SeasonConfiguration,
  UpdateSeasonConfiguration,
} from "@pkuba/api-client";

import { CapacityCalendar } from "./CapacityCalendar";
import { useAdminDirtySource } from "./dirtyGuard";
import { buildSeasonConfigurationPayload } from "./season-configuration-payload";
import { SeasonLifecyclePanel } from "./SeasonLifecyclePanel";
import "./season-management.css";

type AdminClient = ReturnType<typeof import("@pkuba/api-client").createAdminClient>;
type DivisionDraft = SeasonConfiguration["divisions"][number] & { key: string };
type VenueDraft = SeasonConfiguration["venues"][number] & { key: string };
type PeriodDraft = SeasonConfiguration["periods"][number] & { key: string };
type SlotFamilyDraft = SeasonConfiguration["slot_families"][number] & { key: string };
type GridColumnDraft = SeasonConfiguration["grid_columns"][number] & { key: string };
type OverrideDraft = SeasonConfiguration["date_capacity_overrides"][number] & { key: string };
type OrderedCollection = "divisions" | "slot_families" | "venues" | "periods";
type ConfigurationDraft = Omit<
  SeasonConfiguration,
  | "divisions"
  | "venues"
  | "periods"
  | "slot_families"
  | "grid_columns"
  | "date_capacity_overrides"
> & {
  divisions: DivisionDraft[];
  venues: VenueDraft[];
  periods: PeriodDraft[];
  slot_families: SlotFamilyDraft[];
  grid_columns: GridColumnDraft[];
  date_capacity_overrides: OverrideDraft[];
};

const statusLabels: Record<string, string> = {
  SETUP: "准备中",
  PUBLISHED: "已公开",
  ARCHIVED: "已归档",
};
const dayTypeLabels: Record<string, string> = { WEEKDAY: "周中", WEEKEND: "周末" };
const stageOptions = [
  ["GROUP", "小组赛"],
  ["ROUND_ROBIN", "循环赛"],
  ["KNOCKOUT", "淘汰赛"],
  ["SEMIFINAL", "半决赛"],
  ["FINAL", "决赛"],
  ["RELEGATION", "保级赛"],
] as const;
const slotPrefixes = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz".split("");

let localKey = 0;
const nextKey = (prefix: string) => `${prefix}-new-${++localKey}`;

function nextSortOrder(rows: Array<{ sort_order: number }>) {
  return Math.max(0, ...rows.map((row) => row.sort_order)) + 1;
}

function continuousOrder<T extends { sort_order: number }>(rows: T[]): T[] {
  return rows.map((row, index) => ({ ...row, sort_order: index + 1 }));
}

function moveOrderedRow<T extends { key: string; sort_order: number }>(
  rows: T[],
  sourceKey: string,
  targetKey: string,
): T[] {
  const sourceIndex = rows.findIndex((row) => row.key === sourceKey);
  const targetIndex = rows.findIndex((row) => row.key === targetKey);
  if (sourceIndex < 0 || targetIndex < 0 || sourceIndex === targetIndex) return rows;
  const next = [...rows];
  const [moved] = next.splice(sourceIndex, 1);
  next.splice(targetIndex, 0, moved);
  return continuousOrder(next);
}

function nextVenueName(rows: Array<{ name: string }>) {
  const used = new Set(rows.map((row) => row.name.trim()));
  let index = rows.length + 1;
  while (used.has(`新场地 ${index}`)) index += 1;
  return `新场地 ${index}`;
}

function nextSlotPrefix(
  rows: Array<{ division_id: string; prefix: string }>,
  divisions: Array<{ id: string; gender: string }>,
  divisionId: string,
) {
  const gender = divisions.find((row) => row.id === divisionId)?.gender;
  const divisionGender = new Map(divisions.map((row) => [row.id, row.gender]));
  const used = new Set(
    rows
      .filter((row) => divisionGender.get(row.division_id) === gender)
      .map((row) => row.prefix),
  );
  return slotPrefixes.find((prefix) => !used.has(prefix)) ?? "";
}

function expectedFamilyGames(stage: string, slotCount: number) {
  return stage === "GROUP" || stage === "ROUND_ROBIN"
    ? slotCount * Math.max(0, slotCount - 1) / 2
    : Math.floor(slotCount / 2);
}

function toDraft(configuration: SeasonConfiguration): ConfigurationDraft {
  return {
    ...configuration,
    divisions: continuousOrder([...configuration.divisions]
      .sort((left, right) => left.sort_order - right.sort_order)
      .map((row) => ({ ...row, key: row.id }))),
    venues: continuousOrder([...configuration.venues]
      .sort((left, right) => left.sort_order - right.sort_order)
      .map((row) => ({ ...row, key: row.id }))),
    periods: continuousOrder([...configuration.periods]
      .sort((left, right) => left.sort_order - right.sort_order)
      .map((row) => ({
      ...row,
      default_capacities: { ...row.default_capacities },
      key: row.id,
    }))),
    slot_families: continuousOrder([...configuration.slot_families]
      .sort((left, right) => left.sort_order - right.sort_order)
      .map((row) => ({ ...row, key: row.id }))),
    grid_columns: configuration.grid_columns.map((row) => ({ ...row, key: row.id })),
    date_capacity_overrides: configuration.date_capacity_overrides.map((row) => ({
      ...row,
      key: row.id,
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
  const [ledger, setLedger] = useState<CapacityLedgerRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [dirty, setDirty] = useState(false);
  useAdminDirtySource(`season-management:${seasonId}`, dirty);
  const [message, setMessage] = useState<string | null>(null);
  const [messageTone, setMessageTone] = useState<"success" | "error">("success");
  const [showCreate, setShowCreate] = useState(false);
  const [dragging, setDragging] = useState<{ collection: OrderedCollection; key: string } | null>(null);
  const loadGeneration = useRef(0);

  const loadConfiguration = async (id = seasonId) => {
    const generation = ++loadGeneration.current;
    if (!id) {
      setConfiguration(null);
      setDraft(null);
      setLedger([]);
      setLoading(false);
      return;
    }
    setLoading(true);
    setMessage(null);
    try {
      const [next, ledgerRows] = await Promise.all([
        client.getSeasonConfiguration(id),
        client.getCapacityLedger(id),
      ]);
      if (generation !== loadGeneration.current) return;
      setConfiguration(next);
      setDraft(toDraft(next));
      setLedger(ledgerRows);
      setDirty(false);
    } catch (reason: unknown) {
      if (generation !== loadGeneration.current) return;
      setMessageTone("error");
      setMessage(reason instanceof Error ? reason.message : "无法读取赛季配置");
    } finally {
      if (generation === loadGeneration.current) setLoading(false);
    }
  };

  useEffect(() => {
    void loadConfiguration();
    return () => {
      loadGeneration.current += 1;
    };
  }, [seasonId]);

  const activeVenueCount = draft?.venues.filter((row) => row.active).length ?? 0;
  const weeklyCapacity = useMemo(
    () => draft?.periods.reduce(
      (sum, period) => sum
        + (period.default_capacities.WEEKDAY ?? 0) * 5
        + (period.default_capacities.WEEKEND ?? 0) * 2,
      0,
    ) ?? 0,
    [draft?.periods],
  );
  const plannedGameCount = useMemo(
    () => draft?.slot_families.reduce(
      (sum, row) => sum + expectedFamilyGames(row.stage, row.slot_count),
      0,
    ) ?? 0,
    [draft?.slot_families],
  );
  const editable = draft?.editable ?? false;

  const markChanged = (next: ConfigurationDraft) => {
    setDraft(next);
    setDirty(true);
    setMessage(null);
  };
  const reorderCollection = (
    collection: OrderedCollection,
    sourceKey: string,
    targetKey: string,
  ) => {
    if (!draft) return;
    const nextRows = moveOrderedRow(
      draft[collection] as Array<{ key: string; sort_order: number }>,
      sourceKey,
      targetKey,
    );
    markChanged({ ...draft, [collection]: nextRows } as ConfigurationDraft);
  };
  const sortableRow = (
    collection: OrderedCollection,
    key: string,
  ) => ({
    onDragOver: (event: DragEvent<HTMLElement>) => {
      if (editable && dragging?.collection === collection) event.preventDefault();
    },
    onDrop: (event: DragEvent<HTMLElement>) => {
      event.preventDefault();
      if (dragging?.collection === collection) {
        reorderCollection(collection, dragging.key, key);
      }
      setDragging(null);
    },
  });
  const renderOrderHandle = (
    collection: OrderedCollection,
    key: string,
    label: string,
  ) => (
    <button
      aria-label={`拖动${label}排序`}
      className="row-drag-handle"
      disabled={!editable}
      draggable={editable}
      title="拖动排序"
      type="button"
      onDragEnd={() => setDragging(null)}
      onDragStart={(event) => {
        event.dataTransfer.effectAllowed = "move";
        event.dataTransfer.setData("text/plain", key);
        setDragging({ collection, key });
      }}
    >⋮</button>
  );
  const updateDivision = (key: string, patch: Partial<DivisionDraft>) => {
    if (!draft) return;
    markChanged({ ...draft, divisions: draft.divisions.map((row) => row.key === key ? { ...row, ...patch } : row) });
  };
  const updateVenue = (key: string, patch: Partial<VenueDraft>) => {
    if (!draft) return;
    markChanged({
      ...draft,
      venues: draft.venues.map((row) => row.key === key ? { ...row, ...patch } : row),
    });
  };
  const addVenue = () => {
    if (!draft) return;
    markChanged({
      ...draft,
      venues: [...draft.venues, {
        id: "",
        key: nextKey("venue"),
        name: nextVenueName(draft.venues),
        sort_order: nextSortOrder(draft.venues),
        active: true,
        game_count: 0,
      }],
    });
  };
  const removeVenue = (key: string) => {
    if (!draft || draft.venues.length <= 1) return;
    markChanged({
      ...draft,
      venues: draft.venues.filter((row) => row.key !== key),
    });
  };
  const updatePeriod = (key: string, patch: Partial<PeriodDraft>) => {
    if (!draft) return;
    markChanged({ ...draft, periods: draft.periods.map((row) => row.key === key ? { ...row, ...patch } : row) });
  };
  const updateSlotFamily = (key: string, patch: Partial<SlotFamilyDraft>) => {
    if (!draft) return;
    markChanged({
      ...draft,
      slot_families: draft.slot_families.map((row) => row.key === key
        ? {
          ...row,
          ...patch,
          expected_game_count: expectedFamilyGames(
            patch.stage ?? row.stage,
            patch.slot_count ?? row.slot_count,
          ),
        }
        : row),
    });
  };
  const addSlotFamily = () => {
    if (!draft) return;
    const division = draft.divisions.find((row) => row.id);
    if (!division) {
      setMessageTone("error");
      setMessage("请先保存至少一个赛事组别，再添加签位方案。");
      return;
    }
    const prefix = nextSlotPrefix(draft.slot_families, draft.divisions, division.id);
    if (!prefix) {
      setMessageTone("error");
      setMessage("该性别的 52 个大小写字母已全部使用，请先整理签位方案。");
      return;
    }
    const slotCount = Math.max(2, division.team_count);
    markChanged({
      ...draft,
      slot_families: [...draft.slot_families, {
        id: "",
        key: nextKey("slot-family"),
        division_id: division.id,
        division_code: division.code,
        division_name: division.name,
        gender: division.gender,
        stage: "GROUP",
        stage_name: "小组赛",
        round_number: 1,
        prefix,
        slot_count: slotCount,
        sort_order: nextSortOrder(draft.slot_families),
        expected_game_count: expectedFamilyGames("GROUP", slotCount),
      }],
    });
  };
  const updateOverride = (key: string, patch: Partial<OverrideDraft>) => {
    if (!draft) return;
    markChanged({
      ...draft,
      date_capacity_overrides: draft.date_capacity_overrides.map((row) => row.key === key ? { ...row, ...patch } : row),
    });
  };

  const submit = async () => {
    if (!draft || !draft.editable || !dirty) return;
    setBusy(true);
    setMessage(null);
    try {
      const preview = await client.previewSeasonConfiguration(
        draft.id,
        buildSeasonConfigurationPayload(draft) as PreviewSeasonConfiguration,
      );
      const warnings = [
        preview.maintenance_required ? "当前不是准备期，将以维护模式修改。" : "",
        preview.over_capacity.length ? `保存后有 ${preview.over_capacity.length} 个时段处于超容状态。` : "",
        preview.affected_reschedule_request_ids.length
          ? `将明确取消 ${preview.affected_reschedule_request_ids.length} 个受影响的调赛申请。`
          : "",
      ].filter(Boolean);
      if (!window.confirm(["确认保存全部赛季配置？", ...warnings, "操作会记录完整前后快照。"].join("\n"))) return;
      const updated = await client.updateSeasonConfiguration(draft.id, {
        ...buildSeasonConfigurationPayload(draft),
        maintenance_confirmed: preview.maintenance_required,
        impact_hash: preview.impact_hash,
        cancel_reschedule_request_ids: preview.affected_reschedule_request_ids,
      } as UpdateSeasonConfiguration);
      setConfiguration(updated);
      setDraft(toDraft(updated));
      setDirty(false);
      setMessageTone("success");
      setMessage("赛季配置已保存，容量台账已重新计算。");
      setLedger(await client.getCapacityLedger(updated.id));
      await onDataChanged(updated.id);
    } catch (reason: unknown) {
      setMessageTone("error");
      setMessage(reason instanceof Error ? reason.message : "赛季配置保存失败");
    } finally {
      setBusy(false);
    }
  };

  if (loading) return <section className="season-management-state">正在读取赛季配置…</section>;
  if (!draft) {
    return <section className="season-management-state">
      <h2>尚无赛季</h2>
      <p>先创建赛季，再配置组别、标准场地、时段与容量。</p>
      <button className="primary-action" type="button" onClick={() => setShowCreate(true)}>新建赛季</button>
      {showCreate && <CreateSeasonDialog client={client} seasons={seasons} onClose={() => setShowCreate(false)} onCreated={async (created) => { setShowCreate(false); await onDataChanged(created.id); onSeasonChange(created.id); }} />}
    </section>;
  }

  return <div className="season-management">
    <div className="season-management-toolbar">
      <label>管理赛季<select value={seasonId} onChange={(event) => {
        if (dirty && !window.confirm("当前修改尚未保存，确认切换赛季？")) return;
        onSeasonChange(event.target.value);
      }}>{seasons.map((season) => <option key={season.id} value={season.id}>{season.year} · {season.name} · {statusLabels[season.status] ?? season.status}</option>)}</select></label>
      <div><button className="secondary-action" type="button" onClick={() => void loadConfiguration()}>重新读取</button><button className="primary-action" type="button" onClick={() => setShowCreate(true)}>新建赛季</button></div>
    </div>

    <section className="season-facts" aria-label="赛季配置摘要">
      <div><span>状态</span><strong>{statusLabels[draft.status] ?? draft.status}</strong></div>
      <div><span>组别</span><strong>{draft.divisions.length}</strong></div>
      <div><span>标准场地</span><strong>{activeVenueCount}</strong></div>
      <div><span>标准时段</span><strong>{draft.periods.length}</strong></div>
      <div><span>签位方案</span><strong>{draft.slot_families.length}</strong></div>
      <div><span>计划比赛</span><strong>{plannedGameCount} 场</strong></div>
      <div><span>每周容量</span><strong>{weeklyCapacity} 场</strong></div>
    </section>

    {!editable && <div className="season-lock-notice" role="note"><strong>只读</strong><span>{draft.locked_reason}</span></div>}

    {configuration && (
      <SeasonLifecyclePanel
        client={client}
        configuration={configuration}
        dirty={dirty}
        onApplied={async () => {
          await onDataChanged(configuration.id);
          await loadConfiguration(configuration.id);
        }}
      />
    )}

    <section className="season-config-section">
      <div className="season-section-heading"><div><h2>赛季信息</h2></div></div>
      <div className="season-core-grid">
        <label className="season-name-field">赛季名称<input disabled={!editable} value={draft.name} onChange={(event) => markChanged({ ...draft, name: event.target.value })} /></label>
        <label>赛事类型<select disabled={!editable} value={draft.competition_type} onChange={(event) => markChanged({ ...draft, competition_type: event.target.value })}><option value="PKU_CUP">北大杯</option><option value="FRESHMAN_CUP">新生杯</option></select></label>
        <label>赛季年份<input disabled={!editable} min="1" step="1" type="number" value={draft.year} onChange={(event) => markChanged({ ...draft, year: Number(event.target.value) })} /></label>
        <label>计划开始日期<input disabled={!editable} type="date" value={draft.starts_on} onChange={(event) => markChanged({ ...draft, starts_on: event.target.value })} /><small>用于模板和日历的默认范围，不限制特殊日期比赛。</small></label>
        <label>计划结束日期<input disabled={!editable} type="date" value={draft.ends_on} onChange={(event) => markChanged({ ...draft, ends_on: event.target.value })} /><small>范围外比赛、预留和容量例外仍会保留。</small></label>
        <label className="season-timezone-field">时区<input disabled value={draft.timezone} /></label>
      </div>
    </section>

    <section className="season-config-section">
      <div className="season-section-heading"><div><h2>赛事组别</h2><p>球队、签位和比赛均归属于组别。</p></div>{editable && <button className="text-action" type="button" onClick={() => markChanged({ ...draft, divisions: [...draft.divisions, { id: "", key: nextKey("division"), code: "", name: "新组别", gender: "MEN", sort_order: nextSortOrder(draft.divisions), version: 1, team_count: 0, group_count: 0, game_count: 0 }] })}>＋ 添加组别</button>}</div>
      <div className="division-config-table">
        <div className="division-config-row division-config-head"><span>移动</span><span>名称</span><span>分类</span><span>已关联</span><span /></div>
        {draft.divisions.map((row) => {
          const referenced = row.team_count + row.group_count + row.game_count > 0;
          return <div className={`division-config-row ${dragging?.key === row.key ? "is-dragging" : ""}`} key={row.key} {...sortableRow("divisions", row.key)}>
            {renderOrderHandle("divisions", row.key, row.name)}
            <input aria-label={`${row.name}名称`} disabled={!editable} value={row.name} onChange={(event) => updateDivision(row.key, { name: event.target.value })} />
            <select aria-label={`${row.name}分类`} disabled={!editable} value={row.gender} onChange={(event) => updateDivision(row.key, { gender: event.target.value })}><option value="MEN">男子</option><option value="WOMEN">女子</option></select>
            <span className="resource-usage">{row.team_count} 队 · {row.group_count} 组 · {row.game_count} 场</span>
            <button aria-label={`删除${row.name}`} className="row-remove" disabled={!editable || referenced} title={referenced ? "已有引用，不能删除" : "删除组别"} type="button" onClick={() => markChanged({ ...draft, divisions: draft.divisions.filter((item) => item.key !== row.key) })}>×</button>
          </div>;
        })}
      </div>
    </section>

    <section className="season-config-section">
      <div className="season-section-heading">
        <div>
          <h2>签位方案</h2>
          <p>每行只定义一个签位族；保存后模板自动展开为 A1、A2…，无需逐个录入。</p>
        </div>
        {editable && <button className="text-action" type="button" onClick={addSlotFamily}>＋ 添加签位方案</button>}
      </div>
      <div className="slot-family-overview" aria-label="各组别签位与比赛摘要">
        {draft.divisions.map((division) => {
          const rows = draft.slot_families.filter((row) => row.division_id === division.id);
          const groupSlots = rows
            .filter((row) => row.stage === "GROUP" || row.stage === "ROUND_ROBIN")
            .reduce((sum, row) => sum + row.slot_count, 0);
          const games = rows.reduce(
            (sum, row) => sum + expectedFamilyGames(row.stage, row.slot_count),
            0,
          );
          const mismatched = groupSlots !== division.team_count;
          const status = rows.length === 0
            ? "未配置"
            : mismatched
              ? `相差 ${Math.abs(groupSlots - division.team_count)}`
              : "已匹配";
          return <article
            className={`${division.gender === "WOMEN" ? "women" : "men"}${mismatched ? " mismatch" : ""}`}
            key={division.key}
          >
            <header>
              <span aria-hidden="true" />
              <strong>{division.name}</strong>
              <b>{status}</b>
            </header>
            <dl>
              <div><dt>球队</dt><dd>{division.team_count}</dd></div>
              <div><dt>小组签位</dt><dd>{groupSlots}</dd></div>
              <div><dt>预计比赛</dt><dd>{games}</dd></div>
            </dl>
          </article>;
        })}
      </div>
      <div className="slot-family-table">
        <div className="slot-family-row slot-family-head"><span>移动</span><span>组别 / 球队</span><span>阶段</span><span>轮次</span><span>字母</span><span>签位数</span><span>自动比赛数</span><span /></div>
        {draft.slot_families.map((row) => <div className={`slot-family-row ${dragging?.key === row.key ? "is-dragging" : ""}`} key={row.key} {...sortableRow("slot_families", row.key)}>
            {renderOrderHandle("slot_families", row.key, `${row.division_name}${row.stage_name}${row.prefix}`)}
            <select aria-label={`${row.prefix}签位组别`} disabled={!editable} value={row.division_id} onChange={(event) => {
              const division = draft.divisions.find((item) => item.id === event.target.value);
              if (!division) return;
              const peers = draft.slot_families.filter((item) => item.key !== row.key);
              const genderByDivision = new Map(draft.divisions.map((item) => [item.id, item.gender]));
              const prefixUsed = peers.some((item) => genderByDivision.get(item.division_id) === division.gender && item.prefix === row.prefix);
              updateSlotFamily(row.key, {
                division_id: division.id,
                division_code: division.code,
                division_name: division.name,
                gender: division.gender,
                prefix: prefixUsed ? nextSlotPrefix(peers, draft.divisions, division.id) : row.prefix,
              });
            }}>
              {draft.divisions.filter((division) => division.id).map((division) => <option key={division.id} value={division.id}>{division.name} · {division.team_count} 队</option>)}
            </select>
            <select aria-label={`${row.prefix}签位阶段`} disabled={!editable} value={row.stage} onChange={(event) => {
              const option = stageOptions.find(([value]) => value === event.target.value);
              updateSlotFamily(row.key, {
                stage: event.target.value,
                stage_name: option?.[1] ?? event.target.value,
                round_number: event.target.value === "KNOCKOUT" ? row.round_number : 1,
              });
            }}>
              {stageOptions.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select>
            <input aria-label={`${row.prefix}签位轮次`} disabled={!editable || row.stage !== "KNOCKOUT"} min="1" type="number" value={row.round_number} onChange={(event) => updateSlotFamily(row.key, { round_number: Math.max(1, Number(event.target.value)) })} />
            <input aria-label={`${row.division_name}${row.stage_name}签位字母`} className="slot-prefix-input" disabled={!editable} maxLength={1} pattern="[A-Za-z]" value={row.prefix} onChange={(event) => updateSlotFamily(row.key, { prefix: event.target.value.replace(/[^A-Za-z]/g, "").slice(0, 1) })} />
            <input aria-label={`${row.prefix}签位数`} disabled={!editable} min="1" type="number" value={row.slot_count} onChange={(event) => updateSlotFamily(row.key, { slot_count: Number(event.target.value) })} />
            <span className="resource-usage"><strong>{expectedFamilyGames(row.stage, row.slot_count)}</strong> 场</span>
            <button aria-label={`删除${row.division_name}${row.stage_name}${row.prefix}签位方案`} className="row-remove" disabled={!editable} type="button" onClick={() => markChanged({ ...draft, slot_families: draft.slot_families.filter((item) => item.key !== row.key) })}>×</button>
          </div>)}
        {draft.slot_families.length === 0 && <p className="capacity-empty">尚未配置签位方案；模板下载会被阻止。</p>}
      </div>
    </section>

    <section className="season-config-section">
      <div className="season-section-heading">
        <div><h2>标准场地</h2><p>新赛季默认五四东一至东三；启用中的场地按顺序参与自动分配，活动申请的具体预留场地在生效前隐藏。</p></div>
        {editable && <button className="text-action" type="button" onClick={addVenue}>＋ 添加场地</button>}
      </div>
      <div className="venue-config-table simplified" aria-label="标准场地">
        <div className="venue-config-row venue-config-head"><span>移动</span><span>场地名称</span><span>自动分配</span><span>已关联</span><span /></div>
        {draft.venues.map((row) => <div className={`venue-config-row ${dragging?.key === row.key ? "is-dragging" : ""}`} key={row.key} {...sortableRow("venues", row.key)}>
            {renderOrderHandle("venues", row.key, row.name)}
            <input aria-label={`${row.name}名称`} disabled={!editable} value={row.name} onChange={(event) => updateVenue(row.key, { name: event.target.value })} />
            <label className="venue-active-toggle"><input checked={row.active} disabled={!editable} type="checkbox" onChange={(event) => updateVenue(row.key, { active: event.target.checked })} /><span>{row.active ? "启用" : "停用"}</span></label>
            <span className="resource-usage">{row.game_count} 场正式比赛</span>
            <button aria-label={`删除${row.name}`} className="row-remove" disabled={!editable || draft.venues.length <= 1} title={draft.venues.length <= 1 ? "至少保留一个标准场地" : "删除标准场地"} type="button" onClick={() => removeVenue(row.key)}>×</button>
          </div>)}
      </div>
    </section>

    <section className="season-config-section">
      <div className="season-section-heading"><div><h2>标准时段</h2><p>系统已预设 8 个标准时段；通常只需核对显示名称和默认时间，无需手动添加。</p></div><span>固定 8 个</span></div>
      <div className="period-config-table">
        <div className="period-config-row period-config-head"><span>移动</span><span>名称</span><span>默认时间</span><span>已关联</span></div>
        {draft.periods.map((row) => <div className={`period-config-row ${dragging?.key === row.key ? "is-dragging" : ""}`} key={row.key} {...sortableRow("periods", row.key)}>
          {renderOrderHandle("periods", row.key, row.name)}
          <input aria-label={`${row.name}名称`} disabled={!editable} value={row.name} onChange={(event) => updatePeriod(row.key, { name: event.target.value })} />
          <input aria-label={`${row.name}默认时间`} disabled={!editable} type="time" value={row.start_time.slice(0, 5)} onChange={(event) => updatePeriod(row.key, { start_time: event.target.value })} />
          <span className="resource-usage">{row.game_count} 场 · {row.active_reservation_count} 个预留</span>
        </div>)}
      </div>
    </section>

    <section className="season-config-section capacity-section">
      <div className="season-section-heading"><div><h2>默认容量</h2><p>周中与周末各维护一套默认值；0 表示普通流程不可用。容量可以高于标准场地数。</p></div></div>
      <div className="capacity-default-table">
        <div className="capacity-default-row capacity-default-head"><span>时段</span><span>默认时间</span><span>周中</span><span>周末</span></div>
        {draft.periods.map((period) => <div className="capacity-default-row" key={period.key}>
          <strong>{period.name}</strong><span>{period.start_time.slice(0, 5)}</span>
          {(["WEEKDAY", "WEEKEND"] as const).map((dayType) => {
            const value = period.default_capacities[dayType] ?? 0;
            return <label className={`capacity-number ${value === 0 ? "zero" : "open"}`} key={dayType}>
              <input aria-label={`${period.name}${dayTypeLabels[dayType]}容量`} disabled={!editable} min="0" type="number" value={value} onChange={(event) => updatePeriod(period.key, { default_capacities: { ...period.default_capacities, [dayType]: Number(event.target.value) } })} /><span>场</span>
            </label>;
          })}
        </div>)}
      </div>
    </section>

    <section className="season-config-section capacity-section">
      <div className="season-section-heading"><div><h2>特殊日期容量</h2><p>默认不设置。仅由管理员为节假日、补赛日等例外填写，系统不会根据已排比赛自动生成或补高。</p></div>{editable && <button className="text-action" type="button" onClick={() => markChanged({ ...draft, date_capacity_overrides: [...draft.date_capacity_overrides, { id: "", key: nextKey("override"), date: "", period_code: draft.periods[0]?.code.toUpperCase() ?? "P1", capacity: 0, note: "" }] })}>＋ 添加例外</button>}</div>
      {draft.date_capacity_overrides.length ? <div className="capacity-override-table">
        <div className="capacity-override-row capacity-override-head"><span>日期</span><span>时段</span><span>容量</span><span>备注</span><span /></div>
        {draft.date_capacity_overrides.map((row) => <div className="capacity-override-row" key={row.key}>
          <input aria-label="特殊日期" disabled={!editable} required type="date" value={row.date} onChange={(event) => updateOverride(row.key, { date: event.target.value })} />
          <select aria-label="特殊日期时段" disabled={!editable} value={row.period_code.toUpperCase()} onChange={(event) => updateOverride(row.key, { period_code: event.target.value })}>{draft.periods.map((period) => <option key={period.id} value={period.code.toUpperCase()}>{period.name}</option>)}</select>
          <label className="capacity-number"><input aria-label="特殊日期容量" disabled={!editable} min="0" type="number" value={row.capacity} onChange={(event) => updateOverride(row.key, { capacity: Number(event.target.value) })} /><span>场</span></label>
          <input aria-label="特殊日期备注" disabled={!editable} placeholder="如：清明假期" value={row.note} onChange={(event) => updateOverride(row.key, { note: event.target.value })} />
          <button aria-label="删除特殊日期容量" className="row-remove" disabled={!editable} type="button" onClick={() => markChanged({ ...draft, date_capacity_overrides: draft.date_capacity_overrides.filter((item) => item.key !== row.key) })}>×</button>
        </div>)}
      </div> : <p className="capacity-empty">未设置特殊日期容量，全部使用周中或周末默认值。</p>}
    </section>

    <section className="season-config-section capacity-section">
      {draft.over_capacity.length > 0 && <div className="capacity-alert" role="alert">当前有 {draft.over_capacity.length} 个时段超出有效容量；既有比赛不会被自动取消，但普通流程不能继续占用。</div>}
      <CapacityCalendar ledger={ledger} variant="embedded" />
    </section>

    {editable && <div className={`season-save-bar ${dirty ? "dirty" : ""}`}>
      <div><strong>{dirty ? "有尚未保存的修改" : "配置已与服务器同步"}</strong><span>保存前会预览容量、调赛申请和历史赛程影响。</span></div>
      {dirty && <button className="text-action" type="button" onClick={() => configuration && (setDraft(toDraft(configuration)), setDirty(false), setMessage(null))}>撤销修改</button>}
      <button className="primary-action" disabled={!dirty || busy} type="button" onClick={() => void submit()}>{busy ? "正在保存…" : "预览并保存"}</button>
    </div>}
    {message && <p className={`season-management-message ${messageTone}`} role="status">{message}</p>}
    {showCreate && <CreateSeasonDialog client={client} seasons={seasons} onClose={() => setShowCreate(false)} onCreated={async (created) => { setShowCreate(false); await onDataChanged(created.id); onSeasonChange(created.id); }} />}
  </div>;
}

function CreateSeasonDialog({ client, seasons, onClose, onCreated }: {
  client: AdminClient;
  seasons: AdminSeason[];
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
    template_season_id: null,
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault(); setBusy(true); setError(null);
    try { await onCreated(await client.createAdminSeason(form)); }
    catch (reason: unknown) { setError(reason instanceof Error ? reason.message : "赛季创建失败"); }
    finally { setBusy(false); }
  };
  return <div className="dialog-backdrop" role="presentation" onMouseDown={onClose}>
    <section className="season-create-dialog" role="dialog" aria-modal="true" aria-labelledby="create-season-title" onMouseDown={(event) => event.stopPropagation()}>
      <div className="dialog-heading"><div><h2 id="create-season-title">新建赛季</h2></div><button className="dialog-close" type="button" onClick={onClose} aria-label="关闭">×</button></div>
      <p className="dialog-detail">新赛季只复制排期元信息，不复制球队、赛程、身份或比分。</p>
      <form className="season-create-form" onSubmit={(event) => void submit(event)}>
        <label className="wide">赛季名称<input autoFocus required value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} /></label>
        <label>赛事类型<select value={form.competition_type} onChange={(event) => setForm({ ...form, competition_type: event.target.value })}><option value="PKU_CUP">北大杯</option><option value="FRESHMAN_CUP">新生杯</option></select></label>
        <label>赛季年份<input min="1" step="1" required type="number" value={form.year} onChange={(event) => setForm({ ...form, year: Number(event.target.value) })} /></label>
        <label>开始日期<input required type="date" value={form.starts_on} onChange={(event) => setForm({ ...form, starts_on: event.target.value })} /></label>
        <label>结束日期<input required type="date" value={form.ends_on} onChange={(event) => setForm({ ...form, ends_on: event.target.value })} /></label>
        <label className="wide">配置来源<select aria-label="配置来源" value={form.template_season_id ?? ""} onChange={(event) => setForm({ ...form, template_season_id: event.target.value || null })}><option value="">系统默认配置（推荐）</option>{seasons.map((season) => <option key={season.id} value={season.id}>明确沿用 {season.year} · {season.name} 的组别、场地、签位与容量</option>)}</select><small>默认从当前代码生成组别、三个标准场地、八个标准时段和容量；只有主动选择时才沿用历史配置。比赛时段始终使用当前系统默认值。</small></label>
        {error && <p className="dialog-error wide" role="alert">{error}</p>}
        <div className="dialog-actions wide"><button className="secondary-action" type="button" onClick={onClose}>取消</button><button className="primary-action" disabled={busy} type="submit">{busy ? "正在创建…" : "创建赛季"}</button></div>
      </form>
    </section>
  </div>;
}
