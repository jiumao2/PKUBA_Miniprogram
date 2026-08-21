import { Button, ScrollView, Text, View } from "@tarojs/components";
import { useDidShow } from "@tarojs/taro";
import { useMemo, useState } from "react";
import type { PublicScoresheetStat } from "@pkuba/api-client";

import { api } from "../../api";
import { syncTabBar } from "../../tabbar";
import "./index.css";

interface TeamStat {
  team_id: string;
  team_name: string;
  side: "A" | "B";
  period_scores: Array<{ period: string; score: number }>;
  total_score: number;
  won: boolean;
  timeouts: Record<string, unknown[]>;
  team_fouls: Record<string, unknown[]>;
}

export default function DataPage() {
  const [games, setGames] = useState<PublicScoresheetStat[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useDidShow(() => {
    syncTabBar(3);
    void api
      .getPublicScoresheetStats()
      .then((rows) => {
        setGames(rows);
        setSelectedId((current) => current || rows[0]?.game_id || "");
        setError("");
      })
      .catch((reason) => {
        setError(reason instanceof Error ? reason.message : "公开统计读取失败");
      })
      .finally(() => setLoading(false));
  });

  const selected = useMemo(
    () => games.find((game) => game.game_id === selectedId) ?? null,
    [games, selectedId],
  );
  const teamStats = (selected?.team_stats ?? []) as unknown as TeamStat[];

  return (
    <View className="page data-page">
      <Text className="eyebrow">正式发布</Text>
      <Text className="page-title">比赛数据</Text>
      <Text className="data-intro">仅展示已通过全区域人工核对的纸质记录表统计。</Text>

      {loading && <View className="state"><Text className="state-title">正在读取统计…</Text></View>}
      {error && <View className="state data-error"><Text>{error}</Text></View>}
      {!loading && !error && games.length === 0 && (
        <View className="state">
          <Text className="state-title">暂无已发布数据</Text>
          <Text className="state-detail">管理员发布记录表后，球队和球员统计会出现在这里。</Text>
        </View>
      )}

      {games.length > 0 && (
        <>
          <Text className="section-title">已发布场次</Text>
          <ScrollView className="data-game-strip" scrollX showScrollbar={false}>
            <View className="data-game-strip-inner">
              {games.map((game) => (
                <Button
                  className={game.game_id === selectedId ? "data-game-pill active" : "data-game-pill"}
                  key={game.game_id}
                  onClick={() => setSelectedId(game.game_id)}
                >
                  <Text>{game.game_code}</Text>
                  <Text>{game.home_name} {game.home_score}:{game.away_score} {game.away_name}</Text>
                </Button>
              ))}
            </View>
          </ScrollView>
        </>
      )}

      {selected && (
        <View className="data-detail">
          <View className="data-score-header">
            <View><Text>{selected.home_name}</Text><Text className="data-final-score">{selected.home_score}</Text></View>
            <View className="data-score-meta"><Text>{selected.division_name}</Text><Text>{selected.date}</Text><Text>发布 v{selected.publication_number}</Text></View>
            <View><Text>{selected.away_name}</Text><Text className="data-final-score">{selected.away_score}</Text></View>
          </View>

          <Text className="data-subtitle">逐节与球队记录</Text>
          {teamStats.map((team) => (
            <View className="data-team-row" key={team.team_id}>
              <Text>{team.team_name}</Text>
              <View className="data-periods">
                {team.period_scores.map((period) => (
                  <View key={period.period}><Text>{period.period === "OT" ? "加时" : period.period}</Text><Text>{period.score}</Text></View>
                ))}
              </View>
              <Text>暂停 {countMarks(team.timeouts)} · 全队犯规 {countMarks(team.team_fouls)}</Text>
            </View>
          ))}

          <Text className="data-subtitle">球员统计</Text>
          <View className="data-player-table">
            <View className="data-player-row heading"><Text>球员</Text><Text>得分</Text><Text>1/2/3分</Text><Text>犯规</Text></View>
            {selected.player_stats
              .filter((player) => player.appeared || player.points || player.personal_fouls)
              .map((player) => (
                <View className="data-player-row" key={`${player.team_id}-${player.player_id ?? player.player_name}`}>
                  <View><Text>#{player.jersey_number || "–"} {player.player_name}</Text><Text>{player.team_name}{player.starter ? " · 首发" : ""}</Text></View>
                  <Text>{player.points}</Text>
                  <Text>{player.one_point_events}/{player.two_point_events}/{player.three_point_events}</Text>
                  <Text>{player.personal_fouls}</Text>
                </View>
              ))}
          </View>
        </View>
      )}
    </View>
  );
}

function countMarks(groups: Record<string, unknown[]>) {
  return Object.values(groups ?? {}).reduce((total, values) => total + values.length, 0);
}
