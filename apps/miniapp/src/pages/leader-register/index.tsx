import { Button, Picker, Text, View } from "@tarojs/components";
import Taro, { useDidShow } from "@tarojs/taro";
import { useMemo, useState } from "react";
import type { ClaimableTeam, MiniAppMe, Season } from "@pkuba/api-client";

import { api } from "../../api";
import { getMiniAppSession } from "../../auth";
import "../../auth-pages.css";

export default function LeaderRegisterPage() {
  const [season, setSeason] = useState<Season | null>(null);
  const [me, setMe] = useState<MiniAppMe | null>(null);
  const [teams, setTeams] = useState<ClaimableTeam[]>([]);
  const [divisionIndex, setDivisionIndex] = useState(0);
  const [teamIndex, setTeamIndex] = useState(0);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useDidShow(() => {
    const token = getMiniAppSession();
    if (!token) {
      setLoading(false);
      return;
    }
    setLoading(true);
    Promise.all([api.getCurrentSeason(), api.getMiniAppMe(token)])
      .then(async ([currentSeason, currentMe]) => {
        setSeason(currentSeason);
        setMe(currentMe);
        if (!currentMe.leader_binding) {
          setTeams(await api.getClaimableTeams(currentSeason.id, token));
        }
      })
      .catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "读取球队失败"))
      .finally(() => setLoading(false));
  });

  const claim = async () => {
    const team = selected;
    const token = getMiniAppSession();
    if (!season || !team || !token) return;
    setBusy(true);
    setError(null);
    try {
      const updated = await api.claimLeaderTeam({
        season_id: season.id,
        team_id: team.id,
        expected_team_version: team.version,
      }, token);
      setMe(updated);
      setTeams([]);
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "球队认领失败");
    } finally {
      setBusy(false);
    }
  };

  const divisions = useMemo(() => {
    const seen = new Map<string, { id: string; name: string; gender: string }>();
    teams.forEach((team) => {
      seen.set(team.division_id, {
        id: team.division_id,
        name: team.division_name,
        gender: team.division_gender,
      });
    });
    return Array.from(seen.values());
  }, [teams]);
  const selectedDivision = divisions[divisionIndex] ?? divisions[0];
  const divisionTeams = teams.filter((team) => team.division_id === selectedDivision?.id);
  const selected = divisionTeams[teamIndex] ?? divisionTeams[0];
  return (
    <View className="page auth-flow-page">
      <Text className="auth-title">认领参赛球队</Text>
      <Text className="auth-intro">一人一队、一队一领队；认领后只能由超级管理员审计纠正。</Text>

      {loading && <View className="auth-panel"><Text className="auth-detail">正在读取可认领球队…</Text></View>}
      {!loading && !getMiniAppSession() && (
        <View className="auth-panel">
          <Text className="auth-panel-title">请先登录</Text>
          <Button className="auth-primary" onClick={() => Taro.redirectTo({ url: "/pages/auth/index?intent=leader" })}>微信登录</Button>
        </View>
      )}
      {!loading && me?.leader_binding && (
        <View className="auth-panel auth-success-panel">
          <Text className="auth-panel-title">领队身份已生效</Text>
          <Text className="auth-detail">{me.leader_binding.division_name} · {me.leader_binding.team_name}</Text>
          <Button className="auth-secondary" onClick={() => Taro.switchTab({ url: "/pages/mine/index" })}>返回我的</Button>
        </View>
      )}
      {!loading && me && !me.leader_binding && (
        <View className="auth-panel">
          <Text className="auth-panel-title">选择球队</Text>
          {selected ? (
            <>
              <Text className="auth-step-label">1　选择组别</Text>
              <Picker
                mode="selector"
                range={divisions.map((division) => division.name)}
                value={divisionIndex}
                onChange={(event) => {
                  setDivisionIndex(Number(event.detail.value));
                  setTeamIndex(0);
                }}
              >
                <View className={`auth-picker compact-picker ${selectedDivision.gender === "WOMEN" ? "picker-women" : "picker-men"}`}>
                  <Text className="picker-team">{selectedDivision.name}</Text>
                  <Text className="picker-group">点击更换组别</Text>
                </View>
              </Picker>
              <Text className="auth-step-label second-step">2　选择球队</Text>
              <Picker
                mode="selector"
                range={divisionTeams.map(teamLabel)}
                value={teamIndex}
                onChange={(event) => setTeamIndex(Number(event.detail.value))}
              >
                <View className="auth-picker team-picker">
                  <Text className="picker-team">{selected.name}</Text>
                  <Text className="picker-group">{selected.group_name ?? "未分组"} · 点击更换球队</Text>
                </View>
              </Picker>
              <Button className="auth-primary" disabled={busy} onClick={() => void claim()}>
                {busy ? "正在认领…" : "确认认领"}
              </Button>
            </>
          ) : (
            <Text className="auth-detail">当前没有尚未被认领的球队。</Text>
          )}
        </View>
      )}
      {error && <View className="auth-feedback auth-error">{error}</View>}
    </View>
  );
}

function teamLabel(team: ClaimableTeam) {
  return `${team.group_name ?? "未分组"} · ${team.name}`;
}
