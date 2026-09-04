import { Text, View } from "@tarojs/components";
import { useDidShow } from "@tarojs/taro";
import { useRef, useState } from "react";
import type { Brackets } from "@pkuba/api-client";

import { api } from "../../api";
import { BracketView } from "../../components/bracket-view";
import { ScheduleDayScroller } from "../../components/schedule-day-scroller";
import {
  consumeScheduleFocusIntent,
  navigateToOnce,
  type ScheduleFocusIntent,
} from "../../navigation";
import { gameDetailRoute } from "../../routes";
import { usePublicPageShare } from "../../sharing";
import { syncTabBar } from "../../tabbar";
import "./index.css";

export default function SchedulePage() {
  const [brackets, setBrackets] = useState<Brackets | null>(null);
  const [view, setView] = useState<"schedule" | "bracket">("schedule");
  const [scheduleRefreshKey, setScheduleRefreshKey] = useState(0);
  const [scheduleFocus, setScheduleFocus] = useState<ScheduleFocusIntent | null>(null);
  const [bracketLoading, setBracketLoading] = useState(false);
  const [bracketMessage, setBracketMessage] = useState("");
  const bracketLoadIdRef = useRef(0);
  const bracketBusyRef = useRef(false);

  usePublicPageShare({
    title: "PKUBA 赛程与淘汰赛",
    path: "/pages/schedule/index",
  });

  const loadBrackets = async () => {
    if (bracketBusyRef.current) return;
    bracketBusyRef.current = true;
    const loadId = ++bracketLoadIdRef.current;
    setBracketLoading(true);
    setBracketMessage("");
    try {
      const result = await api.getBrackets();
      if (loadId === bracketLoadIdRef.current) setBrackets(result);
    } catch (reason: unknown) {
      if (loadId === bracketLoadIdRef.current) {
        setBracketMessage(reason instanceof Error ? reason.message : "淘汰赛读取失败");
      }
    } finally {
      if (loadId === bracketLoadIdRef.current) setBracketLoading(false);
      bracketBusyRef.current = false;
    }
  };

  useDidShow(() => {
    syncTabBar(1);
    const focusIntent = consumeScheduleFocusIntent();
    if (focusIntent) {
      setView("schedule");
      setScheduleFocus(focusIntent);
      setScheduleRefreshKey((value) => value + 1);
    } else if (view === "schedule") setScheduleRefreshKey((value) => value + 1);
    else void loadBrackets();
  });

  const selectView = (next: "schedule" | "bracket") => {
    if (next === view) return;
    setView(next);
    if (next === "bracket") void loadBrackets();
    else setScheduleRefreshKey((value) => value + 1);
  };

  return (
    <View className="page schedule-page">
      <View className="schedule-sticky-header">
        <View className="schedule-view-tabs">
          <View
            className={`schedule-view-tab ${view === "schedule" ? "is-active" : ""}`}
            onClick={() => selectView("schedule")}
          >
            <Text>赛程赛果</Text>
          </View>
          <View
            className={`schedule-view-tab ${view === "bracket" ? "is-active" : ""}`}
            onClick={() => selectView("bracket")}
          >
            <Text>淘汰赛</Text>
          </View>
        </View>
      </View>
      {view === "schedule" && (
        <ScheduleDayScroller
          focusIntent={scheduleFocus}
          refreshKey={scheduleRefreshKey}
          onGameClick={(game) => void navigateToOnce(gameDetailRoute(game.id))}
        />
      )}
      {bracketLoading && view === "bracket" && !brackets && (
        <View className="bracket-loading">
          <View /><View /><View />
        </View>
      )}
      {bracketMessage && view === "bracket" && !brackets && (
        <View className="state"><Text className="state-detail">{bracketMessage}</Text></View>
      )}
      {view === "bracket" && brackets && (
        <View className="bracket-content">
          {bracketLoading && <Text className="bracket-refreshing">正在更新</Text>}
          <BracketView
            data={brackets}
            onGameClick={(game) => void navigateToOnce(gameDetailRoute(game.id))}
          />
        </View>
      )}
    </View>
  );
}
