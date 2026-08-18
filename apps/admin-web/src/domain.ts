import type { Game } from "@pkuba/api-client";

export interface GameDay {
  date: string;
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
    .map(([date, dayGames]) => ({
      date,
      games: dayGames.sort((a, b) =>
        `${a.start_time}-${a.venue_name}`.localeCompare(`${b.start_time}-${b.venue_name}`),
      ),
    }));
}

export function formatGameDate(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "long",
    day: "numeric",
    weekday: "short",
  }).format(new Date(`${value}T00:00:00+08:00`));
}
