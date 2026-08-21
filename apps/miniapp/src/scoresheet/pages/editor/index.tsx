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
  addScoreEvent,
  canPlaceScore,
  deleteScoreEvent,
  normalizeScoreEvents,
  REGION_LABELS,
  SCORE_BLOCKS,
  SCORESHEET_REGIONS,
  scoreGridRow,
  TEMPLATE_REGION_BOUNDS,
  teamTotal,
  type ScoreEvent,
  type ScorePeriod,
  type ScoresheetDocument,
  type ScoresheetRegion,
  type ScoreValue,
  type TeamSide,
} from "@pkuba/scoresheet-domain";

import { absoluteMediaUrl, api, replaceGameMedia } from "../../../api";
import { getMiniAppSession } from "../../../auth";
import "./index.css";

type StepKey = "SOURCE" | ScoresheetRegion | "CLOSING" | "PUBLISH";

const STEPS: Array<{ key: StepKey; label: string }> = [
  { key: "SOURCE", label: "原图" },
  { key: "SOURCE_GAME", label: "比赛信息" },
  { key: "TEAM_A", label: "A 队" },
  { key: "TEAM_B", label: "B 队" },
  { key: "RUNNING_SCORE", label: "逐次得分" },
  { key: "CLOSING", label: "结表信息" },
  { key: "PUBLISH", label: "校验发布" },
];

const CLIENT_KEY = "pkuba-scoresheet-miniapp-client";

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
  const [leaseToken, setLeaseToken] = useState("");
  const [readOnly, setReadOnly] = useState(true);
  const [saveState, setSaveState] = useState("正在连接");
  const [error, setError] = useState("");
  const [online, setOnline] = useState(true);
  const [sourceScale, setSourceScale] = useState(100);
  const [sourceRotation, setSourceRotation] = useState(0);
  const [history, setHistory] = useState<ScoresheetDocument[]>([]);
  const [future, setFuture] = useState<ScoresheetDocument[]>([]);
  const [accountRole, setAccountRole] = useState("");
  const [selectedScoreId, setSelectedScoreId] = useState("");
  const clientIdRef = useRef(getClientId());
  const serverRef = useRef<ScoresheetDetail | null>(null);
  const leaseRef = useRef("");
  const pendingRef = useRef<ScoresheetDocument | null>(null);
  const pendingBaseVersionRef = useRef<number | null>(null);
  const recoveryRef = useRef<{ local: ScoresheetDocument; baseVersion: number } | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const savingRef = useRef(false);

  const applyServer = useCallback((next: ScoresheetDetail, preservePending = false) => {
    serverRef.current = next;
    setSheet({
      ...next,
      draft: preservePending && pendingRef.current ? pendingRef.current : next.draft,
    });
  }, []);

  const load = useCallback(async () => {
    if (!scoresheetId || !token) throw new Error("记录表参数或登录状态无效");
    const next = await api.getScoresheet(scoresheetId, token);
    applyServer(next, Boolean(pendingRef.current));
    return next;
  }, [applyServer, scoresheetId, token]);

  const acquire = useCallback(async () => {
    const result = await api.acquireScoresheetLease(
      scoresheetId,
      clientIdRef.current,
      "MINIAPP",
      token,
    );
    if (result.read_only || !result.lease_token) {
      leaseRef.current = "";
      setLeaseToken("");
      setReadOnly(true);
      setSaveState(`${result.holder.username} 正在通过${result.holder.surface === "WEB" ? "网页" : "小程序"}编辑`);
      return;
    }
    leaseRef.current = result.lease_token;
    setLeaseToken(result.lease_token);
    setReadOnly(false);
    setSaveState("已保存");
  }, [scoresheetId, token]);

  useDidShow(() => {
    void (async () => {
      try {
        await load();
        const me = await api.getMiniAppMe(token);
        setAccountRole(me.account?.role ?? "");
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
      const result = await api.saveScoresheetDraft(
        scoresheetId,
        mutation,
        [{ path: "/", operation: "SET", value: local }],
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
        const server = await api.getScoresheet(scoresheetId, token);
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
        const server = await api.getScoresheet(scoresheetId, token);
        leaseRef.current = "";
        setLeaseToken("");
        setReadOnly(true);
        if (server.draft_version === mutation.expected_version) {
          recoveryRef.current = { local: unsaved, baseVersion: server.draft_version };
          serverRef.current = server;
          setSheet({ ...server, draft: unsaved });
          setSaveState("编辑权已失效 · 本地输入已保留，接手后恢复");
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
            setSaveState("本地值已保留 · 请重新接手编辑");
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
  }, [applyServer, context, online, scoresheetId, token]);

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

  const sync = useCallback(async () => {
    const current = serverRef.current;
    if (!current || !scoresheetId || !token) return;
    try {
      const update = await api.syncScoresheet(
        scoresheetId,
        current.draft_version,
        current.event_sequence,
        token,
      );
      if ((update.events.length || update.requires_full_reload) && !savingRef.current) {
        const next = await api.getScoresheet(scoresheetId, token);
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
        setLeaseToken("");
        setReadOnly(true);
        setSaveState("编辑权已转移 · 本地输入已保留");
      }
      if (!leaseRef.current && !update.lease && !recoveryRef.current) setSaveState("编辑权已释放 · 可以接手");
    } catch (reason) {
      if (online) setError(message(reason));
    }
  }, [applyServer, online, scoresheetId, token]);

  useEffect(() => {
    const poll = setInterval(() => void sync(), 2000);
    const heartbeat = setInterval(() => {
      if (!leaseRef.current) return;
      void api
        .heartbeatScoresheetLease(
          scoresheetId,
          leaseRef.current,
          clientIdRef.current,
          "MINIAPP",
          token,
        )
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
          setLeaseToken("");
          setReadOnly(true);
          setSaveState("编辑权已失效 · 本地输入已保留");
        });
    }, 15000);
    const network = (result: Taro.onNetworkStatusChange.CallbackResult) => {
      setOnline(result.isConnected);
      if (result.isConnected) {
        void sync().then(() => {
          if (pendingRef.current && leaseRef.current) void flush("NETWORK_RECOVERY");
        });
      } else {
        setSaveState("网络中断 · 未保存输入已保留");
      }
    };
    Taro.onNetworkStatusChange(network);
    return () => {
      clearInterval(poll);
      clearInterval(heartbeat);
      Taro.offNetworkStatusChange(network);
    };
  }, [flush, scoresheetId, sync, token]);

  useUnload(() => {
    if (leaseRef.current && token && !pendingRef.current && !savingRef.current) {
      void api.releaseScoresheetLease(
        scoresheetId,
        leaseRef.current,
        clientIdRef.current,
        "MINIAPP",
        token,
      );
    }
  });

  const takeOver = async (force = false) => {
    try {
      const result = force
        ? await api.forceScoresheetLease(scoresheetId, clientIdRef.current, "MINIAPP", token)
        : await api.acquireScoresheetLease(scoresheetId, clientIdRef.current, "MINIAPP", token);
      if (result.read_only || !result.lease_token) {
        setSaveState(`${result.holder.username} 仍在编辑`);
        return;
      }
      leaseRef.current = result.lease_token;
      setLeaseToken(result.lease_token);
      setReadOnly(false);
      setSaveState("已取得编辑权");
      const server = await api.getScoresheet(scoresheetId, token);
      const recovery = recoveryRef.current;
      recoveryRef.current = null;
      if (!recovery) {
        applyServer(server);
      } else if (server.draft_version === recovery.baseVersion) {
        serverRef.current = server;
        pendingRef.current = recovery.local;
        pendingBaseVersionRef.current = server.draft_version;
        setSheet({ ...server, draft: recovery.local });
        await drainPending("LEASE_RECOVERY");
      } else {
        const choice = await Taro.showModal({
          title: "接手前服务器再次变化",
          content: `差异字段：${documentDiffPaths(recovery.local, server.draft).join("、") || "多个字段"}。请选择本次提交使用的值。`,
          cancelText: "服务器值",
          confirmText: "本地值",
        });
        if (choice.confirm) {
          serverRef.current = server;
          pendingRef.current = recovery.local;
          pendingBaseVersionRef.current = server.draft_version;
          setSheet({ ...server, draft: recovery.local });
          await drainPending("CONFLICT_RESOLVED_LOCAL");
        } else {
          applyServer(server);
          setSaveState("已采用服务器版本");
        }
      }
    } catch (reason) {
      setError(message(reason));
    }
  };

  const handoff = async () => {
    if (!(await drainPending())) {
      setError("仍有未保存输入，暂未释放编辑权。请恢复网络后重试。");
      return;
    }
    if (!leaseRef.current) return;
    await api.releaseScoresheetLease(
      scoresheetId,
      leaseRef.current,
      clientIdRef.current,
      "MINIAPP",
      token,
    );
    leaseRef.current = "";
    setLeaseToken("");
    setReadOnly(true);
    setSaveState("已保存并释放 · 可在网页接手");
  };

  const exitEditor = async () => {
    if (!(await drainPending())) {
      setError("仍有未保存输入，暂未退出。请恢复网络后重试。");
      return;
    }
    if (leaseRef.current) {
      await api.releaseScoresheetLease(
        scoresheetId,
        leaseRef.current,
        clientIdRef.current,
        "MINIAPP",
        token,
      ).catch(() => undefined);
      leaseRef.current = "";
      setLeaseToken("");
    }
    await Taro.navigateBack();
  };

  const reviewCurrent = async () => {
    if (!sheet || !context()) return;
    if (!(await drainPending())) return;
    try {
      let next = serverRef.current!;
      const keys = stepKey(stepIndex) === "CLOSING"
        ? (["SUMMARY", "OFFICIALS"] as ScoresheetRegion[])
        : ([stepKey(stepIndex)] as ScoresheetRegion[]);
      for (const region of keys) {
        if (!SCORESHEET_REGIONS.includes(region)) continue;
        next = await api.reviewScoresheetRegion(scoresheetId, region, context()!, true, token);
        applyServer(next);
      }
      Taro.showToast({ title: "区域已核对", icon: "success" });
    } catch (reason) {
      setError(message(reason));
    }
  };

  const validate = async () => {
    if (!context()) return;
    if (!(await drainPending())) return;
    try {
      applyServer(await api.validateScoresheet(scoresheetId, context()!, token));
    } catch (reason) {
      setError(message(reason));
    }
  };

  const acknowledge = async (warningId: string) => {
    if (!sheet || !context()) return;
    if (!(await drainPending())) return;
    try {
      applyServer(
        await api.acknowledgeScoresheetWarnings(
          scoresheetId,
          context()!,
          [warningId],
          token,
        ),
      );
    } catch (reason) {
      setError(message(reason));
    }
  };

  const publish = async () => {
    if (!context()) return;
    if (!(await drainPending())) return;
    const confirmed = await Taro.showModal({
      title: "发布正式数据",
      content: "发布将同时更新正式比分、排名、对阵和球员统计。确认六个区域均已对照原图核对？",
      confirmText: "确认发布",
    });
    if (!confirmed.confirm) return;
    try {
      applyServer(await api.publishScoresheet(scoresheetId, context()!, token));
      leaseRef.current = "";
      setLeaseToken("");
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
  const reviewed = currentRegions.length > 0 && currentRegions.every(
    (region) => sheet.reviewed_regions[region]?.draft_version === sheet.draft_version,
  );

  return (
    <View className="mini-sheet-page">
      <View className="mini-sheet-safe-top" />
      <View className="mini-sheet-topbar">
        <Button className="mini-sheet-back" onClick={() => void exitEditor()}>‹</Button>
        <View className="mini-sheet-title-block">
          <Text className="mini-sheet-title">{String(sheet.game.label)}</Text>
          <Text className={`mini-sheet-save ${readOnly ? "readonly" : ""}`}>{saveState}</Text>
        </View>
        {readOnly ? <View className="mini-sheet-lease-actions">
          <Button className="mini-sheet-handoff" onClick={() => void takeOver(false)}>接手</Button>
          {accountRole === "SUPERADMIN" && <Button className="mini-sheet-force" onClick={async () => {
            const confirmed = await Taro.showModal({ title: "强制接管", content: "旧客户端会立即转为只读，确认继续？", confirmText: "确认接管" });
            if (confirmed.confirm) await takeOver(true);
          }}>强制</Button>}
        </View> : (
          <Button className="mini-sheet-handoff" onClick={() => void handoff()}>交接</Button>
        )}
      </View>

      <ScrollView className="mini-sheet-steps" scrollX enhanced showScrollbar={false}>
        <View className="mini-sheet-step-row">
          {STEPS.map((step, index) => {
            const regions = step.key === "CLOSING"
              ? (["SUMMARY", "OFFICIALS"] as ScoresheetRegion[])
              : SCORESHEET_REGIONS.includes(step.key as ScoresheetRegion)
                ? ([step.key] as ScoresheetRegion[])
                : [];
            const done = regions.length > 0 && regions.every(
              (region) => sheet.reviewed_regions[region]?.draft_version === sheet.draft_version,
            );
            const issueCount = [...errors, ...warnings].filter((issue) => regions.includes(issue.region)).length;
            return (
              <View className={`mini-sheet-step ${stepIndex === index ? "active" : ""}`} key={step.key} onClick={() => setStepIndex(index)}>
                <Text className={done ? "step-number done" : "step-number"}>{done ? "✓" : index + 1}</Text>
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
        {sheet.recognition && (
          <RecognitionBanner
            onStop={async () => {
              try {
                applyServer(await api.stopScoresheetRecognition(scoresheetId, token));
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

      {currentKey === "RUNNING_SCORE" && view === "STANDARD" && !readOnly && online && (
        <MiniScoreQuickBar
          document={sheet.draft}
          onChange={(events) => changePath("/running_score", events, true)}
        />
      )}

      <View className="mini-sheet-footer">
        <Button disabled={stepIndex === 0} onClick={() => setStepIndex((value) => Math.max(0, value - 1))}>上一步</Button>
        {currentRegions.length > 0 ? (
          <Button className={reviewed ? "reviewed" : "review"} disabled={readOnly || !online} onClick={() => void reviewCurrent()}>{reviewed ? "已核对" : "区域已核对"}</Button>
        ) : currentKey === "PUBLISH" ? (
          <Button className="review" disabled={readOnly || !online} onClick={() => void validate()}>重新校验</Button>
        ) : <View />}
        <Button disabled={stepIndex === STEPS.length - 1} onClick={() => setStepIndex((value) => Math.min(STEPS.length - 1, value + 1))}>下一步</Button>
      </View>

      {currentKey === "PUBLISH" && view === "STANDARD" && (
        <PublishPanel
          acknowledge={acknowledge}
          acknowledgedWarnings={sheet.acknowledged_warnings}
          errors={errors}
          publish={publish}
          readOnly={readOnly || !online}
          reviewedRegions={sheet.reviewed_regions}
          validationReady={sheet.status === "READY" && sheet.validation_draft_version === sheet.draft_version}
          version={sheet.draft_version}
          warnings={warnings}
        />
      )}
    </View>
  );
}

function stepKey(index: number): StepKey {
  return STEPS[index]?.key ?? "SOURCE";
}

function RecognitionBanner({ recognition, readOnly, onStop }: {
  recognition: NonNullable<ScoresheetDetail["recognition"]>;
  readOnly: boolean;
  onStop: () => Promise<void>;
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
  const active = ["QUEUED", "RUNNING", "RETRY_WAIT"].includes(recognition.status);
  return (
    <View className={`mini-recognition-banner ${recognition.status.toLowerCase()}`}>
      <View>
        <Text>自动识别 · 第 {Math.max(1, recognition.attempt_count || 1)}/{recognition.max_attempts} 次</Text>
        <Text>{recognition.status === "RETRY_WAIT" && retrySeconds !== null ? `${retrySeconds} 秒后自动重试` : recognition.status === "RUNNING" ? "正在读取整表" : recognition.status === "FAILED" ? "识别已耗尽，可完整手工录入或重传" : recognition.status}</Text>
      </View>
      {active && !readOnly && <Button onClick={() => void onStop()}>停止重试</Button>}
    </View>
  );
}

function SourceView({ source, scale, rotation, setScale, setRotation, readOnly, onReplace, corners, currentRegion, onCornersChange }: {
  source: ScoresheetDetail["source"];
  scale: number;
  rotation: number;
  setScale: (value: number) => void;
  setRotation: (value: number) => void;
  readOnly: boolean;
  onReplace: () => void;
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
  if (step === "SOURCE") {
    return <View className="mini-standard-state"><Text>切换到“原图”查看或对齐当前记录表照片。</Text></View>;
  }
  if (step === "SOURCE_GAME") {
    return <MiniForm>{["competition", "date", "scheduled_time", "game_number", "venue", "crew_chief", "umpire_1", "umpire_2"].map((key) => <MiniInput disabled={readOnly} key={key} label={gameLabel(key)} onChange={(value) => changePath(`/game/${key}`, value)} value={document.game[key] ?? ""} />)}</MiniForm>;
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
  const computed = computedScoreSummary(document.running_score);
  const winnerOptions = ["未填写", "A 队", "B 队"];
  const winnerIndex = document.summary.winner_side === "A" ? 1 : document.summary.winner_side === "B" ? 2 : 0;
  return (
    <View className="mini-closing-editor">
      <View className="mini-section-heading"><Text>节比分与最终结果</Text></View>
      <View className="mini-computed-summary"><Text>系统按逐次得分计算</Text><Text>A {computed.final.A} : {computed.final.B} B</Text></View>
      {(["1", "2", "3", "4", "OT"] as ScorePeriod[]).map((period) => (
        <View className="mini-period-row" key={period}>
          <Text>{period === "OT" ? "加时" : `第 ${period} 节`} · 计算 {computed.periods[period].A}:{computed.periods[period].B}</Text>
          {(["A", "B"] as TeamSide[]).map((side) => <View className="mini-number-field" key={side}><Text>{side}</Text><Input disabled={readOnly} onInput={(event) => changePath(`/summary/period_scores/${period}/${side}`, event.detail.value === "" ? null : Number(event.detail.value))} type="number" value={String(document.summary.period_scores[period][side] ?? "")} /></View>)}
        </View>
      ))}
      <View className="mini-period-row final"><Text>最终 · 计算 {computed.final.A}:{computed.final.B}</Text>{(["A", "B"] as TeamSide[]).map((side) => <View className="mini-number-field" key={side}><Text>{side}</Text><Input disabled={readOnly} onInput={(event) => changePath(`/summary/final_score/${side}`, event.detail.value === "" ? null : Number(event.detail.value))} type="number" value={String(document.summary.final_score[side] ?? "")} /></View>)}</View>
      <Picker disabled={readOnly} mode="selector" onChange={(event) => changePath("/summary/winner_side", (["", "A", "B"] as const)[Number(event.detail.value)], true)} range={winnerOptions} value={winnerIndex}><View className="mini-input-row"><Text>纸面胜队</Text><Text>{winnerOptions[winnerIndex]}</Text></View></Picker>
      <MiniInput disabled={readOnly} label="比赛结束时间" onChange={(value) => changePath("/summary/ended_at", value)} value={document.summary.ended_at} />
      <View className="mini-section-heading"><Text>工作人员与签名</Text></View>
      <MiniForm>
        {["scorer", "assistant_scorer", "timer", "shot_clock_operator"].map((key) => <MiniInput disabled={readOnly} key={key} label={officialLabel(key)} onChange={(value) => changePath(`/officials/${key}`, value)} value={String(document.officials[key] ?? "")} />)}
        {["crew_chief_signature", "umpire_1_signature", "umpire_2_signature", "captain_protest_signature"].map((key) => <View className="mini-signature-row" key={key}><Text>{officialLabel(key)}</Text><Switch checked={Boolean(document.officials[key])} color="#b52d28" disabled={readOnly} onChange={(event) => changePath(`/officials/${key}`, event.detail.value, true)} /></View>)}
      </MiniForm>
    </View>
  );
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

function MiniForm({ children }: { children: React.ReactNode }) {
  return <View className="mini-sheet-form">{children}</View>;
}

function MiniInput({ label, value, disabled, onChange }: { label: string; value: string; disabled: boolean; onChange: (value: string) => void }) {
  return <View className="mini-input-row"><Text>{label}</Text><Input disabled={disabled} onInput={(event) => onChange(event.detail.value)} value={value} /></View>;
}

function MiniTeamEditor({ document, side, readOnly, changePath }: {
  document: ScoresheetDocument;
  side: TeamSide;
  readOnly: boolean;
  changePath: (path: string, value: unknown, immediate?: boolean) => void;
}) {
  const team = document.teams[side];
  return (
    <View className="mini-team-editor">
      <View className="mini-section-heading"><Text>球队 {side}</Text><Text>{team.name}</Text></View>
      <View className="mini-team-paper-fields">
        {(["H1", "H2", "OT"] as const).map((scope) => (
          <View className="mini-team-field" key={scope}>
            <Text>暂停 {scope}</Text>
            <Input disabled={readOnly} onInput={(event) => changePath(`/teams/${side}/timeouts/${scope}`, miniSplitMarks(event.detail.value))} placeholder="分钟，以空格分隔" value={miniMarkText(team.timeouts[scope] ?? [])} />
          </View>
        ))}
        {(["1", "2", "3", "4"] as const).map((period) => (
          <View className="mini-team-field compact" key={period}>
            <Text>第 {period} 节全队犯规</Text>
            <Input disabled={readOnly} maxlength={1} onInput={(event) => { const count = Math.max(0, Math.min(4, Number(event.detail.value) || 0)); changePath(`/teams/${side}/team_fouls/${period}`, Array(count).fill("X")); }} type="number" value={String((team.team_fouls[period] ?? []).length)} />
          </View>
        ))}
        {(["head_coach", "assistant_coach"] as const).map((role) => (
          <View className="mini-coach-row" key={role}>
            <View className="mini-team-field"><Text>{role === "head_coach" ? "教练员" : "助理教练员"}</Text><Input disabled={readOnly} onInput={(event) => changePath(`/teams/${side}/${role}/name`, event.detail.value)} value={team[role].name} /></View>
            <View className="mini-team-field compact"><Text>犯规</Text><Input disabled={readOnly} onInput={(event) => changePath(`/teams/${side}/${role}/fouls`, miniSplitMarks(event.detail.value.toUpperCase()))} placeholder="C B D" value={miniMarkText(team[role].fouls)} /></View>
          </View>
        ))}
      </View>
      {team.players.map((player, index) => (
        <View className="mini-player-row" key={player.player_id}>
          <View className="mini-player-name"><Text className="mini-player-number">{player.jersey_number || "–"}</Text><Text>{player.name}</Text></View>
          <View className="mini-player-switches">
            <View><Text>出场</Text><Switch checked={player.appeared} color="#b52d28" disabled={readOnly} onChange={(event) => changePath(`/teams/${side}/players/${index}/appeared`, event.detail.value, true)} /></View>
            <View><Text>首发</Text><Switch checked={player.starter} color="#b52d28" disabled={readOnly} onChange={(event) => changePath(`/teams/${side}/players/${index}/starter`, event.detail.value, true)} /></View>
            <View><Text>队长</Text><Switch checked={player.captain} color="#b52d28" disabled={readOnly} onChange={(event) => changePath(`/teams/${side}/players/${index}/captain`, event.detail.value, true)} /></View>
          </View>
          <Input className="mini-foul-input" disabled={readOnly} onInput={(event) => changePath(`/teams/${side}/players/${index}/fouls`, miniSplitMarks(event.detail.value.toUpperCase()))} placeholder="犯规：P T U D F" value={miniMarkText(player.fouls)} />
        </View>
      ))}
    </View>
  );
}

function miniSplitMarks(value: string): string[] {
  return value.trim() ? value.trim().split(/\s+/).filter(Boolean) : [];
}

function miniMarkText(values: unknown[]): string {
  return values.map((value) => typeof value === "object" && value ? String((value as { code?: unknown; minute?: unknown }).code ?? (value as { minute?: unknown }).minute ?? "") : String(value)).filter(Boolean).join(" ");
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

  const tapEmpty = (side: TeamSide, cumulative: number) => {
    if (readOnly) return;
    const delta = cumulative - teamTotal(document.running_score, side);
    if (![1, 2, 3].includes(delta)) return;
    const value = delta as ScoreValue;
    if (!canPlaceScore(document.running_score, side, value, cumulative)) return;
    const player = document.teams[side].players.find((row) => row.appeared) ?? document.teams[side].players[0];
    onChange(addScoreEvent(document.running_score, {
      id: `mini-${Date.now()}-${Math.random().toString(36).slice(2)}`,
      team: side,
      value,
      period: document.running_score[document.running_score.length - 1]?.period ?? "1",
      player_id: player?.player_id,
      player_name: player?.name,
      player_number: player?.jersey_number,
    }));
  };

  return (
    <View className="mini-paper-score">
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
              return <View className={`mini-score-cell ${invalid ? "invalid" : ""} ${event?.id === selectedId ? "selected" : ""}`} key={side} onClick={() => event ? onSelect(event.id) : tapEmpty(side, score)}>{event ? <><Text className="mini-score-player">#{event.player_number || "?"}</Text><Text className="mini-score-mark">{event.value === 1 ? "●" : event.value === 3 ? "◯" : "╱"}</Text><Text className="mini-score-period">{event.period}</Text></> : <Text className="mini-score-empty">{canPlaceScore(document.running_score, side, 1, score) || canPlaceScore(document.running_score, side, 2, score) || canPlaceScore(document.running_score, side, 3, score) ? "＋" : ""}</Text>}</View>;
            })}
          </View>
        ))}
      </ScrollView>
      {selected && <ScoreEventDrawer document={document} event={selected} onChange={onChange} onClose={() => onSelect("")} />}
    </View>
  );
}

function scoreIssueMatches(path: string, event: ScoreEvent) {
  if (path.includes(event.id)) return true;
  const match = path.match(/\/running_score\/(\d+)/);
  return Boolean(match && Number(match[1]) === event.sequence - 1);
}

function ScoreEventDrawer({ document, event, onChange, onClose }: { document: ScoresheetDocument; event: ScoreEvent; onChange: (events: ScoreEvent[]) => void; onClose: () => void }) {
  const players = document.teams[event.team].players;
  const update = (patch: Partial<ScoreEvent>) => {
    onChange(normalizeScoreEvents(document.running_score.map((row) => row.id === event.id ? { ...row, ...patch } : row)));
  };
  const playerIndex = Math.max(0, players.findIndex((player) => player.player_id === event.player_id));
  return <View className="mini-score-drawer-mask" onClick={onClose}><View className="mini-score-drawer" onClick={(click) => click.stopPropagation()}><View className="mini-drawer-heading"><Text>{event.team} 队累计 {event.cumulative} 分</Text><Button onClick={onClose}>完成</Button></View><Picker mode="selector" range={players.map((player) => `${player.jersey_number || "–"} ${player.name}`)} value={playerIndex} onChange={(change) => { const player = players[Number(change.detail.value)]; if (player) update({ player_id: player.player_id, player_name: player.name, player_number: player.jersey_number }); }}><View className="mini-drawer-field"><Text>球员</Text><Text>{players[playerIndex]?.name ?? "未关联"}</Text></View></Picker><Picker mode="selector" range={["1 分", "2 分", "3 分"]} value={event.value - 1} onChange={(change) => update({ value: (Number(change.detail.value) + 1) as ScoreValue })}><View className="mini-drawer-field"><Text>分值</Text><Text>{event.value} 分</Text></View></Picker><Picker mode="selector" range={["1", "2", "3", "4", "OT"]} value={["1", "2", "3", "4", "OT"].indexOf(event.period)} onChange={(change) => update({ period: (["1", "2", "3", "4", "OT"] as ScorePeriod[])[Number(change.detail.value)] })}><View className="mini-drawer-field"><Text>节次</Text><Text>{event.period}</Text></View></Picker><Picker mode="selector" range={["普通", "节末", "终场"]} value={["none", "period", "game"].indexOf(event.boundary ?? "none")} onChange={(change) => update({ boundary: (["none", "period", "game"] as const)[Number(change.detail.value)] })}><View className="mini-drawer-field"><Text>标记</Text><Text>{event.boundary === "game" ? "终场" : event.boundary === "period" ? "节末" : "普通"}</Text></View></Picker><Button className="mini-drawer-delete" onClick={() => { onChange(deleteScoreEvent(document.running_score, event.id)); onClose(); }}>删除得分事件</Button></View></View>;
}

function MiniScoreQuickBar({ document, onChange }: { document: ScoresheetDocument; onChange: (events: ScoreEvent[]) => void }) {
  const [period, setPeriod] = useState<ScorePeriod>("1");
  const add = (side: TeamSide, value: ScoreValue) => {
    const player = document.teams[side].players.find((row) => row.appeared) ?? document.teams[side].players[0];
    onChange(addScoreEvent(document.running_score, { id: `quick-${Date.now()}-${Math.random().toString(36).slice(2)}`, team: side, value, period, player_id: player?.player_id, player_name: player?.name, player_number: player?.jersey_number }));
  };
  return <View className="mini-quick-bar"><Picker mode="selector" range={["1", "2", "3", "4", "OT"]} value={["1", "2", "3", "4", "OT"].indexOf(period)} onChange={(event) => setPeriod((["1", "2", "3", "4", "OT"] as ScorePeriod[])[Number(event.detail.value)])}><View className="mini-quick-period">{period} 节</View></Picker><ScrollView scrollX><View className="mini-quick-buttons">{(["A", "B"] as TeamSide[]).flatMap((side) => ([1, 2, 3] as ScoreValue[]).map((value) => <Button disabled={teamTotal(document.running_score, side) + value > 160} key={`${side}-${value}`} onClick={() => add(side, value)}>{side} +{value}</Button>))}</View></ScrollView></View>;
}

function PublishPanel({ errors, warnings, reviewedRegions, version, validationReady, readOnly, acknowledgedWarnings, acknowledge, publish }: { errors: Array<{ id: string; message: string; region: ScoresheetRegion }>; warnings: Array<{ id: string; message: string; region: ScoresheetRegion }>; reviewedRegions: ScoresheetDetail["reviewed_regions"]; version: number; validationReady: boolean; readOnly: boolean; acknowledgedWarnings: string[]; acknowledge: (warningId: string) => void; publish: () => void }) {
  const allReviewed = SCORESHEET_REGIONS.every((region) => reviewedRegions[region]?.draft_version === version);
  const warningsReady = warnings.every((warning) => acknowledgedWarnings.includes(warning.id));
  return <View className="mini-publish-panel"><View className="mini-publish-summary"><Text>{allReviewed ? "六个区域均已核对" : "仍有区域未核对"}</Text><Text>{validationReady ? `${errors.length} 个错误 · ${warnings.length} 个提醒` : "当前草稿尚未完成服务端校验"}</Text></View>{errors.map((issue) => <View className="mini-publish-issue error" key={issue.id}><Text>{REGION_LABELS[issue.region]}</Text><Text>{issue.message}</Text></View>)}{warnings.map((issue) => <View className="mini-publish-issue" key={issue.id}><Text>{REGION_LABELS[issue.region]}</Text><View><Text>{issue.message}</Text>{acknowledgedWarnings.includes(issue.id) ? <Text className="mini-warning-acknowledged">已确认</Text> : <Button disabled={readOnly} onClick={() => acknowledge(issue.id)}>确认此项</Button>}</View></View>)}<Button className="mini-publish-button" disabled={readOnly || !validationReady || !allReviewed || errors.length > 0 || !warningsReady} onClick={publish}>一次确认发布</Button></View>;
}

function gameLabel(key: string) {
  return ({ competition: "赛事", date: "日期", scheduled_time: "开赛时间", game_number: "比赛编号", venue: "场地", crew_chief: "主裁判", umpire_1: "副裁判 1", umpire_2: "副裁判 2" } as Record<string, string>)[key] ?? key;
}

function officialLabel(key: string) {
  return ({ scorer: "记录员", assistant_scorer: "助理记录员", timer: "计时员", shot_clock_operator: "24 秒计时员", crew_chief_signature: "主裁判签名", umpire_1_signature: "副裁判 1 签名", umpire_2_signature: "副裁判 2 签名", captain_protest_signature: "队长抗议签名" } as Record<string, string>)[key] ?? key;
}
