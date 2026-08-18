import { Text, View } from "@tarojs/components";
import { useDidShow } from "@tarojs/taro";
import { useMemo, useState } from "react";
import type { Game, Season } from "@pkuba/api-client";

import { api } from "../../api";
import { formatDate } from "../../format";
import "./index.css";

export default function HomePage() {
  const [season, setSeason] = useState<Season | null>(null);
  const [games, setGames] = useState<Game[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useDidShow(() => {
    setLoading(true);
    setError(null);
    Promise.all([api.getCurrentSeason(), api.getGames()])
      .then(([currentSeason, currentGames]) => {
        setSeason(currentSeason);
        setGames(currentGames);
      })
      .catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "读取失败"))
      .finally(() => setLoading(false));
  });

  const upcoming = useMemo(() => games.filter((game) => game.status === "SCHEDULED").slice(0, 4), [games]);

  return (
    <View className="page home-page">
      <View className="hero">
        <View className="ball-mark" aria-label="PKUBA 篮球标志">
          <View className="ball-line line-one" />
          <View className="ball-line line-two" />
        </View>
        <View>
          <Text className="eyebrow">PKUBA · 1997</Text>
          <Text className="page-title">{season?.name ?? "北大篮球赛事"}</Text>
          <Text className="hero-subtitle">赛程、结果与球队数据</Text>
        </View>
      </View>

      {loading && <State title="正在读取比赛" detail="赛程由赛事服务器实时提供。" />}
      {error && <State title="暂时无法加载" detail={`${error}，请稍后下拉重试。`} />}
      {!loading && !error && upcoming.length === 0 && (
        <State title="当前没有待赛比赛" detail="休赛期或赛程尚未发布时，我们会在这里通知。" />
      )}

      {!loading && !error && upcoming.length > 0 && (
        <>
          <View className="next-game">
            <Text className="next-label">下一场</Text>
            <Text className="next-date">{formatDate(upcoming[0].date)} · {upcoming[0].start_time}</Text>
            <View className="next-matchup">
              <Text>{upcoming[0].home_name}</Text>
              <Text className="next-vs">VS</Text>
              <Text>{upcoming[0].away_name}</Text>
            </View>
            <Text className="next-venue">{upcoming[0].venue_name} · {upcoming[0].division_name}</Text>
          </View>

          <Text className="section-title">随后进行</Text>
          <View className="game-list">
            {upcoming.slice(1).map((game) => (
              <GameRow game={game} key={game.id} />
            ))}
          </View>
        </>
      )}
    </View>
  );
}

function GameRow({ game }: { game: Game }) {
  return (
    <View className="game-item">
      <View>
        <Text className="game-time">{game.start_time}</Text>
        <Text className="game-code">{formatDate(game.date)}</Text>
      </View>
      <View className="game-teams">
        <Text className="team-name">{game.home_name}</Text>
        <Text className="team-name">{game.away_name}</Text>
      </View>
      <View className="right-meta">
        <Text className="game-place">{game.venue_name}</Text>
      </View>
    </View>
  );
}

function State({ title, detail }: { title: string; detail: string }) {
  return (
    <View className="state">
      <Text className="state-title">{title}</Text>
      <Text className="state-detail">{detail}</Text>
    </View>
  );
}
