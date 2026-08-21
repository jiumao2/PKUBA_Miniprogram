import { Text, View } from "@tarojs/components";
import Taro, { useDidShow } from "@tarojs/taro";
import { useState } from "react";
import type { Brackets, Game } from "@pkuba/api-client";

import { api } from "../../api";
import { BracketView } from "../../components/bracket-view";
import { GameTimeline } from "../../components/game-timeline";
import { syncTabBar } from "../../tabbar";
import "./index.css";

export default function SchedulePage() {
  const [games, setGames] = useState<Game[]>([]);
  const [brackets, setBrackets] = useState<Brackets | null>(null);
  const [view, setView] = useState<"schedule" | "bracket">("schedule");
  const [message, setMessage] = useState("正在读取赛程…");

  useDidShow(() => {
    syncTabBar(1);
    Promise.all([api.getGames(), api.getBrackets()])
      .then(([gameResult, bracketResult]) => {
        setGames(gameResult);
        setBrackets(bracketResult);
        setMessage(gameResult.length ? "" : "当前赛季尚未排入比赛。");
      })
      .catch((reason: unknown) => setMessage(reason instanceof Error ? reason.message : "读取失败"));
  });

  return (
    <View className="page schedule-page">
      <View className="schedule-sticky-header">
        <View className="schedule-view-tabs">
          <View
            className={`schedule-view-tab ${view === "schedule" ? "is-active" : ""}`}
            onClick={() => setView("schedule")}
          >
            <Text>赛程赛果</Text>
          </View>
          <View
            className={`schedule-view-tab ${view === "bracket" ? "is-active" : ""}`}
            onClick={() => setView("bracket")}
          >
            <Text>淘汰赛</Text>
          </View>
        </View>
      </View>
      {message && view === "schedule" && (
        <View className="state"><Text className="state-detail">{message}</Text></View>
      )}
      {view === "schedule" && !!games.length && (
        <GameTimeline
          games={games}
          onGameClick={(game) => Taro.navigateTo({ url: `/pages/game-media/index?id=${game.id}` })}
        />
      )}
      {view === "bracket" && brackets && <BracketView data={brackets} />}
    </View>
  );
}
