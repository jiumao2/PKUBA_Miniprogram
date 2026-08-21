import { Button, Image, ScrollView, Text, View } from "@tarojs/components";
import Taro, { useDidShow } from "@tarojs/taro";
import { useState } from "react";
import type { HomeDashboard, Season } from "@pkuba/api-client";

import logoUrl from "../../assets/pkuba-logo.png";
import { api } from "../../api";
import { GameTimeline } from "../../components/game-timeline";
import { formatDate } from "../../format";
import { syncTabBar } from "../../tabbar";
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
          <Matchday
            dashboard={dashboard}
            onSchedule={() => Taro.switchTab({ url: "/pages/schedule/index" })}
          />
          <GameDensity dashboard={dashboard} />
        </>
      )}

    </View>
  );
}

function GameDensity({ dashboard }: { dashboard: HomeDashboard }) {
  const days = dashboard.daily_game_counts;
  if (!days.length) return null;

  const maxGames = Math.max(...days.map((day) => day.game_count));
  const totalGames = days.reduce((total, day) => total + day.game_count, 0);
  const focusDate = dashboard.display_date && days.some((day) => day.date === dashboard.display_date)
    ? dashboard.display_date
    : days[days.length - 1].date;

  return (
    <View className="game-density">
      <View className="game-density-heading">
        <View>
          <Text className="section-kicker">赛季节奏</Text>
          <Text className="game-density-title">每日比赛数量</Text>
        </View>
        <Text className="game-density-total">{days.length} 日 · {totalGames} 场</Text>
      </View>
      <Text className="game-density-note">仅列有比赛的日期，颜色越深比赛越集中</Text>
      <ScrollView
        className="game-density-scroll"
        scrollX
        scrollIntoView={`game-density-${focusDate}`}
        scrollWithAnimation
        showScrollbar={false}
      >
        <View className="game-density-track">
          {days.map((day) => {
            const [date, weekday] = densityDate(day.date);
            return (
              <View
                aria-label={`${formatDate(day.date)}，${day.game_count} 场比赛`}
                className={`game-density-day level-${densityLevel(day.game_count, maxGames)} ${day.date === focusDate ? "is-focus" : ""}`}
                id={`game-density-${day.date}`}
                key={day.date}
              >
                <Text className="game-density-date">{date}</Text>
                <Text className="game-density-weekday">{weekday}</Text>
                <Text className="game-density-count">
                  {day.game_count}<Text className="game-density-unit">场</Text>
                </Text>
              </View>
            );
          })}
        </View>
      </ScrollView>
    </View>
  );
}

function densityLevel(value: number, maxValue: number) {
  return Math.max(1, Math.min(4, Math.ceil(value / maxValue * 4)));
}

function densityDate(value: string): [string, string] {
  const [year, month, day] = value.split("-").map(Number);
  const weekday = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"];
  return [`${month}/${day}`, weekday[new Date(Date.UTC(year, month - 1, day)).getUTCDay()]];
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
        showDates={recentResults}
        onGameClick={(game) => Taro.navigateTo({ url: `/pages/game-media/index?id=${game.id}` })}
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
