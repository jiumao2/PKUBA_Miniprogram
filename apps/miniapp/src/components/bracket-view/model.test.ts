import { describe, expect, it } from "vitest";
import type { BracketGame, Brackets, BracketRound, DivisionBracket } from "@pkuba/api-client";

import {
  availableDivisions,
  gameOutcomeLabel,
  isWinningSide,
  teamDisplayName,
} from "./model";

const game = {
  id: "10000000-0000-0000-0000-000000000001",
  code: "KO2-1",
  date: "2027-05-01",
  start_time: "18:30",
  venue_name: "邱德拔体育馆",
  stage: "KNOCKOUT",
  round_number: 2,
  home_team_id: "20000000-0000-0000-0000-000000000001",
  away_team_id: "20000000-0000-0000-0000-000000000002",
  home_name: "球队甲",
  away_name: "球队乙",
  home_score: 80,
  away_score: 72,
  winner_team_id: "20000000-0000-0000-0000-000000000001",
  winner_name: "球队甲",
  home_review_required: true,
  away_review_required: false,
  review_required: true,
  status: "FINISHED",
} satisfies BracketGame;

function division(overrides: Partial<DivisionBracket> = {}): DivisionBracket {
  return {
    id: "30000000-0000-0000-0000-000000000001",
    code: "men-a",
    name: "男甲",
    gender: "MEN",
    rounds: [],
    placement_games: [],
    champion_name: null,
    champion_review_required: false,
    ...overrides,
  };
}

describe("bracket presentation", () => {
  it("uses direct participants and shows an unresolved side as pending draw", () => {
    expect(teamDisplayName("上一轮胜者", null)).toBe("待抽签");
    expect(gameOutcomeLabel({ ...game, away_team_id: null, away_name: "上一轮胜者" })).toBe(
      "对阵待补全",
    );
  });

  it("uses stable round keys for multiple knockout rounds", () => {
    const rounds: BracketRound[] = [1, 2].map((roundNumber) => ({
      key: `KNOCKOUT:${roundNumber}`,
      stage: "KNOCKOUT",
      round_number: roundNumber,
      label: `淘汰赛第 ${roundNumber} 轮`,
      review_required: roundNumber === 2,
      games: [game],
    }));
    expect(rounds.map((round) => round.key)).toEqual(["KNOCKOUT:1", "KNOCKOUT:2"]);
    expect(rounds[1].review_required).toBe(true);
  });

  it("keeps review-flagged direct teams, scores, and winner intact", () => {
    expect(teamDisplayName(game.home_name, game.home_team_id)).toBe("球队甲");
    expect(isWinningSide(game, "home")).toBe(true);
    expect(gameOutcomeLabel(game)).toBe("胜队 · 球队甲");
  });

  it("includes a relegation-only division and excludes a true empty state", () => {
    const data: Brackets = {
      season_id: "40000000-0000-0000-0000-000000000001",
      season_name: "测试赛季",
      divisions: [division(), division({ id: "30000000-0000-0000-0000-000000000002", placement_games: [game] })],
    };
    expect(availableDivisions(data).map((item) => item.id)).toEqual([
      "30000000-0000-0000-0000-000000000002",
    ]);
  });
});
