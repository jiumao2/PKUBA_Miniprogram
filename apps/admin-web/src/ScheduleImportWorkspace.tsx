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
  ScheduleImport,
  ScheduleImportReadiness,
  ScheduleImportResetPreview,
  createAdminClient,
} from "@pkuba/api-client";

type AdminClient = ReturnType<typeof createAdminClient>;
type BusyAction = "context" | "download" | "upload" | "confirm" | "reset" | null;

const sheetSpecs = [
  {
    name: "填写说明",
    fields: ["格式版本", "填写规则", "错误处理"],
    example: "格式版本 2.0.0；文件无需签名，可从服务器模板开始填写。",
  },
  {
    name: "赛制定义",
    fields: [
      "组别代码",
      "小组代码",
      "小组名称",
      "小组排序",
      "签位代码",
      "签位名称",
      "种子序号",
    ],
    example: "men · A · A 组 · 1 · A1 · A 组 1 号签 · 1",
  },
  {
    name: "比赛清单",
    fields: [
      "比赛编号",
      "组别代码",
      "小组代码",
      "阶段",
      "轮次",
      "主方签位代码",
      "客方签位代码",
    ],
    example: "G001 · men · A · 小组赛 · 1 · A1 · A2",
  },
  {
    name: "赛程网格",
    fields: ["日期", "星期", "时段代码", "时段名称", "各场地代码列"],
    example: "2027-03-08 · 周一 · p1 · 第一时段 · court-1 = G001",
  },
] as const;

const statusLabels: Record<string, string> = {
  SETUP: "准备中",
  PRE_DRAW_PUBLIC: "抽签前公开",
  ACTIVE: "进行中",
  ARCHIVED: "已归档",
};

export function ScheduleImportWorkspace({
  account,
  client,
  seasons,
  season,
  onSeasonChange,
  onDataChanged,
  onOpenEditor,
}: {
  account: AdminAccount;
  client: AdminClient;
  seasons: AdminSeason[];
  season: AdminSeason;
  onSeasonChange: (seasonId: string) => void;
  onDataChanged: () => Promise<void>;
  onOpenEditor: () => void;
}) {
  const fileInput = useRef<HTMLInputElement>(null);
  const [readiness, setReadiness] = useState<ScheduleImportReadiness | null>(null);
  const [resetPreview, setResetPreview] = useState<ScheduleImportResetPreview | null>(null);
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

  const loadContext = useCallback(async () => {
    setBusy("context");
    setError(null);
    try {
      const [nextReadiness, nextReset] = await Promise.all([
        client.getScheduleImportReadiness(season.id),
        client.getScheduleImportResetPreview(season.id),
      ]);
      setReadiness(nextReadiness);
      setResetPreview(nextReset);
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "无法读取导入前置条件");
    } finally {
      setBusy(null);
    }
  }, [client, season.id]);

  useEffect(() => {
    setFile(null);
    setBatch(null);
    setAcknowledged(false);
    setResetName("");
    setResetAcknowledged(false);
    setMessage(null);
    if (fileInput.current) fileInput.current.value = "";
    void loadContext();
  }, [loadContext]);

  const errors = batch?.issues.filter((issue) => issue.severity === "ERROR") ?? [];
  const warnings = batch?.issues.filter((issue) => issue.severity === "WARNING") ?? [];
  const hasErrors = (batch?.summary.error_count ?? 0) > 0;
  const games = batch?.summary.games ?? [];
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
    try {
      const blob = await client.downloadScheduleTemplate(season.id);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `PKUBA_${season.year}_${season.name}_赛程模板.xlsx`;
      link.click();
      URL.revokeObjectURL(url);
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
    if (!batch || hasErrors || !acknowledged || !readiness) return;
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
    readiness?.ready ? "done" : "current",
    file ? "done" : readiness?.ready ? "current" : "waiting",
    batch ? (hasErrors ? "error" : "done") : "waiting",
    batch?.status === "CONFIRMED" ? "done" : batch && !hasErrors ? "current" : "waiting",
  ];

  return (
    <div className="schedule-import-workspace">
      <ol className="import-steps" aria-label="赛程导入步骤">
        {["格式与前置条件", "选择文件", "校验预览", "确认创建"].map((label, index) => (
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
            <h2 id="format-title">格式与前置条件</h2>
            <p>工作簿只描述本次新增的小组、签位与比赛；不会更新或删除现有赛程。</p>
          </div>
          <div className="season-actions">
            <label>
              操作赛季
              <select
                value={season.id}
                onChange={(event) => onSeasonChange(event.target.value)}
              >
                {seasons.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name} · {statusLabels[item.status] ?? item.status}
                  </option>
                ))}
              </select>
            </label>
            <button
              className="secondary-action"
              disabled={!readiness?.ready || busy !== null}
              onClick={() => void download()}
              type="button"
            >
              {busy === "download" ? "正在生成…" : "下载便捷模板"}
            </button>
          </div>
        </div>

        {readiness && (
          <div className="readiness-strip" aria-label="导入前置条件">
            <ReadinessMetric label="组别" value={readiness.division_count} />
            <ReadinessMetric label="球队" value={readiness.team_count} />
            <ReadinessMetric label="时段" value={readiness.period_count} />
            <ReadinessMetric label="场地" value={readiness.venue_count} />
            <ReadinessMetric label="开放网格行" value={readiness.open_grid_row_count} />
            <ReadinessMetric label="已有比赛" value={readiness.existing_game_count} />
            <span className={readiness.ready ? "readiness-ready" : "readiness-blocked"}>
              {readiness.ready ? "可以导入" : "尚未就绪"}
            </span>
          </div>
        )}
        {readiness && readiness.blockers.length > 0 && (
          <IssueGroup title="阻止导入" tone="error" issues={readiness.blockers} />
        )}

        <div className="sheet-spec-table" role="table" aria-label="XLSX 四张表字段说明">
          <div className="sheet-spec-row sheet-spec-head" role="row">
            <span>工作表</span>
            <span>固定字段</span>
            <span>示例</span>
          </div>
          {sheetSpecs.map((sheet) => (
            <div className="sheet-spec-row" role="row" key={sheet.name}>
              <strong>{sheet.name}</strong>
              <span className="field-tags">
                {sheet.fields.map((field) => <code key={field}>{field}</code>)}
              </span>
              <span>{sheet.example}</span>
            </div>
          ))}
        </div>
        <p className="format-note">
          比赛阶段仅支持小组赛、循环赛、淘汰赛、半决赛、决赛、保级赛。表格不填写日期以外的可调政策；新比赛统一默认“领队可调”，后续在赛程编辑器逐场修改。
        </p>
      </section>

      <section className="import-section" aria-labelledby="upload-title">
        <div className="import-section-heading compact">
          <div>
            <p className="eyebrow">步骤 2</p>
            <h2 id="upload-title">选择 XLSX 文件</h2>
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
              <p className="eyebrow">步骤 3</p>
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
                <span>{game.home_slot_label || game.home_slot_code}<small>vs {game.away_slot_label || game.away_slot_code}</small></span>
                <span>{game.date ?? "未定位"}<small>{game.start_time ?? game.period_name ?? "—"}</small></span>
                <span>{game.venue_name ?? "—"}<small>{game.cell}</small></span>
              </div>
            ))}
            {shownGames.length === 0 && <p className="table-empty">没有符合当前筛选条件的比赛。</p>}
          </div>

          {!hasErrors && batch.status !== "CONFIRMED" && (
            <div className="confirm-create">
              <div>
                <p className="eyebrow">步骤 4</p>
                <h3>确认新增正式赛程</h3>
                <p>确认时服务器会锁定赛季并重新校验；任一冲突都会使本次事务完整回滚。</p>
              </div>
              <label className="confirmation-check">
                <input
                  checked={acknowledged}
                  onChange={(event) => setAcknowledged(event.target.checked)}
                  type="checkbox"
                />
                我已核对将新增 {batch.summary.new_group_count} 个小组、{batch.summary.new_slot_count} 个签位和 {batch.summary.new_game_count} 场比赛；现有比赛不会被更新或删除。
              </label>
              <button
                className="primary-action"
                disabled={!acknowledged || busy !== null}
                onClick={() => void confirm()}
                type="button"
              >
                {busy === "confirm" ? "正在创建…" : "确认并创建"}
              </button>
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

function ReadinessMetric({ label, value }: { label: string; value: number }) {
  return <span><small>{label}</small><strong>{value}</strong></span>;
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
