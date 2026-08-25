import {
  type ChangeEvent,
  type DragEvent,
  type KeyboardEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import type {
  AdminAccount,
  AdminSeason,
  PreviewSeasonConfiguration,
  ScheduleImport,
  ScheduleImportReadiness,
  ScheduleImportResetPreview,
  SeasonConfiguration,
  UpdateSeasonConfiguration,
  createAdminClient,
} from "@pkuba/api-client";

import { buildSeasonConfigurationPayload } from "./season-configuration-payload";
import { confirmAdminNavigation, useAdminDirtySource } from "./dirtyGuard";

type AdminClient = ReturnType<typeof createAdminClient>;
type GridColumnDraft = SeasonConfiguration["grid_columns"][number] & { key: string };
type BusyAction = "context" | "grid" | "download" | "upload" | "confirm" | "reset" | null;

let localGridKey = 0;
const nextGridKey = () => `grid-column-new-${++localGridKey}`;

const statusLabels: Record<string, string> = {
  SETUP: "准备中",
  PUBLISHED: "已公开",
  ARCHIVED: "已归档",
};

const matchupIntegrityCodes = new Set([
  "DUPLICATE_MATCHUP",
  "DUPLICATE_FAMILY_MATCHUPS",
  "MISSING_ROUND_ROBIN_MATCHUPS",
  "UNUSED_ELIMINATION_SLOTS",
  "REUSED_ELIMINATION_SLOTS",
  "NO_GAMES_IN_WORKBOOK",
  "INVALID_MATCHUP",
  "UNKNOWN_SLOT",
  "SAME_PARTICIPANT",
  "SLOT_FAMILY_MISMATCH",
  "GAME_CODE_COLLISION",
  "GAME_COUNT_MISMATCH",
]);
const capacityCodes = new Set(["CAPACITY_EXCEEDED"]);
const scheduleConflictCodes = new Set([
  "VENUE_OCCUPIED",
  "PARTICIPANT_TIME_CONFLICT",
  "FINAL_ONLY_COLUMN",
  "GRID_DATES_CHANGED",
  "GRID_COLUMNS_CHANGED",
]);

export function ScheduleImportWorkspace({
  account,
  client,
  seasons,
  season,
  onSeasonChange,
  onDataChanged,
  onOpenConfiguration,
  onOpenEditor,
}: {
  account: AdminAccount;
  client: AdminClient;
  seasons: AdminSeason[];
  season: AdminSeason;
  onSeasonChange: (seasonId: string) => void;
  onDataChanged: () => Promise<void>;
  onOpenConfiguration: () => void;
  onOpenEditor: () => void;
}) {
  const fileInput = useRef<HTMLInputElement>(null);
  const [readiness, setReadiness] = useState<ScheduleImportReadiness | null>(null);
  const [resetPreview, setResetPreview] = useState<ScheduleImportResetPreview | null>(null);
  const [configuration, setConfiguration] = useState<SeasonConfiguration | null>(null);
  const [gridColumns, setGridColumns] = useState<GridColumnDraft[]>([]);
  const [gridDirty, setGridDirty] = useState(false);
  useAdminDirtySource(`schedule-import-columns:${season.id}`, gridDirty);
  const [file, setFile] = useState<File | null>(null);
  const [batch, setBatch] = useState<ScheduleImport | null>(null);
  const [dragActive, setDragActive] = useState(false);
  const [busy, setBusy] = useState<BusyAction>(null);
  const [acknowledged, setAcknowledged] = useState(false);
  const [query, setQuery] = useState("");
  const [division, setDivision] = useState("all");
  const [stage, setStage] = useState("all");
  const [date, setDate] = useState("");
  const [resetName, setResetName] = useState("");
  const [resetAcknowledged, setResetAcknowledged] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const loadGeneration = useRef(0);

  const loadContext = useCallback(async () => {
    const generation = ++loadGeneration.current;
    setBusy("context");
    setError(null);
    try {
      const [nextReadiness, nextReset, nextConfiguration] = await Promise.all([
        client.getScheduleImportReadiness(season.id),
        client.getScheduleImportResetPreview(season.id),
        client.getSeasonConfiguration(season.id),
      ]);
      if (generation !== loadGeneration.current) return;
      setReadiness(nextReadiness);
      setResetPreview(nextReset);
      setConfiguration(nextConfiguration);
      setGridColumns(
        nextConfiguration.grid_columns.map((row) => ({ ...row, key: row.id })),
      );
      setGridDirty(false);
    } catch (reason: unknown) {
      if (generation !== loadGeneration.current) return;
      setError(reason instanceof Error ? reason.message : "无法读取导入前置条件");
    } finally {
      if (generation === loadGeneration.current) setBusy(null);
    }
  }, [client, season.id]);

  useEffect(() => {
    setFile(null);
    setBatch(null);
    setAcknowledged(false);
    setResetName("");
    setResetAcknowledged(false);
    setConfiguration(null);
    setGridColumns([]);
    setGridDirty(false);
    setMessage(null);
    if (fileInput.current) fileInput.current.value = "";
    void loadContext();
    return () => {
      loadGeneration.current += 1;
    };
  }, [loadContext]);

  const errors = batch?.issues.filter((issue) => issue.severity === "ERROR") ?? [];
  const warnings = batch?.issues.filter((issue) => issue.severity === "WARNING") ?? [];
  const hasErrors = (batch?.summary.error_count ?? 0) > 0;
  const errorCodes = new Set(errors.map((issue) => issue.code));
  const games = batch?.summary.games ?? [];
  const expectedGameCount = batch?.summary.prerequisites.expected_game_count ?? 0;
  const resultingGameCount = batch?.summary.covered_game_count ?? 0;
  const confirmationChecks = batch
    ? [
        {
          key: "matchups",
          label: "对阵完整且唯一",
          passed: ![...matchupIntegrityCodes].some((code) => errorCodes.has(code)),
          detail: "无缺漏、重复或主客颠倒重复；淘汰签位没有漏用或复用。",
        },
        {
          key: "count",
          label: "比赛数量正确",
          passed: resultingGameCount === expectedGameCount,
          detail: `现有有效赛程与本次导入合计覆盖 ${resultingGameCount} 场，赛制配置预计 ${expectedGameCount} 场；本批次新增 ${batch.summary.new_game_count} 场。`,
        },
        {
          key: "capacity",
          label: "比赛容量充足",
          passed: ![...capacityCodes].some((code) => errorCodes.has(code)),
          detail: "按日期和标准时段核算，已计入既有比赛与有效场地预留。",
        },
        {
          key: "resources",
          label: "排期资源无冲突",
          passed: ![...scheduleConflictCodes].some((code) => errorCodes.has(code)),
          detail: "场地不重占，同一球队或签位不同时参赛，决赛专用列使用正确。",
        },
        {
          key: "safety",
          label: "文件与新增边界通过",
          passed: !hasErrors,
          detail: "日期、签位、文件安全和既有比赛冲突均已检查；确认时还会锁定赛季并重新校验。",
        },
      ]
    : [];
  const allConfirmationChecksPassed = confirmationChecks.every((check) => check.passed);
  const divisions = useMemo(
    () => [...new Set(games.map((game) => game.division_code))].sort(),
    [games],
  );
  const stages = useMemo(
    () => [...new Set(games.map((game) => game.stage_name))].sort(),
    [games],
  );
  const shownGames = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    return games.filter((game) => {
      if (division !== "all" && game.division_code !== division) return false;
      if (stage !== "all" && game.stage_name !== stage) return false;
      if (date && game.date !== date) return false;
      if (!normalized) return true;
      return [
        game.code,
        game.division_name,
        game.group_code,
        game.home_slot_code,
        game.home_slot_label,
        game.away_slot_code,
        game.away_slot_label,
        game.venue_name,
      ]
        .filter(Boolean)
        .join(" ")
        .toLowerCase()
        .includes(normalized);
    });
  }, [date, division, games, query, stage]);
  const uploadOnlyBlockers = readiness
    ? readiness.blockers.filter(
        (blocker) => !readiness.template_blockers.some(
          (templateBlocker) => templateBlocker.code === blocker.code,
        ),
      )
    : [];

  const chooseFile = (nextFile: File | null) => {
    setError(null);
    setMessage(null);
    setBatch(null);
    setAcknowledged(false);
    if (!nextFile) {
      setFile(null);
      return;
    }
    if (!nextFile.name.toLowerCase().endsWith(".xlsx")) {
      setError("请选择 .xlsx 工作簿。");
      return;
    }
    if (nextFile.size > 10 * 1024 * 1024) {
      setError("文件超过 10 MB，无法上传。");
      return;
    }
    setFile(nextFile);
  };

  const onFileChange = (event: ChangeEvent<HTMLInputElement>) => {
    chooseFile(event.target.files?.[0] ?? null);
  };

  const clearFile = () => {
    chooseFile(null);
    if (fileInput.current) fileInput.current.value = "";
  };

  const openFilePicker = () => fileInput.current?.click();

  const updateGridColumn = (key: string, patch: Partial<GridColumnDraft>) => {
    setGridColumns((rows) => rows.map((row) => row.key === key ? { ...row, ...patch } : row));
    setGridDirty(true);
    setMessage(null);
  };

  const addGridColumn = () => {
    if (!configuration) return;
    const used = new Set(gridColumns.map((row) => `${row.period_id}:${row.venue_id}`));
    const pair = configuration.periods
      .flatMap((period) => configuration.venues
        .filter((venue) => venue.active)
        .map((venue) => ({ period, venue })))
      .find(({ period, venue }) => !used.has(`${period.id}:${venue.id}`));
    if (!pair) {
      setError("没有可添加的时段与标准场地组合；同一组合只能出现一次。");
      return;
    }
    setGridColumns((rows) => [...rows, {
      id: "",
      key: nextGridKey(),
      period_id: pair.period.id,
      period_code: pair.period.code,
      period_name: pair.period.name,
      start_time: pair.period.start_time,
      venue_id: pair.venue.id,
      venue_name: pair.venue.name,
      final_only: false,
      sort_order: Math.max(0, ...rows.map((row) => row.sort_order)) + 1,
    }]);
    setGridDirty(true);
    setError(null);
    setMessage(null);
  };

  const moveGridColumn = (key: string, direction: -1 | 1) => {
    const rows = [...gridColumns].sort((left, right) => left.sort_order - right.sort_order);
    const index = rows.findIndex((row) => row.key === key);
    const target = index + direction;
    if (index < 0 || target < 0 || target >= rows.length) return;
    [rows[index], rows[target]] = [rows[target], rows[index]];
    setGridColumns(rows.map((row, rowIndex) => ({ ...row, sort_order: rowIndex + 1 })));
    setGridDirty(true);
    setMessage(null);
  };

  const removeGridColumn = (key: string) => {
    setGridColumns((rows) => rows
      .filter((row) => row.key !== key)
      .sort((left, right) => left.sort_order - right.sort_order)
      .map((row, index) => ({ ...row, sort_order: index + 1 })));
    setGridDirty(true);
    setMessage(null);
  };

  const saveGridColumns = async () => {
    if (!configuration || !gridDirty) return;
    setBusy("grid");
    setError(null);
    setMessage(null);
    try {
      const proposed = { ...configuration, grid_columns: gridColumns };
      const payload = buildSeasonConfigurationPayload(proposed);
      const preview = await client.previewSeasonConfiguration(
        season.id,
        payload as PreviewSeasonConfiguration,
      );
      const warnings = [
        preview.maintenance_required ? "当前不是准备期，将以维护模式修改。" : "",
        preview.templates_invalidated ? "已有模板将失效，需要重新下载。" : "",
      ].filter(Boolean);
      if (!window.confirm([
        `确认保存 ${gridColumns.length} 个赛程网格列？`,
        ...warnings,
        "操作会记录完整审计快照。",
      ].join("\n"))) return;
      const updated = await client.updateSeasonConfiguration(season.id, {
        ...payload,
        maintenance_confirmed: preview.maintenance_required,
        impact_hash: preview.impact_hash,
        cancel_reschedule_request_ids: preview.affected_reschedule_request_ids,
      } as UpdateSeasonConfiguration);
      setConfiguration(updated);
      setGridColumns(updated.grid_columns.map((row) => ({ ...row, key: row.id })));
      setGridDirty(false);
      setMessage(`已保存 ${updated.grid_columns.length} 个赛程网格列，请使用最新模板。`);
      await onDataChanged();
      await loadContext();
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "赛程网格列保存失败");
    } finally {
      setBusy(null);
    }
  };

  const onDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setDragActive(false);
    chooseFile(event.dataTransfer.files?.[0] ?? null);
  };

  const onDropKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      openFilePicker();
    }
  };

  const download = async () => {
    setBusy("download");
    setError(null);
    setMessage(null);
    try {
      const blob = await client.downloadScheduleTemplate(season.id);
      if (blob.size === 0) throw new Error("服务器返回了空模板，请重试。");
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `PKUBA_${season.year}_${season.name.replace(/[<>:"/\\|?*]/g, "-")}_赛程模板.xlsx`;
      link.hidden = true;
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1_000);
      setMessage("模板下载已开始。第一、二页无需修改，只需填写第三页赛程网格。");
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "模板下载失败");
    } finally {
      setBusy(null);
    }
  };

  const upload = async () => {
    if (!file) return;
    setBusy("upload");
    setError(null);
    setMessage(null);
    try {
      const nextBatch = await client.uploadSchedule(season.id, file);
      setBatch(nextBatch);
      setAcknowledged(false);
      setMessage(
        nextBatch.summary.error_count > 0
          ? "服务器已完成校验，请先修正全部错误后重新选择文件。"
          : "校验通过；正式赛程尚未写入。",
      );
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "上传校验失败");
    } finally {
      setBusy(null);
    }
  };

  const confirm = async () => {
    if (
      !batch
      || hasErrors
      || !allConfirmationChecksPassed
      || !acknowledged
      || !readiness
    ) return;
    setBusy("confirm");
    setError(null);
    try {
      const confirmed = await client.confirmScheduleImport(batch.id, {
        expected_season_version: readiness.season_version,
      });
      setBatch(confirmed);
      setMessage(
        `已新增 ${confirmed.summary.new_game_count} 场比赛；新比赛默认允许领队申请调赛。`,
      );
      await onDataChanged();
      await loadContext();
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "确认创建失败");
    } finally {
      setBusy(null);
    }
  };

  const reset = async () => {
    if (
      !resetPreview?.eligible ||
      resetName !== season.name ||
      !resetAcknowledged ||
      !window.confirm(
        `确认重置“${season.name}”的全部已确认导入？此操作只删除可追溯的导入比赛、签位和小组。`,
      )
    ) {
      return;
    }
    setBusy("reset");
    setError(null);
    try {
      const result = await client.resetScheduleImports(season.id, {
        expected_season_version: resetPreview.season_version,
        season_name: resetName,
      });
      setMessage(
        `重置完成：删除 ${result.game_count} 场比赛、${result.slot_count} 个签位和 ${result.group_count} 个小组。`,
      );
      setBatch(null);
      setFile(null);
      setResetName("");
      setResetAcknowledged(false);
      if (fileInput.current) fileInput.current.value = "";
      await onDataChanged();
      await loadContext();
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "重置失败，未修改任何数据");
    } finally {
      setBusy(null);
    }
  };

  if (account.role !== "SUPERADMIN") {
    return (
      <section className="state-panel error">
        <h2>需要超级管理员权限</h2>
        <p>赛季初赛程创建与导入重置仅限超级管理员。</p>
      </section>
    );
  }

  const stepState = [
    readiness?.template_ready ? "done" : "current",
    batch ? (hasErrors ? "error" : "done") : readiness?.ready ? "current" : "waiting",
    batch?.status === "CONFIRMED" ? "done" : batch && !hasErrors ? "current" : "waiting",
  ];

  return (
    <div className="schedule-import-workspace">
      <ol className="import-steps" aria-label="赛程导入步骤">
        {["下载并填写第三页", "上传并校验", "确认创建"].map((label, index) => (
          <li className={stepState[index]} key={label}>
            <span>{index + 1}</span>
            <strong>{label}</strong>
          </li>
        ))}
      </ol>

      <section className="import-section format-section" aria-labelledby="format-title">
        <div className="import-section-heading">
          <div>
            <p className="eyebrow">步骤 1</p>
            <h2 id="format-title">下载模板，只填写第三页</h2>
            <p>第一、二页由“赛季和组别”配置自动生成。第二页仅提示签位字母含义，上传时不会读取其中的业务内容。</p>
          </div>
          <div className="season-actions">
            <label>
              操作赛季
              <select
                value={season.id}
                onChange={(event) => {
                  const nextSeasonId = event.target.value;
                  void confirmAdminNavigation().then((confirmed) => {
                    if (confirmed) onSeasonChange(nextSeasonId);
                  });
                }}
              >
                {seasons.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name} · {statusLabels[item.status] ?? item.status}
                  </option>
                ))}
              </select>
            </label>
            <button
              className="quiet-action"
              disabled={busy !== null}
              onClick={onOpenConfiguration}
              type="button"
            >
              前往赛季和组别
            </button>
            <button
              className="primary-action"
              disabled={!readiness?.template_ready || busy !== null}
              onClick={() => void download()}
              type="button"
            >
              {busy === "download" ? "正在生成…" : "下载 XLSX 模板"}
            </button>
          </div>
        </div>

        {configuration && (
          <div className="import-grid-configuration">
            <header>
              <div>
                <p className="eyebrow">模板列设置</p>
                <h3>赛程网格列</h3>
                <p>新赛季固定预填当前系统的 16 个标准场地时段，不从历史赛季读取；只在需要改变第三页排版时调整。</p>
              </div>
              <div className="import-grid-heading-actions">
                <strong>{gridColumns.length} 列</strong>
                {configuration.editable && <button className="quiet-action" disabled={busy !== null} type="button" onClick={addGridColumn}>＋ 添加列</button>}
              </div>
            </header>
            <div className="import-grid-column-table" role="table" aria-label="赛程网格列设置">
              <div className="import-grid-column-row import-grid-column-head" role="row">
                <span>列序</span><span>标准时段</span><span>场地</span><span>用途</span><span>调整</span><span />
              </div>
              {[...gridColumns]
                .sort((left, right) => left.sort_order - right.sort_order)
                .map((row, index, rows) => (
                  <div className={`import-grid-column-row${row.final_only ? " final-only" : ""}`} role="row" key={row.key}>
                    <strong className="import-grid-position">{String(index + 1).padStart(2, "0")}</strong>
                    <select aria-label={`${row.venue_name}网格列时段`} disabled={!configuration.editable || busy !== null} value={row.period_id} onChange={(event) => {
                      const period = configuration.periods.find((item) => item.id === event.target.value);
                      if (!period) return;
                      updateGridColumn(row.key, {
                        period_id: period.id,
                        period_code: period.code,
                        period_name: period.name,
                        start_time: period.start_time,
                      });
                    }}>
                      {configuration.periods.map((period) => <option key={period.id} value={period.id}>{period.start_time.slice(0, 5)} · {period.name}</option>)}
                    </select>
                    <select aria-label={`${row.start_time}网格列场地`} disabled={!configuration.editable || busy !== null} value={row.venue_id} onChange={(event) => {
                      const venue = configuration.venues.find((item) => item.id === event.target.value);
                      if (!venue) return;
                      updateGridColumn(row.key, { venue_id: venue.id, venue_name: venue.name });
                    }}>
                      {!configuration.venues.some((venue) => venue.id === row.venue_id) && <option value={row.venue_id}>{row.venue_name}（特殊场地）</option>}
                      {configuration.venues.filter((venue) => venue.active).map((venue) => <option key={venue.id} value={venue.id}>{venue.name}</option>)}
                    </select>
                    <label className="import-grid-final-toggle"><input checked={row.final_only} disabled={!configuration.editable || busy !== null} type="checkbox" onChange={(event) => updateGridColumn(row.key, { final_only: event.target.checked })} /><span>{row.final_only ? "仅决赛" : "通用"}</span></label>
                    <span className="import-grid-order-actions">
                      <button aria-label={`上移${row.start_time}${row.venue_name}`} disabled={!configuration.editable || busy !== null || index === 0} type="button" onClick={() => moveGridColumn(row.key, -1)}>↑</button>
                      <button aria-label={`下移${row.start_time}${row.venue_name}`} disabled={!configuration.editable || busy !== null || index === rows.length - 1} type="button" onClick={() => moveGridColumn(row.key, 1)}>↓</button>
                    </span>
                    <button aria-label={`删除${row.start_time}${row.venue_name}网格列`} className="import-grid-remove" disabled={!configuration.editable || busy !== null} type="button" onClick={() => removeGridColumn(row.key)}>×</button>
                  </div>
                ))}
              {gridColumns.length === 0 && <p className="import-grid-empty">至少添加一个时段与场地组合，模板才可下载。</p>}
            </div>
            <footer>
              <span>{gridDirty ? "网格列有未保存修改" : "当前网格列已与服务器同步"}</span>
              <button className="primary-action" disabled={!gridDirty || busy !== null} type="button" onClick={() => void saveGridColumns()}>{busy === "grid" ? "正在保存…" : "保存网格列"}</button>
            </footer>
          </div>
        )}

        {readiness && (
          <div className="grid-template-status" aria-label="第三页模板状态">
            <div>
              <small>第三页尺寸</small>
              <strong>{readiness.calendar_day_count} 个日期 × {readiness.grid_column_count} 个标准场地时段</strong>
            </div>
            <span className={readiness.template_ready ? "readiness-ready" : "readiness-blocked"}>
              {readiness.template_ready ? "模板可下载" : "模板配置未完成"}
            </span>
          </div>
        )}
        {readiness && readiness.template_blockers.length > 0 && (
          <IssueGroup title="请先完成模板前置配置" tone="error" issues={readiness.template_blockers} />
        )}
        {uploadOnlyBlockers.length > 0 && (
          <IssueGroup title="模板可下载，但暂不能导入" tone="warning" issues={uploadOnlyBlockers} />
        )}

        <div className="grid-layout-guide" role="table" aria-label="第三页赛程网格填写示意">
          <div className="grid-layout-row grid-layout-head" role="row">
            <span>日期</span>
            <span>星期</span>
            <span>12:50 · 五四东一</span>
            <span>18:30 · 邱德拔</span>
          </div>
          <div className="grid-layout-row" role="row">
            <span>2027-03-08</span>
            <span>周一</span>
            <strong>A1vsA2</strong>
            <span className="final-grid-cell">仅决赛</span>
          </div>
        </div>
        <p className="format-note">
          日期和星期已自动填写。每个有边框的格子直接填写 <code>A1vsA2</code>；女子比赛写 <code>A1vsA2（女）</code>。浅琥珀列只用于决赛。
        </p>
      </section>

      <section className="import-section" aria-labelledby="upload-title">
        <div className="import-section-heading compact">
          <div>
            <p className="eyebrow">步骤 2</p>
            <h2 id="upload-title">上传并校验 V3.1 工作簿</h2>
          </div>
          <span className="subtle">仅 .xlsx · 最大 10 MB</span>
        </div>
        <input
          ref={fileInput}
          accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
          className="visually-hidden"
          onChange={onFileChange}
          type="file"
        />
        <div
          className={`file-dropzone${dragActive ? " active" : ""}${file ? " has-file" : ""}`}
          onClick={openFilePicker}
          onDragEnter={(event) => {
            event.preventDefault();
            setDragActive(true);
          }}
          onDragLeave={() => setDragActive(false)}
          onDragOver={(event) => event.preventDefault()}
          onDrop={onDrop}
          onKeyDown={onDropKeyDown}
          role="button"
          tabIndex={0}
        >
          <span className="file-mark">XLSX</span>
          {file ? (
            <div>
              <strong>{file.name}</strong>
              <span>{formatBytes(file.size)} · {batch ? batchStatus(batch) : "等待上传校验"}</span>
            </div>
          ) : (
            <div>
              <strong>拖放工作簿到此处，或按回车选择</strong>
              <span>上传只创建暂存批次，不会直接写入正式赛程。</span>
            </div>
          )}
        </div>
        <div className="file-actions">
          <button className="secondary-action" onClick={openFilePicker} type="button">
            {file ? "重新选择" : "选择文件"}
          </button>
          {file && (
            <button className="quiet-action" disabled={busy !== null} onClick={clearFile} type="button">
              清除
            </button>
          )}
          <button
            className="primary-action"
            disabled={!file || !readiness?.ready || busy !== null}
            onClick={() => void upload()}
            type="button"
          >
            {busy === "upload" ? "正在校验…" : "上传并校验"}
          </button>
        </div>
      </section>

      {batch && (
        <section className="import-section validation-workspace" aria-labelledby="preview-title">
          <div className="import-section-heading">
            <div>
              <p className="eyebrow">步骤 2 · 校验结果</p>
              <h2 id="preview-title">{hasErrors ? "校验未通过" : "校验预览"}</h2>
              <p>批次 {batch.id.slice(0, 8)} · 文件 SHA-256 {batch.file_sha256.slice(0, 12)}…</p>
            </div>
            <span className={hasErrors ? "validation-state invalid" : "validation-state valid"}>
              {errors.length} 错误 · {warnings.length} 警告
            </span>
          </div>

          <div className="creation-summary" aria-label="本次新增数量">
            <Summary label="新增小组" value={batch.summary.new_group_count} />
            <Summary label="引用小组" value={batch.summary.referenced_group_count} />
            <Summary label="新增签位" value={batch.summary.new_slot_count} />
            <Summary label="引用签位" value={batch.summary.referenced_slot_count} />
            <Summary label="新增比赛" value={batch.summary.new_game_count} featured />
          </div>

          {errors.length > 0 && <IssueGroup title="错误" tone="error" issues={errors} />}
          {warnings.length > 0 && <IssueGroup title="警告" tone="warning" issues={warnings} />}

          <div className="preview-toolbar">
            <input
              aria-label="搜索比赛预览"
              onChange={(event) => setQuery(event.target.value)}
              placeholder="搜索编号、签位、场地"
              value={query}
            />
            <select aria-label="按组别筛选" onChange={(event) => setDivision(event.target.value)} value={division}>
              <option value="all">全部组别</option>
              {divisions.map((item) => <option key={item} value={item}>{item}</option>)}
            </select>
            <select aria-label="按阶段筛选" onChange={(event) => setStage(event.target.value)} value={stage}>
              <option value="all">全部阶段</option>
              {stages.map((item) => <option key={item} value={item}>{item}</option>)}
            </select>
            <input aria-label="按日期筛选" onChange={(event) => setDate(event.target.value)} type="date" value={date} />
            <span>{shownGames.length} / {games.length} 场</span>
          </div>

          <div className="game-preview-table" role="table" aria-label="待创建比赛完整预览">
            <div className="game-preview-row game-preview-head" role="row">
              <span>比赛</span><span>赛制</span><span>对阵签位</span><span>日期 / 时段</span><span>场地</span>
            </div>
            {shownGames.map((game) => (
              <div className="game-preview-row" role="row" key={game.code}>
                <span><strong>{game.code}</strong><small>第 {game.round_number} 轮</small></span>
                <span>{game.division_name}<small>{game.stage_name}{game.group_code ? ` · ${game.group_code}` : ""}</small></span>
                <span>{game.home_slot_label || game.home_slot_code}<small>{game.away_slot_label || game.away_slot_code}</small></span>
                <span>{game.date ?? "未定位"}<small>{game.start_time ?? game.period_name ?? "—"}</small></span>
                <span>{game.venue_name ?? "—"}<small>{game.cell}</small></span>
              </div>
            ))}
            {shownGames.length === 0 && <p className="table-empty">没有符合当前筛选条件的比赛。</p>}
          </div>

          {batch.status !== "CONFIRMED" && (
            <div className="confirm-create">
              <div className="confirm-create-heading">
                <div>
                  <p className="eyebrow">步骤 3 · 最终核对</p>
                  <h3>{allConfirmationChecksPassed ? "确认新增正式赛程" : "尚不能确认创建"}</h3>
                  <p>五项均通过后才可提交；确认时服务器会再次锁定并校验，任一冲突都会完整回滚。</p>
                </div>
                <strong className={allConfirmationChecksPassed ? "audit-total passed" : "audit-total failed"}>
                  {confirmationChecks.filter((check) => check.passed).length} / {confirmationChecks.length} 通过
                </strong>
              </div>
              <ul className="confirmation-audit" aria-label="最终赛程核对清单">
                {confirmationChecks.map((check) => (
                  <li className={check.passed ? "passed" : "failed"} key={check.key}>
                    <span className="audit-mark" aria-hidden="true">{check.passed ? "✓" : "!"}</span>
                    <span>
                      <strong>{check.label}</strong>
                      <small>{check.detail}</small>
                    </span>
                    <b>{check.passed ? "通过" : "未通过"}</b>
                  </li>
                ))}
              </ul>
              <div className="confirm-create-action">
                <label className="confirmation-check">
                  <input
                    checked={acknowledged}
                    disabled={!allConfirmationChecksPassed}
                    onChange={(event) => setAcknowledged(event.target.checked)}
                    type="checkbox"
                  />
                  我已逐项核对以上结果：将新增 {batch.summary.new_group_count} 个小组、{batch.summary.new_slot_count} 个签位和 {batch.summary.new_game_count} 场比赛；现有比赛不会被更新或删除。
                </label>
                <button
                  className="primary-action"
                  disabled={!acknowledged || !allConfirmationChecksPassed || busy !== null}
                  onClick={() => void confirm()}
                  type="button"
                >
                  {busy === "confirm" ? "正在创建…" : "确认并创建"}
                </button>
              </div>
            </div>
          )}
          {batch.status === "CONFIRMED" && (
            <div className="import-success-actions">
              <strong>本批次已创建完成</strong>
              <span>可在赛程编辑器逐场查看并修改“领队可调 / 不可调”。</span>
              <button className="primary-action" onClick={onOpenEditor} type="button">前往赛程编辑</button>
            </div>
          )}
        </section>
      )}

      <section className="import-danger-zone" aria-labelledby="reset-title">
        <div className="import-section-heading compact">
          <div>
            <p className="eyebrow">危险操作</p>
            <h2 id="reset-title">重置本赛季已确认导入</h2>
            <p>只回滚可追溯到已确认批次的比赛、签位和小组；手工数据与赛季基础配置保留。</p>
          </div>
          <button className="secondary-action" disabled={busy !== null} onClick={() => void loadContext()} type="button">
            {busy === "context" ? "正在预检…" : "重新运行预检"}
          </button>
        </div>
        {resetPreview && (
          <>
            <div className="reset-counts">
              <span>{resetPreview.confirmed_batch_count} 个批次</span>
              <span>{resetPreview.game_count} 场比赛</span>
              <span>{resetPreview.slot_count} 个签位</span>
              <span>{resetPreview.group_count} 个小组</span>
            </div>
            {resetPreview.blockers.length > 0 ? (
              <IssueGroup title="当前禁止重置" tone="error" issues={resetPreview.blockers} />
            ) : (
              <div className="reset-confirmation">
                <label>
                  输入赛季名称 <strong>{season.name}</strong>
                  <input
                    autoComplete="off"
                    onChange={(event) => setResetName(event.target.value)}
                    placeholder={season.name}
                    value={resetName}
                  />
                </label>
                <label className="confirmation-check">
                  <input
                    checked={resetAcknowledged}
                    onChange={(event) => setResetAcknowledged(event.target.checked)}
                    type="checkbox"
                  />
                  我理解重置后需要重新上传并确认赛程；操作会写入审计日志。
                </label>
                <button
                  className="danger-action"
                  disabled={resetName !== season.name || !resetAcknowledged || busy !== null}
                  onClick={() => void reset()}
                  type="button"
                >
                  {busy === "reset" ? "正在重置…" : "二次确认并重置"}
                </button>
              </div>
            )}
          </>
        )}
      </section>

      {error && <div className="form-error" role="alert">{error}</div>}
      {message && <div className="form-success" role="status">{message}</div>}
    </div>
  );
}

function Summary({ label, value, featured = false }: { label: string; value: number; featured?: boolean }) {
  return <span className={featured ? "featured" : ""}><small>{label}</small><strong>{value}</strong></span>;
}

function IssueGroup({
  title,
  tone,
  issues,
}: {
  title: string;
  tone: "error" | "warning";
  issues: Array<{ code: string; message: string; cell?: string; count?: number }>;
}) {
  return (
    <div className={`import-issue-group ${tone}`}>
      <strong>{title} · {issues.length}</strong>
      <div>
        {issues.map((issue, index) => (
          <p key={`${issue.code}-${issue.cell ?? ""}-${index}`}>
            <code>{issue.code}</code>
            <span>{issue.message}</span>
            {issue.cell && <small>{issue.cell}</small>}
            {typeof issue.count === "number" && issue.count > 1 && <small>× {issue.count}</small>}
          </p>
        ))}
      </div>
    </div>
  );
}

function formatBytes(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function batchStatus(batch: ScheduleImport) {
  if (batch.status === "CONFIRMED") return "已确认创建";
  return batch.summary.error_count > 0 ? "校验未通过" : "校验通过，待确认";
}
