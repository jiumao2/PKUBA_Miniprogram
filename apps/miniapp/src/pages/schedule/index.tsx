import { Text, View } from "@tarojs/components";
import { useDidShow } from "@tarojs/taro";
import { useState } from "react";
import type { Game } from "@pkuba/api-client";

import { api } from "../../api";
import { formatDate } from "../../format";
import "./index.css";

export default function SchedulePage() {
  const [games, setGames] = useState<Game[]>([]);
  const [message, setMessage] = useState("正在读取赛程…");

  useDidShow(() => {
    api.getGames()
      .then((result) => {
        setGames(result);
        setMessage(result.length ? "" : "当前赛季尚未排入比赛。 ");
      })
      .catch((reason: unknown) => setMessage(reason instanceof Error ? reason.message : "读取失败"));
  });

  return (
    <View className="page">
      <Text className="eyebrow">CURRENT SEASON</Text>
      <Text className="page-title">全部赛程</Text>
      <Text className="schedule-hint">按日期、时段与场地排序；抽签前显示签位名称。</Text>
      {message && <View className="state"><Text className="state-detail">{message}</Text></View>}
      <View className="schedule-days">
        {games.map((game, index) => {
          const newDay = index === 0 || games[index - 1].date !== game.date;
          return (
            <View key={game.id}>
              {newDay && <Text className="date-divider">{formatDate(game.date)}</Text>}
              <View className="schedule-game">
                <View className="schedule-time"><Text>{game.start_time}</Text><Text>{game.venue_name}</Text></View>
                <View className="schedule-teams"><Text>{game.home_name}</Text><Text>{game.away_name}</Text></View>
                <View className="schedule-side"><Text>{game.division_name}</Text><Text>{game.participants_resolved ? "已确认" : "待抽签"}</Text></View>
              </View>
            </View>
          );
        })}
      </View>
    </View>
  );
}
