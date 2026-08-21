import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ApiError,
  createAdminClient,
  type ScoresheetDetail,
  type ScoresheetMutationContext,
  type ScoresheetQueueItem,
} from "@pkuba/api-client";
import {
  addScoreEvent,
  deleteScoreEvent,
  normalizeScoreEvents,
  REGION_LABELS,
  SCORE_BLOCKS,
  SCORESHEET_REGIONS,
  teamTotal,
  type ScoreEvent,
  type ScorePeriod,
  type ScoresheetDocument,
  type ScoresheetRegion,
  type ScoreValue,
  type TeamSide,
} from "@pkuba/scoresheet-domain";

import "./scoresheet.css";

type AdminClient = ReturnType<typeof createAdminClient>;

const STATUS_LABELS: Record<string, string> = {
  NO_SOURCE: "待上传",
  RECOGNITION_QUEUED: "等待识别",
  RECOGNIZING: "识别中",
  RETRY_WAIT: "等待重试",
  DRAFT: "人工核对",
  RECOGNITION_FAILED: "识别失败",
  READY: "可以发布",
  PUBLISHED: "已发布",
};

function clientId() {
  const key = "pkuba-scoresheet-web-client";
  const existing = window.localStorage.getItem(key);
  if (existing) return existing;
  const next = globalThis.crypto?.randomUUID?.() ?? `web-${Date.now()}-${Math.random()}`;
  window.localStorage.setItem(key, next);
  return next;
}

function errorMessage(reason: unknown) {
  return reason instanceof Error ? reason.message : "操作失败，请重试。";
}

function documentDiffPaths(left: unknown, right: unknown, path = "", result: string[] = []): string[] {
  if (result.length >= 8 || JSON.stringify(left) === JSON.stringify(right)) return result;
  if (Array.isArray(left) && Array.isArray(right)) {
    for (let index = 0; index < Math.max(left.length, right.length) && result.length < 8; index += 1) {
      documentDiffPaths(left[index], right[index], `${path}/${index}`, result);
    }
    return result;
  }
  if (left && right && typeof left === "object" && typeof right === "object") {
    const keys = new Set([...Object.keys(left), ...Object.keys(right)]);
    for (const key of keys) {
      if (result.length >= 8) break;
      documentDiffPaths((left as Record<string, unknown>)[key], (right as Record<string, unknown>)[key], `${path}/${key}`, result);
    }
    return result;
  }
  result.push(path || "/");
  return result;
}

function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

export function ScoresheetWorkspace({
  client,
  seasonId,
  accountRole,
}: {
  client: AdminClient;
  seasonId: string;
  accountRole: string;
}) {
  const [items, setItems] = useState<ScoresheetQueueItem[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("");
  const [loading, setLoading] = useState(true);
  const [notice, setNotice] = useState<string | null>(null);
  const [uploadingGame, setUploadingGame] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setItems(await client.listScoresheets(seasonId || undefined));
      setNotice(null);
    } catch (reason) {
      setNotice(errorMessage(reason));
    } finally {
      setLoading(false);
    }
  }, [client, seasonId]);

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), 5000);
    return () => window.clearInterval(timer);
  }, [load]);

  const visible = useMemo(
    () =>
      items.filter(
        (item) =>
          (!status || item.status === status) &&
          (!query || `${item.game_code}${item.game_label}`.toLowerCase().includes(query.toLowerCase())),
      ),
    [items, query, status],
  );

  const upload = async (item: ScoresheetQueueItem, file: File | null) => {
    if (!file) return;
    setUploadingGame(item.game_id);
    setNotice(null);
    try {
      await client.uploadAdminGameMedia(item.game_id, "SCORESHEET", true, file);
      await load();
      const refreshed = await client.listScoresheets(seasonId || undefined);
      const next = refreshed.find((row) => row.game_id === item.game_id);
      if (next?.scoresheet_id) setSelectedId(next.scoresheet_id);
    } catch (reason) {
      setNotice(errorMessage(reason));
    } finally {
      setUploadingGame(null);
    }
  };

  return (
    <div className="scoresheet-queue-shell">
      <header className="scoresheet-queue-header">
        <div>
          <p className="eyebrow">结构化记录表</p>
          <h2>识别、核对与统计发布</h2>
          <p>每场比赛保留一张当前原图；识别失败会自动退避重试，发布前仍须逐区人工核对。</p>
        </div>
        <button
          className="secondary-action"
          onClick={async () =>
            downloadBlob(
              await client.downloadSeasonScoresheetStats(seasonId),
              "赛季记录表统计.xlsx",
            )
          }
          type="button"
        >
          导出赛季 XLSX
        </button>
      </header>

      <div className="scoresheet-queue-filters">
        <input
          aria-label="搜索比赛"
          onChange={(event) => setQuery(event.target.value)}
          placeholder="搜索场次或球队"
          value={query}
        />
        <select aria-label="筛选记录表状态" onChange={(event) => setStatus(event.target.value)} value={status}>
          <option value="">全部状态</option>
          {Object.entries(STATUS_LABELS).map(([value, label]) => (
            <option key={value} value={value}>{label}</option>
          ))}
        </select>
        <span>{visible.length} 场</span>
      </div>

      {notice && <div className="scoresheet-inline-notice error" role="alert">{notice}</div>}
      {loading ? (
        <div className="scoresheet-empty">正在读取记录表队列…</div>
      ) : (
        <div className="scoresheet-queue-table" role="table">
          <div className="scoresheet-queue-row heading" role="row">
            <span>比赛</span><span>日期</span><span>处理状态</span><span>发布</span><span>操作</span>
          </div>
          {visible.map((item) => (
            <div className="scoresheet-queue-row" key={item.game_id} role="row">
              <div><strong>{item.game_code}</strong><small>{item.game_label}</small></div>
              <span>{item.date}</span>
              <div>
                <span className={`scoresheet-status ${item.status.toLowerCase()}`}>
                  {STATUS_LABELS[item.status] ?? item.status}
                </span>
                {item.recognition_status && (
                  <RecognitionQueueMeta item={item} />
                )}
              </div>
              <span>{item.publication_number ? `v${item.publication_number}` : "未发布"}</span>
              <div className="scoresheet-row-actions">
                {item.scoresheet_id ? (
                  <button onClick={() => setSelectedId(item.scoresheet_id)} type="button">打开工作台</button>
                ) : (
                  <label className={uploadingGame === item.game_id ? "upload-label disabled" : "upload-label"}>
                    {uploadingGame === item.game_id ? "上传中…" : "上传原图"}
                    <input
                      accept="image/jpeg,image/png,image/webp"
                      disabled={uploadingGame === item.game_id}
                      onChange={(event) => void upload(item, event.target.files?.[0] ?? null)}
                      type="file"
                    />
                  </label>
                )}
              </div>
            </div>
          ))}
          {!visible.length && <div className="scoresheet-empty">没有符合条件的比赛。</div>}
        </div>
      )}

      {selectedId && (
        <ScoresheetEditor
          accountRole={accountRole}
          client={client}
          onClose={() => {
            setSelectedId(null);
            void load();
          }}
          scoresheetId={selectedId}
        />
      )}
    </div>
  );
}

function RecognitionQueueMeta({ item }: { item: ScoresheetQueueItem }) {
  const countdown = useRetryCountdown(item.next_attempt_at);
  return (
    <small>
      {item.recognition_status === "RETRY_WAIT" ? "重试" : "识别"} {item.recognition_attempt}/{item.recognition_max_attempts}
      {countdown !== null && ` · ${countdown} 秒`}
    </small>
  );
}

function RecognitionStrip({ recognition, readOnly, onStop }: {
  recognition: NonNullable<ScoresheetDetail["recognition"]>;
  readOnly: boolean;
  onStop: () => Promise<void>;
}) {
  const countdown = useRetryCountdown(recognition.next_attempt_at);
  const active = ["QUEUED", "RUNNING", "RETRY_WAIT"].includes(recognition.status);
  const statusText = recognition.status === "RETRY_WAIT" && countdown !== null
    ? `${countdown} 秒后自动重试`
    : recognition.status === "RUNNING"
      ? "正在读取完整记录表"
      : recognition.status === "FAILED"
        ? "四次识别均未成功，可完整手工录入或重传原图"
        : recognition.status;
  return (
    <div className={`recognition-strip ${recognition.status.toLowerCase()}`}>
      <div><strong>自动识别 · 第 {Math.max(1, recognition.attempt_count || 1)}/{recognition.max_attempts} 次</strong><span>{statusText}</span></div>
      {active && !readOnly && <button className="danger-quiet" onClick={() => void onStop()} type="button">停止剩余重试</button>}
    </div>
  );
}

function useRetryCountdown(nextAttemptAt: string | null) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (!nextAttemptAt) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [nextAttemptAt]);
  return nextAttemptAt
    ? Math.max(0, Math.ceil((new Date(nextAttemptAt).getTime() - now) / 1000))
    : null;
}

function ScoresheetEditor({
  client,
  scoresheetId,
  accountRole,
  onClose,
}: {
  client: AdminClient;
  scoresheetId: string;
  accountRole: string;
  onClose: () => void;
}) {
  const [sheet, setSheet] = useState<ScoresheetDetail | null>(null);
  const [region, setRegion] = useState<ScoresheetRegion>("SOURCE_GAME");
  const [selectedScoreId, setSelectedScoreId] = useState("");
  const [leaseToken, setLeaseToken] = useState<string | null>(null);
  const [readOnly, setReadOnly] = useState(true);
  const [saveState, setSaveState] = useState("正在连接");
  const [notice, setNotice] = useState<string | null>(null);
  const [sourceZoom, setSourceZoom] = useState(1);
  const [sourceRotation, setSourceRotation] = useState(0);
  const [sourceOpacity, setSourceOpacity] = useState(1);
  const [leftPaneWidth, setLeftPaneWidth] = useState(420);
  const [rightPaneWidth, setRightPaneWidth] = useState(380);
  const [history, setHistory] = useState<ScoresheetDocument[]>([]);
  const [future, setFuture] = useState<ScoresheetDocument[]>([]);
  const [conflict, setConflict] = useState<{ local: ScoresheetDocument; server: ScoresheetDetail } | null>(null);
  const idRef = useRef(clientId());
  const serverRef = useRef<ScoresheetDetail | null>(null);
  const leaseRef = useRef<string | null>(null);
  const pendingRef = useRef<ScoresheetDocument | null>(null);
  const pendingBaseVersionRef = useRef<number | null>(null);
  const recoveryRef = useRef<{ local: ScoresheetDocument; baseVersion: number } | null>(null);
  const saveTimer = useRef<number | null>(null);
  const savingRef = useRef(false);

  const applyServer = useCallback((next: ScoresheetDetail, preservePending = false) => {
    serverRef.current = next;
    setSheet((current) => ({
      ...next,
      draft: preservePending && pendingRef.current ? pendingRef.current : next.draft,
    }));
  }, []);

  const load = useCallback(async () => {
    const next = await client.getScoresheet(scoresheetId);
    applyServer(next);
    return next;
  }, [applyServer, client, scoresheetId]);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const next = await load();
        const acquired = await client.acquireScoresheetLease(scoresheetId, idRef.current, "WEB");
        if (cancelled) return;
        if (acquired.read_only || !acquired.lease_token) {
          setReadOnly(true);
          setSaveState(`只读 · ${acquired.holder.username} 正在通过${acquired.holder.surface === "WEB" ? "网页" : "小程序"}编辑`);
        } else {
          leaseRef.current = acquired.lease_token;
          setLeaseToken(acquired.lease_token);
          setReadOnly(false);
          setSaveState("已保存");
        }
        applyServer(next);
      } catch (reason) {
        setNotice(errorMessage(reason));
      }
    })();
    return () => {
      cancelled = true;
      if (saveTimer.current) window.clearTimeout(saveTimer.current);
    };
  }, [applyServer, client, load, scoresheetId]);

  const mutationContext = useCallback((): ScoresheetMutationContext | null => {
    const server = serverRef.current;
    const token = leaseRef.current;
    if (!server || !token) return null;
    return {
      expected_version: server.draft_version,
      lease_token: token,
      client_id: idRef.current,
      surface: "WEB",
    };
  }, []);

  const flush = useCallback(async (changeType = "FIELD_EDIT", explicitSave = false) => {
    if (savingRef.current || !pendingRef.current) return;
    const context = mutationContext();
    if (!context || !navigator.onLine) {
      setSaveState(navigator.onLine ? "只读" : "网络中断 · 已保留未保存输入");
      return;
    }
    const document = pendingRef.current;
    pendingRef.current = null;
    pendingBaseVersionRef.current = null;
    savingRef.current = true;
    setSaveState("保存中…");
    try {
      const result = await client.saveScoresheetDraft(
        scoresheetId,
        context,
        [{ path: "/", operation: "SET", value: document }],
        { changeType, explicitSave },
      );
      applyServer(result, Boolean(pendingRef.current));
      pendingBaseVersionRef.current = pendingRef.current ? result.draft_version : null;
      setSaveState(pendingRef.current ? "等待保存…" : "已保存");
      setNotice(null);
    } catch (reason) {
      const unsaved = pendingRef.current ?? document;
      pendingRef.current = null;
      if (reason instanceof ApiError && ["VERSION_CONFLICT", "LEASE_LOST", "LEASE_REQUIRED"].includes(reason.code ?? "")) {
        const server = await client.getScoresheet(scoresheetId);
        if (reason.code === "VERSION_CONFLICT") {
          pendingBaseVersionRef.current = null;
          setConflict({ local: unsaved, server });
          setSaveState("存在跨端差异");
        } else {
          leaseRef.current = null;
          setLeaseToken(null);
          setReadOnly(true);
          if (server.draft_version === context.expected_version) {
            recoveryRef.current = { local: unsaved, baseVersion: server.draft_version };
            pendingBaseVersionRef.current = null;
            serverRef.current = server;
            setSheet({ ...server, draft: unsaved });
            setSaveState("编辑权已失效 · 本地输入已保留，接手后恢复");
          } else {
            pendingBaseVersionRef.current = null;
            setConflict({ local: unsaved, server });
            setSaveState("编辑权已失效 · 发现跨端差异");
          }
        }
      } else {
        pendingRef.current = unsaved;
        pendingBaseVersionRef.current = context.expected_version;
        setSaveState("保存失败 · 输入仍保留");
        setNotice(errorMessage(reason));
      }
    } finally {
      savingRef.current = false;
      if (pendingRef.current && navigator.onLine) {
        saveTimer.current = window.setTimeout(() => void flush(), 50);
      }
    }
  }, [applyServer, client, mutationContext, scoresheetId]);

  const drainPending = useCallback(async (changeType = "EXPLICIT_SAVE") => {
    if (saveTimer.current) window.clearTimeout(saveTimer.current);
    for (let attempt = 0; attempt < 200; attempt += 1) {
      if (savingRef.current) {
        await new Promise<void>((resolve) => window.setTimeout(resolve, 25));
        continue;
      }
      if (!pendingRef.current) return true;
      if (!navigator.onLine || !leaseRef.current) return false;
      await flush(changeType, true);
    }
    return !pendingRef.current && !savingRef.current;
  }, [flush]);

  const queueDocument = useCallback((next: ScoresheetDocument, previous: ScoresheetDocument, immediate = false, changeType = "FIELD_EDIT") => {
    if (readOnly || !navigator.onLine) return;
    setHistory((rows) => [...rows.slice(-49), previous]);
    setFuture([]);
    if (!pendingRef.current) pendingBaseVersionRef.current = serverRef.current?.draft_version ?? null;
    pendingRef.current = next;
    setSheet((current) => current ? { ...current, draft: next } : current);
    setSaveState("等待保存…");
    if (saveTimer.current) window.clearTimeout(saveTimer.current);
    if (immediate) void flush(changeType);
    else saveTimer.current = window.setTimeout(() => void flush(changeType), 750);
  }, [flush, readOnly]);

  const changePath = useCallback((path: string, value: unknown, immediate = false) => {
    if (!sheet) return;
    const previous = sheet.draft;
    const next = structuredClone(previous);
    const parts = path.split("/").filter(Boolean);
    let cursor: any = next;
    for (const part of parts.slice(0, -1)) cursor = cursor[Number.isInteger(Number(part)) ? Number(part) : part];
    cursor[parts.at(-1)!] = value;
    queueDocument(next, previous, immediate);
  }, [queueDocument, sheet]);

  const sync = useCallback(async () => {
    const current = serverRef.current;
    if (!current) return;
    try {
      const update = await client.syncScoresheet(scoresheetId, current.draft_version, current.event_sequence);
      if ((update.events.length || update.requires_full_reload) && !savingRef.current) {
        const next = await client.getScoresheet(scoresheetId);
        if (!pendingRef.current) {
          applyServer(next);
        } else if (pendingBaseVersionRef.current === next.draft_version) {
          serverRef.current = next;
        } else {
          const local = pendingRef.current;
          pendingRef.current = null;
          pendingBaseVersionRef.current = null;
          setConflict({ local, server: next });
          setSaveState("存在跨端差异");
        }
      }
      if (
        leaseRef.current &&
        (!update.lease || update.lease.client_id !== idRef.current || update.lease.surface !== "WEB")
      ) {
        if (pendingRef.current) {
          recoveryRef.current = {
            local: pendingRef.current,
            baseVersion: pendingBaseVersionRef.current ?? current.draft_version,
          };
          pendingRef.current = null;
          pendingBaseVersionRef.current = null;
        }
        leaseRef.current = null;
        setLeaseToken(null);
        setReadOnly(true);
        setSaveState("编辑权已转移 · 本地输入已保留");
      }
      if (!leaseRef.current && !update.lease && !recoveryRef.current) setSaveState("编辑权已释放 · 可接手编辑");
    } catch (reason) {
      if (navigator.onLine) setNotice(errorMessage(reason));
    }
  }, [applyServer, client, scoresheetId]);

  useEffect(() => {
    const poll = window.setInterval(() => void sync(), 2000);
    const heartbeat = window.setInterval(() => {
      const token = leaseRef.current;
      if (!token) return;
      void client
        .heartbeatScoresheetLease(scoresheetId, token, idRef.current, "WEB")
        .catch(() => {
          if (pendingRef.current) {
            recoveryRef.current = {
              local: pendingRef.current,
              baseVersion: pendingBaseVersionRef.current ?? serverRef.current?.draft_version ?? 0,
            };
            pendingRef.current = null;
            pendingBaseVersionRef.current = null;
          }
          leaseRef.current = null;
          setLeaseToken(null);
          setReadOnly(true);
          setSaveState("编辑权已失效 · 本地输入已保留");
        });
    }, 15000);
    const foreground = () => {
      if (document.visibilityState === "visible") void sync();
    };
    const online = () => {
      void sync().then(async () => {
        if (pendingRef.current && leaseRef.current) await drainPending("NETWORK_RECOVERY");
      });
    };
    const offline = () => setSaveState("网络中断 · 已保留未保存输入");
    document.addEventListener("visibilitychange", foreground);
    window.addEventListener("online", online);
    window.addEventListener("offline", offline);
    return () => {
      window.clearInterval(poll);
      window.clearInterval(heartbeat);
      document.removeEventListener("visibilitychange", foreground);
      window.removeEventListener("online", online);
      window.removeEventListener("offline", offline);
    };
  }, [client, drainPending, scoresheetId, sync]);

  const close = async () => {
    if (!(await drainPending())) {
      setNotice("仍有未保存输入，暂未关闭编辑器。请恢复网络后重试。");
      return;
    }
    const token = leaseRef.current;
    if (token) {
      await client.releaseScoresheetLease(scoresheetId, token, idRef.current, "WEB").catch(() => undefined);
    }
    onClose();
  };

  const handoff = async () => {
    if (!(await drainPending())) {
      setNotice("仍有未保存输入，暂未释放编辑权。请恢复网络后重试。");
      return;
    }
    const token = leaseRef.current;
    if (!token) return;
    await client.releaseScoresheetLease(scoresheetId, token, idRef.current, "WEB");
    leaseRef.current = null;
    setLeaseToken(null);
    setReadOnly(true);
    setSaveState("已保存并释放 · 可在另一端接手");
  };

  const takeOver = async (force = false) => {
    try {
      const acquired = force
        ? await client.forceScoresheetLease(scoresheetId, idRef.current, "WEB")
        : await client.acquireScoresheetLease(scoresheetId, idRef.current, "WEB");
      if (acquired.read_only || !acquired.lease_token) {
        setSaveState(`${acquired.holder.username} 仍在编辑`);
        return;
      }
      leaseRef.current = acquired.lease_token;
      setLeaseToken(acquired.lease_token);
      setReadOnly(false);
      setSaveState("已取得编辑权");
      const next = await client.getScoresheet(scoresheetId);
      const recovery = recoveryRef.current;
      recoveryRef.current = null;
      if (!recovery) {
        applyServer(next);
        if (conflict) setConflict({ ...conflict, server: next });
      } else if (next.draft_version === recovery.baseVersion) {
        serverRef.current = next;
        pendingRef.current = recovery.local;
        pendingBaseVersionRef.current = next.draft_version;
        setSheet({ ...next, draft: recovery.local });
        await drainPending("LEASE_RECOVERY");
      } else {
        applyServer(next);
        setConflict({ local: recovery.local, server: next });
        setSaveState("重新接手时发现跨端差异");
      }
    } catch (reason) {
      setNotice(errorMessage(reason));
    }
  };

  const beginResize = (target: "left" | "right", startX: number) => {
    const initial = target === "left" ? leftPaneWidth : rightPaneWidth;
    const move = (event: PointerEvent) => {
      const delta = event.clientX - startX;
      if (target === "left") setLeftPaneWidth(Math.max(300, Math.min(720, initial + delta)));
      else setRightPaneWidth(Math.max(330, Math.min(640, initial - delta)));
    };
    const stop = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", stop);
      document.body.classList.remove("scoresheet-resizing");
    };
    document.body.classList.add("scoresheet-resizing");
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stop, { once: true });
  };

  const command = async (
    action: "review" | "validate" | "warnings" | "publish",
    warningIds: string[] = [],
  ) => {
    const context = mutationContext();
    if (!context) return;
    try {
      if (!(await drainPending())) return;
      let result: ScoresheetDetail;
      if (action === "review") {
        result = await client.reviewScoresheetRegion(scoresheetId, region, mutationContext()!, true);
      } else if (action === "validate") {
        result = await client.validateScoresheet(scoresheetId, mutationContext()!);
      } else if (action === "warnings") {
        result = await client.acknowledgeScoresheetWarnings(
          scoresheetId,
          mutationContext()!,
          warningIds,
        );
      } else {
        result = await client.publishScoresheet(scoresheetId, mutationContext()!);
      }
      applyServer(result);
      if (action === "publish") {
        leaseRef.current = null;
        setLeaseToken(null);
        setReadOnly(true);
        setSaveState("已发布");
      }
      setNotice(action === "publish" ? "记录表已发布，正式比分和统计已同步。" : null);
    } catch (reason) {
      setNotice(errorMessage(reason));
    }
  };

  const undo = (direction: "undo" | "redo") => {
    if (!sheet || readOnly) return;
    const source = direction === "undo" ? history : future;
    const target = source.at(-1);
    if (!target) return;
    if (direction === "undo") {
      setHistory(source.slice(0, -1));
      setFuture((rows) => [...rows, sheet.draft]);
    } else {
      setFuture(source.slice(0, -1));
      setHistory((rows) => [...rows.slice(-49), sheet.draft]);
    }
    pendingRef.current = target;
    pendingBaseVersionRef.current = serverRef.current?.draft_version ?? null;
    setSheet({ ...sheet, draft: target });
    void flush(direction.toUpperCase());
  };

  if (!sheet) {
    return <div className="scoresheet-editor-overlay"><div className="scoresheet-editor-loading">正在打开记录表工作台…</div></div>;
  }

  const errors = sheet.validation_report.errors ?? [];
  const warnings = sheet.validation_report.warnings ?? [];
  const currentIssues = [...errors, ...warnings].filter((issue) => issue.region === region);
  const reviewed = sheet.reviewed_regions[region]?.draft_version === sheet.draft_version;
  const validationReady = sheet.status === "READY"
    && sheet.validation_draft_version === sheet.draft_version;

  return (
    <div className="scoresheet-editor-overlay" role="dialog" aria-label="记录表编辑器" aria-modal="true">
      <header className="scoresheet-editor-topbar">
        <button className="editor-back" onClick={() => void close()} type="button">← 返回比赛资料</button>
        <div><strong>{String(sheet.game.label)}</strong><small>草稿 v{sheet.draft_version} · 来源 v{sheet.source_version}</small></div>
        <div className="editor-save-state"><span className={readOnly ? "readonly" : "editing"}>{saveState}</span></div>
        <div className="editor-top-actions">
          <button disabled={readOnly || !navigator.onLine || !history.length} onClick={() => undo("undo")} type="button">撤销</button>
          <button disabled={readOnly || !navigator.onLine || !future.length} onClick={() => undo("redo")} type="button">重做</button>
          {readOnly && <button onClick={() => void takeOver(false)} type="button">接手编辑</button>}
          {readOnly && accountRole === "SUPERADMIN" && (
            <button className="danger-quiet" onClick={() => window.confirm("强制接管会让旧客户端立即只读，确认继续？") && void takeOver(true)} type="button">强制接管</button>
          )}
          {!readOnly && (
            <button onClick={() => void handoff()} type="button">交接到另一端</button>
          )}
        </div>
      </header>

      <nav className="scoresheet-region-tabs" aria-label="核对区域">
        {SCORESHEET_REGIONS.map((item) => {
          const count = [...errors, ...warnings].filter((issue) => issue.region === item).length;
          const done = sheet.reviewed_regions[item]?.draft_version === sheet.draft_version;
          return (
            <button className={region === item ? "active" : ""} key={item} onClick={() => setRegion(item)} type="button">
              <span>{done ? "✓" : SCORESHEET_REGIONS.indexOf(item) + 1}</span>
              {REGION_LABELS[item]}
              {count > 0 && <em>{count}</em>}
            </button>
          );
        })}
      </nav>

      {notice && <div className="editor-notice" role="alert">{notice}</div>}
      {sheet.recognition && (
        <RecognitionStrip
          onStop={async () => {
            if (!(await drainPending())) return;
            try {
              applyServer(await client.stopScoresheetRecognition(scoresheetId));
            } catch (reason) {
              setNotice(errorMessage(reason));
            }
          }}
          readOnly={readOnly || !navigator.onLine}
          recognition={sheet.recognition}
        />
      )}
      <div className="scoresheet-three-pane" style={{ gridTemplateColumns: `${leftPaneWidth}px 6px minmax(430px, 1fr) 6px ${rightPaneWidth}px` }}>
        <section className="scoresheet-source-pane">
          <header><strong>记录表原图</strong><div><button onClick={() => setSourceZoom((value) => Math.max(.5, value - .1))}>−</button><span>{Math.round(sourceZoom * 100)}%</span><button onClick={() => setSourceZoom((value) => Math.min(3, value + .1))}>＋</button><button onClick={() => setSourceRotation((value) => value + 90)}>旋转</button><label className="source-opacity">透明度<input aria-label="原图透明度" max="100" min="25" onChange={(event) => setSourceOpacity(Number(event.target.value) / 100)} type="range" value={Math.round(sourceOpacity * 100)} /></label></div></header>
          <div className="source-canvas">
            {sheet.source ? (
              <img alt="记录表原图" src={sheet.source.url} style={{ opacity: sourceOpacity, transform: `scale(${sourceZoom}) rotate(${sourceRotation}deg)` }} />
            ) : <div className="scoresheet-empty">原图已缺失</div>}
          </div>
          {!readOnly && navigator.onLine && sheet.source && (
            <label className="replace-source-label">
              重传新原图
              <input accept="image/jpeg,image/png,image/webp" onChange={async (event) => {
                const file = event.target.files?.[0];
                if (!file || !window.confirm("重传会生成新的来源版本并重置识别额度，确认继续？")) return;
                await client.replaceAdminGameMedia(sheet.source!.id, sheet.source!.version, true, file);
                await load();
              }} type="file" />
            </label>
          )}
        </section>

        <div aria-hidden="true" className="pane-resizer" onPointerDown={(event) => beginResize("left", event.clientX)} />

        <section className="scoresheet-standard-pane">
          <header><strong>标准记录表</strong><span>FIBA 2024 · 单页 A4</span></header>
          {region === "RUNNING_SCORE" ? (
            <PaperScoreGrid
              events={sheet.draft.running_score}
              issues={currentIssues.map((issue) => issue.path)}
              onSelect={setSelectedScoreId}
              selectedId={selectedScoreId}
            />
          ) : (
            <StandardSheet document={sheet.draft} region={region} />
          )}
        </section>

        <div aria-hidden="true" className="pane-resizer" onPointerDown={(event) => beginResize("right", event.clientX)} />

        <aside className="scoresheet-inspector-pane">
          <header><div><p className="eyebrow">当前区域</p><h2>{REGION_LABELS[region]}</h2></div><span className={reviewed ? "region-reviewed" : "region-unreviewed"}>{reviewed ? "已核对" : "待核对"}</span></header>
          <RegionEditor
            changePath={changePath}
            document={sheet.draft}
            readOnly={readOnly || !navigator.onLine}
            region={region}
          />
          {region === "RUNNING_SCORE" && !readOnly && navigator.onLine && (
            <ScoreQuickBar
              document={sheet.draft}
              onChange={(events) => changePath("/running_score", events, true)}
              onSelect={setSelectedScoreId}
              selectedId={selectedScoreId}
            />
          )}
          <div className="validation-issues">
            <div className="inspector-section-title"><strong>校验问题</strong><span>{currentIssues.length}</span></div>
            {currentIssues.map((issue) => (
              <div className={`validation-issue ${issue.severity.toLowerCase()}`} key={issue.id}>
                <span>{issue.severity === "ERROR" ? "错误" : "提醒"}</span><p>{issue.message}</p>
                {issue.severity === "WARNING" && (
                  sheet.acknowledged_warnings.includes(issue.id)
                    ? <em>已确认</em>
                    : <button disabled={readOnly || !navigator.onLine} onClick={() => void command("warnings", [issue.id])} type="button">确认此项</button>
                )}
              </div>
            ))}
            {!currentIssues.length && <p className="quiet-copy">当前区域没有服务端校验问题。</p>}
          </div>
          <div className="inspector-actions">
            <button className="secondary-action" disabled={readOnly || !navigator.onLine} onClick={() => void command("review")} type="button">{reviewed ? "重新确认本区域" : "本区域已核对"}</button>
            <button disabled={readOnly || !navigator.onLine} onClick={() => void command("validate")} type="button">执行服务端校验</button>
            <button className="publish-action" disabled={readOnly || !navigator.onLine || !validationReady || errors.length > 0 || warnings.some((item) => !sheet.acknowledged_warnings.includes(item.id)) || SCORESHEET_REGIONS.some((item) => sheet.reviewed_regions[item]?.draft_version !== sheet.draft_version)} onClick={() => window.confirm("确认一次发布正式比分与统计？") && void command("publish")} type="button">发布正式数据</button>
            <div className="export-actions">
              <button onClick={async () => downloadBlob(await client.downloadScoresheetPdf(scoresheetId), `${String(sheet.game.code)}-记录表.pdf`)} type="button">PDF</button>
              <button disabled={!sheet.publication} onClick={async () => downloadBlob(await client.downloadScoresheetCsv(scoresheetId), `${String(sheet.game.code)}-统计.csv`)} type="button">CSV</button>
            </div>
          </div>
        </aside>
      </div>

      {conflict && (
        <div className="scoresheet-conflict-dialog">
          <div><p className="eyebrow">跨端冲突</p><h2>服务器草稿已变化</h2><p>不会自动覆盖。以下字段存在差异：</p><ul>{documentDiffPaths(conflict.local, conflict.server.draft).map((path) => <li key={path}>{path}</li>)}</ul><p>请选择保留服务器值，或在重新取得的当前版本上提交本地值。</p><div><button onClick={() => { pendingRef.current = null; pendingBaseVersionRef.current = null; applyServer(conflict.server); setConflict(null); setSaveState("已采用服务器版本"); }} type="button">采用服务器值</button><button className="publish-action" disabled={readOnly} onClick={() => { serverRef.current = conflict.server; pendingRef.current = conflict.local; pendingBaseVersionRef.current = conflict.server.draft_version; setConflict(null); void flush("CONFLICT_RESOLVED_LOCAL"); }} type="button">提交本地值</button></div></div>
        </div>
      )}
    </div>
  );
}

function StandardSheet({ document, region }: { document: ScoresheetDocument; region: ScoresheetRegion }) {
  return (
    <div className="standard-sheet-page">
      <div className={region === "SOURCE_GAME" ? "sheet-section active" : "sheet-section"}>
        <strong>{document.teams.A.name} vs {document.teams.B.name}</strong>
        <span>{document.game.competition} · {document.game.date} · {document.game.venue}</span>
      </div>
      {(["A", "B"] as TeamSide[]).map((side) => (
        <div className={region === `TEAM_${side}` ? "sheet-team active" : "sheet-team"} key={side}>
          <header>球队 {side} · {document.teams[side].name}</header>
          {document.teams[side].players.map((player) => (
            <div key={player.player_id}><span>{player.jersey_number || "–"}</span><strong>{player.name}</strong><em>{player.starter ? "首发" : player.appeared ? "出场" : ""}</em><small>{player.fouls.map((foul) => typeof foul === "string" ? foul : foul.code).join(" ")}</small></div>
          ))}
        </div>
      ))}
      <div className={region === "SUMMARY" ? "sheet-summary active" : "sheet-summary"}>
        <strong>最终比分</strong><b>{document.summary.final_score.A ?? "–"} : {document.summary.final_score.B ?? "–"}</b>
      </div>
      <div className={region === "OFFICIALS" ? "sheet-officials active" : "sheet-officials"}>
        记录员 {String(document.officials.scorer || "未填写")} · 计时员 {String(document.officials.timer || "未填写")}
      </div>
    </div>
  );
}

function RegionEditor({ document, region, readOnly, changePath }: { document: ScoresheetDocument; region: ScoresheetRegion; readOnly: boolean; changePath: (path: string, value: unknown, immediate?: boolean) => void }) {
  if (region === "SOURCE_GAME") {
    return <div className="region-form">{["competition", "date", "scheduled_time", "game_number", "venue", "crew_chief", "umpire_1", "umpire_2"].map((key) => <label key={key}><span>{key}</span><input disabled={readOnly} onChange={(event) => changePath(`/game/${key}`, event.target.value)} value={document.game[key] ?? ""} /></label>)}</div>;
  }
  if (region === "TEAM_A" || region === "TEAM_B") {
    const side = region.endsWith("A") ? "A" : "B";
    return (
      <TeamRegionEditor
        changePath={changePath}
        document={document}
        readOnly={readOnly}
        side={side}
      />
    );
  }
  if (region === "RUNNING_SCORE") {
    return <div className="score-event-list">{document.running_score.slice().reverse().map((event) => <div key={event.id}><b>{event.team} {event.cumulative}</b><span>+{event.value} · {event.period} 节 · #{event.player_number || "?"}</span></div>)}</div>;
  }
  if (region === "SUMMARY") {
    const computed = computedScoreSummary(document.running_score);
    return <div className="region-form score-summary-form"><div className="computed-score-note"><span>系统按逐次得分计算</span><strong>A {computed.final.A} : {computed.final.B} B</strong></div>{(["1", "2", "3", "4", "OT"] as ScorePeriod[]).map((period) => <div className="period-score-input" key={period}><strong>{period === "OT" ? "加时" : `第 ${period} 节`} <small>计算 {computed.periods[period].A}:{computed.periods[period].B}</small></strong>{(["A", "B"] as TeamSide[]).map((side) => <label key={side}><span>{side}</span><input disabled={readOnly} inputMode="numeric" onChange={(event) => changePath(`/summary/period_scores/${period}/${side}`, event.target.value === "" ? null : Number(event.target.value))} type="number" value={document.summary.period_scores[period][side] ?? ""} /></label>)}</div>)}<div className="period-score-input final"><strong>最终 <small>计算 {computed.final.A}:{computed.final.B}</small></strong>{(["A", "B"] as TeamSide[]).map((side) => <label key={side}><span>{side}</span><input disabled={readOnly} inputMode="numeric" onChange={(event) => changePath(`/summary/final_score/${side}`, event.target.value === "" ? null : Number(event.target.value))} type="number" value={document.summary.final_score[side] ?? ""} /></label>)}</div><label><span>纸面胜队</span><select disabled={readOnly} onChange={(event) => changePath("/summary/winner_side", event.target.value, true)} value={document.summary.winner_side}><option value="">未填写</option><option value="A">A 队</option><option value="B">B 队</option></select></label><label><span>比赛结束时间</span><input disabled={readOnly} onChange={(event) => changePath("/summary/ended_at", event.target.value)} placeholder="例如 14:20" value={document.summary.ended_at} /></label></div>;
  }
  return <div className="region-form">{["scorer", "assistant_scorer", "timer", "shot_clock_operator"].map((key) => <label key={key}><span>{key}</span><input disabled={readOnly} onChange={(event) => changePath(`/officials/${key}`, event.target.value)} value={String(document.officials[key] ?? "")} /></label>)}{["crew_chief_signature", "umpire_1_signature", "umpire_2_signature", "captain_protest_signature"].map((key) => <label className="signature-check" key={key}><input checked={Boolean(document.officials[key])} disabled={readOnly} onChange={(event) => changePath(`/officials/${key}`, event.target.checked, true)} type="checkbox" /><span>{key}</span></label>)}</div>;
}

function computedScoreSummary(events: ScoreEvent[]) {
  const periods = Object.fromEntries(
    (["1", "2", "3", "4", "OT"] as ScorePeriod[]).map((period) => [period, { A: 0, B: 0 }]),
  ) as Record<ScorePeriod, Record<TeamSide, number>>;
  const final: Record<TeamSide, number> = { A: 0, B: 0 };
  for (const event of events) {
    periods[event.period][event.team] += event.value;
    final[event.team] += event.value;
  }
  return { periods, final };
}

function TeamRegionEditor({ document, side, readOnly, changePath }: {
  document: ScoresheetDocument;
  side: TeamSide;
  readOnly: boolean;
  changePath: (path: string, value: unknown, immediate?: boolean) => void;
}) {
  const team = document.teams[side];
  return (
    <div className="region-form player-editor">
      <h3>{team.name}</h3>
      <div className="team-paper-fields">
        {(["H1", "H2", "OT"] as const).map((scope) => (
          <label key={scope}>
            <span>暂停 {scope}</span>
            <input
              disabled={readOnly}
              onChange={(event) =>
                changePath(`/teams/${side}/timeouts/${scope}`, splitMarks(event.target.value))
              }
              placeholder="分钟，以空格分隔"
              value={markText(team.timeouts[scope] ?? [])}
            />
          </label>
        ))}
        {(["1", "2", "3", "4"] as const).map((period) => (
          <label key={period}>
            <span>第 {period} 节全队犯规</span>
            <input
              disabled={readOnly}
              max={4}
              min={0}
              onChange={(event) => {
                const count = Math.max(0, Math.min(4, Number(event.target.value) || 0));
                changePath(`/teams/${side}/team_fouls/${period}`, Array(count).fill("X"));
              }}
              type="number"
              value={(team.team_fouls[period] ?? []).length}
            />
          </label>
        ))}
        {(["head_coach", "assistant_coach"] as const).map((role) => (
          <div className="coach-editor" key={role}>
            <label>
              <span>{role === "head_coach" ? "教练员" : "助理教练员"}</span>
              <input
                disabled={readOnly}
                onChange={(event) => changePath(`/teams/${side}/${role}/name`, event.target.value)}
                value={team[role].name}
              />
            </label>
            <label>
              <span>犯规</span>
              <input
                disabled={readOnly}
                onChange={(event) =>
                  changePath(
                    `/teams/${side}/${role}/fouls`,
                    splitMarks(event.target.value.toUpperCase()),
                  )
                }
                placeholder="C B D"
                value={markText(team[role].fouls)}
              />
            </label>
          </div>
        ))}
      </div>
      {team.players.map((player, index) => (
        <div className="player-editor-row" key={player.player_id}>
          <span className="jersey">{player.jersey_number || "–"}</span>
          <strong>{player.name}</strong>
          <label><input checked={player.appeared} disabled={readOnly} onChange={(event) => changePath(`/teams/${side}/players/${index}/appeared`, event.target.checked, true)} type="checkbox" />出场</label>
          <label><input checked={player.starter} disabled={readOnly} onChange={(event) => changePath(`/teams/${side}/players/${index}/starter`, event.target.checked, true)} type="checkbox" />首发</label>
          <label><input checked={player.captain} disabled={readOnly} onChange={(event) => changePath(`/teams/${side}/players/${index}/captain`, event.target.checked, true)} type="checkbox" />队长</label>
          <input aria-label={`${player.name} 犯规`} disabled={readOnly} onChange={(event) => changePath(`/teams/${side}/players/${index}/fouls`, splitMarks(event.target.value.toUpperCase()))} placeholder="P T U" value={markText(player.fouls)} />
        </div>
      ))}
    </div>
  );
}

function splitMarks(value: string): string[] {
  return value.trim() ? value.trim().split(/\s+/).filter(Boolean) : [];
}

function markText(values: unknown[]): string {
  return values
    .map((value) =>
      typeof value === "object" && value
        ? String((value as { code?: unknown; minute?: unknown }).code ?? (value as { minute?: unknown }).minute ?? "")
        : String(value),
    )
    .filter(Boolean)
    .join(" ");
}

function PaperScoreGrid({ events, issues, selectedId, onSelect }: {
  events: ScoreEvent[];
  issues: string[];
  selectedId: string;
  onSelect: (eventId: string) => void;
}) {
  const byCell = useMemo(() => new Map(events.map((event) => [`${event.team}-${event.cumulative}`, event])), [events]);
  return <div className="paper-score-grid">{SCORE_BLOCKS.map((block) => <div className="score-block" key={block.key}><header>{block.key}</header>{Array.from({ length: 40 }, (_, index) => block.start + index).map((score) => <div className="score-grid-row" key={score}><span>{score}</span>{(["A", "B"] as TeamSide[]).map((side) => { const event = byCell.get(`${side}-${score}`); const bad = event && issues.some((path) => path.includes(event.id) || path.includes(String(event.sequence - 1))); const classes = ["score-mark-cell", bad ? "invalid" : "", event?.id === selectedId ? "selected" : ""].filter(Boolean).join(" "); return <button className={classes} disabled={!event} key={side} onClick={() => event && onSelect(event.id)} type="button"><small>{side}</small>{event && <><b className={`mark ${event.mark ?? "slash"}`}>{event.value === 1 ? "•" : event.value === 3 ? "◯" : "╱"}</b><em>{event.player_number}</em></>}</button>; })}</div>)}</div>)}</div>;
}

function ScoreQuickBar({ document, onChange, selectedId, onSelect }: {
  document: ScoresheetDocument;
  onChange: (events: ScoreEvent[]) => void;
  selectedId: string;
  onSelect: (eventId: string) => void;
}) {
  const [period, setPeriod] = useState<ScorePeriod>("1");
  const selected = document.running_score.find((event) => event.id === selectedId) ?? null;
  const add = (side: TeamSide, value: ScoreValue) => {
    const player = document.teams[side].players.find((row) => row.appeared) ?? document.teams[side].players[0];
    onChange(addScoreEvent(document.running_score, { id: crypto.randomUUID(), team: side, value, period, player_id: player?.player_id, player_name: player?.name, player_number: player?.jersey_number }));
  };
  const update = (patch: Partial<ScoreEvent>) => {
    if (!selected) return;
    const next = document.running_score.map((event) =>
      event.id === selected.id ? { ...event, ...patch } : event,
    );
    onChange(normalizeScoreEvents(next));
  };
  const players = selected ? document.teams[selected.team].players : [];
  return (
    <div className="score-quick-entry">
      <div><label>快捷录入节次<select onChange={(event) => setPeriod(event.target.value as ScorePeriod)} value={period}>{(["1", "2", "3", "4", "OT"] as ScorePeriod[]).map((value) => <option key={value}>{value}</option>)}</select></label></div>
      <div className="quick-score-buttons">{(["A", "B"] as TeamSide[]).flatMap((side) => ([1, 2, 3] as ScoreValue[]).map((value) => <button disabled={teamTotal(document.running_score, side) + value > 160} key={`${side}-${value}`} onClick={() => add(side, value)} type="button">{side} +{value}</button>))}</div>
      <select aria-label="选择得分事件" onChange={(event) => onSelect(event.target.value)} value={selected?.id ?? ""}><option value="">修改已有事件…</option>{document.running_score.map((event) => <option key={event.id} value={event.id}>{event.sequence}. {event.team} +{event.value} → {event.cumulative}</option>)}</select>
      {selected && (
        <div className="score-event-editor">
          <label>球员<select onChange={(event) => { const player = players.find((row) => row.player_id === event.target.value); if (player) update({ player_id: player.player_id, player_name: player.name, player_number: player.jersey_number }); }} value={selected.player_id}>{players.map((player) => <option key={player.player_id} value={player.player_id}>#{player.jersey_number || "–"} {player.name}</option>)}</select></label>
          <label>分值<select onChange={(event) => update({ value: Number(event.target.value) as ScoreValue })} value={selected.value}>{([1, 2, 3] as ScoreValue[]).map((value) => <option key={value} value={value}>{value} 分</option>)}</select></label>
          <label>节次<select onChange={(event) => update({ period: event.target.value as ScorePeriod })} value={selected.period}>{(["1", "2", "3", "4", "OT"] as ScorePeriod[]).map((value) => <option key={value}>{value}</option>)}</select></label>
          <label>标记<select onChange={(event) => update({ boundary: event.target.value as ScoreEvent["boundary"] })} value={selected.boundary ?? "none"}><option value="none">普通</option><option value="period">节末</option><option value="game">终场</option></select></label>
          <button className="danger-quiet" onClick={() => { onChange(deleteScoreEvent(document.running_score, selected.id)); onSelect(""); }} type="button">删除事件</button>
        </div>
      )}
    </div>
  );
}
