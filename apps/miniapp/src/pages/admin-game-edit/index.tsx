import { Button, Input, Picker, Switch, Text, View } from "@tarojs/components";
import Taro, { useDidShow, useRouter } from "@tarojs/taro";
import { useState } from "react";
import type { MobileAdminGame, MobileScheduleOptions } from "@pkuba/api-client";

import { api } from "../../api";
import { getMiniAppSession } from "../../auth";
import "../../role-workspace.css";
import "./index.css";

const STATUS_OPTIONS = [
  { value: "SCHEDULED", label: "未赛" },
  { value: "COMPLETED", label: "已完成" },
  { value: "FORFEIT", label: "弃权" },
  { value: "VOID", label: "已作废" },
];

export default function AdminGameEditPage() {
  const router = useRouter();
  const gameId = router.params.id ?? "";
  const [game, setGame] = useState<MobileAdminGame | null>(null);
  const [options, setOptions] = useState<MobileScheduleOptions | null>(null);
  const [homeScore, setHomeScore] = useState("");
  const [awayScore, setAwayScore] = useState("");
  const [cancelRequest, setCancelRequest] = useState(false);
  const [overrideRules, setOverrideRules] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useDidShow(() => {
    const token = getMiniAppSession();
    if (!token || !gameId) return;
    setError("");
    Promise.all([api.getMobileAdminGame(gameId, token), api.getMobileScheduleOptions(token)])
      .then(([currentGame, currentOptions]) => {
        setGame(currentGame);
        setOptions(currentOptions);
        setHomeScore(currentGame.home_score === null ? "" : String(currentGame.home_score));
        setAwayScore(currentGame.away_score === null ? "" : String(currentGame.away_score));
      })
      .catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "比赛读取失败"));
  });

  if (!game || !options) {
    return <View className="page"><Text className="page-title">比赛纠错</Text>{error ? <View className="flow-feedback">{error}</View> : <View className="state"><Text className="state-detail">正在读取比赛…</Text></View>}</View>;
  }

  const periods = options.periods;
  const venues = options.venues;
  const teams = options.teams.filter((team) => team.division_id === game.division_id);
  const periodIndex = Math.max(0, periods.findIndex((item) => item.id === game.period_id));
  const venueIndex = Math.max(0, venues.findIndex((item) => item.id === game.venue_id));
  const homeIndex = Math.max(0, teams.findIndex((item) => item.id === game.home_team_id));
  const awayIndex = Math.max(0, teams.findIndex((item) => item.id === game.away_team_id));
  const statusIndex = Math.max(0, STATUS_OPTIONS.findIndex((item) => item.value === game.status));

  const save = async () => {
    const token = getMiniAppSession();
    if (!token || !game.home_team_id || !game.away_team_id) {
      setError("请选择两支参赛球队。");
      return;
    }
    const modal = await Taro.showModal({
      title: "直接修改比赛",
      content: "该操作会立即影响公开赛程和比分，并写入审计日志。请再次核对全部字段。",
      confirmText: "确认修改",
      confirmColor: "#c91f26",
    });
    if (!modal.confirm) return;
    setBusy(true);
    setError("");
    try {
      const updated = await api.updateMobileAdminGame(game.id, {
        expected_version: game.version,
        date: game.date,
        period_id: game.period_id,
        venue_id: game.venue_id,
        home_team_id: game.home_team_id,
        away_team_id: game.away_team_id,
        home_score: parseScore(homeScore),
        away_score: parseScore(awayScore),
        status: game.status,
        leader_adjustable: game.leader_adjustable,
        cancel_active_request: cancelRequest,
        override_rules: overrideRules,
        confirmed: true,
      }, token);
      setGame(updated);
      Taro.showToast({ title: "比赛已更新", icon: "success" });
      setTimeout(() => Taro.navigateBack(), 500);
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "修改失败");
    } finally {
      setBusy(false);
    }
  };

  return (
    <View className="page admin-edit-page">
      <Text className="page-title">比赛纠错</Text>
      <View className={`edit-game-heading ${game.division_gender === "WOMEN" ? "is-women" : ""}`}>
        <Text className="edit-division">{game.division_name} · {stageLabel(game.stage)}</Text>
        <Text className="edit-code">{game.code}</Text>
      </View>

      <View className="edit-fields">
        <FieldLabel text="比赛日期" />
        <Picker mode="date" value={game.date} onChange={(event) => setGame({ ...game, date: event.detail.value })}>
          <View className="edit-picker">{game.date}</View>
        </Picker>

        <FieldLabel text="开赛时段" />
        <Picker mode="selector" range={periods.map((item) => `${item.start_time} · ${item.name}`)} value={periodIndex} onChange={(event) => setGame({ ...game, period_id: periods[Number(event.detail.value)].id })}>
          <View className="edit-picker">{periods[periodIndex]?.start_time} · {periods[periodIndex]?.name}</View>
        </Picker>

        <FieldLabel text="场地" />
        <Picker mode="selector" range={venues.map((item) => item.name)} value={venueIndex} onChange={(event) => setGame({ ...game, venue_id: venues[Number(event.detail.value)].id })}>
          <View className="edit-picker">{venues[venueIndex]?.name}</View>
        </Picker>

        <FieldLabel text="主队" />
        <Picker mode="selector" range={teams.map((item) => item.name)} value={homeIndex} onChange={(event) => setGame({ ...game, home_team_id: teams[Number(event.detail.value)].id })}>
          <View className="edit-picker">{teams.find((item) => item.id === game.home_team_id)?.name ?? "请选择主队"}</View>
        </Picker>

        <FieldLabel text="客队" />
        <Picker mode="selector" range={teams.map((item) => item.name)} value={awayIndex} onChange={(event) => setGame({ ...game, away_team_id: teams[Number(event.detail.value)].id })}>
          <View className="edit-picker">{teams.find((item) => item.id === game.away_team_id)?.name ?? "请选择客队"}</View>
        </Picker>

        <View className="score-fields">
          <View><FieldLabel text="主队比分" /><Input className="edit-input score-input" type="number" value={homeScore} onInput={(event) => setHomeScore(event.detail.value)} /></View>
          <View><FieldLabel text="客队比分" /><Input className="edit-input score-input" type="number" value={awayScore} onInput={(event) => setAwayScore(event.detail.value)} /></View>
        </View>

        <FieldLabel text="比赛状态" />
        <Picker mode="selector" range={STATUS_OPTIONS.map((item) => item.label)} value={statusIndex} onChange={(event) => setGame({ ...game, status: STATUS_OPTIONS[Number(event.detail.value)].value })}>
          <View className="edit-picker">{STATUS_OPTIONS[statusIndex]?.label}</View>
        </Picker>

        <SwitchLine label="允许领队申请调赛" checked={game.leader_adjustable} onChange={(checked) => setGame({ ...game, leader_adjustable: checked })} />
        {game.active_reschedule_request_id && <SwitchLine label="取消当前活动调赛申请" checked={cancelRequest} onChange={setCancelRequest} />}
        <SwitchLine label="使用超级管理员例外" checked={overrideRules} onChange={setOverrideRules} />
      </View>

      <Button className="flow-primary" disabled={busy} onClick={() => void save()}>{busy ? "正在保存…" : "保存修改"}</Button>
      {error && <View className="flow-feedback">{error}</View>}
    </View>
  );
}

function FieldLabel({ text }: { text: string }) {
  return <Text className="edit-label">{text}</Text>;
}

function SwitchLine({ label, checked, onChange }: { label: string; checked: boolean; onChange: (checked: boolean) => void }) {
  return <View className="switch-line"><Text>{label}</Text><Switch checked={checked} color="#c91f26" onChange={(event) => onChange(event.detail.value)} /></View>;
}

function parseScore(value: string) {
  const normalized = value.trim();
  return normalized === "" ? null : Number(normalized);
}

function stageLabel(stage: string) {
  return ({ GROUP: "小组赛", ROUND_ROBIN: "循环赛", KNOCKOUT: "淘汰赛", SEMIFINAL: "半决赛", FINAL: "决赛", RELEGATION: "保级赛" } as Record<string, string>)[stage] ?? "比赛";
}
