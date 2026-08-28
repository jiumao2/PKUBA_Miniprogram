import {
  Button,
  Image,
  MovableArea,
  MovableView,
  ScrollView,
  Text,
  View,
} from "@tarojs/components";
import Taro, { useDidShow, useRouter, useUnload } from "@tarojs/taro";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  ApiError,
  createIdempotencyKey,
  type ScoresheetDetail,
  type ScoresheetMutationContext,
} from "@pkuba/api-client";
import {
  REGION_LABELS,
  SCORESHEET_REGIONS,
  semanticScoresheetPath,
  type ScoresheetDocument,
  type ScoresheetRegion,
  type ScoresheetContextPlayerMapping,
} from "@pkuba/scoresheet-domain";

import { absoluteMediaUrl, api, replaceGameMedia } from "../../../api";
import { getMiniAppSession } from "../../../auth";
import { MobileStandardView } from "../../MobileStandardView";
import { GameContextReview } from "../../GameContextReview";
import "./index.css";

type StepKey = ScoresheetRegion | "CLOSING" | "PUBLISH";

const STEPS: Array<{ key: StepKey; label: string }> = [
  { key: "SOURCE_GAME", label: "比赛" },
  { key: "TEAM_A", label: "A 队" },
  { key: "TEAM_B", label: "B 队" },
  { key: "RUNNING_SCORE", label: "得分" },
  { key: "CLOSING", label: "结表" },
  { key: "PUBLISH", label: "发布" },
];

const CLIENT_KEY = "pkuba-scoresheet-miniapp-client";
const RECOVERY_TTL_MS = 24 * 60 * 60 * 1000;
const recognitionOperationKeys = new Map<string, string>();
type PendingPublication = { key: string; context: ScoresheetMutationContext; sourceId: string | null };
const publicationOperations = new Map<string, PendingPublication>();

interface RecoverySnapshot {
  local: ScoresheetDocument;
  baseVersion: number;
  expiresAt: number;
}

function leaseStorageKey(scoresheetId: string) {
  return `pkuba-scoresheet-miniapp-lease:${scoresheetId}`;
}

function clearStoredLease(scoresheetId: string) {
  if (scoresheetId) Taro.removeStorageSync(leaseStorageKey(scoresheetId));
}

function recoveryStorageKey(scoresheetId: string) {
  return `pkuba-scoresheet-miniapp-recovery:${scoresheetId}`;
}

function storeRecovery(scoresheetId: string, local: ScoresheetDocument, baseVersion: number) {
  if (!scoresheetId) return;
  const snapshot: RecoverySnapshot = { local, baseVersion, expiresAt: Date.now() + RECOVERY_TTL_MS };
  Taro.setStorageSync(recoveryStorageKey(scoresheetId), snapshot);
}

function readRecovery(scoresheetId: string): Omit<RecoverySnapshot, "expiresAt"> | null {
  if (!scoresheetId) return null;
  const snapshot = Taro.getStorageSync<RecoverySnapshot>(recoveryStorageKey(scoresheetId));
  if (!snapshot?.local || !snapshot.expiresAt || snapshot.expiresAt <= Date.now()) {
    Taro.removeStorageSync(recoveryStorageKey(scoresheetId));
    return null;
  }
  return { local: snapshot.local, baseVersion: snapshot.baseVersion };
}

function clearRecovery(scoresheetId: string) {
  if (scoresheetId) Taro.removeStorageSync(recoveryStorageKey(scoresheetId));
}

function getClientId() {
  const current = Taro.getStorageSync<string>(CLIENT_KEY);
  if (current) return current;
  const next = `mini-${Date.now()}-${Math.random().toString(36).slice(2)}`;
  Taro.setStorageSync(CLIENT_KEY, next);
  return next;
}

function message(reason: unknown) {
  return reason instanceof Error ? reason.message : "操作失败，请稍后重试";
}

function documentDiffPaths(left: unknown, right: unknown, path = "", result: string[] = []): string[] {
  if (result.length >= 6 || JSON.stringify(left) === JSON.stringify(right)) return result;
  if (Array.isArray(left) && Array.isArray(right)) {
    for (let index = 0; index < Math.max(left.length, right.length) && result.length < 6; index += 1) {
      documentDiffPaths(left[index], right[index], `${path}/${index}`, result);
    }
    return result;
  }
  if (left && right && typeof left === "object" && typeof right === "object") {
    const keys = new Set([...Object.keys(left), ...Object.keys(right)]);
    for (const key of keys) {
      if (result.length >= 6) break;
      documentDiffPaths((left as Record<string, unknown>)[key], (right as Record<string, unknown>)[key], `${path}/${key}`, result);
    }
    return result;
  }
  result.push(path || "/");
  return result;
}

function documentDiffLabels(left: unknown, right: unknown, document?: ScoresheetDocument): string {
  const labels = documentDiffPaths(left, right).map((path) => semanticScoresheetPath(path, document));
  return Array.from(new Set(labels)).join("、") || "多个字段";
}

export default function ScoresheetEditorPage() {
  const router = useRouter();
  const scoresheetId = router.params.id ?? "";
  const token = getMiniAppSession();
  const [sheet, setSheet] = useState<ScoresheetDetail | null>(null);
  const [stepIndex, setStepIndex] = useState(0);
  const [view, setView] = useState<"SOURCE" | "STANDARD">("SOURCE");
  const [readOnly, setReadOnly] = useState(true);
  const [saveState, setSaveState] = useState("正在连接");
  const [error, setError] = useState("");
  const [online, setOnline] = useState(true);
  const [sourceScale, setSourceScale] = useState(1);
  const [sourcePosition, setSourcePosition] = useState({ x: 0, y: 0 });
  const [sourceRotation, setSourceRotation] = useState(0);
  const [history, setHistory] = useState<ScoresheetDocument[]>([]);
  const [future, setFuture] = useState<ScoresheetDocument[]>([]);
  const [selectedScoreId, setSelectedScoreId] = useState("");
  const [standardScrollAnchor, setStandardScrollAnchor] = useState("step-source-game-top");
  const [busyAction, setBusyAction] = useState("");
  const clientIdRef = useRef(getClientId());
  const serverRef = useRef<ScoresheetDetail | null>(null);
  const leaseRef = useRef("");
  const pendingRef = useRef<ScoresheetDocument | null>(null);
  const pendingBaseVersionRef = useRef<number | null>(null);
  const recoveryRef = useRef<{ local: ScoresheetDocument; baseVersion: number } | null>(null);
  const recoveryLoadedRef = useRef(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const savingRef = useRef(false);
  const savingDocumentRef = useRef<ScoresheetDocument | null>(null);
  const actionFlightRef = useRef("");
  const contextReviewOperationRef = useRef({ operation: "", key: "" });
  const acquireFlightRef = useRef<Promise<void> | null>(null);
  const syncFlightRef = useRef<Promise<boolean> | null>(null);
  const heartbeatFlightRef = useRef<Promise<void> | null>(null);
  const flushRef = useRef<(changeType?: string, explicitSave?: boolean) => Promise<void>>(async () => undefined);
  const serverObservationRef = useRef({
    sequence: 0,
    orders: new WeakMap<ScoresheetDetail, number>(),
    writes: new WeakSet<ScoresheetDetail>(),
    // A sync summary may know a newer version before its complete draft arrives.
    latest: null as { order: number; detail: ScoresheetDetail; knownVersion: number; knownEvent: number } | null,
  });

  const projectServer = useCallback((raw: ScoresheetDetail) => {
    const observation = serverObservationRef.current;
    const latest = observation.latest;
    if (raw === latest?.detail) return raw; // Applying a projection must not re-age its capability.
    const order = observation.orders.get(raw) ?? ++observation.sequence;
    observation.orders.set(raw, order);
    const isWrite = observation.writes.has(raw);
    let next = raw;
    if (latest?.detail.id === raw.id) {
      const bodyAdvanced = raw.draft_version > latest.detail.draft_version
        || raw.event_sequence > latest.detail.event_sequence;
      const belowKnown = raw.draft_version < latest.knownVersion || raw.event_sequence < latest.knownEvent;
      // Compare complete bodies to complete bodies, not to summary-only progress.
      // A delayed successful write can fill a missing body, but never replace a newer one.
      if (raw.draft_version < latest.detail.draft_version || raw.event_sequence < latest.detail.event_sequence
        || (belowKnown && !(isWrite && bodyAdvanced))
        || (order < latest.order && !(isWrite && bodyAdvanced))) return latest.detail;
      // Draft progress does not prove an actor's permission is newer. Preserve the
      // later capability observation even when accepting an earlier request's commit.
      if (order < latest.order || belowKnown) {
        next = { ...raw, can_upload_source: latest.detail.can_upload_source };
        observation.orders.set(next, order);
        if (isWrite) observation.writes.add(next);
      }
    }
    observation.latest = { order: Math.max(order, latest?.detail.id === raw.id ? latest.order : order),
      detail: next,
      knownVersion: Math.max(raw.draft_version, latest?.detail.id === raw.id ? latest.knownVersion : raw.draft_version),
      knownEvent: Math.max(raw.event_sequence, latest?.detail.id === raw.id ? latest.knownEvent : raw.event_sequence) };
    return next;
  }, []);

  const readServer = useCallback(async () => {
    const observation = serverObservationRef.current;
    const order = ++observation.sequence;
    const raw = await api.getScoresheet(scoresheetId, token);
    observation.orders.set(raw, order);
    return projectServer(raw);
  }, [projectServer, scoresheetId, token]);

  const writeServer = useCallback(async (request: () => Promise<ScoresheetDetail>) => {
    const observation = serverObservationRef.current;
    const order = ++observation.sequence;
    const raw = await request();
    observation.orders.set(raw, order);
    observation.writes.add(raw);
    return projectServer(raw);
  }, [projectServer]);

  const applyServer = useCallback((raw: ScoresheetDetail, preservePending = false) => {
    const next = projectServer(raw);
    serverRef.current = next;
    setSheet({
      ...next,
      draft: preservePending && pendingRef.current ? pendingRef.current : next.draft,
    });
    return next;
  }, [projectServer]);

  const load = useCallback(async () => {
    if (!scoresheetId || !token) throw new Error("记录表参数或登录状态无效");
    const next = await readServer();
    return applyServer(next, Boolean(pendingRef.current));
  }, [applyServer, readServer, scoresheetId, token]);

  const acquire = useCallback(() => {
    if (leaseRef.current) return Promise.resolve();
    if (acquireFlightRef.current) return acquireFlightRef.current;
    const operation = (async () => {
      const result = await api.acquireScoresheetLease(
        scoresheetId,
        clientIdRef.current,
        "MINIAPP",
        token,
        Taro.getStorageSync<string>(leaseStorageKey(scoresheetId)) || "",
      );
      if (result.read_only || !result.lease_token) {
        leaseRef.current = "";
        clearStoredLease(scoresheetId);
        setReadOnly(true);
        setSaveState(result.read_only_reason || (result.holder
          ? `${result.holder.username} 正在通过${result.holder.surface === "WEB" ? "网页" : "小程序"}编辑`
          : "当前记录表暂不可编辑"));
        return;
      }
      leaseRef.current = result.lease_token;
      Taro.setStorageSync(leaseStorageKey(scoresheetId), result.lease_token);
      setReadOnly(false);
      const recovery = recoveryRef.current;
      if (!recovery) {
        clearRecovery(scoresheetId);
        setSaveState("已保存");
        return;
      }
      const server = await readServer();
      recoveryRef.current = null;
      if (server.draft_version === recovery.baseVersion) {
        serverRef.current = server;
        pendingRef.current = recovery.local;
        pendingBaseVersionRef.current = server.draft_version;
        setSheet({ ...server, draft: recovery.local });
        setSaveState("已自动恢复编辑 · 正在保存本地输入");
        setTimeout(() => void flushRef.current("LEASE_RECOVERY", true), 0);
        return;
      }
      const choice = await Taro.showModal({
        title: "恢复编辑前服务器再次变化",
        content: `差异字段：${documentDiffLabels(recovery.local, server.draft, server.draft)}。请选择本次提交使用的值。`,
        cancelText: "服务器值",
        confirmText: "本地值",
      });
      if (choice.confirm) {
        serverRef.current = server;
        pendingRef.current = recovery.local;
        pendingBaseVersionRef.current = server.draft_version;
        setSheet({ ...server, draft: recovery.local });
        setTimeout(() => void flushRef.current("CONFLICT_RESOLVED_LOCAL", true), 0);
      } else {
        clearRecovery(scoresheetId);
        applyServer(await readServer());
        setSaveState("已采用服务器版本");
      }
    })();
    acquireFlightRef.current = operation;
    return operation.finally(() => {
      if (acquireFlightRef.current === operation) acquireFlightRef.current = null;
    });
  }, [applyServer, readServer, scoresheetId, token]);

  useDidShow(() => {
    void (async () => {
      try {
        if (!recoveryLoadedRef.current) {
          recoveryRef.current = readRecovery(scoresheetId);
          recoveryLoadedRef.current = true;
        }
        await load();
        await acquire();
      } catch (reason) {
        setError(message(reason));
      }
    })();
  });

  const context = useCallback((): ScoresheetMutationContext | null => {
    const current = serverRef.current;
    if (!current || !leaseRef.current) return null;
    return {
      expected_version: current.draft_version,
      lease_token: leaseRef.current,
      client_id: clientIdRef.current,
      surface: "MINIAPP",
    };
  }, []);

  const flush = useCallback(async (changeType = "FIELD_EDIT", explicitSave = false) => {
    if (savingRef.current || !pendingRef.current) return;
    const mutation = context();
    if (!mutation || !online) {
      setSaveState(online ? "只读模式" : "网络中断 · 未保存输入已保留");
      return;
    }
    const local = pendingRef.current;
    pendingRef.current = null;
    pendingBaseVersionRef.current = null;
    savingRef.current = true;
    savingDocumentRef.current = local;
    setSaveState("保存中…");
    try {
      const result = await writeServer(() => api.saveScoresheetDraft(
        scoresheetId,
        mutation,
        [{ path: "/", operation: "SET", value: local }],
        token,
        { changeType, explicitSave },
      ));
      applyServer(result, Boolean(pendingRef.current));
      pendingBaseVersionRef.current = pendingRef.current ? result.draft_version : null;
      if (pendingRef.current) storeRecovery(scoresheetId, pendingRef.current, result.draft_version);
      else clearRecovery(scoresheetId);
      setSaveState(pendingRef.current ? "等待保存…" : "已保存");
      setError("");
    } catch (reason) {
      const unsaved = pendingRef.current ?? local;
      pendingRef.current = null;
      if (reason instanceof ApiError && reason.code === "VERSION_CONFLICT") {
        const server = await readServer();
        const choice = await Taro.showModal({
          title: "发现跨端差异",
          content: "服务器草稿已经变化。取消将采用服务器值；确认会在当前版本上提交本地值。",
          cancelText: "服务器值",
          confirmText: "本地值",
        });
        if (choice.confirm && leaseRef.current) {
          serverRef.current = server;
          pendingRef.current = unsaved;
          pendingBaseVersionRef.current = server.draft_version;
        } else {
          pendingRef.current = null;
          pendingBaseVersionRef.current = null;
          clearRecovery(scoresheetId);
          applyServer(server);
        }
      } else if (
        reason instanceof ApiError &&
        ["LEASE_LOST", "LEASE_REQUIRED"].includes(reason.code ?? "")
      ) {
        const server = await readServer();
        leaseRef.current = "";
        clearStoredLease(scoresheetId);
        setReadOnly(true);
        if (server.draft_version === mutation.expected_version) {
          recoveryRef.current = { local: unsaved, baseVersion: server.draft_version };
          storeRecovery(scoresheetId, unsaved, server.draft_version);
          serverRef.current = server;
          setSheet({ ...server, draft: unsaved });
          setSaveState("编辑权已失效 · 本地输入已保留，等待自动恢复");
        } else {
          const choice = await Taro.showModal({
            title: "编辑权已失效",
            content: `服务器同时发生了修改：${documentDiffLabels(unsaved, server.draft, server.draft)}。取消采用服务器值；确认保留本地值并在恢复编辑后提交。`,
            cancelText: "服务器值",
            confirmText: "本地值",
          });
          if (choice.confirm) {
            recoveryRef.current = { local: unsaved, baseVersion: server.draft_version };
            storeRecovery(scoresheetId, unsaved, server.draft_version);
            serverRef.current = server;
            setSheet({ ...server, draft: unsaved });
            setSaveState("本地值已保留 · 等待自动恢复编辑");
          } else {
            clearRecovery(scoresheetId);
            applyServer(server);
            setSaveState("已采用服务器版本 · 当前为只读");
          }
        }
      } else {
        pendingRef.current = unsaved;
        pendingBaseVersionRef.current = mutation.expected_version;
        storeRecovery(scoresheetId, unsaved, mutation.expected_version);
        setSaveState("保存失败 · 输入仍保留");
        setError(message(reason));
      }
    } finally {
      savingRef.current = false;
      savingDocumentRef.current = null;
      if (pendingRef.current && online) setTimeout(() => void flush(), 60);
    }
  }, [applyServer, context, online, readServer, scoresheetId, token, writeServer]);
  flushRef.current = flush;

  const drainPending = useCallback(async (changeType = "EXPLICIT_SAVE") => {
    if (timerRef.current) clearTimeout(timerRef.current);
    for (let attempt = 0; attempt < 200; attempt += 1) {
      if (savingRef.current) {
        await new Promise<void>((resolve) => setTimeout(resolve, 25));
        continue;
      }
      if (!pendingRef.current) return true;
      if (!online || !leaseRef.current) return false;
      await flush(changeType, true);
    }
    return !pendingRef.current && !savingRef.current;
  }, [flush, online]);

  const queueDocument = useCallback((next: ScoresheetDocument, previous: ScoresheetDocument, immediate = false, changeType = "FIELD_EDIT") => {
    if (readOnly || !online || actionFlightRef.current) return;
    setHistory((rows) => [...rows.slice(-49), previous]);
    setFuture([]);
    if (!pendingRef.current) pendingBaseVersionRef.current = serverRef.current?.draft_version ?? null;
    pendingRef.current = next;
    storeRecovery(scoresheetId, next, pendingBaseVersionRef.current ?? serverRef.current?.draft_version ?? 0);
    setSheet((current) => (current ? { ...current, draft: next } : current));
    setSaveState("等待保存…");
    if (timerRef.current) clearTimeout(timerRef.current);
    if (immediate) void flush(changeType);
    else timerRef.current = setTimeout(() => void flush(changeType), 1000);
  }, [flush, online, readOnly, scoresheetId]);

  const sync = useCallback(() => {
    if (actionFlightRef.current) return Promise.resolve(true);
    if (syncFlightRef.current) return syncFlightRef.current;
    const operation = (async () => {
      const current = serverRef.current;
      if (!current || !scoresheetId || !token) return true;
      const observation = serverObservationRef.current;
      const order = ++observation.sequence;
      let awaitingSync = true;
      try {
      const update = await api.syncScoresheet(
        scoresheetId,
        current.draft_version,
        current.event_sequence,
        token,
      );
      awaitingSync = false;
      if (actionFlightRef.current) return true;
      const latest = serverRef.current;
      const known = observation.latest;
      if (latest?.id !== current.id || (known && (order < known.order
        || update.current_version < known.knownVersion || update.current_event < known.knownEvent))) return true;
      const canUploadSource = update.can_upload_source === true;
      const nextCapability = { ...latest, can_upload_source: canUploadSource };
      observation.orders.set(nextCapability, order);
      // Keep complete-body counters unchanged until a full response is available.
      observation.latest = { order, detail: nextCapability, knownVersion: update.current_version, knownEvent: update.current_event };
      serverRef.current = nextCapability;
      setSheet((shown) => shown?.id === latest.id
        ? { ...shown, can_upload_source: canUploadSource } : shown);
      if ((update.events.length || update.requires_full_reload) && !savingRef.current) {
        const next = await readServer();
        if (actionFlightRef.current || next.draft_version < (serverRef.current?.draft_version ?? 0)) return true;
        if (!pendingRef.current) {
          applyServer(next);
        } else if (pendingBaseVersionRef.current === next.draft_version) {
          serverRef.current = next;
        } else {
          const local = pendingRef.current;
          pendingRef.current = null;
          pendingBaseVersionRef.current = null;
          const choice = await Taro.showModal({
            title: "发现跨端差异",
            content: `差异字段：${documentDiffLabels(local, next.draft, next.draft)}。取消采用服务器值；确认提交本地值。`,
            cancelText: "服务器值",
            confirmText: "本地值",
          });
          if (choice.confirm && leaseRef.current) {
            serverRef.current = next;
            pendingRef.current = local;
            pendingBaseVersionRef.current = next.draft_version;
            setSheet({ ...next, draft: local });
          } else {
            clearRecovery(scoresheetId);
            applyServer(next);
          }
        }
      }
      if (
        leaseRef.current &&
        (!update.lease || update.lease.client_id !== clientIdRef.current || update.lease.surface !== "MINIAPP")
      ) {
        if (pendingRef.current) {
          recoveryRef.current = {
            local: pendingRef.current,
            baseVersion: pendingBaseVersionRef.current ?? current.draft_version,
          };
          storeRecovery(scoresheetId, pendingRef.current, pendingBaseVersionRef.current ?? current.draft_version);
          pendingRef.current = null;
          pendingBaseVersionRef.current = null;
        }
        leaseRef.current = "";
        clearStoredLease(scoresheetId);
        setReadOnly(true);
        setSaveState("编辑权已转移 · 本地输入已保留");
      }
      if (!leaseRef.current && !update.lease) {
        setSaveState("编辑权已释放 · 正在自动取得编辑权");
        await acquire();
      }
        return true;
      } catch (reason) {
        if (awaitingSync && (serverRef.current?.id !== current.id || actionFlightRef.current
          || (observation.latest && order < observation.latest.order))) return true;
        if (online) setError(message(reason));
        return false;
      }
    })();
    syncFlightRef.current = operation;
    return operation.finally(() => {
      if (syncFlightRef.current === operation) syncFlightRef.current = null;
    });
  }, [acquire, applyServer, online, readServer, scoresheetId, token]);

  const heartbeat = useCallback(() => {
    if (heartbeatFlightRef.current || !leaseRef.current) {
      return heartbeatFlightRef.current ?? Promise.resolve();
    }
    const operation = api
      .heartbeatScoresheetLease(
        scoresheetId,
        leaseRef.current,
        clientIdRef.current,
        "MINIAPP",
        token,
      )
      .then(() => undefined)
      .catch(() => {
        if (pendingRef.current) {
          recoveryRef.current = {
            local: pendingRef.current,
            baseVersion: pendingBaseVersionRef.current ?? serverRef.current?.draft_version ?? 0,
          };
          storeRecovery(scoresheetId, pendingRef.current, pendingBaseVersionRef.current ?? serverRef.current?.draft_version ?? 0);
          pendingRef.current = null;
          pendingBaseVersionRef.current = null;
        }
        leaseRef.current = "";
        clearStoredLease(scoresheetId);
        setReadOnly(true);
        setSaveState("编辑权已失效 · 本地输入已保留，等待自动恢复");
      });
    heartbeatFlightRef.current = operation;
    return operation.finally(() => {
      if (heartbeatFlightRef.current === operation) heartbeatFlightRef.current = null;
    });
  }, [scoresheetId, token]);

  useEffect(() => {
    const poll = setInterval(() => void sync(), 2000);
    const heartbeatTimer = setInterval(() => void heartbeat(), 15000);
    const network = (result: Taro.onNetworkStatusChange.CallbackResult) => {
      if (result.isConnected) {
        void sync().then((synchronized) => {
          if (!synchronized) return;
          setOnline(true);
          if (pendingRef.current && leaseRef.current) void flush("NETWORK_RECOVERY");
        });
      } else {
        setOnline(false);
        setSaveState("网络中断 · 未保存输入已保留");
      }
    };
    Taro.onNetworkStatusChange(network);
    return () => {
      clearInterval(poll);
      clearInterval(heartbeatTimer);
      Taro.offNetworkStatusChange(network);
    };
  }, [flush, heartbeat, sync]);

  useUnload(() => {
    const unsaved = pendingRef.current ?? savingDocumentRef.current;
    if (unsaved) {
      storeRecovery(scoresheetId, unsaved, pendingBaseVersionRef.current ?? serverRef.current?.draft_version ?? 0);
      return;
    }
    clearRecovery(scoresheetId);
    if (leaseRef.current && token && !savingRef.current) {
      void api.releaseScoresheetLease(
        scoresheetId,
        leaseRef.current,
        clientIdRef.current,
        "MINIAPP",
        token,
      );
      leaseRef.current = "";
      clearStoredLease(scoresheetId);
    }
  });

  const validate = async () => {
    if (actionFlightRef.current) return;
    actionFlightRef.current = "VALIDATE";
    setBusyAction("VALIDATE");
    try {
      if (!context() || !(await drainPending())) return;
      applyServer(await writeServer(() => api.validateScoresheet(scoresheetId, context()!, token)));
    } catch (reason) {
      setError(message(reason));
    } finally {
      actionFlightRef.current = "";
      setBusyAction("");
    }
  };

  const reviewGameContext = async (mappings: ScoresheetContextPlayerMapping[]) => {
    if (actionFlightRef.current) return;
    const reviewToken = serverRef.current?.validation_report.game_context?.review_token;
    if (!reviewToken || !context()) return;
    actionFlightRef.current = "GAME_CONTEXT";
    setBusyAction("GAME_CONTEXT");
    try {
      const answer = await Taro.showModal({ title: "确认比赛信息复核",
        content: "请确认已对照原图核对当前比赛信息及所选球员归属。原图、得分、犯规和人工编辑会保留；未解决的名单冲突仍会阻止发布。",
        confirmText: "确认复核" });
      if (!answer.confirm || !(await drainPending())) return;
      const mutation = context()!;
      const operation = JSON.stringify([scoresheetId, mutation.expected_version, reviewToken, mappings]);
      if (contextReviewOperationRef.current.operation !== operation) {
        contextReviewOperationRef.current = { operation, key: createIdempotencyKey() };
      }
      applyServer(await writeServer(() => api.reviewScoresheetGameContext(scoresheetId, mutation, reviewToken, mappings,
        token, contextReviewOperationRef.current.key)));
      contextReviewOperationRef.current = { operation: "", key: "" };
      setHistory([]);
      setFuture([]);
      applyServer(await writeServer(() => api.validateScoresheet(scoresheetId, context()!, token)));
      setError("");
      setSaveState("已保存");
    } catch (reason) {
      setError(message(reason));
    } finally {
      actionFlightRef.current = "";
      setBusyAction("");
    }
  };

  const publish = async () => {
    if (actionFlightRef.current) return;
    actionFlightRef.current = "PUBLISH";
    setBusyAction("PUBLISH");
    let operation = "";
    let pending: PendingPublication | undefined;
    const complete = (raw: ScoresheetDetail) => {
      applyServer(raw);
      publicationOperations.delete(operation);
      leaseRef.current = "";
      clearStoredLease(scoresheetId);
      setReadOnly(true);
      setSaveState("已发布");
      setError("");
      Taro.showToast({ title: "发布成功", icon: "success" });
    };
    const send = async (intent: PendingPublication) => complete(await writeServer(
      () => api.publishScoresheet(scoresheetId, intent.context, token, intent.key),
    ));
    try {
      if (!(await drainPending())) return;
      const current = serverRef.current;
      if (!current) return;
      operation = JSON.stringify([token, scoresheetId, current.source?.id,
        current.draft_version, clientIdRef.current]);
      pending = publicationOperations.get(operation);
      if (pending) {
        await send(pending);
        return;
      }
      if (!context()) return;
      const validated = applyServer(await writeServer(() => api.validateScoresheet(scoresheetId, context()!, token)));
      const errors = validated.validation_report.errors ?? [];
      const warnings = validated.validation_report.warnings ?? [];
      if (errors.length > 0) {
        setStepIndex(STEPS.length - 1);
        setView("STANDARD");
        setStandardScrollAnchor("step-publish-top");
        await Taro.showModal({
          title: "校验未通过",
          content: validated.validation_report.game_context?.required
            ? "比赛信息或名单需要复核，原图和人工编辑均已保留。请查看发布区域中的具体差异。"
            : `仍有 ${errors.length} 个错误，请修正后重新发布。`,
          showCancel: false,
        });
        return;
      }
      const confirmed = await Taro.showModal({
        title: "发布正式数据",
        content: warnings.length > 0
          ? `服务端校验通过，仍有 ${warnings.length} 条提醒。继续即一次性确认全部提醒，并更新正式比分、排名、对阵和球员统计。`
          : "服务端校验通过。发布将同时更新正式比分、排名、对阵和球员统计。",
        confirmText: "确认发布",
      });
      if (!confirmed.confirm) return;
      const warningIds = warnings.map((warning) => warning.id);
      if (warningIds.length > 0) {
        applyServer(
          await writeServer(() => api.acknowledgeScoresheetWarnings(scoresheetId, context()!, warningIds, token)),
        );
      }
      pending = { key: createIdempotencyKey(), context: context()!, sourceId: current.source?.id ?? null };
      publicationOperations.set(operation, pending);
      await send(pending);
    } catch (reason) {
      if (pending) {
        try {
          const latest = applyServer(await readServer(), Boolean(pendingRef.current));
          if (latest.status === "PUBLISHED" && latest.draft_version === pending.context.expected_version
            && latest.publication?.draft_version === pending.context.expected_version
            && latest.publication.source_asset_id === pending.sourceId) {
            complete(latest);
            return;
          }
        } catch { /* Preserve the original unknown-result error and pending key. */ }
      }
      setError(message(reason));
    } finally {
      actionFlightRef.current = "";
      setBusyAction("");
    }
  };

  const undo = (direction: "UNDO" | "REDO") => {
    if (!sheet || readOnly || actionFlightRef.current) return;
    const source = direction === "UNDO" ? history : future;
    const target = source[source.length - 1];
    if (!target) return;
    if (direction === "UNDO") {
      setHistory(source.slice(0, -1));
      setFuture((rows) => [...rows, sheet.draft]);
    } else {
      setFuture(source.slice(0, -1));
      setHistory((rows) => [...rows.slice(-49), sheet.draft]);
    }
    pendingRef.current = target;
    pendingBaseVersionRef.current = serverRef.current?.draft_version ?? null;
    storeRecovery(scoresheetId, target, pendingBaseVersionRef.current ?? 0);
    setSheet({ ...sheet, draft: target });
    void flush(direction);
  };

  if (!sheet) {
    return <View className="mini-sheet-loading"><Text>{error || "正在打开记录表…"}</Text></View>;
  }

  const currentKey = stepKey(stepIndex);
  const errors = sheet.validation_report.errors ?? [];
  const warnings = sheet.validation_report.warnings ?? [];

  return (
    <View className="mini-sheet-page">
      <View className="mini-sheet-context">
        <View>
          <Text className="mini-sheet-title">{String(sheet.game.label)}</Text>
          <View className="mini-sheet-state-row"><Text className={`mini-sheet-save ${readOnly ? "readonly" : ""}`}>{saveState}</Text>{sheet.recognition?.status === "SUCCEEDED" && <Text className="mini-recognition-chip">识别完成</Text>}</View>
        </View>
        <Text className={readOnly ? "mini-sheet-mode readonly" : "mini-sheet-mode"}>{readOnly ? "只读查看" : "可编辑"}</Text>
      </View>

      <ScrollView className="mini-sheet-steps" scrollX enhanced showScrollbar={false}>
        <View className="mini-sheet-step-row">
          {STEPS.map((step, index) => {
            const regions = step.key === "CLOSING"
              ? (["SUMMARY", "OFFICIALS"] as ScoresheetRegion[])
              : SCORESHEET_REGIONS.includes(step.key as ScoresheetRegion)
                ? ([step.key] as ScoresheetRegion[])
                : [];
            const issueCount = [...errors, ...warnings].filter((issue) => regions.includes(issue.region)).length;
            return (
              <View className={`mini-sheet-step ${stepIndex === index ? "active" : ""}`} key={step.key} onClick={() => {
                setStepIndex(index);
                setStandardScrollAnchor(stepAnchor(step.key));
              }}>
                <Text className="step-number">{index + 1}</Text>
                <Text>{step.label}</Text>
                {issueCount > 0 && <Text className="step-issue">{issueCount}</Text>}
              </View>
            );
          })}
        </View>
      </ScrollView>

      <View className="mini-sheet-view-switch">
        <Button className={view === "SOURCE" ? "active" : ""} onClick={() => setView("SOURCE")}>原图</Button>
        <Button className={view === "STANDARD" ? "active" : ""} onClick={() => setView("STANDARD")}>标准表</Button>
        <Button disabled={readOnly || history.length === 0} onClick={() => undo("UNDO")}>撤销</Button>
        <Button disabled={readOnly || future.length === 0} onClick={() => undo("REDO")}>重做</Button>
      </View>

      <View className={`mini-sheet-workspace ${currentKey === "RUNNING_SCORE" && view === "STANDARD" ? "running-score" : ""}`}>
        {sheet.recognition && sheet.recognition.status !== "SUCCEEDED"
          && sheet.status !== "PUBLISHED" && !sheet.publication && (
          <RecognitionBanner
            busy={busyAction === "RECOGNITION"}
            onRetry={async () => {
              if (actionFlightRef.current) return;
              const before = serverRef.current;
              if (readOnly || !online || before?.publication || before?.status === "PUBLISHED"
                || before?.recognition?.status !== "FAILED" || before.recognition.can_retry !== true) return;
              actionFlightRef.current = "RECOGNITION";
              setBusyAction("RECOGNITION");
              try {
                const confirmation = await Taro.showModal({
                  title: "重新识别并覆盖草稿？",
                  content: "识别成功后将覆盖整张草稿，包括人工修改；再次失败则保留当前草稿。已发布后不能再识别。",
                  confirmText: "确认重试",
                });
                if (!confirmation.confirm) return;
                if (!(await drainPending())) return;
                const current = serverRef.current;
                if (current?.id !== before.id || current.source_version !== before.source_version
                  || current.recognition?.id !== before.recognition.id
                  || current.publication || current.status === "PUBLISHED"
                  || current.recognition?.can_retry !== true) {
                  setError("记录表状态已变化，请重新核对后操作。");
                  return;
                }
                const mutation = context();
                if (!mutation) return;
                const operation = JSON.stringify([token, scoresheetId, current.source_version,
                  current.recognition.id, mutation.expected_version, mutation.client_id]);
                const key = recognitionOperationKeys.get(operation) ?? createIdempotencyKey();
                recognitionOperationKeys.set(operation, key);
                await api.retryScoresheetRecognition(
                  scoresheetId,
                  { ...mutation, confirmed_overwrite: true },
                  token,
                  key,
                );
                recognitionOperationKeys.delete(operation);
                leaseRef.current = "";
                clearStoredLease(scoresheetId);
                setReadOnly(true);
                setSaveState("自动识别正在进行 · 当前只读");
                await load();
              } catch (reason) {
                setError(message(reason));
              } finally {
                actionFlightRef.current = "";
                setBusyAction("");
              }
            }}
            readOnly={readOnly || !online}
            recognition={sheet.recognition}
          />
        )}
        {error && <View className="mini-sheet-error"><Text>{error}</Text><Button onClick={() => setError("")}>关闭</Button></View>}
        {view === "SOURCE" ? (
          <SourceView
            busy={busyAction === "REPLACE"}
            onReload={async () => { await load(); }}
            onReplace={async () => {
              if (!sheet.source || !sheet.can_upload_source || readOnly || actionFlightRef.current) return;
              actionFlightRef.current = "REPLACE";
              setBusyAction("REPLACE");
              try {
                if (!(await drainPending())) return;
                const selected = await Taro.chooseMedia({ count: 1, mediaType: ["image"], sourceType: ["album", "camera"], sizeType: ["original"] });
                const file = selected.tempFiles[0];
                if (!file) return;
                const confirm = await Taro.showModal({ title: "重传原图", content: "重传会保留旧来源审计、重置识别额度并生成新草稿。", confirmText: "确认重传" });
                if (!confirm.confirm) return;
                const current = serverRef.current;
                if (!current?.can_upload_source || current.id !== sheet.id
                  || current.source?.id !== sheet.source.id
                  || current.source_version !== sheet.source_version) return;
                await replaceGameMedia(sheet.source.id, sheet.source.version, file.tempFilePath, true, token);
                await load();
              } catch (reason) {
                if (!/cancel/i.test(message(reason))) setError(message(reason));
              } finally {
                actionFlightRef.current = "";
                setBusyAction("");
              }
            }}
            position={sourcePosition}
            readOnly={readOnly || !online || sheet.can_upload_source !== true}
            rotation={sourceRotation}
            scale={sourceScale}
            setPosition={setSourcePosition}
            setRotation={setSourceRotation}
            setScale={setSourceScale}
            source={sheet.source}
          />
        ) : currentKey === "RUNNING_SCORE" ? (
          <View className="mini-sheet-running-score">
            <StandardView
              document={sheet.draft}
              issues={[...errors, ...warnings]}
              onChange={(next, immediate) => queueDocument(next, sheet.draft, immediate)}
              onLocateIssue={setStandardScrollAnchor}
              onSelectScore={setSelectedScoreId}
              readOnly={readOnly || !online || Boolean(busyAction)}
              selectedScoreId={selectedScoreId}
              step={currentKey}
            />
          </View>
        ) : (
          <ScrollView className="mini-sheet-standard-scroll" scrollIntoView={standardScrollAnchor} scrollWithAnimation scrollY enhanced showScrollbar={false}>
            <View className="mini-sheet-standard-inner">
              <StandardView
                document={sheet.draft}
                issues={[...errors, ...warnings]}
                onChange={(next, immediate) => queueDocument(next, sheet.draft, immediate)}
                onLocateIssue={setStandardScrollAnchor}
                onSelectScore={setSelectedScoreId}
                readOnly={readOnly || !online || Boolean(busyAction)}
                selectedScoreId={selectedScoreId}
                step={currentKey}
              />
              {currentKey === "PUBLISH" && sheet.validation_report.game_context?.required && (
                <GameContextReview key={sheet.validation_report.game_context.review_token}
                  review={sheet.validation_report.game_context}
                  readOnly={readOnly || !online} busy={Boolean(busyAction)} onConfirm={reviewGameContext} />
              )}
              {currentKey === "PUBLISH" && (
                <PublishPanel
                  busy={busyAction === "PUBLISH"}
                  errors={errors}
                  publish={publish}
                  readOnly={readOnly || !online || Boolean(busyAction)}
                  validationReady={sheet.status === "READY" && sheet.validation_draft_version === sheet.draft_version}
                  warnings={warnings}
                />
              )}
            </View>
          </ScrollView>
        )}
      </View>

      <View className="mini-sheet-footer">
        <Button disabled={stepIndex === 0} onClick={() => setStepIndex((value) => {
          const next = Math.max(0, value - 1);
          setStandardScrollAnchor(stepAnchor(stepKey(next)));
          return next;
        })}>上一步</Button>
        {currentKey === "PUBLISH" ? (
          <Button className="review" disabled={readOnly || !online || Boolean(busyAction)} onClick={() => void validate()}>{busyAction === "VALIDATE" ? "校验中…" : "重新校验"}</Button>
        ) : (
          <Button className="review" disabled={readOnly || !online || Boolean(busyAction)} onClick={() => void drainPending("EXPLICIT_SAVE")}>保存草稿</Button>
        )}
        <Button disabled={stepIndex === STEPS.length - 1} onClick={() => setStepIndex((value) => {
          const next = Math.min(STEPS.length - 1, value + 1);
          setStandardScrollAnchor(stepAnchor(stepKey(next)));
          return next;
        })}>下一步</Button>
      </View>
    </View>
  );
}

function stepKey(index: number): StepKey {
  return STEPS[index]?.key ?? "SOURCE_GAME";
}

function stepAnchor(step: StepKey) {
  if (step === "TEAM_A") return "step-team-a-top";
  if (step === "TEAM_B") return "step-team-b-top";
  if (step === "RUNNING_SCORE") return "step-running-score-top";
  if (step === "CLOSING") return "step-closing-top";
  if (step === "PUBLISH") return "step-publish-top";
  return "step-source-game-top";
}

function RecognitionBanner({ recognition, readOnly, busy, onRetry }: {
  recognition: NonNullable<ScoresheetDetail["recognition"]>;
  readOnly: boolean;
  busy: boolean;
  onRetry: () => Promise<void>;
}) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (!recognition.next_attempt_at) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [recognition.next_attempt_at]);
  const retrySeconds = recognition.next_attempt_at
    ? Math.max(0, Math.ceil((new Date(recognition.next_attempt_at).getTime() - now) / 1000))
    : null;
  return (
    <View className={`mini-recognition-banner ${recognition.status.toLowerCase()}`}>
      <View>
        <Text>{recognition.status === "FAILED" ? "识别未完成" : "正在识别记录表"}</Text>
        <Text>{recognition.status === "RETRY_WAIT" && retrySeconds !== null ? `${retrySeconds} 秒后自动继续` : recognition.status === "FAILED" ? recognition.can_retry === true ? "可以手工录入，或重新识别" : "请核对原图与草稿" : "完成后会自动显示核对内容"}</Text>
      </View>
      {recognition.status === "FAILED" && recognition.can_retry === true && (
        <Button disabled={readOnly || busy} onClick={() => void onRetry()}>{busy ? "启动中…" : "重新识别"}</Button>
      )}
    </View>
  );
}

function SourceView({ source, scale, position, rotation, setScale, setPosition, setRotation, readOnly, busy, onReplace, onReload }: {
  source: ScoresheetDetail["source"];
  scale: number;
  position: { x: number; y: number };
  rotation: number;
  setScale: (value: number) => void;
  setPosition: (value: { x: number; y: number }) => void;
  setRotation: (value: number) => void;
  readOnly: boolean;
  busy: boolean;
  onReplace: () => Promise<void>;
  onReload: () => Promise<void>;
}) {
  const [viewport, setViewport] = useState({ width: 0, height: 0 });
  const failedUrlRef = useRef("");
  const sourceWidth = Number(source?.width ?? 1);
  const sourceHeight = Number(source?.height ?? 1);
  const fit = (nextRotation: number) => {
    const swapped = nextRotation % 180 !== 0;
    const naturalWidth = swapped ? sourceHeight : sourceWidth;
    const naturalHeight = swapped ? sourceWidth : sourceHeight;
    const ratio = viewport.width && viewport.height
      ? Math.min(viewport.width / naturalWidth, viewport.height / naturalHeight)
      : 1;
    const width = Math.max(1, naturalWidth * ratio);
    const height = Math.max(1, naturalHeight * ratio);
    return {
      width,
      height,
      imageWidth: swapped ? height : width,
      imageHeight: swapped ? width : height,
      x: Math.max(0, (viewport.width - width) / 2),
      y: Math.max(0, (viewport.height - height) / 2),
    };
  };
  const fitted = fit(rotation);
  useEffect(() => {
    const timer = setTimeout(() => {
      Taro.createSelectorQuery().select("#scoresheet-source-canvas").boundingClientRect((rect) => {
        const box = rect as unknown as { width?: number; height?: number } | null;
        if (box?.width && box.height) setViewport({ width: box.width, height: box.height });
      }).exec();
    }, 0);
    return () => clearTimeout(timer);
  }, []);
  useEffect(() => {
    if (!viewport.width || !viewport.height || position.x !== 0 || position.y !== 0) return;
    const initial = fit(rotation);
    setPosition({ x: initial.x, y: initial.y });
  }, [viewport.width, viewport.height]);
  const reset = () => {
    setScale(1);
    setRotation(0);
    const next = fit(0);
    setPosition({ x: next.x, y: next.y });
  };
  const rotate = () => {
    const nextRotation = (rotation + 90) % 360;
    const next = fit(nextRotation);
    setScale(1);
    setRotation(nextRotation);
    setPosition({ x: next.x, y: next.y });
  };
  const sourceUrl = source ? absoluteMediaUrl(source.url) : "";
  return (
    <View className="mini-source-view">
      <View className="mini-source-tools">
        <Text>{Math.round(scale * 100)}%</Text>
        <Button onClick={reset}>复位</Button>
        <Button onClick={rotate}>仅旋转视图</Button>
      </View>
      <MovableArea className="mini-source-canvas" id="scoresheet-source-canvas" scaleArea>
        {source ? (
          <MovableView
            className="mini-source-stage"
            direction="all"
            inertia
            outOfBounds
            scale
            scaleMax={4}
            scaleMin={1}
            scaleValue={scale}
            style={{ width: `${fitted.width}px`, height: `${fitted.height}px` }}
            x={position.x}
            y={position.y}
            onChange={(event) => setPosition({ x: event.detail.x, y: event.detail.y })}
            onScale={(event) => {
              setScale(event.detail.scale);
              setPosition({ x: event.detail.x, y: event.detail.y });
            }}
          >
            <Image
              className="mini-source-image"
              mode="aspectFit"
              src={sourceUrl}
              style={{ width: `${fitted.imageWidth}px`, height: `${fitted.imageHeight}px`, transform: `translate(-50%, -50%) rotate(${rotation}deg)` }}
              onError={() => {
                if (failedUrlRef.current === sourceUrl) return;
                failedUrlRef.current = sourceUrl;
                void onReload().catch(() => undefined);
              }}
            />
          </MovableView>
        ) : <Text className="mini-source-missing">当前原图不存在。</Text>}
      </MovableArea>
      {!readOnly && source && <Button className="mini-source-replace" disabled={busy} onClick={() => void onReplace()}>{busy ? "处理中…" : "重传新原图"}</Button>}
    </View>
  );
}

function StandardView({ document, step, readOnly, onChange, issues, selectedScoreId, onSelectScore, onLocateIssue }: {
  document: ScoresheetDocument;
  step: StepKey;
  readOnly: boolean;
  onChange: (document: ScoresheetDocument, immediate?: boolean) => void;
  issues: Array<{ region: ScoresheetRegion; path: string; message: string; severity: string }>;
  selectedScoreId: string;
  onSelectScore: (eventId: string) => void;
  onLocateIssue: (anchor: string) => void;
}) {
  return <MobileStandardView document={document} issues={issues} onChange={onChange} onLocateIssue={onLocateIssue} onSelectScore={onSelectScore} readOnly={readOnly} selectedScoreId={selectedScoreId} step={step} />;
}


function PublishPanel({ errors, warnings, validationReady, readOnly, busy, publish }: { errors: Array<{ id: string; message: string; region: ScoresheetRegion }>; warnings: Array<{ id: string; message: string; region: ScoresheetRegion }>; validationReady: boolean; readOnly: boolean; busy: boolean; publish: () => void }) {
  return <View className="mini-publish-panel"><View className="mini-publish-summary"><Text>服务端完整校验</Text><Text>{validationReady ? `${errors.length} 个错误 · ${warnings.length} 个提醒` : "发布时会重新校验当前草稿"}</Text></View>{errors.map((issue) => <View className="mini-publish-issue error" key={issue.id}><Text>{REGION_LABELS[issue.region]}</Text><Text>{issue.message}</Text></View>)}{warnings.map((issue) => <View className="mini-publish-issue" key={issue.id}><Text>{REGION_LABELS[issue.region]}</Text><View><Text>{issue.message}</Text></View></View>)}<Button className="mini-publish-button" disabled={readOnly || busy} onClick={publish}>{busy ? "发布中…" : "校验并发布"}</Button></View>;
}
