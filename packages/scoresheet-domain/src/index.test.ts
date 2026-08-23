import { describe, expect, it } from "vitest";

import {
  addScoreEvent,
  canPlaceScore,
  deleteScoreEvent,
  deleteScoreAt,
  insertScoreAt,
  nextLegalCumulative,
  scoreGridRow,
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
        period: "8",
        player_id: "",
        player_number: "",
        cumulative: (index + 1) * 2,
      }));
    expect(nextLegalCumulative(events, "A", 1)).toBeNull();
  });

  it("inserts and clears a jersey in the middle without moving cumulative cells", () => {
    const events: ScoreEvent[] = [{
      id: "five", sequence: 1, team: "A", value: 5, period: "1", player_id: "p1", player_number: "7", cumulative: 5,
    }];
    const inserted = insertScoreAt(events, {
      id: "two", team: "A", period: "1", player_id: "p2", player_number: "9", cumulative: 2,
    });
    expect(inserted).toMatchObject([
      { id: "two", value: 2, cumulative: 2, mark: "slash" },
      { id: "five", value: 3, cumulative: 5, mark: "circle" },
    ]);
    expect(deleteScoreAt(inserted, "two")).toMatchObject([
      { id: "five", value: 5, cumulative: 5 },
    ]);
  });
});
