import { Text, View } from "@tarojs/components";
import Taro, { useDidShow } from "@tarojs/taro";
import { useRef, useState } from "react";
import type { Brackets, Game } from "@pkuba/api-client";

import { api } from "../../api";
import { BracketView } from "../../components/bracket-view";
import { GameTimeline } from "../../components/game-timeline";
import { syncTabBar } from "../../tabbar";
import { loadBracketState, loadScheduleState } from "./load";
import "./index.css";

export default function SchedulePage() {
  const [games, setGames] = useState<Game[]>([]);
  const [brackets, setBrackets] = useState<Brackets | null>(null);
  const [view, setView] = useState<"schedule" | "bracket">("schedule");
  const [scheduleMessage, setScheduleMessage] = useState("正在读取赛程…");
  const [bracketMessage, setBracketMessage] = useState("正在读取淘汰赛…");
  const loadIdRef = useRef(0);

  useDidShow(() => {
    syncTabBar(1);
    const loadId = ++loadIdRef.current;
    setGames([]);
    setBrackets(null);
    setScheduleMessage("正在读取赛程…");
    setBracketMessage("正在读取淘汰赛…");

    void loadScheduleState(api).then((result) => {
      if (loadId !== loadIdRef.current) return;
      setGames(result.games);
      setScheduleMessage(result.message);
    });
    void loadBracketState(api).then((result) => {
      if (loadId !== loadIdRef.current) return;
      setBrackets(result.brackets);
      setBracketMessage(result.message);
    });
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
      {scheduleMessage && view === "schedule" && (
        <View className="state"><Text className="state-detail">{scheduleMessage}</Text></View>
      )}
      {view === "schedule" && !!games.length && (
        <GameTimeline
          games={games}
          onGameClick={(game) => Taro.navigateTo({ url: `/pages/game-media/index?id=${game.id}` })}
        />
      )}
      {bracketMessage && view === "bracket" && (
        <View className="state"><Text className="state-detail">{bracketMessage}</Text></View>
      )}
      {view === "bracket" && !bracketMessage && brackets && <BracketView data={brackets} />}
    </View>
  );
}
