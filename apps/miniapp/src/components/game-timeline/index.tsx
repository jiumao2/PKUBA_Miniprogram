import { Text, View } from "@tarojs/components";
import { formatOfficialScore, type Game } from "@pkuba/api-client";

import { formatDate } from "../../format";
import "./index.css";

interface TimeGroup {
  time: string;
  games: Game[];
}

interface DateGroup {
  date: string;
  times: TimeGroup[];
}

export function GameTimeline({
  games,
  showDates = true,
  onGameClick,
}: {
  games: Game[];
  showDates?: boolean;
  onGameClick?: (game: Game) => void;
}) {
  return (
    <View className="game-timeline">
      {groupGames(games).map((day) => (
        <View className="timeline-day" key={day.date}>
          {showDates && <Text className="timeline-date">{formatDate(day.date)}</Text>}
          {day.times.map((timeGroup) => (
            <View className="timeline-time-block" key={`${day.date}-${timeGroup.time}`}>
              <View className="timeline-time-heading">
                <Text className="timeline-time">{timeGroup.time}</Text>
                <Text className="timeline-count">{timeGroup.games.length} 场</Text>
              </View>
              <View className="timeline-games">
                {timeGroup.games.map((game) => (
                  <TimelineGame game={game} onClick={onGameClick} key={game.id} />
                ))}
              </View>
            </View>
          ))}
        </View>
      ))}
    </View>
  );
}

function TimelineGame({ game, onClick }: { game: Game; onClick?: (game: Game) => void }) {
  const women = game.division_gender === "WOMEN";
  const score = formatOfficialScore(game.home_score, game.away_score);
  const hasScore = score !== null;
  const showStatus = game.status === "FORFEIT" || !game.participants_resolved;
  return (
    <View
      className={`timeline-game ${women ? "timeline-women" : "timeline-men"} ${onClick ? "is-clickable" : ""}`}
      onClick={() => onClick?.(game)}
    >
      <View className="timeline-game-topline">
        <Text className="timeline-division">
          {game.division_name} · {game.group_name ?? stageLabel(game.stage)}
        </Text>
        <Text className="timeline-venue">{game.venue_name}</Text>
      </View>
      <View className="timeline-team-row">
        <Text className="timeline-team">{game.home_name}</Text>
        <Text className={`timeline-score ${hasScore ? "has-score" : "no-score"}`}>
          {hasScore ? game.home_score : ""}
        </Text>
      </View>
      <View className="timeline-team-row">
        <Text className="timeline-team">{game.away_name}</Text>
        <Text className={`timeline-score ${hasScore ? "has-score" : "no-score"}`}>
          {hasScore ? game.away_score : ""}
        </Text>
      </View>
      {showStatus && (
        <View className="timeline-game-footline">
          {game.status === "FORFEIT" && <Text className="forfeit-label">弃权</Text>}
          {!game.participants_resolved && <Text className="pending-label">对阵待定</Text>}
        </View>
      )}
    </View>
  );
}

function stageLabel(stage: string) {
  const labels: Record<string, string> = {
    GROUP: "小组赛",
    ROUND_ROBIN: "循环赛",
    KNOCKOUT: "淘汰赛",
    SEMIFINAL: "半决赛",
    FINAL: "决赛",
    RELEGATION: "保级赛",
  };
  return labels[stage] ?? "比赛";
}

function groupGames(games: Game[]): DateGroup[] {
  const days = new Map<string, Map<string, Game[]>>();
  games.forEach((game) => {
    const times = days.get(game.date) ?? new Map<string, Game[]>();
    const atTime = times.get(game.start_time) ?? [];
    atTime.push(game);
    times.set(game.start_time, atTime);
    days.set(game.date, times);
  });
  return Array.from(days, ([date, times]) => ({
    date,
    times: Array.from(times, ([time, groupedGames]) => ({ time, games: groupedGames })),
  }));
}
