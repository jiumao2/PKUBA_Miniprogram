import {
  type ChangeEvent,
  type DragEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import type {
  AdminAccount,
  AdminSeason,
  ScheduleDraft,
  ScheduleImport,
  ScheduleImportReadiness,
  ScheduleImportResetPreview,
  createAdminClient,
} from "@pkuba/api-client";

import {
  ScheduleGridEditor,
  type ScheduleGridValue,
} from "./ScheduleGridEditor";
import "./schedule-planner.css";

type AdminClient = ReturnType<typeof createAdminClient>;
type SaveStatus = "idle" | "dirty" | "saving" | "saved" | "conflict";
type InspectorTab = "pool" | "verify";

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
const headerCodes = new Set([
  "COLUMN_STRUCTURE_CHANGED",
  "GRID_COLUMN_GAP",
  "NO_GRID_COLUMNS",
  "TOO_MANY_GRID_COLUMNS",
  "UNKNOWN_GRID_PERIOD",
  "AMBIGUOUS_GRID_PERIOD",
  "EMPTY_GRID_VENUE",
  "GRID_VENUE_TOO_LONG",
  "DUPLICATE_GRID_COLUMN",
  "GRID_COLUMN_WITHOUT_HEADER",
  "GRID_DATES_CHANGED",
  "EXTRA_GRID_ROW",
]);
const capacityCodes = new Set(["CAPACITY_EXCEEDED"]);
const resourceCodes = new Set([
  "VENUE_OCCUPIED",
  "PARTICIPANT_TIME_CONFLICT",
  "FINAL_ONLY_COLUMN",
]);
const boundaryCodes = new Set([
  "GAME_CODE_ALREADY_EXISTS",
  "MATCHUP_ALREADY_EXISTS",
  "DATE_OUT_OF_RANGE",
  "FORMULA_FORBIDDEN",
  "SEASON_NOT_SETUP",
  "VERSION_CONFLICT",
]);

const statusLabels: Record<SaveStatus, string> = {
  idle: "草稿就绪",
  dirty: "有未保存更改",
  saving: "正在保存…",
  saved: "已保存",
  conflict: "版本冲突，请刷新",
};

function triggerDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.style.display = "none";
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1_000);
}

function normalizedMatchup(value: string) {
  return value
    .replace(/\s+/g, "")
    .replace(/[（）]/g, (item) => (item === "（" ? "(" : ")"))
    .toLowerCase();
}

export function SchedulePlannerWorkspace({
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
  const workingRef = useRef<ScheduleGridValue | null>(null);
  const serverVersionRef = useRef(0);
  const dirtyRef = useRef(false);
  const savePromiseRef = useRef<Promise<void> | null>(null);
  const [draft, setDraft] = useState<ScheduleDraft | null>(null);
  const [working, setWorking] = useState<ScheduleGridValue | null>(null);
  const [readiness, setReadiness] = useState<ScheduleImportReadiness | null>(null);
  const [resetPreview, setResetPreview] = useState<ScheduleImportResetPreview | null>(null);
  const [batch, setBatch] = useState<ScheduleImport | null>(null);
  const [saveStatus, setSaveStatus] = useState<SaveStatus>("idle");
  const [revision, setRevision] = useState(0);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [dragActive, setDragActive] = useState(false);
  const [inspectorTab, setInspectorTab] = useState<InspectorTab>("pool");
  const [focusMode, setFocusMode] = useState(false);
  const [inspectorVisible, setInspectorVisible] = useState(true);
  const [poolQuery, setPoolQuery] = useState("");
  const [poolDivision, setPoolDivision] = useState("all");
  const [poolStage, setPoolStage] = useState("all");
  const [acknowledged, setAcknowledged] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [resetName, setResetName] = useState("");
  const [resetAcknowledged, setResetAcknowledged] = useState(false);

  const applyServerDraft = useCallback((next: ScheduleDraft) => {
    const value = { columns: next.columns, cells: next.cells };
    setDraft(next);
    setWorking(value);
    workingRef.current = value;
    serverVersionRef.current = next.version;
    dirtyRef.current = false;
    setRevision(0);
    setSaveStatus("idle");
  }, []);

  const loadContext = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [nextDraft, nextReadiness, nextReset] = await Promise.all([
        client.getScheduleDraft(season.id),
        client.getScheduleImportReadiness(season.id),
        client.getScheduleImportResetPreview(season.id),
      ]);
      applyServerDraft(nextDraft);
      setReadiness(nextReadiness);
      setResetPreview(nextReset);
      setBatch(null);
      setAcknowledged(false);
      setInspectorTab("pool");
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "无法读取赛程草稿");
    } finally {
      setLoading(false);
    }
  }, [applyServerDraft, client, season.id]);

  useEffect(() => {
    setDraft(null);
    setWorking(null);
    workingRef.current = null;
    setMessage(null);
    setError(null);
    setResetName("");
    setResetAcknowledged(false);
    if (fileInput.current) fileInput.current.value = "";
    void loadContext();
  }, [loadContext]);

  useEffect(() => {
    if (!focusMode) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const leaveFocusMode = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape") setFocusMode(false);
    };
    window.addEventListener("keydown", leaveFocusMode);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", leaveFocusMode);
    };
  }, [focusMode]);

  const saveDraftNow = useCallback(async (): Promise<void> => {
    if (savePromiseRef.current) {
      await savePromiseRef.current;
      if (dirtyRef.current) await saveDraftNow();
      return;
    }
    const task = (async () => {
      while (dirtyRef.current && workingRef.current) {
        dirtyRef.current = false;
        const snapshot = workingRef.current;
        setSaveStatus("saving");
        try {
          const saved = await client.updateScheduleDraft(season.id, {
            expected_version: serverVersionRef.current,
            columns: snapshot.columns.map((column) => ({
              id: column.id,
              period_id: column.period_id,
              venue_name: column.venue_name,
              final_only: column.final_only,
            })),
            cells: snapshot.cells.map((cell) => ({
              column_id: cell.column_id,
              date: cell.date,
              matchup: cell.matchup,
              leader_adjustable: cell.leader_adjustable,
            })),
          });
          serverVersionRef.current = saved.version;
          setDraft((current) =>
            current
              ? {
                  ...current,
                  version: saved.version,
                  updated_at: saved.updated_at,
                  source_name: saved.source_name,
                  summary: saved.summary,
                  matchup_pool: saved.matchup_pool,
                }
              : saved,
          );
          setSaveStatus(dirtyRef.current ? "dirty" : "saved");
        } catch (reason: unknown) {
          dirtyRef.current = true;
          setSaveStatus("conflict");
          throw reason;
        }
      }
    })();
    savePromiseRef.current = task;
    try {
      await task;
    } finally {
      savePromiseRef.current = null;
    }
  }, [client, season.id]);

  useEffect(() => {
    if (!revision) return;
    const timer = window.setTimeout(() => {
      void saveDraftNow().catch((reason: unknown) => {
        setError(reason instanceof Error ? reason.message : "自动保存失败");
      });
    }, 700);
    return () => window.clearTimeout(timer);
  }, [revision, saveDraftNow]);

  const handleGridChange = (next: ScheduleGridValue) => {
    setWorking(next);
    workingRef.current = next;
    dirtyRef.current = true;
    setSaveStatus("dirty");
    setRevision((current) => current + 1);
    setBatch(null);
    setAcknowledged(false);
  };

  const handleNotice = (nextMessage: string) => {
    setMessage(nextMessage);
    window.setTimeout(
      () => setMessage((current) => (current === nextMessage ? null : current)),
      3_500,
    );
  };

  const downloadTemplate = async () => {
    setBusy("template");
    setError(null);
    try {
      const blob = await client.downloadScheduleTemplate(season.id);
      triggerDownload(blob, `PKUBA_${season.year}_${season.name}_赛程模板_v3.2.xlsx`);
      handleNotice("空白模板已开始下载；第三页表头和排期列可以直接修改");
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "模板下载失败");
    } finally {
      setBusy(null);
    }
  };

  const exportDraft = async () => {
    setBusy("export");
    setError(null);
    try {
      await saveDraftNow();
      const blob = await client.exportScheduleDraft(season.id);
      triggerDownload(blob, `PKUBA_${season.year}_${season.name}_赛程草稿.xlsx`);
      handleNotice("当前草稿已导出；逐场“领队不可调”状态不会写入 XLSX");
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "草稿导出失败");
    } finally {
      setBusy(null);
    }
  };

  const importFile = async (file: File) => {
    if (!draft || !working) return;
    if (!file.name.toLowerCase().endsWith(".xlsx")) {
      setError("请选择 .xlsx 文件");
      return;
    }
    if (
      working.cells.length &&
      !window.confirm(
        `当前在线草稿已有 ${working.cells.length} 场比赛。上传将整表替换草稿，是否继续？`,
      )
    ) {
      if (fileInput.current) fileInput.current.value = "";
      return;
    }
    setBusy("import");
    setError(null);
    try {
      await saveDraftNow();
      const imported = await client.importScheduleDraft(
        season.id,
        serverVersionRef.current,
        file,
      );
      applyServerDraft(imported);
      setBatch(null);
      setInspectorTab("pool");
      handleNotice(
        `已从 ${file.name} 载入 ${imported.cells.length} 场比赛，可继续在线调整`,
      );
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "XLSX 载入失败");
    } finally {
      setBusy(null);
      if (fileInput.current) fileInput.current.value = "";
    }
  };

  const validateDraft = async () => {
    if (!draft) return;
    setBusy("validate");
    setError(null);
    try {
      await saveDraftNow();
      const validated = await client.validateScheduleDraft(
        season.id,
        serverVersionRef.current,
      );
      setBatch(validated);
      setAcknowledged(false);
      setInspectorTab("verify");
      handleNotice(
        validated.summary.error_count
          ? `核对完成：发现 ${validated.summary.error_count} 项错误`
          : "核对完成：所有阻断性规则均通过",
      );
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "赛程核对失败");
    } finally {
      setBusy(null);
    }
  };

  const errors = batch?.issues.filter((issue) => issue.severity === "ERROR") ?? [];
  const warnings = batch?.issues.filter((issue) => issue.severity === "WARNING") ?? [];
  const errorCodes = new Set(errors.map((issue) => issue.code));
  const expectedGames =
    batch?.summary.prerequisites.expected_game_count ??
    draft?.summary.expected_game_count ??
    0;
  const coveredGames = batch?.summary.covered_game_count ?? 0;
  const checks = batch
    ? [
        {
          key: "matchups",
          label: "无缺漏或重复比赛",
          passed: ![...matchupIntegrityCodes].some((code) => errorCodes.has(code)),
          detail: "循环对阵完整；反向重复、漏用签位和重复使用签位均会阻止确认。",
        },
        {
          key: "count",
          label: "比赛数量正确",
          passed: coveredGames === expectedGames,
          detail: `赛制预计 ${expectedGames} 场，已有有效赛程与本草稿合计覆盖 ${coveredGames} 场。`,
        },
        {
          key: "capacity",
          label: "比赛容量正确",
          passed: ![...capacityCodes].some((code) => errorCodes.has(code)),
          detail: "逐日、逐标准时段计算，并计入既有比赛和有效预留。",
        },
        {
          key: "headers",
          label: "日期与列头可识别",
          passed: ![...headerCodes].some((code) => errorCodes.has(code)),
          detail: "日期连续；时间精确匹配赛季时段；场地非空；列连续且组合不重复。",
        },
        {
          key: "resources",
          label: "场地与参赛方无冲突",
          passed: ![...resourceCodes].some((code) => errorCodes.has(code)),
          detail: "同场地不重占，同一签位不同时参赛，仅决赛列没有放入其他阶段。",
        },
        {
          key: "boundary",
          label: "新增边界与并发安全",
          passed:
            ![...boundaryCodes].some((code) => errorCodes.has(code)) &&
            errors.length === 0,
          detail: "不覆盖已有比赛；日期、文件安全和赛季版本通过，确认时还会锁定并复核。",
        },
      ]
    : [];
  const checksPassed = Boolean(batch) && checks.every((check) => check.passed);

  const confirmBatch = async () => {
    if (!batch || !acknowledged || !checksPassed) return;
    setBusy("confirm");
    setError(null);
    try {
      const confirmed = await client.confirmScheduleImport(batch.id, {
        expected_season_version: season.version,
      });
      setBatch(confirmed);
      await onDataChanged();
      const nextDraft = await client.getScheduleDraft(season.id);
      applyServerDraft(nextDraft);
      setInspectorTab("verify");
      handleNotice(`已创建 ${confirmed.summary.new_game_count} 场正式比赛`);
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "确认创建失败");
    } finally {
      setBusy(null);
    }
  };

  const resetImports = async () => {
    if (!resetPreview) return;
    setBusy("reset");
    setError(null);
    try {
      const result = await client.resetScheduleImports(season.id, {
        expected_season_version: resetPreview.season_version,
        season_name: resetName,
      });
      handleNotice(`已撤销 ${result.game_count} 场导入比赛`);
      await onDataChanged();
      await loadContext();
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "重置失败");
    } finally {
      setBusy(null);
    }
  };

  const usedMatchups = useMemo(
    () =>
      new Set((working?.cells ?? []).map((cell) => normalizedMatchup(cell.matchup))),
    [working?.cells],
  );
  const poolDivisions = useMemo(
    () => [
      ...new Set((draft?.matchup_pool ?? []).map((item) => item.division_code)),
    ],
    [draft?.matchup_pool],
  );
  const poolStages = useMemo(
    () => [...new Set((draft?.matchup_pool ?? []).map((item) => item.stage_name))],
    [draft?.matchup_pool],
  );
  const pool = useMemo(() => {
    const query = poolQuery.trim().toLowerCase();
    return (draft?.matchup_pool ?? []).filter((item) => {
      if (poolDivision !== "all" && item.division_code !== poolDivision) return false;
      if (poolStage !== "all" && item.stage_name !== poolStage) return false;
      if (
        query &&
        !`${item.matchup} ${item.division_name} ${item.stage_name}`
          .toLowerCase()
          .includes(query)
      )
        return false;
      return true;
    });
  }, [draft?.matchup_pool, poolDivision, poolQuery, poolStage]);
  const unplacedCount = (draft?.matchup_pool ?? []).filter(
    (item) =>
      !item.already_formal &&
      !usedMatchups.has(normalizedMatchup(item.matchup)),
  ).length;
  const activeStep = batch?.status === "CONFIRMED" ? 3 : batch ? 2 : 1;

  if (loading || !draft || !working) {
    return <section className="schedule-planner-state">正在读取赛程草稿…</section>;
  }

  return (
    <div className={focusMode ? "schedule-planner-workspace focus-mode" : "schedule-planner-workspace"}>
      <header className="planner-contextbar">
        <div>
          <label htmlFor="planner-season">编排赛季</label>
          <select
            id="planner-season"
            value={season.id}
            onChange={(event) => onSeasonChange(event.target.value)}
          >
            {seasons.map((item) => (
              <option key={item.id} value={item.id}>{item.name}</option>
            ))}
          </select>
        </div>
        <ol className="planner-steps" aria-label="赛程编排步骤">
          {["编排草稿", "核对规则", "确认创建"].map((label, index) => (
            <li className={activeStep >= index + 1 ? "active" : ""} key={label}>
              <span>{index + 1}</span>{label}
            </li>
          ))}
        </ol>
        <div className={`draft-save-state ${saveStatus}`} role="status">
          <span />{statusLabels[saveStatus]} · v{serverVersionRef.current}
        </div>
      </header>

      {error && (
        <div className="planner-alert error" role="alert">
          <span>{error}</span><button type="button" onClick={() => setError(null)}>×</button>
        </div>
      )}
      {message && (
        <div className="planner-alert" role="status">
          <span>{message}</span><button type="button" onClick={() => setMessage(null)}>×</button>
        </div>
      )}
      {readiness && !readiness.template_ready && (
        <div className="planner-alert warning">
          <span>{readiness.template_blockers.map((item) => item.message).join("；")}</span>
          <button type="button" onClick={onOpenConfiguration}>前往赛季与组别</button>
        </div>
      )}

      <div className="planner-sourcebar">
        <div
          className={dragActive ? "planner-file-drop active" : "planner-file-drop"}
          onDragEnter={(event) => { event.preventDefault(); setDragActive(true); }}
          onDragOver={(event) => event.preventDefault()}
          onDragLeave={() => setDragActive(false)}
          onDrop={(event: DragEvent<HTMLDivElement>) => {
            event.preventDefault();
            setDragActive(false);
            const file = event.dataTransfer.files[0];
            if (file) void importFile(file);
          }}
        >
          <input
            ref={fileInput}
            type="file"
            accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            onChange={(event: ChangeEvent<HTMLInputElement>) => {
              const file = event.target.files?.[0];
              if (file) void importFile(file);
            }}
          />
          <button type="button" disabled={Boolean(busy)} onClick={() => fileInput.current?.click()}>
            上传 XLSX 到草稿
          </button>
          <span>或拖到这里；V3.2 三页为填写说明 / 签位定义（仅提示） / 赛程网格，非空草稿会先确认整表替换</span>
        </div>
        <div className="planner-source-actions">
          <button type="button" disabled={Boolean(busy) || !readiness?.template_ready} onClick={() => void downloadTemplate()}>
            下载空白模板
          </button>
          <button type="button" disabled={Boolean(busy) || !readiness?.template_ready} onClick={() => void exportDraft()}>
            导出当前草稿
          </button>
          <button className="primary" type="button" disabled={Boolean(busy) || saveStatus === "conflict"} onClick={() => void validateDraft()}>
            {busy === "validate" ? "正在核对…" : "保存并核对"}
          </button>
        </div>
      </div>

      <main className={inspectorVisible ? "planner-main" : "planner-main inspector-hidden"}>
        <div className="planner-grid-pane">
          <div className="planner-grid-heading">
            <div>
              <h2>赛程网格</h2>
              <p>{draft.dates.length} 天 · {working.columns.length} 列 · {working.cells.length} 场草稿比赛</p>
            </div>
            <div className="planner-grid-tools">
              <div className="planner-legend" aria-label="颜色图例">
                <span className="men">男篮</span>
                <span className="women">女篮</span>
                <span className="locked">领队不可调</span>
                <span className="final">仅决赛列</span>
              </div>
              <div className="planner-view-actions" aria-label="编辑器视图">
                <button
                  type="button"
                  aria-pressed={!inspectorVisible}
                  onClick={() => setInspectorVisible((current) => !current)}
                >
                  {inspectorVisible ? "收起待排/核对" : "显示待排/核对"}
                </button>
                <button
                  className="focus-mode-toggle"
                  type="button"
                  aria-pressed={focusMode}
                  onClick={() => setFocusMode((current) => !current)}
                >
                  {focusMode ? "退出专注编排" : "进入专注编排"}
                </button>
              </div>
            </div>
          </div>
          <ScheduleGridEditor
            key={`${draft.id}-${draft.source_name}-${revision === 0 ? draft.version : "working"}`}
            draft={draft}
            value={working}
            onChange={handleGridChange}
            onNotice={handleNotice}
            disabled={Boolean(busy) || saveStatus === "conflict"}
          />
        </div>

        {inspectorVisible && <aside className="planner-inspector">
          <div className="inspector-tabs" role="tablist">
            <button className={inspectorTab === "pool" ? "active" : ""} type="button" onClick={() => setInspectorTab("pool")}>
              待排比赛 <span>{unplacedCount}</span>
            </button>
            <button className={inspectorTab === "verify" ? "active" : ""} type="button" onClick={() => setInspectorTab("verify")}>
              核对 <span>{batch?.summary.error_count ?? "—"}</span>
            </button>
          </div>

          {inspectorTab === "pool" ? (
            <div className="matchup-pool">
              <p>拖到空白单元格，或在网格中直接输入。已排比赛会自动淡出。</p>
              <input aria-label="搜索待排比赛" placeholder="搜索对阵、组别或阶段" value={poolQuery} onChange={(event) => setPoolQuery(event.target.value)} />
              <div className="pool-filters">
                <select aria-label="筛选组别" value={poolDivision} onChange={(event) => setPoolDivision(event.target.value)}>
                  <option value="all">全部组别</option>
                  {poolDivisions.map((item) => <option key={item} value={item}>{item}</option>)}
                </select>
                <select aria-label="筛选阶段" value={poolStage} onChange={(event) => setPoolStage(event.target.value)}>
                  <option value="all">全部阶段</option>
                  {poolStages.map((item) => <option key={item} value={item}>{item}</option>)}
                </select>
              </div>
              <div className="pool-list">
                {pool.map((item) => {
                  const scheduled =
                    item.already_formal ||
                    usedMatchups.has(normalizedMatchup(item.matchup));
                  return (
                    <div
                      className={`pool-matchup ${item.gender === "WOMEN" ? "women" : "men"} ${scheduled ? "scheduled" : ""}`}
                      draggable={!scheduled && !busy}
                      key={item.key}
                      onDragStart={(event) => {
                        event.dataTransfer.effectAllowed = "move";
                        event.dataTransfer.setData("application/x-pkuba-matchup", item.matchup);
                      }}
                    >
                      <strong>{item.matchup}</strong>
                      <span>{item.division_name} · {item.stage_name}</span>
                      {scheduled && <small>{item.already_formal ? "已有正式比赛" : "已排入草稿"}</small>}
                    </div>
                  );
                })}
              </div>
            </div>
          ) : (
            <div className="verification-panel">
              {!batch ? (
                <div className="verification-empty">
                  <h3>尚未核对当前草稿</h3>
                  <p>系统会检查完整性、数量、容量、表头、资源冲突和新增边界。</p>
                  <button className="primary" type="button" disabled={Boolean(busy)} onClick={() => void validateDraft()}>保存并开始核对</button>
                </div>
              ) : batch.status === "CONFIRMED" ? (
                <div className="verification-success">
                  <span>✓</span>
                  <h3>已创建正式赛程</h3>
                  <p>本次新增 {batch.summary.new_game_count} 场比赛。</p>
                  <button className="primary" type="button" onClick={onOpenEditor}>前往赛程编辑</button>
                </div>
              ) : (
                <>
                  <div className="verification-summary">
                    <strong>{errors.length ? `${errors.length} 项错误` : "阻断性检查通过"}</strong>
                    <span>{warnings.length} 项警告 · {batch.summary.new_game_count} 场待创建</span>
                  </div>
                  <div className="verification-checks">
                    {checks.map((check) => (
                      <div className={check.passed ? "verification-check passed" : "verification-check failed"} key={check.key}>
                        <span>{check.passed ? "✓" : "!"}</span>
                        <div><strong>{check.label}</strong><p>{check.detail}</p></div>
                      </div>
                    ))}
                  </div>
                  {batch.issues.length > 0 && (
                    <details className="verification-issues" open={errors.length > 0}>
                      <summary>查看错误与警告（{batch.issues.length}）</summary>
                      <ul>
                        {batch.issues.map((issue, index) => (
                          <li className={issue.severity.toLowerCase()} key={`${issue.code}-${issue.cell}-${index}`}>
                            <strong>{issue.cell || issue.code}</strong><span>{issue.message}</span>
                          </li>
                        ))}
                      </ul>
                    </details>
                  )}
                  {batch.summary.games.length > 0 && (
                    <details className="verification-preview">
                      <summary>比赛预览（{batch.summary.games.length}）</summary>
                      <div>
                        {batch.summary.games.slice(0, 80).map((game) => (
                          <p key={game.code}>
                            <strong>{game.home_slot_code}vs{game.away_slot_code}</strong>
                            <span>{game.date} · {game.start_time} · {game.venue_name}</span>
                          </p>
                        ))}
                      </div>
                    </details>
                  )}
                  <label className="verification-acknowledgement">
                    <input type="checkbox" disabled={!checksPassed} checked={acknowledged} onChange={(event) => setAcknowledged(event.target.checked)} />
                    我已逐项核对以上结果，确认本次只新增所列比赛
                  </label>
                  <button className="confirm-schedule" type="button" disabled={!checksPassed || !acknowledged || Boolean(busy)} onClick={() => void confirmBatch()}>
                    {busy === "confirm" ? "正在创建…" : `确认创建 ${batch.summary.new_game_count} 场比赛`}
                  </button>
                </>
              )}
            </div>
          )}
        </aside>}
      </main>

      {account.role === "SUPERADMIN" && (
        <details className="planner-danger-zone">
          <summary>危险操作：重置本赛季已确认的导入</summary>
          <div>
            <p>只删除可追溯到已确认导入批次、且尚未被后续业务使用的比赛、签位和小组。在线草稿和赛季基础配置会保留。</p>
            {resetPreview?.blockers.map((blocker) => (
              <p className="danger-blocker" key={blocker.code}>{blocker.message}</p>
            ))}
            <label>输入赛季名称 <input value={resetName} onChange={(event) => setResetName(event.target.value)} /></label>
            <label><input type="checkbox" checked={resetAcknowledged} onChange={(event) => setResetAcknowledged(event.target.checked)} /> 我理解此操作会删除正式导入赛程</label>
            <button type="button" disabled={!resetPreview?.eligible || resetName !== season.name || !resetAcknowledged || Boolean(busy)} onClick={() => void resetImports()}>执行受保护重置</button>
          </div>
        </details>
      )}
    </div>
  );
}
