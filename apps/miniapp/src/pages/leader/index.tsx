import { Button, Text, View } from "@tarojs/components";
import Taro, { useDidShow } from "@tarojs/taro";
import { useState } from "react";
import type { Game, MiniAppMe, RescheduleGame, RescheduleRequest } from "@pkuba/api-client";

import { api } from "../../api";
import { getMiniAppSession } from "../../auth";
import { GameTimeline } from "../../components/game-timeline";
import "../../role-workspace.css";
import "./index.css";

export default function LeaderWorkspacePage() {
  const [me, setMe] = useState<MiniAppMe | null>(null);
  const [games, setGames] = useState<Game[]>([]);
  const [eligible, setEligible] = useState<RescheduleGame[]>([]);
  const [requests, setRequests] = useState<RescheduleRequest[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useDidShow(() => {
    const token = getMiniAppSession();
    if (!token) {
      setLoading(false);
      return;
    }
    setLoading(true);
    setError("");
    api.getMiniAppMe(token)
      .then(async (current) => {
        setMe(current);
        if (!current.leader_binding) return;
        const [teamGames, eligibleGames, requestItems] = await Promise.all([
          api.getGames(`?team_id=${encodeURIComponent(current.leader_binding.team_id)}`),
          api.getEligibleRescheduleGames(token),
          api.listRescheduleRequests(token),
        ]);
        setGames(teamGames);
        setEligible(eligibleGames);
        setRequests(requestItems);
      })
      .catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "读取失败"))
      .finally(() => setLoading(false));
  });

  const binding = me?.leader_binding;
  const activeRequests = requests.filter((item) => !item.is_terminal);
  if (!loading && !getMiniAppSession()) {
    return <View className="page"><Text className="page-title">领队</Text><View className="state"><Text className="state-detail">请先在“我的”完成微信登录。</Text></View></View>;
  }
  if (!loading && me && !binding) {
    return <View className="page"><Text className="page-title">领队</Text><View className="state"><Text className="state-detail">当前账号尚未认领球队。</Text></View></View>;
  }
  return (
    <View className="page leader-workspace-page">
      <Text className="page-title">领队工作台</Text>
      {loading && <View className="state"><Text className="state-detail">正在读取本队信息…</Text></View>}
      {binding && (
        <>
          <View className={`workspace-identity ${binding.division_gender === "WOMEN" ? "is-women" : ""}`}>
            <Text className="workspace-role">{binding.division_name}领队</Text>
            <Text className="workspace-name">{binding.team_name}</Text>
            <Text className="workspace-meta">账号 · {me?.account.username}</Text>
          </View>

          <View className="workspace-actions">
            <Button
              className="workspace-action primary"
              onClick={() => Taro.navigateTo({ url: "/pages/reschedule-create/index" })}
            >
              发起调赛
              <Text className="workspace-count">{eligible.length} 场可申请</Text>
            </Button>
            <Button
              className="workspace-action"
              onClick={() => Taro.navigateTo({ url: "/pages/reschedule-requests/index" })}
            >
              调赛申请
              <Text className="workspace-count">{activeRequests.length} 项进行中</Text>
            </Button>
            <Button
              className="workspace-action wide"
              onClick={() => Taro.navigateTo({ url: "/pages/special-reschedule/index" })}
            >
              特殊原因调赛与抽签说明
            </Button>
          </View>

          <View className="workspace-section-heading">
            <Text className="workspace-section-title">本队比赛</Text>
            <Text className="workspace-section-count">{games.length} 场</Text>
          </View>
          {games.length ? (
            <GameTimeline
              games={games}
              onGameClick={(game) => Taro.navigateTo({ url: `/pages/game-media/index?id=${game.id}` })}
            />
          ) : (
            <View className="state"><Text className="state-detail">当前赛季没有本队比赛。</Text></View>
          )}
        </>
      )}
      {error && <View className="flow-feedback">{error}</View>}
    </View>
  );
}
