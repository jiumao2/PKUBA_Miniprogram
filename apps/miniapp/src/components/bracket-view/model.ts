import type { BracketGame, Brackets, DivisionBracket } from "@pkuba/api-client";

export function availableDivisions(data: Brackets): DivisionBracket[] {
  return data.divisions.filter(
    (division) => division.rounds.length > 0 || division.placement_games.length > 0,
  );
}

export function teamDisplayName(name: string, teamId: string | null): string {
  return teamId ? name : "待定";
}

export function isWinningSide(game: BracketGame, side: "home" | "away"): boolean {
  const teamId = side === "home" ? game.home_team_id : game.away_team_id;
  return teamId !== null && game.winner_team_id === teamId;
}

export function gameOutcomeLabel(game: BracketGame): string {
  if (!game.home_team_id && !game.away_team_id) return "双方待定";
  if (!game.home_team_id || !game.away_team_id) return "对阵待补全";
  if (game.winner_team_id && game.winner_name) return `胜队 · ${game.winner_name}`;
  if (game.home_score !== null && game.away_score !== null) return "赛果待确认";
  return "比赛待进行";
}
