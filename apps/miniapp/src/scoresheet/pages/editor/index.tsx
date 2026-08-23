import {
  Button,
  Image,
  Input,
  Picker,
  ScrollView,
  Slider,
  Switch,
  Text,
  View,
} from "@tarojs/components";
import Taro, { useDidShow, useRouter, useUnload } from "@tarojs/taro";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ApiError,
  type ScoresheetDetail,
  type ScoresheetMutationContext,
} from "@pkuba/api-client";
import {
  deleteScoreAt,
  insertScoreAt,
  REGION_LABELS,
  SCORE_BLOCKS,
  SCORESHEET_REGIONS,
  scoreGridRow,
  TEMPLATE_REGION_BOUNDS,
  type ScoreEvent,
  type ScorePeriod,
  type ScoresheetDocument,
  type ScoresheetRegion,
  type TeamSide,
} from "@pkuba/scoresheet-domain";

import { absoluteMediaUrl, api, replaceGameMedia } from "../../../api";
import { getMiniAppSession } from "../../../auth";
import {
  type CanonicalScoresheetDocument,
  mergeMobileDocument,
  projectScoresheetDetail,
} from "../../mobileDocument";
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

function leaseStorageKey(scoresheetId: string) {
  return `pkuba-scoresheet-miniapp-lease:${scoresheetId}`;
}

function clearStoredLease(scoresheetId: string) {
  if (scoresheetId) Taro.removeStorageSync(leaseStorageKey(scoresheetId));
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
  const [sourceScale, setSourceScale] = useState(100);
  const [sourceRotation, setSourceRotation] = useState(0);
  const [history, setHistory] = useState<ScoresheetDocument[]>([]);
  const [future, setFuture] = useState<ScoresheetDocument[]>([]);
  const [selectedScoreId, setSelectedScoreId] = useState("");
  const clientIdRef = useRef(getClientId());
  const serverRef = useRef<ScoresheetDetail | null>(null);
  const canonicalRef = useRef<CanonicalScoresheetDocument | null>(null);
  const leaseRef = useRef("");
  const pendingRef = useRef<ScoresheetDocument | null>(null);
  const pendingBaseVersionRef = useRef<number | null>(null);
  const recoveryRef = useRef<{ local: ScoresheetDocument; baseVersion: number } | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const savingRef = useRef(false);
  const acquireFlightRef = useRef<Promise<void> | null>(null);
  const syncFlightRef = useRef<Promise<boolean> | null>(null);
  const heartbeatFlightRef = useRef<Promise<void> | null>(null);
  const flushRef = useRef<(changeType?: string, explicitSave?: boolean) => Promise<void>>(async () => undefined);

  const projectServer = useCallback((raw: ScoresheetDetail) => {
    const projection = projectScoresheetDetail(raw);
    if (projection.canonical) canonicalRef.current = projection.canonical;
    return projection.detail;
  }, []);

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
    const next = await api.getScoresheet(scoresheetId, token);
    return applyServer(next, Boolean(pendingRef.current));
  }, [applyServer, scoresheetId, token]);

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
        setSaveState("已保存");
        return;
      }
      const server = projectServer(await api.getScoresheet(scoresheetId, token));
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
        content: `差异字段：${documentDiffPaths(recovery.local, server.draft).join("、") || "多个字段"}。请选择本次提交使用的值。`,
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
        applyServer(await api.getScoresheet(scoresheetId, token));
        setSaveState("已采用服务器版本");
      }
    })();
    acquireFlightRef.current = operation;
    return operation.finally(() => {
      if (acquireFlightRef.current === operation) acquireFlightRef.current = null;
    });
  }, [applyServer, projectServer, scoresheetId, token]);

  useDidShow(() => {
    void (async () => {
      try {
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
    setSaveState("保存中…");
    try {
      const canonical = mergeMobileDocument(local, canonicalRef.current);
      const result = await api.saveScoresheetDraft(
        scoresheetId,
        mutation,
        [{ path: "/", operation: "SET", value: canonical }],
        token,
        { changeType, explicitSave },
      );
      applyServer(result, Boolean(pendingRef.current));
      pendingBaseVersionRef.current = pendingRef.current ? result.draft_version : null;
      setSaveState(pendingRef.current ? "等待保存…" : "已保存");
      setError("");
    } catch (reason) {
      const unsaved = pendingRef.current ?? local;
      pendingRef.current = null;
      if (reason instanceof ApiError && reason.code === "VERSION_CONFLICT") {
        const server = projectServer(await api.getScoresheet(scoresheetId, token));
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
          applyServer(server);
        }
      } else if (
        reason instanceof ApiError &&
        ["LEASE_LOST", "LEASE_REQUIRED"].includes(reason.code ?? "")
      ) {
        const server = projectServer(await api.getScoresheet(scoresheetId, token));
        leaseRef.current = "";
        clearStoredLease(scoresheetId);
        setReadOnly(true);
        if (server.draft_version === mutation.expected_version) {
          recoveryRef.current = { local: unsaved, baseVersion: server.draft_version };
          serverRef.current = server;
          setSheet({ ...server, draft: unsaved });
          setSaveState("编辑权已失效 · 本地输入已保留，等待自动恢复");
        } else {
          const choice = await Taro.showModal({
            title: "编辑权已失效",
            content: `服务器同时发生了修改：${documentDiffPaths(unsaved, server.draft).join("、") || "多个字段"}。取消采用服务器值；确认保留本地值并在接手后提交。`,
            cancelText: "服务器值",
            confirmText: "本地值",
          });
          if (choice.confirm) {
            recoveryRef.current = { local: unsaved, baseVersion: server.draft_version };
            serverRef.current = server;
            setSheet({ ...server, draft: unsaved });
            setSaveState("本地值已保留 · 等待自动恢复编辑");
          } else {
            applyServer(server);
            setSaveState("已采用服务器版本 · 当前为只读");
          }
        }
      } else {
        pendingRef.current = unsaved;
        pendingBaseVersionRef.current = mutation.expected_version;
        setSaveState("保存失败 · 输入仍保留");
        setError(message(reason));
      }
    } finally {
      savingRef.current = false;
      if (pendingRef.current && online) setTimeout(() => void flush(), 60);
    }
  }, [applyServer, context, online, projectServer, scoresheetId, token]);
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
    if (readOnly || !online) return;
    setHistory((rows) => [...rows.slice(-49), previous]);
    setFuture([]);
    if (!pendingRef.current) pendingBaseVersionRef.current = serverRef.current?.draft_version ?? null;
    pendingRef.current = next;
    setSheet((current) => (current ? { ...current, draft: next } : current));
    setSaveState("等待保存…");
    if (timerRef.current) clearTimeout(timerRef.current);
    if (immediate) void flush(changeType);
    else timerRef.current = setTimeout(() => void flush(changeType), 1000);
  }, [flush, online, readOnly]);

  const changePath = useCallback((path: string, value: unknown, immediate = false) => {
    if (!sheet) return;
    const previous = sheet.draft;
    const next = JSON.parse(JSON.stringify(previous)) as ScoresheetDocument;
    const parts = path.split("/").filter(Boolean);
    let cursor: any = next;
    for (const part of parts.slice(0, -1)) {
      cursor = cursor[Number.isInteger(Number(part)) ? Number(part) : part];
    }
    cursor[parts[parts.length - 1]] = value;
    queueDocument(next, previous, immediate);
  }, [queueDocument, sheet]);

  const sync = useCallback(() => {
    if (syncFlightRef.current) return syncFlightRef.current;
    const operation = (async () => {
      const current = serverRef.current;
      if (!current || !scoresheetId || !token) return true;
      try {
      const update = await api.syncScoresheet(
        scoresheetId,
        current.draft_version,
        current.event_sequence,
        token,
      );
      if ((update.events.length || update.requires_full_reload) && !savingRef.current) {
        const next = projectServer(await api.getScoresheet(scoresheetId, token));
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
            content: `差异字段：${documentDiffPaths(local, next.draft).join("、") || "多个字段"}。取消采用服务器值；确认提交本地值。`,
            cancelText: "服务器值",
            confirmText: "本地值",
          });
          if (choice.confirm && leaseRef.current) {
            serverRef.current = next;
            pendingRef.current = local;
            pendingBaseVersionRef.current = next.draft_version;
            setSheet({ ...next, draft: local });
          } else {
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
        if (online) setError(message(reason));
        return false;
      }
    })();
    syncFlightRef.current = operation;
    return operation.finally(() => {
      if (syncFlightRef.current === operation) syncFlightRef.current = null;
    });
  }, [acquire, applyServer, online, projectServer, scoresheetId, token]);

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
    if (leaseRef.current && token && !pendingRef.current && !savingRef.current) {
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
    if (!context()) return;
    if (!(await drainPending())) return;
    try {
      applyServer(await api.validateScoresheet(scoresheetId, context()!, token));
    } catch (reason) {
      setError(message(reason));
    }
  };

  const publish = async () => {
    if (!context()) return;
    if (!(await drainPending())) return;
    try {
      const validated = applyServer(await api.validateScoresheet(scoresheetId, context()!, token));
      const errors = validated.validation_report.errors ?? [];
      const warnings = validated.validation_report.warnings ?? [];
      if (errors.length > 0) {
        await Taro.showModal({
          title: "校验未通过",
          content: `仍有 ${errors.length} 个错误，请修正后重新发布。`,
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
          await api.acknowledgeScoresheetWarnings(scoresheetId, context()!, warningIds, token),
        );
      }
      applyServer(await api.publishScoresheet(scoresheetId, context()!, token));
      leaseRef.current = "";
      clearStoredLease(scoresheetId);
      setReadOnly(true);
      setSaveState("已发布");
      Taro.showToast({ title: "发布成功", icon: "success" });
    } catch (reason) {
      setError(message(reason));
    }
  };

  const undo = (direction: "UNDO" | "REDO") => {
    if (!sheet || readOnly) return;
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
    setSheet({ ...sheet, draft: target });
    void flush(direction);
  };

  if (!sheet) {
    return <View className="mini-sheet-loading"><Text>{error || "正在打开记录表…"}</Text></View>;
  }

  const currentKey = stepKey(stepIndex);
  const currentRegions = currentKey === "CLOSING"
    ? (["SUMMARY", "OFFICIALS"] as ScoresheetRegion[])
    : SCORESHEET_REGIONS.includes(currentKey as ScoresheetRegion)
      ? ([currentKey] as ScoresheetRegion[])
      : [];
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
              <View className={`mini-sheet-step ${stepIndex === index ? "active" : ""}`} key={step.key} onClick={() => setStepIndex(index)}>
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

      <ScrollView className="mini-sheet-content" scrollY enhanced showScrollbar={false}>
        {sheet.recognition && sheet.recognition.status !== "SUCCEEDED" && (
          <RecognitionBanner
            onRetry={async () => {
              try {
                if (!(await drainPending())) return;
                const mutation = context();
                if (!mutation) return;
                await api.retryScoresheetRecognition(scoresheetId, mutation, token);
                leaseRef.current = "";
                clearStoredLease(scoresheetId);
                setReadOnly(true);
                setSaveState("自动识别正在进行 · 当前只读");
                await load();
              } catch (reason) {
                setError(message(reason));
              }
            }}
            readOnly={readOnly || !online}
            recognition={sheet.recognition}
          />
        )}
        {view === "SOURCE" ? (
          <SourceView
            corners={sheet.draft.source_alignment.corners}
            currentRegion={currentRegions[0] ?? "SOURCE_GAME"}
            onCornersChange={(corners) => changePath("/source_alignment/corners", corners, true)}
            onReplace={async () => {
              if (!sheet.source || readOnly) return;
              const selected = await Taro.chooseMedia({ count: 1, mediaType: ["image"], sourceType: ["album", "camera"], sizeType: ["original"] });
              const file = selected.tempFiles[0];
              if (!file) return;
              const confirm = await Taro.showModal({ title: "重传原图", content: "重传会保留旧来源审计、重置识别额度并生成新草稿。", confirmText: "确认重传" });
              if (!confirm.confirm) return;
              await replaceGameMedia(sheet.source.id, sheet.source.version, file.tempFilePath, true, token);
              await load();
            }}
            onReload={() => void load().catch((reason) => setError(message(reason)))}
            readOnly={readOnly || !online}
            rotation={sourceRotation}
            scale={sourceScale}
            setRotation={setSourceRotation}
            setScale={setSourceScale}
            source={sheet.source}
          />
        ) : (
          <StandardView
            changePath={changePath}
            document={sheet.draft}
            issues={[...errors, ...warnings]}
            onSelectScore={setSelectedScoreId}
            readOnly={readOnly || !online}
            selectedScoreId={selectedScoreId}
            step={currentKey}
          />
        )}
        {error && <View className="mini-sheet-error">{error}</View>}
      </ScrollView>

      <View className="mini-sheet-footer">
        <Button disabled={stepIndex === 0} onClick={() => setStepIndex((value) => Math.max(0, value - 1))}>上一步</Button>
        {currentKey === "PUBLISH" ? (
          <Button className="review" disabled={readOnly || !online} onClick={() => void validate()}>重新校验</Button>
        ) : (
          <Button className="review" disabled={readOnly || !online} onClick={() => void drainPending("EXPLICIT_SAVE")}>保存草稿</Button>
        )}
        <Button disabled={stepIndex === STEPS.length - 1} onClick={() => setStepIndex((value) => Math.min(STEPS.length - 1, value + 1))}>下一步</Button>
      </View>

      {currentKey === "PUBLISH" && view === "STANDARD" && (
        <PublishPanel
          errors={errors}
          publish={publish}
          readOnly={readOnly || !online}
          validationReady={sheet.status === "READY" && sheet.validation_draft_version === sheet.draft_version}
          warnings={warnings}
        />
      )}
    </View>
  );
}

function stepKey(index: number): StepKey {
  return STEPS[index]?.key ?? "SOURCE_GAME";
}

function RecognitionBanner({ recognition, readOnly, onRetry }: {
  recognition: NonNullable<ScoresheetDetail["recognition"]>;
  readOnly: boolean;
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
        <Text>{recognition.status === "RETRY_WAIT" && retrySeconds !== null ? `${retrySeconds} 秒后自动继续` : recognition.status === "FAILED" ? "可以手工录入，或重新识别" : "完成后会自动显示核对内容"}</Text>
      </View>
      {recognition.status === "FAILED" && (
        <Button disabled={readOnly} onClick={() => void onRetry()}>重新识别</Button>
      )}
    </View>
  );
}

function SourceView({ source, scale, rotation, setScale, setRotation, readOnly, onReplace, onReload, corners, currentRegion, onCornersChange }: {
  source: ScoresheetDetail["source"];
  scale: number;
  rotation: number;
  setScale: (value: number) => void;
  setRotation: (value: number) => void;
  readOnly: boolean;
  onReplace: () => void;
  onReload: () => void;
  corners: Array<{ x: number; y: number }>;
  currentRegion: ScoresheetRegion;
  onCornersChange: (corners: Array<{ x: number; y: number }>) => void;
}) {
  const [aligning, setAligning] = useState(false);
  const regionBox = corners.length === 4 ? projectedRegionBox(corners, currentRegion) : null;
  const beginAlignment = () => {
    if (readOnly) return;
    setScale(100);
    setRotation(0);
    onCornersChange([]);
    setAligning(true);
  };
  const captureCorner = (event: unknown) => {
    if (!aligning || readOnly) return;
    const tap = event as {
      detail?: { x?: number; y?: number };
      changedTouches?: Array<{ clientX?: number; clientY?: number }>;
    };
    const pageX = tap.detail?.x ?? tap.changedTouches?.[0]?.clientX;
    const pageY = tap.detail?.y ?? tap.changedTouches?.[0]?.clientY;
    if (pageX === undefined || pageY === undefined) return;
    Taro.createSelectorQuery()
      .select("#scoresheet-source-stage")
      .boundingClientRect((rect) => {
        const box = rect as unknown as { left: number; top: number; width: number; height: number } | null;
        if (!box?.width || !box.height) return;
        const point = {
          x: Math.max(0, Math.min(1, (pageX - box.left) / box.width)),
          y: Math.max(0, Math.min(1, (pageY - box.top) / box.height)),
        };
        const next = [...corners, point].slice(0, 4);
        onCornersChange(next);
        if (next.length === 4) setAligning(false);
      })
      .exec();
  };
  return (
    <View className="mini-source-view">
      <View className="mini-source-tools">
        <Text>缩放 {scale}%</Text>
        <Slider blockSize={18} max={220} min={60} onChange={(event) => setScale(event.detail.value)} step={10} value={scale} />
        <Button onClick={() => setRotation((rotation + 90) % 360)}>旋转 90°</Button>
      </View>
      <ScrollView className="mini-source-canvas" scrollX scrollY enhanced>
        {source ? (
          <View
            className={aligning ? "mini-source-stage aligning" : "mini-source-stage"}
            id="scoresheet-source-stage"
            onClick={captureCorner}
            style={{ transform: `scale(${scale / 100}) rotate(${rotation}deg)` }}
          >
            <Image className="mini-source-image" mode="widthFix" src={absoluteMediaUrl(source.url)} />
            {corners.map((corner, index) => <View className="mini-alignment-corner" key={`${corner.x}-${corner.y}-${index}`} style={{ left: `${corner.x * 100}%`, top: `${corner.y * 100}%` }}><Text>{index + 1}</Text></View>)}
            {regionBox && <View className="mini-source-region-box" style={{ left: `${regionBox.left * 100}%`, top: `${regionBox.top * 100}%`, width: `${regionBox.width * 100}%`, height: `${regionBox.height * 100}%` }}><Text>{REGION_LABELS[currentRegion]}</Text></View>}
          </View>
        ) : <Text className="mini-source-missing">当前原图不存在。</Text>}
      </ScrollView>
      <View className="mini-alignment-actions">
        <Text>{aligning ? `请依次点选左上、右上、右下、左下（${corners.length}/4）` : corners.length === 4 ? `已对齐 · 正在标出${REGION_LABELS[currentRegion]}` : "未对齐 · 不显示字段定位"}</Text>
        {!readOnly && source && <Button onClick={beginAlignment}>{corners.length === 4 ? "重新对齐" : "四角对齐"}</Button>}
      </View>
      <Text className="mini-source-hint">四角对齐只用于人工核对；模型始终读取完整的安全预处理图。</Text>
      {source && <Button className="mini-source-reload" onClick={onReload}>重新载入原图</Button>}
      {!readOnly && source && <Button className="mini-source-replace" onClick={onReplace}>重传新原图</Button>}
    </View>
  );
}

function projectedRegionBox(corners: Array<{ x: number; y: number }>, region: ScoresheetRegion) {
  const bounds = TEMPLATE_REGION_BOUNDS[region];
  const points = [
    projectPoint(corners, bounds.x / 595.2, bounds.y / 841.8),
    projectPoint(corners, (bounds.x + bounds.width) / 595.2, bounds.y / 841.8),
    projectPoint(corners, (bounds.x + bounds.width) / 595.2, (bounds.y + bounds.height) / 841.8),
    projectPoint(corners, bounds.x / 595.2, (bounds.y + bounds.height) / 841.8),
  ];
  const xs = points.map((point) => point.x);
  const ys = points.map((point) => point.y);
  const left = Math.min(...xs);
  const top = Math.min(...ys);
  return { left, top, width: Math.max(...xs) - left, height: Math.max(...ys) - top };
}

function projectPoint(corners: Array<{ x: number; y: number }>, u: number, v: number) {
  const [topLeft, topRight, bottomRight, bottomLeft] = corners;
  return {
    x: (1 - u) * (1 - v) * topLeft.x + u * (1 - v) * topRight.x + u * v * bottomRight.x + (1 - u) * v * bottomLeft.x,
    y: (1 - u) * (1 - v) * topLeft.y + u * (1 - v) * topRight.y + u * v * bottomRight.y + (1 - u) * v * bottomLeft.y,
  };
}

function StandardView({ document, step, readOnly, changePath, issues, selectedScoreId, onSelectScore }: {
  document: ScoresheetDocument;
  step: StepKey;
  readOnly: boolean;
  changePath: (path: string, value: unknown, immediate?: boolean) => void;
  issues: Array<{ region: ScoresheetRegion; path: string; message: string; severity: string }>;
  selectedScoreId: string;
  onSelectScore: (eventId: string) => void;
}) {
  if (step === "SOURCE_GAME") {
    return <MiniForm>{["competition", "date", "scheduled_time", "venue"].map((key) => <MiniReadOnlyRow key={key} label={gameLabel(key)} value={document.game[key] ?? ""} />)}</MiniForm>;
  }
  if (step === "TEAM_A" || step === "TEAM_B") {
    const side = step.endsWith("A") ? "A" : "B";
    return <MiniTeamEditor changePath={changePath} document={document} readOnly={readOnly} side={side} />;
  }
  if (step === "RUNNING_SCORE") {
    return <MiniPaperScoreGrid document={document} issues={issues.filter((issue) => issue.region === "RUNNING_SCORE")} onChange={(events) => changePath("/running_score", events, true)} onSelect={onSelectScore} readOnly={readOnly} selectedId={selectedScoreId} />;
  }
  if (step === "CLOSING") {
    return <MiniClosingEditor changePath={changePath} document={document} readOnly={readOnly} />;
  }
  return <View className="mini-publish-placeholder"><Text>先执行服务端校验，再逐项处理错误和提醒。</Text></View>;
}

function MiniClosingEditor({ document, readOnly, changePath }: {
  document: ScoresheetDocument;
  readOnly: boolean;
  changePath: (path: string, value: unknown, immediate?: boolean) => void;
}) {
  const periods = (["1", "2", "3", "4", "5", "6", "7", "8"] as ScorePeriod[]);
  const final = periods.reduce((total, period) => ({
    A: total.A + (document.summary.period_scores[period].A ?? 0),
    B: total.B + (document.summary.period_scores[period].B ?? 0),
  }), { A: 0, B: 0 });
  const winner = final.A > final.B ? document.teams.A.name : final.B > final.A ? document.teams.B.name : "平分（不能发布）";
  const updatePeriod = (period: ScorePeriod, side: TeamSide, value: number) => {
    const next = { ...document.summary.period_scores[period], [side]: value };
    const all = { ...document.summary.period_scores, [period]: next };
    const nextFinal = periods.reduce((total, key) => ({ A: total.A + (all[key].A ?? 0), B: total.B + (all[key].B ?? 0) }), { A: 0, B: 0 });
    changePath("/summary", {
      ...document.summary,
      period_scores: all,
      final_score: nextFinal,
      winner_side: nextFinal.A > nextFinal.B ? "A" : nextFinal.B > nextFinal.A ? "B" : "",
    }, true);
  };
  return (
    <View className="mini-closing-editor">
      <View className="mini-section-heading"><Text>节比分与最终结果</Text></View>
      {periods.map((period) => (
        <View className="mini-period-row" key={period}>
          <Text>{Number(period) <= 4 ? `第 ${period} 节` : `加时 ${Number(period) - 4}`}</Text>
          {(["A", "B"] as TeamSide[]).map((side) => <MiniStepper disabled={readOnly} key={side} label={side} max={160} onChange={(value) => updatePeriod(period, side, value)} value={document.summary.period_scores[period][side] ?? 0} />)}
        </View>
      ))}
      <View className="mini-derived-result"><Text>最终比分</Text><Text>{document.teams.A.name} {final.A} : {final.B} {document.teams.B.name}</Text><Text>胜队：{winner}</Text></View>
      <Picker disabled={readOnly} mode="time" onChange={(event) => changePath("/summary/ended_at", String(event.detail.value), true)} value={document.summary.ended_at || "00:00"}><View className="mini-input-row"><Text>比赛结束时间</Text><Text>{document.summary.ended_at || "请选择"}</Text></View></Picker>
      <View className="mini-section-heading"><Text>工作人员</Text><Text>没有或看不清时留空</Text></View>
      <MiniForm>
        {document.table_personnel.map((name, index) => <View className="mini-personnel-row" key={index}><MiniInput disabled={readOnly} label={`记录台人员 ${index + 1}`} onChange={(value) => changePath("/table_personnel", document.table_personnel.map((item, itemIndex) => itemIndex === index ? value : item))} value={name} /><Button disabled={readOnly} onClick={() => changePath("/table_personnel", document.table_personnel.filter((_, itemIndex) => itemIndex !== index), true)}>删除</Button></View>)}
        <Button className="mini-personnel-add" disabled={readOnly} onClick={() => changePath("/table_personnel", [...document.table_personnel, ""], true)}>添加记录台人员</Button>
        {["scorer", "assistant_scorer", "timer", "shot_clock_operator", "crew_chief", "umpire_1", "umpire_2", "protest_captain"].map((key) => <MiniInput disabled={readOnly} key={key} label={officialLabel(key)} onChange={(value) => changePath(`/officials/${key}`, value)} value={String(document.officials[key] ?? "")} />)}
      </MiniForm>
    </View>
  );
}

function MiniForm({ children }: { children: React.ReactNode }) {
  return <View className="mini-sheet-form">{children}</View>;
}

function MiniInput({ label, value, disabled, onChange }: { label: string; value: string; disabled: boolean; onChange: (value: string) => void }) {
  return <View className="mini-input-row"><Text>{label}</Text><Input disabled={disabled} onInput={(event) => onChange(event.detail.value)} value={value} /></View>;
}

function MiniReadOnlyRow({ label, value }: { label: string; value: string }) {
  return <View className="mini-input-row locked"><View><Text>{label}</Text><Text className="mini-lock-label">由赛程确定</Text></View><Text>{value || "—"}</Text></View>;
}

function MiniStepper({ label, value, disabled, max, onChange }: { label: string; value: number; disabled: boolean; max: number; onChange: (value: number) => void }) {
  return <View className="mini-stepper"><Text>{label}</Text><Button disabled={disabled || value <= 0} onClick={() => onChange(Math.max(0, value - 1))}>−</Button><Text>{value}</Text><Button disabled={disabled || value >= max} onClick={() => onChange(Math.min(max, value + 1))}>＋</Button></View>;
}

function MiniTeamEditor({ document, side, readOnly, changePath }: {
  document: ScoresheetDocument;
  side: TeamSide;
  readOnly: boolean;
  changePath: (path: string, value: unknown, immediate?: boolean) => void;
}) {
  const team = document.teams[side];
  const resizeMarks = (values: unknown[], count: number, factory: (slot: number) => unknown) => [
    ...values.slice(0, count),
    ...Array.from({ length: Math.max(0, count - values.length) }, (_, index) => factory(values.length + index + 1)),
  ];
  return (
    <View className="mini-team-editor">
      <View className="mini-section-heading"><Text>球队 {side}</Text><Text>{team.name}</Text></View>
      <View className="mini-team-paper-fields">
        {(["H1", "H2", "OT"] as const).map((scope) => (
          <MiniStepper disabled={readOnly} key={scope} label={`暂停 ${scope}`} max={3} onChange={(count) => changePath(`/teams/${side}/timeouts/${scope}`, resizeMarks(team.timeouts[scope] ?? [], count, (slot) => ({ slot, minute: 0 })))} value={(team.timeouts[scope] ?? []).length} />
        ))}
        {(["1", "2", "3", "4"] as const).map((period) => (
          <MiniStepper disabled={readOnly} key={period} label={`第 ${period} 节全队犯规`} max={4} onChange={(count) => changePath(`/teams/${side}/team_fouls/${period}`, Array(count).fill("X"))} value={(team.team_fouls[period] ?? []).length} />
        ))}
        {(["head_coach", "assistant_coach"] as const).map((role) => (
          <View className="mini-coach-row" key={role}>
            <View className="mini-team-field"><Text>{role === "head_coach" ? "教练员" : "助理教练员"}</Text><Input disabled={readOnly} onInput={(event) => changePath(`/teams/${side}/${role}/name`, event.detail.value)} value={team[role].name} /></View>
            <View className="mini-foul-strip"><Text>犯规</Text>{Array.from({ length: 3 }, (_, index) => <FoulSlotPicker disabled={readOnly || (index > 0 && !team[role].fouls[index - 1])} group="coach" key={index} onChange={(value) => changePath(`/teams/${side}/${role}/fouls`, replaceFoulSlot(team[role].fouls, index, value), true)} value={team[role].fouls[index]} />)}<Text className="mini-post-label">附加</Text>{Array.from({ length: 2 }, (_, slot) => {
              const markers = role === "head_coach" ? team.coach_post_foul_markers ?? [] : team.assistant_coach_post_foul_markers ?? [];
              const field = role === "head_coach" ? "coach_post_foul_markers" : "assistant_coach_post_foul_markers";
              return <FoulSlotPicker disabled={readOnly || team[role].fouls.length < 3 || (slot > 0 && !markers[slot - 1])} group="post" key={`post-${slot}`} onChange={(value) => changePath(`/teams/${side}/${field}`, replaceFoulSlot(markers, slot, value), true)} value={markers[slot]} />;
            })}</View>
          </View>
        ))}
      </View>
      {team.players.map((player, index) => (
        <View className="mini-player-row" key={player.player_id}>
          <View className="mini-player-name"><Text className="mini-player-number">{player.jersey_number || "–"}</Text><Text>{player.name}</Text></View>
          <View className="mini-player-state">
            <Picker disabled={readOnly} mode="selector" range={["未上场", "替补", "首发"]} value={player.starter ? 2 : player.appeared ? 1 : 0} onChange={(event) => { const state = Number(event.detail.value); changePath(`/teams/${side}/players/${index}`, { ...player, appeared: state > 0, starter: state === 2 }, true); }}><Text>{player.starter ? "首发" : player.appeared ? "替补" : "未上场"}</Text></Picker>
            <View><Text>队长</Text><Switch checked={player.captain} color="#07c160" disabled={readOnly} onChange={(event) => changePath(`/teams/${side}/players/${index}/captain`, event.detail.value, true)} /></View>
          </View>
          <View className="mini-foul-strip"><Text>犯规</Text>{Array.from({ length: 5 }, (_, slot) => <FoulSlotPicker disabled={readOnly || (slot > 0 && !player.fouls[slot - 1])} group="player" key={slot} onChange={(value) => changePath(`/teams/${side}/players/${index}/fouls`, replaceFoulSlot(player.fouls, slot, value), true)} value={player.fouls[slot]} />)}<Text className="mini-post-label">附加</Text>{Array.from({ length: 2 }, (_, slot) => <FoulSlotPicker disabled={readOnly || player.fouls.length < 5 || (slot > 0 && !player.post_foul_markers?.[slot - 1])} group="post" key={`post-${slot}`} onChange={(value) => changePath(`/teams/${side}/players/${index}/post_foul_markers`, replaceFoulSlot(player.post_foul_markers ?? [], slot, value), true)} value={player.post_foul_markers?.[slot]} />)}</View>
        </View>
      ))}
    </View>
  );
}

type MiniFoul = { code: string; free_throws?: number | null; cancelled?: boolean; slot?: number; catalog_id?: string | null; mark_style?: string; period?: number | null };

const SUBSCRIPT: Record<string, string> = { "1": "₁", "2": "₂", "3": "₃", c: "c" };

function foulChoices(group: "player" | "coach" | "post") {
  const codes = group === "player" ? ["P", "T", "U", "D"] : group === "coach" ? ["C", "B", "D", "F"] : ["D", "GD", "F"];
  const result: Array<{ label: string; value: MiniFoul | null }> = [{ label: "空", value: null }];
  for (const code of codes) {
    const suffixes = ["", ...(code === "F" || code === "GD" ? [] : ["1", "2", "3", "c"])];
    for (const suffix of suffixes) result.push({
      label: `${code}${SUBSCRIPT[suffix] ?? ""}`,
      value: { code, free_throws: /^[123]$/.test(suffix) ? Number(suffix) : null, cancelled: suffix === "c", catalog_id: null, mark_style: "plain", period: null },
    });
  }
  return result;
}

function foulLabel(value: unknown) {
  if (!value || typeof value !== "object") return String(value || "＋");
  const foul = value as MiniFoul;
  const suffix = foul.cancelled ? "c" : foul.free_throws ? String(foul.free_throws) : "";
  return `${foul.code}${SUBSCRIPT[suffix] ?? ""}`;
}

function replaceFoulSlot(values: unknown[], index: number, value: MiniFoul | null) {
  if (!value) return values.slice(0, index);
  const next = values.slice(0, index + 1);
  next[index] = { ...value, slot: index + 1 };
  return next.map((row, slot) => typeof row === "object" && row ? { ...row as object, slot: slot + 1 } : row);
}

function FoulSlotPicker({ group, value, disabled, onChange }: { group: "player" | "coach" | "post"; value: unknown; disabled: boolean; onChange: (value: MiniFoul | null) => void }) {
  const choices = foulChoices(group);
  const current = foulLabel(value);
  const selected = Math.max(0, choices.findIndex((choice) => choice.label === current));
  return <Picker disabled={disabled} mode="selector" range={choices.map((choice) => choice.label)} value={selected} onChange={(event) => onChange(choices[Number(event.detail.value)]?.value ?? null)}><View className={value ? "mini-foul-cell filled" : "mini-foul-cell"}><Text>{current}</Text></View></Picker>;
}

function MiniPaperScoreGrid({ document, issues, readOnly, onChange, selectedId, onSelect }: {
  document: ScoresheetDocument;
  issues: Array<{ path: string }>;
  readOnly: boolean;
  onChange: (events: ScoreEvent[]) => void;
  selectedId: string;
  onSelect: (eventId: string) => void;
}) {
  const [blockIndex, setBlockIndex] = useState(0);
  const [issueCursor, setIssueCursor] = useState(-1);
  const [period, setPeriod] = useState<ScorePeriod>("1");
  const [pendingCell, setPendingCell] = useState<{ side: TeamSide; cumulative: number } | null>(null);
  const block = SCORE_BLOCKS[blockIndex];
  const selected = document.running_score.find((event) => event.id === selectedId) ?? null;
  const scrollEvent = selected ?? document.running_score[document.running_score.length - 1];
  const scrollTarget = scrollEvent && scoreGridRow(scrollEvent.cumulative)?.block === blockIndex
    ? `mini-score-row-${scrollEvent.cumulative}`
    : "";
  const byCell = useMemo(
    () => new Map(document.running_score.map((event) => [`${event.team}-${event.cumulative}`, event])),
    [document.running_score],
  );
  useEffect(() => {
    const selectedEvent = document.running_score.find((event) => event.id === selectedId);
    const event = selectedEvent ?? document.running_score[document.running_score.length - 1];
    const location = event ? scoreGridRow(event.cumulative) : null;
    if (location) setBlockIndex(location.block);
  }, [document.running_score.length, selectedId]);
  const issueEvents = useMemo(() => {
    const seen = new Set<string>();
    const result: ScoreEvent[] = [];
    for (const issue of issues) {
      const indexMatch = issue.path.match(/\/running_score\/(\d+)/);
      const index = indexMatch ? Number(indexMatch[1]) : -1;
      const event = index >= 0
        ? document.running_score[index]
        : document.running_score.find((row) => issue.path.includes(row.id));
      if (event && !seen.has(event.id)) {
        seen.add(event.id);
        result.push(event);
      }
    }
    return result;
  }, [document.running_score, issues]);

  const jumpIssue = (direction: -1 | 1) => {
    if (!issueEvents.length) return;
    const nextCursor = (issueCursor + direction + issueEvents.length) % issueEvents.length;
    const event = issueEvents[nextCursor];
    const location = scoreGridRow(event.cumulative);
    setIssueCursor(nextCursor);
    onSelect(event.id);
    if (location) setBlockIndex(location.block);
  };

  return (
    <View className="mini-paper-score">
      <View className="mini-score-period-picker"><Text>当前节次</Text><Picker disabled={readOnly} mode="selector" range={["第1节", "第2节", "第3节", "第4节", "加时1", "加时2", "加时3", "加时4"]} value={Number(period) - 1} onChange={(event) => setPeriod(String(Number(event.detail.value) + 1) as ScorePeriod)}><Text>{Number(period) <= 4 ? `第 ${period} 节` : `加时 ${Number(period) - 4}`}</Text></Picker><Text>点空格后选球员号码</Text></View>
      {issueEvents.length > 0 && <View className="mini-score-issue-nav"><Text>{issueEvents.length} 处得分格问题</Text><View><Button onClick={() => jumpIssue(-1)}>上一处</Button><Button onClick={() => jumpIssue(1)}>下一处</Button></View></View>}
      <ScrollView className="mini-score-block-tabs" scrollX>
        <View>{SCORE_BLOCKS.map((item, index) => <Button className={index === blockIndex ? "active" : ""} key={item.key} onClick={() => setBlockIndex(index)}>{item.key}</Button>)}</View>
      </ScrollView>
      <ScrollView className="mini-score-grid-scroll" scrollIntoView={scrollTarget} scrollY showScrollbar={false}>
        <View className="mini-score-grid-heading"><Text>累计</Text><Text>A 队</Text><Text>B 队</Text></View>
        {Array.from({ length: 40 }, (_, index) => block.start + index).map((score) => (
          <View className="mini-score-grid-row" id={`mini-score-row-${score}`} key={score}>
            <Text>{score}</Text>
            {(["A", "B"] as TeamSide[]).map((side) => {
              const event = byCell.get(`${side}-${score}`);
              const invalid = event && issues.some((issue) => scoreIssueMatches(issue.path, event));
              const unusual = Boolean(event && event.value >= 4);
              return <View className={`mini-score-cell ${invalid || unusual ? "invalid" : ""} ${event?.id === selectedId ? "selected" : ""}`} key={side} onClick={() => { if (event) onSelect(event.id); else if (!readOnly) setPendingCell({ side, cumulative: score }); }}>{event ? <><Text className="mini-score-player">{event.player_number || "?"}</Text><Text className="mini-score-mark">{event.value === 1 ? "●" : event.value === 3 ? "◯" : "╱"}</Text><Text className="mini-score-period">{event.value >= 4 ? `+${event.value}` : Number(event.period) <= 4 ? event.period : `OT${Number(event.period) - 4}`}</Text></> : <Text className="mini-score-empty">＋</Text>}</View>;
            })}
          </View>
        ))}
      </ScrollView>
      {(selected || pendingCell) && <ScoreEventDrawer cell={pendingCell} document={document} event={selected} initialPeriod={period} onChange={onChange} onClose={() => { onSelect(""); setPendingCell(null); }} />}
    </View>
  );
}

function scoreIssueMatches(path: string, event: ScoreEvent) {
  if (path.includes(event.id)) return true;
  const match = path.match(/\/running_score\/(\d+)/);
  return Boolean(match && Number(match[1]) === event.sequence - 1);
}

function ScoreEventDrawer({ document, event, cell, initialPeriod, onChange, onClose }: { document: ScoresheetDocument; event: ScoreEvent | null; cell: { side: TeamSide; cumulative: number } | null; initialPeriod: ScorePeriod; onChange: (events: ScoreEvent[]) => void; onClose: () => void }) {
  const side = event?.team ?? cell?.side ?? "A";
  const cumulative = event?.cumulative ?? cell?.cumulative ?? 0;
  const players = document.teams[side].players.filter((player) => player.jersey_number);
  const update = (patch: Partial<ScoreEvent>) => {
    if (!event) return;
    onChange(document.running_score.map((row) => row.id === event.id ? { ...row, ...patch } : row));
  };
  const playerIndex = Math.max(0, players.findIndex((player) => player.player_id === event?.player_id));
  const choosePlayer = (index: number) => {
    const player = players[index];
    if (!player) return;
    if (event) update({ player_id: player.player_id, player_name: player.name, player_number: player.jersey_number });
    else if (cell) onChange(insertScoreAt(document.running_score, { id: `cell-${Date.now()}-${Math.random().toString(36).slice(2)}`, team: cell.side, cumulative: cell.cumulative, period: initialPeriod, player_id: player.player_id, player_name: player.name, player_number: player.jersey_number, boundary: "none" }));
    if (!event) onClose();
  };
  return <View className="mini-score-drawer-mask" onClick={onClose}><View className="mini-score-drawer" onClick={(click) => click.stopPropagation()}><View className="mini-drawer-heading"><Text>{side} 队累计 {cumulative} 分</Text><Button onClick={onClose}>完成</Button></View><Picker mode="selector" range={players.map((player) => `${player.jersey_number} ${player.name}`)} value={playerIndex} onChange={(change) => choosePlayer(Number(change.detail.value))}><View className="mini-drawer-field"><Text>球员号码</Text><Text>{event ? event.player_number || "请选择" : "点击选择后插入"}</Text></View></Picker>{event && <><View className={event.value >= 4 ? "mini-score-derived invalid" : "mini-score-derived"}><Text>本次得分</Text><Text>{event.value} 分（由累计格自动推导）</Text></View><Picker mode="selector" range={["第1节", "第2节", "第3节", "第4节", "加时1", "加时2", "加时3", "加时4"]} value={Number(event.period) - 1} onChange={(change) => update({ period: String(Number(change.detail.value) + 1) as ScorePeriod })}><View className="mini-drawer-field"><Text>节次</Text><Text>{Number(event.period) <= 4 ? `第 ${event.period} 节` : `加时 ${Number(event.period) - 4}`}</Text></View></Picker><Picker mode="selector" range={["普通", "节末", "终场"]} value={["none", "period", "game"].indexOf(event.boundary ?? "none")} onChange={(change) => update({ boundary: (["none", "period", "game"] as const)[Number(change.detail.value)] })}><View className="mini-drawer-field"><Text>标记</Text><Text>{event.boundary === "game" ? "终场" : event.boundary === "period" ? "节末" : "普通"}</Text></View></Picker><Button className="mini-drawer-delete" onClick={() => { onChange(deleteScoreAt(document.running_score, event.id)); onClose(); }}>清空此格号码</Button></>}</View></View>;
}

function PublishPanel({ errors, warnings, validationReady, readOnly, publish }: { errors: Array<{ id: string; message: string; region: ScoresheetRegion }>; warnings: Array<{ id: string; message: string; region: ScoresheetRegion }>; validationReady: boolean; readOnly: boolean; publish: () => void }) {
  return <View className="mini-publish-panel"><View className="mini-publish-summary"><Text>保存后由服务端完整校验</Text><Text>{validationReady ? `${errors.length} 个错误 · ${warnings.length} 个提醒` : "发布时会自动重新校验当前草稿"}</Text></View>{errors.map((issue) => <View className="mini-publish-issue error" key={issue.id}><Text>{REGION_LABELS[issue.region]}</Text><Text>{issue.message}</Text></View>)}{warnings.map((issue) => <View className="mini-publish-issue" key={issue.id}><Text>{REGION_LABELS[issue.region]}</Text><View><Text>{issue.message}</Text></View></View>)}<Button className="mini-publish-button" disabled={readOnly} onClick={publish}>校验并发布</Button></View>;
}

function gameLabel(key: string) {
  return ({ competition: "赛事", date: "日期", scheduled_time: "开赛时间", game_number: "比赛编号", venue: "场地", crew_chief: "主裁判", umpire_1: "副裁判 1", umpire_2: "副裁判 2" } as Record<string, string>)[key] ?? key;
}

function officialLabel(key: string) {
  return ({ scorer: "记录员", assistant_scorer: "助理记录员", timer: "计时员", shot_clock_operator: "24 秒计时员", crew_chief_signature: "主裁判签名", umpire_1_signature: "副裁判 1 签名", umpire_2_signature: "副裁判 2 签名", captain_protest_signature: "队长抗议签名" } as Record<string, string>)[key] ?? key;
}
