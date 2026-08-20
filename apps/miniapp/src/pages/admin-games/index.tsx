import { ScrollView, Text, View } from "@tarojs/components";
import Taro, { useDidShow } from "@tarojs/taro";
import { useMemo, useState } from "react";
import type { Game, MiniAppMe } from "@pkuba/api-client";

import { api } from "../../api";
import { getMiniAppSession } from "../../auth";
import { GameTimeline } from "../../components/game-timeline";
import "./index.css";

export default function AdminGamesPage() {
  const [me, setMe] = useState<MiniAppMe | null>(null);
  const [games, setGames] = useState<Game[]>([]);
  const [divisionId, setDivisionId] = useState("all");
  const [message, setMessage] = useState("正在读取赛程…");

  useDidShow(() => {
    const token = getMiniAppSession();
    if (!token) {
      setMessage("请先登录管理员账号。");
      return;
    }
    Promise.all([api.getMiniAppMe(token), api.getGames()])
      .then(([current, allGames]) => {
        setMe(current);
        setGames(allGames);
        setMessage(allGames.length ? "" : "当前赛季暂无赛程。");
      })
      .catch((reason: unknown) => setMessage(reason instanceof Error ? reason.message : "读取失败"));
  });

  const divisions = useMemo(() => {
    const seen = new Map<string, { id: string; name: string; gender: string }>();
    games.forEach((game) => seen.set(game.division_id, {
      id: game.division_id,
      name: game.division_name,
      gender: game.division_gender,
    }));
    return Array.from(seen.values());
  }, [games]);
  const shown = divisionId === "all" ? games : games.filter((game) => game.division_id === divisionId);
  return (
    <View className="page admin-games-page">
      <Text className="page-title">赛程与资料</Text>
      {me?.admin_role === "SUPERADMIN" && (
        <View className="state"><Text className="state-detail">赛程直接修改统一在网页后台完成；点击比赛可查看和上传资料。</Text></View>
      )}
      {!!games.length && (
        <ScrollView scrollX className="admin-division-tabs" showScrollbar={false}>
          <View className="admin-division-row">
            <View className={`admin-division-tab ${divisionId === "all" ? "is-active" : ""}`} onClick={() => setDivisionId("all")}><Text>全部</Text></View>
            {divisions.map((division) => (
              <View
                className={`admin-division-tab ${divisionId === division.id ? "is-active" : ""} ${division.gender === "WOMEN" ? "is-women" : ""}`}
                key={division.id}
                onClick={() => setDivisionId(division.id)}
              >
                <Text>{division.name}</Text>
              </View>
            ))}
          </View>
        </ScrollView>
      )}
      {message && <View className="state"><Text className="state-detail">{message}</Text></View>}
      {!!shown.length && (
        <GameTimeline
          games={shown}
          onGameClick={(game) => Taro.navigateTo({ url: `/pages/game-media/index?id=${game.id}` })}
        />
      )}
    </View>
  );
}
