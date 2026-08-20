import type { Game } from "@pkuba/api-client";

export interface GameDay {
  date: string;
  games: Game[];
  times: GameTimeGroup[];
}

export interface GameTimeGroup {
  time: string;
  games: Game[];
}

export function groupGamesByDate(games: Game[]): GameDay[] {
  const groups = new Map<string, Game[]>();
  for (const game of games) {
    const day = groups.get(game.date) ?? [];
    day.push(game);
    groups.set(game.date, day);
  }
  return [...groups.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([date, dayGames]) => {
      const sortedGames = dayGames.sort((a, b) =>
        `${a.start_time}-${a.venue_name}`.localeCompare(`${b.start_time}-${b.venue_name}`),
      );
      const times = new Map<string, Game[]>();
      for (const game of sortedGames) {
        const timeGames = times.get(game.start_time) ?? [];
        timeGames.push(game);
        times.set(game.start_time, timeGames);
      }
      return {
        date,
        games: sortedGames,
        times: [...times.entries()].map(([time, timeGames]) => ({
          time,
          games: timeGames,
        })),
      };
    });
}

export function selectRecentGameDays(
  gameDays: GameDay[],
  today: string,
  limit = 4,
): GameDay[] {
  if (limit <= 0) return [];
  const upcoming = gameDays.filter((day) => day.date >= today);
  if (upcoming.length > 0) return upcoming.slice(0, limit);
  return gameDays.slice(-limit).reverse();
}

export function formatGameDate(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "long",
    day: "numeric",
    weekday: "short",
  }).format(new Date(`${value}T00:00:00+08:00`));
}
