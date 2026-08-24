import { describe, expect, it } from "vitest";

import {
  addScoreEvent,
  canPlaceScore,
  deleteScoreEvent,
  deriveScoreEvents,
  fiba2024FoulEditorOptions,
  nextLegalCumulative,
  periodCheckpoints,
  removeScoreCell,
  scoreGridRow,
  setScoreCell,
  type ScoresheetDocument,
  type ScoreEvent,
} from "./index";

describe("paper running score", () => {
  it("crosses all four 40-point blocks", () => {
    expect(scoreGridRow(1)).toEqual({ block: 0, row: 0 });
    expect(scoreGridRow(40)).toEqual({ block: 0, row: 39 });
    expect(scoreGridRow(41)).toEqual({ block: 1, row: 0 });
    expect(scoreGridRow(80)).toEqual({ block: 1, row: 39 });
    expect(scoreGridRow(81)).toEqual({ block: 2, row: 0 });
    expect(scoreGridRow(121)).toEqual({ block: 3, row: 0 });
    expect(scoreGridRow(160)).toEqual({ block: 3, row: 39 });
  });

  it("only accepts the next legal cumulative score", () => {
    const events = addScoreEvent([], {
      id: "one",
      team: "A",
      value: 3,
      period: "1",
    });
    expect(nextLegalCumulative(events, "A", 2)).toBe(5);
    expect(canPlaceScore(events, "A", 2, 4)).toBe(false);
    expect(canPlaceScore(events, "A", 2, 5)).toBe(true);
  });

  it("renumbers cumulative scores after deletion", () => {
    const events: ScoreEvent[] = [
      {
        id: "a",
        sequence: 1,
        team: "A",
        value: 2,
        period: "1",
        player_id: "",
        player_number: "7",
        cumulative: 2,
      },
      {
        id: "b",
        sequence: 2,
        team: "A",
        value: 3,
        period: "1",
        player_id: "",
        player_number: "8",
        cumulative: 5,
      },
    ];
    expect(deleteScoreEvent(events, "a")[0]).toMatchObject({
      id: "b",
      sequence: 1,
      cumulative: 3,
      mark: "circle",
    });
  });

  it("refuses quick-entry scores beyond the fourth paper block", () => {
    const events: ScoreEvent[] = Array.from({ length: 80 }, (_, index) => ({
        id: `limit-${index}`,
        sequence: index + 1,
        team: "A",
        value: 2,
        period: "5",
        player_id: "",
        player_number: "",
        cumulative: (index + 1) * 2,
      }));
    expect(nextLegalCumulative(events, "A", 1)).toBeNull();
  });

  it("derives all fixed-cell fields and clears a jersey without moving later cells", () => {
    const emptyPeriods = Object.fromEntries(
      (["1", "2", "3", "4", "5"] as const).map((period) => [period, { A: null, B: null }]),
    ) as ScoresheetDocument["summary"]["period_scores"];
    const document = {
      running_score: [{
        id: "five", sequence: 1, team: "A", value: 99, period: "5", player_id: "p1", player_number: "7", cumulative: 5, mark: "dot", boundary: "game",
      }, {
        id: "four-b", sequence: 2, team: "B", value: 99, period: "5", player_id: "p3", player_number: "6", cumulative: 4, mark: "dot", boundary: "game",
      }],
      summary: {
        period_scores: {
          ...emptyPeriods,
          "1": { A: 2, B: 3 },
          "2": { A: 3, B: 1 },
        },
        final_score: { A: 5, B: 4 },
        winner_side: "A",
        ended_at: "",
      },
    } as ScoresheetDocument;
    setScoreCell(document, {
      id: "two", team: "A", player_id: "p2", player_number: "9", cumulative: 2,
    });
    expect(document.running_score).toMatchObject([
      { id: "two", value: 2, cumulative: 2, mark: "slash" },
      { id: "five", value: 3, cumulative: 5, mark: "circle", boundary: "game" },
      { id: "four-b", value: 4, cumulative: 4, mark: undefined, boundary: "game" },
    ]);
    removeScoreCell(document, "A", 2);
    expect(document.running_score).toMatchObject([
      { id: "five", value: 5, cumulative: 5 },
      { id: "four-b", value: 4, cumulative: 4, mark: undefined, boundary: "game" },
    ]);
    expect(periodCheckpoints(document, "A")).toEqual([
      { period: "1", cumulative: 2 },
      { period: "2", cumulative: 5 },
      { period: "3", cumulative: 5 },
      { period: "4", cumulative: 5 },
    ]);
    deriveScoreEvents(document);
  });
});

describe("shared FIBA 2024 foul catalogue", () => {
  it("keeps web and miniapp editor groups, suffixes, and catalog ids aligned", () => {
    expect(fiba2024FoulEditorOptions("player").map((option) => option.code))
      .toEqual(["P", "T", "U", "D"]);
    expect(fiba2024FoulEditorOptions("coach").map((option) => option.code))
      .toEqual(["C", "B", "D", "F"]);
    expect(fiba2024FoulEditorOptions("post_foul")).toEqual([
      { code: "D", catalogId: "system.post_disqualifying", markStyle: "plain", allowedSuffixes: ["", "1", "2", "3", "c"] },
      { code: "GD", catalogId: "system.game_disqualification", markStyle: "plain", allowedSuffixes: [""] },
      { code: "F", catalogId: "system.fighting_remainder", markStyle: "plain", allowedSuffixes: [""] },
    ]);
  });
});
