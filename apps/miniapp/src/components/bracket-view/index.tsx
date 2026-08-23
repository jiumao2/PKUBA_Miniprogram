import { ScrollView, Text, View } from "@tarojs/components";
import { useEffect, useState } from "react";
import type { BracketGame, Brackets, DivisionBracket } from "@pkuba/api-client";

import { formatDate } from "../../format";
import {
  availableDivisions,
  gameOutcomeLabel,
  isWinningSide,
  teamDisplayName,
} from "./model";
import "./index.css";

export function BracketView({
  data,
  onGameClick,
}: {
  data: Brackets;
  onGameClick?: (game: BracketGame) => void;
}) {
  const available = availableDivisions(data);
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
          <View>
            <Text className="champion-label">冠军</Text>
            {division.champion_review_required && (
              <Text className="review-badge champion-review-badge">签位待复核</Text>
            )}
          </View>
          <Text className="champion-name">{division.champion_name}</Text>
        </View>
      )}

      {!!division.rounds.length && (
        <ScrollView scrollX className="bracket-scroll" showScrollbar={false} enhanced>
          <View
            className="bracket-track"
            style={{ width: `${division.rounds.length * 520 + 40}rpx` }}
          >
            {division.rounds.map((round) => (
              <View
                className={`bracket-round ${round.review_required ? "needs-review" : ""}`}
                key={round.key}
              >
                <View className="round-heading">
                  <View className="round-title-line">
                    <Text className="round-name">{round.label}</Text>
                    {round.review_required && <Text className="review-badge">待复核</Text>}
                  </View>
                  <Text className="round-count">{round.games.length} 场</Text>
                </View>
                <View className="round-games">
                  {round.games.map((game) => (
                    <MatchCard
                      game={game}
                      gender={division.gender}
                      onClick={onGameClick}
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
            <MatchCard game={game} gender={division.gender} key={game.id} onClick={onGameClick} />
          ))}
        </View>
      )}
    </View>
  );
}

function MatchCard({
  game,
  gender,
  onClick,
}: {
  game: BracketGame;
  gender: string;
  onClick?: (game: BracketGame) => void;
}) {
  return (
    <View
      className={`bracket-match ${gender === "WOMEN" ? "gender-women" : "gender-men"} ${game.review_required ? "needs-review" : ""} ${onClick ? "is-clickable" : ""}`}
      onClick={() => onClick?.(game)}
    >
      <View className="match-meta">
        <Text>{formatDate(game.date)} · {game.start_time}</Text>
        <Text>{game.venue_name}</Text>
      </View>
      {game.review_required && (
        <View className="match-review-state">
          <Text>签位待复核</Text>
          <Text>当前对阵保持不变</Text>
        </View>
      )}
      <TeamRow
        name={game.home_name}
        teamId={game.home_team_id}
        score={game.home_score}
        winner={isWinningSide(game, "home")}
        reviewRequired={game.home_review_required}
      />
      <TeamRow
        name={game.away_name}
        teamId={game.away_team_id}
        score={game.away_score}
        winner={isWinningSide(game, "away")}
        reviewRequired={game.away_review_required}
      />
      <View className="match-outcome">
        <Text>{gameOutcomeLabel(game)}</Text>
      </View>
    </View>
  );
}

function TeamRow({
  name,
  teamId,
  score,
  winner,
  reviewRequired,
}: {
  name: string;
  teamId: string | null;
  score: number | null;
  winner: boolean;
  reviewRequired: boolean;
}) {
  return (
    <View className={`bracket-team-row ${winner ? "is-winner" : ""}`}>
      <View className="bracket-team-identity">
        <Text className="bracket-team-name">{teamDisplayName(name, teamId)}</Text>
        {reviewRequired && <Text className="team-review-mark">待复核</Text>}
      </View>
      <Text className="bracket-score">{score ?? "—"}</Text>
    </View>
  );
}

function genderClass(division: DivisionBracket) {
  return division.gender === "WOMEN" ? "gender-women" : "gender-men";
}
