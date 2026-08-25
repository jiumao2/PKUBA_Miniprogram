import { Button, Image, Text, View } from "@tarojs/components";
import Taro, { useDidShow } from "@tarojs/taro";
import { useState } from "react";
import type { HomeDashboard, Season } from "@pkuba/api-client";

import logoUrl from "../../assets/pkuba-logo.png";
import { api } from "../../api";
import { GameTimeline } from "../../components/game-timeline";
import { navigateToOnce } from "../../navigation";
import { gameDetailRoute } from "../../routes";
import { formatDate } from "../../format";
import { syncTabBar } from "../../tabbar";
import {
  buildSeasonCalendar,
  densityLevel,
  localDateKey,
  shortDate,
  WEEKDAYS,
} from "./calendar";
import "./index.css";

export default function HomePage() {
  const [season, setSeason] = useState<Season | null>(null);
  const [dashboard, setDashboard] = useState<HomeDashboard | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useDidShow(() => {
    syncTabBar(0);
    setLoading(true);
    setError(null);
    Promise.all([api.getCurrentSeason(), api.getHomeDashboard()])
      .then(([currentSeason, currentDashboard]) => {
        setSeason(currentSeason);
        setDashboard(currentDashboard);
      })
      .catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "读取失败"))
      .finally(() => setLoading(false));

  });

  return (
    <View className="page home-page">
      <View className="hero">
        <View className="hero-copy">
          <Text className="hero-year">{season?.year ?? ""}</Text>
          <Text className="hero-title">{season?.name ?? "北大杯"}</Text>
          <View className="hero-rule"><View className="hero-rule-accent" /></View>
        </View>
        <Image
          className="association-logo"
          src={logoUrl}
          mode="aspectFit"
          aria-label="北大篮协 PKUBA·1997"
        />
      </View>

      {loading && <State title="正在加载" />}
      {error && <State title="暂时无法加载" detail={`${error}，请稍后重试。`} />}
      {!loading && !error && dashboard && (
        <>
          <GameDensity dashboard={dashboard} />
          <Matchday
            dashboard={dashboard}
            onSchedule={() => Taro.switchTab({ url: "/pages/schedule/index" })}
          />
        </>
      )}

    </View>
  );
}

function GameDensity({ dashboard }: { dashboard: HomeDashboard }) {
  const days = dashboard.daily_game_counts;
  const today = localDateKey(new Date());
  const cells = buildSeasonCalendar(today, days);
  const maxGames = Math.max(0, ...cells.map((day) => day.gameCount));
  const totalGames = cells.reduce((total, day) => total + day.gameCount, 0);

  return (
    <View className="game-density">
      <View className="game-density-heading">
        <Text className="game-density-title">比赛日历</Text>
        <Text className="game-density-total">
          {shortDate(cells[0].date)}—{shortDate(cells[cells.length - 1].date)} · {totalGames} 场
        </Text>
      </View>
      <View className="game-density-weekdays" aria-hidden>
        {WEEKDAYS.map((weekday) => <Text key={weekday}>{weekday}</Text>)}
      </View>
      <View className="game-density-grid">
        {cells.map((cell) => cell.outside ? (
          <View className="game-density-day is-outside" key={cell.key} />
        ) : (
          <View
            aria-label={`${formatDate(cell.date)}，${cell.gameCount} 场比赛${cell.date === today ? "，今天" : ""}`}
            className={`game-density-day level-${densityLevel(cell.gameCount, maxGames)} ${cell.date === today ? "is-today" : ""}`}
            key={cell.key}
          >
            <View className="game-density-date-row">
              <Text className="game-density-date">{shortDate(cell.date)}</Text>
              {cell.date === today && <Text className="game-density-today">今</Text>}
            </View>
            <Text className="game-density-count">{cell.gameCount}</Text>
          </View>
        ))}
      </View>
    </View>
  );
}

function Matchday({ dashboard, onSchedule }: { dashboard: HomeDashboard; onSchedule: () => void }) {
  if (dashboard.mode === "EMPTY") {
    return (
      <View className="matchday empty-matchday">
        <Text className="matchday-title">暂无近期比赛</Text>
        <Button className="text-button" onClick={onSchedule}>查看完整赛程</Button>
      </View>
    );
  }
  const recentResults = dashboard.mode === "RECENT_RESULTS";
  const today = dashboard.mode === "TODAY";
  const title = recentResults
    ? "最近三个比赛日"
    : dashboard.display_date
      ? formatDate(dashboard.display_date)
      : "比赛日";
  return (
    <View className="matchday">
      <View className="matchday-heading">
        <View>
          <Text className="section-kicker">
            {recentResults ? "近期赛果" : today ? "今日比赛" : "下一比赛日"}
          </Text>
          <Text className="matchday-title">{title}</Text>
        </View>
        <Text className="game-count">{dashboard.total_games} 场</Text>
      </View>
      <GameTimeline
        games={dashboard.games}
        showDates={recentResults || dashboard.games.some((game) => game.date !== dashboard.display_date)}
        onGameClick={(game) => void navigateToOnce(gameDetailRoute(game.id))}
      />
      <Button className="text-button" onClick={onSchedule}>
        {recentResults ? "查看全部赛程与赛果" : "查看完整赛程"}
      </Button>
    </View>
  );
}

function State({ title, detail }: { title: string; detail?: string }) {
  return (
    <View className="state">
      <Text className="state-title">{title}</Text>
      {detail && <Text className="state-detail">{detail}</Text>}
    </View>
  );
}
