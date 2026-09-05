import { Button, Image, Picker, Text, View } from "@tarojs/components";
import Taro, { useDidShow } from "@tarojs/taro";
import { useMemo, useState } from "react";
import { ApiError, type HomeDashboard, type Season } from "@pkuba/api-client";

import logoUrl from "../../assets/pkuba-logo.png";
import { api } from "../../api";
import { GameTimeline } from "../../components/game-timeline";
import { navigateToOnce, switchToScheduleDate } from "../../navigation";
import { gameDetailRoute } from "../../routes";
import { usePublicPageShare } from "../../sharing";
import { formatDate } from "../../format";
import { syncTabBar } from "../../tabbar";
import {
  buildSeasonCalendar,
  calendarRangeOptions,
  type CalendarRange,
  densityLevel,
  localDateKey,
  shortDate,
  WEEKDAYS,
} from "./calendar";
import "./index.css";

type HomeLoadNotice = {
  title: string;
  detail: string;
};

function describeHomeLoadFailure(reason: unknown): HomeLoadNotice {
  if (reason instanceof ApiError && reason.code === "NO_PUBLIC_SEASON") {
    return {
      title: "当前处于休赛期",
      detail: "暂无公开赛季。",
    };
  }
  const message = reason instanceof Error ? reason.message.trim() : "读取失败";
  return {
    title: "暂时无法加载",
    detail: /[。！？!?]$/.test(message)
      ? `${message}请稍后重试。`
      : `${message}，请稍后重试。`,
  };
}

export default function HomePage() {
  const [season, setSeason] = useState<Season | null>(null);
  const [dashboard, setDashboard] = useState<HomeDashboard | null>(null);
  const [loading, setLoading] = useState(true);
  const [notice, setNotice] = useState<HomeLoadNotice | null>(null);

  usePublicPageShare({
    title: season?.name ? `${season.name} · PKUBA` : "PKUBA 北大篮球赛事",
    path: "/pages/home/index",
  });

  useDidShow(() => {
    syncTabBar(0);
    setLoading(true);
    setNotice(null);
    Promise.all([api.getCurrentSeason(), api.getHomeDashboard()])
      .then(([currentSeason, currentDashboard]) => {
        setSeason(currentSeason);
        setDashboard(currentDashboard);
      })
      .catch((reason: unknown) => setNotice(describeHomeLoadFailure(reason)))
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
      {notice && <State title={notice.title} detail={notice.detail} />}
      {!loading && !notice && dashboard && (
        <>
          <GameDensity dashboard={dashboard} season={season} />
          <Matchday
            dashboard={dashboard}
            onSchedule={() => Taro.switchTab({ url: "/pages/schedule/index" })}
          />
        </>
      )}

    </View>
  );
}

function GameDensity({ dashboard, season }: { dashboard: HomeDashboard; season: Season | null }) {
  const [range, setRange] = useState<CalendarRange>("recent");
  const days = dashboard.daily_game_counts;
  const today = localDateKey(new Date());
  const seasonStart = season?.starts_on ?? today;
  const seasonEnd = season?.ends_on ?? today;
  const rangeOptions = useMemo(
    () => calendarRangeOptions(seasonStart, seasonEnd),
    [seasonEnd, seasonStart],
  );
  const effectiveRange = rangeOptions.some((option) => option.value === range) ? range : "recent";
  const rangeIndex = Math.max(0, rangeOptions.findIndex((option) => option.value === effectiveRange));
  const cells = buildSeasonCalendar(today, days, effectiveRange, seasonStart, seasonEnd);
  const activeCells = cells.filter((cell) => !cell.outside);
  const maxGames = Math.max(0, ...days.map((day) => day.game_count));
  const totalGames = activeCells.reduce((total, day) => total + day.gameCount, 0);
  const firstDate = activeCells[0]?.date ?? seasonStart;
  const lastDate = activeCells[activeCells.length - 1]?.date ?? seasonEnd;

  return (
    <View className="game-density">
      <View className="game-density-heading">
        <Picker
          mode="selector"
          range={rangeOptions.map((option) => option.label)}
          value={rangeIndex}
          onChange={(event) => {
            const next = rangeOptions[Number(event.detail.value)];
            if (next) setRange(next.value);
          }}
        >
          <View
            className="game-density-range-trigger"
            aria-label={`选择比赛日历范围，当前${rangeOptions[rangeIndex].label}`}
          >
            <Text className="game-density-title">比赛日历</Text>
            <Text className="game-density-range-label">{rangeOptions[rangeIndex].label}</Text>
            <Text className="game-density-range-chevron" aria-hidden>⌄</Text>
          </View>
        </Picker>
        <Text className="game-density-total">
          {shortDate(firstDate)}—{shortDate(lastDate)} · {totalGames} 场
        </Text>
      </View>
      <View className="game-density-weekdays" aria-hidden>
        {WEEKDAYS.map((weekday) => <Text key={weekday}>{weekday}</Text>)}
      </View>
      <View className="game-density-grid">
        {cells.map((cell) => cell.outside ? (
          <View
            aria-label={`${formatDate(cell.date)}，不可选择`}
            className="game-density-day is-outside"
            key={cell.key}
          >
            <Text className="game-density-date">{shortDate(cell.date)}</Text>
          </View>
        ) : (
          <View
            aria-label={`${formatDate(cell.date)}，${cell.gameCount} 场比赛${cell.date === today ? "，今天" : ""}`}
            className={`game-density-day level-${densityLevel(cell.gameCount, maxGames)} ${cell.date === today ? "is-today" : ""}`}
            key={cell.key}
            onClick={() => void switchToScheduleDate(cell.date)}
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
