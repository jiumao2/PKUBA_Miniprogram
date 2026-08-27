import { Button, Picker, Text, View } from "@tarojs/components";
import Taro, { useDidShow, useRouter } from "@tarojs/taro";
import { useEffect, useMemo, useRef, useState } from "react";
import type { RescheduleGame, RescheduleTarget } from "@pkuba/api-client";

import { api } from "../../api";
import { getMiniAppSession } from "../../auth";
import { formatDate } from "../../format";
import "../../role-workspace.css";
import {
  RESCHEDULE_LOGIN_REQUIRED,
  RescheduleAccessNotice,
  rescheduleAccessProblem,
  type RescheduleAccessProblem,
} from "../rescheduleAccess";
import {
  parseRescheduleEntryMode,
  RESCHEDULE_ENTRY_COPY,
  targetsForEntryMode,
} from "./mode";

export default function RescheduleCreatePage() {
  const router = useRouter();
  const entryMode = parseRescheduleEntryMode(router.params.mode);
  const entryCopy = RESCHEDULE_ENTRY_COPY[entryMode];
  const [games, setGames] = useState<RescheduleGame[]>([]);
  const [targets, setTargets] = useState<RescheduleTarget[]>([]);
  const [gameIndex, setGameIndex] = useState(0);
  const [dateIndex, setDateIndex] = useState(0);
  const [periodIndex, setPeriodIndex] = useState(0);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [accessProblem, setAccessProblem] = useState<RescheduleAccessProblem | null>(null);
  const loadSequence = useRef(0);
  const loadedIdentity = useRef<string | null>(null);

  const identityGuard = (token: string) => {
    const sequence = loadSequence.current;
    return () => sequence === loadSequence.current && token === getMiniAppSession();
  };

  const clearPrivateContent = () => {
    setGames([]);
    setTargets([]);
    setGameIndex(0);
    setDateIndex(0);
    setPeriodIndex(0);
    setBusy(false);
  };
  const requireLogin = () => {
    loadSequence.current += 1;
    loadedIdentity.current = null;
    clearPrivateContent();
    setAccessProblem(RESCHEDULE_LOGIN_REQUIRED);
    setLoading(false);
  };
  const showFailure = (reason: unknown, fallback: string) => {
    const problem = rescheduleAccessProblem(reason, fallback);
    setAccessProblem(problem);
    if (problem.kind !== "error") {
      loadSequence.current += 1;
      clearPrivateContent();
      setLoading(false);
    }
  };

  const loadTargets = async (game: RescheduleGame, token: string, current: () => boolean) => {
    setTargets([]);
    setDateIndex(0);
    setPeriodIndex(0);
    const availableTargets = await api.getRescheduleTargets(
      game.id,
      entryCopy.processRoute,
      token,
    );
    if (current()) setTargets(targetsForEntryMode(availableTargets, entryMode));
  };

  const load = async () => {
    const token = getMiniAppSession();
    if (!token) {
      requireLogin();
      return;
    }
    const sequence = ++loadSequence.current;
    const current = () => sequence === loadSequence.current && token === getMiniAppSession();
    if (loadedIdentity.current !== token) {
      clearPrivateContent();
      loadedIdentity.current = token;
    }
    setLoading(true);
    setAccessProblem(null);
    try {
      const items = await api.getEligibleRescheduleGames(token);
      if (!current()) return;
      setGames(items);
      setGameIndex(0);
      setTargets([]);
      setDateIndex(0);
      setPeriodIndex(0);
      if (items[0]) {
        const available = await api.getRescheduleTargets(items[0].id, entryCopy.processRoute, token);
        if (!current()) return;
        setTargets(targetsForEntryMode(available, entryMode));
      }
    } catch (reason: unknown) {
      if (current()) showFailure(reason, "读取失败，请重试。");
    } finally {
      if (current()) setLoading(false);
    }
  };

  useDidShow(() => {
    void Taro.setNavigationBarTitle({ title: entryCopy.title });
    void load();
  });
  useEffect(() => () => { loadSequence.current += 1; }, []);

  const selectedGame = games[gameIndex] ?? games[0];
  const dates = useMemo(() => Array.from(new Set(targets.map((item) => item.date))), [targets]);
  const selectedDate = dates[dateIndex] ?? dates[0] ?? "";
  const dayTargets = targets.filter((item) => item.date === selectedDate);
  const selectedTarget = dayTargets[periodIndex] ?? dayTargets[0];

  const changeGame = async (index: number) => {
    const token = getMiniAppSession();
    const game = games[index];
    setGameIndex(index);
    if (!token) { requireLogin(); return; }
    if (!game) return;
    const current = identityGuard(token);
    setLoading(true);
    try {
      await loadTargets(game, token, current);
    } catch (reason: unknown) {
      if (current()) showFailure(reason, "可用时段读取失败");
    } finally {
      if (current()) setLoading(false);
    }
  };

  const submit = async () => {
    const token = getMiniAppSession();
    if (!token) { requireLogin(); return; }
    if (!selectedGame || !selectedTarget) return;
    const current = identityGuard(token);
    const confirmation = await Taro.showModal({
      title: `提交${entryCopy.title}申请`,
      content: `${formatDate(selectedTarget.date)} ${selectedTarget.start_time}。提交后原比赛会被锁定，具体场地将在调赛生效并更新正式赛程后公布。`,
      confirmText: "提交申请",
      confirmColor: "#c91f26",
    });
    if (!confirmation.confirm) return;
    if (!current()) return;
    setBusy(true);
    try {
      await api.createRescheduleRequest({
        game_id: selectedGame.id,
        expected_game_version: selectedGame.version,
        target_date: selectedTarget.date,
        target_period_id: selectedTarget.period_id,
        process_route: entryCopy.processRoute,
      }, token);
      if (!current()) return;
      Taro.showToast({ title: "申请已提交", icon: "success" });
      await Taro.redirectTo({ url: "/pages/reschedule-requests/index" });
    } catch (reason: unknown) {
      if (current()) showFailure(reason, "提交失败");
    } finally {
      if (current()) setBusy(false);
    }
  };

  return (
    <View className="page reschedule-create-page">
      <Text className="page-title">{entryCopy.title}</Text>
      {entryMode === "cross_week" && (
        <Text className="flow-guidance">{RESCHEDULE_ENTRY_COPY.cross_week.guidance}</Text>
      )}
      {loading && <View className="state"><Text className="state-detail">正在核对可申请比赛和容量…</Text></View>}
      {accessProblem && <RescheduleAccessNotice
        problem={accessProblem}
        onRetry={() => void load()}
        returnEntry={entryMode === "cross_week" ? "reschedule_handbook" : "reschedule_ordinary"}
      />}
      {!loading && !accessProblem && !selectedGame && (
        <View className="state"><Text className="state-detail">当前没有满足政策和截止时间的可调比赛。</Text></View>
      )}
      {selectedGame && (
        <View className="flow-panel">
          <Text className="flow-panel-title">选择原比赛</Text>
          <Picker
            mode="selector"
            range={games.map(gameLabel)}
            value={gameIndex}
            onChange={(event) => void changeGame(Number(event.detail.value))}
          >
            <View className={`flow-picker ${selectedGame.division_gender === "WOMEN" ? "is-women" : ""}`}>
              <Text className="flow-picker-title">{selectedGame.home_name}　—　{selectedGame.away_name}</Text>
              <Text className="flow-picker-meta">{formatDate(selectedGame.date)} · {selectedGame.start_time} · {selectedGame.venue_name}</Text>
            </View>
          </Picker>

          {!!targets.length && (
            <>
              <Text className="flow-label">目标日期</Text>
              <Picker
                mode="selector"
                range={dates.map(formatDate)}
                value={dateIndex}
                onChange={(event) => {
                  setDateIndex(Number(event.detail.value));
                  setPeriodIndex(0);
                }}
              >
                <View className="flow-picker">
                  <Text className="flow-picker-title">{formatDate(selectedDate)}</Text>
                  <Text className="flow-picker-meta">点击更换日期</Text>
                </View>
              </Picker>

              <Text className="flow-label">目标时段</Text>
              <Picker
                mode="selector"
                range={dayTargets.map(targetLabel)}
                value={periodIndex}
                onChange={(event) => setPeriodIndex(Number(event.detail.value))}
              >
                <View className="flow-picker">
                  <Text className="flow-picker-title">{selectedTarget?.start_time}</Text>
                  <Text className="flow-picker-meta">
                    {selectedTarget?.request_type_label} · {selectedTarget?.process_route_label}
                  </Text>
                </View>
              </Picker>
              <Text className="flow-helper">系统会在提交时内部预留可用场地，调赛生效并更新正式赛程后公布。</Text>
              <Button className="flow-primary" disabled={busy} onClick={() => void submit()}>
                {busy ? "正在提交…" : "提交申请"}
              </Button>
            </>
          )}
          {!loading && !targets.length && (
            <View className="flow-feedback">{entryCopy.emptyMessage}</View>
          )}
        </View>
      )}
    </View>
  );
}

function gameLabel(game: RescheduleGame) {
  return `${formatDate(game.date)} ${game.start_time} · ${game.home_name} — ${game.away_name}`;
}

function targetLabel(target: RescheduleTarget) {
  return target.start_time;
}
