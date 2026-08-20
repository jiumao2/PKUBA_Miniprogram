import { ScrollView, Text, View } from "@tarojs/components";
import { useEffect, useState } from "react";
import type { BracketGame, Brackets, DivisionBracket } from "@pkuba/api-client";

import { formatDate } from "../../format";
import "./index.css";

export function BracketView({ data }: { data: Brackets }) {
  const available = data.divisions.filter(
    (division) => division.rounds.length || division.placement_games.length,
  );
  const [divisionId, setDivisionId] = useState(available[0]?.id ?? "");
  useEffect(() => {
    if (!available.some((division) => division.id === divisionId)) {
      setDivisionId(available[0]?.id ?? "");
    }
  }, [available, divisionId]);
  const division = available.find((item) => item.id === divisionId) ?? available[0];

  if (!division) {
    return <View className="state"><Text className="state-detail">当前赛季暂无淘汰赛对阵。</Text></View>;
  }
  return (
    <View className="bracket-view">
      <ScrollView scrollX className="bracket-division-tabs" showScrollbar={false}>
        <View className="bracket-division-row">
          {available.map((item) => (
            <View
              className={`bracket-division-tab ${item.id === division.id ? "is-active" : ""} ${genderClass(item)}`}
              key={item.id}
              onClick={() => setDivisionId(item.id)}
            >
              <Text>{item.name}</Text>
            </View>
          ))}
        </View>
      </ScrollView>

      {division.champion_name && (
        <View className={`champion-line ${genderClass(division)}`}>
          <Text className="champion-label">冠军</Text>
          <Text className="champion-name">{division.champion_name}</Text>
        </View>
      )}

      {!!division.rounds.length && (
        <ScrollView scrollX className="bracket-scroll" showScrollbar={false} enhanced>
          <View
            className="bracket-track"
            style={{ width: `${division.rounds.length * 520 + 40}rpx` }}
          >
            {division.rounds.map((round, roundIndex) => (
              <View
                className="bracket-round"
                key={round.stage}
                style={{ paddingTop: `${roundIndex * 76}rpx` }}
              >
                <View className="round-heading">
                  <Text className="round-name">{round.label}</Text>
                  <Text className="round-count">{round.games.length} 场</Text>
                </View>
                <View
                  className="round-games"
                  style={{ gap: `${28 + roundIndex * 112}rpx` }}
                >
                  {round.games.map((game) => (
                    <MatchCard
                      game={game}
                      gender={division.gender}
                      hasNext={roundIndex < division.rounds.length - 1}
                      key={game.id}
                    />
                  ))}
                </View>
              </View>
            ))}
          </View>
        </ScrollView>
      )}

      {!!division.placement_games.length && (
        <View className="placement-section">
          <Text className="placement-title">保级赛</Text>
          {division.placement_games.map((game) => (
            <MatchCard game={game} gender={division.gender} hasNext={false} key={game.id} />
          ))}
        </View>
      )}
    </View>
  );
}

function MatchCard({
  game,
  gender,
  hasNext,
}: {
  game: BracketGame;
  gender: string;
  hasNext: boolean;
}) {
  const hasScore = game.home_score !== null && game.away_score !== null;
  return (
    <View className={`bracket-match ${gender === "WOMEN" ? "gender-women" : "gender-men"} ${hasNext ? "has-next" : ""}`}>
      <View className="match-meta">
        <Text>{formatDate(game.date)} · {game.start_time}</Text>
        <Text>{game.venue_name}</Text>
      </View>
      <TeamRow
        name={game.home_name}
        score={game.home_score}
        winner={game.winner_name === game.home_name}
        hasScore={hasScore}
      />
      <TeamRow
        name={game.away_name}
        score={game.away_score}
        winner={game.winner_name === game.away_name}
        hasScore={hasScore}
      />
      <View className="match-outcome">
        <Text>{game.winner_name ? `晋级 · ${game.winner_name}` : "对阵待决"}</Text>
      </View>
    </View>
  );
}

function TeamRow({
  name,
  score,
  winner,
  hasScore,
}: {
  name: string;
  score: number | null;
  winner: boolean;
  hasScore: boolean;
}) {
  return (
    <View className={`bracket-team-row ${winner ? "is-winner" : ""}`}>
      <Text className="bracket-team-name">{name}</Text>
      <Text className="bracket-score">{hasScore ? score : "—"}</Text>
    </View>
  );
}

function genderClass(division: DivisionBracket) {
  return division.gender === "WOMEN" ? "gender-women" : "gender-men";
}
