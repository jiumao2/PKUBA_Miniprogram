import { Button, Picker, Text, View } from "@tarojs/components";
import Taro, { useDidShow } from "@tarojs/taro";
import { useMemo, useState } from "react";
import type { RescheduleGame, RescheduleTarget } from "@pkuba/api-client";

import { api } from "../../api";
import { getMiniAppSession } from "../../auth";
import { formatDate } from "../../format";
import "../../role-workspace.css";

export default function RescheduleCreatePage() {
  const [games, setGames] = useState<RescheduleGame[]>([]);
  const [targets, setTargets] = useState<RescheduleTarget[]>([]);
  const [gameIndex, setGameIndex] = useState(0);
  const [dateIndex, setDateIndex] = useState(0);
  const [periodIndex, setPeriodIndex] = useState(0);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const loadTargets = async (game: RescheduleGame, token: string) => {
    setTargets([]);
    setDateIndex(0);
    setPeriodIndex(0);
    setTargets(await api.getRescheduleTargets(game.id, token));
  };

  useDidShow(() => {
    const token = getMiniAppSession();
    if (!token) {
      setLoading(false);
      return;
    }
    setLoading(true);
    setError("");
    api.getEligibleRescheduleGames(token)
      .then(async (items) => {
        setGames(items);
        if (items[0]) await loadTargets(items[0], token);
      })
      .catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "读取失败"))
      .finally(() => setLoading(false));
  });

  const selectedGame = games[gameIndex] ?? games[0];
  const dates = useMemo(() => Array.from(new Set(targets.map((item) => item.date))), [targets]);
  const selectedDate = dates[dateIndex] ?? dates[0] ?? "";
  const dayTargets = targets.filter((item) => item.date === selectedDate);
  const selectedTarget = dayTargets[periodIndex] ?? dayTargets[0];

  const changeGame = async (index: number) => {
    const token = getMiniAppSession();
    const game = games[index];
    setGameIndex(index);
    setError("");
    if (!token || !game) return;
    setLoading(true);
    try {
      await loadTargets(game, token);
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "可用时段读取失败");
    } finally {
      setLoading(false);
    }
  };

  const submit = async () => {
    const token = getMiniAppSession();
    if (!token || !selectedGame || !selectedTarget) return;
    const confirmation = await Taro.showModal({
      title: "提交调赛申请",
      content: `${formatDate(selectedTarget.date)} ${selectedTarget.start_time}。提交后原比赛会被锁定，具体场地将在调赛生效并更新正式赛程后公布。`,
      confirmText: "提交申请",
      confirmColor: "#c91f26",
    });
    if (!confirmation.confirm) return;
    setBusy(true);
    setError("");
    try {
      await api.createRescheduleRequest({
        game_id: selectedGame.id,
        expected_game_version: selectedGame.version,
        target_date: selectedTarget.date,
        target_period_id: selectedTarget.period_id,
      }, token);
      Taro.showToast({ title: "申请已提交", icon: "success" });
      await Taro.redirectTo({ url: "/pages/reschedule-requests/index" });
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "提交失败");
    } finally {
      setBusy(false);
    }
  };

  return (
    <View className="page reschedule-create-page">
      <Text className="page-title">发起调赛</Text>
      {loading && <View className="state"><Text className="state-detail">正在核对可申请比赛和容量…</Text></View>}
      {!loading && !selectedGame && (
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
                  <Text className="flow-picker-meta">{selectedTarget?.request_type === "SAME_WEEK" ? "同周调赛" : "跨周调赛"}</Text>
                </View>
              </Picker>
              <Text className="flow-helper">系统会在提交时内部预留可用场地，调赛生效并更新正式赛程后公布。</Text>
              <Button className="flow-primary" disabled={busy} onClick={() => void submit()}>
                {busy ? "正在提交…" : "提交申请"}
              </Button>
            </>
          )}
          {!loading && !targets.length && (
            <View className="flow-feedback">这场比赛当前没有同时满足容量、场地和球队冲突检查的目标时段。</View>
          )}
        </View>
      )}
      {error && <View className="flow-feedback">{error}</View>}
    </View>
  );
}

function gameLabel(game: RescheduleGame) {
  return `${formatDate(game.date)} ${game.start_time} · ${game.home_name} — ${game.away_name}`;
}

function targetLabel(target: RescheduleTarget) {
  return `${target.start_time} · ${target.request_type === "SAME_WEEK" ? "同周" : "跨周"}`;
}
