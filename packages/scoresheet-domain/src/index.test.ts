import { describe, expect, it } from "vitest";

import {
  deriveScoreEvents,
  fiba2024FoulEditorOptions,
  isBlankPlayer,
  isOrderedFoulSlotEnabled,
  isOrderedPostFoulSlotEnabled,
  isValidJerseyNumber,
  OFFICIAL_LABELS,
  paperPlayerRows,
  periodCheckpoints,
  removeScoreCell,
  scoreGridRow,
  setPeriodScore,
  setOrderedFormalFoul,
  setOrderedPostFoul,
  setScoreCell,
  setTimeoutMinute,
  TIMEOUT_SLOT_COUNTS,
  timeoutMinute,
  semanticScoresheetPath,
  sparsePlayerRows,
  type FoulEntry,
  type ScoresheetDocument,
  type TeamEntry,
} from "./index";

function team(side: "A" | "B", name: string): TeamEntry {
  return {
    side,
    name,
    players: [],
    timeouts: [],
    team_fouls: [],
    coach_fouls: [],
    coach_post_foul_markers: [],
    assistant_coach_fouls: [],
    assistant_coach_post_foul_markers: [],
    head_coach: "",
    assistant_coach: "",
  };
}

function document(): ScoresheetDocument {
  return {
    schema_version: "1.4.0",
    rules_profile: "fiba_2024",
    id: "sheet",
    revision: 0,
    template_id: "pku-basketball-2019-v1",
    status: "draft",
    created_at: "2026-08-25T00:00:00Z",
    updated_at: "2026-08-25T00:00:00Z",
    source: {
      original_filename: "sheet.jpg",
      original_url: "/source",
      aligned_url: "",
      width: 2400,
      height: 3400,
      rotation: 0,
      corners: [[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]],
    },
    header: {
      competition: "联赛",
      game_number: "1",
      date: "2026-08-25",
      scheduled_time: "12:50",
      venue: "球场",
      crew_chief: "",
      umpire_1: "",
      umpire_2: "",
    },
    teams: [team("A", "A队"), team("B", "B队")],
    score_events: [],
    stated_period_scores: [1, 2, 3, 4, 5].map((period) => ({
      period: period as 1 | 2 | 3 | 4 | 5,
      team_a: 0,
      team_b: 0,
    })),
    final_score: { team_a: 0, team_b: 0, winner_name: "", ended_at: "" },
    officials: [],
    acknowledged_warnings: [],
  };
}

describe("authoritative paper running score", () => {
  it("covers every fixed cell from 1 to 160", () => {
    expect(scoreGridRow(1)).toEqual({ block: 0, row: 0 });
    expect(scoreGridRow(40)).toEqual({ block: 0, row: 39 });
    expect(scoreGridRow(41)).toEqual({ block: 1, row: 0 });
    expect(scoreGridRow(80)).toEqual({ block: 1, row: 39 });
    expect(scoreGridRow(121)).toEqual({ block: 3, row: 0 });
    expect(scoreGridRow(160)).toEqual({ block: 3, row: 39 });
    expect(scoreGridRow(161)).toBeNull();
  });

  it("derives points, period, marks, boundaries, final score, and winner", () => {
    const draft = document();
    setPeriodScore(draft, 1, "A", 2);
    setPeriodScore(draft, 1, "B", 1);
    setPeriodScore(draft, 2, "A", 3);
    setPeriodScore(draft, 2, "B", 3);
    setPeriodScore(draft, 5, "A", 1);
    setPeriodScore(draft, 5, "B", 0);
    setScoreCell(draft, "A", 2, "7");
    setScoreCell(draft, "A", 5, "8");
    setScoreCell(draft, "A", 6, "8");
    setScoreCell(draft, "B", 4, "00");
    deriveScoreEvents(draft);

    expect(draft.score_events).toMatchObject([
      { team: "A", cumulative_score: 2, points: 2, period: 1, mark: "diagonal", boundary: "period_end" },
      { team: "A", cumulative_score: 5, points: 3, period: 2, scorer_circled: true, boundary: "period_end" },
      { team: "B", cumulative_score: 4, points: 4, period: 2, mark: null, boundary: "game_end" },
      { team: "A", cumulative_score: 6, points: 1, period: 5, mark: "filled_dot", boundary: "game_end" },
    ]);
    expect(draft.final_score).toMatchObject({ team_a: 6, team_b: 4, winner_name: "A队" });
    expect(periodCheckpoints(draft, "A").at(-1)).toEqual({ period: 5, cumulative: 6 });

    removeScoreCell(draft, "A", 2);
    expect(draft.score_events.find((event) => event.team === "A" && event.cumulative_score === 5)?.points).toBe(5);
  });
});

describe("shared paper fields", () => {
  it("preserves sparse timeout slots including minute 0 and 10", () => {
    let result = team("A", "A队");
    result = setTimeoutMinute(result, "H2", 2, 0);
    result = setTimeoutMinute(result, "H2", 3, 10);
    expect(TIMEOUT_SLOT_COUNTS).toEqual({ H1: 2, H2: 3, OT: 3 });
    expect(timeoutMinute(result, "H2", 1)).toBeNull();
    expect(timeoutMinute(result, "H2", 2)).toBe(0);
    expect(timeoutMinute(result, "H2", 3)).toBe(10);
    result = setTimeoutMinute(result, "H2", 2, null);
    expect(timeoutMinute(result, "H2", 3)).toBe(10);
  });

  it("creates stable 12-row paper rosters and validates FIBA jersey values", () => {
    const result = team("A", "A队");
    result.players = [{ ...paperPlayerRows(result)[6], name: "测试队员", jersey_number: "00" }];
    const rows = paperPlayerRows(result);
    expect(rows).toHaveLength(12);
    expect(rows[6]).toMatchObject({ row: 7, name: "测试队员", jersey_number: "00" });
    ["", "0", "00", "1", "99"].forEach((value) => expect(isValidJerseyNumber(value)).toBe(true));
    ["000", "01", "100", "A"].forEach((value) => expect(isValidJerseyNumber(value)).toBe(false));
  });

  it("stores only material player rows and keeps stable paper row numbers", () => {
    const blank = paperPlayerRows(team("A", "A队"))[2];
    const material = { ...blank, name: "张三", jersey_number: "0" };
    expect(isBlankPlayer(blank)).toBe(true);
    expect(isBlankPlayer(material)).toBe(false);
    expect(sparsePlayerRows([blank, material])).toEqual([material]);
  });

  it("enforces consecutive formal and post-foul slots", () => {
    const foul = (slot: number): FoulEntry => ({ slot, code: "P", free_throws: null, cancelled: false, period: 1 });
    let formal: FoulEntry[] = [];
    let post: FoulEntry[] = [];
    expect(isOrderedFoulSlotEnabled(formal, 2)).toBe(false);
    ({ formalEntries: formal, postEntries: post } = setOrderedFormalFoul(formal, post, 2, foul(2)));
    expect(formal).toEqual([]);
    ({ formalEntries: formal, postEntries: post } = setOrderedFormalFoul(formal, post, 1, foul(1)));
    ({ formalEntries: formal, postEntries: post } = setOrderedFormalFoul(formal, post, 2, foul(2)));
    expect(formal.map((entry) => entry.slot)).toEqual([1, 2]);
    expect(isOrderedPostFoulSlotEnabled(formal, post, 2, 1)).toBe(true);
    post = setOrderedPostFoul(formal, post, 2, 1, { ...foul(1), code: "D" });
    post = setOrderedPostFoul(formal, post, 2, 2, { ...foul(2), code: "D" });
    expect(post.map((entry) => entry.slot)).toEqual([1, 2]);
    ({ formalEntries: formal, postEntries: post } = setOrderedFormalFoul(formal, post, 1, null));
    expect(formal).toEqual([]);
    expect(post).toEqual([]);
  });

  it("translates conflict paths into mobile-facing Chinese labels", () => {
    const draft = document();
    draft.teams[0].players = [{ ...paperPlayerRows(draft.teams[0])[4], name: "王五" }];
    expect(semanticScoresheetPath("/teams/0/players/0/name", draft)).toBe("A 队第 5 行姓名");
    expect(semanticScoresheetPath("/stated_period_scores/4/team_b", draft)).toBe("决胜期合计 · B 队");
  });

  it("shares the exact Chinese official labels and foul catalogue", () => {
    expect(Object.values(OFFICIAL_LABELS)).toEqual([
      "记录员", "助理记录员", "计时员", "24 秒计时员",
      "主裁", "第一副裁", "第二副裁", "球队抗议队长",
    ]);
    expect(fiba2024FoulEditorOptions("player").map((option) => option.code)).toEqual(["P", "T", "U", "D"]);
  });
});
